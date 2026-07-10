# src/helpdesk/orchestrator — Orchestrator Agent (Foundry Hosted Agent)

**Owner:** Trinity (AI / Agent Engineer)

## What goes here
The **Orchestrator** — a Python app built on the **Microsoft Agent Framework**,
deployed as a **Hosted Agent in Azure AI Foundry**. It is the single entry point
end users talk to (via the UI). It does NOT resolve tickets itself; it **routes**:

1. Receives the user request from the UI.
2. Hands off to the **Triage agent** (`src/helpdesk/agents`) to try KB resolution.
3. If unresolved, hands off to the **Incident agent** (`src/helpdesk/agents`) to
   create/assign/check/update ServiceNow incidents via the APIM MCP endpoint.
4. Streams the consolidated response back to the UI.

## Inputs it needs (from azd outputs → app settings / env)
- `AZURE_AI_PROJECT_ENDPOINT` — Foundry project to run under.
- `AZURE_OPENAI_CHAT_DEPLOYMENT` — chat model for reasoning/routing.
- `ORCHESTRATOR_REASONING_EFFORT` — gpt-5.x reasoning effort for the
  orchestrator's own two per-turn passes (decide-tool + relay). Defaults to
  `low` (the #1 latency lever — the reasoning "thinking" time is ~73–81% of each
  turn). Injected as a non-reserved container env var so it is retunable via
  `azd env set ORCHESTRATOR_REASONING_EFFORT=<none|minimal|low|medium|high>`
  (then re-register) without a container rebuild. Empty/`default` omits the
  override (model default effort). Never set temperature/max_tokens — reasoning
  models reject them.
- `AZURE_CLIENT_ID` — managed identity for auth (DefaultAzureCredential).
- The triage + incident agent IDs (set by the postprovision hook).

## Deployment
Not an `azd` host. The `postprovision` hook packages this code and registers it
as a Foundry hosted agent, writing its agent ID back to the azd environment
(`AZURE_AI_ORCHESTRATOR_AGENT_ID`).

## Boundary
Routing + handoff only. KB grounding logic lives in `src/helpdesk/agents`. ServiceNow
call details live in `src/servicenow`.
