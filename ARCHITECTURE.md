# Architecture — ServiceNow IT Helpdesk AI Agent (Azure Solution Accelerator)

> **Source of truth.** The team codes against this document. Cross-component
> contracts (Bicep outputs, azd inputs, module signatures) change here first,
> with Morpheus (Lead) approval.

## 1. What we're building

A one-click (`azd up`) Azure Solution Accelerator for a **ServiceNow ticketing AI
agent**. End users chat with a custom UI; an **Orchestrator** agent routes their
request through specialist agents that (1) try to resolve it from a knowledge
base, and (2) if unresolved, create/assign/check/update a ServiceNow incident.

**Language:** Python everywhere. **Tests:** pytest. **IaC:** Bicep via `azd`.
**ServiceNow instance:** `https://dev283128.service-now.com`.

## 2. Component diagram

```mermaid
flowchart TD
    User([End User]) -->|chat| UI[UI - Azure App Service<br/>src/ui]
    UI -->|invoke| ORCH[Orchestrator Agent<br/>Foundry Hosted Agent<br/>src/orchestrator]

    subgraph Foundry[Azure AI Foundry Project]
        ORCH -->|handoff| TRIAGE[Triage Agent<br/>src/agents]
        ORCH -->|handoff| INC[Incident Agent<br/>src/agents]
    end

    TRIAGE -->|grounded search| SEARCH[(Azure AI Search<br/>index: it-helpdesk-kb)]
    SEARCH -. indexed from .- STG[(Azure Storage<br/>container: kb)]
    INC -->|MCP tools| APIM[API Management - Developer tier<br/>MCP endpoint<br/>infra: apim.bicep]
    APIM -->|Table API + Basic auth| SNOW[(ServiceNow Instance<br/>dev283128)]
    APIM -.reads creds.-> KV[(Key Vault)]

    ORCH -.identity.-> MI[User-Assigned Managed Identity]
    UI -.identity.-> MI
    MI -.RBAC.-> SEARCH & STG & KV & Foundry

    subgraph Obs[Observability]
        AI[Application Insights + Log Analytics]
    end
    UI & Foundry & APIM -.telemetry.-> AI
```

All resources live in **one resource group** (`rg-<environmentName>`), named with
a shared **resource token** (see §5).

## 3. Data flow — the 4 user capabilities

The Orchestrator always receives the request first and routes. Validation
prompts (from `assets/Sample-Prompts.txt`) are noted per capability.

### 3.1 Triage & resolve (KB)
1. UI → Orchestrator with the user's problem statement.
2. Orchestrator → **Triage agent**.
3. Triage agent queries **Azure AI Search** (`it-helpdesk-kb`) grounded on the KB
   docs, returns resolution steps (with citations) if found.
4. If resolved → response flows back UI ← Orchestrator. **Stop.**

### 3.2 Create & assign incident (escalation)
_Prompt: "Unable to log into Epic. Create a new incident."_
1. Triage agent finds no resolution (or user asks to escalate) and extracts the
   **Recommended Assignment Group** from the matching KB doc.
2. Orchestrator → **Incident agent**.
3. Incident agent calls the **APIM MCP endpoint** → `POST /api/now/table/incident`
   with `short_description`, `description`, `assignment_group`, urgency/impact.
4. Returns the new incident number to the user.

### 3.3 Check ticket status
_Prompt: "lookup details for incident INC0000057"_
1. Orchestrator → Incident agent.
2. Incident agent calls APIM MCP → `GET /api/now/table/incident?sysparm_query=number=INC0000057`.
3. Returns state, assignment, notes.

### 3.4 Update ticket
_Prompt: "update urgency for INC0010027 to low"_
1. Orchestrator → Incident agent.
2. Resolve number → `sys_id` (GET), then `PATCH /api/now/table/incident/{sys_id}`
   with `urgency = 3` (low). ServiceNow enum mapping lives in `src/servicenow`.
3. Confirms the update.

## 4. azd input / naming contract

`azd up` prompts for **exactly** these (nothing more):

| Input | Source | azd env var | Notes |
|-------|--------|-------------|-------|
| Azure login | `az login` / azd | — | Handled by azd. |
| Subscription | azd built-in prompt | `AZURE_SUBSCRIPTION_ID` | |
| Region | azd built-in prompt | `AZURE_LOCATION` | Bicep `location`. |
| ServiceNow instance URL | `scripts/preprovision` | `SERVICENOW_INSTANCE_URL` | Default `https://dev283128.service-now.com`. |
| ServiceNow username | `scripts/preprovision` | `SERVICENOW_USERNAME` | → Key Vault secret. |
| ServiceNow password | `scripts/preprovision` | `SERVICENOW_PASSWORD` | **Secure.** → Key Vault secret; never output. |

Flow: `preprovision` hook → `azd env set …` → `infra/main.parameters.json`
(`${VAR}` substitution) → Bicep params → `keyvault.bicep` stores creds → APIM
named values reference the Key Vault secrets → managed identity reads them.
**No secret ever appears in source, outputs, or app settings in plaintext.**

## 5. Resource token & naming convention

```
resourceToken = uniqueString(subscription().id, environmentName, location)
```

Every resource: `<abbreviation><resourceToken>` (see `infra/abbreviations.json`),
except the resource group which is human-readable `rg-<environmentName>`.

| Resource | Pattern (example) |
|----------|-------------------|
| Resource group | `rg-<env>` |
| Managed identity | `id-<token>` |
| Key Vault | `kv<token>` |
| Storage | `st<token>` |
| AI Search | `srch-<token>` |
| AI Foundry account | `aif-<token>` |
| Foundry project | `proj-<token>` |
| API Management | `apim-<token>` |
| App Service plan | `plan-<token>` |
| Web App (UI) | `app-<token>` |
| Log Analytics | `log-<token>` |
| App Insights | `appi-<token>` |

## 6. Bicep modules & owners

`infra/main.bicep` is subscription-scoped, creates the single RG, and wires all
modules. It is **fully authored**; module bodies are **stubs** with locked
param/output signatures.

| Module | Owner | Responsibility |
|--------|-------|----------------|
| `main.bicep` | Tank (Morpheus locked) | RG, naming, wiring, outputs contract |
| `modules/monitoring.bicep` | Tank | Log Analytics + App Insights |
| `modules/identity.bicep` | Tank | User-assigned MI (all components run as) |
| `modules/keyvault.bicep` | Tank | Key Vault + ServiceNow secrets + RBAC |
| `modules/storage.bicep` | Tank | KB blob container + Blob Data roles |
| `modules/search.bicep` | Tank | AI Search service + data-plane roles |
| `modules/foundry.bicep` | Tank | Foundry account + project + model deployments |
| `modules/apim.bicep` | **Switch** (config) / Tank (resource) | Developer-tier APIM, OpenAPI import, **MCP endpoint** |
| `modules/appservice.bicep` | Tank | Plan + Web App (`azd-service-name: ui`) |

## 7. Bicep outputs contract (consumed by app + agents + hooks)

| Output | Consumed by |
|--------|-------------|
| `AZURE_RESOURCE_GROUP`, `AZURE_LOCATION`, `AZURE_RESOURCE_TOKEN` | azd / diagnostics |
| `AZURE_MANAGED_IDENTITY_CLIENT_ID` / `_PRINCIPAL_ID` / `_RESOURCE_ID` / `_NAME` | UI, orchestrator, agents (auth) |
| `AZURE_KEY_VAULT_NAME`, `AZURE_KEY_VAULT_ENDPOINT` | UI, servicenow client |
| `SERVICENOW_USERNAME_SECRET_NAME`, `SERVICENOW_PASSWORD_SECRET_NAME` | APIM named values, servicenow client |
| `AZURE_AI_PROJECT_ENDPOINT`, `AZURE_AI_PROJECT_NAME`, `AZURE_AI_FOUNDRY_NAME` | Orchestrator + agents + postprovision |
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_CHAT_DEPLOYMENT`, `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | Agents + indexing |
| `AZURE_STORAGE_ACCOUNT_NAME`, `AZURE_STORAGE_BLOB_ENDPOINT`, `AZURE_STORAGE_KB_CONTAINER` | postprovision (KB upload) |
| `AZURE_SEARCH_SERVICE_NAME`, `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_INDEX_NAME` | Triage agent + indexing |
| `AZURE_APIM_NAME`, `AZURE_APIM_GATEWAY_URL`, `SERVICENOW_MCP_ENDPOINT` | Incident agent (`src/servicenow`) |
| `SERVICENOW_INSTANCE_URL` | servicenow client |
| `AZURE_APP_SERVICE_NAME`, `SERVICE_UI_URI` | azd deploy / user |
| `APPLICATIONINSIGHTS_CONNECTION_STRING`, `AZURE_LOG_ANALYTICS_WORKSPACE_ID` | All components (telemetry) |

## 8. Cross-component interfaces (who needs what)

- **Trinity (orchestrator + agents)** needs: `AZURE_AI_PROJECT_ENDPOINT`,
  `AZURE_OPENAI_CHAT_DEPLOYMENT`, `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`,
  `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_INDEX_NAME`, `SERVICENOW_MCP_ENDPOINT`,
  `AZURE_MANAGED_IDENTITY_CLIENT_ID`.
- **Switch (ServiceNow/APIM)** owns: OpenAPI import + MCP exposure in
  `apim.bicep`, and the MCP client + ServiceNow field mapping in
  `src/servicenow`. Produces/consumes `SERVICENOW_MCP_ENDPOINT`.
- **UI** needs: `AZURE_AI_PROJECT_ENDPOINT`, `AZURE_MANAGED_IDENTITY_CLIENT_ID`,
  `APPLICATIONINSIGHTS_CONNECTION_STRING` (Bicep sets these as app settings).
- **Dozer (tests/docs)** validates the outputs contract, the 4 data flows, and a
  fresh-clone `azd up`.

## 9. Build sequence

1. **Tank** implements Bicep modules bottom-up (monitoring → identity → keyvault
   → storage → search → foundry → apim → appservice); gets `azd provision` green.
2. **Switch** implements APIM OpenAPI import + MCP exposure + `src/servicenow`
   client in parallel (mock the endpoint until APIM exists).
3. **Trinity** implements `src/orchestrator`, `src/agents`, `src/ui`, and the
   `postprovision` agent-creation + index-build steps, coding against the outputs
   contract (mock env vars until infra is live).
4. **Dozer** writes pytest suites from the 4 data flows + sample prompts and a
   fresh-clone deploy validation, in parallel.
5. **Morpheus** reviews at the boundaries and gates merge.

All four can start immediately — the contracts in §4–§8 are the seams.
