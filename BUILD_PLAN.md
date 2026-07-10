# Build Plan — parallel assignments & seams

> Companion to `ARCHITECTURE.md`. Names every file/module's owner and the
> interfaces between them so Tank, Trinity, Switch, and Dozer build in parallel
> without colliding.

## Ownership map

| Path | Owner | What to build |
|------|-------|---------------|
| `infra/main.bicep` | Morpheus (locked) / Tank (maintains) | RG, naming, wiring, outputs — **already authored** |
| `infra/main.parameters.json`, `abbreviations.json` | Tank | keep in sync with params |
| `infra/modules/monitoring.bicep` | Tank | Log Analytics + App Insights |
| `infra/modules/identity.bicep` | Tank | user-assigned MI |
| `infra/modules/keyvault.bicep` | Tank | Key Vault + ServiceNow secrets + RBAC |
| `infra/modules/storage.bicep` | Tank | KB container + blob roles |
| `infra/modules/search.bicep` | Tank | AI Search + data-plane roles |
| `infra/modules/foundry.bicep` | Tank | Foundry account/project + model deployments |
| `infra/modules/apim.bicep` | **Switch** (API/MCP config) + Tank (resource) | Developer-tier APIM, OpenAPI import, MCP endpoint |
| `infra/modules/appservice.bicep` | Tank | shared plan + two Web Apps (`azd-service-name: api` Python AG-UI backend, `azd-service-name: ui` Node/Next.js frontend) |
| `src/helpdesk/orchestrator/**` | Trinity | Agent Framework hosted orchestrator |
| `src/helpdesk/agents/**` | Trinity | triage + incident agent defs |
| `src/helpdesk/ui/**` | Trinity | FastAPI AG-UI backend (`/agui`) |
| `frontend/**` | Switch | Next.js + CopilotKit customer UI |
| `src/servicenow/**` | Switch | MCP client + ServiceNow field mapping |
| `src/shared/**` | shared (Morpheus reviews) | config/auth/logging |
| `scripts/preprovision.*` | Tank | ServiceNow input capture |
| `scripts/postprovision.*` + `postprovision.py` | Tank (KB upload) + Trinity (index + agents) | post-deploy wiring |
| `tests/**` | Dozer | pytest suites + fresh-clone validation |

## Interfaces (the seams)

**Bicep → everything:** the outputs in `main.bicep` (ARCHITECTURE.md §7) are the
only sanctioned way components learn endpoints/names. No hard-coded endpoints.

**Switch → Trinity:** `SERVICENOW_MCP_ENDPOINT` (APIM MCP URL) + the tool names
exposed there. Trinity's incident agent consumes these; Switch guarantees the
tool contract (create/read/update incident).

**Tank → Trinity:** `AZURE_AI_PROJECT_ENDPOINT`, `AZURE_OPENAI_*` deployments,
`AZURE_SEARCH_ENDPOINT` + `AZURE_SEARCH_INDEX_NAME`, `AZURE_MANAGED_IDENTITY_CLIENT_ID`.

**Tank → UI:** app settings on the Web App (ARCHITECTURE.md §7/§8).

## Parallel start (all can begin now)

- **Tank:** implement modules bottom-up; target green `azd provision`.
- **Switch:** APIM import + MCP exposure + `src/servicenow` (mock endpoint until APIM up).
- **Trinity:** orchestrator/agents/ui + postprovision steps against mocked env vars.
- **Dozer:** pytest from the 4 data flows + sample prompts + fresh-clone `azd up`.

## Definition of done (accelerator)

1. `azd up` from a fresh clone provisions everything into one RG with only the
   documented prompts.
2. The 3 sample prompts work end-to-end against the ServiceNow dev instance.
3. Triage resolves KB-answerable questions without creating a ticket.
4. No secrets in source, outputs, or plaintext app settings.
