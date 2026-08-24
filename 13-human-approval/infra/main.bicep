@description('Name for the Azure Container App and its managed environment.')
param name string = 'foundry-change-control'

@description('Azure region for the demo resources.')
param location string = resourceGroup().location

@description('Fully qualified image already pushed to an Azure Container Registry.')
param containerImage string

@secure()
@description('Shared key stored as a Container Apps secret and a Foundry project connection secret.')
param mcpApiKey string

@description('Minimum replicas. Keep at zero for scale-to-zero demo economics.')
param minReplicas int = 0

@description('Maximum replicas. SQLite requires one writer for this demo.')
param maxReplicas int = 1

var compact = toLower(replace(name, '-', ''))
var storageName = take('${compact}${uniqueString(resourceGroup().id)}', 24)
var shareName = 'changecontrol'

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
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
      }
      secrets: [
        {
          name: 'mcp-api-key'
          value: mcpApiKey
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
              name: 'MCP_API_KEY'
              secretRef: 'mcp-api-key'
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
}

output mcpUrl string = 'https://${app.properties.configuration.ingress.fqdn}/mcp'
output containerAppName string = app.name
output storageAccountName string = storage.name
