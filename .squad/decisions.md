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


### 2025-06-13T00:00:00Z: Final README verification completed; RBAC wording corrected by coordinator
**By:** Fact Checker, coordinator
**What:** Final README deploy-guide claims were verified against Bicep, azure.yaml, hooks, and Python metadata. No contradicted claims were found. The one RBAC wording advisory was resolved directly by the coordinator: deploying users get Azure AI Developer and Cognitive Services OpenAI User only, not Cognitive Services User.
**References:** fact-checker-final-review.md; README.md; infra/modules/foundry.bicep
**Why:** Keeps customer-facing hackathon prerequisites aligned with the deployed RBAC contract and records the coordinator-owned README wording fix because no Dozer follow-up inbox file was written.

### 2025-06-13T00:00:00Z: RAI final review yellow; prompt-hardening advisory accepted
**By:** Rai, Trinity
**What:** Rai found no critical Responsible AI blockers and cleared ship with advisory recommendations for prompt-injection boundaries and side-effect confirmation. Trinity implemented the advisory in `src/helpdesk/agents/prompts.py` only.
**References:** rai-final-review.md; trinity-prompt-hardening.md
**Why:** The accelerator can create/update ServiceNow tickets, so live Foundry prompts now explicitly treat user/KB content as untrusted data and require confirmation for create/update unless the current turn already explicitly requested the exact action.

### 2025-06-13T00:00:00Z: UI deploy contract rejected, reassigned under Reviewer lockout, then cleared
**By:** coordinator, Morpheus, Switch
**What:** Morpheus rejected the final seam review due to three coupled UI deploy blockers: `azure.yaml` pointed at missing `./src/ui`, App Service started `app:app` with undeclared gunicorn, and the deploy root lacked a complete live dependency manifest. Under Reviewer lockout, coordinator reassigned the atomic revision to Switch, who fixed all three blockers plus stale layout comments. Morpheus re-reviewed and cleared the seams to ship.
**References:** coordinator-ui-deploy-contract-rejected-by-morpheus-revision-r.md; morpheus-final-review.md; switch-ui-deploy-fix.md; morpheus-rereview.md
**Why:** The original authors were locked out of revising their rejected work, and the deploy path needed one coordinated fix spanning azure.yaml, App Service startup, and packaging. Validation passed: live-import smoke test, 48 mock pytests, ruff, and Bicep build.

### 2025-06-13T00:00:00Z: QA hardening and hackathon README completed
**By:** Dozer
**What:** Dozer expanded offline mock-mode coverage from 12 to 48 tests, added OpenAPI, UI, KB, and orchestrator flow coverage, rewrote test documentation, and replaced the README stub with a hackathon-grade deploy guide grounded in the implementation.
**References:** dozer-qa-hardening-and-readme.md
**Why:** The solution accelerator now has broad offline validation for the sample prompts and customer-facing deployment instructions before final review and ship readiness.
