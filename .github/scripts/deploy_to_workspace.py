"""
deploy_to_workspace.py  –  Fabric Workspace Deployer

Reads the 'fabric/' directory tree (written by sync_powerbi.py / the
WorkspaceSync pipeline) and pushes every item definition to a target Fabric
workspace via the REST API.  The workspace is made to exactly mirror the
repository – new items are created, existing items are overwritten, and any
items present in the workspace but absent from the repo are deleted.

  For each <DisplayName>.<ItemType> folder found in fabric/:
    • If the item already exists in the target workspace (matched by
      displayName + type):   POST  …/items/{id}/updateDefinition
    • If the item does not exist:   POST  …/items  (create with definition)
    • Items in the workspace not present in the repo are deleted
    • Item types with no deployable definition are skipped gracefully

The .platform file (Fabric Git-integration metadata) is intentionally
excluded from the parts list sent to the API.

Expected environment variables
-------------------------------
TENANT_ID            – Azure AD tenant ID
CLIENT_ID            – Service Principal application (client) ID
CLIENT_SECRET        – Service Principal client secret
TARGET_WORKSPACE_ID  – Fabric workspace ID to deploy into
"""

import os
import sys
import json
import time
import base64
import pathlib
import re
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TENANT_ID           = os.environ["TENANT_ID"]
CLIENT_ID           = os.environ["CLIENT_ID"]
CLIENT_SECRET       = os.environ["CLIENT_SECRET"]
TARGET_WORKSPACE_ID = os.environ["TARGET_WORKSPACE_ID"]

FABRIC_BASE  = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
AUTH_URL     = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"

SOURCE_ROOT  = pathlib.Path("fabric")

# Item types that have no deployable definition – skip them entirely
NO_DEPLOY_TYPES = {
    "SQLAnalyticsEndpoint",  # auto-generated from Lakehouse; cannot be deployed
    "SQLEndpoint",           # variant name for the same auto-generated endpoint
    "Dashboard",             # service-only; no source definition
    "MountedWarehouse",
    "MountedDataFactory",
}

# The .platform file is Fabric Git-integration metadata only; the API does
# not accept it as part of a definition payload.
EXCLUDED_FILES = {".platform"}

# Long-running operation poll settings
POLL_INTERVAL = 5    # seconds between polls
POLL_MAX      = 72   # 72 × 5 s = 6 min max per item

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def get_access_token() -> str:
    resp = requests.post(
        AUTH_URL,
        data={
            "grant_type":    "client_credentials",
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope":         FABRIC_SCOPE,
        },
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        print("ERROR: No access_token in authentication response.", file=sys.stderr)
        sys.exit(1)
    return token


# ---------------------------------------------------------------------------
# Long-running operation poller
# ---------------------------------------------------------------------------

def poll_lro(session: requests.Session, operation_id: str) -> dict | None:
    """Poll a Fabric LRO and return the result body on success."""
    status_url = f"{FABRIC_BASE}/operations/{operation_id}"
    result_url = f"{FABRIC_BASE}/operations/{operation_id}/result"

    for attempt in range(1, POLL_MAX + 1):
        time.sleep(POLL_INTERVAL)
        resp = session.get(status_url, timeout=30)
        resp.raise_for_status()
        body   = resp.json()
        status = body.get("status", "").lower()
        print(f"      Polling {operation_id}: {status} (attempt {attempt}/{POLL_MAX})")

        if status == "succeeded":
            r = session.get(result_url, timeout=60)
            if r.status_code == 200:
                return r.json()
            return {}   # success with no result body

        if status in ("failed", "cancelled"):
            err = body.get("error", {})
            raise RuntimeError(
                f"Operation {status}: {err.get('message', 'unknown error')}"
            )

    raise TimeoutError(f"LRO {operation_id} did not finish within the polling limit.")


def handle_response(session: requests.Session, resp: requests.Response) -> dict:
    """
    Resolve a synchronous (200/201) or asynchronous (202) API response.
    Raises on non-success status codes.
    """
    if resp.status_code in (200, 201):
        return resp.json() if resp.content else {}

    if resp.status_code == 202:
        operation_id = resp.headers.get("x-ms-operation-id", "")
        if not operation_id:
            location     = resp.headers.get("Location", "")
            operation_id = location.rstrip("/").split("/")[-1]
        if not operation_id:
            raise RuntimeError("202 accepted but no operation ID found in response headers.")
        return poll_lro(session, operation_id) or {}

    resp.raise_for_status()
    return {}


# ---------------------------------------------------------------------------
# Workspace item inventory
# ---------------------------------------------------------------------------

def list_workspace_items(session: requests.Session) -> dict[tuple[str, str], str]:
    """
    Return a dict mapping (displayName_lower, type_lower) → item_id for all
    items currently in the target workspace.
    """
    index: dict[tuple[str, str], str] = {}
    url = f"{FABRIC_BASE}/workspaces/{TARGET_WORKSPACE_ID}/items"
    while url:
        resp = session.get(url, timeout=60)
        resp.raise_for_status()
        body = resp.json()
        for item in body.get("value", []):
            key = (item.get("displayName", "").lower(), item.get("type", "").lower())
            index[key] = item["id"]
        url = body.get("continuationUri")
    return index


def list_workspace_items_full(session: requests.Session) -> list[dict]:
    """
    Return the full item dicts for every item in the target workspace.
    Used to identify items that should be deleted.
    """
    items = []
    url = f"{FABRIC_BASE}/workspaces/{TARGET_WORKSPACE_ID}/items"
    while url:
        resp = session.get(url, timeout=60)
        resp.raise_for_status()
        body = resp.json()
        items.extend(body.get("value", []))
        url = body.get("continuationUri")
    return items


# ---------------------------------------------------------------------------
# Definition builder
# ---------------------------------------------------------------------------

def build_parts(item_dir: pathlib.Path) -> list[dict]:
    """
    Walk `item_dir` and return a list of base64-encoded part dicts
    ready for the Fabric API, excluding the .platform metadata file.
    """
    parts = []
    for file_path in sorted(item_dir.rglob("*")):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(item_dir).as_posix()
        # Skip Git-integration metadata files
        if file_path.name in EXCLUDED_FILES:
            continue
        raw     = file_path.read_bytes()
        encoded = base64.b64encode(raw).decode("ascii")
        parts.append({
            "path":        rel,
            "payload":     encoded,
            "payloadType": "InlineBase64",
        })
    return parts


# ---------------------------------------------------------------------------
# Deploy operations
# ---------------------------------------------------------------------------

def update_item_definition(
    session: requests.Session,
    item_id: str,
    parts: list[dict],
) -> None:
    # updateMetadata=true forces a full overwrite even when there are conflicts
    url  = f"{FABRIC_BASE}/workspaces/{TARGET_WORKSPACE_ID}/items/{item_id}/updateDefinition?updateMetadata=true"
    body = {"definition": {"parts": parts}}
    resp = session.post(url, json=body, timeout=120)

    if resp.status_code == 400:
        err = resp.json().get("errorCode", "")
        raise ValueError(f"updateDefinition not supported for this item type (400 – {err})")

    handle_response(session, resp)


def create_item_with_definition(
    session: requests.Session,
    display_name: str,
    item_type: str,
    parts: list[dict],
) -> str:
    """Create a new item and return its ID."""
    url  = f"{FABRIC_BASE}/workspaces/{TARGET_WORKSPACE_ID}/items"
    body = {
        "displayName": display_name,
        "type":        item_type,
        "definition":  {"parts": parts},
    }
    resp   = session.post(url, json=body, timeout=120)
    result = handle_response(session, resp)
    return result.get("id", "")


def delete_item(session: requests.Session, item_id: str) -> None:
    """Delete an item from the target workspace."""
    url  = f"{FABRIC_BASE}/workspaces/{TARGET_WORKSPACE_ID}/items/{item_id}"
    resp = session.delete(url, timeout=60)
    if resp.status_code == 404:
        return  # already gone
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Directory scanner
# ---------------------------------------------------------------------------

def iter_item_dirs(source: pathlib.Path):
    """
    Yield (display_name, item_type, item_dir) for each <Name>.<Type>
    folder directly under source.
    """
    for item_dir in sorted(source.iterdir()):
        if not item_dir.is_dir():
            continue
        # workspace_manifest.json and any stray files at the root are ignored
        folder_name = item_dir.name
        # Split on the LAST dot to separate display name from item type
        # e.g. "My.Report.Report" → name="My.Report", type="Report"
        dot_pos = folder_name.rfind(".")
        if dot_pos == -1:
            print(f"  SKIP  {folder_name}  (no '.' type separator in folder name)")
            continue
        display_name = folder_name[:dot_pos]
        item_type    = folder_name[dot_pos + 1:]
        yield display_name, item_type, item_dir


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=== Fabric Workspace Deployer ===")
    print(f"Source    : {SOURCE_ROOT.resolve()}")
    print(f"Target    : {TARGET_WORKSPACE_ID}")

    if not SOURCE_ROOT.is_dir():
        print(f"\nERROR: Source directory '{SOURCE_ROOT}' not found.", file=sys.stderr)
        print("Make sure the repository was checked out at the correct commit.", file=sys.stderr)
        sys.exit(1)

    # ── 1. Authenticate ──────────────────────────────────────────────────────
    print("\n[1/3] Authenticating as Service Principal …")
    token   = get_access_token()
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    })
    print("  OK")

    # ── 2. Inventory target workspace ────────────────────────────────────────
    print("\n[2/3] Inventorying target workspace …")
    existing = list_workspace_items(session)
    print(f"  Found {len(existing)} existing item(s) in workspace")

    # ── 3. Deploy items ──────────────────────────────────────────────────────
    print("\n[3/4] Deploying items …")
    results = {"deployed": 0, "created": 0, "deleted": 0, "skipped": 0, "errors": 0}

    # Track which (name, type) keys are in the repo so we can delete extras
    repo_keys: set[tuple[str, str]] = set()

    for display_name, item_type, item_dir in iter_item_dirs(SOURCE_ROOT):
        print(f"\n  [{item_type}]  {display_name}")

        if item_type in NO_DEPLOY_TYPES:
            print(f"    SKIP – {item_type} cannot be deployed via definition API")
            results["skipped"] += 1
            continue

        parts = build_parts(item_dir)
        if not parts:
            print("    SKIP – no deployable files found in folder")
            results["skipped"] += 1
            continue

        lookup_key = (display_name.lower(), item_type.lower())
        repo_keys.add(lookup_key)
        existing_id = existing.get(lookup_key)

        try:
            if existing_id:
                print(f"    Updating existing item {existing_id} …")
                update_item_definition(session, existing_id, parts)
                print("    ✓ Updated")
                results["deployed"] += 1
            else:
                print("    Creating new item …")
                new_id = create_item_with_definition(session, display_name, item_type, parts)
                print(f"    ✓ Created  {new_id}")
                # Add to index in case of duplicates in same run
                existing[lookup_key] = new_id
                results["created"] += 1

        except ValueError as exc:
            # updateDefinition not supported – log and move on
            print(f"    SKIP – {exc}")
            results["skipped"] += 1
        except Exception as exc:
            print(f"    ERROR – {exc}")
            results["errors"] += 1

    # ── 4. Delete workspace items not in the repo ────────────────────────────
    print("\n[4/4] Removing items not present in repo …")
    # Re-fetch full item list to catch any items not matched during deploy
    all_ws_items = list_workspace_items_full(session)
    for ws_item in all_ws_items:
        ws_name = ws_item.get("displayName", "")
        ws_type = ws_item.get("type", "")
        ws_key  = (ws_name.lower(), ws_type.lower())
        ws_id   = ws_item["id"]

        # Skip types that have no repo representation
        if ws_type in NO_DEPLOY_TYPES:
            continue

        if ws_key not in repo_keys:
            print(f"  [{ws_type}]  {ws_name}")
            try:
                delete_item(session, ws_id)
                print(f"    ✓ Deleted  {ws_id}")
                results["deleted"] += 1
            except Exception as exc:
                print(f"    ERROR deleting – {exc}")
                results["errors"] += 1

    print(f"\n  Updated : {results['deployed']}")
    print(f"  Created : {results['created']}")
    print(f"  Deleted : {results['deleted']}")
    print(f"  Skipped : {results['skipped']}")
    print(f"  Errors  : {results['errors']}")

    if results["errors"] > 0:
        print("\nDeployment completed with errors.", file=sys.stderr)
        sys.exit(1)

    print("\n=== Deployment complete ===")


if __name__ == "__main__":
    main()
