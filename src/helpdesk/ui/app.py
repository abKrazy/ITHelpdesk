"""FastAPI chat application.

Routes:
  * ``GET /``               — server-rendered chat page.
  * ``POST /api/chat``      — JSON {message, history} -> {reply, route}.
  * ``POST /api/chat/stream`` — Server-Sent Events: incremental token stream.
  * ``GET /healthz``        — liveness probe for App Service.

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

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
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


def _sse(payload: dict) -> str:
    """Format one Server-Sent Events ``data:`` frame from a JSON-able payload."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _iter_in_thread(make_iterator):
    """Drive a *blocking* iterator on a worker thread, yielding items async.

    The OpenAI Responses stream is a synchronous iterator; iterating it directly
    inside the async event loop would block it. We pump items through an
    ``asyncio.Queue`` so the ASGI server can flush each SSE frame as it arrives.
    Exceptions raised while building or iterating are re-raised to the caller.
    """
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    sentinel = object()

    def worker() -> None:
        try:
            for item in make_iterator():
                loop.call_soon_threadsafe(queue.put_nowait, item)
        except Exception as exc:  # forward to the async side
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, sentinel)

    task = loop.run_in_executor(None, worker)
    try:
        while True:
            item = await queue.get()
            if item is sentinel:
                break
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        await task


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

    async def _live_stream(
        message: str, history: list[dict[str, str]]
    ) -> AsyncIterator[dict]:
        """Stream token deltas from the hosted orchestrator via the Responses API.

        Emits ``{"type":"token","text":...}`` for each ``response.output_text.delta``
        and a terminal ``{"type":"done","route":[...]}`` on ``response.completed``.
        Unknown event types are skipped defensively.
        """
        settings = get_settings()
        conversation = [
            {"role": turn["role"], "content": turn["content"]} for turn in history
        ]
        conversation.append({"role": "user", "content": message})
        client = _openai_client()

        def make_stream():
            return client.responses.create(
                model=settings.chat_deployment or "gpt-4o",
                input=conversation,
                stream=True,
            )

        got_text = False
        async for event in _iter_in_thread(make_stream):
            etype = getattr(event, "type", None)
            if etype == "response.output_text.delta":
                delta = getattr(event, "delta", "") or ""
                if delta:
                    got_text = True
                    yield {"type": "token", "text": delta}
            elif etype == "response.completed":
                break
            # Skip other event types (created, in_progress, item.*, etc.).
        if not got_text:
            yield {"type": "token", "text": "(the orchestrator returned no content)"}
        yield {"type": "done", "route": ["orchestrator"]}

    async def _mock_stream(
        message: str, history: list[dict[str, str]]
    ) -> AsyncIterator[dict]:
        """Simulate streaming for the in-process mock orchestrator.

        The mock returns the full reply as one string; we chunk it by word so the
        UI and tests still exercise the incremental token-rendering code path.
        """
        result = _mock_orchestrator().run(message, history=history)
        reply = result.reply or "(no response)"
        for token in re.findall(r"\S+\s*", reply):
            yield {"type": "token", "text": token}
            await asyncio.sleep(0.005)
        yield {"type": "done", "route": list(result.route)}

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

    @app.post("/api/chat/stream")
    async def chat_stream(payload: ChatRequest) -> StreamingResponse:
        """Stream the assistant reply as Server-Sent Events.

        Frames: ``{"type":"token","text":...}`` incrementally, then a terminal
        ``{"type":"done","route":[...]}``. Any failure yields a structured
        ``{"type":"error","text":...}`` frame instead of a bare HTTP 500, so the
        browser client always receives a parseable terminal event.
        """
        history = [turn.model_dump() for turn in payload.history[-10:]]
        message = payload.message

        async def event_source() -> AsyncIterator[str]:
            try:
                if get_settings().mock_mode:
                    generator = _mock_stream(message, history)
                else:
                    generator = _live_stream(message, history)
                async for chunk in generator:
                    yield _sse(chunk)
            except Exception as exc:
                detail = _short_error_detail(exc)
                _LOGGER.exception("Chat stream failed")
                yield _sse(
                    {
                        "type": "error",
                        "text": _CHAT_ERROR_REPLY.format(reason=detail),
                        "error": detail,
                    }
                )

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return app


app = create_app()
