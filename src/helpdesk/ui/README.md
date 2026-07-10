# src/helpdesk/ui — Python AG-UI backend (Azure App Service `api`)

**Owner:** Trinity (implementation) / Morpheus (shape)

## What goes here
The **AG-UI backend** the CopilotKit / Next.js frontend talks to. A FastAPI app
deployed to **Azure App Service** (the `api` service — the customer-facing UI is
the separate Node app in `frontend/`, the `ui` service). It exposes a single
AG-UI endpoint plus a liveness probe:

- `POST /agui` — the AG-UI protocol endpoint. `HelpdeskAGUIProxyAgent`
  (`agui_proxy.py`) forwards the conversation to the Foundry Hosted
  **Orchestrator** agent and translates its streamed output into AG-UI events:
  sub-agent handoff tool pairs, KB citations, and the ServiceNow human-approval
  interrupt (approve resumes the thread; reject cancels with no write).
- `GET /healthz` — App Service liveness probe.

## azd
This is the `azd` service **`api`** (host: `appservice`). The hosting Web App is
tagged `azd-service-name: api` in `infra/modules/appservice.bicep`, so
`azd deploy api` targets it.

## Inputs it needs (App Service app settings — set by Bicep)
- `AZURE_AI_PROJECT_ENDPOINT` — to reach the hosted Orchestrator.
- `AZURE_CLIENT_ID` — managed identity auth (DefaultAzureCredential).
- `AZURE_OPENAI_CHAT_DEPLOYMENT` — model used against the hosted-agent endpoint.
- `APPLICATIONINSIGHTS_CONNECTION_STRING` — telemetry.

## Boundary
AG-UI translation + invoking the Orchestrator only. No KB logic and no direct
ServiceNow calls — routing, HITL, and tool execution all live in the Orchestrator
and its sub-agents.

## Run locally / App Service start command
The Web App runs the ASGI app `helpdesk.ui.app:app`. App Service start command
(also usable locally):

```
python -m gunicorn helpdesk.ui.app:app --bind 0.0.0.0:8000 \
    --timeout 600 --worker-class uvicorn.workers.UvicornWorker
```

Dependencies come from the `ui` extra (`pip install -e .[ui]`) or
`src/requirements.txt`.

Set `HELPDESK_MOCK=1` to run the whole flow offline (local KB + in-memory
ServiceNow) with no Azure dependency — the in-process deterministic
`Orchestrator` backs `/agui`, handy for local UI testing and CI.
