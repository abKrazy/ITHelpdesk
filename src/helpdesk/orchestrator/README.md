# src/orchestrator — Orchestrator Agent (Foundry Hosted Agent)

**Owner:** Trinity (AI / Agent Engineer)

## What goes here
The **Orchestrator** — a Python app built on the **Microsoft Agent Framework**,
deployed as a **Hosted Agent in Azure AI Foundry**. It is the single entry point
end users talk to (via the UI). It does NOT resolve tickets itself; it **routes**:

1. Receives the user request from the UI.
2. Hands off to the **Triage agent** (`src/agents`) to try KB resolution.
3. If unresolved, hands off to the **Incident agent** (`src/agents`) to
   create/assign/check/update ServiceNow incidents via the APIM MCP endpoint.
4. Streams the consolidated response back to the UI.

## Inputs it needs (from azd outputs → app settings / env)
- `AZURE_AI_PROJECT_ENDPOINT` — Foundry project to run under.
- `AZURE_OPENAI_CHAT_DEPLOYMENT` — chat model for reasoning/routing.
- `AZURE_CLIENT_ID` — managed identity for auth (DefaultAzureCredential).
- The triage + incident agent IDs (set by the postprovision hook).

## Deployment
Not an `azd` host. The `postprovision` hook packages this code and registers it
as a Foundry hosted agent, writing its agent ID back to the azd environment
(`AZURE_AI_ORCHESTRATOR_AGENT_ID`).

## Boundary
Routing + handoff only. KB grounding logic lives in `src/agents`. ServiceNow
call details live in `src/servicenow`.
