# Squad Decisions

## Active Decisions
### 2026-09-03T06:30:00Z: Closed-loop KB admin stays on API App Service; single-hostname UI proxy remains optional (consolidated)
**By:** Coordinator, Morpheus
**What:** Keep the closed-loop KB admin and authoring surface on the Python API App Service for now. The API app owns gap queue reads/writes, authored KB Blob writes, author metadata, and Search indexer triggering through the managed identity that already has Storage/Search RBAC. If a single visible hostname becomes required later, prefer routing or proxying `/admin/*` from the UI hostname to the API app rather than moving privileged Blob/Search write logic into the public UI app.
**Why:** This preserves least privilege, avoids duplicating KB write/indexing logic in the Next.js UI app, keeps support admin gated by Easy Auth on the API app, and accepts the separate low-traffic internal admin hostname for the hackathon. Morpheus assessed edge-routing and UI-proxy options; the Coordinator recorded the final user decision to keep the current API-hosted admin surface.

### 2026-09-03T06:30:00Z: API App Service Easy Auth uses a confidential Entra client with ID-token issuance
**By:** Tank, Coordinator
**What:** Configure the API App Service Easy Auth AAD provider with `MICROSOFT_PROVIDER_AUTHENTICATION_SECRET`, issuer, allowed audience, redirect callback, and Entra app ID-token issuance from postprovision hooks. Keep the secret only in App Service app settings and preserve fail-soft warnings when Graph or ARM operations are unavailable.
**Why:** The dryrun4 `/admin` callback 401 came from Easy Auth being configured without a client-secret setting and relying on the legacy implicit `id_token` path. A confidential client plus ID-token issuance makes browser sign-in reproducible for fresh `azd` deployments without changing anonymous `/agui` and `/healthz` behavior.

### 2026-09-03T06:30:00Z: Foundry identities have Blob RBAC for closed-loop KB gap persistence
**By:** Tank
**What:** Grant the Azure AI Foundry account system-assigned managed identity `Storage Blob Data Contributor` on the KB storage account and codify the assignment in Bicep using the Foundry module `aiFoundryPrincipalId` output. The hosted orchestrator runtime user-assigned identity remains the intended identity path.
**Why:** Hosted orchestrator gap writes use Blob storage. This belt-and-suspenders RBAC keeps Blob-backed knowledge-gap persistence working if the platform resolves the Foundry account system-assigned identity instead of the runtime user-assigned identity, while avoiding storage keys, connection strings, or secrets.

### 2026-09-03T06:30:00Z: Hosted orchestrator writes KB gaps directly to Blob
**By:** Trinity
**What:** The standalone Foundry Hosted Agent orchestrator writes closed-loop knowledge gaps to `_system/kb-gaps/{question_hash}.json` in the KB Blob container after emitting telemetry spans. It uses `azure-storage-blob` and `DefaultAzureCredential`, pins to `AZURE_CLIENT_ID` when present, and writes only when `AZURE_STORAGE_BLOB_ENDPOINT` is configured.
**Why:** The hosted container cannot import the helpdesk package, but unresolved user questions still need durable backlog capture. Direct Blob persistence with the same `question_hash` as telemetry closes the loop while preserving the hosted container boundary.

### 2026-09-02: dryrun4 validated end-to-end; governed-sub gotchas documented
**By:** Coordinator
**What:** dryrun4 completed full provision and postprovision validation for Foundry, hosted orchestrator dependency pins, Blob-sourced KB indexing, triage grounding with citations, and ServiceNow incident create/status/update. Two environment gotchas were documented: governed-sub Storage public network access policy can block KB Blob upload until an RG-scoped exemption/private-endpoint path exists, and ServiceNow PDI hibernation can rotate credentials so azd env and Key Vault/APIM named values must be refreshed.
**Why:** The accelerator's four core capabilities were validated in a governed subscription. The failures encountered were environment/credential issues rather than application bugs and should be treated as deployment runbook items.

### 2026-09-02: ServiceNow params are prompted only by preprovision hooks
**By:** Copilot / Coordinator
**What:** ServiceNow Bicep params now have empty-string defaults in infra/main.bicep and @minLength(1) was removed from serviceNowInstanceUrl; main.parameters.json empty bindings alone did not suppress azd required-parameter prompts. The preprovision hooks remain the single interactive source and prompt URL, username, then password, looping until URL is non-empty.
**Why:** azd resolves required Bicep params before hooks and prompts alphabetically, which put password before username and bypassed the intended hook order. Making the params optional lets the hook control ordering and validation.

### 2026-09-02: Hosted orchestrator Agent Framework pins use core + latest hosting preview
**By:** Switch
**What:** src/orchestrator/requirements.txt pins agent-framework-core==1.15.0 and agent-framework-foundry-hosting==1.0.0b260821, while retaining azure-ai-projects==2.3.0, azure-identity>=1.19.0, and azure-monitor-opentelemetry>=1.6.0. The container no longer depends on the agent-framework meta package or direct agent-framework-foundry package because it only imports core agent_framework symbols and agent_framework_foundry_hosting.
**Why:** The failed ACR build was caused by PyPI resolver drift: agent-framework==1.10.0 hard-pinned old core while an optional OpenAI subpackage required a newer core. Pinning the actually imported packages keeps hosted orchestrator builds deterministic.

### 2026-09-02T18:28:44.954-05:00: Closed-loop KB authoring delivered (consolidated)
**By:** Morpheus, Trinity, Switch, Tank, Dozer, Coordinator
**What:** Delivered CLKB-1..9 as an API-hosted, Easy-Auth-protected closed-loop KB authoring flow. Knowledge gaps are stored as durable Blob JSON under _system/kb-gaps/{question_hash}.json, merging repeated gaps, deduping reasons, preserving workflow status, and using ETags for status updates while retaining the existing knowledge_gap App Insights span. /admin, /admin/gaps, /admin/gaps/{question_hash}/status, /admin/kb/new, and /admin/kb live on the API FastAPI app; /admin/* requires App Service Easy Auth's X-MS-CLIENT-PRINCIPAL, with local/test bypass via ADMIN_AUTH_DISABLED=1, while /agui and /healthz remain anonymous. Publishing writes canonical support-authored markdown to kbdocs/authored/{doc_id}.md with Blob metadata doc_id, title, source, assignment_group, keywords, resolution_steps, gap_hash, created_at, and author, marks the gap resolved, and calls helpdesk.agents.setup.run_indexer(..., wait=False) unless KB_RUN_INDEXER_ON_PUBLISH_ENABLED=0; run failures are non-fatal queued_fallback because the scheduled indexer catches up.

Native Azure AI Search Blob-pull indexing replaced app-side chunk/embed/push while preserving the existing Foundry IQ retrieval contract. The production index schema remains id, doc_id, title, source, assignment_group, content, resolution_steps, and 1536-dimensional content_vector, with search fields content, resolution_steps, title and source-data fields doc_id, title, source, content, resolution_steps, assignment_group. setup.py now creates/updates a Blob datasource, Split skill, Azure OpenAI embedding skill, index projections, and indexer; scripts/postprovision.py uploads seed docs with metadata, creates the native pipeline, runs the indexer once, then refreshes Foundry IQ. Scratch proof reached Search datasource validation and was blocked only by Search MI Storage RBAC before Tank's infra; Trinity owns post-provision re-verification of projected fields, 1536 vectors, stable chunk keys, and Foundry IQ citations.

Infra added API-only Easy Auth with fail-soft Entra app registration (ADMIN_AAD_CLIENT_ID, ADMIN_AAD_TENANT_ID, optional ADMIN_AAD_APP_DISPLAY_NAME) and Search service managed identity RBAC to read Storage and call the embedding deployment. No datasource/skillset/indexer Bicep was added; Search SDK setup owns those resources. README now documents support-staff operation, Entra manual fallback, RBAC, feature flags, and native pull indexing. Tests cover the mocked gap-to-admin-to-publish-to-indexer flow, Blob metadata contract, non-fatal indexer fallback, and managed-identity datasource shape; validation ended at commit e4b4995 with 158 tests passing and ruff clean.
**Why:** The design closes the loop from real unresolved tickets to authored KB articles without adding a new app or Event Grid/Function surface. Blob is the durable backlog and KB source of truth, Easy Auth keeps support administration on the API app without front-end token forwarding, native Search indexing makes support-authored docs survive reprovisioning, and Foundry IQ remains a stable retrieval layer over the same Search index.

### 2026-09-02: Index KB from Blob Storage (single source of truth) — no local fallback
**By:** Squad (Coordinator), requested by arbaner. Built by Trinity; verified + hardened by Coordinator.
**What:** The AI Search index is now built from KB markdown in the `kbdocs` Blob container, NOT from local `assets/kb`. New `load_kb_from_blob(*, blob_endpoint, container)` in `src/helpdesk/agents/setup.py` enumerates `*.md` blobs (deferred `azure.storage.blob` import), parses each via `parse_markdown`. `build_search_index()` gained `blob_endpoint`/`kb_container` params: when a blob endpoint is provided it reads ONLY from Blob and RAISES a clear "re-run azd provision" RuntimeError if the load fails OR the container is empty — there is NO silent local fallback in the deployed path. `upload_kb_docs()` (postprovision) is now FATAL after the RBAC-propagation retry ladder is exhausted (Blob is the required RAG source, no longer archival-only). postprovision STEP 2 passes `AZURE_STORAGE_BLOB_ENDPOINT` as REQUIRED (removed the `default=None`) so a missing endpoint fails loudly instead of falling to the `else: load_local_kb()` branch. `load_local_kb` is retained solely as (a) the seed source that `upload_kb_docs` pushes to Blob and (b) the source for mock/offline unit tests (which call `build_search_index` with no blob endpoint). 5 new tests in `tests/test_search_index_kb_source.py`; full suite 143 passed, ruff clean. Commit `90d61fa` on master.
**Why:** Prerequisite for the closed-loop "gap → support authors KB → re-index" feature: support-authored KB articles must live in durable Blob and survive an `azd provision` / index rebuild. Indexing from local assets would silently drop any KB not baked into the repo. Per explicit user direction, Blob upload is a NECESSARY step and there is no local fallback — a failed/empty upload must fail the deploy rather than produce an ungrounded or stale index.

### 2026-09-02: Knowledge-Gap Harvester — transport & dual-runtime design
**By:** Squad (Coordinator), requested by arbaner. Built by Trinity (offline orchestrator), Switch (hosted main.py), Dozer (tests + README/Kusto).
**What:** New feature that captures the ORIGINATING user question as a structured "knowledge gap" whenever triage can't confidently resolve from the KB or the user escalates to a ServiceNow incident — so the team can turn real unmet tickets into KB articles. Transport = an OpenTelemetry span named `knowledge_gap` emitted to the EXISTING App Insights (Foundry project + App Service). No new infra, no new RBAC, no provisioning step. Span attributes use the `helpdesk.kb_gap.*` prefix: `reason`, `tool`, `had_citations`, `question_length`, `question_hash` (first 16 hex of sha256), and `question` (raw text — attached ONLY when `AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED` is truthy). Master toggle `KB_GAP_HARVEST_ENABLED` (default ON). Recording is best-effort — never raises, never blocks/alters a turn. Four stable reason codes for Kusto: `triage_unresolved`, `triage_no_citations`, `incident_created`, `kb_insufficient`. Canonical recorder lives in `src/helpdesk/observability/knowledge_gaps.py`; because the hosted orchestrator container (`src/orchestrator/`) is built with `COPY . ./` from that dir ONLY and CANNOT import the `helpdesk` package, an INLINE mirror lives in `src/orchestrator/main.py` using the identical span name + attribute keys so one Kusto query covers both runtimes. `OrchestratorResponse` gained an additive `knowledge_gap: KnowledgeGap | None` field (does not affect the UI). README documents it + two Kusto backlog queries. 15 new tests; full suite 138 passed, ruff clean.
**Why:** Chose spans-to-App-Insights over blob storage because hosted-agent managed-identity blob RBAC is not guaranteed in governed subscriptions, and App Insights is already wired for both runtimes — zero added surface for hackathon deploys. Duplicated the recorder (canonical + inline mirror) rather than restructuring the container build, because the two-runtime import boundary is a hard Dockerfile constraint; parity is enforced by a test asserting identical span name + attribute keys.

### THE DOMINANT CONTRIBUTOR = orchestrator gpt-5.4 reasoning "thinking" time, spent TWICE per turn
Token evidence (orchestrator `chat gpt-5.4` spans): **1360–1639 input tokens → only 29–147 output
tokens, in 6–10s.** Producing ~40 tokens should take <1s; the 6–10s is gpt-5.4 reasoning-model
hidden thinking on the large, rule-dense ~1500-token orchestrator prompt. Proof it's the *model
mode + prompt*, not the model itself: the **identical gpt-5.4** as the incident sub-agent returns in
**0.38–0.57s**, and **gpt-5.4-mini** (triage) in **0.42s** — 12–15× faster. The orchestrator pays this
cost twice: once to decide which tool to call, and again to re-relay the sub-agent's output verbatim
(the "double model round-trip"). Cold start adds ~6s on the FIRST turn only (turn 1 took 12.27s to
`response.created` vs 6.2–6.5s steady state = hosted-container warm-up).

## (C) "forward-request" error root cause + latency cost + owner

**What they are:** APIM exceptions `ClientConnectionFailure at transfer-response` on operation
`servicenow-mcp;rev=1 - getMcp` — **84 over 6h; 8 during my 3-turn window.** The `GET /servicenow/mcp`
requests show `resultCode = 0 [not sent in full]`, success=False (42/42 fail over 6h).

**Root cause:** MCP Streamable-HTTP transport. The Foundry MCP client opens `GET /servicenow/mcp`
to establish the SSE **server→client downstream channel**, then tears it down as soon as it has the
JSON-RPC reply from the `POST`. APIM logs that client-initiated close as `ClientConnectionFailure`
at the `transfer-response` stage. It is a normal artifact of the MCP SSE channel lifecycle, not a
backend failure — every actual tool call (`POST /servicenow/mcp`) succeeds (200/202).

**A SEPARATE, unrelated error class:** `OperationNotFound at configuration` (60/6h) is **100% internet
scanner noise** hitting the public gateway — `GET /`, `/favicon.ico`, Fortinet exploit probes
(`/lang/custom/sbin/init`, `/remote/logincheck`, `/migadmin/...`). NONE is agent traffic.

**Latency cost: ~0 (they do NOT contribute to slowness).** The failing GET SSE channel runs
**concurrently** with the POST tool calls, which complete in <1ms–810ms. No retries, no backoff.
Window evidence (turn 2): MCP `POST`s returned 200/202 in <1ms–810ms while the GET "failed" alongside
at 55ms & 825ms — overlapping, never serial; the `createIncident` tool span succeeded first try (2.05s).

**Owner: Tank (APIM / infra), for TRACE HYGIENE only — not Trinity, not a latency fix.**

## (D) Prioritized recommendations (impact / effort / risk / owner)

1. **Cut orchestrator gpt-5.4 reasoning time — THE #1 lever (~12–17s/turn).** Needs eval + sign-off:
   - (a) **Lower the orchestrator's reasoning effort** (gpt-5.4 supports low/minimal; we already use
     `KnowledgeRetrievalMinimalReasoningEffort` for KB retrieval — precedent exists).
     Impact **HIGH** (each 6.6s pass → ~1–2s; ~8–12s/turn). Effort **LOW**. Risk **MEDIUM** (routing
     quality — must re-run the 5-case deflect/create/status regression). **Owner: Trinity.**
   - (b) **Move the orchestrator routing brain to gpt-5.4-mini** (proven 0.42s vs 6.61s). Impact
     **HIGH**. Effort **LOW** (`create_version` + env). Risk **MEDIUM-HIGH** (deflect/routing judgment
     on the brain). **Owner: Trinity (agent) + Tank (deploy).** Needs eval.
   - (c) **Trim the ~1500-token orchestrator prompt.** Impact **LOW-MED**. Effort **MED**. Risk **MED**.
     **Owner: Trinity.**
2. **Eliminate the double model round-trip (the 2nd ~6.6s "relay" pass).** The orchestrator re-invokes
   gpt-5.4 purely to paste the sub-agent output verbatim. Relay the sub-agent output straight through
   (bypass the LLM for pure relay) or let the sub-agent's answer be terminal. Impact **HIGH**
   (~6.6s/turn AND halves reasoning cost). Effort **MED-HIGH** (architectural — currently guaranteed by
   the "RELAY VERBATIM" instruction). Risk **MEDIUM**. **Owner: Trinity + Morpheus (arch).** Sign-off.
3. **First-turn cold start (~6s).** Warm-keep the hosted orchestrator + prompt-agent containers
   (min-replica / keep-alive ping). Impact **LOW-MED** (first turn only). Effort **LOW**. Risk **LOW**.
   **Owner: Tank.**
4. **Fix forward-request/getMcp SSE noise — trace hygiene, ~0 latency.** Handle the client SSE close
   gracefully / drop the unused GET SSE channel if the Foundry client only needs POST. Impact **~0
   latency** (cleaner traces, fewer false alarms). Effort **LOW**. Risk **LOW**. **Owner: Tank.**
5. **Perceived latency / first token.** Handoff status frames already stream ("Calling Triage/Incident
   Agent"). Consider surfacing the sub-agent's raw steps to the user the moment they return, instead of
   waiting for the 2nd orchestrator pass (ties to #2). Impact **MED** (perceived). Effort **MED**.
   **Owner: Switch (UI) + Trinity.**
6. **NOT worth optimizing now:** KB retrieval (1.42s — already extractive + minimal-effort) and the
   APIM MCP path (2.0s create / 1.3+1.4s query+get). **APIM developer tier is NOT the bottleneck** —
   MCP POSTs return in <1ms–810ms. No tier change needed for latency.

## (E) Quick win implemented?
**No functional changes made — recommendations only.** There is no zero-risk quick win: the KB is
already minimal-effort/extractive, there is NO misconfigured reasoning-effort/timeout to safely flip
(the orchestrator runs gpt-5.4 at its default effort with no override), and the forward-request errors
cost ~0 latency. Every real lever (reasoning effort, mini for the brain, removing the relay pass) changes
the routing brain's behavior and requires abKrazy sign-off + a fresh eval before shipping.

**Recommended first step for abKrazy to approve:** 1(a) lower the orchestrator's reasoning effort — the
single highest impact-to-risk move (~8–12s/turn) — gated behind a re-run of the 5-case regression.

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

### Phase 2: Hosted Orchestrator deploy = CONTAINER path (not code-ZIP)
**By:** Squad (Coordinator) for @abKrazy
**What:** Deploy the MAF Foundry Hosted Orchestrator via the **container** path — `az acr build` (server-side, no local Docker) pushes the image to our provisioned ACR, then `AIProjectClient.agents.create_version(agent_name="it-helpdesk-orchestrator", definition=HostedAgentDefinition(container_configuration=ContainerConfiguration(image=...)))`.
**Why:** In azure-ai-projects 2.3.0 the code-ZIP path is only exposed via the PRIVATE method `_create_version_from_code` (leading underscore, undocumented multipart contract) — fragile for an accelerator. The container path uses the PUBLIC, stable `create_version` API, aligns with Tank's ACR AcrPush/AcrPull RBAC already provisioned, and `az acr build` needs no Docker daemon on hackathon laptops (runs server-side, invoked from the postprovision shell hook to avoid the Windows az.cmd-from-python issue).
**Verified SDK shape (2.3.0):** HostedAgentDefinition(kind="hosted", cpu:str, memory:str, environment_variables:dict, container_configuration=ContainerConfiguration(image:str), protocol_versions=[ProtocolVersionRecord(protocol="responses", version=<tbd-live>)]). Enums: AgentEndpointProtocol.RESPONSES="responses"; CodeDependencyResolution in {bundled, remote_build}.
**Open live item:** exact responses protocol `version` string is a Foundry contract — discover on first live deploy and pin.

### 2026-09-02: CLKB-2 native indexer live fix — keyword key analyzer + parent projection field
**By:** Trinity

**What:** Fixed the live Azure AI Search index projection failure and proved the native Blob pull-indexing pipeline end-to-end in `ITHelpdesk-Assistant-dryrun4`.

## Fixes

- Changed the index key field `id` from `SimpleField` to `SearchField(..., key=True, searchable=True, filterable=True, analyzer_name=keyword)`.
- Added additive helper field `parent_id` (`Edm.String`, filterable). Azure AI Search index projections require a non-key parent field; using existing `doc_id` as `parentKeyFieldName` polluted `doc_id` with the encoded blob path, so `parent_id` is required to keep triage's `doc_id` contract intact.
- `build_search_index()` now recreates the Search index before provisioning the native indexer pipeline. Recreate is deliberate and safe because Blob is the source of truth and postprovision immediately reruns the indexer.
- Recreate also deletes the Search agentic-retrieval knowledge base/source first when they reference `it-helpdesk-kb`, because Search blocks deleting an index while `it-helpdesk-kb-source` references it. `create_foundry_agents()` / `ensure_kb_knowledge_base()` recreates them by name afterward.
- Removed indexer `field_mappings` from the projected path. Projection mappings now read custom Blob metadata from the enrichment tree as `/document/doc_id`, `/document/title`, `/document/source`, `/document/assignment_group`, and `/document/resolution_steps`.
- Kept `content_vector` unchanged: `text-embedding-3-large`, dimensions `1536`.

## Native pipeline after fix

- Data source: `it-helpdesk-kb-blob-ds`
  - Type: `azureblob`
  - Auth: Search service system-assigned managed identity via `ResourceId=<storage-account-resource-id>;`
  - Container: `kbdocs`
  - Change detection: `metadata_storage_last_modified`
  - Deletion detection: native Blob soft delete
- Skillset: `it-helpdesk-kb-blob-skillset`
  - `SplitSkill`: `/document/content` -> `/document/pages/*`, pages, 1200 characters, 100 overlap
  - `AzureOpenAIEmbeddingSkill`: `/document/pages/*` -> `/document/pages/*/content_vector`, `text-embedding-3-large`, dimensions `1536`, Search MI auth
  - Projection target: `it-helpdesk-kb`
  - Parent key field: `parent_id`
  - Projection mode: `skipIndexingParentDocuments`
- Indexer: `it-helpdesk-kb-blob-indexer`
  - Schedule: every 5 minutes
  - `parsingMode=text`, `.md` only, `contentAndMetadata`

## Blob metadata -> index fields

| Source path | Index field |
|---|---|
| `/document/doc_id` | `doc_id` |
| `/document/title` | `title` |
| `/document/source` | `source` |
| `/document/assignment_group` | `assignment_group` |
| `/document/resolution_steps` | `resolution_steps` |
| `/document/pages/*` | `content` |
| `/document/pages/*/content_vector` | `content_vector` |
| generated parent key | `parent_id` |

## Live run proof

Environment:

- azd env: `ITHelpdesk-Assistant-dryrun4`
- Search endpoint: `https://srch-qhe5qssi4mriy.search.windows.net`
- Index: `it-helpdesk-kb`
- Storage: `stqhe5qssi4mriy` / `kbdocs`

Result:

- Index recreation was needed:
  - First defect: existing `id` key lacked `keyword` analyzer.
  - Second live defect found during proof: schema needed additive `parent_id` to prevent `doc_id` pollution.
- Recreated index by name, deleted/recreated Foundry IQ `knowledgeBase`/`knowledgeSource` by name, so the KB MCP URL remains:
  - `https://srch-qhe5qssi4mriy.search.windows.net/knowledgebases/it-helpdesk-kb/mcp?api-version=2026-05-01-preview`
- Indexer run completed successfully after final fix:
  - Final status poll: `success`
  - Errors: `0`
  - Warnings: `0`
- Query proof:
  - Chunk count: `7`
  - Doc count: `7`
  - Chunks by doc:
    - `laptop-performance`: 1
    - `outlook-email-issues`: 1
    - `password-reset`: 1
    - `printer-issues`: 1
    - `software-installation`: 1
    - `unable-to-login`: 1
    - `vpn-connectivity`: 1
  - Missing required fields: `0`
  - `content_vector` schema dimensions: `1536`

Sample indexed chunk:

```json
{
  "id": "416f9700d114_aHR0cHM6Ly9zdHFoZTVxc3NpNG1yaXkuYmxvYi5jb3JlLndpbmRvd3MubmV0L2tiZG9jcy9wYXNzd29yZC1yZXNldC5tZA2_pages_0",
  "parent_id": "aHR0cHM6Ly9zdHFoZTVxc3NpNG1yaXkuYmxvYi5jb3JlLndpbmRvd3MubmV0L2tiZG9jcy9wYXNzd29yZC1yZXNldC5tZA2",
  "doc_id": "password-reset",
  "title": "Password Reset and Login Assistance",
  "source": "password-reset.md",
  "assignment_group": "Service Desk",
  "resolution_steps": "1. Verify username. 2. Use self-service password reset. 3. Restart affected applications. 4. Reauthenticate and complete MFA."
}
```

## Validation

- `HELPDESK_MOCK=1 PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider` passed: 159 tests.
- `.\.venv\Scripts\python.exe -m ruff check .` passed.

**Why:** Azure AI Search index projections enforce a stricter index contract than normal indexing: the key field must use the keyword analyzer and a separate parent key field is required. The additive `parent_id` keeps Foundry IQ and triage source-data fields stable while satisfying native one-to-many projection requirements.
