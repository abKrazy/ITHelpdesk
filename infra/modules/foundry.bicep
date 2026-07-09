// =============================================================================
// modules/foundry.bicep — STUB (Owner: Tank)
// Azure AI Foundry account (hub) + project + model deployments. This is where
// Trinity's 3 agents (orchestrator hosted agent, triage, incident) live. The
// project emits endpoints for postprovision to create native data-plane
// connections (AI Search, MCP, telemetry) after ARM provisioning.
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

@description('Resource ID of the runtime managed identity.')
param managedIdentityResourceId string

@description('Principal ID of the runtime managed identity (Foundry data roles).')
param managedIdentityPrincipalId string

@description('Object ID of the deploying user (optional local-dev access).')
param principalId string = ''

@description('Principal ID of the Azure AI Search service system-assigned managed identity.')
param searchServicePrincipalId string

@description('Azure AI Search endpoint for the native triage Knowledge Base tool connection.')
param searchEndpoint string

@description('Azure AI Search service resource ID (connection metadata).')
param searchResourceId string

// Role definition IDs (built-in).
var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var cognitiveServicesOpenAIUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var azureAIDeveloperRoleId = '64702f94-c441-49e6-a78b-ef80e0188fee'
var foundryProjectManagerRoleId = 'eadc314b-1a2d-4efa-be10-5d325db5065e'

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

// Project connections: the Azure AI Search connection backing the native triage
// Knowledge Base tool is created control-plane here. azure-ai-projects 2.x has no
// data-plane connection *create* API (only get/list/get_default), so postprovision
// cannot create it — it only reads it back. AAD auth: the project + account
// identities are granted Search data roles by search-rbac.bicep. MCP + telemetry
// connections remain data-plane (the MCP tool passes its APIM key inline).
resource searchConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: aiProject
  name: '${aiProjectName}-search'
  properties: {
    category: 'CognitiveSearch'
    target: searchEndpoint
    authType: 'AAD'
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: searchResourceId
      Location: location
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

resource searchOpenAIUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiFoundry.id, searchServicePrincipalId, cognitiveServicesOpenAIUserRoleId)
  scope: aiFoundry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAIUserRoleId)
    principalId: searchServicePrincipalId
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

resource userFoundryProjectManagerOnAccount 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(aiFoundry.id, principalId, foundryProjectManagerRoleId, 'account')
  scope: aiFoundry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryProjectManagerRoleId)
    principalId: principalId
  }
}

resource userFoundryProjectManagerOnProject 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(aiProject.id, principalId, foundryProjectManagerRoleId, 'project')
  scope: aiProject
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryProjectManagerRoleId)
    principalId: principalId
  }
}

// --- OUTPUTS (contract — do not change signatures) ---------------------------
output aiFoundryName string = aiFoundry.name
output aiFoundryPrincipalId string = aiFoundry.identity.principalId
output projectName string = aiProject.name
output projectPrincipalId string = aiProject.identity.principalId
output projectEndpoint string = 'https://${toLower(aiFoundryName)}.services.ai.azure.com/api/projects/${aiProjectName}'
output openAiEndpoint string = 'https://${toLower(aiFoundryName)}.openai.azure.com/'
output searchConnectionName string = searchConnection.name
