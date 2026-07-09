"""FastAPI chat application.

Routes:
  * ``GET /``            — server-rendered chat page.
  * ``POST /api/chat``   — JSON {message, history} -> {reply, route}.
  * ``GET /healthz``     — liveness probe for App Service.

Two backends, chosen by :attr:`Settings.mock_mode`:
  * **Live** — forwards the conversation to the **Foundry Hosted Agent**
    ``it-helpdesk-orchestrator`` (a Microsoft Agent Framework agent) via the
    project's OpenAI **Responses** endpoint (``agent_reference``). The hosted
    orchestrator's LLM decides which sub-agent (triage / incident) to call.
  * **Mock** — an in-process deterministic :class:`Orchestrator` so the UI runs
    offline (CI / local smoke test) without any live Azure dependency.

Every failure path returns a valid ``ChatReply`` JSON body (never a raw HTTP 500
text body) so the browser client can always ``JSON.parse`` the response.

App Service start command (deploy root ./src on PYTHONPATH):
    python -m gunicorn helpdesk.ui.app:app --bind 0.0.0.0:8000 \
        --timeout 600 --worker-class uvicorn.workers.UvicornWorker
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from ..orchestrator import Orchestrator
from ..shared.config import get_settings

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_LOGGER = logging.getLogger(__name__)
_CHAT_ERROR_REPLY = (
    "⚠️ I couldn't reach the ServiceNow backend right now — {reason}. "
    "Please try again or contact an administrator."
)

ORCHESTRATOR_AGENT_NAME = "it-helpdesk-orchestrator"


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatTurn] = Field(default_factory=list)


class ChatReply(BaseModel):
    reply: str
    route: list[str]
    error: str | None = None


def _short_error_detail(exc: Exception) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    return detail[:240]


def _extract_output_text(resp) -> str:
    text = getattr(resp, "output_text", None)
    if text:
        return str(text).strip()
    parts: list[str] = []
    for item in getattr(resp, "output", None) or []:
        for content in getattr(item, "content", None) or []:
            chunk = getattr(content, "text", None)
            if chunk:
                parts.append(str(chunk))
    return "\n".join(parts).strip() or "(the orchestrator returned no content)"


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

        Hosted agents are NOT invoked via ``agent_reference`` (that is the Prompt
        Agent contract). They expose a dedicated endpoint
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

    def _live_reply(message: str, history: list[dict[str, str]]) -> ChatReply:
        settings = get_settings()
        # Thread the recent conversation so the hosted orchestrator has context
        # (e.g. the incident number created earlier in the chat).
        conversation = [
            {"role": turn["role"], "content": turn["content"]} for turn in history
        ]
        conversation.append({"role": "user", "content": message})
        client = _openai_client()
        resp = client.responses.create(
            model=settings.chat_deployment or "gpt-4o",
            input=conversation,
        )
        return ChatReply(reply=_extract_output_text(resp), route=["orchestrator"])

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return _TEMPLATES.TemplateResponse(request, "index.html")

    @app.post("/api/chat", response_model=ChatReply, response_model_exclude_none=True)
    async def chat(payload: ChatRequest) -> ChatReply:
        history = [turn.model_dump() for turn in payload.history[-10:]]
        try:
            if get_settings().mock_mode:
                result = _mock_orchestrator().run(payload.message, history=history)
                return ChatReply(reply=result.reply, route=result.route)
            return _live_reply(payload.message, history)
        except Exception as exc:
            detail = _short_error_detail(exc)
            _LOGGER.exception("Chat orchestrator failed")
            return ChatReply(
                reply=_CHAT_ERROR_REPLY.format(reason=detail),
                route=["error"],
                error=detail,
            )

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return app


app = create_app()
