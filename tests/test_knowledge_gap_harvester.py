"""Knowledge-gap harvester coverage for offline and hosted orchestrators."""

from __future__ import annotations

import builtins
import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

from helpdesk.observability.knowledge_gaps import (
    REASON_INCIDENT_CREATED,
    REASON_KB_INSUFFICIENT,
    REASON_TRIAGE_UNRESOLVED,
    SPAN_NAME,
    harvest_enabled,
    record_knowledge_gap,
)
from helpdesk.orchestrator import Orchestrator, TICKET_OFFER_MARKER

_HOSTED_MAIN_PATH = Path(__file__).resolve().parents[1] / "src" / "orchestrator" / "main.py"


class FakeSpan:
    def __init__(self, name: str) -> None:
        self.name = name
        self.attributes: dict[str, object] = {}

    def __enter__(self) -> FakeSpan:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        return None

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value


class FakeTracer:
    def __init__(self) -> None:
        self.spans: list[FakeSpan] = []

    def start_as_current_span(self, name: str) -> FakeSpan:
        span = FakeSpan(name)
        self.spans.append(span)
        return span


@pytest.fixture()
def orch(monkeypatch: pytest.MonkeyPatch) -> Orchestrator:
    monkeypatch.delenv("KB_GAP_HARVEST_ENABLED", raising=False)
    monkeypatch.delenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", raising=False)
    return Orchestrator()


@pytest.fixture(scope="module")
def hosted_orchestrator_main():
    spec = importlib.util.spec_from_file_location("hosted_orchestrator_main", _HOSTED_MAIN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["hosted_orchestrator_main"] = module
    spec.loader.exec_module(module)
    return module


def test_harvest_enabled_defaults_on_and_falsy_values_disable() -> None:
    assert harvest_enabled({}) is True
    assert harvest_enabled({"KB_GAP_HARVEST_ENABLED": ""}) is True
    assert harvest_enabled({"KB_GAP_HARVEST_ENABLED": "1"}) is True

    for value in ("0", "false", "no"):
        assert harvest_enabled({"KB_GAP_HARVEST_ENABLED": value}) is False


def test_record_enabled_by_default_emits_knowledge_gap_span() -> None:
    tracer = FakeTracer()

    gap = record_knowledge_gap(
        "The printer is speaking Klingon",
        REASON_TRIAGE_UNRESOLVED,
        environ={},
        tracer=tracer,
    )

    assert gap is not None
    assert gap.recorded is True
    assert [span.name for span in tracer.spans] == [SPAN_NAME]


def test_record_disabled_returns_none_and_does_not_start_span() -> None:
    tracer = FakeTracer()

    gap = record_knowledge_gap(
        "The printer is speaking Klingon",
        REASON_TRIAGE_UNRESOLVED,
        environ={"KB_GAP_HARVEST_ENABLED": "0"},
        tracer=tracer,
    )

    assert gap is None
    assert tracer.spans == []


def test_content_recording_off_uses_privacy_safe_question_metadata_only() -> None:
    tracer = FakeTracer()

    gap = record_knowledge_gap(
        "Why is my badge reader blinking purple?",
        REASON_TRIAGE_UNRESOLVED,
        environ={},
        tracer=tracer,
    )

    assert gap is not None
    attrs = tracer.spans[0].attributes
    assert attrs["helpdesk.kb_gap.question_length"] == len(
        "Why is my badge reader blinking purple?"
    )
    assert attrs["helpdesk.kb_gap.question_hash"]
    assert "helpdesk.kb_gap.question" not in attrs
    assert gap.question is None


def test_content_recording_on_attaches_raw_question_to_span_and_dataclass() -> None:
    tracer = FakeTracer()
    question = "Why is my badge reader blinking purple?"

    gap = record_knowledge_gap(
        question,
        REASON_TRIAGE_UNRESOLVED,
        environ={"AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED": "true"},
        tracer=tracer,
    )

    assert gap is not None
    assert tracer.spans[0].attributes["helpdesk.kb_gap.question"] == question
    assert gap.question == question


def test_reason_tool_and_had_citations_are_propagated() -> None:
    tracer = FakeTracer()

    gap = record_knowledge_gap(
        "Create a ticket for the portal outage",
        REASON_INCIDENT_CREATED,
        had_citations=True,
        tool="manage_servicenow_incident",
        environ={},
        tracer=tracer,
    )

    assert gap is not None
    assert gap.reason == REASON_INCIDENT_CREATED
    assert gap.tool == "manage_servicenow_incident"
    assert gap.had_citations is True
    attrs = tracer.spans[0].attributes
    assert attrs["helpdesk.kb_gap.reason"] == REASON_INCIDENT_CREATED
    assert attrs["helpdesk.kb_gap.tool"] == "manage_servicenow_incident"
    assert attrs["helpdesk.kb_gap.had_citations"] is True


def test_question_hash_and_length_are_based_on_stripped_question() -> None:
    question = "  Why is Teams showing error 80090016?  "
    stripped = question.strip()

    gap = record_knowledge_gap(
        question,
        REASON_TRIAGE_UNRESOLVED,
        environ={},
        tracer=FakeTracer(),
    )

    assert gap is not None
    assert gap.question_length == len(stripped)
    assert gap.question_hash == hashlib.sha256(stripped.encode("utf-8")).hexdigest()[:16]


def test_tracer_none_without_ambient_tracer_returns_unrecorded_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def block_opentelemetry_import(
        name: str,
        globals_: dict | None = None,
        locals_: dict | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ):
        if name == "opentelemetry" or name.startswith("opentelemetry."):
            raise ImportError("no ambient OpenTelemetry in this test")
        return real_import(name, globals_, locals_, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", block_opentelemetry_import)

    gap = record_knowledge_gap(
        "No KB article covers this scanner error",
        REASON_TRIAGE_UNRESOLVED,
        environ={},
        tracer=None,
    )

    assert gap is not None
    assert gap.recorded is False


@pytest.mark.parametrize("question", ["", "   \n\t  "])
def test_empty_or_whitespace_question_returns_none(question: str) -> None:
    tracer = FakeTracer()

    gap = record_knowledge_gap(
        question,
        REASON_TRIAGE_UNRESOLVED,
        environ={},
        tracer=tracer,
    )

    assert gap is None
    assert tracer.spans == []


def test_kb_resolved_orchestrator_answer_has_no_knowledge_gap(orch: Orchestrator) -> None:
    resp = orch.run("How do I reset my forgotten password?")

    assert resp.triage is not None and resp.triage.resolved is True
    assert resp.knowledge_gap is None


def test_explicit_create_without_kb_match_records_incident_created_gap(
    orch: Orchestrator,
) -> None:
    resp = orch.run("Please file a ticket for qzxv jklm nprst.")

    assert resp.incident is not None and resp.incident.action == "create"
    assert resp.knowledge_gap is not None
    assert resp.knowledge_gap.reason == REASON_INCIDENT_CREATED


def test_unresolved_triage_without_escalation_records_triage_unresolved_gap(
    orch: Orchestrator,
) -> None:
    resp = orch.run("The quarterly TPS report is purple")

    assert resp.route == ["triage"]
    assert resp.incident is None
    assert resp.knowledge_gap is not None
    assert resp.knowledge_gap.reason == REASON_TRIAGE_UNRESOLVED


def test_confirmation_after_kb_offer_records_kb_insufficient_gap(
    orch: Orchestrator,
) -> None:
    original = "my laptop is running slow. please file a ticket."
    offer = orch.run(original)

    resp = orch.run(
        "go ahead",
        history=[
            {"role": "user", "content": original},
            {"role": "assistant", "content": offer.reply},
        ],
    )

    assert TICKET_OFFER_MARKER in offer.reply
    assert resp.incident is not None and resp.incident.action == "create"
    assert resp.knowledge_gap is not None
    assert resp.knowledge_gap.reason == REASON_KB_INSUFFICIENT


def test_hosted_inline_recorder_emits_same_span_name_and_attributes(
    hosted_orchestrator_main,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = "Create a ticket for the portal outage"
    canonical_tracer = FakeTracer()
    hosted_tracer = FakeTracer()

    canonical_gap = record_knowledge_gap(
        question,
        REASON_INCIDENT_CREATED,
        had_citations=True,
        tool="manage_servicenow_incident",
        environ={"AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED": "1"},
        tracer=canonical_tracer,
    )
    assert canonical_gap is not None

    monkeypatch.setattr(hosted_orchestrator_main, "KB_GAP_HARVEST_ENABLED", "")
    monkeypatch.setattr(hosted_orchestrator_main, "_get_tracer", lambda: hosted_tracer)
    monkeypatch.setattr(hosted_orchestrator_main, "_content_recording_enabled", lambda: True)

    hosted_orchestrator_main._record_kb_gap(
        question,
        REASON_INCIDENT_CREATED,
        had_citations=True,
        tool="manage_servicenow_incident",
    )

    assert hosted_tracer.spans[0].name == canonical_tracer.spans[0].name == SPAN_NAME
    assert hosted_tracer.spans[0].attributes == canonical_tracer.spans[0].attributes
