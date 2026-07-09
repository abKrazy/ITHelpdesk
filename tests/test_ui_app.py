"""FastAPI UI contract tests via Starlette's TestClient (mock mode).

The App Service UI (``src/helpdesk/ui/app.py``) is the customer-facing front
door: it renders the chat page and forwards messages to the Orchestrator. These
tests exercise the real ASGI app in-process (no network, no Azure) and assert the
health probe plus the ``/api/chat`` route/reply contract for all 3 sample prompts
(``assets/Sample-Prompts.txt``), which is exactly what App Service + the browser
depend on.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from helpdesk.ui.app import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_healthz_liveness_probe(client: TestClient) -> None:
    """App Service liveness probe returns 200 + a stable body."""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_index_page_renders(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert resp.text.strip(), "index page should not be empty"


def test_chat_lookup_prompt(client: TestClient) -> None:
    """Prompt 1 — status lookup routes to the incident agent."""
    resp = client.post("/api/chat", json={"message": "lookup details for incident INC0000057"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["route"] == ["incident"]
    assert "INC0000057" in body["reply"]
    assert "State:" in body["reply"]


def test_chat_create_prompt(client: TestClient) -> None:
    """Prompt 2 — triage offers KB steps before creating."""
    resp = client.post(
        "/api/chat", json={"message": "Unable to log into Epic. Create a new incident."}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["route"] == ["triage"]
    assert "Identity and Access Management" in body["reply"]
    assert "reply 'go ahead'" in body["reply"]


def test_chat_confirmation_uses_history(client: TestClient) -> None:
    original = "my laptop is running slow. please file a ticket."
    offer_resp = client.post("/api/chat", json={"message": original})
    offer = offer_resp.json()["reply"]

    resp = client.post(
        "/api/chat",
        json={
            "message": "go ahead",
            "history": [
                {"role": "user", "content": original},
                {"role": "assistant", "content": offer},
            ],
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["route"] == ["triage", "incident"]
    assert "Created incident INC" in body["reply"]
    assert "Desktop Support" in body["reply"]


def test_chat_update_prompt(client: TestClient) -> None:
    """Prompt 3 — urgency update routes to the incident agent."""
    resp = client.post("/api/chat", json={"message": "update urgency for INC0010027 to low"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["route"] == ["incident"]
    assert "INC0010027" in body["reply"]
    assert "Low" in body["reply"]


def test_chat_response_schema(client: TestClient) -> None:
    """The chat endpoint always returns {reply: str, route: list[str]}."""
    resp = client.post("/api/chat", json={"message": "How do I reset my forgotten password?"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"reply", "route"}
    assert isinstance(body["reply"], str) and body["reply"]
    assert isinstance(body["route"], list) and all(isinstance(r, str) for r in body["route"])
    # A KB-answerable question resolves via triage only (no ticket).
    assert body["route"] == ["triage"]


def test_chat_orchestrator_failure_returns_json_error(client: TestClient) -> None:
    """A downstream orchestrator failure is rendered as parseable chat JSON."""

    class FailingOrchestrator:
        def run(self, _message: str, history=None) -> None:
            raise RuntimeError("MCP endpoint unreachable")

    previous = app.state.orchestrator
    app.state.orchestrator = FailingOrchestrator()
    try:
        resp = client.post("/api/chat", json={"message": "lookup INC0000057"})
    finally:
        app.state.orchestrator = previous

    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
    body = resp.json()
    assert body["route"] == ["error"]
    assert body["error"] == "MCP endpoint unreachable"
    assert "couldn't reach the ServiceNow backend" in body["reply"]


def test_chat_requires_message_field(client: TestClient) -> None:
    """A malformed body (missing 'message') is rejected with 422, not a 500."""
    resp = client.post("/api/chat", json={})
    assert resp.status_code == 422


def _parse_sse(body: str) -> list[dict]:
    events: list[dict] = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:"):].strip()))
    return events


def test_chat_stream_yields_tokens_then_done(client: TestClient) -> None:
    """The streaming endpoint emits multiple token frames then a done frame.

    Mock mode chunks the reply by word, so the browser exercises the same
    incremental token-rendering path used against the live hosted orchestrator.
    """
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "lookup details for incident INC0000057"},
    ) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = "".join(resp.iter_text())

    events = _parse_sse(body)
    tokens = [e for e in events if e["type"] == "token"]
    done = [e for e in events if e["type"] == "done"]

    assert len(tokens) > 1, "expected the reply to arrive as multiple token frames"
    assert len(done) == 1, "expected exactly one terminal 'done' frame"
    assert done[0]["route"] == ["incident"]

    reply = "".join(t["text"] for t in tokens)
    assert "INC0000057" in reply
    assert "State:" in reply


def test_chat_stream_confirmation_uses_history(client: TestClient) -> None:
    """History threading works identically on the streaming endpoint."""
    original = "my laptop is running slow. please file a ticket."
    offer = client.post("/api/chat", json={"message": original}).json()["reply"]

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={
            "message": "go ahead",
            "history": [
                {"role": "user", "content": original},
                {"role": "assistant", "content": offer},
            ],
        },
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    events = _parse_sse(body)
    done = next(e for e in events if e["type"] == "done")
    assert done["route"] == ["triage", "incident"]
    reply = "".join(e["text"] for e in events if e["type"] == "token")
    assert "Created incident INC" in reply


def test_chat_stream_failure_yields_error_frame(client: TestClient) -> None:
    """A downstream failure is delivered as a structured 'error' frame, not a 500."""

    class FailingOrchestrator:
        def run(self, _message: str, history=None) -> None:
            raise RuntimeError("MCP endpoint unreachable")

    previous = app.state.orchestrator
    app.state.orchestrator = FailingOrchestrator()
    try:
        with client.stream(
            "POST", "/api/chat/stream", json={"message": "lookup INC0000057"}
        ) as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())
    finally:
        app.state.orchestrator = previous

    events = _parse_sse(body)
    error = next(e for e in events if e["type"] == "error")
    assert error["error"] == "MCP endpoint unreachable"
    assert "couldn't reach the ServiceNow backend" in error["text"]
