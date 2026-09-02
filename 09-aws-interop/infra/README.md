# Pattern-owned Azure deployment

This deployment creates only Pattern 9 resources:

- one Basic Azure Container Registry with admin access disabled;
- one Consumption workload-profile Container Apps managed environment without Log Analytics;
- one external Container App capped at `0.25` vCPU, `0.5 GiB`, one replica, and
  `minReplicas: 0`;
- three APIs inside an existing APIM instance: REST, REST-backed MCP, and A2A;
- one secret APIM named value for APIM-to-backend authentication;
- one APIM product and one product-scoped subscription.

The script requires explicit subscription and APIM parameters. It checks the selected
subscription, APIM provisioning state, global MCP body-logging safety, ownership tags, and
ownership descriptions before writing anything. It never changes an existing API, product,
subscription, named value, resource group, or policy that lacks the Pattern 9 marker. The
public Container App rejects capability, quote, agent-card, and A2A traffic unless APIM adds
the rotated pattern-owned backend key; only the liveness endpoint remains directly readable.

## Deploy

```powershell
$evidenceDir = Join-Path $env:TEMP "pattern9-live"
.\09-aws-interop\infra\deploy.ps1 `
  -SubscriptionId "<subscription-id>" `
  -ApimResourceGroup "<apim-resource-group>" `
  -ApimName "<apim-name>" `
  -OutputEnvPath (Join-Path $evidenceDir "runtime.env")

Get-Content (Join-Path $evidenceDir "runtime.env") |
  ForEach-Object {
    $name, $value = $_ -split "=", 2
    [Environment]::SetEnvironmentVariable($name, $value, "Process")
  }

uv run python 09-aws-interop/verify_live.py `
  --evidence (Join-Path $evidenceDir "live-evidence.json")
```

`runtime.env` contains an APIM subscription key and must remain outside Git. The verification
artifact is sanitized and records which components were real and which were simulated.

For the optional real Foundry prompt-agent invocation, create a temporary project connection
without putting its key on the command line:

```powershell
$projectId = "<complete-foundry-project-resource-id>"
.\09-aws-interop\infra\foundry_connection.ps1 `
  -Action create `
  -ProjectResourceId $projectId
$env:PATTERN9_FOUNDRY_CONNECTION_NAME = "pattern9-apim-mcp"
uv run python 09-aws-interop\foundry_mcp_agent.py
.\09-aws-interop\infra\foundry_connection.ps1 `
  -Action delete `
  -ProjectResourceId $projectId
```

## Cost boundary

The Container App scales to zero, so it has no idle replica compute charge. Active requests
consume Container Apps vCPU-seconds, GiB-seconds, and request units after the subscription's
monthly Consumption free grant. The managed environment has no dedicated workload-profile
charge and sends no logs to Log Analytics. The pattern-owned Basic registry is the only
continuously billed resource: the published retail meter is **USD 0.167/day**, or **USD 5.01
for 30 days**, before contract discounts and taxes. ACR build execution and data transfer can
add usage charges. The existing APIM service is not created or resized by this pattern.

Confirm current regional and agreement pricing with the
[Azure pricing calculator](https://azure.microsoft.com/pricing/calculator/) before deployment.
The exact retained architecture cap is one Basic registry plus a scale-to-zero, one-replica
Container App.

## Cleanup

```powershell
.\09-aws-interop\infra\cleanup.ps1 `
  -SubscriptionId "<subscription-id>" `
  -ApimResourceGroup "<apim-resource-group>" `
  -ApimName "<apim-name>"
```

Cleanup rechecks every ownership marker, deletes only the three Pattern 9 APIs, product,
subscription, backend-auth named value, and dedicated resource group, and leaves the shared
APIM service and all other APIs untouched.
