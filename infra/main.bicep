targetScope = 'resourceGroup'

@description('Environment name (dev, test, prod)')
param environment string = 'dev'

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Short workload token used in resource names')
param workload string = 'ddmcp'

@description('Deploy the fallback MCP server (Container Apps environment + registry + app)?')
param deployFallbackServer bool = false

@description('Principal ID of the user/service principal running this deployment (for Key Vault + Storage RBAC). Leave empty to skip.')
param deployerPrincipalId string = ''

@description('Principal type of deployerPrincipalId')
@allowed([
  'User'
  'ServicePrincipal'
])
param deployerPrincipalType string = 'User'

@description('AI Search SKU -- Basic is sufficient for POC scale; use Standard (S1) or higher for production')
@allowed([
  'basic'
  'standard'
  'standard2'
  'standard3'
])
param searchSku string = 'basic'

@description('Embedding model to deploy')
param embeddingModelName string = 'text-embedding-3-large'

@description('Chat model to deploy for Knowledge Base query planning')
param chatModelName string = 'gpt-5-mini'

var suffix = '${workload}-${environment}-${location}'
var tags = {
  workload: workload
  environment: environment
  pattern: 'dev-docs-mcp-knowledge-agent'
}

// Names kept short/deduped where a resource type enforces stricter limits
var storageAccountNameRaw = toLower(replace('st${workload}${environment}${location}', '-', ''))
var containerRegistryNameRaw = toLower(replace('acr${workload}${environment}${location}', '-', ''))
var foundryAccountName = 'aif-${suffix}'
var searchServiceName = 'srch-${suffix}'
var keyVaultName = take('kv-${suffix}', 24)
var containerAppsEnvName = 'cae-${suffix}'
var containerAppName = take('ca-mcp-${suffix}', 32)

module storage 'modules/storage.bicep' = {
  name: 'storage-deployment'
  params: {
    storageAccountName: take(storageAccountNameRaw, 24)
    location: location
    tags: tags
  }
}

module foundry 'modules/foundry.bicep' = {
  name: 'foundry-deployment'
  params: {
    foundryAccountName: foundryAccountName
    location: location
    tags: tags
    embeddingModelName: embeddingModelName
    chatModelName: chatModelName
  }
}

module search 'modules/search.bicep' = {
  name: 'search-deployment'
  params: {
    searchServiceName: searchServiceName
    location: location
    tags: tags
    skuName: searchSku
  }
}

module keyVault 'modules/keyvault.bicep' = {
  name: 'keyvault-deployment'
  params: {
    keyVaultName: keyVaultName
    location: location
    tags: tags
  }
}

module rbac 'modules/rbac.bicep' = {
  name: 'rbac-deployment'
  params: {
    storageAccountId: storage.outputs.storageAccountId
    foundryAccountId: foundry.outputs.foundryAccountId
    keyVaultId: keyVault.outputs.keyVaultId
    searchPrincipalId: search.outputs.principalId
    deployerPrincipalId: deployerPrincipalId
    deployerPrincipalType: deployerPrincipalType
  }
}

module containerApp 'modules/containerapp.bicep' = if (deployFallbackServer) {
  name: 'containerapp-deployment'
  params: {
    environmentName: containerAppsEnvName
    registryName: take(containerRegistryNameRaw, 50)
    containerAppName: containerAppName
    location: location
    tags: tags
    keyVaultId: keyVault.outputs.keyVaultId
    keyVaultUri: keyVault.outputs.keyVaultUri
    searchEndpoint: search.outputs.searchEndpoint
  }
}

output deploymentSummary object = {
  resourceGroup: resourceGroup().name
  region: location
  environment: environment
  storageAccount: storage.outputs.storageAccountName
  blobEndpoint: storage.outputs.blobEndpoint
  rawContainer: 'raw'
  foundryResource: foundry.outputs.foundryAccountName
  foundryOpenAIEndpoint: foundry.outputs.openAIEndpoint
  documentIntelligenceEndpoint: foundry.outputs.documentIntelligenceEndpoint
  embeddingDeployment: foundry.outputs.embeddingDeploymentName
  chatDeployment: foundry.outputs.chatDeploymentName
  searchService: search.outputs.searchServiceName
  searchEndpoint: search.outputs.searchEndpoint
  searchPrincipalId: search.outputs.principalId
  keyVault: keyVault.outputs.keyVaultName
  keyVaultUri: keyVault.outputs.keyVaultUri
  fallbackServerDeployed: deployFallbackServer
  // The `!` null-forgiving operator suppresses BCP318 here -- the ternary
  // guard already ensures containerApp is only dereferenced when the
  // conditional module was actually deployed.
  containerAppFqdn: deployFallbackServer ? containerApp!.outputs.containerAppFqdn : ''
  containerRegistry: deployFallbackServer ? containerApp!.outputs.registryLoginServer : ''
}
