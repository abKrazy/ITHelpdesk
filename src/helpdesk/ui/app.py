"""FastAPI backend for the ServiceNow IT Helpdesk agent.

Exposes a single **AG-UI** endpoint (``POST /agui``) that the CopilotKit /
Next.js frontend calls, plus a ``/healthz`` liveness probe for App Service. The
AG-UI proxy (:class:`HelpdeskAGUIProxyAgent`) translates the orchestrator's
streamed output into AG-UI events: sub-agent handoff tool pairs, KB citations,
and the ServiceNow human-approval interrupt.

Two backends, chosen by :attr:`Settings.mock_mode`:
  * **Live** — forwards the conversation to the Foundry Hosted Agent
    ``it-helpdesk-orchestrator`` via the project's OpenAI **Responses** endpoint.
    The hosted orchestrator's LLM decides which sub-agent (triage / incident) to
    invoke.
  * **Mock** — an in-process deterministic :class:`Orchestrator` so the backend
    runs offline (CI / local smoke test) without any live Azure dependency.

App Service start command (deploy root ./src on PYTHONPATH):
    python -m gunicorn helpdesk.ui.app:app --bind 0.0.0.0:8000 \
        --timeout 600 --worker-class uvicorn.workers.UvicornWorker
"""

from __future__ import annotations

from agent_framework_ag_ui import AgentFrameworkAgent, add_agent_framework_fastapi_endpoint
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ..orchestrator import Orchestrator
from ..shared.config import get_settings
from .agui_proxy import HelpdeskAGUIProxyAgent

ORCHESTRATOR_AGENT_NAME = "it-helpdesk-orchestrator"


def create_app() -> FastAPI:
    app = FastAPI(title="ServiceNow IT Helpdesk Agent")

    # Instantiate lazily so the module imports without Azure creds; the mock
    # Orchestrator + agents pick mock vs live from the environment (config.py).
    app.state.orchestrator = None
    app.state.openai_client = None

    def _mock_orchestrator() -> Orchestrator:
        if app.state.orchestrator is None:
            app.state.orchestrator = Orchestrator()
        return app.state.orchestrator

    def _openai_client():
        """OpenAI client bound to the Foundry **hosted** Orchestrator agent (cached).

        Hosted agents expose a dedicated endpoint
        ``.../agents/{name}/endpoint/protocols/openai/`` — ``get_openai_client``
        builds a client pointed at it when passed ``agent_name``. Live mode only.
        """
        if app.state.openai_client is None:
            from azure.ai.projects import AIProjectClient

            from ..shared import get_credential

            settings = get_settings()
            project = AIProjectClient(
                endpoint=settings.ai_project_endpoint, credential=get_credential()
            )
            app.state.openai_client = project.get_openai_client(
                agent_name=ORCHESTRATOR_AGENT_NAME
            )
        return app.state.openai_client

    app.state.agui_proxy_agent = HelpdeskAGUIProxyAgent(
        settings_factory=get_settings,
        mock_orchestrator_factory=_mock_orchestrator,
        openai_client_factory=_openai_client,
    )
    add_agent_framework_fastapi_endpoint(
        app,
        AgentFrameworkAgent(agent=app.state.agui_proxy_agent, require_confirmation=False),
        "/agui",
        keepalive_seconds=None,
    )

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return app


app = create_app()
