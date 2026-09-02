targetScope = 'resourceGroup'

@description('Azure region for the pattern-owned resources.')
param location string = resourceGroup().location

@minLength(3)
@maxLength(12)
@description('Lowercase alphanumeric prefix used for globally unique resource names.')
param namePrefix string = 'p9gateway'

@description('Ownership marker checked by deployment and cleanup scripts.')
param ownershipMarker string = '09-cross-cloud-protocol-gateway'

var suffix = take(uniqueString(subscription().subscriptionId, resourceGroup().id), 8)
var commonTags = {
  'pattern-id': ownershipMarker
  'pattern-purpose': 'simulated-cross-cloud-protocol-gateway'
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: take('${namePrefix}${suffix}', 50)
  location: location
  tags: commonTags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${namePrefix}-env-${suffix}'
  location: location
  tags: commonTags
  properties: {
    zoneRedundant: false
  }
}

output registryName string = registry.name
output registryLoginServer string = registry.properties.loginServer
output environmentName string = environment.name
