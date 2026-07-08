// =============================================================================
// modules/foundry.bicep — STUB (Owner: Tank)
// Azure AI Foundry account (hub) + project + model deployments. This is where
// Trinity's 3 agents (orchestrator hosted agent, triage, incident) live. The
// project connects to AI Search (grounding) and Storage, and emits telemetry
// to Application Insights.
// Signature LOCKED by main.bicep.
// Prefer AVM: br/public:avm/res/cognitive-services/account (kind=AIServices)
//             + project + deployments, or the Foundry AVM pattern module.
// =============================================================================

@description('Deployment region.')
param location string

@description('Tags applied to all resources.')
param tags object

@description('AI Foundry (Cognitive Services / AIServices) account name.')
param aiFoundryName string

@description('AI Foundry project name.')
param aiProjectName string

@description('Chat model deployment name.')
param chatModelDeploymentName string

@description('Chat model name to deploy.')
param chatModelName string

@description('Embedding model deployment name.')
param embeddingModelDeploymentName string

@description('Embedding model name to deploy.')
param embeddingModelName string

@description('Resource ID of the AI Search service to connect for grounding.')
param searchServiceResourceId string

@description('Resource ID of the Storage account to connect.')
param storageAccountResourceId string

@description('KB blob container name for the project storage connection.')
param kbContainerName string

@description('Resource ID of Application Insights for tracing.')
param applicationInsightsResourceId string

@description('Resource ID of the runtime managed identity.')
param managedIdentityResourceId string

@description('Principal ID of the runtime managed identity (Foundry data roles).')
param managedIdentityPrincipalId string

@description('Object ID of the deploying user (optional local-dev access).')
param principalId string = ''

// Role definition IDs (built-in).
var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var cognitiveServicesOpenAIUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var azureAIDeveloperRoleId = '64702f94-c441-49e6-a78b-ef80e0188fee'

// Azure AI Foundry account (Cognitive Services / AIServices kind) with project
// management enabled so the child project + hosted agents can be created.
resource aiFoundry 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: aiFoundryName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityResourceId}': {}
    }
  }
  properties: {
    allowProjectManagement: true
    customSubDomainName: toLower(aiFoundryName)
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

// The Foundry project — where Trinity's agents (orchestrator/triage/incident) live.
resource aiProject 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: aiFoundry
  name: aiProjectName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: aiProjectName
    description: 'ServiceNow IT Helpdesk agent project'
  }
}

// Chat model deployment (agents reason with this).
resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = {
  parent: aiFoundry
  name: chatModelDeploymentName
  sku: {
    name: 'GlobalStandard'
    capacity: 30
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: chatModelName
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
    raiPolicyName: 'Microsoft.DefaultV2'
  }
}

// Embedding model deployment (KB indexing). Serialized after the chat deployment
// because a Cognitive Services account rejects parallel deployment writes.
resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = {
  parent: aiFoundry
  name: embeddingModelDeploymentName
  sku: {
    name: 'Standard'
    capacity: 30
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: embeddingModelName
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
    raiPolicyName: 'Microsoft.DefaultV2'
  }
  dependsOn: [
    chatDeployment
  ]
}

// --- Project connections (grounding + telemetry) -----------------------------
// Derive resource names/endpoints from the passed-in resource IDs.
var searchServiceNameFromId = last(split(searchServiceResourceId, '/'))
var storageAccountNameFromId = last(split(storageAccountResourceId, '/'))

// Connect the project to Azure AI Search for grounded retrieval (AAD auth).
resource searchConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: aiProject
  name: 'search-connection'
  properties: {
    category: 'CognitiveSearch'
    target: 'https://${searchServiceNameFromId}.search.windows.net'
    authType: 'AAD'
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: searchServiceResourceId
      Location: location
    }
  }
}

// Connect the project to the KB storage account (AAD auth).
resource storageConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: aiProject
  name: 'storage-connection'
  properties: {
    category: 'AzureBlob'
    target: 'https://${storageAccountNameFromId}.blob.${environment().suffixes.storage}/'
    authType: 'AAD'
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: storageAccountResourceId
      AccountName: storageAccountNameFromId
      ContainerName: kbContainerName
      Location: location
    }
  }
}

// Wire Application Insights for agent/project tracing.
resource appInsightsConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: aiProject
  name: 'appinsights-connection'
  properties: {
    category: 'AppInsights'
    target: applicationInsightsResourceId
    authType: 'ApiKey'
    isSharedToAll: true
    credentials: {
      key: applicationInsightsResourceId
    }
    metadata: {
      ApiType: 'Azure'
      ResourceId: applicationInsightsResourceId
    }
  }
}

// Grant the runtime managed identity the roles needed to use the project + models.
resource miAiDeveloper 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiFoundry.id, managedIdentityPrincipalId, azureAIDeveloperRoleId)
  scope: aiFoundry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', azureAIDeveloperRoleId)
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource miCognitiveUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiFoundry.id, managedIdentityPrincipalId, cognitiveServicesUserRoleId)
  scope: aiFoundry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource miOpenAIUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiFoundry.id, managedIdentityPrincipalId, cognitiveServicesOpenAIUserRoleId)
  scope: aiFoundry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAIUserRoleId)
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Optionally grant the deploying user the same access for local dev.
resource userAiDeveloper 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(aiFoundry.id, principalId, azureAIDeveloperRoleId, 'user')
  scope: aiFoundry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', azureAIDeveloperRoleId)
    principalId: principalId
  }
}

resource userOpenAIUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(aiFoundry.id, principalId, cognitiveServicesOpenAIUserRoleId, 'user')
  scope: aiFoundry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAIUserRoleId)
    principalId: principalId
  }
}

// --- OUTPUTS (contract — do not change signatures) ---------------------------
output aiFoundryName string = aiFoundry.name
output projectName string = aiProject.name
output projectEndpoint string = 'https://${toLower(aiFoundryName)}.services.ai.azure.com/api/projects/${aiProjectName}'
output openAiEndpoint string = 'https://${toLower(aiFoundryName)}.openai.azure.com/'
