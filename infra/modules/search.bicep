@description('Azure AI Search service name')
param searchServiceName string

@description('Azure region')
param location string

@description('Tags to apply to all resources')
param tags object = {}

@description('Search service SKU -- Basic (or higher) is sufficient for semantic ranker and Knowledge Bases at POC scale; Standard (S1) or higher is recommended for production/customer-facing workloads (replica/partition scale, concurrency headroom)')
@allowed([
  'basic'
  'standard'
  'standard2'
  'standard3'
])
param skuName string = 'basic'

// NOTE: Knowledge Bases (agentic retrieval) and the native MCP endpoint are
// data-plane concepts managed via REST calls against the search endpoint
// (see scripts/post_deploy_search.py), not ARM/Bicep resources -- this module
// only provisions the Search service itself with semantic ranker enabled.
resource searchService 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: searchServiceName
  location: location
  tags: tags
  sku: {
    name: skuName
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    semanticSearch: 'standard'
    publicNetworkAccess: 'enabled'
  }
}

output searchServiceId string = searchService.id
output searchServiceName string = searchService.name
output searchEndpoint string = 'https://${searchService.name}.search.windows.net'
output principalId string = searchService.identity.principalId
