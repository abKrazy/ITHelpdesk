// =============================================================================
// modules/appservice.bicep — STUB (Owner: Tank)
// Linux App Service plan + Web App hosting the customer-facing UI
// (src/helpdesk/ui, deployed from the ./src root — see azure.yaml).
// Runs as the user-assigned managed identity. App settings wire the UI to the
// Foundry project (orchestrator) and expose the ServiceNow MCP endpoint. Secrets
// are referenced from Key Vault, never inlined.
// Signature LOCKED by main.bicep. The web app MUST be tagged
// 'azd-service-name: ui' so `azd deploy` targets it.
// Prefer AVM: br/public:avm/res/web/serverfarm + br/public:avm/res/web/site
// =============================================================================

@description('Deployment region.')
param location string

@description('Tags applied to all resources.')
param tags object

@description('App Service plan name.')
param appServicePlanName string

@description('Web App name.')
param appServiceName string

@description('Resource ID of the runtime managed identity.')
param managedIdentityResourceId string

@description('Client ID of the runtime managed identity (for AAD token requests).')
param managedIdentityClientId string

@description('Key Vault name for Key Vault-referenced app settings.')
param keyVaultName string

@description('Application Insights connection string.')
param applicationInsightsConnectionString string

@description('Foundry project endpoint the UI uses to reach the Orchestrator.')
param aiProjectEndpoint string

@description('Azure OpenAI endpoint used for chat and embedding model calls.')
param openAiEndpoint string

@description('Azure OpenAI embedding model deployment name.')
param openAiEmbeddingDeployment string

@description('Azure OpenAI chat model deployment name.')
param openAiChatDeployment string

@description('ServiceNow MCP endpoint URL (from APIM).')
param serviceNowMcpEndpoint string

@description('Azure AI Search endpoint the triage agent queries for grounded KB.')
param searchEndpoint string

@description('Azure AI Search index name that holds the grounded KB.')
param searchIndexName string

resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: appServicePlanName
  location: location
  tags: tags
  kind: 'linux'
  // Basic B2 gives the UI more CPU/RAM headroom while keeping Always On (warm-keep).
  // Basic (B1+) is the minimum tier that supports Always On. Do not downgrade to
  // Free/Shared (F1/D1) — those cannot hold the gunicorn worker warm, reintroducing
  // a first-request cold start on the UI after idle.
  sku: {
    name: 'B2'
    tier: 'Basic'
  }
  properties: {
    reserved: true
  }
}

resource webApp 'Microsoft.Web/sites@2023-12-01' = {
  name: appServiceName
  location: location
  tags: union(tags, {
    'azd-service-name': 'ui'
  })
  kind: 'app,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityResourceId}': {}
    }
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    keyVaultReferenceIdentity: managedIdentityResourceId
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      // Warm-keep: keeps the gunicorn/uvicorn worker resident so the first user
      // request after idle does not pay a Python app cold start. Requires B1+ plan.
      alwaysOn: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appCommandLine: 'python -m gunicorn helpdesk.ui.app:app --bind 0.0.0.0:8000 --timeout 600 --worker-class uvicorn.workers.UvicornWorker'
      appSettings: [
        {
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: 'true'
        }
        {
          name: 'ENABLE_ORYX_BUILD'
          value: 'true'
        }
        {
          name: 'WEBSITES_PORT'
          value: '8000'
        }
        {
          // The managed identity the UI authenticates as (DefaultAzureCredential).
          name: 'AZURE_CLIENT_ID'
          value: managedIdentityClientId
        }
        {
          name: 'AZURE_AI_PROJECT_ENDPOINT'
          value: aiProjectEndpoint
        }
        {
          name: 'AZURE_OPENAI_ENDPOINT'
          value: openAiEndpoint
        }
        {
          name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT'
          value: openAiEmbeddingDeployment
        }
        {
          name: 'AZURE_OPENAI_CHAT_DEPLOYMENT'
          value: openAiChatDeployment
        }
        {
          name: 'SERVICENOW_MCP_ENDPOINT'
          value: serviceNowMcpEndpoint
        }
        {
          name: 'AZURE_SEARCH_ENDPOINT'
          value: searchEndpoint
        }
        {
          name: 'AZURE_SEARCH_INDEX_NAME'
          value: searchIndexName
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: applicationInsightsConnectionString
        }
        {
          name: 'AZURE_KEY_VAULT_NAME'
          value: keyVaultName
        }
      ]
    }
  }
}

// --- OUTPUTS (contract — do not change signatures) ---------------------------
output name string = webApp.name
output uri string = 'https://${webApp.properties.defaultHostName}'
