# Remote MCP deployment

A Foundry prompt agent cannot call `localhost`. This deployment creates the smallest
durable demo shape: an external HTTPS Azure Container App, scale-to-zero, one replica
maximum, and an Azure Files mount for the SQLite database. SQLite remains **demo-only**.

## Deploy

Build the image in an existing Azure Container Registry, then deploy the Bicep:

```powershell
$env:MCP_TOOL_API_KEY = Read-Host "Generate/paste a high-entropy tool key"
$env:MCP_OPERATOR_API_KEY = Read-Host "Generate/paste a different operator key"
az acr build --registry <acr-name> --image foundry-change-control:v1 13-human-approval
az deployment group create `
  --resource-group <resource-group> `
  --template-file 13-human-approval/infra/main.bicep `
  --parameters containerImage=<acr-name>.azurecr.io/foundry-change-control:v1 `
               containerRegistryName=<acr-name> `
               mcpToolApiKey=$env:MCP_TOOL_API_KEY `
               mcpOperatorApiKey=$env:MCP_OPERATOR_API_KEY
```

The ACR must be in the deployment resource group. Bicep creates a user-assigned managed
identity, grants it `AcrPull` scoped to that registry, and configures Container Apps
registry authentication; no registry password is stored.

The commands do not echo either key from this repository. Do not place them on a command
line retained in shell history; pass it from a secure process environment or approved
secret manager in real automation.

Create a **Foundry project custom-key connection** whose target is the `mcpUrl` output
and whose `x-mcp-api-key` value is **only the tool key**. Set
`MCP_CHANGE_CONTROL_CONNECTION_NAME` to that connection name. The prompt agent receives
only the connection ID; it cannot register or submit operator decisions.

For the operator registration/decision/audit channel and the explicit tool replay probe,
set the separate values only in the current process:

```powershell
$env:MCP_CHANGE_CONTROL_URL = "https://<app-fqdn>/mcp"
$env:MCP_CHANGE_CONTROL_TOOL_API_KEY = Read-Host "Tool key"
$env:MCP_CHANGE_CONTROL_OPERATOR_API_KEY = Read-Host "Operator key"
uv run python 13-human-approval/run_approval_demo.py --change-request CRQ-1003
```

## Cost and cleanup

Container Apps compute scales to zero, but Azure Files, the managed environment, log
ingestion (if enabled), and the registry can still incur charges. The demo deliberately
caps the app at one replica because SQLite has one writer. Remove the resource group
after the demo, or delete the Container App, managed environment, storage account and
test image individually if the group is shared.

```powershell
az group delete --name <dedicated-demo-resource-group> --yes --no-wait
```
