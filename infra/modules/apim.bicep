// =============================================================================
// modules/apim.bicep — Owner: Switch (API/MCP config) + Tank (resource)
// -----------------------------------------------------------------------------
// API Management (DEVELOPER tier). This module:
//   1. Provisions the APIM service with the shared user-assigned managed identity.
//   2. Imports the ServiceNow Table API OpenAPI spec
//      (assets/ServiceNow-OpenAPI-spec.json) as a REST API whose backend is the
//      ServiceNow instance.
//   3. Injects ServiceNow Basic auth at the gateway using Key Vault-backed named
//      values (credentials NEVER inlined — APIM reads them via the managed
//      identity granted Secrets User by keyvault.bicep).
//   4. Exposes the imported REST operations as an MCP server endpoint
//      (APIM AI-gateway MCP feature) that Trinity's incident agent calls.
//   5. Wires APIM diagnostics into Application Insights.
//
// Output signatures (name / gatewayUrl / mcpEndpointUrl) are LOCKED by main.bicep.
// The MCP endpoint URL follows the APIM convention: {gateway}/{mcpApiPath}/mcp.
// =============================================================================

@description('Deployment region.')
param location string

@description('Tags applied to all resources.')
param tags object

@description('API Management service name.')
param apimName string

@description('ServiceNow instance base URL (backend for the imported API).')
param serviceNowInstanceUrl string

@description('Key Vault name that holds ServiceNow credentials.')
param keyVaultName string

@description('Key Vault secret name for the ServiceNow username.')
param serviceNowUsernameSecretName string

@description('Key Vault secret name for the ServiceNow password.')
param serviceNowPasswordSecretName string

@description('Resource ID of the runtime managed identity (APIM uses it to read Key Vault).')
param managedIdentityResourceId string

@description('Principal ID of the runtime managed identity.')
#disable-next-line no-unused-params
param managedIdentityPrincipalId string

@description('Client ID of the runtime managed identity. Used by Key Vault-backed named values so APIM authenticates to Key Vault as the user-assigned identity.')
param managedIdentityClientId string

@description('Application Insights name for APIM diagnostics.')
param applicationInsightsName string

// -----------------------------------------------------------------------------
// Naming / paths
// -----------------------------------------------------------------------------
// The imported REST (Table API) lives at `restApiPath`; the MCP server is a
// second API of type `mcp` whose `path` is `mcpApiPath`. The public MCP URL is
// `{gateway}/{mcpApiPath}/mcp` — this MUST match the `mcpEndpointUrl` output.
var restApiPath = 'servicenow-api'
var mcpApiPath = 'servicenow'
var mcpEndpointPath = '${mcpApiPath}/mcp'

// Named value names (referenced in the inbound Basic-auth policy as {{name}}).
var usernameNamedValueName = 'servicenow-username'
var passwordNamedValueName = 'servicenow-password'

// Inbound policy: build "Basic base64(user:pass)" from the Key Vault-backed
// named values and force it onto every backend request to ServiceNow. The named
// values are resolved by APIM before the expression evaluates, so the raw
// credentials never appear in source, outputs, or logs.
var basicAuthPolicyXml = '<policies><inbound><base /><set-header name="Authorization" exists-action="override"><value>@("Basic " + System.Convert.ToBase64String(System.Text.Encoding.UTF8.GetBytes("{{${usernameNamedValueName}}}:{{${passwordNamedValueName}}}")))</value></set-header></inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>'

// -----------------------------------------------------------------------------
// Existing Application Insights (for diagnostics wiring)
// -----------------------------------------------------------------------------
resource applicationInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: applicationInsightsName
}

// -----------------------------------------------------------------------------
// 1) APIM service — Developer tier, user-assigned managed identity attached
// -----------------------------------------------------------------------------
resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' = {
  name: apimName
  location: location
  tags: tags
  sku: {
    name: 'Developer'
    capacity: 1
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityResourceId}': {}
    }
  }
  properties: {
    publisherEmail: 'admin@servicenow-helpdesk-accelerator.local'
    publisherName: 'ServiceNow IT Helpdesk Accelerator'
  }
}

// -----------------------------------------------------------------------------
// 2) Named values backed by Key Vault (ServiceNow Basic-auth credentials)
//    APIM authenticates to Key Vault as the user-assigned managed identity.
// -----------------------------------------------------------------------------
resource usernameNamedValue 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' = {
  parent: apim
  name: usernameNamedValueName
  properties: {
    displayName: usernameNamedValueName
    secret: true
    keyVault: {
      secretIdentifier: 'https://${keyVaultName}${environment().suffixes.keyvaultDns}/secrets/${serviceNowUsernameSecretName}'
      identityClientId: managedIdentityClientId
    }
  }
}

resource passwordNamedValue 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' = {
  parent: apim
  name: passwordNamedValueName
  properties: {
    displayName: passwordNamedValueName
    secret: true
    keyVault: {
      secretIdentifier: 'https://${keyVaultName}${environment().suffixes.keyvaultDns}/secrets/${serviceNowPasswordSecretName}'
      identityClientId: managedIdentityClientId
    }
  }
}

// -----------------------------------------------------------------------------
// 3) Import the ServiceNow Table API OpenAPI spec as a REST API
//    Backend serviceUrl = the ServiceNow instance base URL.
// -----------------------------------------------------------------------------
resource restApi 'Microsoft.ApiManagement/service/apis@2024-06-01-preview' = {
  parent: apim
  name: 'servicenow-table-api'
  properties: {
    displayName: 'ServiceNow Table API'
    description: 'ServiceNow Table API (incident CRUD) imported from the OpenAPI spec.'
    path: restApiPath
    protocols: [
      'https'
    ]
    subscriptionRequired: false
    serviceUrl: serviceNowInstanceUrl
    type: 'http'
    format: 'openapi+json'
    value: loadTextContent('../../assets/ServiceNow-OpenAPI-spec.json')
  }
}

// Inbound Basic-auth policy on the REST API (applies to every operation the MCP
// server exposes as a tool). Depends on the named values existing first.
resource restApiPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-06-01-preview' = {
  parent: restApi
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: basicAuthPolicyXml
  }
  dependsOn: [
    usernameNamedValue
    passwordNamedValue
  ]
}

// -----------------------------------------------------------------------------
// 4) Expose the imported REST API as an MCP server (AI-gateway MCP feature).
//
//    IMPORTANT — verified-working shape (2025-09-01-preview):
//    The MCP server is created as a **bare** API with `type: 'mcp'` ONLY — NO
//    `sourceApiId`, NO `apiType`, NO `mcpProperties`. Sending any of those extra
//    fields alongside `type: 'mcp'` makes the APIM control plane SILENTLY DROP the
//    `type` field, producing a plain HTTP API with no working MCP endpoint (the
//    original bug). The REST operations are wired in as tools via child
//    `apis/tools` resources whose `operationId` points at the source API's
//    operations (full ARM resource IDs). Basic auth to ServiceNow is inherited
//    from the source REST API's inbound policy when a tool routes to its
//    operation — the MCP server itself needs no auth policy.
//
//    Public URL: {gateway}/{mcpApiPath}/mcp  (streamable HTTP transport).
//    Verified live: initialize -> 200 JSON-RPC result; tools/list -> 6 tools;
//    tools/call queryTable -> reached ServiceNow (INC record) with Basic auth.
// -----------------------------------------------------------------------------
resource mcpApi 'Microsoft.ApiManagement/service/apis@2025-09-01-preview' = {
  parent: apim
  name: 'servicenow-mcp'
  properties: {
    displayName: 'ServiceNow MCP Server'
    description: 'MCP server exposing ServiceNow incident create/read/update operations as tools.'
    path: mcpApiPath
    protocols: [
      'https'
    ]
    subscriptionRequired: false
    type: 'mcp'
  }
  dependsOn: [
    restApiPolicy
  ]
}

// The MCP tools, one per source REST operation to expose. `sourceOperation` is
// the operation name auto-generated by APIM from the OpenAPI import (method +
// url path). `operationId` must be the FULL ARM resource ID of that operation.
var mcpTools = [
  {
    name: 'createIncident'
    sourceOperation: 'post-api-now-table-tablename'
    description: 'Create a record in a ServiceNow table (e.g. incident).'
  }
  {
    name: 'queryTable'
    sourceOperation: 'get-api-now-table-tablename'
    description: 'Query records from a ServiceNow table.'
  }
  {
    name: 'getRecord'
    sourceOperation: 'get-api-now-table-tablename-sys_id'
    description: 'Get a single ServiceNow record by sys_id.'
  }
  {
    name: 'patchRecord'
    sourceOperation: 'patch-api-now-table-tablename-sys_id'
    description: 'Partially update a ServiceNow record by sys_id.'
  }
  {
    name: 'updateRecord'
    sourceOperation: 'put-api-now-table-tablename-sys_id'
    description: 'Replace a ServiceNow record by sys_id.'
  }
  {
    name: 'deleteRecord'
    sourceOperation: 'delete-api-now-table-tablename-sys_id'
    description: 'Delete a ServiceNow record by sys_id.'
  }
]

// @batchSize(1) forces these child resources to deploy SERIALLY (one at a time).
// All 6 tools mutate the SAME parent MCP API (servicenow-mcp); deploying them in
// parallel makes concurrent writers race on the parent API's ETag, producing
// `PreconditionFailed: Resource was modified since last retrieval.` A partial
// failure leaves the live APIM with only a subset of tools (a corrupted tool set).
// Serializing removes the race so both a fresh `azd up` and re-provisions converge.
@batchSize(1)
resource mcpToolResources 'Microsoft.ApiManagement/service/apis/tools@2025-09-01-preview' = [
  for tool in mcpTools: {
    parent: mcpApi
    name: tool.name
    properties: {
      displayName: tool.name
      description: tool.description
      operationId: '${restApi.id}/operations/${tool.sourceOperation}'
    }
  }
]

// -----------------------------------------------------------------------------
// 5) Diagnostics -> Application Insights.
//    NOTE (MCP requirement): response payload logging is set to 0 bytes to avoid
//    buffering response bodies, which would break MCP streaming.
// -----------------------------------------------------------------------------
resource apimLogger 'Microsoft.ApiManagement/service/loggers@2024-06-01-preview' = {
  parent: apim
  name: 'appinsights'
  properties: {
    loggerType: 'applicationInsights'
    description: 'Application Insights logger for APIM.'
    resourceId: applicationInsights.id
    credentials: {
      instrumentationKey: applicationInsights.properties.InstrumentationKey
    }
  }
}

resource apimDiagnostics 'Microsoft.ApiManagement/service/diagnostics@2024-06-01-preview' = {
  parent: apim
  name: 'applicationinsights'
  properties: {
    loggerId: apimLogger.id
    alwaysLog: 'allErrors'
    sampling: {
      samplingType: 'fixed'
      percentage: 100
    }
    frontend: {
      response: {
        body: {
          bytes: 0
        }
      }
    }
    backend: {
      response: {
        body: {
          bytes: 0
        }
      }
    }
  }
}

// -----------------------------------------------------------------------------
// OUTPUTS (contract — signatures locked by main.bicep)
// -----------------------------------------------------------------------------
output name string = apim.name
output gatewayUrl string = apim.properties.gatewayUrl
output mcpEndpointUrl string = '${apim.properties.gatewayUrl}/${mcpEndpointPath}'
