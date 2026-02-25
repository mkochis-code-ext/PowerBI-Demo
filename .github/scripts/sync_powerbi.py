"""
sync_powerbi.py

Authenticates to the Power BI REST API as a Service Principal, discovers all
content in the target workspace, and downloads the source files for every
artefact it finds.  Files are written under the 'powerbi/' directory tree.

  Resource type          Source file saved
  ─────────────────────  ──────────────────────────────────────────────────
  Power BI Report        <name>.pbix   – full report + embedded dataset
  Paginated Report       <name>.rdl    – Report Definition Language source
  Dataflow               <name>.json   – Power Query / mashup model
  Dataset                <name>.json   – full schema, tables, measures,
                                         data sources (REST API model def)
  Dashboard              <name>.json   – dashboard definition + tile layout

Expected environment variables
-------------------------------
TENANT_ID      – Azure AD tenant ID
CLIENT_ID      – Service Principal application (client) ID
CLIENT_SECRET  – Service Principal client secret
WORKSPACE_ID   – Power BI workspace (group) ID to sync
"""

import os
import sys
import json
import time
import pathlib
import re
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TENANT_ID     = os.environ["TENANT_ID"]
CLIENT_ID     = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]
WORKSPACE_ID  = os.environ["WORKSPACE_ID"]

API_BASE      = "https://api.powerbi.com/v1.0/myorg"
AUTH_URL      = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
SCOPE         = "https://analysis.windows.net/powerbi/api/.default"

OUTPUT_ROOT   = pathlib.Path("powerbi")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sanitize(name: str) -> str:
    """Replace characters that are problematic in file names."""
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


def get_access_token() -> str:
    resp = requests.post(
        AUTH_URL,
        data={
            "grant_type":    "client_credentials",
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope":         SCOPE,
        },
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        print("ERROR: No access_token in authentication response.", file=sys.stderr)
        print(resp.text, file=sys.stderr)
        sys.exit(1)
    print("  Authentication successful.")
    return token


def api_get(session: requests.Session, url: str, stream: bool = False):
    resp = session.get(url, timeout=120, stream=stream)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp


def write_binary(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    print(f"    Saved  {path}")


def write_json(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"    Saved  {path}")


# ---------------------------------------------------------------------------
# Resource discovery
# ---------------------------------------------------------------------------

def list_resource(session: requests.Session, resource: str) -> list:
    url  = f"{API_BASE}/groups/{WORKSPACE_ID}/{resource}"
    resp = api_get(session, url)
    if resp is None:
        print(f"  WARNING: Could not list {resource} (404).")
        return []
    return resp.json().get("value", [])


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------

def export_report_source(session: requests.Session, report: dict) -> None:
    """
    Download the source file for a report via the /Export endpoint.

    Power BI reports  → .pbix  (binary Power BI Desktop file)
    Paginated reports → .rdl   (Report Definition Language XML source)

    Both report types are served by the same REST endpoint; Power BI returns
    the correct format automatically based on the report type.
    """
    report_id   = report["id"]
    report_name = sanitize(report.get("name", report_id))
    report_type = report.get("reportType", "PowerBIReport")

    if report_type == "PaginatedReport":
        _export_paginated_report_rdl(session, report_id, report_name)
    else:
        _export_powerbi_report(session, report_id, report_name)


def _export_powerbi_report(session, report_id, report_name):
    url  = f"{API_BASE}/groups/{WORKSPACE_ID}/reports/{report_id}/Export"
    resp = api_get(session, url)
    if resp is None:
        print(f"    SKIP   reports/{report_name}.pbix  (not exportable / not found)")
        return
    path = OUTPUT_ROOT / "reports" / f"{report_name}.pbix"
    write_binary(path, resp.content)


def _export_paginated_report_rdl(session, report_id, report_name):
    """
    Download the .rdl source file for a Paginated Report.

    The GET /groups/{groupId}/reports/{reportId}/Export endpoint returns the
    raw Report Definition Language (RDL) XML when called against a paginated
    report, giving a complete, restorable source backup.
    """
    url  = f"{API_BASE}/groups/{WORKSPACE_ID}/reports/{report_id}/Export"
    resp = api_get(session, url)
    if resp is None:
        print(f"    SKIP   paginated-reports/{report_name}.rdl  (not exportable / not found)")
        return
    path = OUTPUT_ROOT / "paginated-reports" / f"{report_name}.rdl"
    write_binary(path, resp.content)


def export_dataflow(session: requests.Session, dataflow: dict) -> None:
    dataflow_id   = dataflow["objectId"]
    dataflow_name = sanitize(dataflow.get("name", dataflow_id))
    url           = f"{API_BASE}/groups/{WORKSPACE_ID}/dataflows/{dataflow_id}"
    resp          = api_get(session, url)
    if resp is None:
        print(f"    SKIP   dataflows/{dataflow_name}.json  (not found)")
        return
    path = OUTPUT_ROOT / "dataflows" / f"{dataflow_name}.json"
    write_json(path, resp.json())


def export_dataset_metadata(session: requests.Session, dataset: dict) -> None:
    """
    Save the full dataset model definition available through the REST API.

    This captures every structural detail the API exposes: the dataset object
    itself, all tables with their columns and measures, data source connection
    info, and recent refresh history.  The resulting JSON is sufficient to
    reconstruct the schema and can be used as a change-tracking artefact.

    Note: downloading the dataset as a raw .pbix binary requires the workspace
    to be on Premium/PPU with the XMLA read endpoint enabled.  For REST-API-
    only access (as used here) the JSON model definition is the authoritative
    source backup.
    """
    dataset_id   = dataset["id"]
    dataset_name = sanitize(dataset.get("name", dataset_id))
    base_url     = f"{API_BASE}/groups/{WORKSPACE_ID}/datasets/{dataset_id}"

    metadata = {"dataset": dataset}

    # Tables / columns / measures
    tables_resp = api_get(session, f"{base_url}/tables")
    if tables_resp:
        metadata["tables"] = tables_resp.json().get("value", [])

    # Data sources
    sources_resp = api_get(session, f"{base_url}/datasources")
    if sources_resp:
        metadata["datasources"] = sources_resp.json().get("value", [])

    # Refresh history (last 10 entries)
    refresh_resp = api_get(session, f"{base_url}/refreshes?$top=10")
    if refresh_resp:
        metadata["refreshHistory"] = refresh_resp.json().get("value", [])

    path = OUTPUT_ROOT / "datasets" / f"{dataset_name}.json"
    write_json(path, metadata)


def export_dashboard_metadata(session: requests.Session, dashboard: dict) -> None:
    dashboard_id   = dashboard["id"]
    dashboard_name = sanitize(dashboard.get("displayName", dashboard_id))
    base_url       = f"{API_BASE}/groups/{WORKSPACE_ID}/dashboards/{dashboard_id}"

    metadata = {"dashboard": dashboard}

    # Tiles
    tiles_resp = api_get(session, f"{base_url}/tiles")
    if tiles_resp:
        metadata["tiles"] = tiles_resp.json().get("value", [])

    path = OUTPUT_ROOT / "dashboards" / f"{dashboard_name}.json"
    write_json(path, metadata)


# ---------------------------------------------------------------------------
# Workspace manifest
# ---------------------------------------------------------------------------

def write_workspace_manifest(
    reports: list,
    dataflows: list,
    datasets: list,
    dashboards: list,
) -> None:
    manifest = {
        "workspaceId": WORKSPACE_ID,
        "syncedAt":    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "counts": {
            "reports":    len(reports),
            "dataflows":  len(dataflows),
            "datasets":   len(datasets),
            "dashboards": len(dashboards),
        },
        "reports":    reports,
        "dataflows":  dataflows,
        "datasets":   datasets,
        "dashboards": dashboards,
    }
    path = OUTPUT_ROOT / "workspace_manifest.json"
    write_json(path, manifest)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=== Power BI Workspace Sync ===")
    print(f"Workspace: {WORKSPACE_ID}")

    # Authenticate
    print("\n[1/6] Authenticating as Service Principal …")
    token   = get_access_token()
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})

    # Discover
    print("\n[2/6] Discovering workspace contents …")
    reports    = list_resource(session, "reports")
    dataflows  = list_resource(session, "dataflows")
    datasets   = list_resource(session, "datasets")
    dashboards = list_resource(session, "dashboards")

    print(f"  Reports:    {len(reports)}")
    print(f"  Dataflows:  {len(dataflows)}")
    print(f"  Datasets:   {len(datasets)}")
    print(f"  Dashboards: {len(dashboards)}")

    # Export reports
    print("\n[3/6] Exporting reports …")
    for report in reports:
        print(f"  → {report.get('name')}  [{report.get('reportType', 'PowerBIReport')}]")
        try:
            export_report_source(session, report)
        except Exception as exc:
            print(f"    ERROR exporting report {report.get('name')}: {exc}")

    # Export dataflows
    print("\n[4/6] Exporting dataflows …")
    for df in dataflows:
        print(f"  → {df.get('name')}")
        try:
            export_dataflow(session, df)
        except Exception as exc:
            print(f"    ERROR exporting dataflow {df.get('name')}: {exc}")

    # Export dataset metadata
    print("\n[5/6] Exporting dataset metadata …")
    for ds in datasets:
        print(f"  → {ds.get('name')}")
        try:
            export_dataset_metadata(session, ds)
        except Exception as exc:
            print(f"    ERROR exporting dataset {ds.get('name')}: {exc}")

    # Export dashboard metadata
    print("\n[6/6] Exporting dashboard metadata …")
    for db in dashboards:
        print(f"  → {db.get('displayName')}")
        try:
            export_dashboard_metadata(session, db)
        except Exception as exc:
            print(f"    ERROR exporting dashboard {db.get('displayName')}: {exc}")

    # Write manifest
    write_workspace_manifest(reports, dataflows, datasets, dashboards)
    print("\n=== Sync complete ===")


if __name__ == "__main__":
    main()
