# Fabric CI/CD Automation

Automated backup, deployment, and promotion of Microsoft Fabric workspace artifacts using GitHub Actions. This repository provides **two independent deployment strategies** — choose the one that fits your workflow, or use both together.

## Table of Contents

- [Overview](#overview)
- [Repository Structure](#repository-structure)
- [GitHub Actions Workflows](#github-actions-workflows)
  - [WorkspaceSync — Source Backup](#workspacesync--source-backup)
  - [WorkspaceDeploy — REST API Deploy](#workspacedeploy--rest-api-deploy)
  - [WorkspacePipelineDeploy — Fabric Deployment Pipeline](#workspacepipelinedeploy--fabric-deployment-pipeline)
- [Python Scripts](#python-scripts)
  - [sync_powerbi.py](#sync_powerbipy)
  - [deploy_to_workspace.py](#deploy_to_workspacepy)
- [Choosing a Deployment Strategy](#choosing-a-deployment-strategy)
- [Setup & Configuration](#setup--configuration)
  - [1. Create an Azure AD Service Principal](#1-create-an-azure-ad-service-principal)
  - [2. Grant Fabric Permissions](#2-grant-fabric-permissions)
  - [3. Configure GitHub Secrets](#3-configure-github-secrets)
  - [4. Create GitHub Environments](#4-create-github-environments)
  - [5. Fabric Deployment Pipeline Setup (WorkspacePipelineDeploy only)](#5-fabric-deployment-pipeline-setup-workspacepipelinedeploy-only)
  - [6. Workflow Permissions](#6-workflow-permissions)
- [Configuration Reference](#configuration-reference)
- [Fabric REST API Endpoints Used](#fabric-rest-api-endpoints-used)
- [Troubleshooting](#troubleshooting)

---

## Overview

The automation in this repository covers three scenarios:

| Workflow | Purpose | Trigger |
|----------|---------|---------|
| **WorkspaceSync** | Download every artifact from a Fabric workspace into the `fabric/` directory and commit to `main` | Scheduled (daily) or manual |
| **WorkspaceDeploy** | Push the `fabric/` directory contents to a target workspace via the Fabric REST API (create / update / delete items) | Manual — choose branch, environment, and optional single-item deploy |
| **WorkspacePipelineDeploy** | Promote artifacts through a **Fabric Deployment Pipeline** (Dev → Test → Prod) using the built-in Deployment Pipelines API | Manual |

The `fabric/` folder in this repository contains example Fabric artifacts (Notebooks, Semantic Models, a Lakehouse, a SQL Database, and an Environment). These are provided as sample content and are managed automatically by the sync and deploy pipelines.

---

## Repository Structure

```
.github/
  scripts/
    sync_powerbi.py          # Downloads workspace items to fabric/
    deploy_to_workspace.py   # Deploys fabric/ items to a target workspace
    requirements.txt         # Python dependency (requests)
  workflows/
    WorkspaceSync.yml         # Backup workflow
    WorkspaceDeploy.yml       # REST API deploy workflow
    WorkspacePipelineDeploy.yml  # Fabric Deployment Pipeline workflow
fabric/                       # Artifact source files (auto-managed by sync)
```

---

## GitHub Actions Workflows

### WorkspaceSync — Source Backup

**File:** `.github/workflows/WorkspaceSync.yml`

Downloads the source definition of every item in a Fabric workspace and commits the files to `main`. This gives you a full version-controlled backup of your workspace.

| Setting | Value |
|---------|-------|
| **Trigger** | Scheduled daily at 02:00 UTC, or manual `workflow_dispatch` |
| **Manual input** | Optional `workspace_id_override` to sync a different workspace without changing secrets |
| **Runs on** | `ubuntu-latest`, Python 3.12 |
| **Permissions** | `contents: write` (pushes commits via `GITHUB_TOKEN`) |

**What it does:**

1. Checks out `main`.
2. Runs `sync_powerbi.py`, which authenticates as a service principal, lists all workspace items, and calls `getDefinition` on each one.
3. Decoded source files are written to `fabric/<ItemName>.<ItemType>/`.
4. A `workspace_manifest.json` is generated with metadata about the sync.
5. Changes are staged, committed, and pushed to `main` with `--force-with-lease`.

If there are no changes the workflow exits cleanly without creating a commit.

---

### WorkspaceDeploy — REST API Deploy

**File:** `.github/workflows/WorkspaceDeploy.yml`

Deploys artifact definitions from the `fabric/` directory directly to one or more target Fabric workspaces using the Fabric REST API. This workflow does **not** use Fabric Deployment Pipelines — it creates, updates, and deletes items directly.

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
2. **deploy-test** — Checks out `main` for pipeline scripts, then overlays the `fabric/` directory from the target branch. Runs `deploy_to_workspace.py` against the **TEST** workspace. Gated behind the `Fabric-Test` environment.
3. **deploy-prod** — Same process targeting the **PROD** workspace, gated behind `Fabric-Production`. Runs after test succeeds (or independently if `prod-only` is selected).

**Full deploy** (default, no `item` input): The target workspace is made to exactly mirror the repo — new items are created, existing items are updated, and items in the workspace that are not in the repo are deleted.

**Selective deploy** (`item` input set): Only the named item and its transitive dependencies are deployed. Nothing is deleted.

---

### WorkspacePipelineDeploy — Fabric Deployment Pipeline

**File:** `.github/workflows/WorkspacePipelineDeploy.yml`

Promotes artifacts through a Fabric Deployment Pipeline using the native Deployment Pipelines API. This assumes you already have a configured Deployment Pipeline in Fabric with workspaces assigned to each stage.

| Setting | Value |
|---------|-------|
| **Trigger** | Manual `workflow_dispatch` only |
| **Permissions** | `contents: read` |

**Jobs:**

1. **Promote to Test** — Logs in with a service principal via `azure/login`, discovers the pipeline and stage IDs by name, then triggers a deployment from the Development stage to the Test stage. Polls for completion. Gated behind `Fabric-Test`.
2. **Promote to Production** — After Test succeeds, triggers a deployment from Test to Production. Polls for completion. Gated behind `Fabric-Production`.

If the source and target stages are already in sync the workflow emits a warning and succeeds without error.

**Configurable environment variables** (set in the workflow file):

| Variable | Default | Description |
|----------|---------|-------------|
| `FABRIC_PIPELINE_NAME` | `PowerBI Deployment` | Display name of the Fabric Deployment Pipeline |
| `DEV_WORKSPACE_NAME` | `PowerBI-DEV` | Development workspace name (used for lookup) |
| `DEV_STAGE_NAME` | `Development` | Stage name in the Fabric pipeline |
| `TEST_STAGE_NAME` | `Test` | Stage name in the Fabric pipeline |
| `PROD_STAGE_NAME` | `Production` | Stage name in the Fabric pipeline |
| `DEBUG` | `1` | Set to `1` for verbose logging |

---

## Python Scripts

### sync_powerbi.py

**File:** `.github/scripts/sync_powerbi.py`

Authenticates as a service principal and downloads source definitions for every item in a Fabric workspace.

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
- Handles long-running operations (202 responses) with polling (5 s interval, 6 min max).
- Output directory structure mirrors Fabric Git integration: `fabric/<DisplayName>.<ItemType>/`.
- Writes a `workspace_manifest.json` with a full inventory and sync timestamp.

**Required environment variables:**

| Variable | Description |
|----------|-------------|
| `TENANT_ID` | Azure AD tenant ID |
| `CLIENT_ID` | Service principal application (client) ID |
| `CLIENT_SECRET` | Service principal client secret |
| `WORKSPACE_ID` | Fabric workspace ID to sync |

---

### deploy_to_workspace.py

**File:** `.github/scripts/deploy_to_workspace.py`

Reads the `fabric/` directory and pushes item definitions to a target workspace.

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

**SemanticModel format:** Definitions are uploaded in `TMDL` format (configured via `FORMAT_BY_TYPE`).

**`.platform` files** are excluded from the parts list sent to the API.

**Required environment variables:**

| Variable | Description |
|----------|-------------|
| `TENANT_ID` | Azure AD tenant ID |
| `CLIENT_ID` | Service principal application (client) ID |
| `CLIENT_SECRET` | Service principal client secret |
| `TARGET_WORKSPACE_ID` | Fabric workspace ID to deploy into |
| `DEPLOY_ITEM` | *(Optional)* Folder name for selective deploy (e.g. `Add Calculated Measure.Notebook`) |

---

## Choosing a Deployment Strategy

This repo offers two ways to move artifacts between environments. You can use one or both.

| | WorkspaceDeploy (REST API) | WorkspacePipelineDeploy (Fabric Pipeline) |
|-|---------------------------|------------------------------------------|
| **Mechanism** | Reads files from Git, pushes definitions directly to target workspace | Uses Fabric's built-in Deployment Pipelines API to promote between stages |
| **Branch support** | Deploy from any branch (`dev`, feature branches, etc.) | N/A — operates on workspaces already assigned to pipeline stages |
| **Selective deploy** | Yes — deploy a single item + dependencies | No — promotes all artifacts in the stage |
| **Deletes stale items** | Yes (full deploy mode) | No — Fabric pipelines only add/update |
| **Requires Fabric Deployment Pipeline** | No | Yes |
| **Best for** | Git-driven workflows, branch-based promotion, fine-grained control | Organizations already using Fabric Deployment Pipelines |

**Recommended workflow:**

1. Use **WorkspaceSync** on a schedule to keep `main` backed up with the latest workspace state.
2. Create feature branches from `main`, make changes, and merge via pull request.
3. Use **WorkspaceDeploy** to push branch content to Test and Production workspaces.

Alternatively, if your team prefers Fabric's built-in promotion model, use **WorkspacePipelineDeploy** to push changes through the Fabric Deployment Pipeline stages after syncing the development workspace.

---

## Setup & Configuration

### 1. Create an Azure AD Service Principal

Register an application in Azure AD (Microsoft Entra ID):

1. Go to **Azure Portal → Microsoft Entra ID → App registrations → New registration**.
2. Give it a name (e.g. `Fabric-CI-CD`), select **Single tenant**, and register.
3. Note the **Application (client) ID** and **Directory (tenant) ID**.
4. Under **Certificates & secrets → Client secrets**, create a new secret and copy the value immediately.
5. Note your **Azure Subscription ID** (needed for the WorkspacePipelineDeploy workflow).

### 2. Grant Fabric Permissions

The service principal needs access to your Fabric workspaces:

**a) Enable Service Principal access in Fabric:**

1. Go to the **Fabric Admin Portal → Tenant settings**.
2. Under **Developer settings**, enable **Service principals can use Fabric APIs**.
3. Add the service principal (or a security group containing it) to the allowed list.

**b) Add the Service Principal to each workspace:**

For every workspace the automation touches (Dev, Test, Production):

1. Open the workspace in Fabric.
2. Go to **Manage access** (the people icon).
3. Click **Add people or groups** and search for your service principal by name.
4. Assign the **Admin** role.

**c) Add the Service Principal to the Deployment Pipeline** (WorkspacePipelineDeploy only):

1. Open the Fabric Deployment Pipeline.
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

> **Finding workspace IDs:** Open a workspace in Fabric. The URL contains the workspace ID: `https://app.fabric.microsoft.com/groups/<workspace-id>/...`

### 4. Create GitHub Environments

Go to **GitHub → Repository → Settings → Environments** and create:

| Environment | Purpose | Recommended Protection |
|-------------|---------|----------------------|
| `Fabric-Test` | Gates deployment to the Test workspace | Required reviewers |
| `Fabric-Production` | Gates deployment to the Production workspace | Required reviewers, wait timer |

Environment protection rules control who can approve deployments and add optional wait timers before deployment begins.

### 5. Fabric Deployment Pipeline Setup (WorkspacePipelineDeploy only)

If using the Fabric Deployment Pipeline workflow:

1. In Fabric, create a Deployment Pipeline named **`PowerBI Deployment`** (or update `FABRIC_PIPELINE_NAME` in the workflow).
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
| `FABRIC_PIPELINE_NAME` | Workflow env | `PowerBI Deployment` | Must match the exact display name of your Fabric Deployment Pipeline |
| `DEV_WORKSPACE_NAME` | Workflow env | `PowerBI-DEV` | Used for lookup if `DEV_WORKSPACE_ID` is empty |
| `DEV_STAGE_NAME` | Workflow env | `Development` | Must match the stage name in your pipeline |
| `TEST_STAGE_NAME` | Workflow env | `Test` | Must match the stage name in your pipeline |
| `PROD_STAGE_NAME` | Workflow env | `Production` | Must match the stage name in your pipeline |
| `DEBUG` | Workflow env | `1` | Set to `1` for verbose logging, `0` to disable |

### deploy_to_workspace.py Constants

These are set in the Python source and can be adjusted:

| Constant | Default | Description |
|----------|---------|-------------|
| `POLL_INTERVAL` | `5` seconds | Time between polling attempts for long-running operations |
| `POLL_MAX` | `72` | Maximum polling attempts (72 × 5 s = 6 min) |
| `FORMAT_BY_TYPE` | `{"semanticmodel": "TMDL"}` | Definition format per item type |
| `METADATA_ONLY_TYPES` | Lakehouse, Environment, SQLDatabase, Warehouse | Types created without definition upload |
| `EXCLUDED_FILES` | `.platform` | Files excluded from the parts list sent to the API |

---

## Fabric REST API Endpoints Used

All endpoints use the base URL `https://api.fabric.microsoft.com`.

### Workspace & Item APIs

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/workspaces/{workspaceId}/items` | GET | List all items in a workspace |
| `/v1/workspaces/{workspaceId}/items/{itemId}/getDefinition` | POST | Download item source definition |
| `/v1/workspaces/{workspaceId}/items` | POST | Create a new item (with or without definition) |
| `/v1/workspaces/{workspaceId}/items/{itemId}/updateDefinition` | POST | Update an existing item's definition |
| `/v1/workspaces/{workspaceId}/items/{itemId}` | DELETE | Delete an item from the workspace |

### Deployment Pipeline APIs

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/deploymentPipelines` | GET | List all deployment pipelines |
| `/v1/deploymentPipelines/{pipelineId}/stages` | GET | List stages in a pipeline |
| `/v1/deploymentPipelines/{pipelineId}/deploy` | POST | Trigger a stage-to-stage deployment |

### Operations API

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/operations/{operationId}` | GET | Poll a long-running operation status |
| `/v1/operations/{operationId}/result` | GET | Retrieve the result of a completed operation |

### Authentication

**Python scripts** use OAuth 2.0 client credentials flow directly:

```
POST https://login.microsoftonline.com/{tenantId}/oauth2/v2.0/token
Scope: https://api.fabric.microsoft.com/.default
```

**WorkspacePipelineDeploy** uses the Azure CLI via `azure/login`:

```shell
az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv
```

### Additional Resources

- [Microsoft Fabric REST API Overview](https://learn.microsoft.com/en-us/rest/api/fabric/)
- [Fabric Git Integration](https://learn.microsoft.com/en-us/fabric/cicd/git-integration/intro-to-git-integration)
- [Fabric Deployment Pipelines](https://learn.microsoft.com/en-us/fabric/cicd/deployment-pipelines/intro-to-deployment-pipelines)

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `No access_token in authentication response` | Incorrect client ID, secret, or tenant ID | Verify `POWERBI_CLIENT_ID`, `POWERBI_CLIENT_SECRET`, and `POWERBI_TENANT_ID` secrets |
| `getDefinition` returns 400/404 for an item | Item type does not support definition download | Expected for auto-generated types (SQL endpoints, etc.) — the script skips these gracefully |
| `Pipeline 'PowerBI Deployment' not found` | Pipeline name mismatch | Ensure `FABRIC_PIPELINE_NAME` in the workflow matches the exact name in Fabric |
| `Could not resolve source/target stage IDs` | Stage name mismatch | Ensure `DEV_STAGE_NAME`, `TEST_STAGE_NAME`, `PROD_STAGE_NAME` match the exact stage names in your Fabric pipeline |
| `NoItemsToDeploy` warning | Source and target stages are already in sync | Not an error — the workflow succeeds with a warning |
| `updateDefinition failed (400)` | Item definition is invalid or the type doesn't support update | Check the item type; metadata-only types (Lakehouse, Environment) cannot be updated via definition API |
| WorkspaceSync commit push fails | `GITHUB_TOKEN` lacks write permission | Go to **Settings → Actions → General → Workflow permissions** and enable **Read and write** |
| Selective deploy can't find item | Folder name doesn't match | Use the exact folder name including the type suffix (e.g. `Add Calculated Measure.Notebook`) |
| LRO polling timeout | Operation took longer than 6 minutes | Increase `POLL_MAX` in the Python script |
