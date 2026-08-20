@description('Container Apps environment name')
param environmentName string

@description('Container Registry name (no hyphens, globally unique)')
param registryName string

@description('Container App name for the fallback MCP server')
param containerAppName string

@description('Azure region')
param location string

@description('Tags to apply to all resources')
param tags object = {}

@description('Key Vault resource ID the Container App needs read access to')
param keyVaultId string

@description('Key Vault URI for secret references')
param keyVaultUri string

@description('AI Search endpoint the fallback server calls')
param searchEndpoint string

@description('Name of the Key Vault secret holding the Search admin/query key')
param searchKeySecretName string = 'search-admin-key'

@description('Container image to deploy on first create. deploy_mcp_server.ps1 overwrites this via az containerapp update --image once a real image has been built with az acr build -- this placeholder just avoids a chicken-and-egg problem on first deploy (no image exists in the registry yet).')
param containerImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${environmentName}-logs'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: registryName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
}

resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    environmentId: containerAppsEnvironment.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8080
        transport: 'http'
        allowInsecure: false
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: 'system'
        }
      ]
      secrets: [
        {
          name: 'search-key'
          keyVaultUrl: '${keyVaultUri}secrets/${searchKeySecretName}'
          identity: 'system'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'mcp-fallback-server'
          image: containerImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'SEARCH_ENDPOINT'
              value: searchEndpoint
            }
            {
              name: 'SEARCH_API_KEY'
              secretRef: 'search-key'
            }
            {
              name: 'MCP_TRANSPORT'
              value: 'streamable-http'
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 2
      }
    }
  }
}

resource keyVaultRef 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: last(split(keyVaultId, '/'))
}

var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource containerAppToKeyVault 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVaultId, containerApp.id, keyVaultSecretsUserRoleId)
  scope: keyVaultRef
  properties: {
    principalId: containerApp.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalType: 'ServicePrincipal'
  }
}

resource containerAppToRegistry 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, containerApp.id, acrPullRoleId)
  scope: registry
  properties: {
    principalId: containerApp.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalType: 'ServicePrincipal'
  }
}

output containerAppFqdn string = containerApp.properties.configuration.ingress.fqdn
output containerAppName string = containerApp.name
output registryLoginServer string = registry.properties.loginServer
output containerAppsEnvironmentName string = containerAppsEnvironment.name
