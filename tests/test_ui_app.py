"""FastAPI backend contract tests via Starlette's TestClient (mock mode).

The App Service ``api`` process (``src/helpdesk/ui/app.py``) exposes a single
**AG-UI** endpoint (``POST /agui``) that the CopilotKit / Next.js frontend calls,
plus a ``/healthz`` liveness probe. These tests exercise the real ASGI app
in-process (no network, no Azure) and assert the AG-UI contract — sub-agent
handoff tool pairs, KB citations, and the ServiceNow human-approval interrupt
(approve / reject) — using the sample prompts from ``assets/Sample-Prompts.txt``.

Deterministic orchestrator routing for those same prompts is covered separately
in ``test_orchestrator_flows.py``.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from types import SimpleNamespace

import httpx
import pytest
from agent_framework import Content, Message
from fastapi.testclient import TestClient

ui_app_module = importlib.import_module("helpdesk.ui.app")
app = ui_app_module.app
agui_proxy_module = importlib.import_module("helpdesk.ui.agui_proxy")


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_healthz_liveness_probe(client: TestClient) -> None:
    """App Service liveness probe returns 200 + a stable body."""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# --- AG-UI protocol helpers -------------------------------------------------


def _parse_sse(body: str) -> list[dict]:
    events: list[dict] = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:"):].strip()))
    return events


async def _post_agui(payload: dict) -> list[dict]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post("/agui", json=payload)
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    return _parse_sse(resp.text)


def _text_deltas(events: list[dict]) -> str:
    return "".join(e.get("delta", "") for e in events if e["type"] == "TEXT_MESSAGE_CONTENT")


def _tool_args(events: list[dict], tool_name: str) -> dict:
    call_ids = [
        e["toolCallId"]
        for e in events
        if e["type"] == "TOOL_CALL_START" and e.get("toolCallName") == tool_name
    ]
    assert call_ids, f"expected {tool_name} tool call"
    raw_args = "".join(
        e.get("delta", "")
        for e in events
        if e["type"] == "TOOL_CALL_ARGS" and e.get("toolCallId") == call_ids[-1]
    )
    return json.loads(raw_args)


def _interrupt(events: list[dict]) -> dict:
    finished = [e for e in events if e["type"] == "RUN_FINISHED"]
    assert finished
    outcome = finished[-1].get("outcome")
    assert outcome and outcome["type"] == "interrupt"
    return outcome["interrupts"][0]


# --- AG-UI endpoint contract (mock mode) ------------------------------------


@pytest.mark.asyncio
async def test_agui_endpoint_interrupt_then_approve_executes_mock_update() -> None:
    """Prompt 3 — an update turn proposes a ServiceNow write, interrupts for
    approval, then executes on resume-with-approval."""
    payload = {
        "threadId": "agui-approve-thread",
        "runId": "agui-approve-initial",
        "messages": [
            {
                "id": "user-1",
                "role": "user",
                "content": "update urgency for INC0010027 to low",
            }
        ],
    }
    initial_events = await _post_agui(payload)

    tool_names = [e.get("toolCallName") for e in initial_events if e["type"] == "TOOL_CALL_START"]
    assert "route_orchestrator" in tool_names
    assert "manage_servicenow_incident" in tool_names

    interrupt = _interrupt(initial_events)
    proposal_json = interrupt["metadata"]["agent_framework"]["function_call"]["arguments"][
        "proposal_json"
    ]
    proposal = json.loads(proposal_json)
    assert proposal["operation"] == "update"
    assert proposal["incident_number"] == "INC0010027"
    assert proposal["delta"] == {"urgency": "3"}

    approved_events = await _post_agui(
        {
            **payload,
            "runId": "agui-approve-resume",
            "resume": [
                {
                    "interruptId": interrupt["id"],
                    "status": "resolved",
                    "payload": {"approved": True, "proposal_json": proposal_json},
                }
            ],
        }
    )

    results = [e for e in approved_events if e["type"] == "TOOL_CALL_RESULT"]
    assert results
    assert "Updated incident INC0010027" in results[0]["content"]
    assert "Approved ServiceNow change executed" in _text_deltas(approved_events)
    assert approved_events[-1]["type"] == "RUN_FINISHED"
    assert "outcome" not in approved_events[-1]


@pytest.mark.asyncio
async def test_agui_endpoint_reject_does_not_execute_mock_update() -> None:
    """Rejecting the approval cancels the write — no ServiceNow call is made."""
    payload = {
        "threadId": "agui-reject-thread",
        "runId": "agui-reject-initial",
        "messages": [
            {
                "id": "user-1",
                "role": "user",
                "content": "update urgency for INC0010027 to low",
            }
        ],
    }
    initial_events = await _post_agui(payload)
    interrupt = _interrupt(initial_events)

    rejected_events = await _post_agui(
        {
            **payload,
            "runId": "agui-reject-resume",
            "resume": [
                {
                    "interruptId": interrupt["id"],
                    "status": "resolved",
                    "payload": {"approved": False},
                }
            ],
        }
    )

    assert not [e for e in rejected_events if e["type"] == "TOOL_CALL_RESULT"]
    assert "ServiceNow change cancelled" in _text_deltas(rejected_events)


@pytest.mark.asyncio
async def test_agui_mock_kb_turn_emits_terminal_citations_tool() -> None:
    """Capability 1 — a KB-answerable turn resolves via triage and surfaces a
    terminal ``citations`` tool call (no approval card)."""
    events = await _post_agui(
        {
            "threadId": "agui-mock-citations-thread",
            "runId": "agui-mock-citations-run",
            "messages": [
                {
                    "id": "user-1",
                    "role": "user",
                    "content": "How do I reset my forgotten password?",
                }
            ],
        }
    )

    marker = "\u30104:0\u2020source\u3011"
    text = _text_deltas(events)
    assert marker in text

    payload = _tool_args(events, "citations")
    assert payload["citations"] == [
        {
            "index": 1,
            "sourceId": "password-reset",
            "sourceTitle": "Password Reset and Login Assistance",
            "sourceName": "password-reset.md",
            "assignmentGroup": "Service Desk",
            "markers": [marker],
            "chunkIds": ["password-reset-mock-0"],
            "url": "mcp://searchindex/password-reset-mock-0",
        }
    ]
    citation_start = next(
        i
        for i, event in enumerate(events)
        if event["type"] == "TOOL_CALL_START" and event.get("toolCallName") == "citations"
    )
    last_text = max(i for i, event in enumerate(events) if event["type"] == "TEXT_MESSAGE_CONTENT")
    assert citation_start > last_text
    assert "outcome" not in events[-1]


@pytest.mark.asyncio
async def test_agui_mock_status_turn_has_no_approval_or_citations() -> None:
    """Capability 3 — a read-only status lookup routes to the incident agent
    with no approval interrupt and no citations."""
    events = await _post_agui(
        {
            "threadId": "agui-mock-status-thread",
            "runId": "agui-mock-status-run",
            "messages": [
                {
                    "id": "user-1",
                    "role": "user",
                    "content": "lookup details for incident INC0000057",
                }
            ],
        }
    )

    tool_names = [e.get("toolCallName") for e in events if e["type"] == "TOOL_CALL_START"]
    assert "manage_servicenow_incident" in tool_names
    assert "citations" not in tool_names
    assert "servicenow_write_approval" not in tool_names
    assert "function_approval_request" not in [e.get("name") for e in events if e["type"] == "CUSTOM"]
    assert "\u3010" not in _text_deltas(events)
    assert "outcome" not in events[-1]


def test_agui_live_proxy_emits_citations_tool_side_channel() -> None:
    """The live proxy maps a hosted-orchestrator handoff + terminal citations
    function_call into AG-UI tool pairs (route -> triage -> citations)."""
    citations = [
        {
            "index": 1,
            "sourceId": "laptop-performance",
            "sourceTitle": "Laptop Running Slow",
            "sourceName": "laptop-performance.md",
            "assignmentGroup": "Desktop Support",
            "markers": ["\u30105:0\u2020source\u3011"],
            "chunkIds": ["laptop-performance-2"],
            "url": "mcp://searchindex/laptop-performance-2",
        }
    ]
    events = [
        SimpleNamespace(
            type="response.output_item.added",
            item=SimpleNamespace(
                type="function_call",
                id="triage-call",
                name="troubleshoot_from_knowledge_base",
            ),
        ),
        SimpleNamespace(type="response.output_text.delta", delta="Try these steps."),
        SimpleNamespace(
            type="response.function_call_arguments.done",
            name="citations",
            item_id="citations-call",
            arguments=json.dumps({"citations": citations}),
        ),
        SimpleNamespace(type="response.completed"),
    ]

    class _FakeResponses:
        def create(self, **_kwargs):
            return iter(events)

    class _FakeClient:
        responses = _FakeResponses()

    proxy = agui_proxy_module.HelpdeskAGUIProxyAgent(
        settings_factory=lambda: SimpleNamespace(mock_mode=False, chat_deployment="test-model"),
        mock_orchestrator_factory=lambda: None,
        openai_client_factory=lambda: _FakeClient(),
    )

    async def _collect():
        return [
            update
            async for update in proxy.run(
                messages=[Message("user", [Content.from_text("my laptop is slow")])],
                stream=True,
            )
        ]

    updates = asyncio.run(_collect())
    function_calls = [
        content
        for update in updates
        for content in update.contents
        if content.type == "function_call"
    ]

    assert [call.name for call in function_calls[:2]] == [
        "route_orchestrator",
        "troubleshoot_from_knowledge_base",
    ]
    citation_call = next(call for call in function_calls if call.name == "citations")
    assert citation_call.arguments["citations"] == citations
