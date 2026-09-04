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

import base64
import html
import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any, NoReturn
from urllib.parse import parse_qs

from agent_framework_ag_ui import AgentFrameworkAgent, add_agent_framework_fastapi_endpoint
from fastapi import APIRouter, Depends, FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..orchestrator import Orchestrator
from ..observability.kb_gap_store import get_gap, list_gaps, set_status
from ..shared.config import get_settings
from .agui_proxy import HelpdeskAGUIProxyAgent

ORCHESTRATOR_AGENT_NAME = "it-helpdesk-orchestrator"
_LOGGER = logging.getLogger(__name__)
_ASSIGNMENT_GROUPS = ("Service Desk", "Desktop Support", "Network Support")


class AdminPrincipal:
    def __init__(self, display_name: str, identifier: str) -> None:
        self.display_name = display_name
        self.identifier = identifier


class AdminAuthRequired(Exception):
    def __init__(self, body: str) -> None:
        self.body = body


def create_app() -> FastAPI:
    app = FastAPI(title="ServiceNow IT Helpdesk Agent")

    @app.exception_handler(AdminAuthRequired)
    async def admin_auth_required_handler(
        _request: Request, exc: AdminAuthRequired
    ) -> HTMLResponse:
        return HTMLResponse(exc.body, status_code=401)

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

    app.include_router(_admin_router())
    return app


def _admin_router() -> APIRouter:
    router = APIRouter(prefix="/admin", dependencies=[Depends(_require_admin)])

    @router.get("", response_class=HTMLResponse)
    async def admin_home(_principal: AdminPrincipal = Depends(_require_admin)) -> HTMLResponse:
        gaps = [
            gap
            for gap in list_gaps(limit=50)
            if str(gap.get("status") or "new") in {"new", "triaged", "in_progress"}
        ]
        rows = "\n".join(_gap_row(gap) for gap in gaps) or (
            "<tr><td colspan='6'>No open knowledge gaps.</td></tr>"
        )
        return HTMLResponse(
            _page(
                "Knowledge gaps",
                f"""
                <h1>Knowledge gaps</h1>
                <p>Signed in as {html.escape(_principal.display_name)}.</p>
                <table>
                  <thead>
                    <tr>
                      <th>Question</th><th>Reason</th><th>Tool</th>
                      <th>Count</th><th>Last seen</th><th>Action</th>
                    </tr>
                  </thead>
                  <tbody>{rows}</tbody>
                </table>
                """,
            )
        )

    @router.get("/gaps")
    async def admin_list_gaps(status: str | None = None, limit: int = 50) -> JSONResponse:
        return JSONResponse({"gaps": list_gaps(status=status or None, limit=limit)})

    @router.post("/gaps/{question_hash}/status")
    async def admin_set_gap_status(question_hash: str, request: Request) -> JSONResponse:
        form = await _form_fields(request)
        status = form.get("status", "")
        note = form.get("note") or None
        try:
            return JSONResponse(set_status(question_hash, status, note=note))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @router.get("/kb/new", response_class=HTMLResponse)
    async def admin_new_kb(
        question_hash: str,
        _principal: AdminPrincipal = Depends(_require_admin),
    ) -> HTMLResponse:
        gap = get_gap(question_hash)
        if gap is None:
            return HTMLResponse(_page("Gap not found", "<h1>Gap not found</h1>"), status_code=404)
        return HTMLResponse(_new_kb_form(gap))

    @router.post("/kb")
    async def admin_publish_kb(
        request: Request,
        principal: AdminPrincipal = Depends(_require_admin),
    ) -> JSONResponse:
        form = await _form_fields(request)
        try:
            article = _validate_kb_form(form)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        article["author"] = principal.identifier
        article["created_at"] = _utc_now_iso()
        markdown = _render_kb_markdown(article)
        blob_name = f"authored/{article['doc_id']}.md"
        try:
            _upload_kb_blob(blob_name, markdown, _blob_metadata(article))
            indexing = _try_run_indexer_after_publish()
            gap = set_status(
                article["gap_hash"], "resolved", note=f"Published KB article {blob_name}"
            )
        except Exception as exc:
            _LOGGER.exception("Failed to publish KB article %s", blob_name)
            return JSONResponse(
                {"error": f"Failed to publish KB article: {exc}"}, status_code=500
            )
        return JSONResponse(
            {"blob": blob_name, "gap": gap, "metadata": _blob_metadata(article), "indexing": indexing}
        )

    return router


def _admin_auth_disabled() -> bool:
    value = get_settings().get("ADMIN_AUTH_DISABLED", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _require_admin(
    x_ms_client_principal: str | None = Header(default=None, alias="X-MS-CLIENT-PRINCIPAL"),
) -> AdminPrincipal:
    if _admin_auth_disabled():
        return AdminPrincipal("Local admin", "local-admin")
    if not x_ms_client_principal:
        body = (
            "<h1>Sign in required</h1>"
            '<p><a href="/.auth/login/aad?post_login_redirect_uri=/admin">'
            "Sign in with Microsoft Entra ID</a></p>"
        )
        return _raise_html_401(body)
    return _parse_easy_auth_principal(x_ms_client_principal)


def _raise_html_401(body: str) -> NoReturn:
    raise AdminAuthRequired(body)


def _parse_easy_auth_principal(header_value: str) -> AdminPrincipal:
    try:
        padded = header_value + "=" * (-len(header_value) % 4)
        principal = json.loads(base64.b64decode(padded).decode("utf-8"))
    except Exception:
        return AdminPrincipal("Authenticated admin", "authenticated-admin")

    claims = principal.get("claims") if isinstance(principal, dict) else []
    claim_values: dict[str, str] = {}
    for claim in claims or []:
        if isinstance(claim, dict) and claim.get("typ") and claim.get("val"):
            claim_type = str(claim["typ"])
            claim_value = str(claim["val"])
            claim_values[claim_type] = claim_value
            claim_values[claim_type.rsplit("/", 1)[-1]] = claim_value

    name = (
        str(principal.get("userDetails") or "")
        or claim_values.get("name")
        or claim_values.get("preferred_username")
        or claim_values.get("upn")
        or claim_values.get("emails")
        or claim_values.get("emailaddress")
        or "Authenticated admin"
    )
    identifier = (
        claim_values.get("preferred_username")
        or claim_values.get("email")
        or claim_values.get("emails")
        or claim_values.get("emailaddress")
        or claim_values.get("upn")
        or claim_values.get("oid")
        or claim_values.get("objectidentifier")
        or name
    )
    return AdminPrincipal(display_name=name, identifier=identifier)


def _gap_row(gap: dict[str, Any]) -> str:
    question_hash = html.escape(str(gap.get("question_hash") or ""))
    question = html.escape(str(gap.get("question") or "(content recording disabled)"))
    reason = html.escape(str(gap.get("reason") or ""))
    tool_name = html.escape(str(gap.get("tool") or ""))
    count = html.escape(str(gap.get("occurrence_count") or 0))
    last_seen = html.escape(str(gap.get("last_seen_at") or ""))
    return (
        "<tr>"
        f"<td>{question}</td><td>{reason}</td><td>{tool_name}</td>"
        f"<td>{count}</td><td>{last_seen}</td>"
        f'<td><a href="/admin/kb/new?question_hash={question_hash}">Author KB</a></td>'
        "</tr>"
    )


def _new_kb_form(gap: dict[str, Any]) -> str:
    question_hash = str(gap.get("question_hash") or "")
    question = str(gap.get("question") or "")
    title = _title_from_question(question)
    group_options = "\n".join(
        f'<option value="{html.escape(group)}">{html.escape(group)}</option>'
        for group in _ASSIGNMENT_GROUPS
    )
    content = f"""
    <h1>Author KB article</h1>
    <form method="post" action="/admin/kb">
      <input type="hidden" name="gap_hash" value="{html.escape(question_hash)}" />
      <label>Title <input name="title" value="{html.escape(title)}" required /></label>
      <label>Doc ID <input name="doc_id" value="{html.escape(_slugify(title or question_hash))}" /></label>
      <label>Source <input name="source" value="support-authored" required /></label>
      <label>Assignment group <select name="assignment_group">{group_options}</select></label>
      <label>Keywords <input name="keywords" value="" placeholder="vpn, password, error" required /></label>
      <label>Overview <textarea name="overview">{html.escape(question)}</textarea></label>
      <label>Symptoms <textarea name="symptoms"></textarea></label>
      <label>Common causes <textarea name="common_causes"></textarea></label>
      <label>Resolution steps <textarea name="resolution_steps" required></textarea></label>
      <label>When to create a ticket <textarea name="when_to_create_ticket"></textarea></label>
      <button type="submit">Publish</button>
    </form>
    """
    return _page("Author KB article", content)


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ddd; padding: .5rem; vertical-align: top; }}
    label {{ display: block; margin: 1rem 0; }}
    input, select, textarea {{ display: block; width: min(48rem, 100%); }}
    textarea {{ min-height: 6rem; }}
  </style>
</head>
<body>{body}</body>
</html>"""


async def _form_fields(request: Request) -> dict[str, str]:
    if request.headers.get("content-type", "").lower().startswith("application/json"):
        payload = await request.json()
        if isinstance(payload, dict):
            return {str(key): str(value or "") for key, value in payload.items()}
    body = (await request.body()).decode("utf-8")
    parsed = parse_qs(body, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _validate_kb_form(form: dict[str, str]) -> dict[str, str]:
    title = form.get("title", "").strip()
    gap_hash = form.get("gap_hash", "").strip()
    assignment_group = form.get("assignment_group", "").strip()
    resolution_steps = form.get("resolution_steps", "").strip()
    keywords = ", ".join(_split_keywords(form.get("keywords", "")))
    source = form.get("source", "").strip() or "support-authored"
    if not title or not gap_hash or not assignment_group or not resolution_steps or not keywords:
        raise ValueError(
            "title, gap_hash, assignment_group, keywords, and resolution_steps are required"
        )
    if source != "support-authored":
        raise ValueError("source must be support-authored for admin-authored KB articles")
    return {
        "doc_id": _slugify(form.get("doc_id", "") or title),
        "title": title,
        "source": source,
        "gap_hash": gap_hash,
        "assignment_group": assignment_group,
        "keywords": keywords,
        "overview": form.get("overview", "").strip() or title,
        "symptoms": form.get("symptoms", "").strip() or "- Not documented yet.",
        "common_causes": form.get("common_causes", "").strip() or "- Not documented yet.",
        "resolution_steps": resolution_steps,
        "when_to_create_ticket": form.get("when_to_create_ticket", "").strip()
        or "- Create a ticket if the resolution steps do not resolve the issue.",
    }


def _render_kb_markdown(article: dict[str, str]) -> str:
    keywords_yaml = "\n".join(f"  - {keyword}" for keyword in _split_keywords(article["keywords"]))
    return f"""---
title: {article["title"]}
keywords:
{keywords_yaml}
assignment_group: {article["assignment_group"]}
source: {article["source"]}
gap_hash: {article["gap_hash"]}
created_at: {article["created_at"]}
author: {article["author"]}
---
# {article["title"]}

## Overview
{article["overview"]}

## Symptoms
{article["symptoms"]}

## Common Causes
{article["common_causes"]}

## Resolution Steps
{article["resolution_steps"]}

## When to Create a Ticket
{article["when_to_create_ticket"]}

## Recommended Assignment Group
{article["assignment_group"]}

## Keywords
{article["keywords"]}
"""


def _metadata_safe(value: str) -> str:
    """Azure Blob metadata values are sent as HTTP headers: they must be a single
    line with no CR/LF or reserved characters. Collapse all whitespace (including
    the newlines in multi-line fields like resolution_steps) to single spaces."""
    return " ".join((value or "").replace("\r", "\n").split())


def _blob_metadata(article: dict[str, str]) -> dict[str, str]:
    raw = {
        "doc_id": article["doc_id"],
        "title": article["title"],
        "source": article["source"],
        "assignment_group": article["assignment_group"],
        "keywords": article["keywords"],
        "resolution_steps": article["resolution_steps"],
        "gap_hash": article["gap_hash"],
        "created_at": article["created_at"],
        "author": article["author"],
    }
    return {key: _metadata_safe(value) for key, value in raw.items() if value}


def _upload_kb_blob(blob_name: str, markdown: str, metadata: dict[str, str]) -> None:
    from azure.storage.blob import BlobServiceClient

    from ..shared import get_credential

    settings = get_settings()
    service = BlobServiceClient(
        account_url=settings.storage_blob_endpoint,
        credential=get_credential(),
        connection_timeout=30,
        read_timeout=120,
    )
    blob = service.get_container_client(settings.kb_container or "kbdocs").get_blob_client(blob_name)
    blob.upload_blob(
        markdown.encode("utf-8"),
        overwrite=True,
        metadata=metadata,
    )


def _try_run_indexer_after_publish() -> dict[str, str]:
    flag = os.environ.get("KB_RUN_INDEXER_ON_PUBLISH_ENABLED", "")
    if flag.strip().lower() in {"0", "false", "no", "off"}:
        return {"status": "disabled", "message": "Immediate indexing is disabled."}

    settings = get_settings()
    if not settings.search_endpoint:
        return {
            "status": "skipped",
            "message": "KB article is queued for the scheduled Search indexer run.",
        }

    try:
        from ..agents import setup

        indexer_name = setup.kb_indexing_resource_names(settings.search_index_name)["indexer"]
        setup.run_indexer(
            search_endpoint=settings.search_endpoint,
            indexer_name=indexer_name,
            wait=False,
        )
        return {
            "status": "triggered",
            "message": "KB article uploaded and Search indexer trigger requested.",
        }
    except Exception:
        _LOGGER.warning(
            "KB article uploaded, but immediate Search indexer run failed; scheduled run will retry",
            exc_info=True,
        )
        return {
            "status": "queued_fallback",
            "message": "KB article uploaded; scheduled Search indexing will pick it up shortly.",
        }


def _title_from_question(question: str) -> str:
    cleaned = question.strip().rstrip("?")
    return cleaned[:80] if cleaned else "New support-authored article"


def _split_keywords(raw: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,\n]", raw) if part.strip()]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:80] or "support-authored-article"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


app = create_app()
