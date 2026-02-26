"""
deploy_to_workspace.py  –  Fabric Workspace Deployer

Reads the 'fabric/' directory tree (written by sync_powerbi.py / the
WorkspaceSync pipeline) and pushes item definitions to a target Fabric
workspace via the REST API.

Modes of operation
------------------
  • DEPLOY_ITEM not set  →  Full deploy.  The workspace is made to exactly
    mirror the repository: new items are created, existing items are
    overwritten, and items present in the workspace but absent from the repo
    are deleted.

  • DEPLOY_ITEM set (e.g. "Add Calculated Measure.Notebook")  →  Selective
    deploy.  Only the named item and all of its transitive dependencies are
    deployed.  Nothing is deleted.

For each item that is deployed:
  • If the item already exists in the target workspace (matched by
    displayName + type):   POST  …/items/{id}/updateDefinition
  • If the item does not exist:   POST  …/items  (create with definition)
  • Item types with no deployable definition are skipped gracefully

Dependency resolution
---------------------
Dependencies are discovered by scanning item source files for cross-item
references:
  • Notebook  → META JSON blocks referencing lakehouses and environments
  • SemanticModel expressions.tmdl  → Sql.Database() references to lakehouses
  • Report definition.pbir  → datasetReference pointing to semantic models
Dependencies are resolved transitively (deps of deps are included).

The .platform file (Fabric Git-integration metadata) is intentionally
excluded from the parts list sent to the API.

Expected environment variables
-------------------------------
TENANT_ID            – Azure AD tenant ID
CLIENT_ID            – Service Principal application (client) ID
CLIENT_SECRET        – Service Principal client secret
TARGET_WORKSPACE_ID  – Fabric workspace ID to deploy into
DEPLOY_ITEM          – (optional) <DisplayName>.<ItemType> to selectively deploy
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
TENANT_ID           = os.environ["TENANT_ID"]
CLIENT_ID           = os.environ["CLIENT_ID"]
CLIENT_SECRET       = os.environ["CLIENT_SECRET"]
TARGET_WORKSPACE_ID = os.environ["TARGET_WORKSPACE_ID"]

FABRIC_BASE  = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
AUTH_URL     = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"

SOURCE_ROOT  = pathlib.Path("fabric")

# Optional: selective deploy mode
DEPLOY_ITEM  = os.environ.get("DEPLOY_ITEM", "").strip()

# Item types that have no deployable definition – skip them entirely
NO_DEPLOY_TYPES = {
    "SQLAnalyticsEndpoint",  # auto-generated from Lakehouse; cannot be deployed
    "SQLEndpoint",           # variant name for the same auto-generated endpoint
    "Dashboard",             # service-only; no source definition
    "MountedWarehouse",
    "MountedDataFactory",
}

# Map of lower-cased item type to the definition format string the API
# requires when uploading parts.  Types not listed here omit the format
# field, letting the API use its default.
FORMAT_BY_TYPE: dict[str, str] = {
    "semanticmodel": "TMDL",
}

# Item types that can be created (name + type) but whose definition cannot
# be uploaded or updated via the definition API.  When such an item already
# exists in the target workspace it is deleted and re-created.
METADATA_ONLY_TYPES = {
    "Lakehouse",
    "Environment",
    "SQLDatabase",
    "Warehouse",
}

# Files to exclude from the definition parts list.
EXCLUDED_FILES = {".platform"}

# File extensions that are ZIP archives whose raw bytes may differ between
# exports even when the logical content is identical (embedded timestamps,
# compression metadata, etc.).
ZIP_EXTENSIONS = {".dacpac", ".bacpac", ".nupkg"}

# Member files inside ZIP archives that contain volatile build metadata
# regenerated on every Fabric export.
ZIP_VOLATILE_MEMBERS = {"DacMetadata.xml", "Origin.xml"}

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
# Content comparison  (avoids unnecessary updateDefinition calls)
# ---------------------------------------------------------------------------

def _zip_contents_signature(data: bytes) -> list[tuple[str, int, int]]:
    """Return sorted list of (filename, CRC-32, size) for non-volatile members."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return sorted(
                (i.filename, i.CRC, i.file_size)
                for i in z.infolist()
                if i.filename not in ZIP_VOLATILE_MEMBERS
            )
    except (zipfile.BadZipFile, Exception):
        return []  # will cause a mismatch → safe to update


def _bytes_equal(a: bytes, b: bytes, path: str) -> bool:
    """Compare two byte sequences, using ZIP-aware logic for known archives."""
    suffix = pathlib.PurePosixPath(path).suffix.lower()
    if suffix in ZIP_EXTENSIONS:
        return _zip_contents_signature(a) == _zip_contents_signature(b)
    return a == b


def get_remote_definition(
    session: requests.Session,
    item_id: str,
    item_type: str = "",
) -> dict | None:
    """Download the current definition of an item from the target workspace.

    For item types that require a specific format (e.g. SemanticModel → TMDL),
    the format is included in the request body so the returned parts use the
    same layout as the repo.

    Returns the definition dict or None if unavailable.
    """
    url  = f"{FABRIC_BASE}/workspaces/{TARGET_WORKSPACE_ID}/items/{item_id}/getDefinition"

    # Request the same format we upload in, so the parts are comparable
    fmt = FORMAT_BY_TYPE.get(item_type.lower(), "") if item_type else ""
    body = {"format": fmt} if fmt else None
    resp = session.post(url, json=body, timeout=60)

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
            return None
        try:
            result = poll_lro(session, operation_id)
            if result and isinstance(result, dict):
                return result.get("definition", result)
        except Exception:
            return None

    return None


def definitions_match(
    local_parts: list[dict],
    remote_definition: dict | None,
) -> bool:
    """Return True if the local parts are identical to the remote definition.

    Compares decoded payloads byte-for-byte (or ZIP-content-aware for
    archive types).  Returns False if the remote definition is unavailable
    so that an update is always attempted in that case.
    """
    if remote_definition is None:
        return False

    remote_parts = remote_definition.get("parts", [])

    # Build lookup: path → decoded bytes for remote
    remote_by_path: dict[str, bytes] = {}
    for part in remote_parts:
        path = part.get("path", "")
        # Skip .platform in remote too
        if pathlib.PurePosixPath(path).name in EXCLUDED_FILES:
            continue
        payload_type = part.get("payloadType", "InlineBase64")
        payload      = part.get("payload", "")
        if payload_type == "InlineBase64":
            remote_by_path[path] = base64.b64decode(payload)
        else:
            remote_by_path[path] = payload.encode("utf-8") if isinstance(payload, str) else payload

    # Build lookup: path → decoded bytes for local
    local_by_path: dict[str, bytes] = {}
    for part in local_parts:
        path = part.get("path", "")
        payload_type = part.get("payloadType", "InlineBase64")
        payload      = part.get("payload", "")
        if payload_type == "InlineBase64":
            local_by_path[path] = base64.b64decode(payload)
        else:
            local_by_path[path] = payload.encode("utf-8") if isinstance(payload, str) else payload

    # Compare file sets
    if set(local_by_path.keys()) != set(remote_by_path.keys()):
        local_only  = set(local_by_path.keys()) - set(remote_by_path.keys())
        remote_only = set(remote_by_path.keys()) - set(local_by_path.keys())
        if local_only:
            print(f"      File set mismatch – local only: {local_only}")
        if remote_only:
            print(f"      File set mismatch – remote only: {remote_only}")
        return False

    # Compare content of each file
    for path, local_data in local_by_path.items():
        remote_data = remote_by_path[path]
        if not _bytes_equal(local_data, remote_data, path):
            print(f"      Content differs: {path}")
            return False

    return True


# ---------------------------------------------------------------------------
# Deploy operations
# ---------------------------------------------------------------------------

def _definition_body(
    parts: list[dict],
    item_type: str,
) -> dict:
    """Build the ``definition`` JSON body, including *format* when needed."""
    defn: dict = {"parts": parts}
    fmt = FORMAT_BY_TYPE.get(item_type.lower())
    if fmt:
        defn["format"] = fmt
    return {"definition": defn}


class UpdateFailed(Exception):
    """Raised when updateDefinition returns a non-retryable error."""


def update_item_definition(
    session: requests.Session,
    item_id: str,
    parts: list[dict],
    item_type: str = "",
) -> None:
    url  = f"{FABRIC_BASE}/workspaces/{TARGET_WORKSPACE_ID}/items/{item_id}/updateDefinition"
    body = _definition_body(parts, item_type)
    resp = session.post(url, json=body, timeout=120)

    if resp.status_code in (400, 404):
        try:
            err_body = resp.json()
        except Exception:
            err_body = {"raw": resp.text[:500]}
        code = err_body.get("errorCode", "")
        msg  = err_body.get("message", err_body.get("raw", "unknown"))
        raise UpdateFailed(
            f"updateDefinition failed ({resp.status_code} – {code}): {msg}"
        )

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
    }
    body.update(_definition_body(parts, item_type))
    resp   = session.post(url, json=body, timeout=120)
    result = handle_response(session, resp)
    return result.get("id", "")


def create_item_no_definition(
    session: requests.Session,
    display_name: str,
    item_type: str,
) -> str:
    """Create an item with only name + type (no definition parts)."""
    url  = f"{FABRIC_BASE}/workspaces/{TARGET_WORKSPACE_ID}/items"
    body = {
        "displayName": display_name,
        "type":        item_type,
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
    if not resp.ok:
        try:
            err_body = resp.json()
            msg = err_body.get("message", err_body.get("errorCode", resp.text[:300]))
        except Exception:
            msg = resp.text[:300]
        raise RuntimeError(f"Delete failed ({resp.status_code}): {msg}")


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
# Dependency resolution
# ---------------------------------------------------------------------------

def _extract_meta_json(content: str) -> list[dict]:
    """
    Extract all META JSON blocks from notebook .py content.

    Lines look like:
        # META {
        # META   "key": "value"
        # META }
    We strip the ``# META `` prefix from each line, track brace depth, and
    parse the resulting JSON when the top-level block closes.
    """
    PREFIX = "# META"
    results: list[dict] = []
    current_lines: list[str] = []
    in_meta = False
    brace_depth = 0

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith(PREFIX):
            if in_meta:
                # end of contiguous META lines – reset
                in_meta = False
                current_lines.clear()
                brace_depth = 0
            continue

        json_part = stripped[len(PREFIX):].strip()
        if not json_part:
            continue

        if not in_meta:
            if "{" in json_part:
                in_meta = True
                brace_depth = 0
                current_lines = []
            else:
                continue

        current_lines.append(json_part)
        brace_depth += json_part.count("{") - json_part.count("}")

        if brace_depth <= 0 and in_meta:
            json_str = "\n".join(current_lines)
            try:
                results.append(json.loads(json_str))
            except json.JSONDecodeError:
                pass
            current_lines = []
            in_meta = False

    return results


def _parse_notebook_deps(
    item_dir: pathlib.Path,
    name_index: dict[str, str],
    type_index: dict[str, list[str]],
) -> set[str]:
    """
    Scan notebook .py files for META dependency blocks.

    Returns a set of repo folder names that this notebook depends on.
    """
    deps: set[str] = set()
    for py_file in item_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8", errors="replace")
        for meta in _extract_meta_json(content):
            dependencies = meta.get("dependencies", {})

            # ── Lakehouse ──
            lakehouse = dependencies.get("lakehouse", {})
            lh_name = lakehouse.get("default_lakehouse_name")
            if lh_name:
                key = f"{lh_name}.lakehouse"
                if key in name_index:
                    deps.add(name_index[key])

            # ── Environment (no name in META – include all Environment items) ──
            if "environment" in dependencies:
                for env_folder in type_index.get("environment", []):
                    deps.add(env_folder)
    return deps


def _parse_semantic_model_deps(
    item_dir: pathlib.Path,
    name_index: dict[str, str],
    type_index: dict[str, list[str]],
) -> set[str]:
    """
    Scan .tmdl files for Sql.Database() calls (indicating a Lakehouse
    SQL endpoint dependency).
    """
    deps: set[str] = set()
    for tmdl_file in item_dir.rglob("*.tmdl"):
        content = tmdl_file.read_text(encoding="utf-8", errors="replace")
        if "Sql.Database" in content:
            # The connection string contains workspace-specific GUIDs which
            # cannot be directly mapped to a repo folder name.  Include all
            # Lakehouse items as dependencies.
            for lh_folder in type_index.get("lakehouse", []):
                deps.add(lh_folder)
    return deps


def _parse_report_deps(
    item_dir: pathlib.Path,
    name_index: dict[str, str],
    type_index: dict[str, list[str]],
) -> set[str]:
    """
    Scan .pbir files for datasetReference → semantic model dependency.
    """
    deps: set[str] = set()
    for pbir_file in item_dir.rglob("*.pbir"):
        content = pbir_file.read_text(encoding="utf-8", errors="replace")
        try:
            pbir = json.loads(content)
        except json.JSONDecodeError:
            continue
        ds_ref = pbir.get("datasetReference", {})
        by_path = ds_ref.get("byPath", {})
        ds_name = by_path.get("datasetName")
        if ds_name:
            key = f"{ds_name}.semanticmodel"
            if key in name_index:
                deps.add(name_index[key])
    return deps


# Per-type dependency parsers keyed by LOWER-CASED item type.
_DEP_PARSERS: dict = {
    "notebook":      _parse_notebook_deps,
    "semanticmodel": _parse_semantic_model_deps,
    "report":        _parse_report_deps,
}


def build_dependency_graph(source: pathlib.Path) -> dict[str, set[str]]:
    """
    Return ``{folder_name: {dep_folder_name, …}}`` for every item in the repo.

    ``name_index`` : lowered folder name  →  actual folder name
    ``type_index`` : lowered item type    →  [folder_name, …]
    """
    name_index: dict[str, str] = {}
    type_index: dict[str, list[str]] = {}

    for item_dir in sorted(source.iterdir()):
        if not item_dir.is_dir():
            continue
        folder = item_dir.name
        dot_pos = folder.rfind(".")
        if dot_pos == -1:
            continue
        item_type = folder[dot_pos + 1:]
        name_index[folder.lower()] = folder
        type_index.setdefault(item_type.lower(), []).append(folder)

    graph: dict[str, set[str]] = {}
    for folder in name_index.values():
        dot_pos = folder.rfind(".")
        item_type = folder[dot_pos + 1:].lower()
        item_dir = source / folder

        parser = _DEP_PARSERS.get(item_type)
        if parser:
            graph[folder] = parser(item_dir, name_index, type_index)
        else:
            graph[folder] = set()

    return graph


def resolve_transitive(
    graph: dict[str, set[str]],
    start: str,
) -> set[str]:
    """Return ``start`` plus all of its transitive dependencies."""
    result: set[str] = set()
    stack = [start]
    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.add(current)
        for dep in graph.get(current, set()):
            if dep not in result:
                stack.append(dep)
    return result


def topo_sort(items: set[str], graph: dict[str, set[str]]) -> list[str]:
    """Return *items* in dependency-first order (topological sort)."""
    result: list[str] = []
    visited: set[str] = set()

    def visit(item: str) -> None:
        if item in visited:
            return
        visited.add(item)
        for dep in sorted(graph.get(item, set())):
            if dep in items:
                visit(dep)
        result.append(item)

    for item in sorted(items):
        visit(item)
    return result


def find_item_folder(source: pathlib.Path, user_input: str) -> str | None:
    """
    Case-insensitive match of *user_input* against folder names under *source*.

    Returns the actual folder name or ``None``.
    """
    target = user_input.strip().lower()
    for item_dir in sorted(source.iterdir()):
        if item_dir.is_dir() and item_dir.name.lower() == target:
            return item_dir.name
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=== Fabric Workspace Deployer ===")
    print(f"Source    : {SOURCE_ROOT.resolve()}")
    print(f"Target    : {TARGET_WORKSPACE_ID}")
    selective = bool(DEPLOY_ITEM)
    if selective:
        print(f"Mode      : Selective deploy  →  {DEPLOY_ITEM}")
    else:
        print("Mode      : Full deploy")

    if not SOURCE_ROOT.is_dir():
        print(f"\nERROR: Source directory '{SOURCE_ROOT}' not found.", file=sys.stderr)
        print("Make sure the repository was checked out at the correct commit.", file=sys.stderr)
        sys.exit(1)

    # ── 0. Resolve deploy set (selective mode) ───────────────────────────────
    deploy_folders: set[str] | None = None  # None ⇒ deploy everything

    if selective:
        matched_folder = find_item_folder(SOURCE_ROOT, DEPLOY_ITEM)
        if not matched_folder:
            print(f"\nERROR: No item matching '{DEPLOY_ITEM}' found in {SOURCE_ROOT}/",
                  file=sys.stderr)
            print("Available items:", file=sys.stderr)
            for d in sorted(SOURCE_ROOT.iterdir()):
                if d.is_dir() and "." in d.name:
                    print(f"  • {d.name}", file=sys.stderr)
            sys.exit(1)

        print(f"\n[0] Resolving dependencies for {matched_folder} …")
        dep_graph = build_dependency_graph(SOURCE_ROOT)
        deploy_folders = resolve_transitive(dep_graph, matched_folder)
        ordered = topo_sort(deploy_folders, dep_graph)
        print(f"  Will deploy {len(deploy_folders)} item(s) (in dependency order):")
        for f in ordered:
            tag = " ← requested" if f == matched_folder else "   (dependency)"
            print(f"    • {f}{tag}")

    # ── 1. Authenticate ──────────────────────────────────────────────────────
    step = "[1/3]" if selective else "[1/4]"
    print(f"\n{step} Authenticating as Service Principal …")
    token   = get_access_token()
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    })
    print("  OK")

    # ── 2. Inventory target workspace ────────────────────────────────────────
    step = "[2/3]" if selective else "[2/4]"
    print(f"\n{step} Inventorying target workspace …")
    existing = list_workspace_items(session)
    print(f"  Found {len(existing)} existing item(s) in workspace")

    # ── 3. Deploy items ──────────────────────────────────────────────────────
    step = "[3/3]" if selective else "[3/4]"
    print(f"\n{step} Deploying items …")
    results = {"deployed": 0, "created": 0, "deleted": 0, "skipped": 0, "errors": 0}

    # Track which (name, type) keys are in the repo so we can delete extras
    repo_keys: set[tuple[str, str]] = set()

    # Determine iteration order
    if selective:
        # Use the topologically sorted list so deps are created first
        ordered_items = []
        ordered_set = topo_sort(deploy_folders, dep_graph)  # type: ignore[arg-type]
        for folder_name in ordered_set:
            dot_pos = folder_name.rfind(".")
            if dot_pos == -1:
                continue
            display_name = folder_name[:dot_pos]
            item_type = folder_name[dot_pos + 1:]
            item_dir = SOURCE_ROOT / folder_name
            ordered_items.append((display_name, item_type, item_dir))
    else:
        ordered_items = list(iter_item_dirs(SOURCE_ROOT))

    for display_name, item_type, item_dir in ordered_items:
        print(f"\n  [{item_type}]  {display_name}")

        if item_type in NO_DEPLOY_TYPES:
            print(f"    SKIP – {item_type} cannot be deployed via definition API")
            results["skipped"] += 1
            continue

        lookup_key = (display_name.lower(), item_type.lower())
        repo_keys.add(lookup_key)
        existing_id = existing.get(lookup_key)

        # ── Metadata-only types (Lakehouse, Environment, …) ──────────────
        # These cannot be updated via the definition API and cannot be safely
        # deleted when other items depend on them.  If the item already exists
        # in the target workspace it is left as-is; otherwise it is created.
        if item_type in METADATA_ONLY_TYPES:
            if existing_id:
                print(f"    OK – already exists ({existing_id}), skipping (metadata-only type)")
                results["skipped"] += 1
            else:
                print("    Creating item (metadata-only – no definition upload) …")
                try:
                    new_id = create_item_no_definition(session, display_name, item_type)
                    print(f"    ✓ Created  {new_id}")
                    existing[lookup_key] = new_id
                    results["created"] += 1
                except Exception as exc:
                    print(f"    ERROR – {exc}")
                    results["errors"] += 1
            continue

        # ── Normal types with deployable definitions ─────────────────────
        parts = build_parts(item_dir)
        if not parts:
            print("    SKIP – no deployable files found in folder")
            results["skipped"] += 1
            continue

        try:
            if existing_id:
                # ── Compare with remote before updating ──────────────
                print(f"    Comparing with remote definition {existing_id} …")
                remote_def = get_remote_definition(session, existing_id, item_type)
                if definitions_match(parts, remote_def):
                    print("    — No changes detected, skipping update")
                    results["skipped"] += 1
                    continue
                print(f"    Updating existing item {existing_id} …")
                update_item_definition(session, existing_id, parts, item_type)
                print("    ✓ Updated")
                results["deployed"] += 1
            else:
                print("    Creating new item …")
                new_id = create_item_with_definition(session, display_name, item_type, parts)
                print(f"    ✓ Created  {new_id}")
                existing[lookup_key] = new_id
                results["created"] += 1

        except UpdateFailed as exc:
            print(f"    ERROR – {exc}")
            results["errors"] += 1
        except Exception as exc:
            print(f"    ERROR – {exc}")
            results["errors"] += 1

    # ── 4. Delete workspace items not in the repo (full deploy only) ─────────
    if selective:
        print("\n  (Selective deploy – skipping delete step)")
    else:
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
