// =============================================================================
// modules/mcp-connection.bicep — Owner: Tank
// Foundry project **RemoteTool** connection for the ServiceNow APIM MCP server.
//
// Why this exists: the Incident Prompt Agent's MCP tool used to carry the APIM
// subscription key inline in its definition headers, so it never surfaced as a
// reusable project connection in the Foundry portal and stored the key in
// plaintext. Creating it as a project connection (control-plane — the
// azure-ai-projects data-plane SDK has no connection *create* API) makes the
// MCP tool show in the portal Connections/Tools tab and keeps the key in the
// Foundry connection secret store. The Incident agent then references it via
// MCPTool(project_connection_id=...) with no inline headers.
//
// Runs AFTER apim so the APIM subscription key (a @secure() output) is available.
// Category/authType verified against microsoft-foundry/foundry-samples
// (prompt-agents/code-interpreter-custom): category 'RemoteTool',
// authType 'CustomKeys', credentials.keys keyed by the HTTP header name.
// =============================================================================

@description('AI Foundry (Cognitive Services / AIServices) account name.')
param aiFoundryName string

@description('AI Foundry project name.')
param aiProjectName string

@description('Connection name (shown in the Foundry portal Connections/Tools tab).')
param connectionName string = 'servicenow-apim-mcp'

@description('APIM MCP server endpoint URL (connection target).')
param mcpEndpointUrl string

@description('HTTP header name the MCP/APIM backend expects the key in.')
param apimKeyHeaderName string = 'Ocp-Apim-Subscription-Key'

@description('APIM subscription key — stored as a connection secret, not inline.')
@secure()
param apimSubscriptionKey string

resource aiFoundry 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: aiFoundryName

  resource project 'projects' existing = {
    name: aiProjectName
  }
}

resource mcpConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: aiFoundry::project
  name: connectionName
  properties: {
    authType: 'CustomKeys'
    category: 'RemoteTool'
    target: mcpEndpointUrl
    isSharedToAll: true
    credentials: {
      keys: {
        '${apimKeyHeaderName}': apimSubscriptionKey
      }
    }
  }
}

// --- OUTPUTS ------------------------------------------------------------------
// Full ARM resource ID — the value passed to MCPTool(project_connection_id=...).
output connectionId string = mcpConnection.id
output connectionName string = mcpConnection.name
