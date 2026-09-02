[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet("create", "delete")]
    [string] $Action,

    [Parameter(Mandatory)]
    [string] $ProjectResourceId,

    [string] $ConnectionName = "pattern9-apim-mcp",
    [string] $McpUrl = $env:PATTERN9_MCP_URL
)

$ErrorActionPreference = "Stop"
$OwnershipMarker = "09-cross-cloud-protocol-gateway"
$ApiVersion = "2025-06-01"

if (
    $ProjectResourceId -notmatch
    "^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.CognitiveServices/accounts/[^/]+/projects/[^/]+$"
) {
    throw "ProjectResourceId is not a complete Microsoft Foundry project ARM ID."
}

$token = az account get-access-token `
    --resource "https://management.azure.com/" `
    --query accessToken `
    -o tsv
if ($LASTEXITCODE -ne 0 -or -not $token) {
    throw "Unable to acquire an Azure management token from the current az login."
}

$url = "https://management.azure.com$ProjectResourceId/connections/$ConnectionName`?api-version=$ApiVersion"
$headers = @{
    Authorization = "Bearer $token"
    "If-Match" = "*"
}

function Get-Connection {
    try {
        return Invoke-RestMethod -Method get -Uri $url -Headers $headers -ErrorAction Stop
    } catch {
        if ([int]$_.Exception.Response.StatusCode -eq 404) {
            return $null
        }
        throw
    }
}

function Assert-Owned {
    param([object] $Connection)
    if (
        $Connection -and
        $Connection.properties.metadata.'pattern-id' -ne $OwnershipMarker
    ) {
        throw "Refusing to change connection '$ConnectionName'; it is not Pattern 9 owned."
    }
}

$existing = Get-Connection
Assert-Owned -Connection $existing

if ($Action -eq "delete") {
    if ($existing) {
        Invoke-RestMethod `
            -Method delete `
            -Uri $url `
            -Headers $headers `
            -ErrorAction Stop | Out-Null
    }
    Write-Output "Pattern-owned Foundry connection '$ConnectionName' was removed."
    exit 0
}

if (-not $McpUrl) {
    throw "Set PATTERN9_MCP_URL in the current process."
}
$parsedUrl = [Uri]$McpUrl
if ($parsedUrl.Scheme -ne "https" -or $parsedUrl.IsLoopback) {
    throw "PATTERN9_MCP_URL must be a remote HTTPS endpoint."
}
$subscriptionKey = $env:PATTERN9_APIM_SUBSCRIPTION_KEY
if (-not $subscriptionKey) {
    throw "Set PATTERN9_APIM_SUBSCRIPTION_KEY in the current process."
}

$body = @{
    properties = @{
        category = "CustomKeys"
        target = $McpUrl.TrimEnd("/")
        authType = "CustomKeys"
        credentials = @{
            keys = @{
                "Ocp-Apim-Subscription-Key" = $subscriptionKey
            }
        }
        metadata = @{
            "pattern-id" = $OwnershipMarker
            "ApiType" = "MCP"
        }
    }
} | ConvertTo-Json -Depth 12 -Compress

$created = Invoke-RestMethod `
    -Method put `
    -Uri $url `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $body `
    -ErrorAction Stop

if ($created.properties.metadata.'pattern-id' -ne $OwnershipMarker) {
    throw "Foundry returned a connection without the Pattern 9 ownership marker."
}
Write-Output "Pattern-owned Foundry connection '$ConnectionName' is ready."
