"""FastAPI chat application.

Routes:
  * ``GET /``            — server-rendered chat page.
  * ``POST /api/chat``   — JSON {message} -> {reply, route}; invokes the Orchestrator.
  * ``GET /healthz``     — liveness probe for App Service.

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

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_LOGGER = logging.getLogger(__name__)
_CHAT_ERROR_REPLY = (
    "⚠️ I couldn't reach the ServiceNow backend right now — {reason}. "
    "Please try again or contact an administrator."
)


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


def create_app() -> FastAPI:
    app = FastAPI(title="ServiceNow IT Helpdesk Agent")

    # Instantiate lazily so the module imports without Azure creds; the
    # Orchestrator + agents pick mock vs live from the environment (config.py).
    app.state.orchestrator = None

    def _orchestrator() -> Orchestrator:
        if app.state.orchestrator is None:
            app.state.orchestrator = Orchestrator()
        return app.state.orchestrator

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return _TEMPLATES.TemplateResponse(request, "index.html")

    @app.post("/api/chat", response_model=ChatReply, response_model_exclude_none=True)
    async def chat(payload: ChatRequest) -> ChatReply:
        try:
            history = [turn.model_dump() for turn in payload.history[-10:]]
            result = _orchestrator().run(payload.message, history=history)
        except Exception as exc:
            detail = _short_error_detail(exc)
            _LOGGER.exception("Chat orchestrator failed")
            return ChatReply(
                reply=_CHAT_ERROR_REPLY.format(reason=detail),
                route=["error"],
                error=detail,
            )
        return ChatReply(reply=result.reply, route=result.route)

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return app


app = create_app()
