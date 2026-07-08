# src/ui — Customer-facing Chat UI (Azure App Service)

**Owner:** Trinity (UI implementation) / Morpheus (shape)

## What goes here
The web app end users interact with. A lightweight Python web app (FastAPI +
server-rendered chat, or similar) deployed to **Azure App Service**. It forwards
user messages to the **Orchestrator** (Foundry hosted agent) and streams
responses back.

## azd
This IS an `azd` service (`ui`, host: `appservice`). The hosting Web App is
tagged `azd-service-name: ui` in `infra/modules/appservice.bicep`, so
`azd deploy ui` targets it.

## Inputs it needs (App Service app settings — set by Bicep)
- `AZURE_AI_PROJECT_ENDPOINT` — to reach the Orchestrator.
- `AZURE_CLIENT_ID` — managed identity auth (DefaultAzureCredential).
- `SERVICENOW_MCP_ENDPOINT` — informational / optional direct probes.
- `APPLICATIONINSIGHTS_CONNECTION_STRING` — telemetry.

## Boundary
Presentation + calling the Orchestrator only. No KB logic, no ServiceNow calls.

## Run locally / App Service start command
The Web App runs the ASGI app `helpdesk.ui.app:app`. App Service start command
(also usable locally):

```
python -m uvicorn helpdesk.ui.app:app --host 0.0.0.0 --port 8000
```

App Service sets `$PORT`; use `--port $PORT` there. Dependencies come from the
`ui` extra (`pip install -e .[ui]`) or `requirements.txt`.

Set `HELPDESK_MOCK=1` to run the whole chat flow offline (local KB + in-memory
ServiceNow), with no Azure dependency — handy for local UI testing.
