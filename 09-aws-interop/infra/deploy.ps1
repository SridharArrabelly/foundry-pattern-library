[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $SubscriptionId,

    [Parameter(Mandatory)]
    [string] $ApimResourceGroup,

    [Parameter(Mandatory)]
    [string] $ApimName,

    [string] $ResourceGroupName = "rg-pattern9-protocol-gateway",
    [string] $Location = "swedencentral",
    [ValidatePattern("^[a-z0-9]{3,12}$")]
    [string] $NamePrefix = "p9gateway",
    [string] $OutputEnvPath = (Join-Path $env:TEMP "pattern9-live.env")
)

$ErrorActionPreference = "Stop"
$OwnershipMarker = "09-cross-cloud-protocol-gateway"
$OwnershipDescription = "[owner:$OwnershipMarker]"
$ApiVersion = "2025-09-01-preview"
$RestApiId = "pattern9-simulated-aws-rest"
$McpApiId = "pattern9-simulated-aws-mcp"
$A2aApiId = "pattern9-simulated-aws-a2a"
$ProductId = "pattern9-protocol-gateway"
$SubscriptionResourceId = "pattern9-protocol-gateway-review"
$BackendNamedValueId = "pattern9-backend-gateway-key"

$PatternRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RepoRoot = (Resolve-Path (Join-Path $PatternRoot "..")).Path
$MainTemplate = Join-Path $PSScriptRoot "main.bicep"
$AppTemplate = Join-Path $PSScriptRoot "app.bicep"
$OpenApiPath = Join-Path $PSScriptRoot "openapi.json"

function Invoke-Az {
    param([string[]] $Arguments, [switch] $AllowNotFound)
    $output = & az @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        $text = ($output | Out-String).Trim()
        if ($AllowNotFound -and $text -match "(?i)notfound|could not be found|ResourceNotFound") {
            return $null
        }
        throw "az $($Arguments -join ' ') failed: $text"
    }
    return ($output | Out-String).Trim()
}

function Invoke-ManagementJson {
    param(
        [ValidateSet("get", "put", "post", "delete")]
        [string] $Method,
        [string] $Url,
        [object] $Body,
        [switch] $AllowNotFound
    )
    $headers = @{
        Authorization = "Bearer $script:ManagementToken"
        "If-Match" = "*"
    }
    $arguments = @{
        Uri = $Url
        Method = $Method
        Headers = $headers
        ErrorAction = "Stop"
    }
    if ($null -ne $Body) {
        $arguments.Body = $Body | ConvertTo-Json -Depth 30 -Compress
        $arguments.ContentType = "application/json"
    }
    try {
        return Invoke-RestMethod @arguments
    } catch {
        $statusCode = [int]$_.Exception.Response.StatusCode
        if ($AllowNotFound -and $statusCode -eq 404) {
            return $null
        }
        $responseBody = $_.ErrorDetails.Message
        throw "ARM $Method $Url failed with HTTP $statusCode`: $responseBody"
    }
}

function Assert-OutsideRepository {
    param([string] $Path)
    $full = [IO.Path]::GetFullPath($Path)
    if ($full.StartsWith($RepoRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "OutputEnvPath must be outside the repository because it contains a subscription key."
    }
}

function Assert-OwnedApi {
    param([string] $ApiId, [string] $ApimBase)
    $url = "$ApimBase/apis/$ApiId`?api-version=$ApiVersion"
    $api = Invoke-ManagementJson -Method get -Url $url -AllowNotFound
    if ($api -and -not ([string]$api.properties.description).StartsWith($OwnershipDescription)) {
        throw "Refusing to overwrite APIM API '$ApiId'; its ownership marker does not match."
    }
}

function Assert-OwnedProduct {
    param([string] $ApimBase)
    $url = "$ApimBase/products/$ProductId`?api-version=$ApiVersion"
    $product = Invoke-ManagementJson -Method get -Url $url -AllowNotFound
    if ($product -and -not ([string]$product.properties.description).StartsWith($OwnershipDescription)) {
        throw "Refusing to overwrite APIM product '$ProductId'; its ownership marker does not match."
    }
}

function Assert-OwnedNamedValue {
    param([string] $ApimBase)
    $url = "$ApimBase/namedValues/$BackendNamedValueId`?api-version=$ApiVersion"
    $namedValue = Invoke-ManagementJson -Method get -Url $url -AllowNotFound
    if (
        $namedValue -and
        $OwnershipMarker -notin @($namedValue.properties.tags)
    ) {
        throw "Refusing to overwrite APIM named value '$BackendNamedValueId'; its ownership marker does not match."
    }
}

function Assert-SafeMcpLogging {
    param([string] $ApimBase)
    $diagnosticsUrl = "$ApimBase/diagnostics?api-version=$ApiVersion"
    $diagnostics = Invoke-ManagementJson -Method get -Url $diagnosticsUrl
    foreach ($diagnostic in @($diagnostics.value)) {
        $bytes = $diagnostic.properties.frontend.response.body.bytes
        if ($null -ne $bytes -and [int]$bytes -gt 0) {
            throw (
                "APIM global diagnostic '$($diagnostic.name)' logs $bytes frontend response " +
                "payload bytes. Set it to 0 before deploying an MCP streaming API."
            )
        }
    }
    $policyUrl = "$ApimBase/policies/policy?api-version=$ApiVersion"
    $policy = Invoke-ManagementJson -Method get -Url $policyUrl -AllowNotFound
    if ($policy -and ([string]$policy.properties.value) -match "context\.Response\.Body") {
        throw "The APIM global policy reads context.Response.Body, which is unsafe for MCP streaming."
    }
}

Assert-OutsideRepository -Path $OutputEnvPath

$activeSubscription = Invoke-Az -Arguments @("account", "show", "--query", "id", "-o", "tsv")
if ($activeSubscription -ne $SubscriptionId) {
    throw "The active Azure subscription does not match the explicit SubscriptionId parameter."
}
$script:ManagementToken = Invoke-Az -Arguments @(
    "account", "get-access-token",
    "--resource", "https://management.azure.com/",
    "--query", "accessToken",
    "-o", "tsv"
)

$apim = Invoke-Az -Arguments @(
    "apim", "show",
    "--resource-group", $ApimResourceGroup,
    "--name", $ApimName,
    "--query", "{id:id,gatewayUrl:gatewayUrl,sku:sku.name,provisioningState:provisioningState}",
    "-o", "json"
) | ConvertFrom-Json
if ($apim.provisioningState -ne "Succeeded") {
    throw "APIM '$ApimName' is not in Succeeded state."
}

$ApimBase = "https://management.azure.com$($apim.id)"
Assert-SafeMcpLogging -ApimBase $ApimBase
foreach ($apiId in @($RestApiId, $McpApiId, $A2aApiId)) {
    Assert-OwnedApi -ApiId $apiId -ApimBase $ApimBase
}
Assert-OwnedProduct -ApimBase $ApimBase
Assert-OwnedNamedValue -ApimBase $ApimBase

$existingGroup = Invoke-Az -Arguments @(
    "group", "show",
    "--name", $ResourceGroupName,
    "-o", "json"
) -AllowNotFound
if ($existingGroup) {
    $group = $existingGroup | ConvertFrom-Json
    if ($group.tags.'pattern-id' -ne $OwnershipMarker) {
        throw "Refusing to use resource group '$ResourceGroupName'; it is not pattern-owned."
    }
} else {
    Invoke-Az -Arguments @(
        "group", "create",
        "--name", $ResourceGroupName,
        "--location", $Location,
        "--tags", "pattern-id=$OwnershipMarker", "pattern-purpose=simulated-cross-cloud-protocol-gateway",
        "-o", "none"
    ) | Out-Null
}

$foundationRaw = Invoke-Az -Arguments @(
    "deployment", "group", "create", "--only-show-errors",
    "--resource-group", $ResourceGroupName,
    "--name", "pattern9-foundation",
    "--template-file", $MainTemplate,
    "--parameters", "location=$Location", "namePrefix=$NamePrefix", "ownershipMarker=$OwnershipMarker",
    "--query", "properties.outputs",
    "-o", "json"
)
$foundation = $foundationRaw | ConvertFrom-Json
$registryName = $foundation.registryName.value
$registryLoginServer = $foundation.registryLoginServer.value
$environmentName = $foundation.environmentName.value

$armAuthentication = Invoke-Az -Arguments @(
    "acr", "config", "authentication-as-arm", "show",
    "--registry", $registryName,
    "--query", "status",
    "-o", "tsv"
)
if ($armAuthentication -ne "enabled") {
    Invoke-Az -Arguments @(
        "acr", "config", "authentication-as-arm", "update",
        "--registry", $registryName,
        "--status", "enabled",
        "-o", "none"
    ) | Out-Null
}

$contentFiles = @(
    (Join-Path $PatternRoot "Dockerfile"),
    (Join-Path $PatternRoot "requirements.txt"),
    (Join-Path $PatternRoot "simulator.py")
)
$hashInput = ($contentFiles | ForEach-Object { (Get-FileHash $_ -Algorithm SHA256).Hash }) -join ""
$imageTag = $hashInput.Substring(0, 12).ToLowerInvariant()
$image = "$registryLoginServer/pattern9-simulator:$imageTag"

Invoke-Az -Arguments @(
    "acr", "build",
    "--registry", $registryName,
    "--image", "pattern9-simulator:$imageTag",
    "--file", (Join-Path $PatternRoot "Dockerfile"),
    $PatternRoot,
    "--no-logs",
    "-o", "none"
) | Out-Null

$randomBytes = New-Object byte[] 32
$randomGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $randomGenerator.GetBytes($randomBytes)
} finally {
    $randomGenerator.Dispose()
}
$backendGatewayKey = [Convert]::ToBase64String($randomBytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
$secretParameterPath = Join-Path (
    [IO.Path]::GetTempPath()
) "pattern9-app-parameters-$([Guid]::NewGuid().ToString('N')).json"
@{
    '$schema' = "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#"
    contentVersion = "1.0.0.0"
    parameters = @{
        backendGatewayKey = @{
            value = $backendGatewayKey
        }
    }
} | ConvertTo-Json -Depth 8 | Set-Content -Path $secretParameterPath -Encoding utf8
try {
    $appRaw = Invoke-Az -Arguments @(
        "deployment", "group", "create", "--only-show-errors",
        "--resource-group", $ResourceGroupName,
        "--name", "pattern9-app",
        "--template-file", $AppTemplate,
        "--parameters",
            "location=$Location",
            "namePrefix=$NamePrefix",
            "ownershipMarker=$OwnershipMarker",
            "registryName=$registryName",
            "environmentName=$environmentName",
            "containerImage=$image",
            "@$secretParameterPath",
        "--query", "properties.outputs",
        "-o", "json"
    )
} finally {
    Remove-Item -LiteralPath $secretParameterPath -Force -ErrorAction SilentlyContinue
}
$appOutputs = $appRaw | ConvertFrom-Json
$backendBaseUrl = $appOutputs.backendBaseUrl.value

$namedValuePayload = @{
    properties = @{
        displayName = $BackendNamedValueId
        secret = $true
        tags = @($OwnershipMarker)
        value = $backendGatewayKey
    }
}
Invoke-ManagementJson `
    -Method put `
    -Url "$ApimBase/namedValues/$BackendNamedValueId`?api-version=$ApiVersion" `
    -Body $namedValuePayload | Out-Null

$openApi = (Get-Content -Raw $OpenApiPath).Replace(
    "https://pattern9-backend.example.invalid",
    $backendBaseUrl
)
$restApiPayload = @{
    properties = @{
        displayName = "Pattern 9 simulated AWS/Bedrock REST capability"
        description = "$OwnershipDescription Industry-neutral deterministic REST backend."
        path = "pattern9/aws-simulator"
        protocols = @("https")
        subscriptionRequired = $true
        format = "openapi+json"
        value = $openApi
        serviceUrl = $backendBaseUrl
    }
}
Invoke-ManagementJson `
    -Method put `
    -Url "$ApimBase/apis/$RestApiId`?api-version=$ApiVersion" `
    -Body $restApiPayload | Out-Null

$restApiResourceId = "$($apim.id)/apis/$RestApiId"
$mcpApiPayload = @{
    properties = @{
        displayName = "Pattern 9 cross-cloud MCP tools"
        description = "$OwnershipDescription APIM REST-backed MCP API; no response body access."
        path = "pattern9/aws-mcp"
        protocols = @("https")
        subscriptionRequired = $true
        type = "mcp"
        mcpProperties = @{
            endpoints = @{
                mcp = @{
                    uriTemplate = "/mcp"
                }
            }
        }
        mcpTools = @(
            @{
                name = "list_capabilities"
                description = "List deterministic industry-neutral quote capabilities."
                operationId = "$restApiResourceId/operations/listCapabilities"
            },
            @{
                name = "create_quote"
                description = "Create a deterministic simulated AWS/Bedrock capability quote."
                operationId = "$restApiResourceId/operations/createQuote"
            }
        )
    }
}
Invoke-ManagementJson `
    -Method put `
    -Url "$ApimBase/apis/$McpApiId`?api-version=$ApiVersion" `
    -Body $mcpApiPayload | Out-Null

$a2aApiPayload = @{
    properties = @{
        displayName = "Pattern 9 simulated AWS/Bedrock A2A agent"
        description = "$OwnershipDescription Genuine JSON-RPC A2A adapter over a simulated backend."
        path = "pattern9/aws-a2a"
        protocols = @("https")
        subscriptionRequired = $true
        subscriptionKeyParameterNames = @{
            header = "Ocp-Apim-Subscription-Key"
            query = "subscription-key"
        }
        type = "a2a"
        isAgent = $true
        agent = @{
            id = "simulated-aws-capability-agent"
        }
        a2aProperties = @{
            agentCardPath = "/.well-known/agent-card.json"
            agentCardBackendUrl = "$backendBaseUrl/.well-known/agent-card.json"
        }
        jsonRpcProperties = @{
            backendUrl = $backendBaseUrl
            path = "/a2a"
        }
    }
}
Invoke-ManagementJson `
    -Method put `
    -Url "$ApimBase/apis/$A2aApiId`?api-version=$ApiVersion" `
    -Body $a2aApiPayload | Out-Null

$policyXml = @'
<policies>
  <inbound>
    <base />
    <rate-limit-by-key calls="60" renewal-period="60" counter-key="@(context.Subscription?.Id ?? context.Request.IpAddress)" />
    <set-header name="X-Pattern9-Gateway" exists-action="override">
      <value>apim</value>
    </set-header>
    <set-header name="X-Pattern9-Backend-Key" exists-action="override">
      <value>{{pattern9-backend-gateway-key}}</value>
    </set-header>
  </inbound>
  <backend>
    <base />
  </backend>
  <outbound>
    <base />
  </outbound>
  <on-error>
    <base />
  </on-error>
</policies>
'@
foreach ($apiId in @($RestApiId, $McpApiId, $A2aApiId)) {
    Invoke-ManagementJson `
        -Method put `
        -Url "$ApimBase/apis/$apiId/policies/policy?api-version=$ApiVersion" `
        -Body @{ properties = @{ format = "rawxml"; value = $policyXml } } | Out-Null
}

$productPayload = @{
    properties = @{
        displayName = "Pattern 9 protocol gateway"
        description = "$OwnershipDescription Scoped access to Pattern 9 REST, MCP, and A2A APIs."
        state = "published"
        subscriptionRequired = $true
        approvalRequired = $false
    }
}
Invoke-ManagementJson `
    -Method put `
    -Url "$ApimBase/products/$ProductId`?api-version=$ApiVersion" `
    -Body $productPayload | Out-Null

foreach ($apiId in @($RestApiId, $McpApiId, $A2aApiId)) {
    Invoke-ManagementJson `
        -Method put `
        -Url "$ApimBase/products/$ProductId/apis/$apiId`?api-version=$ApiVersion" `
        -Body @{} | Out-Null
}

$subscriptionUrl = "$ApimBase/subscriptions/$SubscriptionResourceId`?api-version=$ApiVersion"
$existingSubscription = Invoke-ManagementJson -Method get -Url $subscriptionUrl -AllowNotFound
if (
    $existingSubscription -and
    $existingSubscription.properties.displayName -ne "Pattern 9 protocol gateway review"
) {
    throw "Refusing to overwrite APIM subscription '$SubscriptionResourceId'."
}
$subscriptionPayload = @{
    properties = @{
        displayName = "Pattern 9 protocol gateway review"
        scope = "$($apim.id)/products/$ProductId"
        state = "active"
        allowTracing = $false
    }
}
Invoke-ManagementJson -Method put -Url $subscriptionUrl -Body $subscriptionPayload | Out-Null
$secrets = Invoke-ManagementJson `
    -Method post `
    -Url "$ApimBase/subscriptions/$SubscriptionResourceId/listSecrets?api-version=$ApiVersion"
if (-not $secrets.primaryKey) {
    throw "APIM did not return a primary subscription key."
}

$gatewayUrl = ([string]$apim.gatewayUrl).TrimEnd("/")
$outputDirectory = Split-Path -Parent ([IO.Path]::GetFullPath($OutputEnvPath))
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
@(
    "PATTERN9_BACKEND_URL=$backendBaseUrl"
    "PATTERN9_REST_URL=$gatewayUrl/pattern9/aws-simulator"
    "PATTERN9_MCP_URL=$gatewayUrl/pattern9/aws-mcp/mcp"
    "PATTERN9_A2A_URL=$gatewayUrl/pattern9/aws-a2a"
    "PATTERN9_APIM_SUBSCRIPTION_KEY=$($secrets.primaryKey)"
) | Set-Content -Path $OutputEnvPath -Encoding utf8

Write-Output "Pattern 9 deployment completed."
Write-Output "Backend: $backendBaseUrl"
Write-Output "APIM REST: $gatewayUrl/pattern9/aws-simulator"
Write-Output "APIM MCP: $gatewayUrl/pattern9/aws-mcp/mcp"
Write-Output "APIM A2A: $gatewayUrl/pattern9/aws-a2a"
Write-Output "Secrets were written only to: $([IO.Path]::GetFullPath($OutputEnvPath))"
