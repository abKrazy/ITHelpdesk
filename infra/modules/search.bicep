// =============================================================================
// modules/search.bicep — STUB (Owner: Tank)
// Azure AI Search service that stores the vector/semantic KB index used by the
// triage agent for grounding. The postprovision hook builds the index
// (AZURE_SEARCH_INDEX_NAME) from the KB docs in Storage.
// Managed identity granted "Search Index Data Contributor" + "Search Service
// Contributor"; Foundry connects to this service for grounding.
// Signature LOCKED by main.bicep.
// Prefer AVM: br/public:avm/res/search/search-service
// =============================================================================

@description('Deployment region.')
param location string

@description('Tags applied to all resources.')
param tags object

@description('Azure AI Search service name.')
param searchServiceName string

@description('Principal ID of the runtime managed identity (index data + service contributor).')
param managedIdentityPrincipalId string

@description('Object ID of the deploying user (optional local-dev data access).')
param principalId string = ''

// Role definition IDs (built-in).
var searchIndexDataContributorRoleId = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
var searchServiceContributorRoleId = '7ca78c08-252a-4471-8644-bb5ff32d4ba0'

resource searchService 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: searchServiceName
  location: location
  tags: tags
  sku: {
    name: 'basic'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: 'enabled'
    // Enable RBAC (AAD) data-plane auth; keep API keys off for least privilege.
    disableLocalAuth: true
    authOptions: null
    semanticSearch: 'free'
  }
}

// Grant the runtime managed identity data-plane + control-plane access.
resource miIndexDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, managedIdentityPrincipalId, searchIndexDataContributorRoleId)
  scope: searchService
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataContributorRoleId)
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource miServiceContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, managedIdentityPrincipalId, searchServiceContributorRoleId)
  scope: searchService
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchServiceContributorRoleId)
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Optionally grant the deploying user data-plane + control-plane access.
resource userIndexDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(searchService.id, principalId, searchIndexDataContributorRoleId, 'user')
  scope: searchService
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataContributorRoleId)
    principalId: principalId
  }
}

resource userServiceContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(searchService.id, principalId, searchServiceContributorRoleId, 'user')
  scope: searchService
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchServiceContributorRoleId)
    principalId: principalId
  }
}

// --- OUTPUTS (contract — do not change signatures) ---------------------------
output name string = searchService.name
output resourceId string = searchService.id
output endpoint string = 'https://${searchService.name}.search.windows.net'
output principalId string = searchService.identity.principalId
