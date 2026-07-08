# src/agents — Specialist Foundry Agents (Triage + Incident)

**Owner:** Trinity (AI / Agent Engineer)

## What goes here
Definitions (instructions, tools, model config) for the two specialist agents the
Orchestrator hands off to. Each does **one job**.

### 1. Triage agent
- **Goal:** resolve the user request from the knowledge base before any ticket.
- **Grounding:** KB docs in Azure Storage, indexed in **Azure AI Search**
  (`AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_INDEX_NAME`).
- **Output:** either a resolution (with cited KB steps) or a "not resolved —
  escalate" signal plus the recommended **Assignment Group** parsed from the KB.

### 2. Incident agent
- **Goal:** create / assign / check status / update ServiceNow incidents.
- **Tools:** the **APIM MCP endpoint** (`SERVICENOW_MCP_ENDPOINT`). It does NOT
  call ServiceNow directly — it goes through APIM. The MCP client + tool wiring
  live in `src/servicenow`; this module composes them into an agent.
- Maps the 4 sample prompts: lookup incident, create incident, update urgency.

## Inputs it needs (from azd outputs)
- `AZURE_AI_PROJECT_ENDPOINT`, `AZURE_OPENAI_CHAT_DEPLOYMENT`,
  `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`
- `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_INDEX_NAME`
- `SERVICENOW_MCP_ENDPOINT`
- `AZURE_CLIENT_ID`

## Boundary
Agent definitions + KB grounding. Raw ServiceNow/MCP transport → `src/servicenow`.
Routing between agents → `src/orchestrator`.
