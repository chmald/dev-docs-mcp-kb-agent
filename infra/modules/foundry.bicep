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

@description('Chat deployment capacity (in thousands of TPM). Agentic retrieval spends this on query planning AND answer generation per retrieve call -- 10K TPM returns HTTP 429 on the very first query. 150K is a comfortable POC floor.')
param chatCapacity int = 150

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
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
    // Required for the account to host Foundry projects. Without it, project
    // creation fails with "Project can only created under AIServices Kind
    // account with allowProjectManagement set to true".
    //
    // This needs accounts API 2025-06-01 or later. On 2024-10-01 the property is
    // not in the type definition, Bicep emits BCP037, and — critically — the
    // property is STRIPPED from the compiled ARM template, so suppressing the
    // warning does not help. The failure then looks like a service bug rather
    // than a silently dropped property.
    allowProjectManagement: true
  }
}

@description('Default Foundry project. Content Understanding and other Foundry-surfaced capabilities expect the account to have a project; creating it here removes the manual portal step that the first live deployment required.')
resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundryAccount
  name: '${foundryAccountName}-project'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: '${foundryAccountName}-project'
    description: 'Default project for the Developer Docs MCP Knowledge Base pattern.'
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
@description('AI Foundry subdomain URL. This -- NOT the cognitiveservices.azure.com endpoint -- is the form Azure AI Search accepts for a skillset\'s AIServicesByIdentity subdomainUrl. No trailing slash: the Search API rejects one.')
output aiServicesSubdomainUrl string = 'https://${foundryAccountName}.services.ai.azure.com'
output foundryProjectName string = foundryProject.name
output principalId string = foundryAccount.identity.principalId
output embeddingDeploymentName string = embeddingDeployment.name
output chatDeploymentName string = chatDeployment.name
