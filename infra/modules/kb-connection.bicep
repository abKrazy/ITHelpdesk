// =============================================================================
// modules/kb-connection.bicep — Owner: Trinity (AI/Agent) / Tank (infra)
// Foundry project **RemoteTool** connection for the Foundry IQ knowledge base
// (Azure AI Search agentic-retrieval) MCP endpoint.
//
// Why this exists: the triage Prompt Agent grounds on a **Foundry IQ knowledge
// base** — an Azure AI Search agentic-retrieval knowledgeBase (+knowledgeSource
// over the KB index) — via an MCP tool, exactly like the incident agent grounds
// on the ServiceNow APIM MCP server. This is NOT an inline Azure AI Search tool
// and NOT a managed project Index (AISearchIndexResource); those never surface
// as a Foundry IQ knowledge base in the portal.
//
// The connection authenticates as the **project's system-assigned managed
// identity** (authType 'ProjectManagedIdentity', audience the Search data plane)
// — no keys. The project MI is granted 'Search Index Data Reader' on the search
// service by search-rbac.bicep. The triage agent references this connection by
// NAME via MCPTool(project_connection_id=...), so the portal links the tool to
// the connection in the Tools/Connections tab.
//
// Runs AFTER search (needs the search endpoint) and foundry (needs the project +
// its system-assigned identity). The knowledgeSource/knowledgeBase themselves are
// created data-plane in postprovision (ensure_kb_knowledge_base); this connection
// only points at the KB's MCP endpoint, which resolves at query time.
//
// authType 'ProjectManagedIdentity' + audience 'https://search.azure.com/' and
// the target '{search}/knowledgebases/{kb}/mcp?api-version=...' shape are
// verified against MS Learn (foundry-iq-connect) and a live ARM PUT against this
// project (api-version 2025-04-01-preview).
// =============================================================================

@description('AI Foundry (Cognitive Services / AIServices) account name.')
param aiFoundryName string

@description('AI Foundry project name.')
param aiProjectName string

@description('Connection name (shown in the Foundry portal Connections/Tools tab).')
param connectionName string = 'it-helpdesk-kb-mcp'

@description('Azure AI Search service endpoint (e.g. https://<svc>.search.windows.net).')
param searchEndpoint string

@description('Foundry IQ knowledge base name (Azure AI Search agentic-retrieval knowledgeBase).')
param knowledgeBaseName string = 'it-helpdesk-kb'

@description('Search data-plane api-version pinned on the knowledge base MCP endpoint.')
param mcpApiVersion string = '2026-05-01-preview'

@description('AAD audience the project MI requests a token for (the Search data plane).')
param audience string = 'https://search.azure.com/'

// Knowledge base MCP endpoint the triage agent grounds through.
var kbMcpEndpointUrl = '${searchEndpoint}/knowledgebases/${knowledgeBaseName}/mcp?api-version=${mcpApiVersion}'

resource aiFoundry 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: aiFoundryName

  resource project 'projects' existing = {
    name: aiProjectName
  }
}

resource kbConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: aiFoundry::project
  name: connectionName
  properties: {
    // ProjectManagedIdentity is a valid Foundry connection authType but is not
    // yet in Bicep's connection type schema (BCP036). Verified via live ARM PUT.
    #disable-next-line BCP036
    authType: 'ProjectManagedIdentity'
    category: 'RemoteTool'
    target: kbMcpEndpointUrl
    isSharedToAll: true
    audience: audience
    metadata: {
      ApiType: 'Azure'
    }
  }
}

// --- OUTPUTS ------------------------------------------------------------------
output connectionId string = kbConnection.id
output connectionName string = kbConnection.name
output kbMcpEndpointUrl string = kbMcpEndpointUrl
output knowledgeBaseName string = knowledgeBaseName
