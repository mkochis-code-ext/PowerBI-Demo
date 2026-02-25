# Fabric Deployment Pipeline

This document describes the automated deployment pipeline for Microsoft Fabric workspaces using GitHub Actions and Fabric Deployment Pipelines.

## Overview

This pipeline automates the promotion of Fabric artifacts from Development through Test to Production environments using a Git-based workflow. The pipeline ensures that all changes are version-controlled, validated, and systematically deployed across environments with appropriate approvals.

## Architecture

### Git Integration
- **Development Workspace (`GIT-DEV`)**: The only workspace connected to Git. All development changes are committed and synchronized through this workspace.
- **Test Workspace**: Updated via Fabric Deployment Pipeline (not directly connected to Git)
- **Production Workspace**: Updated via Fabric Deployment Pipeline (not directly connected to Git)

### Deployment Flow
```
┌─────────────────────────────────────────────────────────────────┐
│  Developer Workflow (Git)                                       │
├─────────────────────────────────────────────────────────────────┤
│  1. Create Feature Branch                                       │
│  2. Make Changes in Fabric Workspace                            │
│  3. Commit to Feature Branch                                    │
│  4. Create Pull Request                                         │
│  5. Merge to Main Branch                                        │
└─────────────────────────────────────────────────────────────────┘
                              ↓
                    [Triggers GitHub Actions]
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 1: Development                                           │
│  ───────────────────────────────────────────────────────────    │
│  ✓ Sync GIT-DEV workspace with main branch                     │
│  ✓ Validate no uncommitted changes                             │
│  ✓ Resolve Fabric Pipeline IDs                                 │
│                                                                 │
│  [Requires: Fabric-Development approval]                        │
└─────────────────────────────────────────────────────────────────┘
                              ↓
                     [Fabric Deployment API]
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 2: Test                                                  │
│  ───────────────────────────────────────────────────────────    │
│  ✓ Deploy Development → Test                                   │
│  ✓ Poll deployment status until complete                       │
│                                                                 │
│  [Requires: Fabric-Test approval]                               │
└─────────────────────────────────────────────────────────────────┘
                              ↓
                     [Fabric Deployment API]
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 3: Production                                            │
│  ───────────────────────────────────────────────────────────    │
│  ✓ Deploy Test → Production                                    │
│  ✓ Poll deployment status until complete                       │
│                                                                 │
│  [Requires: Fabric-Production approval]                         │
└─────────────────────────────────────────────────────────────────┘
                              ↓
                        ✅ COMPLETE
```

## Development Workflow

### 1. Create a Feature Branch
Developers create a new Git branch to work on their changes:

```bash
git checkout -b feature/my-new-feature
```

### 2. Work in a Development Workspace
- Create or use a Fabric workspace for development
- Connect the workspace to your feature branch in Git
- Make changes to Fabric artifacts (notebooks, semantic models, reports, etc.)
- Test your changes in the development workspace

### 3. Commit Changes to Git
From within the Fabric workspace:
- Commit your changes to the feature branch
- Add meaningful commit messages describing the changes
- Ensure all artifacts are properly committed

### 4. Create a Pull Request
In GitHub:
- Create a pull request from your feature branch to `main`
- Add reviewers to review your changes
- Address any review comments
- Ensure all required checks pass

### 5. Merge to Main
Once approved:
- Merge the pull request to the `main` branch
- This automatically triggers the deployment pipeline

## Automated Deployment Pipeline

### Stage 1: Development (Automatic)
**Trigger**: Push to `main` branch or manual workflow dispatch

**Actions**:
1. Syncs the `GIT-DEV` workspace with the latest code from the `main` branch
2. Validates that the workspace has no uncommitted changes
3. Ensures the workspace is in sync with Git before proceeding
4. Resolves Fabric Deployment Pipeline and Stage IDs

**Validation**:
- Git connection status check
- Git credentials configuration (for service principal)
- Workspace sync verification
- Pending changes detection (fails if uncommitted changes exist)

**Approval**: Requires `Fabric-Development` environment approval (configured in GitHub)

### Stage 2: Test (Approval Required)
**Trigger**: Successful completion of Development stage + environment approval

**Actions**:
1. Deploys artifacts from Development stage to Test stage using Fabric Deployment Pipeline API
2. Polls deployment status until completion
3. Validates successful deployment

**Approval**: Requires `Fabric-Test` environment approval (configured in GitHub)

**Note**: If there are no changes between Development and Test, the deployment is skipped with a warning.

### Stage 3: Production (Approval Required)
**Trigger**: Successful completion of Test stage + environment approval

**Actions**:
1. Deploys artifacts from Test stage to Production stage using Fabric Deployment Pipeline API
2. Polls deployment status until completion
3. Validates successful deployment

**Approval**: Requires `Fabric-Production` environment approval (configured in GitHub)

**Note**: If there are no changes between Test and Production, the deployment is skipped with a warning.

## Prerequisites

### GitHub Secrets Configuration
The following secrets must be configured in your GitHub repository:

**Azure Service Principal Authentication**:
- `AZURE_CLIENT_ID`: Service principal application (client) ID
- `AZURE_CLIENT_SECRET`: Service principal client secret
- `AZURE_TENANT_ID`: Azure Active Directory tenant ID
- `AZURE_SUBSCRIPTION_ID`: Azure subscription ID

**Fabric Git Integration**:
- `FABRIC_GIT_CONNECTION_ID`: Pre-configured Git connection ID (recommended for service principals)

### GitHub Environments
Create the following environments in GitHub (Settings → Environments):
- `Fabric-Development`: Configure required reviewers for Development approval
- `Fabric-Test`: Configure required reviewers for Test approval
- `Fabric-Production`: Configure required reviewers for Production approval

### Service Principal Setup
The service principal must be granted proper permissions in Fabric:

**Step 1: Add Service Principal to Workspaces**
- Navigate to each workspace (Development, Test, Production) in Fabric
- Go to Workspace Settings → Manage Access
- Add the service principal as an **Admin**
- Repeat for all three workspaces

**Step 2: Add Service Principal to Deployment Pipeline**
- Navigate to the Fabric Deployment Pipeline in Fabric
- Go to Pipeline Settings → Manage Access
- Add the service principal as an **Admin**
- This allows the pipeline to trigger deployments between stages

**Step 3: Azure Subscription Permissions**
- Ensure the service principal has the ability to acquire access tokens for the Fabric API resource (`https://api.fabric.microsoft.com`)

### Fabric Configuration
1. **Create Fabric Deployment Pipeline**: Named `Git Deployment` (or update `FABRIC_PIPELINE_NAME` in workflow)
2. **Configure Stages**: Three stages named exactly:
   - `Development`
   - `Test`
   - `Production`
3. **Assign Workspaces**: Assign the appropriate workspace to each stage
4. **Git Connection**: Connect only the Development workspace (`GIT-DEV`) to your GitHub repository

## Key Features

### Git Validation
- Ensures the Development workspace is synchronized with Git before deployment
- Fails the pipeline if there are uncommitted changes in the workspace
- Prevents out-of-sync deployments

### Long Polling
- Test and Production stages poll deployment status until completion
- Prevents premature progression to next stage
- Provides real-time deployment status updates

### Error Handling
- Graceful handling of "no changes to deploy" scenarios
- Detailed error messages for troubleshooting
- Git credentials configuration with fallback options

### Debug Mode
Set `DEBUG: '1'` in the workflow file to enable verbose logging for troubleshooting.

## Monitoring and Troubleshooting

### GitHub Actions UI
- Navigate to Actions tab in GitHub repository
- View workflow runs, logs, and approval status
- Monitor deployment progress in real-time

### Common Issues

**Git Credentials Not Configured**:
- Ensure `FABRIC_GIT_CONNECTION_ID` secret is set with valid connection ID
- Verify service principal has proper workspace permissions
- Check Git connection exists in Fabric workspace settings

**Deployment Fails**:
- Review deployment pipeline configuration in Fabric
- Verify workspace assignments to pipeline stages
- Check service principal permissions on target workspaces

**No Items to Deploy**:
- This is expected when stages are already in sync
- Pipeline continues with warning message

## Best Practices

1. **Always work in feature branches**: Never commit directly to `main`
2. **Test thoroughly before merging**: Validate changes in your development workspace
3. **Use meaningful commit messages**: Helps track changes across environments
4. **Review pull requests carefully**: Ensure changes are reviewed before deployment
5. **Monitor deployment logs**: Check GitHub Actions logs for any warnings or errors
6. **Keep workspaces in sync**: Ensure Development workspace commits all changes regularly

## Pipeline Maintenance

### Updating Configuration
Key configuration values are defined in the `env` section of the workflow file:
- `FABRIC_PIPELINE_NAME`: Name of your Fabric Deployment Pipeline
- `DEV_WORKSPACE_NAME`: Name of your Development workspace
- `DEV_STAGE_NAME`, `TEST_STAGE_NAME`, `PROD_STAGE_NAME`: Stage names in Fabric pipeline

### Modifying Approval Flow
Edit environment protection rules in GitHub Settings → Environments to change:
- Required reviewers
- Wait timer before deployment
- Environment secrets

## Fabric REST API Endpoints

This pipeline uses the following Microsoft Fabric REST API endpoints. All endpoints are called using the base URL `https://api.fabric.microsoft.com`.

### Workspace APIs

| Endpoint | Method | Purpose | Documentation |
|----------|--------|---------|---------------|
| `/v1/workspaces` | GET | List all workspaces to resolve workspace ID by name | [List Workspaces](https://learn.microsoft.com/en-us/rest/api/fabric/core/workspaces/list-workspaces) |

### Git Integration APIs

| Endpoint | Method | Purpose | Documentation |
|----------|--------|---------|---------------|
| `/v1/workspaces/{workspaceId}/git/connection` | GET | Get Git connection details for a workspace | [Get Git Connection](https://learn.microsoft.com/en-us/rest/api/fabric/core/git/get-connection) |
| `/v1/workspaces/{workspaceId}/git/myGitCredentials` | GET | Get current Git credentials configuration | [Get Git Credentials](https://learn.microsoft.com/en-us/rest/api/fabric/core/git/get-git-credentials) |
| `/v1/workspaces/{workspaceId}/git/myGitCredentials` | PUT | Configure Git credentials for service principal access | [Update Git Credentials](https://learn.microsoft.com/en-us/rest/api/fabric/core/git/update-git-credentials) |
| `/v1/workspaces/{workspaceId}/git/status` | GET | Get Git sync status and pending changes | [Get Git Status](https://learn.microsoft.com/en-us/rest/api/fabric/core/git/get-status) |
| `/v1/workspaces/{workspaceId}/git/updateFromGit` | POST | Sync workspace with Git repository (pull changes) | [Update From Git](https://learn.microsoft.com/en-us/rest/api/fabric/core/git/update-from-git) |

### Deployment Pipeline APIs

| Endpoint | Method | Purpose | Documentation |
|----------|--------|---------|---------------|
| `/v1/deploymentPipelines` | GET | List all deployment pipelines to resolve pipeline ID | [List Deployment Pipelines](https://learn.microsoft.com/en-us/rest/api/fabric/core/deployment-pipelines/list-deployment-pipelines) |
| `/v1/deploymentPipelines/{pipelineId}/stages` | GET | List stages in a deployment pipeline | [List Deployment Pipeline Stages](https://learn.microsoft.com/en-us/rest/api/fabric/core/deployment-pipelines/list-deployment-pipeline-stages) |
| `/v1/deploymentPipelines/{pipelineId}/deploy` | POST | Deploy artifacts from source stage to target stage | [Deploy Stage Content](https://learn.microsoft.com/en-us/rest/api/fabric/core/deployment-pipelines/deploy-stage-content) |
| `{Location header URL}` | GET | Poll long-running deployment operation status | [Long Running Operations](https://learn.microsoft.com/en-us/rest/api/fabric/core/long-running-operations) |

### API Authentication

All API calls use Azure AD bearer token authentication:
```powershell
$token = az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv
```

The service principal must have appropriate permissions on workspaces and deployment pipelines as described in the [Service Principal Setup](#service-principal-setup) section.

### Additional Resources

- [Microsoft Fabric REST API Overview](https://learn.microsoft.com/en-us/rest/api/fabric/)
- [Fabric Git Integration](https://learn.microsoft.com/en-us/fabric/cicd/git-integration/intro-to-git-integration)
- [Fabric Deployment Pipelines](https://learn.microsoft.com/en-us/fabric/cicd/deployment-pipelines/intro-to-deployment-pipelines)

## Support

For issues or questions:
1. Review GitHub Actions workflow logs
2. Check Fabric workspace and deployment pipeline status
3. Verify all prerequisites are configured correctly
4. Consult Fabric API documentation for API-related issues
