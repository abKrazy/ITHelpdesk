// =============================================================================
// main.bicep — SUBSCRIPTION-SCOPED entry point for `azd up`
// =============================================================================
// Owner: Tank (Infra). Authored/contract-locked by Morpheus (Lead).
//
// Responsibilities of THIS file (fully authored — do not stub):
//   1. Create the single resource group that holds EVERY resource.
//   2. Compute the resource token + consistent naming for all resources.
//   3. Wire up every module with the correct params.
//   4. Surface the complete OUTPUTS CONTRACT the app + agents depend on.
//
// The module BODIES (infra/modules/*.bicep) are intentionally STUBS that Tank
// implements. Their param/output *signatures* are fixed by this file — changing
// a module signature is a cross-team contract change and must go through
// Morpheus. Prefer Azure Verified Modules (AVM) inside each stub where sensible.
// =============================================================================

targetScope = 'subscription'

// -----------------------------------------------------------------------------
// PARAMETERS (bound from the azd environment via main.parameters.json)
// -----------------------------------------------------------------------------

@minLength(1)
@maxLength(64)
@description('Name of the azd environment. Used to derive the resource token and RG name.')
param environmentName string

@minLength(1)
@allowed([
  // Foundry **Hosted Agents** (the Phase-2 orchestrator) are only available in
  // these regions. Picking anything else fails hosted-agent registration with
  // "Unsupported region for Foundry Hosted Agents". Keep in sync with
  // https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents#region-availability
  'eastus2'
  'northcentralus'
  'swedencentral'
  'westus'
  'westus3'
])
@description('Primary Azure region for all resources. azd prompts for this (AZURE_LOCATION). Restricted to Foundry Hosted Agents regions.')
param location string

@description('Object ID of the deploying user/principal. azd sets AZURE_PRINCIPAL_ID. Granted data-plane roles for local dev (Search/Storage/Foundry).')
param principalId string = ''

// --- ServiceNow inputs (collected by scripts/preprovision) -------------------
@description('ServiceNow instance base URL. Default is the accelerator dev instance.')
param serviceNowInstanceUrl string = 'https://dev283128.service-now.com'

@description('ServiceNow username used for Table API / MCP auth.')
param serviceNowUsername string

@secure()
@description('ServiceNow password. Stored ONLY in Key Vault; never emitted as an output.')
param serviceNowPassword string

// --- Model deployment knobs (Trinity/Tank tune) ------------------------------
@description('Chat model deployment name used by the agents.')
param chatModelDeploymentName string = 'gpt-4o'

@description('Chat model name to deploy in Foundry.')
param chatModelName string = 'gpt-4o'

@description('Embedding model deployment name used to index KB docs.')
param embeddingModelDeploymentName string = 'text-embedding-3-large'

@description('Embedding model name to deploy in Foundry.')
param embeddingModelName string = 'text-embedding-3-large'

@description('Name of the Azure AI Search index that stores the grounded KB.')
param searchIndexName string = 'it-helpdesk-kb'

@description('Blob container that holds the raw KB markdown docs.')
param kbContainerName string = 'kbdocs'

// -----------------------------------------------------------------------------
// NAMING — resource token + convention (locked contract)
// -----------------------------------------------------------------------------
// resourceToken makes every deployment's resources globally unique yet stable
// for a given (subscription, environment, location). ALL resources use the
// pattern:  <abbreviation><resourceToken>   (or <abbr>-<env>-<token> where a
// hyphenated, human-readable name is preferred, e.g. the resource group).
var abbrs = loadJsonContent('./abbreviations.json')
var resourceToken = uniqueString(subscription().id, environmentName, location)
var tags = {
  'azd-env-name': environmentName
  solution: 'servicenow-it-helpdesk-agent'
}

var rgName = '${abbrs.resourcesResourceGroups}${environmentName}'

// -----------------------------------------------------------------------------
// RESOURCE GROUP — the single RG for the whole accelerator
// -----------------------------------------------------------------------------
resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: rgName
  location: location
  tags: tags
}

// -----------------------------------------------------------------------------
// MODULE WIRING (all deploy INTO the single RG)
// Sequencing (implicit via dependency refs):
//   monitoring -> identity -> keyvault -> storage/search -> foundry -> search-rbac/acr/apim -> appservice
// -----------------------------------------------------------------------------

// 1) Monitoring: Log Analytics + Application Insights ------------- Owner: Tank
module monitoring './modules/monitoring.bicep' = {
  name: 'monitoring'
  scope: rg
  params: {
    location: location
    tags: tags
    logAnalyticsName: '${abbrs.operationalInsightsWorkspaces}${resourceToken}'
    applicationInsightsName: '${abbrs.insightsComponents}${resourceToken}'
  }
}

// 2) Identity: user-assigned managed identity (app + agents run as) - Owner: Tank
//    RBAC role assignments live inside identity.bicep so they can target each
//    resource by principalId once created (Tank wires the graph).
module identity './modules/identity.bicep' = {
  name: 'identity'
  scope: rg
  params: {
    location: location
    tags: tags
    managedIdentityName: '${abbrs.managedIdentityUserAssignedIdentities}${resourceToken}'
  }
}

// 3) Key Vault: holds ServiceNow creds; MI granted Secrets User ---- Owner: Tank
module keyvault './modules/keyvault.bicep' = {
  name: 'keyvault'
  scope: rg
  params: {
    location: location
    tags: tags
    keyVaultName: '${abbrs.keyVaultVaults}${resourceToken}'
    // Grant both the runtime managed identity and (optionally) the deploying
    // user access to secrets.
    managedIdentityPrincipalId: identity.outputs.principalId
    principalId: principalId
    // ServiceNow secrets to seed. Password is @secure and never output.
    serviceNowUsername: serviceNowUsername
    serviceNowPassword: serviceNowPassword
  }
}

// 4) Storage: KB docs container (Trinity's postprovision uploads) -- Owner: Tank
module storage './modules/storage.bicep' = {
  name: 'storage'
  scope: rg
  params: {
    location: location
    tags: tags
    storageAccountName: '${abbrs.storageStorageAccounts}${resourceToken}'
    kbContainerName: kbContainerName
    managedIdentityPrincipalId: identity.outputs.principalId
    principalId: principalId
  }
}

// 5) AI Search: vector/semantic index over the KB ---------------- Owner: Tank
module search './modules/search.bicep' = {
  name: 'search'
  scope: rg
  params: {
    location: location
    tags: tags
    searchServiceName: '${abbrs.searchSearchServices}${resourceToken}'
    managedIdentityPrincipalId: identity.outputs.principalId
    principalId: principalId
  }
}

// 6) Foundry: AI hub/project + model deployments ----------------- Owner: Tank
//    Trinity's postprovision creates the 3 agents against this project.
module foundry './modules/foundry.bicep' = {
  name: 'foundry'
  scope: rg
  params: {
    location: location
    tags: tags
    aiFoundryName: '${abbrs.cognitiveServicesAccounts}${resourceToken}'
    aiProjectName: '${abbrs.aiFoundryProjects}${resourceToken}'
    chatModelDeploymentName: chatModelDeploymentName
    chatModelName: chatModelName
    embeddingModelDeploymentName: embeddingModelDeploymentName
    embeddingModelName: embeddingModelName
    // Native AI Search connection for the triage KB tool is created in the
    // Foundry module (control-plane); MCP + telemetry stay data-plane.
    managedIdentityResourceId: identity.outputs.resourceId
    managedIdentityPrincipalId: identity.outputs.principalId
    principalId: principalId
    searchServicePrincipalId: search.outputs.principalId
    searchEndpoint: search.outputs.endpoint
    searchResourceId: search.outputs.resourceId
  }
}

// 7) Cross-service Search RBAC: Foundry identities can manage/read Search.
module searchRbac './modules/search-rbac.bicep' = {
  name: 'search-rbac'
  scope: rg
  params: {
    searchServiceName: search.outputs.name
    foundryProjectPrincipalId: foundry.outputs.projectPrincipalId
    foundryAccountPrincipalId: foundry.outputs.aiFoundryPrincipalId
  }
}

// 8) ACR: stores the Phase-2 Foundry Hosted Agent container image -- Owner: Tank
module acr './modules/acr.bicep' = {
  name: 'acr'
  scope: rg
  params: {
    location: location
    tags: tags
    acrName: '${abbrs.containerRegistryRegistries}${resourceToken}'
    foundryProjectPrincipalId: foundry.outputs.projectPrincipalId
    principalId: principalId
  }
}

// 9) APIM: Developer tier, imports the ServiceNow OpenAPI spec and
//    exposes it as an MCP server endpoint. --------------------- Owner: Switch (config) / Tank (resource)
module apim './modules/apim.bicep' = {
  name: 'apim'
  scope: rg
  params: {
    location: location
    tags: tags
    apimName: '${abbrs.apiManagementService}${resourceToken}'
    // ServiceNow backend + auth (auth pulled from Key Vault named values).
    serviceNowInstanceUrl: serviceNowInstanceUrl
    keyVaultName: keyvault.outputs.name
    serviceNowUsernameSecretName: keyvault.outputs.serviceNowUsernameSecretName
    serviceNowPasswordSecretName: keyvault.outputs.serviceNowPasswordSecretName
    managedIdentityResourceId: identity.outputs.resourceId
    managedIdentityPrincipalId: identity.outputs.principalId
    // Client ID is required so APIM's Key Vault-backed named values authenticate
    // as the user-assigned managed identity (granted Secrets User by keyvault.bicep).
    managedIdentityClientId: identity.outputs.clientId
    applicationInsightsName: monitoring.outputs.applicationInsightsName
  }
}

// 10) App Service: the customer-facing UI ------------------------ Owner: Tank
module appservice './modules/appservice.bicep' = {
  name: 'appservice'
  scope: rg
  params: {
    location: location
    tags: tags
    appServicePlanName: '${abbrs.webServerFarms}${resourceToken}'
    appServiceName: '${abbrs.webSitesAppService}${resourceToken}'
    managedIdentityResourceId: identity.outputs.resourceId
    managedIdentityClientId: identity.outputs.clientId
    keyVaultName: keyvault.outputs.name
    applicationInsightsConnectionString: monitoring.outputs.applicationInsightsConnectionString
    // App settings the UI needs to reach the orchestrator + services.
    aiProjectEndpoint: foundry.outputs.projectEndpoint
    openAiEndpoint: foundry.outputs.openAiEndpoint
    openAiEmbeddingDeployment: embeddingModelDeploymentName
    openAiChatDeployment: chatModelDeploymentName
    serviceNowMcpEndpoint: apim.outputs.mcpEndpointUrl
    searchEndpoint: search.outputs.endpoint
    searchIndexName: searchIndexName
  }
}

// -----------------------------------------------------------------------------
// OUTPUTS CONTRACT — consumed by azd env, the UI app settings, and the
// postprovision hook (KB upload + agent creation). Do NOT remove or rename
// without a cross-team contract change (Morpheus approval).
// -----------------------------------------------------------------------------

// -- Environment / RG --
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_LOCATION string = location
output AZURE_RESOURCE_TOKEN string = resourceToken

// -- Managed identity (how everything authenticates) --
output AZURE_MANAGED_IDENTITY_NAME string = identity.outputs.name
output AZURE_MANAGED_IDENTITY_CLIENT_ID string = identity.outputs.clientId
output AZURE_MANAGED_IDENTITY_PRINCIPAL_ID string = identity.outputs.principalId
output AZURE_MANAGED_IDENTITY_RESOURCE_ID string = identity.outputs.resourceId

// -- Key Vault --
output AZURE_KEY_VAULT_NAME string = keyvault.outputs.name
output AZURE_KEY_VAULT_ENDPOINT string = keyvault.outputs.endpoint
output SERVICENOW_USERNAME_SECRET_NAME string = keyvault.outputs.serviceNowUsernameSecretName
output SERVICENOW_PASSWORD_SECRET_NAME string = keyvault.outputs.serviceNowPasswordSecretName

// -- Foundry (Trinity's agents target these) --
output AZURE_AI_FOUNDRY_NAME string = foundry.outputs.aiFoundryName
output AZURE_AI_PROJECT_NAME string = foundry.outputs.projectName
output AZURE_AI_PROJECT_PRINCIPAL_ID string = foundry.outputs.projectPrincipalId
output AZURE_AI_PROJECT_ENDPOINT string = foundry.outputs.projectEndpoint
output AZURE_OPENAI_ENDPOINT string = foundry.outputs.openAiEndpoint
output AZURE_OPENAI_CHAT_DEPLOYMENT string = chatModelDeploymentName
output AZURE_OPENAI_EMBEDDING_DEPLOYMENT string = embeddingModelDeploymentName

// -- Storage (KB source docs) --
output AZURE_STORAGE_ACCOUNT_NAME string = storage.outputs.name
output AZURE_STORAGE_BLOB_ENDPOINT string = storage.outputs.blobEndpoint
output AZURE_STORAGE_KB_CONTAINER string = kbContainerName

// -- AI Search (grounded KB index) --
output AZURE_SEARCH_SERVICE_NAME string = search.outputs.name
output AZURE_SEARCH_ENDPOINT string = search.outputs.endpoint
output AZURE_SEARCH_INDEX_NAME string = searchIndexName

// -- Container Registry (Foundry Hosted Agent images) --
output AZURE_CONTAINER_REGISTRY_NAME string = acr.outputs.name
output ACR_LOGIN_SERVER string = acr.outputs.loginServer
output ACR_RESOURCE_ID string = acr.outputs.resourceId

// -- APIM MCP endpoint (Switch's incident agent calls this) --
output AZURE_APIM_NAME string = apim.outputs.name
output AZURE_APIM_GATEWAY_URL string = apim.outputs.gatewayUrl
output APIM_GATEWAY_URL string = apim.outputs.gatewayUrl
output APIM_MCP_URL string = apim.outputs.mcpEndpointUrl
output SERVICENOW_MCP_ENDPOINT string = apim.outputs.mcpEndpointUrl
@secure()
output APIM_SUBSCRIPTION_KEY string = apim.outputs.mcpSubscriptionKey
@secure()
output SERVICENOW_MCP_SUBSCRIPTION_KEY string = apim.outputs.mcpSubscriptionKey
output SERVICENOW_INSTANCE_URL string = serviceNowInstanceUrl

// -- App Service (the UI) --
output AZURE_APP_SERVICE_NAME string = appservice.outputs.name
output SERVICE_UI_URI string = appservice.outputs.uri

// -- Monitoring --
output APPLICATIONINSIGHTS_CONNECTION_STRING string = monitoring.outputs.applicationInsightsConnectionString
output AZURE_LOG_ANALYTICS_WORKSPACE_ID string = monitoring.outputs.logAnalyticsWorkspaceId
