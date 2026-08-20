@description('Storage account resource ID')
param storageAccountId string

@description('Foundry (Cognitive Services) account resource ID')
param foundryAccountId string

@description('Key Vault resource ID')
param keyVaultId string

@description('AI Search service system-assigned managed identity principal ID')
param searchPrincipalId string

@description('Principal ID of the user/service principal running the deployment (for Key Vault + Storage data-plane access). Leave empty to skip.')
param deployerPrincipalId string = ''

@description('Principal type of deployerPrincipalId -- User for interactive az login, ServicePrincipal for an ADO pipeline identity')
@allowed([
  'User'
  'ServicePrincipal'
])
param deployerPrincipalType string = 'User'

// Built-in Azure role definition IDs
var storageBlobDataReaderRoleId = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var keyVaultSecretsOfficerRoleId = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'

resource storageAccountRef 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: last(split(storageAccountId, '/'))
}

resource foundryAccountRef 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: last(split(foundryAccountId, '/'))
}

resource keyVaultRef 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: last(split(keyVaultId, '/'))
}

// AI Search MI -> Storage Blob Data Reader (indexer data source connection reads raw PDFs)
resource searchToStorage 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccountId, searchPrincipalId, storageBlobDataReaderRoleId)
  scope: storageAccountRef
  properties: {
    principalId: searchPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataReaderRoleId)
    principalType: 'ServicePrincipal'
  }
}

// AI Search MI -> Cognitive Services User (skillset calls to Document Intelligence + AOAI embeddings)
resource searchToFoundry 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundryAccountId, searchPrincipalId, cognitiveServicesUserRoleId)
  scope: foundryAccountRef
  properties: {
    principalId: searchPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
    principalType: 'ServicePrincipal'
  }
}

// Deployer -> Key Vault Secrets Officer (deploy.ps1 writes the Search admin key as a secret)
resource deployerToKeyVault 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  name: guid(keyVaultId, deployerPrincipalId, keyVaultSecretsOfficerRoleId)
  scope: keyVaultRef
  properties: {
    principalId: deployerPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsOfficerRoleId)
    principalType: deployerPrincipalType
  }
}

// Deployer -> Storage Blob Data Contributor (scripts/upload_documents.py needs
// data-plane write access -- ARM Contributor alone does not grant blob CRUD)
resource deployerToStorage 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  name: guid(storageAccountId, deployerPrincipalId, storageBlobDataContributorRoleId)
  scope: storageAccountRef
  properties: {
    principalId: deployerPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalType: deployerPrincipalType
  }
}
