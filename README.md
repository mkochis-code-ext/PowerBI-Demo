# PowerBI CI/CD Automation

Automated backup, deployment, and promotion of Microsoft PowerBI workspace artifacts using GitHub Actions. This repository provides **two independent deployment strategies** — choose the one that fits your workflow, or use both together.

## Table of Contents

- [Overview](#overview)
- [Repository Structure](#repository-structure)
- [GitHub Actions Workflows](#github-actions-workflows)
  - [WorkspaceSync — Source Backup](#workspacesync--source-backup)
  - [WorkspaceDeploy — REST API Deploy](#workspacedeploy--rest-api-deploy)
  - [WorkspacePipelineDeploy — PowerBI Deployment Pipeline](#workspacepipelinedeploy--fabric-deployment-pipeline)
- [Python Scripts](#python-scripts)
  - [sync_powerbi.py](#sync_powerbipy)
  - [deploy_to_workspace.py](#deploy_to_workspacepy)
- [Choosing a Deployment Strategy](#choosing-a-deployment-strategy)
- [Setup & Configuration](#setup--configuration)
  - [1. Create an Azure AD Service Principal](#1-create-an-azure-ad-service-principal)
  - [2. Grant PowerBI Permissions](#2-grant-fabric-permissions)
  - [3. Configure GitHub Secrets](#3-configure-github-secrets)
  - [4. Create GitHub Environments](#4-create-github-environments)
  - [5. PowerBI Deployment Pipeline Setup (WorkspacePipelineDeploy only)](#5-fabric-deployment-pipeline-setup-workspacepipelinedeploy-only)
  - [6. Workflow Permissions](#6-workflow-permissions)
- [Configuration Reference](#configuration-reference)
- [API & Library Reference](#api--library-reference)
  - [Authentication](#authentication)
  - [Workspace Items API](#microsoft-fabric-rest-api--workspace-items)
  - [Deployment Pipelines API](#microsoft-fabric-rest-api--deployment-pipelines)
  - [Long-Running Operations (LRO)](#long-running-operations-lro)
  - [Python Dependencies](#python-dependencies)
  - [GitHub Actions](#github-actions)
  - [Additional Resources](#additional-resources)
- [Troubleshooting](#troubleshooting)

---

## Overview

The automation in this repository covers three scenarios:

| Workflow | Purpose | Trigger |
|----------|---------|---------|
| **WorkspaceSync** | Download every artifact from a PowerBI workspace into the `workspace/` directory and commit to `main` | Scheduled (every 4 hours) or manual |
| **WorkspaceDeploy** | Push the `workspace/` directory contents to a target workspace via the Fabric REST API (create / update / delete items) | Manual — choose branch, environment, and optional single-item deploy |
| **WorkspacePipelineDeploy** | Promote artifacts through a **PowerBI Deployment Pipeline** (Dev → Test → Prod) using the built-in Deployment Pipelines API | Manual |

The `workspace/` folder in this repository contains example PowerBI artifacts (Notebooks, Semantic Models, a Lakehouse, a SQL Database, and an Environment). These are provided as sample content and are managed automatically by the sync and deploy pipelines.

---

## Repository Structure

```
.github/
  scripts/
    sync_powerbi.py          # Downloads workspace items to workspace/
    deploy_to_workspace.py   # Deploys workspace/ items to a target workspace
    requirements.txt         # Python dependency (requests)
  workflows/
    WorkspaceSync.yml         # Backup workflow
    WorkspaceDeploy.yml       # REST API deploy workflow
    WorkspacePipelineDeploy.yml  # PowerBI Deployment Pipeline workflow
workspace/                       # Artifact source files (auto-managed by sync)
```

---

## GitHub Actions Workflows

### WorkspaceSync — Source Backup

**File:** `.github/workflows/WorkspaceSync.yml`

Downloads the source definition of every item in a PowerBI workspace and commits the files to `main`. This gives you a full version-controlled backup of your workspace.

| Setting | Value |
|---------|-------|
| **Trigger** | Scheduled every 4 hours (`0 */4 * * *`), or manual `workflow_dispatch` |
| **Manual input** | Optional `workspace_id_override` to sync a different workspace without changing secrets |
| **Runs on** | `ubuntu-latest`, Python 3.12 |
| **Permissions** | `contents: write` (pushes commits via `GITHUB_TOKEN`) |

**What it does:**

1. Checks out `main`.
2. Runs `sync_powerbi.py`, which authenticates as a service principal, lists all workspace items, and calls `getDefinition` on each one.
3. Decoded source files are written to `workspace/<ItemName>.<ItemType>/`. Each file is **compared with its existing copy** before writing — unchanged files are skipped to avoid false-positive git diffs.
4. For ZIP-based formats (`.dacpac`, `.bacpac`, `.nupkg`), comparison uses the ZIP central-directory metadata (CRC-32, filename, uncompressed size) rather than raw bytes, because PowerBI regenerates the archive envelope on every export. Volatile members (`DacMetadata.xml`, `Origin.xml`) are excluded from the comparison.
5. A `workspace_manifest.json` is generated with metadata about the sync.
6. Changes are staged, committed, and pushed to `main` with `--force-with-lease`.

If there are no changes the workflow exits cleanly without creating a commit. The run summary logs per-item and per-file statistics (written vs. unchanged).

---

### WorkspaceDeploy — REST API Deploy

**File:** `.github/workflows/WorkspaceDeploy.yml`

Deploys artifact definitions from the `workspace/` directory directly to one or more target PowerBI workspaces using the Fabric REST API. This workflow does **not** use PowerBI Deployment Pipelines — it creates, updates, and deletes items directly.

| Setting | Value |
|---------|-------|
| **Trigger** | Manual `workflow_dispatch` only |
| **Permissions** | `contents: read` |

**Manual inputs:**

| Input | Description | Default |
|-------|-------------|---------|
| `branch` | Branch to deploy content from | `dev` |
| `environment` | Which environment(s) to target | `both` (options: `both`, `test-only`, `prod-only`) |
| `item` | Optional single-item deploy (e.g. `Add Calculated Measure.Notebook`). Leave blank for a full workspace deploy. | *(empty)* |

**How it works:**

1. **resolve-commit** — Resolves the HEAD SHA of the selected branch so both deploy jobs use the same ref.
2. **deploy-test** — Checks out `main` for pipeline scripts, then overlays the `workspace/` directory from the target branch. Runs `deploy_to_workspace.py` against the **TEST** workspace. Gated behind the `PowerBI-Test` environment.
3. **deploy-prod** — Same process targeting the **PROD** workspace, gated behind `PowerBI-Production`. Runs after test succeeds (or independently if `prod-only` is selected).

**Full deploy** (default, no `item` input): The target workspace is made to exactly mirror the repo — new items are created, existing items are updated (only when content has changed), and items in the workspace that are not in the repo are deleted.

**Selective deploy** (`item` input set): Only the named item and its transitive dependencies are deployed. Nothing is deleted.

**Content comparison before update:** For existing items, the deploy script downloads the current remote definition and compares it with the local files before calling `updateDefinition`. If the definitions match, the update is skipped entirely — this avoids unnecessary API calls, preserves item IDs, and prevents audit-log noise in the PowerBI portal. ZIP-based formats (`.dacpac`, etc.) use central-directory metadata comparison, and volatile members are excluded, matching the same logic used by the sync script.

---

### WorkspacePipelineDeploy — PowerBI Deployment Pipeline

**File:** `.github/workflows/WorkspacePipelineDeploy.yml`

Promotes artifacts through a PowerBI Deployment Pipeline using the native Deployment Pipelines API. This assumes you already have a configured Deployment Pipeline in PowerBI with workspaces assigned to each stage.

| Setting | Value |
|---------|-------|
| **Trigger** | Manual `workflow_dispatch` only |
| **Permissions** | `contents: read` |

**Jobs:**

1. **Promote to Test** — Logs in with a service principal via `azure/login`, discovers the pipeline and stage IDs by name, then triggers a deployment from the Development stage to the Test stage. Polls for completion. Gated behind `PowerBI-Test`.
2. **Promote to Production** — After Test succeeds, triggers a deployment from Test to Production. Polls for completion. Gated behind `PowerBI-Production`.

If the source and target stages are already in sync the workflow emits a warning and succeeds without error.

**Configurable environment variables** (set in the workflow file):

| Variable | Default | Description |
|----------|---------|-------------|
| `PowerBI_PIPELINE_NAME` | `PowerBI Deployment` | Display name of the PowerBI Deployment Pipeline |
| `DEV_WORKSPACE_NAME` | `PowerBI-DEV` | Development workspace name (used for lookup) |
| `DEV_STAGE_NAME` | `Development` | Stage name in the PowerBI pipeline |
| `TEST_STAGE_NAME` | `Test` | Stage name in the PowerBI pipeline |
| `PROD_STAGE_NAME` | `Production` | Stage name in the PowerBI pipeline |
| `DEBUG` | `1` | Set to `1` for verbose logging |

---

## Python Scripts

### sync_powerbi.py

**File:** `.github/scripts/sync_powerbi.py`

Authenticates as a service principal and downloads source definitions for every item in a PowerBI workspace.

**Supported item types** (anything that exposes `getDefinition`):

| Item Type | Files Saved |
|-----------|-------------|
| Notebook | `notebook-content.py` + `.platform` |
| SemanticModel | `model.bim` or `*.tmdl` tree + `.platform` |
| Report | `definition.pbir` + `report.json` + … + `.platform` |
| Lakehouse | `lakehouse.metadata.json` + `.platform` |
| DataPipeline | `pipeline-content.json` + `.platform` |
| SQLDatabase | `SqlDatabase.json` + `.platform` |
| Environment | `environment.yml` + `.platform` |
| … and more | Any type exposing `getDefinition` |

**Skipped types** (no downloadable definition): `SQLAnalyticsEndpoint`, `SQLEndpoint`, `Dashboard`, `MountedWarehouse`, `MountedDataFactory`.

**Key behaviors:**
- **Per-file content comparison** — each file is compared with the existing repo copy before writing. Identical files are skipped, so only genuine changes appear in the git diff.
- **ZIP-aware comparison** — `.dacpac`, `.bacpac`, and `.nupkg` files are compared by their central-directory metadata (CRC-32, filename, uncompressed size) instead of raw bytes. Members with volatile timestamps (`DacMetadata.xml`, `Origin.xml`) are excluded.
- Handles long-running operations (202 responses) with polling (5 s interval, 6 min max).
- Output directory structure mirrors PowerBI Git integration: `workspace/<DisplayName>.<ItemType>/`.
- Writes a `workspace_manifest.json` with a full inventory and sync timestamp.
- Logs per-item and per-file statistics: items changed / unchanged, files written / skipped.

**Required environment variables:**

| Variable | Description |
|----------|-------------|
| `TENANT_ID` | Azure AD tenant ID |
| `CLIENT_ID` | Service principal application (client) ID |
| `CLIENT_SECRET` | Service principal client secret |
| `WORKSPACE_ID` | PowerBI workspace ID to sync |

---

### deploy_to_workspace.py

**File:** `.github/scripts/deploy_to_workspace.py`

Reads the `workspace/` directory and pushes item definitions to a target workspace.

**Two modes of operation:**

| Mode | Behavior |
|------|----------|
| **Full deploy** (`DEPLOY_ITEM` not set) | Creates missing items, updates existing items, **deletes** workspace items not present in the repo |
| **Selective deploy** (`DEPLOY_ITEM` set) | Deploys only the named item and its transitive dependencies. Nothing is deleted. |

**Dependency resolution:**

The script automatically discovers cross-item dependencies so they are deployed in the correct order:

| Source Item | How Dependencies Are Found |
|-------------|---------------------------|
| **Notebook** | Parses `# META` JSON blocks for lakehouse and environment references |
| **SemanticModel** | Scans `.tmdl` files for `Sql.Database()` calls → Lakehouse dependencies |
| **Report** | Reads `.pbir` files for `datasetReference.byPath.datasetName` → SemanticModel dependencies |

Dependencies are resolved transitively and deployed in topological order (dependencies first).

**Item handling by type:**

| Category | Types | Behavior |
|----------|-------|----------|
| **Normal** | Notebook, SemanticModel, Report, DataPipeline, etc. | Full create/update via definition API |
| **Metadata-only** | Lakehouse, Environment, SQLDatabase, Warehouse | Created with name + type only (no definition upload). If the item already exists it is left as-is. |
| **Skipped** | SQLAnalyticsEndpoint, SQLEndpoint, Dashboard, MountedWarehouse, MountedDataFactory | Cannot be deployed; skipped automatically |

**SemanticModel format:** Definitions are uploaded and downloaded in `TMDL` format (configured via `FORMAT_BY_TYPE`). When fetching the remote definition for comparison, the same format is requested so the parts are directly comparable.

**`.platform` files** are excluded from the parts list sent to the API.

**Required environment variables:**

| Variable | Description |
|----------|-------------|
| `TENANT_ID` | Azure AD tenant ID |
| `CLIENT_ID` | Service principal application (client) ID |
| `CLIENT_SECRET` | Service principal client secret |
| `TARGET_WORKSPACE_ID` | PowerBI workspace ID to deploy into |
| `DEPLOY_ITEM` | *(Optional)* Folder name for selective deploy (e.g. `Add Calculated Measure.Notebook`) |

---

## Choosing a Deployment Strategy

This repo offers two ways to move artifacts between environments. You can use one or both.

| | WorkspaceDeploy (REST API) | WorkspacePipelineDeploy (PowerBI Pipeline) |
|-|---------------------------|------------------------------------------|
| **Mechanism** | Reads files from Git, pushes definitions directly to target workspace | Uses PowerBI's built-in Deployment Pipelines API to promote between stages |
| **Branch support** | Deploy from any branch (`dev`, feature branches, etc.) | N/A — operates on workspaces already assigned to pipeline stages |
| **Selective deploy** | Yes — deploy a single item + dependencies | No — promotes all artifacts in the stage |
| **Deletes stale items** | Yes (full deploy mode) | No — PowerBI pipelines only add/update |
| **Requires PowerBI Deployment Pipeline** | No | Yes |
| **Best for** | Git-driven workflows, branch-based promotion, fine-grained control | Organizations already using PowerBI Deployment Pipelines |

**Recommended workflow:**

1. Use **WorkspaceSync** on a schedule to keep `main` backed up with the latest workspace state.
2. Create feature branches from `main`, make changes, and merge via pull request.
3. Use **WorkspaceDeploy** to push branch content to Test and Production workspaces.

Alternatively, if your team prefers PowerBI's built-in promotion model, use **WorkspacePipelineDeploy** to push changes through the PowerBI Deployment Pipeline stages after syncing the development workspace.

---

## Setup & Configuration

### 1. Create an Azure AD Service Principal

Register an application in Azure AD (Microsoft Entra ID):

1. Go to **Azure Portal → Microsoft Entra ID → App registrations → New registration**.
2. Give it a name (e.g. `PowerBI-CI-CD`), select **Single tenant**, and register.
3. Note the **Application (client) ID** and **Directory (tenant) ID**.
4. Under **Certificates & secrets → Client secrets**, create a new secret and copy the value immediately.
5. Note your **Azure Subscription ID** (needed for the WorkspacePipelineDeploy workflow).

### 2. Grant PowerBI Permissions

The service principal needs access to your PowerBI workspaces:

**a) Enable Service Principal access in PowerBI:**

1. Go to the **PowerBI Admin Portal → Tenant settings**.
2. Under **Developer settings**, enable **Service principals can use PowerBI APIs**.
3. Add the service principal (or a security group containing it) to the allowed list.

**b) Add the Service Principal to each workspace:**

For every workspace the automation touches (Dev, Test, Production):

1. Open the workspace in PowerBI.
2. Go to **Manage access** (the people icon).
3. Click **Add people or groups** and search for your service principal by name.
4. Assign the **Admin** role.

**c) Add the Service Principal to the Deployment Pipeline** (WorkspacePipelineDeploy only):

1. Open the PowerBI Deployment Pipeline.
2. Go to pipeline settings and add the service principal as an **Admin**.

### 3. Configure GitHub Secrets

Go to **GitHub → Repository → Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret | Used By | Description |
|--------|---------|-------------|
| `POWERBI_TENANT_ID` | All workflows | Azure AD tenant ID |
| `POWERBI_CLIENT_ID` | All workflows | Service principal application (client) ID |
| `POWERBI_CLIENT_SECRET` | All workflows | Service principal client secret value |
| `POWERBI_WORKSPACE_ID` | WorkspaceSync | Source workspace ID for backup |
| `POWERBI_TEST_WORKSPACE_ID` | WorkspaceDeploy | Target workspace ID for Test environment |
| `POWERBI_PROD_WORKSPACE_ID` | WorkspaceDeploy | Target workspace ID for Production environment |
| `POWERBI_SUBSCRIPTION_ID` | WorkspacePipelineDeploy | Azure subscription ID (used by `azure/login`) |

> **Finding workspace IDs:** Open a workspace in PowerBI. The URL contains the workspace ID: `https://app.fabric.microsoft.com/groups/<workspace-id>/...`

### 4. Create GitHub Environments

Go to **GitHub → Repository → Settings → Environments** and create:

| Environment | Purpose | Recommended Protection |
|-------------|---------|----------------------|
| `PowerBI-Test` | Gates deployment to the Test workspace | Required reviewers |
| `PowerBI-Production` | Gates deployment to the Production workspace | Required reviewers, wait timer |

Environment protection rules control who can approve deployments and add optional wait timers before deployment begins.

### 5. PowerBI Deployment Pipeline Setup (WorkspacePipelineDeploy only)

If using the PowerBI Deployment Pipeline workflow:

1. In PowerBI, create a Deployment Pipeline named **`PowerBI Deployment`** (or update `PowerBI_PIPELINE_NAME` in the workflow).
2. Configure three stages named exactly: **`Development`**, **`Test`**, **`Production`**.
3. Assign the appropriate workspace to each stage.
4. Ensure the service principal has Admin access to the pipeline.

### 6. Workflow Permissions

For **WorkspaceSync** (which pushes commits):

1. Go to **GitHub → Repository → Settings → Actions → General**.
2. Under **Workflow permissions**, select **Read and write permissions**.
3. This allows the `GITHUB_TOKEN` to push commits to `main`.

---

## Configuration Reference

### WorkspaceDeploy Environment Variables

These are set in the workflow YAML and typically don't need to change:

| Variable | Location | Default |
|----------|----------|---------|
| `PYTHON_VERSION` | Workflow env | `3.12` |

### WorkspacePipelineDeploy Environment Variables

| Variable | Location | Default | Description |
|----------|----------|---------|-------------|
| `PowerBI_PIPELINE_NAME` | Workflow env | `PowerBI Deployment` | Must match the exact display name of your PowerBI Deployment Pipeline |
| `DEV_WORKSPACE_NAME` | Workflow env | `PowerBI-DEV` | Used for lookup if `DEV_WORKSPACE_ID` is empty |
| `DEV_STAGE_NAME` | Workflow env | `Development` | Must match the stage name in your pipeline |
| `TEST_STAGE_NAME` | Workflow env | `Test` | Must match the stage name in your pipeline |
| `PROD_STAGE_NAME` | Workflow env | `Production` | Must match the stage name in your pipeline |
| `DEBUG` | Workflow env | `1` | Set to `1` for verbose logging, `0` to disable |

### sync_powerbi.py Constants

These are set in the Python source and can be adjusted:

| Constant | Default | Description |
|----------|---------|-------------|
| `POLL_INTERVAL` | `5` seconds | Time between polling attempts for long-running operations |
| `POLL_MAX` | `72` | Maximum polling attempts (72 × 5 s = 6 min) |
| `NO_DEFINITION_TYPES` | SQLAnalyticsEndpoint, SQLEndpoint, Dashboard, MountedWarehouse, MountedDataFactory | Item types skipped (no downloadable definition) |
| `ZIP_EXTENSIONS` | `.dacpac`, `.bacpac`, `.nupkg` | File extensions compared via ZIP central-directory metadata |
| `ZIP_VOLATILE_MEMBERS` | `DacMetadata.xml`, `Origin.xml` | ZIP members excluded from content comparison (volatile timestamps) |

### deploy_to_workspace.py Constants

These are set in the Python source and can be adjusted:

| Constant | Default | Description |
|----------|---------|-------------|
| `POLL_INTERVAL` | `5` seconds | Time between polling attempts for long-running operations |
| `POLL_MAX` | `72` | Maximum polling attempts (72 × 5 s = 6 min) |
| `FORMAT_BY_TYPE` | `{"semanticmodel": "TMDL"}` | Definition format per item type (used for both upload and remote comparison) |
| `METADATA_ONLY_TYPES` | Lakehouse, Environment, SQLDatabase, Warehouse | Types created without definition upload |
| `EXCLUDED_FILES` | `.platform` | Files excluded from the parts list sent to the API |
| `NO_DEPLOY_TYPES` | SQLAnalyticsEndpoint, SQLEndpoint, Dashboard, MountedWarehouse, MountedDataFactory | Item types that cannot be deployed |
| `ZIP_EXTENSIONS` | `.dacpac`, `.bacpac`, `.nupkg` | File extensions compared via ZIP central-directory metadata |
| `ZIP_VOLATILE_MEMBERS` | `DacMetadata.xml`, `Origin.xml` | ZIP members excluded from content comparison (volatile timestamps) |

---

## API & Library Reference

A comprehensive reference of every external API endpoint and library used across the scripts and workflows, organized by functional area.

### Authentication

Both Python scripts authenticate using the **OAuth 2.0 client credentials** grant. The WorkspacePipelineDeploy workflow authenticates via the Azure CLI instead.

#### Microsoft Identity Platform — Token Endpoint

| | |
|-|-|
| **URL** | `POST https://login.microsoftonline.com/{tenantId}/oauth2/v2.0/token` |
| **Used by** | `sync_powerbi.py`, `deploy_to_workspace.py` |
| **Purpose** | Acquire a bearer token for the Fabric REST API using a service principal (client ID + secret). |
| **Request body** | `grant_type=client_credentials`, `client_id`, `client_secret`, `scope=https://api.fabric.microsoft.com/.default` |
| **Response** | JSON with `access_token` (used as `Authorization: Bearer …` header on all subsequent calls). |
| **Docs** | [Microsoft identity platform — client credentials flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow) |

#### Azure CLI — `az account get-access-token`

| | |
|-|-|
| **Command** | `az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv` |
| **Used by** | `WorkspacePipelineDeploy.yml` (PowerShell inline scripts) |
| **Purpose** | Obtain a Fabric bearer token from the Azure CLI session established by `azure/login@v2`. |
| **Why Azure CLI?** | The pipeline deploy workflow uses `az rest` for some API calls, which requires an active `az` login session. Using the CLI for token acquisition keeps authentication consistent within those scripts. |
| **Docs** | [az account get-access-token](https://learn.microsoft.com/en-us/cli/azure/account?view=azure-cli-latest#az-account-get-access-token) |

---

### Microsoft Fabric REST API — Workspace Items

Base URL: `https://api.fabric.microsoft.com/v1`

#### List Items

| | |
|-|-|
| **Endpoint** | `GET /workspaces/{workspaceId}/items` |
| **Used by** | `sync_powerbi.py` — discover all items for backup; `deploy_to_workspace.py` — inventory the target workspace to decide create vs. update vs. delete. |
| **Pagination** | OData-style — follow `continuationUri` in the response until absent. |
| **Response** | `{ "value": [ { "id", "displayName", "type", … } ], "continuationUri": "…" }` |
| **Docs** | [List Items](https://learn.microsoft.com/en-us/rest/api/fabric/core/items/list-items) |

#### Get Definition

| | |
|-|-|
| **Endpoint** | `POST /workspaces/{workspaceId}/items/{itemId}/getDefinition` |
| **Used by** | `sync_powerbi.py` — download source files for every item; `deploy_to_workspace.py` — fetch remote definition for content comparison before update. |
| **Request body** | Optional `{ "format": "TMDL" }` — required for SemanticModel so that parts are returned in TMDL layout (the default is `model.bim`). |
| **Response** | **200** — definition returned inline. **202** — long-running operation; poll via `x-ms-operation-id` (see [Operations](#long-running-operations-lro) below). **400/404** — item type does not support definitions. |
| **Returned payload** | `{ "definition": { "parts": [ { "path", "payload" (base64), "payloadType" } ] } }` |
| **Why POST?** | The API uses POST (not GET) because the request body can include format preferences. |
| **Docs** | [Get Item Definition](https://learn.microsoft.com/en-us/rest/api/fabric/core/items/get-item-definition) |

#### Create Item

| | |
|-|-|
| **Endpoint** | `POST /workspaces/{workspaceId}/items` |
| **Used by** | `deploy_to_workspace.py` — create items that don't exist in the target workspace. |
| **Request body** | `{ "displayName", "type", "definition": { "format"?, "parts": [ … ] } }` — for metadata-only types (Lakehouse, Environment, SQLDatabase, Warehouse) the `definition` field is omitted. |
| **Response** | **200/201** — item created, returns `{ "id", … }`. **202** — long-running operation. |
| **Docs** | [Create Item](https://learn.microsoft.com/en-us/rest/api/fabric/core/items/create-item) |

#### Update Definition

| | |
|-|-|
| **Endpoint** | `POST /workspaces/{workspaceId}/items/{itemId}/updateDefinition` |
| **Used by** | `deploy_to_workspace.py` — push updated source files to an existing item (only called when content comparison detects a difference). |
| **Request body** | `{ "definition": { "format"?, "parts": [ { "path", "payload", "payloadType" } ] } }` |
| **Response** | **200** — updated synchronously. **202** — long-running operation. **400** — invalid definition or unsupported type. |
| **Docs** | [Update Item Definition](https://learn.microsoft.com/en-us/rest/api/fabric/core/items/update-item-definition) |

#### Delete Item

| | |
|-|-|
| **Endpoint** | `DELETE /workspaces/{workspaceId}/items/{itemId}` |
| **Used by** | `deploy_to_workspace.py` — remove workspace items that no longer exist in the repo (full deploy mode only). |
| **Response** | **200** — deleted. **404** — already gone (treated as success). |
| **Docs** | [Delete Item](https://learn.microsoft.com/en-us/rest/api/fabric/core/items/delete-item) |

---

### Microsoft Fabric REST API — Deployment Pipelines

Used exclusively by `WorkspacePipelineDeploy.yml`.

#### List Deployment Pipelines

| | |
|-|-|
| **Endpoint** | `GET /v1/deploymentPipelines` |
| **Purpose** | Discover the pipeline ID by matching on the `displayName` configured in the workflow (`FABRIC_PIPELINE_NAME`). |
| **Docs** | [List Deployment Pipelines](https://learn.microsoft.com/en-us/rest/api/fabric/core/deployment-pipelines/list-deployment-pipelines) |

#### List Pipeline Stages

| | |
|-|-|
| **Endpoint** | `GET /v1/deploymentPipelines/{pipelineId}/stages` |
| **Purpose** | Discover stage IDs (Development, Test, Production) by matching on stage `displayName`. The stage IDs are required as `sourceStageId` / `targetStageId` in the deploy call. |
| **Docs** | [List Deployment Pipeline Stages](https://learn.microsoft.com/en-us/rest/api/fabric/core/deployment-pipelines/list-deployment-pipeline-stages) |

#### Deploy (Stage-to-Stage Promotion)

| | |
|-|-|
| **Endpoint** | `POST /v1/deploymentPipelines/{pipelineId}/deploy` |
| **Purpose** | Trigger an asynchronous promotion of all artifacts from one stage to another (Dev → Test, Test → Prod). |
| **Request body** | `{ "sourceStageId", "targetStageId", "note": "GH Dev→Test" }` |
| **Response** | **202** — deployment started. Headers: `x-ms-operation-id`, `Retry-After`, `Location` (status polling URL). |
| **Error handling** | A `NoItemsToDeploy` error means source and target are already in sync — the workflow treats this as a warning, not a failure. |
| **Docs** | [Deploy](https://learn.microsoft.com/en-us/rest/api/fabric/core/deployment-pipelines/deploy) |

---

### Long-Running Operations (LRO)

Several Fabric endpoints return **202 Accepted** for operations that take longer than a few seconds. Both Python scripts and the PowerShell pipeline deploy script poll for completion.

#### Poll Operation Status

| | |
|-|-|
| **Endpoint** | `GET /v1/operations/{operationId}` |
| **Purpose** | Check whether a long-running operation has completed. |
| **Response** | `{ "status": "Running" | "Succeeded" | "Failed" | "Cancelled", "error"?: { "message" } }` |
| **Poll cadence** | Every 5 seconds, up to 72 attempts (6 minutes) in both Python scripts. The PowerShell pipeline deploy uses the `Retry-After` header (default 10 s), up to 60 attempts. |

#### Get Operation Result

| | |
|-|-|
| **Endpoint** | `GET /v1/operations/{operationId}/result` |
| **Purpose** | Retrieve the output payload once an operation succeeds (e.g., the item definition returned by an async `getDefinition`). |
| **Docs** | [Long Running Operations](https://learn.microsoft.com/en-us/rest/api/fabric/core/long-running-operations) |

---

### Python Dependencies

| Library | Version | Purpose | Repository |
|---------|---------|---------|------------|
| **requests** | `>=2.31.0` | All HTTP communication in both Python scripts — Azure AD token acquisition, Fabric REST API calls, LRO polling. The only third-party dependency. | [github.com/psf/requests](https://github.com/psf/requests) |

Standard-library modules used: `os`, `sys`, `json`, `time`, `base64`, `pathlib`, `re`, `zipfile`, `io`.

### GitHub Actions

| Action | Version | Used by | Purpose |
|--------|---------|---------|---------|
| `actions/checkout@v4` | v4 | All workflows | Check out the repository at the correct ref |
| `actions/setup-python@v5` | v5 | WorkspaceSync, WorkspaceDeploy | Install Python 3.12 and `pip install -r requirements.txt` |
| `azure/login@v2` | v2 | WorkspacePipelineDeploy | Authenticate to Azure as the service principal (enables `az rest` / `az account get-access-token`) |

### Additional Resources

- [Microsoft Fabric REST API — Full Reference](https://learn.microsoft.com/en-us/rest/api/fabric/core)
- [PowerBI Items — Get Item Definition](https://learn.microsoft.com/en-us/rest/api/fabric/core/items/get-item-definition)
- [PowerBI Deployment Pipelines — Overview](https://learn.microsoft.com/en-us/fabric/cicd/deployment-pipelines/intro-to-deployment-pipelines)
- [PowerBI Git Integration — Overview](https://learn.microsoft.com/en-us/fabric/cicd/git-integration/intro-to-git-integration)
- [Microsoft Identity Platform — Client Credentials Flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow)
- [Azure CLI — `az rest` command](https://learn.microsoft.com/en-us/cli/azure/reference-index?view=azure-cli-latest#az-rest)
- [requests library documentation](https://docs.python-requests.org/en/latest/)

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `No access_token in authentication response` | Incorrect client ID, secret, or tenant ID | Verify `POWERBI_CLIENT_ID`, `POWERBI_CLIENT_SECRET`, and `POWERBI_TENANT_ID` secrets |
| `getDefinition` returns 400/404 for an item | Item type does not support definition download | Expected for auto-generated types (SQL endpoints, etc.) — the script skips these gracefully |
| `Pipeline 'PowerBI Deployment' not found` | Pipeline name mismatch | Ensure `PowerBI_PIPELINE_NAME` in the workflow matches the exact name in PowerBI |
| `Could not resolve source/target stage IDs` | Stage name mismatch | Ensure `DEV_STAGE_NAME`, `TEST_STAGE_NAME`, `PROD_STAGE_NAME` match the exact stage names in your PowerBI pipeline |
| `NoItemsToDeploy` warning | Source and target stages are already in sync | Not an error — the workflow succeeds with a warning |
| `updateDefinition failed (400)` | Item definition is invalid or the type doesn't support update | Check the item type; metadata-only types (Lakehouse, Environment) cannot be updated via definition API |
| WorkspaceSync commit push fails | `GITHUB_TOKEN` lacks write permission | Go to **Settings → Actions → General → Workflow permissions** and enable **Read and write** |
| Selective deploy can't find item | Folder name doesn't match | Use the exact folder name including the type suffix (e.g. `Add Calculated Measure.Notebook`) |
| LRO polling timeout | Operation took longer than 6 minutes | Increase `POLL_MAX` in the Python script |
