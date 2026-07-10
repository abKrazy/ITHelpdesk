# tests — pytest suites

**Owner:** Dozer (QA & Docs)

These suites validate the accelerator against the contracts in
[`ARCHITECTURE.md`](../ARCHITECTURE.md). Almost everything runs **offline in mock
mode** — no Azure, no ServiceNow, no network — so CI and a fresh clone can prove
correctness before anyone deploys.

## Quick start

```bash
# From the repo root: install runtime + test + UI + servicenow extras.
pip install -e ".[dev,ui,servicenow]"

# Run the whole suite (mock mode is the default — see below).
pytest
```

Expected: **all tests pass** (48 at time of writing) and `ruff check src tests`
is clean.

## Mock mode vs live

The stack picks mock vs live from the environment (`src/helpdesk/shared/config.py`):

- **Mock mode** — `HELPDESK_MOCK=1` (set automatically for the suite by
  `tests/conftest.py`). Triage searches the local KB (`assets/kb/*.md`) via
  `LocalKbSearchClient`; the incident agent uses the in-memory ServiceNow mock
  seeded with **INC0000057** and **INC0010027**; the ServiceNow client tests use a
  fake MCP transport. **This is the default and needs no credentials.**
- **Live mode** — mock is off when `HELPDESK_MOCK` is unset/false *and*
  `AZURE_AI_PROJECT_ENDPOINT` is present (or force it with `HELPDESK_LIVE=1`).
  Live runs hit Azure AI Search, Foundry, and the APIM MCP endpoint, so they need
  a deployed environment and Azure credentials (`azd env get-values > .env`, or an
  `az login` session / managed identity).

```bash
# Force mock (belt-and-suspenders; conftest already does this):
HELPDESK_MOCK=1 pytest                 # PowerShell: $env:HELPDESK_MOCK=1; pytest

# Live smoke against a deployed env (advanced):
azd env get-values > .env              # export the azd outputs
# load .env into your shell, then:
HELPDESK_LIVE=1 pytest tests/test_smoke.py
```

## What each file covers

| File | Scope | Mode |
|------|-------|------|
| `conftest.py` | Bootstraps `HELPDESK_MOCK=1` + makes `src/` importable | — |
| `test_smoke.py` | The 3 sample prompts through the Orchestrator (also runnable as a script: `python tests/test_smoke.py`); create-intent prompts with a confident KB match now deflect first and file only after confirmation. | mock |
| `test_orchestrator_flows.py` | All **4 capabilities** + edge cases: KB resolve (no ticket), deflection-first create flow, lookup, update, unknown incident, escalation handoff, ambiguous/empty prompts, urgency-label variants, intent detection | mock |
| `test_servicenow_client.py` | Live `src/servicenow` MCP client (create/get/update, PATCH-over-PUT, not-found, retry/no-retry) against a **fake MCP transport** | offline |
| `test_openapi_import.py` | `assets/ServiceNow-OpenAPI-spec.json` parses and has the create/read/update ops + params the incident flow relies on | offline |
| `test_ui_app.py` | FastAPI app via `TestClient`: `/healthz` liveness and the `/agui` AG-UI contract (HITL approve/reject, KB citations side-channel, status turns) for the sample prompts | mock |
| `test_kb_assets.py` | Every `assets/kb/*.md` doc has a title, resolution steps, and a Recommended Assignment Group | offline |

## Notes

- Tests run in-process; no ports are opened and nothing is left running.
- Config is in `pyproject.toml` (`[tool.pytest.ini_options]`, `asyncio_mode = auto`).
- A `StarletteDeprecationWarning` from `fastapi.testclient` is benign.
