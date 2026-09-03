// =============================================================================
// modules/storage.bicep — STUB (Owner: Tank)
// Storage account + blob container for the raw KB markdown docs. The
// postprovision hook uploads assets/kb/*.md here; AI Search indexes from it.
// Managed identity granted "Storage Blob Data Contributor".
// Signature LOCKED by main.bicep.
// Prefer AVM: br/public:avm/res/storage/storage-account
// =============================================================================

@description('Deployment region.')
param location string

@description('Tags applied to all resources.')
param tags object

@description('Storage account name (3-24 lowercase alphanumeric).')
param storageAccountName string

@description('Blob container that holds the KB markdown docs.')
param kbContainerName string

@description('Principal ID of the runtime managed identity (Blob Data Contributor).')
param managedIdentityPrincipalId string

@description('Principal ID of the Azure AI Foundry account system-assigned managed identity (Blob Data Contributor for hosted-agent fallback auth).')
param foundryAccountPrincipalId string

@description('Principal ID of the Azure AI Search service system-assigned managed identity (Blob Data Reader for native indexer pull).')
param searchServicePrincipalId string = ''

@description('Object ID of the deploying user (optional local-dev data access).')
param principalId string = ''

@description('Principal type of the deployer for role assignments (User for interactive azd up, ServicePrincipal for CI).')
@allowed([
  'User'
  'ServicePrincipal'
  'Group'
])
param deployerPrincipalType string = 'User'

// Role definition IDs (built-in).
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var storageBlobDataReaderRoleId = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource kbContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: kbContainerName
  properties: {
    publicAccess: 'None'
  }
}

// Grant the runtime managed identity blob data access (postprovision uploads KB).
resource miBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, managedIdentityPrincipalId, storageBlobDataContributorRoleId)
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Grant the Foundry account system-assigned identity blob data access as a
// fallback if hosted containers resolve that identity instead of the UAMI.
resource foundryAccountBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, foundryAccountPrincipalId, storageBlobDataContributorRoleId, 'foundry-account')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalId: foundryAccountPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Grant the Search service system-assigned managed identity read access so its
// native Blob data source can pull KB markdown without storage keys or SAS.
resource searchBlobReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(searchServicePrincipalId)) {
  name: guid(storageAccount.id, searchServicePrincipalId, storageBlobDataReaderRoleId, 'search')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataReaderRoleId)
    principalId: searchServicePrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Optionally grant the deploying user blob data access for local index builds.
resource userBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(storageAccount.id, principalId, storageBlobDataContributorRoleId, 'user')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalId: principalId
    principalType: deployerPrincipalType
  }
}

// --- OUTPUTS (contract — do not change signatures) ---------------------------
output name string = storageAccount.name
output resourceId string = storageAccount.id
output blobEndpoint string = storageAccount.properties.primaryEndpoints.blob
