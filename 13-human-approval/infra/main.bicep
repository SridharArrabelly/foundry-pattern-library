@description('Name for the Azure Container App and its managed environment.')
param name string = 'foundry-change-control'

@description('Azure region for the demo resources.')
param location string = resourceGroup().location

@description('Fully qualified image already pushed to an Azure Container Registry.')
param containerImage string

@description('Existing Azure Container Registry name in this resource group. Managed-identity ARM-audience authentication must be enabled before deployment.')
param containerRegistryName string

@allowed([
  'rbac'
  'rbac-abac'
])
@description('Registry authorization mode: rbac uses AcrPull; rbac-abac uses Container Registry Repository Reader.')
param containerRegistryAuthorizationMode string = 'rbac'

@secure()
@description('Tool-only key stored in the Container App and Foundry project connection.')
param mcpToolApiKey string

@secure()
@description('Separate operator key for pending registrations, decisions, and audit reads.')
param mcpOperatorApiKey string

@description('Minimum replicas. Keep at zero for scale-to-zero demo economics.')
param minReplicas int = 0

@description('Maximum replicas. SQLite requires one writer for this demo.')
param maxReplicas int = 1

var compact = toLower(replace(name, '-', ''))
var storageName = take('${compact}${uniqueString(resourceGroup().id)}', 24)
var shareName = 'changecontrol'
var acrPullRoleDefinitionId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var acrRepositoryReaderRoleDefinitionId = 'b93aa761-3e63-49ed-ac28-beffa264f7ac'
var registryPullRoleDefinitionId = containerRegistryAuthorizationMode == 'rbac-abac'
  ? acrRepositoryReaderRoleDefinitionId
  : acrPullRoleDefinitionId

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: containerRegistryName
}

resource pullIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${name}-pull'
  location: location
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, pullIdentity.id, registryPullRoleDefinitionId)
  scope: registry
  properties: {
    principalId: pullIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      registryPullRoleDefinitionId
    )
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource share 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileService
  name: shareName
  properties: {
    enabledProtocols: 'SMB'
    accessTier: 'TransactionOptimized'
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${name}-env'
  location: location
}

resource environmentStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: environment
  name: 'sqlite'
  properties: {
    azureFile: {
      accountName: storage.name
      accountKey: storage.listKeys().keys[0].value
      shareName: share.name
      accessMode: 'ReadWrite'
    }
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${pullIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        {
          server: registry.properties.loginServer
          identity: pullIdentity.id
        }
      ]
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
      }
      secrets: [
        {
          name: 'mcp-tool-api-key'
          value: mcpToolApiKey
        }
        {
          name: 'mcp-operator-api-key'
          value: mcpOperatorApiKey
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'mcp'
          image: containerImage
          env: [
            {
              name: 'MCP_TOOL_API_KEY'
              secretRef: 'mcp-tool-api-key'
            }
            {
              name: 'MCP_OPERATOR_API_KEY'
              secretRef: 'mcp-operator-api-key'
            }
            {
              name: 'APPROVAL_DB_PATH'
              value: '/data/change-control.sqlite3'
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          volumeMounts: [
            {
              volumeName: 'sqlite'
              mountPath: '/data'
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8080
                scheme: 'HTTP'
              }
              initialDelaySeconds: 5
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
      }
      volumes: [
        {
          name: 'sqlite'
          storageType: 'AzureFile'
          storageName: environmentStorage.name
        }
      ]
    }
  }
  dependsOn: [
    acrPull
  ]
}

output mcpUrl string = 'https://${app.properties.configuration.ingress.fqdn}/mcp'
output containerAppName string = app.name
output storageAccountName string = storage.name
output requiredRegistryRole string = containerRegistryAuthorizationMode == 'rbac-abac'
  ? 'Container Registry Repository Reader'
  : 'AcrPull'
