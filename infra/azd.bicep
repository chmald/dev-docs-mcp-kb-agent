// Azure Developer CLI (azd) entry point -- `azd up` / `azd provision` deploy this file.
//
// It is a thin subscription-scoped wrapper: it creates (or reuses) the resource
// group and calls the SAME main.bicep that infra/deploy.ps1 and the manual path
// (docs/03b) deploy, so every path produces identical resources and names.
// Every value below comes from the azd environment via azd.parameters.json;
// docs/12-configuration-reference.md lists them all.
targetScope = 'subscription'

@minLength(1)
@maxLength(12)
@description('azd environment name (AZURE_ENV_NAME). Used as the environment token in every resource name -- keep it short, lowercase letters and digits only.')
param environmentName string

@minLength(1)
@description('Azure region for all resources (AZURE_LOCATION)')
param location string

@description('Resource group to deploy into (AZURE_RESOURCE_GROUP). Empty = rg-<workload>-<env>-<region>.')
param resourceGroupName string = ''

@description('Short workload token used in resource names (WORKLOAD_NAME)')
param workload string = 'ddmcp'

@description('Object ID of the deploying principal, set by azd (AZURE_PRINCIPAL_ID). Granted Key Vault + Storage data roles.')
param principalId string = ''

@description('Type of the deploying principal (AZURE_PRINCIPAL_TYPE)')
@allowed([
  'User'
  'ServicePrincipal'
])
param principalType string = 'User'

@description('Also deploy the fallback MCP server (Container Apps) -- DEPLOY_FALLBACK_SERVER')
param deployFallbackServer bool = false

@description('AI Search SKU -- SEARCH_SKU')
@allowed([
  'basic'
  'standard'
  'standard2'
  'standard3'
])
param searchSku string = 'basic'

param embeddingModelName string = 'text-embedding-3-large'
param embeddingModelVersion string = '1'
param embeddingCapacity int = 30
param chatModelName string = 'gpt-5-mini'
param chatModelVersion string = '2025-08-07'
param chatCapacity int = 150
param deployHybridModels bool = true
param visionModelName string = 'gpt-4.1'
param visionModelVersion string = '2025-04-14'
param visionCapacity int = 1000
param frontierModelName string = 'gpt-5.6-sol'
param frontierModelVersion string = '2026-07-09'
param frontierCapacity int = 200

var rgName = empty(resourceGroupName) ? 'rg-${workload}-${environmentName}-${location}' : resourceGroupName

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: rgName
  location: location
  tags: {
    'azd-env-name': environmentName
    workload: workload
    pattern: 'dev-docs-mcp-knowledge-agent'
  }
}

module main 'main.bicep' = {
  name: 'main'
  scope: rg
  params: {
    environment: environmentName
    location: location
    workload: workload
    deployFallbackServer: deployFallbackServer
    deployerPrincipalId: principalId
    deployerPrincipalType: principalType
    searchSku: searchSku
    embeddingModelName: embeddingModelName
    embeddingModelVersion: embeddingModelVersion
    embeddingCapacity: embeddingCapacity
    chatModelName: chatModelName
    chatModelVersion: chatModelVersion
    chatCapacity: chatCapacity
    deployHybridModels: deployHybridModels
    visionModelName: visionModelName
    visionModelVersion: visionModelVersion
    visionCapacity: visionCapacity
    frontierModelName: frontierModelName
    frontierModelVersion: frontierModelVersion
    frontierCapacity: frontierCapacity
  }
}

// Outputs land in .azure/<env>/.env and are exported to the postprovision hook,
// which writes them into demo-ids.local.json for the Python scripts.
output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_RESOURCE_GROUP string = rg.name
output DEMO_ENVIRONMENT string = environmentName
output STORAGE_ACCOUNT string = main.outputs.deploymentSummary.storageAccount
output BLOB_ENDPOINT string = main.outputs.deploymentSummary.blobEndpoint
output RAW_CONTAINER string = main.outputs.deploymentSummary.rawContainer
output FOUNDRY_RESOURCE string = main.outputs.deploymentSummary.foundryResource
output FOUNDRY_OPENAI_ENDPOINT string = main.outputs.deploymentSummary.foundryOpenAIEndpoint
output DOCUMENT_INTELLIGENCE_ENDPOINT string = main.outputs.deploymentSummary.documentIntelligenceEndpoint
output AI_SERVICES_SUBDOMAIN_URL string = main.outputs.deploymentSummary.aiServicesSubdomainUrl
output FOUNDRY_PROJECT string = main.outputs.deploymentSummary.foundryProject
output EMBEDDING_DEPLOYMENT string = main.outputs.deploymentSummary.embeddingDeployment
output CHAT_DEPLOYMENT string = main.outputs.deploymentSummary.chatDeployment
output VISION_DEPLOYMENT string = main.outputs.deploymentSummary.visionDeployment
output FRONTIER_DEPLOYMENT string = main.outputs.deploymentSummary.frontierDeployment
output FRONTIER_MODEL string = main.outputs.deploymentSummary.frontierModel
output HYBRID_MODELS_DEPLOYED bool = main.outputs.deploymentSummary.hybridModelsDeployed
output SEARCH_SERVICE string = main.outputs.deploymentSummary.searchService
output SEARCH_ENDPOINT string = main.outputs.deploymentSummary.searchEndpoint
output SEARCH_PRINCIPAL_ID string = main.outputs.deploymentSummary.searchPrincipalId
output KEY_VAULT string = main.outputs.deploymentSummary.keyVault
output KEY_VAULT_URI string = main.outputs.deploymentSummary.keyVaultUri
output FALLBACK_SERVER_DEPLOYED bool = main.outputs.deploymentSummary.fallbackServerDeployed
output CONTAINER_APP_FQDN string = main.outputs.deploymentSummary.containerAppFqdn
output CONTAINER_REGISTRY string = main.outputs.deploymentSummary.containerRegistry
