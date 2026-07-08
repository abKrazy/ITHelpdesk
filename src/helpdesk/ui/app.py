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

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from ..orchestrator import Orchestrator

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


class ChatRequest(BaseModel):
    message: str


class ChatReply(BaseModel):
    reply: str
    route: list[str]


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

    @app.post("/api/chat", response_model=ChatReply)
    async def chat(payload: ChatRequest) -> ChatReply:
        result = _orchestrator().run(payload.message)
        return ChatReply(reply=result.reply, route=result.route)

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return app


app = create_app()
