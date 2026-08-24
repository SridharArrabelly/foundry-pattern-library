# Remote MCP deployment

A Foundry prompt agent cannot call `localhost`. The default deployment creates the
smallest remote demo shape: an external HTTPS Azure Container App with exactly one
replica and ephemeral SQLite state. SQLite remains **demo-only** and a container restart
resets the demo data.

An optional Azure Files-backed SQLite volume is available with
`useAzureFiles=true`, but Container Apps mounts Azure Files with the storage account key.
Many enterprise policies (correctly) disable shared-key access. Don't weaken those policies
for this demo; use the default ephemeral mode or adapt the service to your approved durable
workflow store.

## Deploy

Build the image in an existing Azure Container Registry, then deploy the Bicep:

```powershell
$acr = "<acr-name>"
$mode = az acr show --name $acr --query roleAssignmentMode -o tsv
$roleMode = if ($mode -eq "AbacRepositoryPermissions") { "rbac-abac" } else { "rbac" }
$armAudience = az acr config authentication-as-arm show --registry $acr --query status -o tsv
if ($armAudience -ne "enabled") {
  az acr config authentication-as-arm update --registry $acr --status enabled
}

$env:MCP_TOOL_API_KEY = Read-Host "Generate/paste a high-entropy tool key"
$env:MCP_OPERATOR_API_KEY = Read-Host "Generate/paste a different operator key"
az acr build --registry $acr --image foundry-change-control:v1 13-human-approval
az deployment group create `
  --resource-group <resource-group> `
  --template-file 13-human-approval/infra/main.bicep `
  --parameters containerImage="${acr}.azurecr.io/foundry-change-control:v1" `
               containerRegistryName=$acr `
               containerRegistryAuthorizationMode=$roleMode `
               useAzureFiles=false `
               mcpToolApiKey=$env:MCP_TOOL_API_KEY `
               mcpOperatorApiKey=$env:MCP_OPERATOR_API_KEY
```

The ACR must be in the deployment resource group. Bicep creates a user-assigned managed
identity and configures Container Apps registry authentication; no registry password is
stored. For a registry using classic RBAC it grants `AcrPull`. For an ABAC-enabled registry
(`roleAssignmentMode == AbacRepositoryPermissions`) it grants **Container Registry
Repository Reader** instead. Managed-identity image pulls also require ACR's
`authentication-as-arm` policy to be enabled; the preflight above checks and enables it.
Do not skip the preflight or assume an arbitrary existing ACR supports the selected role.

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

The default ephemeral deployment holds one replica so SQLite state survives between demo
calls; compute, the managed environment, log ingestion (if enabled), and the registry can
incur charges. The app is capped at one replica because SQLite has one writer. If an
approved Azure Files configuration is used, `minReplicas=0` can preserve the file while
compute scales to zero. Remove the dedicated resource group after the demo.

```powershell
az group delete --name <dedicated-demo-resource-group> --yes --no-wait
```
