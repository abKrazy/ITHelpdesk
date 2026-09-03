"""The MAF Foundry Hosted Agent entrypoint (``src/orchestrator/main.py``).

Loaded by file path (it ships as a standalone container app, not part of the
``helpdesk`` package) and exercised offline. The orchestrator now does ONE
routing model pass and then streams the chosen sub-agent's answer straight
through as the terminal reply — there is NO second (relay) model generation.
These tests pin that routing-then-proxy behavior offline:

* the module imports and advertises exactly two routing tools that map to the
  correct Foundry Prompt Agents by name;
* the routing pass classifies intent and returns a self-contained sub-agent
  input (create-on-confirm must be self-contained from history);
* the streaming proxy emits the handoff ``function_call`` chip FIRST, then the
  sub-agent's text deltas — the exact outer SSE shape the UI consumes — and it
  forwards ONLY ``response.output_text.delta`` events from the inner stream;
* sub-agents are still invoked with their OWN model deployment (Foundry rejects
  a model/agent mismatch with HTTP 400).
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_MAIN_PATH = Path(__file__).resolve().parents[1] / "src" / "orchestrator" / "main.py"


@pytest.fixture(scope="module")
def orchestrator_main():
    spec = importlib.util.spec_from_file_location("orchestrator_main", _MAIN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    # Register so dataclass/typing resolution works when loaded by file path.
    sys.modules["orchestrator_main"] = module
    spec.loader.exec_module(module)
    return module


# --- helpers ------------------------------------------------------------------
def _fc_item(name: str, arguments: str, call_id: str = "call_abc"):
    return SimpleNamespace(
        type="function_call", name=name, arguments=arguments, call_id=call_id
    )


def _drain_stream(agen) -> list:
    """Collect every AgentResponseUpdate from an async generator synchronously."""

    async def _collect():
        out = []
        async for update in agen:
            out.append(update)
        return out

    return asyncio.run(_collect())


class ResourceNotFoundError(Exception):
    pass


class _FakeBlob:
    def __init__(self) -> None:
        self.data: bytes | None = None
        self.uploads: list[dict[str, object]] = []

    def download_blob(self):
        if self.data is None:
            raise ResourceNotFoundError()
        return SimpleNamespace(readall=lambda: self.data)

    def upload_blob(self, data, **kwargs) -> None:
        self.data = data
        self.uploads.append({"data": data, **kwargs})


class _FakeContainer:
    def __init__(self) -> None:
        self.blobs: dict[str, _FakeBlob] = {}

    def get_blob_client(self, name):
        return self.blobs.setdefault(name, _FakeBlob())


class _FakeBlobServiceClient:
    instances: list["_FakeBlobServiceClient"] = []

    def __init__(self, *, account_url, credential, **kwargs):
        self.account_url = account_url
        self.credential = credential
        self.kwargs = kwargs
        self.containers: dict[str, _FakeContainer] = {}
        _FakeBlobServiceClient.instances.append(self)

    def get_container_client(self, name):
        return self.containers.setdefault(name, _FakeContainer())


class _FakeDefaultAzureCredential:
    instances: list["_FakeDefaultAzureCredential"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _FakeDefaultAzureCredential.instances.append(self)


def _install_fake_blob_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    for name in ["azure", "azure.identity", "azure.storage", "azure.storage.blob"]:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))

    sys.modules["azure.identity"].DefaultAzureCredential = _FakeDefaultAzureCredential
    sys.modules["azure.storage.blob"].BlobServiceClient = _FakeBlobServiceClient


# --- module surface -----------------------------------------------------------
def test_module_imports_and_exposes_two_routing_tools(orchestrator_main) -> None:
    tools = orchestrator_main.ROUTING_TOOLS
    assert len(tools) == 2
    names = {t["name"] for t in tools}
    assert names == {"troubleshoot_from_knowledge_base", "manage_servicenow_incident"}
    # Flat Responses function-tool shape.
    for t in tools:
        assert t["type"] == "function"
        assert t["parameters"]["type"] == "object"


def test_routing_tools_map_to_correct_prompt_agents(orchestrator_main) -> None:
    assert orchestrator_main._AGENT_BY_TOOL == {
        "troubleshoot_from_knowledge_base": "it-helpdesk-triage",
        "manage_servicenow_incident": "it-helpdesk-incident",
    }
    assert orchestrator_main._ARG_FIELD_BY_TOOL == {
        "troubleshoot_from_knowledge_base": "problem",
        "manage_servicenow_incident": "request",
    }


def test_default_sub_agent_names(orchestrator_main) -> None:
    assert orchestrator_main.TRIAGE_AGENT_NAME == "it-helpdesk-triage"
    assert orchestrator_main.INCIDENT_AGENT_NAME == "it-helpdesk-incident"
    assert orchestrator_main.PORT == 8088
    assert orchestrator_main.ORCHESTRATOR_CONTRACT_VERSION == "agui-proposal-mode-v1"


def test_build_agent_returns_relay_orchestrator(orchestrator_main) -> None:
    agent = orchestrator_main.build_agent()
    assert isinstance(agent, orchestrator_main.RelayOrchestrator)
    assert agent.name == "it-helpdesk-orchestrator"


def test_record_kb_gap_writes_expected_blob_record(orchestrator_main, monkeypatch) -> None:
    _FakeBlobServiceClient.instances.clear()
    _FakeDefaultAzureCredential.instances.clear()
    _install_fake_blob_sdk(monkeypatch)
    monkeypatch.setenv("AZURE_STORAGE_BLOB_ENDPOINT", "https://storage.example.net/")
    monkeypatch.setenv("AZURE_STORAGE_KB_CONTAINER", "custom-kb")
    monkeypatch.setenv("AZURE_CLIENT_ID", "uami-client-id")
    monkeypatch.setattr(orchestrator_main, "KB_GAP_HARVEST_ENABLED", "")
    monkeypatch.setattr(orchestrator_main, "_get_tracer", lambda: None)
    monkeypatch.setattr(orchestrator_main, "_kb_gap_credential", None)
    monkeypatch.setattr(orchestrator_main, "_kb_gap_credential_client_id", None)
    monkeypatch.setattr(orchestrator_main, "_kb_gap_blob_service_client", None)
    monkeypatch.setattr(orchestrator_main, "_kb_gap_blob_service_key", None)

    question = "How do I set up dual monitors?"
    orchestrator_main._record_kb_gap(
        question,
        "kb_insufficient",
        had_citations=False,
        tool="troubleshoot_from_knowledge_base",
    )

    question_hash = hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]
    client = _FakeBlobServiceClient.instances[0]
    assert client.account_url == "https://storage.example.net/"
    assert client.kwargs == {"connection_timeout": 5, "read_timeout": 10}
    assert _FakeDefaultAzureCredential.instances[0].kwargs == {
        "managed_identity_client_id": "uami-client-id"
    }

    blob = client.containers["custom-kb"].blobs[f"_system/kb-gaps/{question_hash}.json"]
    assert blob.uploads[-1]["overwrite"] is True
    record = json.loads(blob.data.decode("utf-8"))
    assert record == {
        "question_hash": question_hash,
        "question": question,
        "status": "new",
        "reason": "kb_insufficient",
        "reasons": ["kb_insufficient"],
        "tool": "troubleshoot_from_knowledge_base",
        "had_citations": False,
        "occurrence_count": 1,
        "first_seen_at": record["first_seen_at"],
        "last_seen_at": record["last_seen_at"],
    }
    assert record["first_seen_at"].endswith("Z")
    assert record["last_seen_at"].endswith("Z")
    assert blob.data == json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")

    first_seen = record["first_seen_at"]
    orchestrator_main._record_kb_gap(question, "triage_unresolved", had_citations=True)
    merged = json.loads(blob.data.decode("utf-8"))
    assert merged["occurrence_count"] == 2
    assert merged["first_seen_at"] == first_seen
    assert merged["reason"] == "triage_unresolved"
    assert merged["reasons"] == ["kb_insufficient", "triage_unresolved"]
    assert merged["had_citations"] is True


def test_record_kb_gap_skips_blob_write_without_endpoint(
    orchestrator_main, monkeypatch
) -> None:
    monkeypatch.delenv("AZURE_STORAGE_BLOB_ENDPOINT", raising=False)
    monkeypatch.setattr(orchestrator_main, "KB_GAP_HARVEST_ENABLED", "")
    monkeypatch.setattr(orchestrator_main, "_get_tracer", lambda: None)

    def _fail_blob_service(endpoint):  # pragma: no cover - must not be called
        raise AssertionError(f"unexpected Blob write for {endpoint}")

    monkeypatch.setattr(orchestrator_main, "_kb_gap_blob_service", _fail_blob_service)

    orchestrator_main._record_kb_gap("How do I set up dual monitors?", "kb_insufficient")


# --- routing instructions -----------------------------------------------------
def test_routing_instructions_encode_rules(orchestrator_main) -> None:
    instructions = orchestrator_main.ROUTING_INSTRUCTIONS
    # Intent classification runs before deflect-first.
    assert "CLASSIFY INTENT FIRST" in instructions
    assert instructions.index("CLASSIFY INTENT FIRST") < instructions.index(
        "DEFLECT FIRST"
    ), "intent classification must come before DEFLECT FIRST"
    # Status/lookup/update intents must skip triage / KB retrieval entirely.
    assert "MUST skip triage entirely" in instructions
    # Deflect-first: KB before any ticket (intent A only).
    assert "troubleshoot_from_knowledge_base FIRST" in instructions
    # Create only on confirmation, with a self-contained request from history.
    assert "CREATE ONLY ON CONFIRMATION" in instructions
    assert "self-contained" in instructions
    # The router must NOT relay/repeat the sub-agent's answer.
    assert "do NOT relay" in instructions or "not relay" in instructions.lower()


# --- reasoning knob -----------------------------------------------------------
def test_reasoning_effort_defaults_to_low(orchestrator_main) -> None:
    assert orchestrator_main.REASONING_EFFORT == "low"


def test_reasoning_option_pins_effort(orchestrator_main, monkeypatch) -> None:
    monkeypatch.setattr(orchestrator_main, "REASONING_EFFORT", "low")
    assert orchestrator_main._reasoning_option() == {"effort": "low"}
    monkeypatch.setattr(orchestrator_main, "REASONING_EFFORT", "MEDIUM")
    assert orchestrator_main._reasoning_option() == {"effort": "medium"}


def test_reasoning_option_omitted_when_unset(orchestrator_main, monkeypatch) -> None:
    for sentinel in ("", "default", "DEFAULT"):
        monkeypatch.setattr(orchestrator_main, "REASONING_EFFORT", sentinel)
        assert orchestrator_main._reasoning_option() is None


# --- message flattening + arg extraction --------------------------------------
def test_messages_to_input_flattens_history(orchestrator_main) -> None:
    from agent_framework import Message

    msgs = [
        Message("user", ["my laptop is slow"]),
        Message("assistant", ["Did these steps resolve the issue?"]),
        Message("user", ["no, please file it"]),
    ]
    items = orchestrator_main._messages_to_input(msgs)
    assert items == [
        {"role": "user", "content": "my laptop is slow"},
        {"role": "assistant", "content": "Did these steps resolve the issue?"},
        {"role": "user", "content": "no, please file it"},
    ]


def test_messages_to_input_accepts_plain_string(orchestrator_main) -> None:
    assert orchestrator_main._messages_to_input("vpn down") == [
        {"role": "user", "content": "vpn down"}
    ]


def test_messages_to_input_lifts_assignment_group_from_citations_side_channel(
    orchestrator_main,
) -> None:
    msg = SimpleNamespace(
        role="assistant",
        text="Try these steps.",
        contents=[
            SimpleNamespace(
                type="function_call",
                name="citations",
                arguments=json.dumps(
                    {"citations": [{"assignmentGroup": "Desktop Support"}]}
                ),
            )
        ],
    )

    assert orchestrator_main._messages_to_input([msg]) == [
        {
            "role": "assistant",
            "content": "Try these steps.\n\nRecommended Assignment Group: Desktop Support",
        }
    ]


def test_tool_args_to_message_reads_schema_field(orchestrator_main) -> None:
    assert (
        orchestrator_main._tool_args_to_message(
            "troubleshoot_from_knowledge_base", '{"problem":"vpn is down"}'
        )
        == "vpn is down"
    )
    assert (
        orchestrator_main._tool_args_to_message(
            "manage_servicenow_incident",
            '{"request":"create an incident for: laptop slow; assign to Desktop Support"}',
        )
        == "create an incident for: laptop slow; assign to Desktop Support"
    )


def test_tool_args_to_message_tolerates_bad_json(orchestrator_main) -> None:
    assert (
        orchestrator_main._tool_args_to_message(
            "troubleshoot_from_knowledge_base", "not json"
        )
        == "not json"
    )


# --- routing pass -------------------------------------------------------------
def _patch_route_client(orchestrator_main, monkeypatch, response):
    class _FakeResponses:
        def create(self, **kwargs):
            self.last_kwargs = kwargs
            return response

    fake = _FakeResponses()

    class _FakeClient:
        responses = fake

    monkeypatch.setattr(orchestrator_main, "_get_openai_client", lambda: _FakeClient())
    return fake


def test_route_intent_selects_triage(orchestrator_main, monkeypatch) -> None:
    resp = SimpleNamespace(
        output=[_fc_item("troubleshoot_from_knowledge_base", '{"problem":"laptop slow"}')],
        output_text=None,
    )
    fake = _patch_route_client(orchestrator_main, monkeypatch, resp)

    decision = orchestrator_main._route_intent([{"role": "user", "content": "laptop slow"}])
    assert decision.tool_name == "troubleshoot_from_knowledge_base"
    assert decision.agent_name == "it-helpdesk-triage"
    assert decision.sub_agent_input == "laptop slow"
    assert decision.direct_text is None
    # Routing pass runs ONE model call with tools, no parallel tool calls.
    assert fake.last_kwargs["tools"] is orchestrator_main.ROUTING_TOOLS
    assert fake.last_kwargs["parallel_tool_calls"] is False
    assert fake.last_kwargs["store"] is False


def test_route_intent_selects_incident_for_status(orchestrator_main, monkeypatch) -> None:
    resp = SimpleNamespace(
        output=[_fc_item("manage_servicenow_incident", '{"request":"status of INC0010045"}')],
        output_text=None,
    )
    _patch_route_client(orchestrator_main, monkeypatch, resp)

    decision = orchestrator_main._route_intent(
        [{"role": "user", "content": "what is the status of INC0010045?"}]
    )
    assert decision.tool_name == "manage_servicenow_incident"
    assert decision.agent_name == "it-helpdesk-incident"
    assert decision.sub_agent_input == "status of INC0010045"
    assert decision.servicenow_write_proposal is None


def test_route_intent_create_confirm_is_self_contained(orchestrator_main, monkeypatch) -> None:
    """create-on-confirm: the router reads the ORIGINAL problem + assignment group
    out of history so the incident sub-agent input is self-contained even though
    the latest user turn is just "yes, create it"."""
    args = json.dumps(
        {"request": "create an incident for: my laptop is running slow; assign to Desktop Support"}
    )
    resp = SimpleNamespace(
        output=[_fc_item("manage_servicenow_incident", args)], output_text=None
    )
    _patch_route_client(orchestrator_main, monkeypatch, resp)

    decision = orchestrator_main._route_intent(
        [
            {"role": "user", "content": "my laptop is running slow, open a ticket"},
            {"role": "assistant", "content": "Try... Assignment Group: Desktop Support. Did these help?"},
            {"role": "user", "content": "yes, create it"},
        ]
    )
    assert decision.agent_name == "it-helpdesk-incident"
    assert "my laptop is running slow" in decision.sub_agent_input
    assert "Desktop Support" in decision.sub_agent_input


def test_route_intent_enriches_create_request_from_history_assignment_group(
    orchestrator_main, monkeypatch
) -> None:
    """If the routing model omits assignment_group on create, the orchestrator
    injects the latest triage recommendation before invoking the Incident agent."""
    resp = SimpleNamespace(
        output=[
            _fc_item(
                "manage_servicenow_incident",
                '{"request":"create an incident for: my laptop is running slow"}',
            )
        ],
        output_text=None,
    )
    _patch_route_client(orchestrator_main, monkeypatch, resp)

    decision = orchestrator_main._route_intent(
        [
            {"role": "user", "content": "my laptop is running slow"},
            {
                "role": "assistant",
                "content": "Try these steps.\n\nRecommended Assignment Group: Desktop Support",
            },
            {"role": "user", "content": "create the ticket"},
        ]
    )

    assert decision.agent_name == "it-helpdesk-incident"
    assert decision.sub_agent_input.endswith("assignment_group: Desktop Support")
    assert json.loads(decision.arguments_json)["request"] == decision.sub_agent_input
    assert decision.servicenow_write_proposal is not None
    assert decision.servicenow_write_proposal["operation"] == "create"
    assert decision.servicenow_write_proposal["assignment_group"] == "Desktop Support"


def test_route_intent_update_returns_write_proposal(orchestrator_main, monkeypatch) -> None:
    resp = SimpleNamespace(
        output=[
            _fc_item(
                "manage_servicenow_incident",
                '{"request":"update urgency for INC0010027 to low"}',
            )
        ],
        output_text=None,
    )
    _patch_route_client(orchestrator_main, monkeypatch, resp)

    decision = orchestrator_main._route_intent(
        [{"role": "user", "content": "update urgency for INC0010027 to low"}]
    )

    assert decision.agent_name == "it-helpdesk-incident"
    assert decision.servicenow_write_proposal == {
        "operation": "update",
        "incident_number": "INC0010027",
        "delta": {"urgency": "3"},
        "summary": "update urgency for INC0010027 to low",
    }


def test_route_intent_direct_reply_when_no_tool(orchestrator_main, monkeypatch) -> None:
    resp = SimpleNamespace(output=[], output_text="Could you share the incident number?")
    _patch_route_client(orchestrator_main, monkeypatch, resp)

    decision = orchestrator_main._route_intent([{"role": "user", "content": "help"}])
    assert decision.tool_name is None
    assert decision.agent_name is None
    assert decision.direct_text == "Could you share the incident number?"


# --- streaming proxy ----------------------------------------------------------
def test_iter_prompt_agent_text_forwards_only_output_text_delta(
    orchestrator_main, monkeypatch
) -> None:
    """The proxy must forward ONLY response.output_text.delta events so the inner
    sub-agent's own tool calls never leak as bogus handoff chips on the outer
    stream. Citations arrive inline in text deltas and are preserved."""
    events = [
        SimpleNamespace(type="response.created"),
        SimpleNamespace(type="response.output_item.added"),
        # inner sub-agent tool call — MUST be dropped
        SimpleNamespace(type="response.function_call_arguments.delta", delta='{"q":'),
        SimpleNamespace(type="response.output_text.delta", delta="1. Restart. "),
        SimpleNamespace(type="response.output_text.delta", delta="See [1]\u2020source."),
        SimpleNamespace(type="response.output_text.done", text="ignored"),
        SimpleNamespace(type="response.completed"),
    ]

    class _FakeResponses:
        def create(self, **kwargs):
            assert kwargs["stream"] is True
            assert kwargs["model"] == "gpt-5.4-mini"
            assert kwargs["extra_body"]["agent_reference"]["name"] == "it-helpdesk-triage"
            return iter(events)

    class _FakeClient:
        responses = _FakeResponses()

    monkeypatch.setattr(orchestrator_main, "_get_openai_client", lambda: _FakeClient())
    monkeypatch.setattr(
        orchestrator_main,
        "_MODEL_BY_AGENT",
        {"it-helpdesk-triage": "gpt-5.4-mini", "it-helpdesk-incident": "gpt-5.4"},
    )

    out = list(
        orchestrator_main._iter_prompt_agent_text("it-helpdesk-triage", "laptop slow")
    )
    assert out == ["1. Restart. ", "See [1]\u2020source."]


# --- KB citation side-channel -------------------------------------------------
_MCP_OUTPUT = (
    "Retrieved 5 documents.\n\n"
    "\u30105:0\u2020source\u3011\n"
    '{"id":"laptop-performance-2","doc_id":"laptop-performance",'
    '"title":"Laptop Running Slow","source":"laptop-performance.md",'
    '"assignment_group":"Desktop Support","content":"steps"}\n'
    "\u30105:1\u2020source\u3011\n"
    '{"id":"laptop-performance-3","doc_id":"laptop-performance",'
    '"title":"Laptop Running Slow","source":"laptop-performance.md",'
    '"assignment_group":"Desktop Support"}\n'
    "\u30105:4\u2020source\u3011\n"
    '{"id":"laptop-performance-4","doc_id":"laptop-performance",'
    '"title":"Laptop Running Slow","source":"laptop-performance.md",'
    '"assignment_group":"Desktop Support"}\nVisible: 0% - 100%"'
)
_ANSWER_TEXT = (
    "1. Restart.\u30105:0\u2020source\u3011\u30105:1\u2020source\u3011"
    "\u30105:4\u2020source\u3011\n\nRecommended Assignment Group: Desktop "
    "Support.\u30105:4\u2020source\u3011\n\nNo ticket yet."
)


def test_parse_mcp_output_chunks_resolves_real_titles(orchestrator_main) -> None:
    """The KB mcp_call output carries the REAL per-marker document metadata
    (friendly title + filename + doc_id), which the inline ``†source`` marker does
    NOT. Parsing it must map each marker to its document."""
    chunks = orchestrator_main._parse_mcp_output_chunks(_MCP_OUTPUT)
    assert chunks["\u30105:0\u2020source\u3011"] == {
        "chunkId": "laptop-performance-2",
        "docId": "laptop-performance",
        "title": "Laptop Running Slow",
        "source": "laptop-performance.md",
        "assignmentGroup": "Desktop Support",
    }
    assert chunks["\u30105:4\u2020source\u3011"]["chunkId"] == "laptop-performance-4"


def test_build_citations_dedupes_by_source_and_numbers_by_appearance(
    orchestrator_main,
) -> None:
    """Distinct markers pointing at the same document collapse to ONE numbered
    source; every marker that maps to it is listed so the UI can replace each
    inline ``【…】`` with the same ``[n]``. Repeated markers do not add entries."""
    chunks = orchestrator_main._parse_mcp_output_chunks(_MCP_OUTPUT)
    cits = orchestrator_main._build_citations(_ANSWER_TEXT, chunks, {})
    assert len(cits) == 1
    entry = cits[0]
    assert entry["index"] == 1
    assert entry["sourceTitle"] == "Laptop Running Slow"
    assert entry["sourceName"] == "laptop-performance.md"
    assert entry["sourceId"] == "laptop-performance"
    assert entry["markers"] == [
        "\u30105:0\u2020source\u3011",
        "\u30105:1\u2020source\u3011",
        "\u30105:4\u2020source\u3011",
    ]
    assert entry["chunkIds"] == [
        "laptop-performance-2",
        "laptop-performance-3",
        "laptop-performance-4",
    ]


def test_build_citations_two_distinct_docs_get_sequential_numbers(
    orchestrator_main,
) -> None:
    """Two different documents -> two numbered sources in first-appearance order."""
    chunks = {
        "\u30101:0\u2020source\u3011": {
            "chunkId": "vpn-1",
            "docId": "vpn-connect",
            "title": "VPN Connection",
            "source": "vpn-connect.md",
            "assignmentGroup": "Network",
        },
        "\u30101:1\u2020source\u3011": {
            "chunkId": "pw-1",
            "docId": "password-reset",
            "title": "Password Reset",
            "source": "password-reset.md",
            "assignmentGroup": "Identity",
        },
    }
    text = "A\u30101:0\u2020source\u3011 then B\u30101:1\u2020source\u3011 end."
    cits = orchestrator_main._build_citations(text, chunks, {})
    assert [c["index"] for c in cits] == [1, 2]
    assert [c["sourceTitle"] for c in cits] == ["VPN Connection", "Password Reset"]


def test_build_citations_empty_when_no_markers(orchestrator_main) -> None:
    assert orchestrator_main._build_citations("no markers here", {}, {}) == []


def test_iter_prompt_agent_text_populates_citations_sink(
    orchestrator_main, monkeypatch
) -> None:
    """With a sink provided, the proxy reads the mcp_call output + annotations off
    the SAME inner stream and resolves citations — WITHOUT altering yielded text."""
    events = [
        SimpleNamespace(
            type="response.output_item.done",
            item=SimpleNamespace(type="mcp_call", output=_MCP_OUTPUT),
        ),
        SimpleNamespace(type="response.output_text.delta", delta="1. Restart."),
        SimpleNamespace(
            type="response.output_text.delta",
            delta="\u30105:0\u2020source\u3011\u30105:1\u2020source\u3011",
        ),
        SimpleNamespace(
            type="response.output_text.annotation.added",
            annotation=SimpleNamespace(
                type="url_citation",
                title="mcp://searchindex/laptop-performance-2",
                url="mcp://searchindex/laptop-performance-2",
            ),
        ),
        SimpleNamespace(type="response.completed"),
    ]

    class _FakeResponses:
        def create(self, **kwargs):
            return iter(events)

    class _FakeClient:
        responses = _FakeResponses()

    monkeypatch.setattr(orchestrator_main, "_get_openai_client", lambda: _FakeClient())
    monkeypatch.setattr(
        orchestrator_main,
        "_MODEL_BY_AGENT",
        {"it-helpdesk-triage": "gpt-5.4-mini", "it-helpdesk-incident": "gpt-5.4"},
    )

    sink: list = []
    out = list(
        orchestrator_main._iter_prompt_agent_text(
            "it-helpdesk-triage", "laptop slow", citations_sink=sink
        )
    )
    # Text is byte-equivalent to the deltas (markers preserved inline).
    assert out == ["1. Restart.", "\u30105:0\u2020source\u3011\u30105:1\u2020source\u3011"]
    assert len(sink) == 1
    assert sink[0]["sourceTitle"] == "Laptop Running Slow"
    assert sink[0]["url"] == "mcp://searchindex/laptop-performance-2"


def test_run_stream_emits_terminal_citations_frame(
    orchestrator_main, monkeypatch
) -> None:
    """A KB turn's citations surface as a terminal ``citations`` function_call
    item AFTER the text — a structured side-channel, not part of the answer text.
    The handoff chip stays first and the token text is unchanged."""
    decision = orchestrator_main.RouteDecision(
        tool_name="troubleshoot_from_knowledge_base",
        agent_name="it-helpdesk-triage",
        sub_agent_input="laptop slow",
        call_id="call_123",
        arguments_json='{"problem":"laptop slow"}',
    )
    monkeypatch.setattr(orchestrator_main, "_route_intent", lambda items: decision)

    citations = [
        {
            "index": 1,
            "sourceId": "laptop-performance",
            "sourceTitle": "Laptop Running Slow",
            "sourceName": "laptop-performance.md",
            "markers": ["\u30105:0\u2020source\u3011"],
        }
    ]

    async def _fake_astream(agent_name, message):
        yield "1. Restart.\u30105:0\u2020source\u3011"
        yield orchestrator_main._CitationsFrame(citations)

    monkeypatch.setattr(orchestrator_main, "_astream_prompt_agent", _fake_astream)

    agent = orchestrator_main.build_agent()
    updates = _drain_stream(agent.run(messages="my laptop is slow", stream=True))

    # chip -> text -> citations function_call (in that order).
    assert updates[0].contents[0].type == "function_call"
    assert updates[0].contents[0].name == "troubleshoot_from_knowledge_base"
    assert updates[1].contents[0].type == "text"
    assert updates[1].contents[0].text == "1. Restart.\u30105:0\u2020source\u3011"
    last = updates[-1].contents[0]
    assert last.type == "function_call"
    assert last.name == "citations"
    payload = json.loads(last.arguments)
    assert payload["citations"][0]["sourceTitle"] == "Laptop Running Slow"
    assert payload["citations"][0]["index"] == 1


def test_run_stream_emits_proposal_without_invoking_incident(
    orchestrator_main, monkeypatch
) -> None:
    decision = orchestrator_main.RouteDecision(
        tool_name="manage_servicenow_incident",
        agent_name="it-helpdesk-incident",
        sub_agent_input="update urgency for INC0010027 to low",
        call_id="call_123",
        arguments_json='{"request":"update urgency for INC0010027 to low"}',
        servicenow_write_proposal={
            "operation": "update",
            "incident_number": "INC0010027",
            "delta": {"urgency": "3"},
            "summary": "update urgency for INC0010027 to low",
        },
    )
    monkeypatch.setattr(orchestrator_main, "_route_intent", lambda items: decision)

    async def _should_not_run(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("incident agent should not execute before approval")
        yield ""

    monkeypatch.setattr(orchestrator_main, "_astream_prompt_agent", _should_not_run)

    agent = orchestrator_main.build_agent()
    updates = _drain_stream(agent.run(messages="update urgency for INC0010027 to low", stream=True))

    assert updates[0].contents[0].name == "manage_servicenow_incident"
    proposal_call = updates[1].contents[0]
    assert proposal_call.type == "function_call"
    assert proposal_call.name == "servicenow_write_proposal"
    assert json.loads(proposal_call.arguments)["operation"] == "update"


def test_run_stream_execute_approved_reinvokes_incident_agent(
    orchestrator_main, monkeypatch
) -> None:
    proposal = {
        "operation": "update",
        "incident_number": "INC0010027",
        "delta": {"urgency": "3"},
        "summary": "update urgency for INC0010027 to low",
    }
    command = orchestrator_main._approved_proposal_command(proposal)
    captured: dict[str, str] = {}

    async def _fake_astream(agent_name, message):
        captured["agent_name"] = agent_name
        captured["message"] = message
        yield "Updated incident INC0010027: urgency=Low."

    monkeypatch.setattr(orchestrator_main, "_astream_prompt_agent", _fake_astream)

    agent = orchestrator_main.build_agent()
    updates = _drain_stream(agent.run(messages=command, stream=True))

    assert updates[0].contents[0].name == "manage_servicenow_incident"
    assert captured["agent_name"] == "it-helpdesk-incident"
    assert "APPROVED BY USER" in captured["message"]
    assert "INC0010027" in captured["message"]
    assert updates[1].contents[0].text == "Updated incident INC0010027: urgency=Low."


def test_run_stream_emits_chip_then_text(orchestrator_main, monkeypatch) -> None:
    """GOLDEN SSE SHAPE: streaming a routed turn yields the handoff function_call
    chip FIRST (so the UI shows "Calling Triage Agent"), then the sub-agent's text
    deltas — with NO second orchestrator model generation in between."""
    decision = orchestrator_main.RouteDecision(
        tool_name="troubleshoot_from_knowledge_base",
        agent_name="it-helpdesk-triage",
        sub_agent_input="laptop slow",
        call_id="call_123",
        arguments_json='{"problem":"laptop slow"}',
    )
    monkeypatch.setattr(orchestrator_main, "_route_intent", lambda items: decision)

    async def _fake_astream(agent_name, message):
        assert agent_name == "it-helpdesk-triage"
        for chunk in ["1. Restart your VPN client. ", "Did these steps resolve it?"]:
            yield chunk

    monkeypatch.setattr(orchestrator_main, "_astream_prompt_agent", _fake_astream)

    agent = orchestrator_main.build_agent()
    updates = _drain_stream(agent.run(messages="my laptop is slow", stream=True))

    # First update = the handoff chip (function_call) the UI consumes.
    first = updates[0].contents[0]
    assert first.type == "function_call"
    assert first.name == "troubleshoot_from_knowledge_base"
    assert first.call_id == "call_123"
    assert first.arguments == '{"problem":"laptop slow"}'
    # Remaining updates = the sub-agent's answer streamed straight through.
    texts = [u.contents[0].text for u in updates[1:]]
    assert texts == ["1. Restart your VPN client. ", "Did these steps resolve it?"]


def test_run_stream_direct_reply_has_no_chip(orchestrator_main, monkeypatch) -> None:
    """A direct clarifying reply from the routing pass streams through as text with
    NO function_call chip (no sub-agent was selected)."""
    decision = orchestrator_main.RouteDecision(direct_text="Which incident number?")
    monkeypatch.setattr(orchestrator_main, "_route_intent", lambda items: decision)

    agent = orchestrator_main.build_agent()
    updates = _drain_stream(agent.run(messages="help me", stream=True))
    assert all(c.type == "text" for u in updates for c in u.contents)
    assert "".join(c.text for u in updates for c in u.contents) == "Which incident number?"


def test_run_nonstream_returns_terminal_message(orchestrator_main, monkeypatch) -> None:
    decision = orchestrator_main.RouteDecision(
        tool_name="manage_servicenow_incident",
        agent_name="it-helpdesk-incident",
        sub_agent_input="status of INC0010045",
        call_id="call_9",
        arguments_json='{"request":"status of INC0010045"}',
    )
    monkeypatch.setattr(orchestrator_main, "_route_intent", lambda items: decision)
    monkeypatch.setattr(
        orchestrator_main,
        "_invoke_prompt_agent",
        lambda agent, message: f"INC0010045 is In Progress ({agent})",
    )

    agent = orchestrator_main.build_agent()
    response = asyncio.run(agent.run(messages="status of INC0010045", stream=False))
    text = response.messages[0].contents[0].text
    assert "INC0010045 is In Progress" in text


# --- model-per-agent regression (unchanged behavior) --------------------------
def test_call_prompt_agent_uses_each_agents_own_model(orchestrator_main, monkeypatch) -> None:
    """Regression: invoking a Prompt Agent by agent_reference MUST pass that
    agent's own deployment. The Foundry Responses API rejects a mismatch with
    400 ("Model must match the agent's model ... when agent is specified"), which
    is what broke KB deflection when triage moved to gpt-5.4-mini while the
    orchestrator kept passing its own gpt-5.4 model for every sub-agent call.
    """
    captured: list[dict] = []

    class _FakeResponses:
        def create(self, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(output_text="ok", output=None)

    class _FakeClient:
        responses = _FakeResponses()

    monkeypatch.setattr(orchestrator_main, "_get_openai_client", lambda: _FakeClient())
    monkeypatch.setattr(
        orchestrator_main,
        "_MODEL_BY_AGENT",
        {"it-helpdesk-triage": "gpt-5.4-mini", "it-helpdesk-incident": "gpt-5.4"},
    )

    orchestrator_main._call_prompt_agent("it-helpdesk-triage", "laptop slow")
    orchestrator_main._call_prompt_agent("it-helpdesk-incident", "status of INC0010036")

    assert captured[0]["model"] == "gpt-5.4-mini"
    assert captured[0]["extra_body"]["agent_reference"]["name"] == "it-helpdesk-triage"
    assert captured[1]["model"] == "gpt-5.4"
    assert captured[1]["extra_body"]["agent_reference"]["name"] == "it-helpdesk-incident"


def test_extract_output_text_prefers_output_text(orchestrator_main) -> None:
    resp = SimpleNamespace(output_text="hello world", output=None)
    assert orchestrator_main._extract_output_text(resp) == "hello world"

    resp2 = SimpleNamespace(
        output_text=None,
        output=[SimpleNamespace(content=[SimpleNamespace(text="from parts")])],
    )
    assert orchestrator_main._extract_output_text(resp2) == "from parts"
