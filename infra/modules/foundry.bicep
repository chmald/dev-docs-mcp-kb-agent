@description('Cognitive Services multi-service account name (kind=AIServices) serving Document Intelligence + Azure OpenAI')
param foundryAccountName string

@description('Azure region')
param location string

@description('Tags to apply to all resources')
param tags object = {}

@description('Embedding model to deploy')
param embeddingModelName string = 'text-embedding-3-large'

@description('Embedding model version')
param embeddingModelVersion string = '1'

@description('Chat model to deploy for Knowledge Base query planning')
param chatModelName string = 'gpt-5-mini'

@description('Chat model version')
param chatModelVersion string = '2025-08-07'

@description('Embedding deployment capacity (in thousands of TPM)')
param embeddingCapacity int = 30

@description('Chat deployment capacity (in thousands of TPM)')
param chatCapacity int = 10

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: foundryAccountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: foundryAccountName
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: false
  }
}

// NOTE: Cognitive Services model deployments are known to throttle concurrent
// operations on the same account -- the explicit dependsOn below serializes
// the two deployments rather than relying on Bicep's default parallelism.
resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: foundryAccount
  name: 'embedding'
  sku: {
    name: 'Standard'
    capacity: embeddingCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: embeddingModelName
      version: embeddingModelVersion
    }
  }
}

resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: foundryAccount
  name: 'chat'
  sku: {
    name: 'GlobalStandard'
    capacity: chatCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: chatModelName
      version: chatModelVersion
    }
  }
  dependsOn: [
    embeddingDeployment
  ]
}

output foundryAccountId string = foundryAccount.id
output foundryAccountName string = foundryAccount.name
output openAIEndpoint string = foundryAccount.properties.endpoint
output documentIntelligenceEndpoint string = 'https://${foundryAccountName}.cognitiveservices.azure.com/'
output principalId string = foundryAccount.identity.principalId
output embeddingDeploymentName string = embeddingDeployment.name
output chatDeploymentName string = chatDeployment.name
