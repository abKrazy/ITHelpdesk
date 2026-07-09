# Squad Decisions

## Active Decisions

### 2026-07-09: Latency investigation — trace-driven per-agent breakdown + forward-request root cause (recommendations only, NO functional change)
**By:** Trinity (AI / Agent Engineer)
**Status:** Diagnostic complete. Live-measured against the hosted orchestrator v6 (gpt-5.4),
triage v5 (gpt-5.4-mini), incident v5 (gpt-5.4). NO code/infra change made — every
meaningful latency lever alters the routing brain's behavior and needs abKrazy sign-off + eval.

---

## (A) Measured end-to-end latency per turn (felt by the UI)

Drove 3 live full flows through the hosted orchestrator exactly as the UI does
(`get_openai_client(agent_name='it-helpdesk-orchestrator')` → `responses.create(stream=True)`),
window **15:47:39–15:48:58Z 2026-07-09**:

| Turn | Time-to-first-token | Total wall clock |
|------|--------------------:|-----------------:|
| 1 — KB deflect ("my laptop is running slow") | **22.08s** | **28.48s** |
| 2 — Create ticket (confirm "file a ticket") → INC0010054 | **21.86s** | **27.59s** |
| 3 — Status lookup ("status of INC0010054?") | **17.12s** | **22.61s** |

The user is right: ~17–22s to first token, ~22–28s total. Very slow.

## (B) Per-agent / per-stage breakdown (App Insights spans, same window)

**Turn 1 — KB deflect** (`invoke_agent it-helpdesk-orchestrator` = 17.39s):
| Stage | Span | Dur |
|-------|------|----:|
| **Orchestrator reasoning pass #1 (decide tool)** | `chat gpt-5.4` | **7.27s** |
| Sub-agent handoff (triage) | `execute_tool troubleshoot_from_knowledge_base` | 3.28s |
| └ triage agent | `responsesapi invoke_agent it-helpdesk-triage:5` | 2.66s |
| &nbsp;&nbsp;└ KB retrieval (AI Search) | `mcp_knowledge-base.knowledge_base_retrieve` | 1.42s |
| &nbsp;&nbsp;└ triage chat (**gpt-5.4-mini**) | `chat gpt-5.4-mini` | **0.42s** |
| **Orchestrator reasoning pass #2 (relay verbatim)** | `chat gpt-5.4` | **6.83s** |
| **DOMINANT** | orchestrator's TWO gpt-5.4 passes = **14.1s (81%)** | |

**Turn 2 — Create ticket** (`invoke_agent it-helpdesk-orchestrator` = 21.27s):
| Stage | Span | Dur |
|-------|------|----:|
| **Orchestrator reasoning pass #1** | `chat gpt-5.4` | **10.02s** |
| Sub-agent handoff (incident) | `execute_tool manage_servicenow_incident` | 4.63s |
| └ incident agent | `responsesapi invoke_agent it-helpdesk-incident:5` | 3.95s |
| &nbsp;&nbsp;└ APIM→ServiceNow create | `mcp_servicenow-apim.createIncident` | 2.05s |
| &nbsp;&nbsp;└ incident chat gpt-5.4 (×2) | `chat gpt-5.4` | 0.57s + 0.38s |
| **Orchestrator reasoning pass #2 (relay)** | `chat gpt-5.4` | **6.61s** |
| **DOMINANT** | orchestrator's two gpt-5.4 passes = **16.6s (78%)** | |

**Turn 3 — Status lookup** (`invoke_agent it-helpdesk-orchestrator` = 16.57s):
| Stage | Span | Dur |
|-------|------|----:|
| **Orchestrator reasoning pass #1** | `chat gpt-5.4` | **6.24s** |
| Sub-agent handoff (incident) | `execute_tool manage_servicenow_incident` | 4.48s |
| └ incident agent | `responsesapi invoke_agent it-helpdesk-incident:5` | 3.86s |
| &nbsp;&nbsp;└ resolve sys_id | `mcp_servicenow-apim.queryTable` | 1.33s |
| &nbsp;&nbsp;└ read record | `mcp_servicenow-apim.getRecord` | 1.40s |
| &nbsp;&nbsp;└ incident chat gpt-5.4 | `chat gpt-5.4` | 0.30s |
| **Orchestrator reasoning pass #2 (relay)** | `chat gpt-5.4` | **5.85s** |
| **DOMINANT** | orchestrator's two gpt-5.4 passes = **12.1s (73%)** | |

**Aggregate p50/max (3 turns):** `chat gpt-5.4` (orchestrator) **p50 6.61s / max 10.02s, TWO per turn**;
incident `chat gpt-5.4` 0.38–0.57s; triage `chat gpt-5.4-mini` 0.42s; `knowledge_base_retrieve` 1.42s;
`createIncident` 2.05s; `queryTable` 1.33s; `getRecord` 1.40s.

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


### 2026-07-09: KB deflection regression fixed — orchestrator now invokes each sub-agent with its OWN model (triage stays on gpt-5.4-mini)
**By:** Trinity
**What:** Fixed the live bug where the triage agent stopped returning knowledge-base
steps ("I wasn't able to retrieve the knowledge base steps just now"). Root cause
was NOT a gpt-5.4-mini capability regression, NOT a broken KB/index/MCP — it was a
CODE bug in the hosted orchestrator surfaced by the triage→gpt-5.4-mini switch.

**Root cause (live KQL + repro evidence):**
- Reproduced the exact UI turn ("laptop is slow. file a ticket") against the hosted
  orchestrator. The orchestrator DID call `troubleshoot_from_knowledge_base`, but the
  tool returned `Error: Function failed.` and the LLM apologized.
- App Insights `appi-ztk6zx5aedqtc`: `execute_tool troubleshoot_from_knowledge_base`
  (cloud_RoleName `it-helpdesk-orchestrator`) span **success=False**, while
  `execute_tool manage_servicenow_incident` was **success=True**. The orchestrator
  `exceptions` table logged the exact cause:
  `openai.BadRequestError: 400 invalid_payload — "Model must match the agent's model
  'gpt-5.4-mini' when agent is specified." param='model'`.
- Direct triage Prompt Agent call on gpt-5.4-mini worked perfectly (called
  `knowledge_base_retrieve`, retrieved 5 docs, cited sources, deflected) — proving the
  mini model, the KB, the AI Search index, and the MCP connection are all healthy.
- The bug: `src/orchestrator/main.py._call_prompt_agent` passed the orchestrator's OWN
  `MODEL` (gpt-5.4) for EVERY `agent_reference` Responses call. Foundry requires the
  `model` param to equal the referenced agent's own deployment. Incident stayed on
  gpt-5.4 (matched → worked); triage moved to gpt-5.4-mini (mismatch → 400 → KB
  deflection broke).

**Fix (orchestrator code layer — kept triage on gpt-5.4-mini):**
- `src/orchestrator/main.py`: added `TRIAGE_MODEL` (from `TRIAGE_MODEL_DEPLOYMENT_NAME`
  / `AZURE_OPENAI_TRIAGE_CHAT_DEPLOYMENT`, falling back to `MODEL`) and a
  `_MODEL_BY_AGENT` map. `_call_prompt_agent` now resolves the model per sub-agent so
  triage is invoked with gpt-5.4-mini and incident with gpt-5.4.
- `src/helpdesk/agents/setup.py`: `create_hosted_orchestrator(...)` takes a new
  `triage_chat_deployment` kwarg and injects `TRIAGE_MODEL_DEPLOYMENT_NAME` into the
  hosted container env (non-reserved key). Falls back to the main deployment when unset.
- `scripts/postprovision.py`: forwards `AZURE_OPENAI_TRIAGE_CHAT_DEPLOYMENT` so `azd up`
  reproduces the fix idempotently.
- Tests: `tests/test_orchestrator_hosted.py` gains a regression test proving
  `_call_prompt_agent` passes each agent's own model; `tests/test_hosted_orchestrator_setup.py`
  asserts `TRIAGE_MODEL_DEPLOYMENT_NAME` is injected (default + dedicated-mini cases).
  `ruff check .` clean; full `pytest` green (98 passed).

**Live republish + verification:**
- Rebuilt the orchestrator image via `az acr build`
  (`acrztk6zx5aedqtc.azurecr.io/it-helpdesk-orchestrator:kbfix-20260709101920`) and
  re-registered the hosted agent via `AIProjectClient.agents.create_version` →
  **orchestrator v6**. Triage stays **v5 / gpt-5.4-mini**; incident stays v5 / gpt-5.4.
- Re-ran the live "laptop is slow. file a ticket" turn: orchestrator now returns the
  actual numbered KB steps + "Recommended assignment group: Desktop Support",
  deflect-first, no premature ticket. KQL: `execute_tool troubleshoot_from_knowledge_base`
  = **success=True**, `invoke_agent it-helpdesk-triage:5` = **True**,
  `execute_tool mcp_knowledge-base.knowledge_base_retrieve` = **True**.

**Recommendation for abKrazy:** No revert needed — gpt-5.4-mini is fully reliable for
the triage KB tool-calling workload (it calls `knowledge_base_retrieve` and relays cited
steps correctly). The cost/latency win of running triage on gpt-5.4-mini is preserved.
The general lesson: whenever a sub-agent's deployment diverges from the orchestrator's,
the orchestrator must invoke it with that agent's own model — now handled generically.

**Why:** Restores KB deflection (the core "resolve before ticketing" behavior) without
sacrificing the deliberate cost optimization, and hardens the orchestrator against any
future per-agent model divergence.


### 2026-07-09: Triage traces + orchestrator traces diagnosed live — config already correct, no code/infra change
**By:** Trinity
**What:** Investigated the report that "triage + orchestrator agents are not
logging traces." Diagnosed LIVE via KQL against App Insights `appi-ztk6zx5aedqtc`
(app GUID `4505fe85-82ee-4226-881f-f24556379ac6`) BEFORE changing anything.
Conclusion: **all three agents DO emit traces; the reported gap was a transient
post-republish warm-up window, now self-healed and verified.** No orchestrator
rebuild, no bicep change, no code change was required for tracing.

**What is present / what was missing:**
- (a) Hosted orchestrator's own spans — **present** under `cloud_RoleName ==
  'it-helpdesk-orchestrator'`: `invoke_agent it-helpdesk-orchestrator`,
  `execute_tool troubleshoot_from_knowledge_base` / `manage_servicenow_incident`,
  `chat gpt-5.4`. Verified the v5 (gpt-5.4) container still initializes Azure
  Monitor — no OTel regression from the gpt-5.4 redeploy.
- (b)/(c) Triage + incident Prompt Agent internal spans — **present** but under
  `cloud_RoleName == 'responsesapi'` (Foundry's managed responses runtime, NOT
  the agent names): `invoke_agent it-helpdesk-triage:N`,
  `invoke_agent it-helpdesk-incident:N`, `chat <model>`,
  `execute_tool mcp_knowledge-base.knowledge_base_retrieve`,
  `execute_tool mcp_servicenow-apim.*`. The span NAME carries the agent+version,
  so they are findable — but a filter by `cloud_RoleName == 'it-helpdesk-triage'`
  finds nothing, which is likely why they looked "missing."

**Root cause of the transient gap:** Right after a Prompt Agent `create_version`
republish, the Foundry managed runtime for the new version cold-starts and its
App Insights export takes a few minutes to warm up. Live timeline proof: prompt
agents were republished 08:04Z; orchestrator drove tool calls at 08:20–08:22Z
with NO `responsesapi` traces; by 08:35Z direct + 08:45Z orchestrator-driven
flows produced full `responsesapi` traces again. The Task-2 triage republish
(v5, ~08:53Z) reproduced the same brief gap, then recovered — triage `gpt-5.4-mini`
spans appeared by ~08:59Z. This warm-up is inherent Foundry platform behavior and
self-heals; it is not a defect in our code or infra.

**Config verified correct + durable (idempotent via `azd up`):**
- Project↔App Insights connection `proj-ztk6zx5aedqtc-appinsights` exists,
  `isDefault=true`, `category=AppInsights`, target = `appi-ztk6zx5aedqtc`. This is
  created control-plane in `infra/modules/foundry.bicep`
  (`resource appInsightsConnection ... name '${aiProjectName}-appinsights'`,
  lines ~210–226) so `azd up` reproduces it. No bicep change needed.
- Orchestrator hosted-agent env vars are correct: `OTEL_SERVICE_NAME=
  it-helpdesk-orchestrator`, `AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED=true`,
  `AZURE_AI_MODEL_DEPLOYMENT_NAME=gpt-5.4`. `APPLICATIONINSIGHTS_CONNECTION_STRING`
  is (correctly) NOT set in the container env — it is reserved + auto-injected by
  Foundry; `main.py` reads it at runtime.

**Known platform limitation (documented, not fixable in our code):** the Foundry
responses backend starts its OWN root trace for each prompt-agent invocation
instead of continuing the orchestrator's propagated `traceparent`, so the
orchestrator trace and the prompt-agent trace are NOT linked parent↔child in the
end-to-end transaction view. Both are still individually visible in App Insights
and the Foundry Tracing tab. Confirmed by experiment: a call carrying a parent
span still produced `responsesapi` spans under a DIFFERENT `operation_Id`.

**LIVE KQL evidence (post-fix, all three agents tracing):**
Driving a full orchestrator flow (deflect → KB steps → create INC0010050) at
08:45Z, then querying `union dependencies,requests where timestamp>08:45Z`:
- `it-helpdesk-orchestrator`: trace + dependency (chat gpt-5.4, execute_tool, invoke_agent)
- `responsesapi`: `invoke_agent it-helpdesk-triage:4`, `invoke_agent it-helpdesk-incident:5`,
  `chat gpt-5.4-2026-03-05` (x6), `execute_tool mcp_knowledge-base.knowledge_base_retrieve`,
  `execute_tool mcp_servicenow-apim.createIncident`
- `agentsv2`: request (the prompt-agent ingress)

**Why:** The system was already correctly instrumented end-to-end; the fix was to
prove it live and identify the transient warm-up as the cause rather than change
working infra. Documented the `responsesapi` role-name attribution and the
cross-boundary correlation limitation so future "missing traces" reports are
triaged correctly (filter by span NAME `invoke_agent it-helpdesk-*`, not by
`cloud_RoleName`, and allow a few minutes after any agent republish).

### 2026-07-09: Triage Prompt Agent moved to gpt-5.4-mini (orchestrator + incident stay on gpt-5.4)
**By:** Trinity
**What:** Pointed ONLY the triage Prompt Agent at the new `gpt-5.4-mini`
deployment (provisioned by Tank, exposed via azd env
`AZURE_OPENAI_TRIAGE_CHAT_DEPLOYMENT=gpt-5.4-mini`). The hosted orchestrator and
the incident Prompt Agent remain on the main `gpt-5.4` deployment.

**Code changes:**
- `src/helpdesk/shared/config.py`: added `Settings.triage_chat_deployment`,
  loaded from `AZURE_OPENAI_TRIAGE_CHAT_DEPLOYMENT`, falling back to
  `chat_deployment` when unset (mirrors how `chat_deployment` is loaded), so
  environments that only provision the main deployment keep working.
- `src/helpdesk/agents/setup.py`: `create_foundry_agents(...)` takes a new
  optional `triage_chat_deployment: str | None = None` kwarg. It passes
  `triage_chat_deployment or chat_deployment` as `chat_deployment` into
  `build_triage_definition(...)` ONLY. `build_incident_definition(...)` keeps the
  main `chat_deployment`. Added a `[setup]` log line surfacing both models.
- `scripts/postprovision.py`: `create_foundry_agents()` now reads
  `AZURE_OPENAI_TRIAGE_CHAT_DEPLOYMENT` (optional) and forwards it, so `azd up`
  reproduces triage-on-mini idempotently.
- Tests: added `tests/test_config_settings.py` (3 cases: dedicated var set,
  fallback to main, empty when nothing configured) and two cases in
  `tests/test_foundry_agents_setup.py`
  (`test_triage_uses_its_own_deployment_incident_stays_on_main`,
  `test_triage_deployment_falls_back_to_main_when_unset`). `ruff check .` clean;
  `pytest` green.

**Live republish:** republished the triage Prompt Agent via
`AIProjectClient.agents.create_version(agent_name="it-helpdesk-triage", ...)`
using `build_triage_definition(chat_deployment="gpt-5.4-mini", ...)` — published
**triage v5**, `model=gpt-5.4-mini`. Orchestrator stays v5 (hosted, gpt-5.4),
incident stays v5 (prompt, gpt-5.4).

**LIVE KQL evidence** (App Insights `appi-ztk6zx5aedqtc`,
`dependencies` where `cloud_RoleName=='responsesapi'`, `gen_ai.request.model`):
- `invoke_agent it-helpdesk-triage:5`  -> `gpt-5.4-mini-2026-03-17`
- `chat gpt-5.4-mini-2026-03-17`       -> `gpt-5.4-mini-2026-03-17`
- `execute_tool mcp_knowledge-base.knowledge_base_retrieve` (triage/KB path)
- `invoke_agent it-helpdesk-incident:5` -> `gpt-5.4-2026-03-05` (still gpt-5.4)
- `it-helpdesk-orchestrator` `chat` span -> `gpt-5.4` (still gpt-5.4)

Direct triage-on-mini call grounded correctly on the KB (returned cited steps),
confirming the smaller model still uses the `knowledge_base_retrieve` MCP tool.

**Why:** Triage is the high-volume, first-hop deflection agent; running it on the
cheaper/faster `gpt-5.4-mini` cuts cost/latency for the common path while keeping
the orchestrator's routing brain and the incident agent's ServiceNow writes on
the full `gpt-5.4`. The fallback keeps non-mini environments safe.

### 2026-07-09: Orchestrator handoff-event contract for the UI (Switch consumes this)
**By:** Trinity
**What:** The exact stream contract Switch must implement to render "Calling
Orchestrator / Triage Agent / Incident Agent" as the hosted orchestrator hands
off. **No orchestrator code change was required** — the outer Responses stream
already surfaces the orchestrator's internal tool calls as first-class events.

---

## TL;DR

When the UI calls `client.responses.create(..., stream=True)` against the hosted
`it-helpdesk-orchestrator` agent, the tool/function calls the orchestrator makes
to its two sub-agents ARE visible in the outer stream as
`response.output_item.added` events whose `item.type == "function_call"` and
whose `item.name` is the tool name. Switch maps that tool name to a label and
emits a UI SSE `status` frame. This was captured live (not theory).

## Where the sub-agent name appears (live-verified event shapes)

Primary signal (earliest — fires the moment the orchestrator decides to call a tool):

```
event.type == "response.output_item.added"
event.item.type == "function_call"
event.item.name  == "troubleshoot_from_knowledge_base" | "manage_servicenow_incident"
event.item.id    == "<call id>"   # correlates added -> args.delta -> done
```

Secondary / fallback signal (fires after the tool arguments finish streaming):

```
event.type == "response.function_call_arguments.done"
event.name       == "<same tool name>"
event.item_id    == "<same call id>"
event.arguments  == "<json string of tool args>"   # usually the underlying user problem
```

Between those two, `response.function_call_arguments.delta` events stream the
JSON arguments token-by-token (`event.delta`, `event.item_id`). Switch does NOT
need these for the label; ignore them for status rendering.

## Tool -> label mapping

| Signal                                    | UI status label          |
|-------------------------------------------|--------------------------|
| stream start (`response.created`)          | `Calling Orchestrator`   |
| function_call `troubleshoot_from_knowledge_base` | `Calling Triage Agent`   |
| function_call `manage_servicenow_incident` | `Calling Incident Agent` |

## Parsing rules for Switch

1. **On `response.created`** (first event of every stream): emit
   `status = "Calling Orchestrator"`. (`response.in_progress` follows; ignore it —
   don't double-emit.)
2. **On `response.output_item.added` where `item.type == "function_call"`**: read
   `item.name`, map via the table, emit the corresponding `status` frame. This is
   the authoritative, earliest handoff signal — use this one.
3. **Fallback** (only if `item.name` is ever absent on `added` in a future SDK/
   server version): use `response.function_call_arguments.done.name`. In the live
   capture BOTH carried the name, so `added` is sufficient today.
4. **Token text** continues to ride `response.output_text.delta` (`event.delta`)
   exactly as today — unchanged. The user-visible answer text is NOT affected by
   these status frames; status is a separate, out-of-band signal.
5. **A single stream can emit multiple handoffs** across turns and even within a
   turn (e.g. deflect turn -> triage; later confirm turn -> incident). Emit one
   status frame per `function_call` `added` event; render the latest as the
   active indicator and clear it when the first `response.output_text.delta`
   with visible text arrives (the orchestrator has started relaying the answer).
6. **Unknown `item.name`**: ignore (future-proofing) — don't emit a bogus label.
7. **End**: on `response.completed`, clear any lingering status indicator.

## Suggested UI SSE `status` frame (Switch owns final shape)

Mirroring the existing token/done/error frame protocol on `POST /api/chat/stream`:

```
data: {"type":"status","label":"Calling Triage Agent","tool":"troubleshoot_from_knowledge_base"}\n\n
```

## Live evidence (event-type counts + tool names, captured 2026-07-09 ~09:00Z)

Deflect-first turn ("my laptop battery drains very fast…"), streamed from the
hosted orchestrator:

```
  1  response.created
  1  response.in_progress
  3  response.output_item.added        <- one is item.type=function_call,
 17  response.function_call_arguments.delta   name=troubleshoot_from_knowledge_base
  1  response.function_call_arguments.done
  3  response.output_item.done
  1  response.content_part.added
119  response.output_text.delta        <- the relayed answer text
  1  response.output_text.done
  1  response.content_part.done
  1  response.completed
```

Two-turn create-ticket flow (streamed), function_call item names observed:

```
TURN1 (new problem)   -> added.item.name = troubleshoot_from_knowledge_base ; args.done.name = troubleshoot_from_knowledge_base
TURN2 (confirm ticket)-> added.item.name = manage_servicenow_incident       ; args.done.name = manage_servicenow_incident
```

**Why:** The UI must show which sub-agent the orchestrator is calling. Because the
hosted MAF orchestrator runs its tools inside the container, the concern was that
handoffs would be invisible to the outer Responses stream. A live stream dump
proved the opposite: Foundry surfaces each tool invocation as a `function_call`
output item with the tool name, so the UI can render handoff status purely from
the existing stream — no orchestrator rebuild, no marker injected into the token
text (which would risk corrupting the user-visible answer). Switch implements the
SSE `status` frame + rendering from this contract.

### 2026-07-09: Agent handoff status chips in the chat UI (SSE `status` frame)

**By:** Switch

**What:** Added a new out-of-band SSE frame `{"type":"status","label":...,"tool":...}`
to `POST /api/chat/stream` that surfaces the orchestrator's handoffs in the chat UI
as "Calling Orchestrator" → "Calling Triage Agent" / "Calling Incident Agent".

- **Live path** (`_live_stream`): derives status purely from the existing hosted
  orchestrator Responses stream — `response.created` → "Calling Orchestrator";
  `response.output_item.added` with `item.type == "function_call"` maps `item.name`
  (`troubleshoot_from_knowledge_base` → Triage, `manage_servicenow_incident` →
  Incident). `response.function_call_arguments.done` is a de-duplicated fallback
  (keyed on call id) used only if `item.name` is absent on `added`. Unknown tool
  names are ignored. No orchestrator or agent code changed (per Trinity's contract).
- **Mock path** (`_mock_stream`): synthesises the same sequence deterministically
  from the mock orchestrator's `route` (triage/incident) so local dev + tests
  exercise the feature offline.
- **Frontend** (`index.html`): renders the latest `status` label as a pulsing chip
  above the reply bubble; clears it on the first visible `token` and on `done`/`error`.
  Non-streaming `/api/chat` fallback is unaffected — chips are a streaming-only
  enhancement that degrades gracefully.

**Why:** The UI needed to show which sub-agent the orchestrator is handing off to.
Trinity's live capture proved these signals already exist in the outer Responses
stream, so this is a pure UI + SSE change with no risk to the orchestrator or the
user-visible answer text. Live-verified on app-ztk6zx5aedqtc: a new-problem turn
emits Orchestrator→Triage; a confirm-ticket turn emits Orchestrator→Incident
(created INC0010051).


### 2026-07-09: gpt-5.5 model migration BLOCKED on zero subscription quota (swedencentral)

**By:** Tank

**What:** Halted the gpt-4o -> gpt-5.5 infra migration. Did NOT create the live
`gpt-5.5` deployment, did NOT change `infra/main.bicep` / `infra/main.parameters.json` /
`infra/modules/foundry.bicep` model defaults, and did NOT flip the azd env
(`AZURE_OPENAI_CHAT_MODEL` / `AZURE_OPENAI_CHAT_DEPLOYMENT` remain `gpt-4o`). The
existing live `gpt-4o` (GlobalStandard, cap 30, Succeeded) and `text-embedding-3-large`
deployments are untouched, so currently-live agents keep working. Migration is parked
until a quota increase is granted.

**Why:** Quota check FIRST (per task guard rail) proved the subscription has ZERO
TPM quota for gpt-5.5 in swedencentral:
- `az cognitiveservices usage list -l swedencentral`:
  `OpenAI.GlobalStandard.gpt-5.5` -> currentValue 0.00, **limit 0.00**;
  `OpenAI.DataZoneStandard.gpt-5.5` -> **limit 0.00**. Every other gpt-5.x family
  has a non-zero limit (1000-3000); gpt-5.5 specifically has none.
- The model IS in the account catalog: `gpt-5.5` v`2026-04-24`, format OpenAI,
  SKUs `GlobalStandard, DataZoneStandard, DataZoneProvisionedManaged,
  GlobalProvisionedManaged` (confirms no plain `Standard` SKU for gpt-5.5).
- Authoritative live probe (capacity 1) failed:
  `(InsufficientQuota) This operation require 1 new capacity ... which is bigger
  than the current available capacity 0 ... quota limit is 0 for
  One Thousand Tokens Per Minute - gpt-5.5 - GlobalStandard.` The failed probe
  created no resource.

Because the largest capacity that fits is 0, nothing can be provisioned. Flipping
the Bicep defaults + azd env to `gpt-5.5` with no deployment behind them would break
Tank's `azd up` one-click contract (next `azd provision` fails InsufficientQuota) and
would point Trinity's agents/UI at a non-existent deployment mid-migration. So the
coupled changes are intentionally NOT applied.

**Unblock (action required by abKrazy / subscription owner):** Request a quota
increase for `OpenAI.GlobalStandard.gpt-5.5` in **swedencentral** on subscription
`f7bd143a-73f9-4467-82d5-01ecc49d1610` (account `aif-ztk6zx5aedqtc`, RG
`rg-ithelpdesksc`). Target >= 30 (30K TPM) to match the current gpt-4o capacity;
smaller is acceptable if that is all that is granted. Do it via the Azure AI Foundry
portal (Management center -> Quota) or an Azure support "Service and subscription
limits (quotas)" request for Cognitive Services / OpenAI. Once granted, re-run this
task and Tank will: pin `version: '2026-04-24'` with
`versionUpgradeOption: 'NoAutoUpgrade'`, set capacity to the granted value, flip the
Bicep + parameters + azd env defaults, create the live `gpt-5.5` deployment, then
hand off to Trinity.

**Handoff to Trinity:** DO NOT repoint agents/UI yet. There is no `gpt-5.5`
deployment and it cannot be created until quota is granted. Agents remain on `gpt-4o`.

### 2026-07-09: gpt-4o -> gpt-5.4 chat model migration (infra half) provisioned

**By:** Tank

**What:** Completed the infra half of the chat-model migration to `gpt-5.4`
(the latest GPT-5 that HAS quota, chosen after the gpt-5.5 attempt was blocked on
zero quota — see `tank-gpt55-model-deployment.md`). Concretely:
- Created the live `gpt-5.4` deployment on account `aif-ztk6zx5aedqtc`
  (RG `rg-ithelpdesksc`, swedencentral): model `gpt-5.4` v`2026-03-05`, format
  OpenAI, SKU `GlobalStandard`, capacity **30**. `deployment show` =
  provisioningState **Succeeded**.
- `infra/main.bicep`: `chatModelDeploymentName` + `chatModelName` defaults ->
  `'gpt-5.4'`.
- `infra/main.parameters.json`: `AZURE_OPENAI_CHAT_DEPLOYMENT` and
  `AZURE_OPENAI_CHAT_MODEL` defaults -> `gpt-5.4`.
- `infra/modules/foundry.bicep` chatDeployment: kept `sku.name:'GlobalStandard'`,
  capacity 30, PINNED `model.version:'2026-03-05'`, and set
  `versionUpgradeOption:'NoAutoUpgrade'` (a pinned version is incompatible with
  auto-upgrade). Embedding deployment (`text-embedding-3-large`) untouched.
- azd env: `AZURE_OPENAI_CHAT_MODEL=gpt-5.4`, `AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-5.4`.
- Left the live `gpt-4o` (GlobalStandard, Succeeded) and `text-embedding-3-large`
  deployments in place so currently-live agents keep working mid-migration.

**Why:** Quota check FIRST (per guard rail) proved gpt-5.4 HAS quota in
swedencentral: `OpenAI.GlobalStandard.gpt-5.4` -> currentValue 100, limit 3000
(available ~2900 TPM). Chose capacity = min(30, available) = **30** to match the
current gpt-4o capacity. `az bicep build --file infra/main.bicep` succeeds (only
pre-existing lint warnings). Did NOT republish agents / rebuild the orchestrator /
redeploy the UI — that is Trinity's half and required the deployment to exist first.

**Handoff to Trinity:** The `gpt-5.4` deployment is live (Succeeded, GlobalStandard,
cap 30) and the azd env now points chat model + deployment at `gpt-5.4`. Safe to
repoint the agents/UI and rebuild/redeploy. gpt-4o remains live until you cut over.

### 2026-07-09: Separate gpt-5.4-mini deployment for the triage agent

**By:** Tank

**What:** Provisioned a SEPARATE Foundry chat model deployment named `gpt-5.4-mini`
(model `gpt-5.4-mini`, version `2026-03-17`, SKU `GlobalStandard`, capacity 30) on
account `aif-ztk6zx5aedqtc` / RG `rg-ithelpdesksc` in swedencentral — live now,
provisioningState Succeeded. Added Bicep plumbing so a fresh `azd up` reproduces it:
new params `triageChatModelDeploymentName` / `triageChatModelName` in `infra/main.bicep`
(threaded into the foundry module), a second chat deployment resource
`triageChatDeployment` in `infra/modules/foundry.bicep` (pinned version `2026-03-17`,
`NoAutoUpgrade`, `dependsOn: [chatDeployment, embeddingDeployment]` to serialize
against parallel Cognitive Services deployment writes), corresponding
`main.parameters.json` entries, and a new output
`AZURE_OPENAI_TRIAGE_CHAT_DEPLOYMENT`. azd env now carries
`AZURE_OPENAI_TRIAGE_CHAT_DEPLOYMENT=gpt-5.4-mini` and
`AZURE_OPENAI_TRIAGE_CHAT_MODEL=gpt-5.4-mini`.

**Why:** The user wants the triage agent to run on the latest GPT mini model,
separate from the orchestrator/incident agents (which stay on gpt-5.4). Keeping it
a distinct deployment lets Trinity wire the triage Prompt Agent to the mini model
without touching the shared chat deployment. Quota was confirmed available
(OpenAI.GlobalStandard.gpt-5.4-mini limit 1000, current 0 → capacity min(30,1000)=30).
The gpt-5.4, gpt-4o, and text-embedding-3-large deployments were left untouched.

### 2026-07-09: Cut all three agents + UI from gpt-4o to gpt-5.4 (reasoning model)
**By:** Trinity
**What:** Repointed the hardcoded `gpt-4o` runtime fallbacks to `gpt-5.4` in the
orchestrator (`src/orchestrator/main.py`), the UI blocking + streaming paths
(`src/helpdesk/ui/app.py`), plus README current-state docs and test fixtures.
Republished the triage (v4) and incident (v5) Prompt Agents and registered a new
hosted orchestrator version (v5) on image
`acrztk6zx5aedqtc.azurecr.io/it-helpdesk-orchestrator:gpt54-20260709030100`. Set
App Service app setting `AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-5.4` and shipped the UI
(`azd deploy ui`). Everything keys off the chat **deployment name** (`gpt-5.4`),
which is the model handle — the agent builders already set `model=chat_deployment`.
**Why:** The `gpt-5.4` deployment is now live on `aif-ztk6zx5aedqtc`; the old
`gpt-4o` fallbacks risked silently pinning the old model. gpt-5.4 is a reasoning
model: confirmed no `temperature`/`max_tokens`/`top_p` are passed anywhere in the
call path (would be rejected), and the UI stream handler already filters to
`response.output_text.delta` and ignores unknown events, so reasoning events don't
break streaming. Live regression on gpt-5.4 passed all five cases (deflect-first
KB, create-on-confirm INC0010049, status-only, cold sys_id-resolving urgency
update to 1-High, streaming 182 token frames). App Insights spans on
cloud_RoleName `it-helpdesk-orchestrator` show `gen_ai.request.model=gpt-5.4` /
`gen_ai.response.model=gpt-5.4-2026-03-05`, proving the cutover took effect.


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

### 2026-07-08: Render friendly ServiceNow incident state labels
**By:** Trinity
**What:** Incident user-facing replies now map ServiceNow incident state codes to friendly labels, while preserving raw `state` data and adding `state_label` alongside `urgency_label`.
**Why:** ServiceNow Table API returns incident state as numeric codes; showing labels like `New` and `In Progress` makes lookup results readable without breaking fallback behavior for novel state codes.

### 2026-07-08: Deflection-first triage for create-intent requests
**By:** Trinity
**What:** When a user asks to create/file/open/log a ticket and triage finds confident KB troubleshooting steps, the Orchestrator now returns those steps plus a confirmation offer and does not create the incident until a follow-up confirmation. The UI sends the last 10 prior chat turns as `{role, content}` history so confirmations can be tied to the previous offer and the original problem can be used for the incident short description.
**Why:** The core product spec requires KB triage before incident creation. A stable offer marker in the assistant reply keeps confirmation detection deterministic and mock-safe while avoiding accidental ticket creation from a bare "yes" without a prior offer.

### 2026-07-08: Use semantic rerankerScore for live triage confidence
**By:** Trinity
**What:** Live Azure AI Search triage confidence now uses `@search.rerankerScore` with a 2.0 threshold when semantic ranking is available, while mock/local search keeps the existing normalized keyword score threshold of 0.25. Search indexing also carries each article's full `resolution_steps` on every chunk so deflection shows clean steps regardless of which chunk matched.
**Why:** Hybrid vector+keyword `@search.score` is an RRF ordering score around 0.01-0.03, so it cannot safely drive the live confidence gate calibrated for local 0-1 scores. Semantic reranker scores are on a 0-4 scale and are query-comparable enough for the deflection gate, with the local fallback preserving mock behavior.

## 2026-07-08 Phase 1 — Native Foundry Prompt Agents

### 2026-07-08T19:19:33-05:00: Phase 1 native Foundry infrastructure and RBAC
**By:** Tank
**What:** Phase 1 infrastructure now provisions the native Foundry tool substrate: Basic ACR with admin disabled and `AcrPull` for the Foundry project managed identity; App Insights/Log Analytics; APIM MCP URL/key outputs; `AZURE_OPENAI_ENDPOINT`; Search system identity and data-plane/control-plane RBAC for app, Foundry project/account, user, and Search managed identities; and `Cognitive Services OpenAI User` for the Search identity so the integrated vectorizer can call Foundry OpenAI.
**Why:** Fresh `azd up` deployments must reproduce the live working state without portal fixes. Native Prompt Agents, Azure AI Search grounding, integrated vectorization, and the Phase 2 hosted orchestrator all need deterministic resources, outputs, and managed-identity grants from Bicep.

### 2026-07-08T19:19:33-05:00: Phase 1 native Prompt Agent tool wiring
**By:** Trinity
**What:** `create_foundry_agents(project_endpoint, chat_deployment, search_endpoint, apim_mcp_url, apim_key)` creates the native-tool Prompt Agents `it-helpdesk-triage` and `it-helpdesk-incident`; the orchestrator remains deferred to Phase 2 as a MAF Hosted Agent. Triage uses the native Azure AI Search Knowledge tool with the auto-provisioned `it-helpdesk-search-conn`, references existing project connections only, and relies on the integrated `text-embedding-3-large` vectorizer at 1536 dimensions for the KB index.
**Why:** Deflection-first triage must be grounded by Foundry's native Azure AI Search tool and citations, not the previous deterministic reranker gate. Reusing Foundry-created connections avoids unsupported SDK connection creation paths and preserves the Phase 2 hosted-agent boundary.

### 2026-07-08T19:19:33-05:00: Phase 1 native APIM MCP incident agent
**By:** Switch
**What:** The incident Prompt Agent uses the native Foundry `MCPTool` against APIM's locked MCP endpoint `${gateway}/servicenow/mcp` with inline `Ocp-Apim-Subscription-Key` headers. This connectionless header path supersedes the earlier Custom-Keys connection plan. Postprovision sources the APIM subscription key from the environment first and falls back to ARM `listSecrets` at runtime.
**Why:** The installed `azure-ai-projects` connection surface cannot create/upsert the needed connection reliably, while inline MCP headers are the verified live path. Runtime ARM fallback keeps one-click provisioning resilient without persisting APIM keys in source.

### 2026-07-08T19:19:33-05:00: Phase 1 live verification
**By:** Tank, Switch, Trinity
**What:** Phase 1 was verified live: Bicep compiles; triage performs deflection-first RAG with grounded citations; the incident MCP path returned real ServiceNow incident `INC0010036` through APIM -> ServiceNow; changes were committed as `3c7131b` and pushed to `abKrazy/ITHelpdesk`.
**Why:** The team needed proof that the native Foundry Prompt Agent path, integrated-vectorizer search grounding, and connectionless APIM MCP incident flow work end-to-end before moving to Phase 2.

### 2026-07-09T02-31-26: Hosted agents are invoked via their dedicated endpoint, not agent_reference; Foundry AI Search connection must be created control-plane in Bicep
**By:** Coordinator
**What:** Hosted agents are invoked via their dedicated endpoint, not agent_reference; Foundry AI Search connection must be created control-plane in Bicep
**References:** src/helpdesk/ui/app.py, infra/modules/foundry.bicep, infra/main.bicep
**Why:** Two Phase-2 architecture facts verified live (swedencentral):

1. Hosted Agent invocation. A Foundry Hosted Agent (MAF container) CANNOT be called via client.responses.create(extra_body={agent_reference}) — that is the Prompt Agent contract and returns HTTP 400 "Hosted agents can only be called through the agent endpoint". Use AIProjectClient.get_openai_client(agent_name="it-helpdesk-orchestrator"), which points the OpenAI client at .../agents/{name}/endpoint/protocols/openai/, then call responses.create(model=..., input=conversation) with NO agent_reference. Fixed in src/helpdesk/ui/app.py.

2. AI Search connection is control-plane only. azure-ai-projects 2.3.0 ConnectionsOperations exposes only get/get_default/list — NO create. The native triage Knowledge Base tool needs a project AI Search connection, so it must be created in Bicep (Microsoft.CognitiveServices/accounts/projects/connections, category CognitiveSearch, authType AAD, isSharedToAll true). Postprovision only reads it back via ensure_search_connection. Fixed in infra/modules/foundry.bicep + wired from main.bicep.

Live end-to-end verified: deflect-first triage, incident creation (INC0010039 -> Desktop Support via APIM MCP), and follow-up ticket-status routing to the incident agent. Committed 8ed48b7, pushed to abKrazy/ITHelpdesk master.

### Phase 2: Hosted Orchestrator deploy = CONTAINER path (not code-ZIP)
**By:** Squad (Coordinator) for @abKrazy
**What:** Deploy the MAF Foundry Hosted Orchestrator via the **container** path — `az acr build` (server-side, no local Docker) pushes the image to our provisioned ACR, then `AIProjectClient.agents.create_version(agent_name="it-helpdesk-orchestrator", definition=HostedAgentDefinition(container_configuration=ContainerConfiguration(image=...)))`.
**Why:** In azure-ai-projects 2.3.0 the code-ZIP path is only exposed via the PRIVATE method `_create_version_from_code` (leading underscore, undocumented multipart contract) — fragile for an accelerator. The container path uses the PUBLIC, stable `create_version` API, aligns with Tank's ACR AcrPush/AcrPull RBAC already provisioned, and `az acr build` needs no Docker daemon on hackathon laptops (runs server-side, invoked from the postprovision shell hook to avoid the Windows az.cmd-from-python issue).
**Verified SDK shape (2.3.0):** HostedAgentDefinition(kind="hosted", cpu:str, memory:str, environment_variables:dict, container_configuration=ContainerConfiguration(image:str), protocol_versions=[ProtocolVersionRecord(protocol="responses", version=<tbd-live>)]). Enums: AgentEndpointProtocol.RESPONSES="responses"; CodeDependencyResolution in {bundled, remote_build}.
**Open live item:** exact responses protocol `version` string is a Foundry contract — discover on first live deploy and pin.

### 2026-07-08: Phase 2 orchestrator = MAF Hosted Agent (agent-framework-foundry-hosting)

**By:** Squad (Coordinator), for @abKrazy
**What:** The custom Orchestrator is rebuilt as a Microsoft Agent Framework agent
deployed as a **Foundry Hosted Agent** (container → ACR → Foundry Agent Service),
per the canonical doc:
https://learn.microsoft.com/en-us/agent-framework/hosting/foundry-hosted-agent?pivots=programming-language-python

Verified package/API reality in the venv:
- `agent-framework-foundry` 1.10.0 (GA), `agent-framework-orchestrations` 1.0.0 (GA) already installed.
- `agent-framework-foundry-hosting` is **pre-release only** (`1.0.0a260630`); installed via `pip install --pre`. Hosted Agents are Preview.
- Host pattern: `ResponsesHostServer(agent, store=...)` → exposes `/responses` on port 8088; `.run()`.
- `Agent(client, instructions, *, name, tools=[...])`; `@tool(approval_mode="never_require")`.
- `FoundryChatClient(project_endpoint=, model=, credential=DefaultAzureCredential())`.
- Runtime auto-injects `FOUNDRY_PROJECT_ENDPOINT`, `AZURE_AI_MODEL_DEPLOYMENT_NAME`, `APPLICATIONINSIGHTS_CONNECTION_STRING`.

**Why:** Spec requires the Orchestrator to be a custom MAF agent deployed as a Hosted
Agent, invoking the Triage + Incident **Prompt Agents** (which stay native Foundry
Prompt Agents). The orchestrator's tools call the sub-agents via the Responses API
using `agent_reference`, holding conversation context so routing follow-ups
(e.g. "what's the ticket priority?") go to the incident agent, not triage.

### 2026-07-09T03-29-52: ServiceNow MCP tool is now a Foundry project connection (RemoteTool), not an inline-keyed MCPTool
**By:** Coordinator
**What:** ServiceNow MCP tool is now a Foundry project connection (RemoteTool), not an inline-keyed MCPTool
**References:** infra/modules/mcp-connection.bicep, infra/main.bicep, src/helpdesk/agents/definitions/incident_agent.py
**Why:** The Incident Prompt Agent's MCP tool used to carry the APIM subscription key inline in MCPTool headers — it never appeared in the Foundry portal Connections/Tools tab and the key sat in plaintext in the agent definition.

Fix (committed 62f0d74, pushed to abKrazy/ITHelpdesk master):
- New Bicep module infra/modules/mcp-connection.bicep creates a control-plane RemoteTool project connection (authType CustomKeys, category RemoteTool, target=APIM MCP url, key under Ocp-Apim-Subscription-Key). Runs after apim+foundry; key from apim.outputs.mcpSubscriptionKey. The azure-ai-projects data-plane SDK has no connection-create API, so connections MUST be control-plane.
- main.bicep wires the module and emits AZURE_AI_MCP_CONNECTION_ID (full ARM id), AZURE_AI_MCP_CONNECTION_NAME, AZURE_AI_SEARCH_CONNECTION_NAME.
- build_incident_definition takes mcp_connection_id (dropped apim_key); MCPTool uses project_connection_id, no headers.
- postprovision.py passes mcp_connection_id from env; removed dead resolve_apim_key/_derive_apim_service_name.

Verified live in swedencentral: connections.list() shows both search and servicenow-apim-mcp (RemoteTool); incident tool references project_connection_id with zero plaintext key; deflect-first triage + incident creation (INC0010041) work end-to-end. Triage AI Search was already a CognitiveSearch project connection — confirmed correct.

### 2026-07-08: Phase 2 hosted-agent ACR and agent-identity RBAC
**By:** Tank
**What:** `infra/modules/acr.bicep` grants the Foundry project managed identity `AcrPull` (`7f951dda-4ed3-4680-a7ca-43fe172d538d`) at the Basic ACR scope for hosted-agent runtime image pulls, enables ACR `azureADAuthenticationAsArmPolicy`, and conditionally grants the deploying `principalId` `AcrPush` (`8311e382-0749-4cb8-b61a-304f252e45ec`) at the same scope for hosted-agent image pushes.

**Why:** The hosted-agent permissions reference recommends Container Registry Repository Reader/Writer but also lists `AcrPull`/`AcrPush` as valid ACR-scope alternatives. Because this accelerator provisions Basic ACR, `AcrPull` keeps the existing Phase 1 path compatible while still satisfying runtime pulls by the Foundry project managed identity. `AZURE_PRINCIPAL_ID` is already an optional azd-bound parameter, so we can grant `AcrPush` without inventing a new deployer parameter; if it is empty, the deployer must already have push rights through subscription/resource-group Owner or another ACR-scoped assignment. The preferred hackathon path remains ACR remote build/hosted-agent packaging (azd/Foundry packages and pushes the image to ACR), so local Docker should not be required.

**What:** No Bicep role assignment is created for the hosted orchestrator agent's auto-provisioned identity.

**Why:** A Foundry hosted agent automatically gets its own agent blueprint and Entra identity when the agent is created, which happens after provisioning. The permissions reference says agents have implicit access to core capabilities within their own project, including model inferencing through the project endpoint and session storage, so no provision-time Bicep action is possible or needed for the standard same-project orchestrator-to-sub-agent pattern. If a deployment hits an advanced explicit-access requirement after the agent identity exists, assign Foundry User (`53ca6127-db72-4b80-b1b0-d745d6d5456d`) on the project scope:

```powershell
az role assignment create --assignee "<HOSTED_AGENT_PRINCIPAL_ID>" --role "53ca6127-db72-4b80-b1b0-d745d6d5456d" --scope "/subscriptions/<SUBSCRIPTION_ID>/resourceGroups/<RESOURCE_GROUP>/providers/Microsoft.CognitiveServices/accounts/<FOUNDRY_ACCOUNT_NAME>/projects/<FOUNDRY_PROJECT_NAME>"
```

### 2026-07-08: Foundry IQ knowledge base = Search agentic-retrieval KB + MCP grounding (not a managed Index)

**By:** Trinity

**What:** The triage agent (`it-helpdesk-triage`) now grounds on a **Foundry IQ
knowledge base**, which is an **Azure AI Search agentic-retrieval `knowledgeBase`**
(plus a `searchIndex` `knowledgeSource` over the existing `it-helpdesk-kb` index),
NOT a managed project `Index` (`AISearchIndexResource`) and NOT an inline
`AzureAISearchTool`. Both prior approaches were wrong — neither produced a Foundry
IQ knowledge base.

The agent grounds via an **MCP tool**, the same RemoteTool project-connection
pattern the incident agent uses for the ServiceNow APIM MCP server:

- Data-plane (`SearchIndexClient`, preview `azure-search-documents`):
  `create_or_update_knowledge_source(SearchIndexKnowledgeSource(...))` +
  `create_or_update_knowledge_base(KnowledgeBase(...))`. Use **Minimal** reasoning
  effort + **EXTRACTIVE_DATA** output mode so **no LLM** is required in the KB —
  any higher reasoning effort/answer synthesis needs a `models` (Azure OpenAI)
  entry or retrieval fails with "A Knowledge Base model must be specified".
  KS = `it-helpdesk-kb-source`, KB = `it-helpdesk-kb`, semantic config `kb-semantic`.
- Control-plane: a **RemoteTool** project connection (`it-helpdesk-kb-mcp`) with
  `authType: ProjectManagedIdentity`, `audience: https://search.azure.com/`,
  `category: RemoteTool`, `metadata: { ApiType: Azure }`, target
  `{search}/knowledgebases/{kb}/mcp?api-version=2026-05-01-preview`. The project's
  **system-assigned managed identity** needs **Search Index Data Reader** on the
  search service (already granted by `search-rbac.bicep`).
- Agent: `MCPTool(server_label="knowledge-base", server_url={kb mcp url},
  require_approval="never", allowed_tools=["knowledge_base_retrieve"],
  project_connection_id="it-helpdesk-kb-mcp")` inside `PromptAgentDefinition`.
  Reference the connection by **NAME** (the portal links connections by name).
  `knowledge_base_retrieve` is the only MCP tool a Search KB exposes today.

Codified in `infra/modules/kb-connection.bicep` (+ wired in `infra/main.bicep`,
output `AZURE_AI_KB_CONNECTION_NAME`), `src/helpdesk/agents/definitions/triage_agent.py`
(`ensure_kb_knowledge_base`, `kb_mcp_url`, `build_triage_definition`),
`src/helpdesk/agents/setup.py`, and `scripts/postprovision.py`.

**Why:** The user reported the triage agent still connected to the Search index as
a raw tool and that no Foundry IQ knowledge base existed. A managed project Index
is not a Foundry IQ knowledge base. Live-verified in `swedencentral` (Basic tier,
agentic-retrieval supported): a query "my laptop is running slow…" invoked
`knowledge_base_retrieve` and returned KB steps **with citations** 【…†source】 plus
the Desktop Support assignment group; the incident/APIM MCP handoff stayed intact.


---

### 2026-07-09: Hosted orchestrator must relay triage steps VERBATIM (user can't see tool output)

**By:** Trinity

**What:** Hardened `ORCHESTRATOR_INSTRUCTIONS` in `src/orchestrator/main.py` (the
live Foundry Hosted Agent) with a top-priority RELAY VERBATIM rule: the user only
ever sees the orchestrator's own reply, never the tool/sub-agent outputs, so the
orchestrator MUST paste the `troubleshoot_from_knowledge_base` tool's full answer
— every numbered step and any 【…†source】 citation — verbatim, and must NEVER say
"I've shared/provided the steps" without including their text. The deflect-first,
create-on-confirmation, and follow-up-to-incident rules are unchanged. Aligned the
same "relay verbatim, user can't see tool output" guidance into
`src/helpdesk/agents/prompts.py` `ORCHESTRATOR_INSTRUCTIONS` for consistency.
Rebuilt the container via `az acr build` (unique tag) and published a NEW hosted
orchestrator version (v2, latest/default) via
`AIProjectClient.agents.create_version` on env `ithelpdesksc` (swedencentral).

**Why:** The hosted orchestrator was non-deterministic — it sometimes summarized
("I've provided some troubleshooting steps…") instead of pasting the triage steps,
so the UI showed a confirmation offer with NO steps. Forcing verbatim relay makes
the numbered steps appear every time. Live re-verification against the dedicated
hosted-agent endpoint (exactly as the UI calls it): 8/8 trials
("please file a ticket" ×6 + plain ×2) showed the restart / disk space / Task
Manager steps with deflect-first preserved (no ticket created on the first turn);
full happy path passed (steps → "go ahead" → INC0010042 created → status query
routed to the incident tool). NOTE: reusing an image tag does NOT bump the hosted
version — use a unique tag to force a fresh pull + new version.


---

### 2026-07-09: Incident agent resolves INC number -> sys_id before update/lookup

**By:** Trinity
**What:** Added an explicit two-step "Resolving an incident by its INC number"
section to both the live incident Prompt Agent instructions
(`src/helpdesk/agents/definitions/incident_agent.py`) and the mock/reference
instructions (`src/helpdesk/agents/prompts.py`). The agent must first LIST/query
the `incident` table with `sysparm_query=number={INC}` (fields
`sys_id,number,short_description,urgency,state,assignment_group`) to resolve the
`sys_id`, then apply `getRecord`/`patchRecord` on `incident/{sys_id}`. It reports
"does not exist" ONLY when the list query returns an empty `result`. Urgency
mapping (low=3, medium=2, high=1) preserved. Republished the live
`it-helpdesk-incident` Prompt Agent (v4) via `create_version`.
**Why:** The ServiceNow APIM MCP spec exposes only sys_id-keyed get/patch/put/delete
operations plus a list/query. The agent was passing the INC `number` field where a
`sys_id` path key is required, so ServiceNow returned not-found/restricted on every
follow-up update or status lookup by number (user-reported live bug: INC0010043
"does not appear to exist"). No `allowed_tools` change was needed — the MCPTool
already exposes `queryTable`; the bug was purely instructional. Live-verified on
env `ithelpdesksc`: created INC0010044, updated urgency to medium in a separate
call (sys_id resolved + patched), and status-by-number returned the record with
urgency 2.


---

### 2026-07-09: Foundry project AppInsights connection + hosted-orchestrator telemetry env vars
**By:** Tank
**What:** Closed both telemetry infra gaps so Foundry tracing works end-to-end.
1. Added a control-plane **AppInsights** project connection in `infra/modules/foundry.bicep`
   (`Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview`,
   `category: 'AppInsights'`, `authType: 'ApiKey'`, `target` = App Insights resource ID,
   `credentials.key` = App Insights connection string, `isSharedToAll: true`,
   `metadata: { ApiType: 'Azure', ResourceId: <App Insights resource ID> }`). Threaded new
   params `applicationInsightsResourceId` + `applicationInsightsConnectionString` (@secure) from
   `main.bicep` (`monitoring.outputs.*`), and emit `AZURE_AI_APPINSIGHTS_CONNECTION_NAME`.
   Created it **live** in env `ithelpdesksc` on project `proj-ztk6zx5aedqtc` as
   `proj-ztk6zx5aedqtc-appinsights` (ARM PUT) — verified it lists as category AppInsights,
   `isDefault: true`.
2. Hosted orchestrator container now receives `APPLICATIONINSIGHTS_CONNECTION_STRING`,
   `OTEL_SERVICE_NAME=it-helpdesk-orchestrator`, and
   `AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED=true` via
   `helpdesk.agents.setup.create_hosted_orchestrator` (new optional
   `applicationinsights_connection_string` param, read from env, never hardcoded);
   `scripts/postprovision.py` passes `env("APPLICATIONINSIGHTS_CONNECTION_STRING")`.
   Did NOT redeploy the hosted orchestrator — Trinity owns the single redeploy after adding
   instrumentation code.
**Why:** App Insights + Log Analytics were provisioned but nothing linked them to the Foundry
project, so the portal Tracing tab was empty and `AIProjectClient(...).telemetry.get_connection_string()`
couldn't resolve a connection; and the hosted orchestrator container had no App Insights connection
string to export traces. The AppInsights connection shape was verified against
Azure-Samples/foundry-hosted-agentframework-demos (`infra/core/ai/ai-project.bicep`). Connections
must be created control-plane because azure-ai-projects 2.x has no data-plane connection-create API.


---

### 2026-07-09: Hosted orchestrator emits OpenTelemetry traces to App Insights (Foundry Tracing tab)
**By:** Trinity
**What:** Instrumented the MAF hosted orchestrator (`src/orchestrator/main.py`)
with OpenTelemetry. At startup `configure_telemetry()` resolves the App Insights
connection string from `APPLICATIONINSIGHTS_CONNECTION_STRING` (falling back to
`AIProjectClient(...).telemetry.get_connection_string()`), calls
`azure.monitor.opentelemetry.configure_azure_monitor(connection_string=...)`, and
enables Microsoft Agent Framework's built-in GenAI instrumentation
(`agent_framework.observability.enable_instrumentation` +
`enable_sensitive_telemetry` gated on
`AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED`). Setup is guarded so it no-ops
(never raises) when no connection string is available (local/mock). Each sub-agent
handoff in `_invoke_prompt_agent` is wrapped in an explicit `invoke_agent {name}`
span with `gen_ai.*` attributes so the triage/incident handoffs are visible.
Added `azure-monitor-opentelemetry` to `src/orchestrator/requirements.txt`.
Cloud role name = `it-helpdesk-orchestrator` via `OTEL_SERVICE_NAME`.

**IMPORTANT platform change:** Foundry now **reserves and auto-injects**
`APPLICATIONINSIGHTS_CONNECTION_STRING` for hosted agents (same as `FOUNDRY_*` /
`AGENT_*`). Passing it to `AIProjectClient.agents.create_version` fails with
`invalid_payload ... reserved for platform use`. So
`helpdesk.agents.setup.create_hosted_orchestrator` no longer sets it (or accepts
the `applicationinsights_connection_string` param); it only sets the non-reserved
knobs `OTEL_SERVICE_NAME` and `AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED`.
`postprovision.py` updated accordingly.

**Why:** The spec requires the orchestrator's traces to flow to the App Insights
the Foundry project is connected to and appear in the portal Tracing tab.
Live-verified against env `ithelpdesksc` (swedencentral): published hosted
orchestrator **v3** (image `it-helpdesk-orchestrator:otel-20260709005349`), drove
real requests, and confirmed spans in App Insights `appi-ztk6zx5aedqtc` (Log
Analytics workspace `log-ztk6zx5aedqtc`): `invoke_agent it-helpdesk-orchestrator`,
`invoke_agent it-helpdesk-triage`, `invoke_agent it-helpdesk-incident`,
`execute_tool troubleshoot_from_knowledge_base`,
`execute_tool manage_servicenow_incident`, and `chat gpt-4o` under
`cloud_RoleName == "it-helpdesk-orchestrator"`. Telemetry is additive only — the
verbatim-relay instructions, deflect-first flow, and tool wiring are unchanged.


---

### 2026-07-09: Chat UI streams tokens over SSE with a "Thinking…" indicator

**By:** Switch

**What:** Added `POST /api/chat/stream` to the App Service UI (`src/helpdesk/ui/app.py`)
— a `text/event-stream` (SSE) `StreamingResponse` that emits one frame per line
`data: {json}\n\n`. Frame protocol: `{"type":"token","text":...}` per delta,
a terminal `{"type":"done","route":[...]}`, and on failure a structured
`{"type":"error","text":...,"error":...}` frame (never a bare HTTP 500). The
live path calls `client.responses.create(..., stream=True)` and forwards
`response.output_text.delta` deltas, ending on `response.completed`; unknown
event types are skipped defensively. The blocking sync stream is driven on a
worker thread via an `asyncio.Queue` so the event loop can flush each frame.
The mock path chunks the in-process orchestrator's full reply by word so tests
and offline runs exercise the same incremental path. `index.html` shows an
animated "Thinking…" bubble on submit, clears it on the first token, appends
tokens incrementally (`white-space: pre-wrap`, auto-scroll), renders the route
on `done`, and falls back to `POST /api/chat` if the stream fails to start.
The original `/api/chat` endpoint is unchanged (fallback + existing tests).

**Why:** The blocking `POST /api/chat` left users staring at nothing until the
whole answer landed. Streaming + a Thinking indicator gives immediate feedback
and progressive rendering. SSE-over-fetch (POST + `ReadableStream` reader) was
chosen over `EventSource` because we send a JSON body. The structured terminal
`error` frame preserves the "client can always parse a response" contract.
Verified live against the hosted `it-helpdesk-orchestrator` (gpt-4o): 148
incremental token frames for "my laptop is running slow", and confirmed the
deployed App Service (`app-ztk6zx5aedqtc.azurewebsites.net`) streams end-to-end.
UI-only change; no orchestrator/triage/incident agent or infra changes.


---

### 2026-07-09: Orchestrator classifies intent FIRST — status/lookup/update skips triage/KB

**By:** Trinity

**What:** Added a `CLASSIFY INTENT FIRST` section as the leading rule in
`ORCHESTRATOR_INSTRUCTIONS` (`src/orchestrator/main.py`, hosted MAF agent) and
aligned `src/helpdesk/agents/prompts.py` `ORCHESTRATOR_INSTRUCTIONS` (mock +
reference path). The orchestrator now routes on user intent before anything else:
(A) a NEW technical problem / "how do I…" / symptom report (including "open a
ticket for this new problem") follows the unchanged DEFLECT FIRST flow —
`troubleshoot_from_knowledge_base` first, steps pasted verbatim, then offer a
ticket; (B) any ticket status / lookup / priority / urgency / assignment-group
question or field update, or any reference to an existing INC number for a
read/update, goes straight to `manage_servicenow_incident` ONLY and NEVER calls
the knowledge base. DEFLECT FIRST is now explicitly scoped to intent (A). Concrete
(B) examples that must skip triage are baked into the prompt. Updated
`tests/test_orchestrator_hosted.py::test_instructions_encode_routing_rules` to
assert the new guidance and that CLASSIFY INTENT precedes DEFLECT FIRST. All
existing rules (RELAY VERBATIM, CREATE ONLY ON CONFIRMATION,
follow-up-about-existing-ticket → incident, never invent numbers/statuses/KB
content) are intact. No tool wiring or telemetry changed.

Rebuilt the hosted-agent container under a UNIQUE tag
(`it-helpdesk-orchestrator:intent-20260709021807`) via `az acr build` and
published hosted orchestrator **v4** via `AIProjectClient.agents.create_version`
(reusing `helpdesk.agents.setup.create_hosted_orchestrator`). Verified live on env
`ithelpdesksc` (swedencentral) by driving the dedicated agent endpoint exactly as
the UI does (`project.get_openai_client(agent_name="it-helpdesk-orchestrator")` →
`client.responses.create`). App Insights `execute_tool` spans (`appi-ztk6zx5aedqtc`,
cloud_RoleName `it-helpdesk-orchestrator`) prove routing:
`troubleshoot_from_knowledge_base` fired EXACTLY once (07:22:28Z, the "my laptop is
slow" new-problem case), while the priority-check, cold urgency-update, and cold
status cases on INC0010047 each fired `manage_servicenow_incident` ONLY (07:23:06,
07:23:20, 07:23:43Z) — triage/KB did NOT fire for any status/update intent.

**Why:** The live orchestrator (v3) misrouted ticket status/lookup/update requests
into the deflect-first KB path because the prompt led with "DEFLECT FIRST … for ANY
technical problem" and had no explicit up-front intent-classification step. The
knowledge base cannot answer questions about a specific ticket, so KB retrieval for
a status/update intent is always wrong and wastes a hop. Classifying intent first
makes routing deterministic: help-seeking deflects, ticket-management goes straight
to ServiceNow.

