# Squad Decisions

## Active Decisions

### 2026-07-08T16-19-30: Full architecture locked: azd one-click, APIM(Dev)+MCP, 3 Foundry agents, App Service UI, Python, single RG
**By:** coordinator
**What:** Full architecture locked: azd one-click, APIM(Dev)+MCP, 3 Foundry agents, App Service UI, Python, single RG
**References:** Morpheus, Tank, Trinity, Switch, Dozer
**Why:** ### 2026-07-08T11:18:19-05:00: Locked architecture for the ServiceNow ticketing AI agent solution accelerator
**By:** abKrazy (via Copilot)

**Deployment:** One-click `azd up`. Prompts only for minimum inputs: Azure login, subscription, region, and ServiceNow credentials (username/password or OAuth). Auto-generate a consistent resource token; all resources in ONE resource group.

**ServiceNow API surface:** `assets/ServiceNow-OpenAPI-spec.json` is the standard Table API (GET/POST/PUT/PATCH/DELETE on `/api/now/table/{tableName}` and `/{sys_id}`). Instance: https://dev283128.service-now.com. This spec is imported into Azure API Management (Developer tier) and exposed as an MCP server endpoint.

**Microsoft Foundry — 3 agents:**
1. Orchestrator agent — Python, built with Microsoft Agent Framework, deployed as a Hosted Agent in Foundry. Fronts the UI; hands off to sub-agents.
2. Ticket triage agent — grounded in KB docs (assets/kb/*.md) stored in Azure Storage and indexed into Azure AI Search.
3. Incident creation agent — calls the APIM MCP endpoint to create/assign/update/check incidents.

**UI:** Custom web UI on Azure App Service; end users talk to the Orchestrator.

**Language:** Python (all app + agent code). Tests via pytest.

**Assets to wire in:** OpenAPI spec → APIM; KB docs → Storage → AI Search index; sample prompts (lookup INC0000057, create incident, update urgency INC0010027) → validation harness.

**README:** Must list ALL prereqs for customer-facing hackathons — required RBAC roles for APIM + Foundry + resource creation, Foundry model-deployment quota, azd/az CLI versions, ServiceNow dev instance setup. Super clear, step-by-step.

**Validation:** Static + local validation done by the team (bicep build/lint, azd config validation, python lint + unit/integration tests with ServiceNow & Foundry mocked, sample-prompt harness). Live `azd up` against a real subscription is the user's manual step (requires their tenant + cost).

### 2026-07-08T16-00-30: Python is the implementation language for the Orchestrator and application code
**By:** coordinator
**What:** Python is the implementation language for the Orchestrator and application code
**References:** Morpheus, Tank, Trinity, Switch, Dozer
**Why:** ### 2026-07-08T11:00:05-05:00: Language decision
**By:** abKrazy (via Copilot)
**What:** The custom Orchestrator agent and all application/tooling code for the ServiceNow ticketing solution accelerator will be written in Python.
**Why:** User confirmed Python when asked at team setup. Aligns with Azure AI Foundry Python SDK and typical solution-accelerator conventions.
**Implications:** Tank provisions hosting compatible with a Python app; Trinity builds the Orchestrator and Foundry agent wiring in Python; Switch writes the ServiceNow REST client in Python; Dozer's tests use a Python test framework (e.g., pytest).

### 2026-07-08T11:18:19-05:00: deploy shape — UI is the only azd service; Orchestrator is a Foundry hosted agent
**By:** Morpheus (Lead) — 2026-07-08
**Status:** Adopted (scaffold locked)

## Decision
`azure.yaml` declares exactly one `service`: **`ui`** (host: `appservice`). The
**Orchestrator** is deployed as a **Foundry Hosted Agent**, created by the
`postprovision` hook (`scripts/postprovision.py`), not by `azd deploy`. The
triage + incident agents are also created in postprovision. Agent IDs are
written back to the azd environment.

## Why
Foundry hosted agents are created via the Foundry control plane/SDK, not azd
hosts. Keeping only the UI as an azd service keeps the deploy contract crisp and
avoids a fake host for the orchestrator.

## Implications
- Trinity implements the postprovision agent-creation steps (idempotent).
- `infra/main.bicep` is fully authored; `infra/modules/*.bicep` are stubs with
  **locked param/output signatures** — changing a signature needs Morpheus sign-off.

### 2026-07-08T11:18:19-05:00: ServiceNow secrets flow (Key Vault, no plaintext leaks)
**By:** Morpheus (Lead) — 2026-07-08
**Status:** Adopted (scaffold locked)

## Decision
ServiceNow credentials are collected by the `preprovision` hook, stored **only**
in **Key Vault** (`keyvault.bicep`), and consumed by **APIM named values** that
reference those secrets. The runtime managed identity reads Key Vault. The
`serviceNowPassword` Bicep param is `@secure()` and is **never** emitted as an
output. Only secret *names* (not values) appear in outputs/app settings.

## Why
No secrets in source, Bicep outputs, or plaintext app settings — a hard
constraint. APIM injects Basic auth to ServiceNow at the gateway so agents never
handle raw creds.

## Implications
- Switch's `apim.bicep` wires named values → Key Vault secrets and an inbound
  Basic-auth policy.
- App settings reference secrets via `@Microsoft.KeyVault(...)` if ever needed.

### 2026-07-08T11:18:19-05:00: single resource group + resource-token naming
**By:** Morpheus (Lead) — 2026-07-08
**Status:** Adopted (scaffold locked)

## Decision
All Azure resources deploy into **one resource group** `rg-<environmentName>`.
Every resource is named `<abbreviation><resourceToken>` where
`resourceToken = uniqueString(subscription().id, environmentName, location)`.
Abbreviations live in `infra/abbreviations.json`.

## Why
Hackathon adopters need `azd up` to produce a clean, self-contained, easy-to-
delete footprint. A stable token keeps names globally unique yet deterministic
across re-deploys.

## Implications
- `infra/main.bicep` is subscription-scoped and creates the RG; all modules
  deploy into it.
- Tank must not create secondary resource groups.

### 2026-07-08T11:18:19-05:00: ServiceNow live MCP client (contract, auth, field mapping)
**By:** Switch (Backend / Integration Engineer) — 2026-07-08
**Status:** Implemented (`src/servicenow/**`), validated with a fake MCP transport
**Scope:** `src/servicenow/**` (and the already-shipped `infra/modules/apim.bicep`)

## Decision

`src/servicenow` now ships a **live** MCP client, not just a README. It talks to
the APIM MCP endpoint (`SERVICENOW_MCP_ENDPOINT` = `{gateway}/servicenow/mcp`,
streamable-HTTP) using the `mcp` package (v1.26.0) and implements Trinity's
`ServiceNowClient` protocol.

### Import path (the Switch ↔ Trinity seam)
`get_servicenow_client()` does `from servicenow import build_client`. We expose
`build_client(mcp_endpoint) -> MCPServiceNowClient` at the **top-level
`servicenow` package** (`src/servicenow/__init__.py`), which is importable as
`servicenow` via `pyproject`'s `package-dir = {"" = "src"}`. Verified:
`import servicenow; servicenow.build_client(...)` works.

- **Type identity:** we do NOT redefine `Incident`/`IncidentNotFound`. The client
  loads them from Trinity's contract module at import time via a layout-agnostic
  resolver (`_load_contract`): (1) scan `sys.modules` for the module exposing
  `get_servicenow_client`+`Incident` (guarantees identity with the caller),
  (2) try `agents.servicenow_client` / `src.agents.servicenow_client` /
  `helpdesk.agents.servicenow_client`, (3) fall back to a direct file load of
  `../agents/servicenow_client.py`. **If Trinity finalizes a single-package layout
  (e.g. `helpdesk`), no change is needed here** — the resolver already covers it,
  and `build_client` stays reachable as long as the package that re-exports it is
  importable as `servicenow`. If Trinity renames the top-level package, update
  `get_servicenow_client`'s `from servicenow import build_client` accordingly (her
  file) — our module just needs to remain importable under that name.

### MCP tool contract (discovery, not hard-coded names)
`assets/ServiceNow-OpenAPI-spec.json` has **no `operationId`s**, so APIM
auto-generates tool names. The client therefore calls `list_tools()` and
**classifies** each tool into 4 logical ops by input schema:
- **create** — no `sys_id`, has body fields (`short_description`, `urgency`, …)
- **query** — no `sys_id`, has `sysparm_query`
- **get** — has `sys_id`, no body
- **update** — has `sys_id` + body (prefers a `patch-*` tool over `put-*`)

Names can be pinned via env: `SERVICENOW_MCP_TOOL_{CREATE,QUERY,GET,UPDATE}`.
Request body is nested under `body`/`requestBody`/`payload` if the tool schema
declares it, otherwise flattened alongside `tableName` (both APIM shapes handled).

### Auth to APIM
The MCP API is imported with `subscriptionRequired: false` (see `apim.bicep`), and
the gateway injects ServiceNow **Basic auth** from Key Vault-backed named values
(Morpheus' secrets decision). So **the client sends no ServiceNow credentials**.
For hardened deployments it optionally adds headers from env:
- `SERVICENOW_MCP_SUBSCRIPTION_KEY` → `Ocp-Apim-Subscription-Key`
- `SERVICENOW_MCP_ACCESS_TOKEN` → `Authorization: Bearer …`

### Field / enum mapping (authoritative — `servicenow/mapping.py`)
- urgency/impact: `low/medium/high ↔ 3/2/1` (accepts labels or codes)
- state: `new=1, in progress=2, on hold=3, resolved=6, closed=7, canceled=8`
- flows: create → POST `incident`; get → GET `?sysparm_query=number=…`;
  update → GET (resolve number→`sys_id`) then PATCH `incident/{sys_id}`
  (ARCHITECTURE.md §3.2–3.4).

### Resilience
Sync protocol over async MCP transport (worker-thread bridge when already inside a
running loop). Bounded exponential-backoff retries for `ServiceNowUnreachable`
(connect/timeout); `ServiceNowAuthError` (401/403) and `IncidentNotFound` are
terminal (no retry). Distinct exception types let the Orchestrator tell
"not found" vs "unreachable" vs "auth failed" apart.

## Validation
- `pip install -e .[servicenow,dev]` resolves; `mcp==1.26.0`, `httpx==0.28.1`
  (`pyproject` `mcp>=1.0` name/spec correct — no change needed).
- `python -m py_compile` clean; `ruff check src/servicenow tests/…` clean.
- `tests/test_servicenow_client.py` — **9 passed** against a fake MCP transport,
  covering the 3 sample prompts (create "Unable to log into Epic", get INC0000057,
  update INC0010027 urgency→low), field/enum mapping, PATCH-over-PUT preference,
  not-found, retry-on-transient, and no-retry-on-auth.

## Implications / asks
- **Trinity:** if you rename the top-level package, keep `build_client`
  re-exported as `servicenow` (or tell me the new name). No other change needed —
  contract types are loaded dynamically.
- **Dozer:** `tests/test_smoke.py` currently fails to *collect* because
  `orchestrator` isn't importable yet — unrelated to `src/servicenow` (green in
  isolation).

### 2026-07-08T11:18:19-05:00: Python package layout = single `helpdesk` umbrella + ServiceNow import contract
**Author:** Trinity (AI / Agent Engineer)
**Date:** 2026-07-08
**Affects:** Switch (src/servicenow), Tank (scripts/postprovision.py), Dozer (tests), anyone importing our Python code.

## Decision

All first-party Python code lives under a **single umbrella package `helpdesk`**:

```
src/helpdesk/__init__.py
src/helpdesk/shared/        (config, credential)
src/helpdesk/agents/        (triage, incident, kb, search_client, servicenow_client, embeddings, setup, prompts)
src/helpdesk/orchestrator/  (Orchestrator router)
src/helpdesk/ui/            (FastAPI app + templates)
```

`pyproject.toml`:
```toml
[tool.setuptools]
package-dir = { "" = "src" }
[tool.setuptools.packages.find]
where = ["src"]
[tool.setuptools.package-data]
"helpdesk.ui" = ["templates/*.html"]
```

Import rules:
- **Cross-package imports use relative imports within `helpdesk`** (e.g. `from ..shared import get_settings`, `from ..agents.incident import IncidentAgent`).
- External absolute imports use the full path: `from helpdesk.orchestrator import Orchestrator`.

### Why NOT flat top-level packages (`agents`, `shared`, `ui`, …)
The original `package-dir = {""="src"}` made `agents`, `shared`, etc. **separate
top-level packages**, so `from ..shared import …` (no common parent) was broken.
Flipping everything to flat absolute imports (`from shared import …`) also fails
in practice: the name **`agents` collides with the installed OpenAI Agents SDK**
(`site-packages/agents/`), so `import agents.incident` resolves to the wrong
package. The `helpdesk` umbrella eliminates all collisions and makes the existing
relative imports correct.

## ACTION REQUIRED — Switch (ServiceNow / APIM MCP client)

The incident agent depends on a typed `ServiceNowClient` protocol
(`helpdesk/agents/servicenow_client.py`). In **live** mode
(`SERVICENOW_MCP_ENDPOINT` set, `HELPDESK_MOCK` unset) the factory
`get_servicenow_client()` imports your client via, in order:

1. `from helpdesk.servicenow import build_client`   ← **preferred**
2. `from servicenow import build_client`             ← fallback (top-level)

**Contract your module must expose:**

```python
def build_client(mcp_endpoint: str) -> ServiceNowClient: ...
```

where the returned object implements:

```python
class ServiceNowClient(Protocol):
    def create_incident(self, short_description: str, description: str = "",
                        assignment_group: str = "", urgency: str = "3") -> Incident: ...
    def get_incident(self, number: str) -> Incident: ...
    def update_incident(self, number: str, fields: dict[str, str]) -> Incident: ...
```

`Incident` is the dataclass in `helpdesk/agents/servicenow_client.py` (fields:
`number, sys_id, short_description, description, assignment_group, urgency, state,
fields`). Urgency enum: low=3, medium=2, high=1 (authoritative mapping is yours to
own in `src/servicenow`).

**Recommended:** move your client to `src/helpdesk/servicenow/` (with an
`__init__.py` exposing `build_client`) so it ships as `helpdesk.servicenow`. If
you keep it at top-level `src/servicenow`, add an `__init__.py` exposing
`build_client` — the fallback import will find it. Either works; the umbrella path
is preferred for consistency. I did **not** edit `src/servicenow/**`.

## ACTION for Tank (already applied by Trinity)

`scripts/postprovision.py` now imports `from helpdesk.agents.setup import
build_search_index, create_foundry_agents` and `from helpdesk.shared import
get_credential`, and adds `src/` to `sys.path` so it runs from a fresh checkout.
It is idempotent and honours `HELPDESK_MOCK=1` (no-ops every live step).

## Mock mode (for Dozer + CI)

`HELPDESK_MOCK=1` makes the whole stack run with **no live Azure**: triage uses
the local KB search, incident uses the in-memory `MockServiceNowClient` seeded
with `INC0000057` and `INC0010027`. `tests/test_smoke.py` drives the 3 sample
prompts through `helpdesk.orchestrator.Orchestrator` and asserts routing + results.

## Governance

- All meaningful changes require team consensus
- Document architectural decisions here
- Keep history focused on work, decisions focused on direction

### 2026-07-08T16:33:22-05:00: APIM MCP-from-REST server fixed — bare `type:'mcp'` API + child `tools` (prior bicep silently produced NO MCP server)
**By:** Switch (APIM / MCP / deploy)
**Status:** Verified live on `apim-4c3eanpernjki` (Developer tier, eastus) and baked into `infra/modules/apim.bicep`

## WHAT
The deployed UI returned HTTP 500 on incident-status because the ServiceNow MCP
client hit a 404 — the APIM `servicenow-mcp` endpoint was **not actually an MCP
server**. Root cause: the old `apim.bicep` created the MCP API with
`type:'mcp'` **plus** `apiType:'mcp'`, `sourceApiId`, and `mcpProperties`. The
APIM control plane (even at api-version `2025-09-01-preview`) **silently drops
`type`/`apiType`/`sourceApiId`** when those extra fields are sent together,
leaving a plain HTTP API with orphaned `mcpProperties` and no `/mcp` endpoint.
`az bicep build` passed and ARM returned 200, so the failure was invisible until runtime.

### Correct, verified-working shape (api-version `2025-09-01-preview`)
1. **Bare MCP API** — `Microsoft.ApiManagement/service/apis` with `type:'mcp'`
   ONLY (plus `displayName`, `path`, `protocols:['https']`, `subscriptionRequired`).
   **No** `sourceApiId`, **no** `apiType`, **no** `mcpProperties`. (When sent
   alone, `type:'mcp'` sticks; when sent with the extras, it is dropped.)
2. **Tools** — one `Microsoft.ApiManagement/service/apis/tools` child per source
   operation, `operationId` = FULL ARM resource ID of the source REST operation
   (`{restApi.id}/operations/{operationName}`).
3. **Auth** — the MCP server needs no auth policy; when a tool routes to its
   source operation, it inherits that REST API's inbound Basic-auth policy
   (Key Vault-backed `servicenow-username`/`servicenow-password`). Confirmed: a
   `queryTable` tool call reached ServiceNow and returned a real incident.

Note: `mcpProperties.endpoints` is documented as an array in bicep-types, but the
live control plane deserializes it as a dictionary — it is irrelevant here because
REST-backed MCP servers omit `mcpProperties` entirely.

## WHY
Developer tier in eastus **does** support APIM MCP servers (Learn: "Expose REST
API as MCP server" lists Developer). The feature just requires the bare-API +
tools pattern, not the passthrough `mcpProperties`/`sourceApiId` shape. This is a
one-click hackathon accelerator, so the working shape is now in bicep so a fresh
`azd up` reproduces it.

## VERIFIED ENDPOINT + TOOLS (live proof)
- **Endpoint:** `https://apim-4c3eanpernjki.azure-api.net/servicenow/mcp` (streamable HTTP)
- `initialize` -> HTTP 200, JSON-RPC result, serverInfo `Azure API Management`.
- `tools/list` -> 6 tools: **createIncident, queryTable, getRecord, patchRecord, updateRecord, deleteRecord** (full input schemas incl. `TableRecord` body).
- `tools/call queryTable {tableName:incident, sysparm_limit:1}` -> returned `INC0000060` from ServiceNow (Basic auth inherited end-to-end).

## FILES CHANGED
- `infra/modules/apim.bicep` — `mcpApi` reduced to bare `type:'mcp'`; added
  `mcpTools` var + `mcpToolResources` loop (6 `apis/tools` children).
- `SERVICENOW_MCP_ENDPOINT` / `mcpEndpointUrl` **unchanged** — still
  `{gateway}/servicenow/mcp`, which is the verified working URL.
- `az bicep build infra/main.bicep` -> exit 0 (only benign BCP081 preview-type
  warnings + a pre-existing unrelated output-secret-name lint).

## Reference
- Learn: https://learn.microsoft.com/en-us/azure/api-management/export-rest-mcp-server
- Learn (programmatic REST/Bicep/ARM): https://learn.microsoft.com/en-us/azure/api-management/manage-mcp-servers-rest-api
- Example: `azure-rest-api-specs .../2025-09-01-preview/examples/ApiManagementCreateApiTool.json`

## ASK
Coordinator: redeploy with `azd provision` (do NOT need full `azd up`), then
re-verify the app's incident-status path. Live APIM is already left in the
working state, so the app should work immediately even before re-provision.

### 2026-07-08T20-01-37: Fixed APIM MCP mcpProperties.endpoints array->object (McpEndpointContract dictionary)
**By:** switch
**What:** Fixed APIM MCP mcpProperties.endpoints array->object (McpEndpointContract dictionary)
**References:** infra/modules/apim.bicep
**Why:** Live APIM control-plane validation for Microsoft.ApiManagement/service/apis@2025-09-01-preview rejected mcpProperties.endpoints when sent as a JSON array because the backend deserializes it as Dictionary<String, McpEndpointContract>. Bicep build cannot catch this because the preview resource type has no local type metadata (BCP081), so Bicep passes the shape through and the runtime control plane enforces the contract. Updated infra/modules/apim.bicep to keep transportType: 'streamable' and express endpoints as an object keyed by the endpoint name: endpoints: { mcp: { uriTemplate: '/mcp' } }. This preserves the existing MCP path contract: api path remains servicenow, endpoint path remains /mcp, and the locked mcpEndpointUrl output remains ${apim.properties.gatewayUrl}/servicenow/mcp.

### 2026-07-08: Recognize ServiceNow APIM MCP TableRecord request bodies
**By:** Switch
**What:** The ServiceNow MCP client now treats `TableRecord` as a write-body container alongside `body`, `requestBody`, and `payload`.
**Why:** The live APIM MCP-from-REST server generated from the ServiceNow Table API OpenAPI spec exposes write tool schemas with request bodies under `TableRecord`. Without that container, `createIncident` was not classified as create, `patchRecord`/`updateRecord` were misclassified as get, and create/update calls flattened record fields instead of nesting them under `TableRecord`.

**Verification:** Full pytest suite passed: `55 passed`. Live APIM MCP + ServiceNow dev instance e2e passed on 2026-07-08: created `INC0010031` (`cc8af854838247581611b2b6feaad392`), updated urgency to `3`, and fetched the same incident back with urgency `3`.

### 2026-07-08T16:51:07-05:00: Serialize apis/tools deployment with @batchSize(1) to kill the parent-API ETag race
**By:** Switch (APIM / MCP / deploy) — 2026-07-08
**Status:** Implemented (`infra/modules/apim.bicep`), live state restored + convergence proven
**Scope:** `infra/modules/apim.bicep` (`mcpToolResources` loop)

## What
Applied `@batchSize(1)` to the `mcpToolResources` for-loop in `apim.bicep` so the
six `Microsoft.ApiManagement/service/apis/tools` children deploy **serially**
(one at a time) instead of in parallel.

All 6 tools mutate the **same parent MCP API** (`servicenow-mcp`). Deploying them
in parallel made concurrent writers race on the parent API's ETag, producing:
`PreconditionFailed: Resource was modified since last retrieval.` (5 conflicts,
then 3 on retry) during `azd provision`. The partial failure **corrupted the live
tool set**, leaving only 3 of 6 tools (`deleteRecord, getRecord, patchRecord`;
missing `createIncident, queryTable, updateRecord`). Because `queryTable` was
gone, the app's incident-status path failed live with
`ServiceNowToolError: MCP server exposes no tool for operation 'query'`.

## Why
`@batchSize(1)` is the idiomatic Bicep fix for concurrent child-resource
modification of a shared parent — it removes the ETag race entirely so both a
fresh `azd up` and re-provisions converge cleanly, deterministically producing
all 6 MCP tools. This is a customer-facing hackathon accelerator, so a fresh
`azd up` MUST reliably yield the full tool set.

## Validation
- `az bicep build infra/main.bicep` → exit 0 (only pre-existing BCP081/secret-name warnings).
- Restored the 3 missing tools on live APIM (`apim-4c3eanpernjki`) via `az rest`
  PUT (serial). Live `tools/list` → all **6** tools; `tools/call queryTable`
  {tableName:incident} → live incident **INC0010030** ("Mouse has stopped working").
- Convergence proof: a scoped group deployment re-applying all 6 tools with
  `@batchSize(1)` against the existing parent → `provisioningState=Succeeded`,
  exit 0, **zero** ETag conflicts (the exact op that previously threw 5). Ended
  with all 6 tools present. Did NOT run full `azd provision` (avoids re-racing;
  targeted scoped re-apply is the safer convergence check) and did NOT run
  `azd deploy` (coordinator redeploys app code).

## Files changed
- `infra/modules/apim.bicep` — `@batchSize(1)` decorator + explanatory comment on `mcpToolResources`.

### 2026-07-08T20-05-30: Added required ContainerName metadata to Foundry AzureBlob storage-connection
**By:** tank
**What:** Added required ContainerName metadata to Foundry AzureBlob storage-connection
**References:** infra/modules/foundry.bicep, infra/main.bicep
**Why:** AzureBlob Foundry project connections require the blob container name in connection metadata. Threaded the existing top-level kbContainerName value from infra/main.bicep into the foundry module, added a kbContainerName parameter to infra/modules/foundry.bicep, and set ContainerName in the storage-connection metadata while leaving search-connection and appinsights-connection untouched because their categories do not require ContainerName.

### 2026-07-08: Wire Azure OpenAI app settings into App Service
**By:** Tank
**What:** Added `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`, and `AZURE_OPENAI_CHAT_DEPLOYMENT` to the customer-facing App Service settings. The Bicep module now accepts the endpoint and deployment names, `main.bicep` wires them from Foundry/model parameters, and the live App Service `app-4c3eanpernjki` in `rg-ithelpdeskeast` was updated immediately.
**Why:** Triage/KB grounding embeds search queries and requires the Azure OpenAI endpoint plus embedding/chat deployment names. These values already existed in the azd environment and main outputs but were not passed into `infra/modules/appservice.bicep`, causing live triage prompts to fail with `AZURE_OPENAI_EMBEDDING_DEPLOYMENT is not configured.`

### 2026-07-08T19-45-30: Fixed invalid KB blob container name (kb→kbdocs, <3 char limit)
**By:** tank
**What:** Fixed invalid KB blob container name (kb→kbdocs, <3 char limit)
**References:** infra/main.bicep, infra/main.parameters.json
**Why:** Azure Blob container names must be 3-63 characters, lowercase letters/numbers/single hyphens, start/end with a letter or number, and avoid consecutive hyphens. The previous KB container default `kb` was only 2 characters, so `az bicep build` succeeded but the live deployment failed at runtime when Azure Storage enforced the container-name constraint. Changed the default to `kbdocs` consistently in `infra/main.bicep` and `infra/main.parameters.json`, and aligned Python fallbacks in `scripts/postprovision.py` and `src/helpdesk/shared/config.py` so the container name continues to flow from `AZURE_STORAGE_KB_CONTAINER` with a valid default.

### 2026-07-08T20-36-09: postprovision/preprovision hooks now propagate native (Python) non-zero exit codes so azd aborts on hook failure
**By:** tank
**What:** postprovision/preprovision hooks now propagate native (Python) non-zero exit codes so azd aborts on hook failure
**References:** scripts/postprovision.ps1, scripts/postprovision.sh
**Why:** PowerShell's $ErrorActionPreference='Stop' does not turn native command non-zero exit codes into terminating errors in the target runtime, so the postprovision Python worker could fail while the wrapper still returned success to azd. Added an explicit $LASTEXITCODE check after scripts/postprovision.ps1 invokes postprovision.py so failures write an error and exit with the Python code. Audited preprovision wrappers as well: scripts/preprovision.ps1 now wraps fail-critical native 'azd env set' calls with explicit $LASTEXITCODE propagation while preserving the existing Read-Host prompting behavior. POSIX wrappers already use set -e, so their Python/azd native command failures propagate without changes.

### 2026-07-08T15:40:16.9408368-05:00 — Use standalone Azure AgentsClient and pin beta SDKs

**By:** Trinity

## What

`create_foundry_agents()` now uses `azure.ai.agents.AgentsClient` directly for Foundry agent list/create/update operations with `azure-ai-agents==1.2.0b6` and `azure-ai-projects==2.3.0`. Both beta SDKs are pinned in the deploy-root `src/requirements.txt` and `pyproject.toml`.

## Why

In `azure-ai-projects` 2.x, `AIProjectClient(...).agents` no longer exposes `create_agent` or `list_agents`, which broke live `azd up` postprovision. The standalone `AgentsClient` in `azure-ai-agents==1.2.0b6` exposes `list_agents`, `create_agent`, and `update_agent`; exact pins prevent future beta API drift from breaking hackathon deploys at Oryx build time.

### 2026-07-08T20-37-07: Fixed embedding dimension mismatch (text-embedding-3-large 3072 vs index 1536) that zeroed the KB index and blocked agent creation
**By:** trinity
**What:** Fixed embedding dimension mismatch (text-embedding-3-large 3072 vs index 1536) that zeroed the KB index and blocked agent creation
**References:** src/helpdesk/agents/embeddings.py, src/helpdesk/agents/setup.py
**Why:** The live postprovision failure was caused by text-embedding-3-large returning its native 3072-dimension vectors while the Azure AI Search content_vector field was configured for 1536 dimensions. I moved the embedding dimension to a single shared constant in src/helpdesk/agents/embeddings.py, made embed_texts accept and pass the OpenAI dimensions parameter, and wired both indexing (src/helpdesk/agents/setup.py) and query-time vector search (src/helpdesk/agents/search_client.py) to use that same constant. I also added upload-result verification after merge_or_upload_documents so any future partial document upload failure raises with the first document error instead of silently continuing. Tests now cover the dimensions parameter, the shared index/query invariant, and loud upload failures.

### Foundry agents must use the NEW Foundry Agent experience (not classic assistants)

**Author:** Trinity (AI / Agent Engineer)
**Date:** 2026-07-08T16:08:27-05:00
**Affects:** `src/helpdesk/agents/setup.py`, `pyproject.toml`, `src/requirements.txt`,
`scripts/postprovision.py` (caller unchanged), anyone reading the agent-ID env vars.

## WHAT

`create_foundry_agents()` now creates the 3 agents (`it-helpdesk-triage`,
`it-helpdesk-incident`, `it-helpdesk-orchestrator`) through the **new Azure AI
Foundry Agent experience**:

```python
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition

with AIProjectClient(endpoint=project_endpoint, credential=get_credential()) as project:
    version = project.agents.create_version(
        agent_name=name,
        definition=PromptAgentDefinition(model=chat_deployment, instructions=instructions),
    )
    agent_id = version.name          # stable agent id (== AgentDetails.id)
```

- The new-experience agent **id == its name** (e.g. `it-helpdesk-triage`); no
  `asst_` prefix. `create_version` returns `AgentVersionDetails` (`.id="name:1"`,
  `.name`, `.version`). We persist the stable **name** into
  `AZURE_AI_{TRIAGE,INCIDENT,ORCHESTRATOR}_AGENT_ID` via the existing
  `_azd_env_set` helper.
- Idempotency: agents are **versioned** — re-running publishes a new version of the
  same named agent instead of duplicating. We `agents.list()` first only to log
  "already exists" vs "created".
- Dropped the now-unused `azure-ai-agents==1.2.0b6` pin from `pyproject.toml`
  (`orchestrator` + `agents` extras) and `src/requirements.txt`. The new path lives
  entirely in **`azure-ai-projects==2.3.0`** (unchanged pin — it is the current
  PyPI latest and already exposes `.agents`).
- Added `tests/test_foundry_agents_setup.py` (fakes the `azure.ai.projects` SDK,
  offline) asserting the new `create_version` call shape, no `asst_` IDs, azd
  persistence, and client close. Suite: **52 passed**, ruff clean.
- Cleanup: the 3 classic `asst_` agents created earlier
  (`asst_W63u5v61HTtjt10RsFb2qYWw`, `asst_ArzHGA0JLERaDicovU52DV7B`,
  `asst_rLmNyq7Nn4lRBF7UAeDG0fNi`) were **deleted** via
  `azure.ai.agents.AgentsClient.delete_agent(id)` during the live probe. Project
  now holds only new-experience agents.

## WHY

The previous code used `azure.ai.agents.AgentsClient(endpoint).create_agent(...)`,
which hits the legacy data-plane assistants API (`{endpoint}/assistants`,
`asst_`-prefixed IDs) = the **classic Foundry experience**. The user explicitly
required the agents to appear in the **new** Foundry portal experience.

Empirically confirmed against the live project
`https://aif-4c3eanpernjki.services.ai.azure.com/api/projects/proj-4c3eanpernjki`
(user credential): a `create_version` agent is listed by `project.agents.list()`
(new experience) and is a versioned Prompt Agent, whereas classic assistants only
appeared under `AgentsClient.list_agents()`.

Authoritative sources:
- azure-ai-projects README (Microsoft Learn, 2.3.0): "Create and run Agents using
  methods on the `.agents` client property."
  https://learn.microsoft.com/en-us/python/api/overview/azure/ai-projects-readme?view=azure-python
- SDK sample `sample_agent_basic.py` — the canonical create call
  `project_client.agents.create_version(agent_name=..., definition=PromptAgentDefinition(model=..., instructions=...))`.
  https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/ai/azure-ai-projects/samples/agents/sample_agent_basic.py

## IMPLICATIONS

- The runtime never invokes agents via the agents SDK (orchestrator/triage/incident
  use their own search + ServiceNow logic); `config.py` reads the agent-ID env vars
  as opaque strings only. The ID-shape change (`asst_...` → agent name) is therefore
  safe — nothing parses the prefix.
- New-experience agents are referenced by **name** (`agent_reference`), so persisting
  the name is the correct forward-looking identifier if the UI later calls them.
- `scripts/postprovision.py` signature/caller is unchanged; coordinator runs
  postprovision live to (re)create all 3 as new-experience agents.

### 2026-07-08T20-20-40: Removed unused agent-framework dep breaking Linux App Service build
**By:** trinity
**What:** Removed unused agent-framework dep breaking Linux App Service build
**References:** src/requirements.txt, pyproject.toml
**Why:** Removed the vestigial `agent-framework>=1.0` dependency from the deploy-root `src/requirements.txt` and the `[orchestrator]` optional dependency group in `pyproject.toml`. Repo-wide verification found zero `agent_framework` module imports; the live Foundry setup uses the Azure AI Agents/Projects SDK via `azure.ai.projects.AIProjectClient` and `project.agents`, not the Agent Framework PyPI package. The removed package was pulling a prerelease dependency graph involving Windows-only `agent-framework-hyperlight`, causing Linux App Service Oryx/Kudu pip resolution to fail. Remaining runtime dependencies are the Azure SDKs, OpenAI, FastAPI/Uvicorn/Gunicorn, MCP, httpx, Pydantic, Jinja2, and Azure Search packages that are Linux-compatible for Python 3.11.

### 2026-07-08T16:21:22.1465152-05:00: Chat UI gracefully handles backend failures
**By:** Trinity
**What:** The /api/chat endpoint and browser fetch path now degrade gracefully when the orchestrator or downstream ServiceNow backend fails, returning/rendering parseable assistant-style error JSON instead of a bare server error.
**Why:** A bare FastAPI 500 produced an unparseable Unexpected token browser error in the customer-facing UI, hiding the actual backend outage from users.
