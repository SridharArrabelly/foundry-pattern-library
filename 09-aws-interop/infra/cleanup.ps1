[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $SubscriptionId,

    [Parameter(Mandatory)]
    [string] $ApimResourceGroup,

    [Parameter(Mandatory)]
    [string] $ApimName,

    [string] $ResourceGroupName = "rg-pattern9-protocol-gateway",
    [switch] $NoWait
)

$ErrorActionPreference = "Stop"
$OwnershipMarker = "09-cross-cloud-protocol-gateway"
$OwnershipDescription = "[owner:$OwnershipMarker]"
$ApiVersion = "2025-09-01-preview"
$ApiIds = @(
    "pattern9-simulated-aws-mcp",
    "pattern9-simulated-aws-a2a",
    "pattern9-simulated-aws-rest"
)
$ProductId = "pattern9-protocol-gateway"
$SubscriptionResourceId = "pattern9-protocol-gateway-review"
$BackendNamedValueId = "pattern9-backend-gateway-key"

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

function Get-Resource {
    param([string] $Url)
    $raw = Invoke-Az -Arguments @(
        "rest", "--only-show-errors", "--method", "get", "--url", $Url
    ) -AllowNotFound
    if ($raw) {
        return $raw | ConvertFrom-Json -Depth 30
    }
    return $null
}

function Remove-OwnedApi {
    param([string] $ApiId, [string] $ApimBase)
    $url = "$ApimBase/apis/$ApiId`?api-version=$ApiVersion"
    $api = Get-Resource -Url $url
    if (-not $api) {
        return
    }
    if (-not ([string]$api.properties.description).StartsWith($OwnershipDescription)) {
        throw "Refusing to delete APIM API '$ApiId'; its ownership marker does not match."
    }
    Invoke-Az -Arguments @(
        "rest", "--only-show-errors", "--method", "delete", "--url", $url,
        "--headers", "If-Match=*"
    ) | Out-Null
}

$activeSubscription = Invoke-Az -Arguments @("account", "show", "--query", "id", "-o", "tsv")
if ($activeSubscription -ne $SubscriptionId) {
    throw "The active Azure subscription does not match the explicit SubscriptionId parameter."
}

$apimId = Invoke-Az -Arguments @(
    "apim", "show",
    "--resource-group", $ApimResourceGroup,
    "--name", $ApimName,
    "--query", "id",
    "-o", "tsv"
)
$ApimBase = "https://management.azure.com$apimId"

$subscriptionUrl = "$ApimBase/subscriptions/$SubscriptionResourceId`?api-version=$ApiVersion"
$subscription = Get-Resource -Url $subscriptionUrl
if ($subscription) {
    if ($subscription.properties.displayName -ne "Pattern 9 protocol gateway review") {
        throw "Refusing to delete APIM subscription '$SubscriptionResourceId'."
    }
    Invoke-Az -Arguments @(
        "rest", "--only-show-errors", "--method", "delete", "--url", $subscriptionUrl,
        "--headers", "If-Match=*"
    ) | Out-Null
}

foreach ($apiId in $ApiIds) {
    Remove-OwnedApi -ApiId $apiId -ApimBase $ApimBase
}

$productUrl = "$ApimBase/products/$ProductId`?api-version=$ApiVersion"
$product = Get-Resource -Url $productUrl
if ($product) {
    if (-not ([string]$product.properties.description).StartsWith($OwnershipDescription)) {
        throw "Refusing to delete APIM product '$ProductId'; its ownership marker does not match."
    }
    Invoke-Az -Arguments @(
        "rest", "--only-show-errors", "--method", "delete", "--url", $productUrl,
        "--headers", "If-Match=*", "--body", "{}"
    ) | Out-Null
}

$namedValueUrl = "$ApimBase/namedValues/$BackendNamedValueId`?api-version=$ApiVersion"
$namedValue = Get-Resource -Url $namedValueUrl
if ($namedValue) {
    if ($OwnershipMarker -notin @($namedValue.properties.tags)) {
        throw "Refusing to delete APIM named value '$BackendNamedValueId'; it is not pattern-owned."
    }
    Invoke-Az -Arguments @(
        "rest", "--only-show-errors", "--method", "delete", "--url", $namedValueUrl,
        "--headers", "If-Match=*"
    ) | Out-Null
}

$groupRaw = Invoke-Az -Arguments @(
    "group", "show", "--name", $ResourceGroupName, "-o", "json"
) -AllowNotFound
if ($groupRaw) {
    $group = $groupRaw | ConvertFrom-Json
    if ($group.tags.'pattern-id' -ne $OwnershipMarker) {
        throw "Refusing to delete resource group '$ResourceGroupName'; it is not pattern-owned."
    }
    $arguments = @("group", "delete", "--name", $ResourceGroupName, "--yes")
    if ($NoWait) {
        $arguments += "--no-wait"
    }
    Invoke-Az -Arguments $arguments | Out-Null
}

Write-Output "Pattern-owned APIM artifacts and Azure resources were removed."
