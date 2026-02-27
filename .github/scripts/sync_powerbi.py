"""
sync_powerbi.py  –  PowerBI Workspace Source Backup

Uses the Microsoft Fabric REST API to discover every item in the target
workspace and download its source definition files.  Content is decoded from
base64 and saved under 'workspace/' preserving the exact path structure returned
by the API.

  Item type              Source files saved
  ─────────────────────  ────────────────────────────────────────────────
  Notebook               <name>.ipynb  +  .platform
  SemanticModel          model.bim  (or  *.tmdl  tree)  +  .platform
  Report                 definition.pbir  +  report.json  +  …  +  .platform
  Lakehouse              lakehouse.metadata.json  +  .platform
  DataPipeline           pipeline-content.json  +  .platform
  SparkJobDefinition     SparkJobDefinitionV1.json  +  .platform
  Warehouse              …  +  .platform
  KQLDatabase            DatabaseProperties.json  +  .platform
  KQLQueryset            queryset.kql  +  .platform
  MLModel                MLModel.json  +  .platform
  MLExperiment           MLExperiment.json  +  .platform
  Eventstream            eventstream.json  +  .platform
  SQLDatabase            SqlDatabase.json  +  .platform
  … any other type that exposes a getDefinition endpoint …

Items that do not expose getDefinition (e.g. SQLAnalyticsEndpoint, Dashboard)
are skipped gracefully and recorded in workspace_manifest.json.

Expected environment variables
-------------------------------
TENANT_ID      – Azure AD tenant ID
CLIENT_ID      – Service Principal application (client) ID
CLIENT_SECRET  – Service Principal client secret
WORKSPACE_ID   – PowerBI workspace ID to sync
"""

import os
import sys
import json
import time
import base64
import pathlib
import re
import zipfile
import io
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TENANT_ID     = os.environ["TENANT_ID"]
CLIENT_ID     = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]
WORKSPACE_ID  = os.environ["WORKSPACE_ID"]

FABRIC_BASE   = "https://api.fabric.microsoft.com/v1"
AUTH_URL      = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"

# Fabric scope covers all Fabric items including Semantic Models / Power BI
FABRIC_SCOPE  = "https://api.fabric.microsoft.com/.default"

OUTPUT_ROOT   = pathlib.Path("workspace")

# Item types that are auto-generated / service-only and have no downloadable
# source definition.
NO_DEFINITION_TYPES = {
    "SQLAnalyticsEndpoint",   # auto-generated from Lakehouse
    "SQLEndpoint",            # variant name for the same auto-generated endpoint
    "Dashboard",              # service-only construct, no source file
    "MountedWarehouse",
    "MountedDataFactory",
}

# Long-running operation poll settings
POLL_INTERVAL = 5    # seconds between polls
POLL_MAX      = 72   # 72 x 5 s = 6 min max per item

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sanitize(name: str) -> str:
    """Strip characters that are unsafe in directory / file names."""
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


def get_access_token(scope: str) -> str:
    resp = requests.post(
        AUTH_URL,
        data={
            "grant_type":    "client_credentials",
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope":         scope,
        },
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        print("ERROR: No access_token in authentication response.", file=sys.stderr)
        print(resp.text, file=sys.stderr)
        sys.exit(1)
    return token


# File extensions that are ZIP archives whose raw bytes may differ between
# exports even when the logical content is identical (embedded timestamps,
# compression metadata, etc.).  For these we compare the *member contents*
# inside the archive rather than the outer bytes.
ZIP_EXTENSIONS = {".dacpac", ".bacpac", ".nupkg"}

# Member files inside ZIP archives that contain volatile build metadata
# (timestamps, checksums) regenerated on every Fabric export.  These are
# excluded from the content-change comparison so that an unchanged schema
# is not falsely flagged as modified.
ZIP_VOLATILE_MEMBERS = {"DacMetadata.xml", "Origin.xml"}


def _zip_contents_equal(a: bytes, b: bytes) -> bool:
    """Return True if two ZIP archives contain identical member files.

    Compares only the central-directory metadata (file names, CRC-32
    checksums, and uncompressed sizes) without decompressing any data.
    Members listed in ``ZIP_VOLATILE_MEMBERS`` (e.g. DacMetadata.xml,
    Origin.xml) are excluded because PowerBI regenerates them with fresh
    timestamps on every export even when the schema is unchanged.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(a)) as za, zipfile.ZipFile(io.BytesIO(b)) as zb:
            entries_a = sorted(
                (i.filename, i.CRC, i.file_size) for i in za.infolist()
                if i.filename not in ZIP_VOLATILE_MEMBERS
            )
            entries_b = sorted(
                (i.filename, i.CRC, i.file_size) for i in zb.infolist()
                if i.filename not in ZIP_VOLATILE_MEMBERS
            )
            return entries_a == entries_b
    except (zipfile.BadZipFile, Exception):
        # If either isn't a valid ZIP, fall back to raw comparison
        return a == b


def write_file(path: pathlib.Path, data: bytes) -> bool:
    """Write *data* to *path* only if the content has actually changed.

    For ZIP-based formats (.dacpac, etc.) the comparison is done on the
    archive member contents rather than the raw bytes, because PowerBI
    regenerates the ZIP envelope on every export.

    Returns True if the file was written (new or changed), False if skipped.
    """
    if path.exists():
        existing = path.read_bytes()
        is_zip = path.suffix.lower() in ZIP_EXTENSIONS
        contents_match = (
            _zip_contents_equal(existing, data) if is_zip
            else existing == data
        )
        if contents_match:
            print(f"      SKIP  {path}  (unchanged)")
            return False
        print(f"      WRITE {path}  (content changed)")
    else:
        print(f"      WRITE {path}  (new file)")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


# ---------------------------------------------------------------------------
# Fabric API – item discovery
# ---------------------------------------------------------------------------

def list_workspace_items(session: requests.Session) -> list:
    """
    Return all items in the workspace.
    Handles OData-style pagination via continuationUri.
    """
    items = []
    url   = f"{FABRIC_BASE}/workspaces/{WORKSPACE_ID}/items"
    while url:
        resp = session.get(url, timeout=60)
        resp.raise_for_status()
        body  = resp.json()
        items.extend(body.get("value", []))
        url   = body.get("continuationUri")
    return items


# ---------------------------------------------------------------------------
# Fabric API – getDefinition
# ---------------------------------------------------------------------------

def get_item_definition(session: requests.Session, item_id: str) -> dict | None:
    """
    POST  /workspaces/{workspaceId}/items/{itemId}/getDefinition

    Returns the 'definition' dict (containing a 'parts' list) or None when
    the item type does not support the endpoint.

    The API can respond:
      200  – definition returned synchronously
      202  – long-running operation; poll until complete
      400  – item type does not support getDefinition
      404  – item not found
    """
    url  = f"{FABRIC_BASE}/workspaces/{WORKSPACE_ID}/items/{item_id}/getDefinition"
    resp = session.post(url, timeout=60)

    if resp.status_code in (400, 404):
        return None

    if resp.status_code == 200:
        return resp.json().get("definition")

    if resp.status_code == 202:
        operation_id = resp.headers.get("x-ms-operation-id", "")
        if not operation_id:
            location     = resp.headers.get("Location", "")
            operation_id = location.rstrip("/").split("/")[-1]
        if not operation_id:
            print("      WARNING: 202 received but could not find operation ID.")
            return None
        return _poll_lro(session, operation_id)

    resp.raise_for_status()
    return None


def _poll_lro(session: requests.Session, operation_id: str) -> dict | None:
    """Poll a Fabric long-running operation until it succeeds or fails."""
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
            result = session.get(result_url, timeout=60)
            result.raise_for_status()
            return result.json().get("definition")

        if status in ("failed", "cancelled"):
            err = body.get("error", {})
            print(f"      Operation {status}: {err.get('message', 'unknown error')}")
            return None

    print(f"      WARNING: operation {operation_id} timed out after polling.")
    return None


# ---------------------------------------------------------------------------
# Save definition parts
# ---------------------------------------------------------------------------

def save_definition(item_name: str, item_type: str, definition: dict) -> tuple[int, int]:
    """
    Decode every part in the definition and write it to disk.

    Returns (written_count, skipped_count) so the caller can track totals.

    Directory layout mirrors the PowerBI GitHub integration exactly:

        workspace/<ItemDisplayName>.<ItemType>/<path returned by API>

    Examples:
        workspace/Sales Report.Report/definition.pbir
        workspace/Sales Model.SemanticModel/model.bim
        workspace/Bronze.Lakehouse/lakehouse.metadata.json
        workspace/ETL Pipeline.DataPipeline/pipeline-content.json
        workspace/Analysis.Notebook/notebook-content.ipynb
        workspace/MyDB.SQLDatabase/SqlDatabase.json
        workspace/Shared Env.Environment/environment.yml
    """
    parts = definition.get("parts", [])
    if not parts:
        print("      WARNING: definition contained no parts.")
        return (0, 0)

    written = 0
    skipped = 0

    # <DisplayName>.<ItemType>  –  matches PowerBI Git integration folder naming
    item_dir = OUTPUT_ROOT / f"{sanitize(item_name)}.{item_type}"
    for part in parts:
        rel_path     = part.get("path", "unknown_file")
        payload_type = part.get("payloadType", "InlineBase64")
        payload      = part.get("payload", "")

        if payload_type == "InlineBase64":
            raw = base64.b64decode(payload)
        else:
            raw = payload.encode("utf-8") if isinstance(payload, str) else payload

        if write_file(item_dir / rel_path, raw):
            written += 1
        else:
            skipped += 1

    return (written, skipped)


# ---------------------------------------------------------------------------
# Workspace manifest
# ---------------------------------------------------------------------------

def write_manifest(items: list, skipped: list) -> None:
    type_counts: dict[str, int] = {}
    for item in items:
        t = item.get("type", "Unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    manifest = {
        "workspaceId":  WORKSPACE_ID,
        "syncedAt":     time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "totalItems":   len(items),
        "skippedCount": len(skipped),
        "byType":       {t: type_counts[t] for t in sorted(type_counts)},
        "items":        items,
        "skippedItems": skipped,
    }
    out = OUTPUT_ROOT / "workspace_manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Manifest  →  {out}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=== PowerBI Workspace Source Backup ===")
    print(f"Workspace : {WORKSPACE_ID}")
    print(f"Output    : {OUTPUT_ROOT.resolve()}")

    # ── 1. Authenticate ─────────────────────────────────────────────────────────────────────
    print("\n[1/3] Authenticating as Service Principal …")
    token   = get_access_token(FABRIC_SCOPE)
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    })
    print("  OK")

    # ── 2. Discover items ────────────────────────────────────────────────────────────
    print("\n[2/3] Discovering workspace items …")
    items = list_workspace_items(session)
    print(f"  Found {len(items)} item(s)")

    type_counts: dict[str, int] = {}
    for item in items:
        t = item.get("type", "Unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
    for t, c in sorted(type_counts.items()):
        print(f"    {t:<40} {c}")

    # ── 3. Download source definitions ─────────────────────────────────────────────
    print("\n[3/3] Downloading source definitions …")
    skipped: list = []
    total_files_written  = 0
    total_files_unchanged = 0
    items_with_changes   = 0
    items_unchanged      = 0

    for item in items:
        item_id   = item["id"]
        item_name = item.get("displayName", item_id)
        item_type = item.get("type", "Unknown")

        print(f"\n  [{item_type}]  {item_name}")

        if item_type in NO_DEFINITION_TYPES:
            print(f"    SKIP – {item_type} does not expose a source definition")
            skipped.append({**item, "skipReason": "no getDefinition support"})
            continue

        try:
            definition = get_item_definition(session, item_id)
            if definition is None:
                print("    SKIP – getDefinition not supported or returned nothing")
                skipped.append({**item, "skipReason": "getDefinition returned nothing"})
            else:
                written, unchanged = save_definition(item_name, item_type, definition)
                total_files_written  += written
                total_files_unchanged += unchanged
                if written > 0:
                    items_with_changes += 1
                    print(f"    ✓ {written} file(s) written, {unchanged} unchanged")
                else:
                    items_unchanged += 1
                    print(f"    — No changes detected ({unchanged} file(s) identical)")
        except Exception as exc:
            print(f"    ERROR – {exc}")
            skipped.append({**item, "skipReason": str(exc)})

    write_manifest(items, skipped)

    total_saved = len(items) - len(skipped)
    print(f"\n  Items processed : {total_saved}")
    print(f"  Items changed   : {items_with_changes}")
    print(f"  Items unchanged : {items_unchanged}")
    print(f"  Items skipped   : {len(skipped)}")
    print(f"  Files written   : {total_files_written}")
    print(f"  Files unchanged : {total_files_unchanged}")
    print("\n=== Backup complete ===")


if __name__ == "__main__":
    main()
