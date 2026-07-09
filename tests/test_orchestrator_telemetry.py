"""Telemetry wiring for the hosted orchestrator (``src/orchestrator/main.py``).

The orchestrator exports OpenTelemetry traces to Application Insights so agent
runs and the two sub-agent handoffs appear in the Foundry Tracing tab. These
tests exercise the setup offline: it must configure Azure Monitor + Agent
Framework instrumentation when a connection string is present, no-op safely when
it is absent (local/mock), and wrap each sub-agent invocation in a span.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_MAIN_PATH = Path(__file__).resolve().parents[1] / "src" / "orchestrator" / "main.py"


@pytest.fixture()
def orchestrator_main():
    spec = importlib.util.spec_from_file_location("orchestrator_main_telemetry", _MAIN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_content_recording_respects_env(orchestrator_main, monkeypatch) -> None:
    monkeypatch.delenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", raising=False)
    assert orchestrator_main._content_recording_enabled() is False
    monkeypatch.setenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", "true")
    assert orchestrator_main._content_recording_enabled() is True
    monkeypatch.setenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", "no")
    assert orchestrator_main._content_recording_enabled() is False


def test_configure_telemetry_noops_without_connection(orchestrator_main, monkeypatch) -> None:
    """No env var and no project endpoint -> no-op, never raises, returns False."""
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.setattr(orchestrator_main, "PROJECT_ENDPOINT", "")
    monkeypatch.setattr(orchestrator_main, "_telemetry_configured", False)

    assert orchestrator_main.configure_telemetry() is False
    assert orchestrator_main._telemetry_configured is False


def test_configure_telemetry_wires_azure_monitor_and_instrumentation(
    orchestrator_main, monkeypatch
) -> None:
    conn = "InstrumentationKey=abc;IngestionEndpoint=https://x.in.applicationinsights.azure.com/"
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", conn)
    monkeypatch.setenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", "true")
    monkeypatch.setattr(orchestrator_main, "_telemetry_configured", False)

    calls: dict[str, object] = {}

    fake_monitor = types.ModuleType("azure.monitor.opentelemetry")
    fake_monitor.configure_azure_monitor = lambda **kw: calls.__setitem__("conn", kw.get("connection_string"))
    monkeypatch.setitem(sys.modules, "azure.monitor.opentelemetry", fake_monitor)

    import agent_framework.observability as obs

    monkeypatch.setattr(obs, "enable_instrumentation", lambda: calls.__setitem__("instr", True))
    monkeypatch.setattr(obs, "enable_sensitive_telemetry", lambda: calls.__setitem__("sensitive", True))

    assert orchestrator_main.configure_telemetry() is True
    assert orchestrator_main._telemetry_configured is True
    assert calls["conn"] == conn
    assert calls["instr"] is True
    # Content recording was enabled, so sensitive capture must be opted in.
    assert calls["sensitive"] is True
    # Cloud role name honored for App Insights cloud_RoleName.
    import os

    assert os.environ["OTEL_SERVICE_NAME"] == "it-helpdesk-orchestrator"


def test_configure_telemetry_skips_sensitive_when_recording_disabled(
    orchestrator_main, monkeypatch
) -> None:
    conn = "InstrumentationKey=abc;IngestionEndpoint=https://x.in.applicationinsights.azure.com/"
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", conn)
    monkeypatch.delenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", raising=False)
    monkeypatch.setattr(orchestrator_main, "_telemetry_configured", False)

    calls: dict[str, object] = {}
    fake_monitor = types.ModuleType("azure.monitor.opentelemetry")
    fake_monitor.configure_azure_monitor = lambda **kw: None
    monkeypatch.setitem(sys.modules, "azure.monitor.opentelemetry", fake_monitor)

    import agent_framework.observability as obs

    monkeypatch.setattr(obs, "enable_instrumentation", lambda: calls.__setitem__("instr", True))
    monkeypatch.setattr(
        obs, "enable_sensitive_telemetry", lambda: calls.__setitem__("sensitive", True)
    )

    assert orchestrator_main.configure_telemetry() is True
    assert calls.get("instr") is True
    assert "sensitive" not in calls


def test_invoke_prompt_agent_wraps_call_in_span(orchestrator_main, monkeypatch) -> None:
    """Each sub-agent handoff is wrapped in a span carrying the target agent name."""

    class _FakeSpan:
        def __init__(self) -> None:
            self.attrs: dict[str, object] = {}

        def set_attribute(self, k, v):
            self.attrs[k] = v

        def record_exception(self, exc):  # pragma: no cover - unused here
            self.attrs["exception"] = exc

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    span = _FakeSpan()

    class _FakeTracer:
        def start_as_current_span(self, name):
            span.attrs["__name__"] = name
            return span

    monkeypatch.setattr(orchestrator_main, "_get_tracer", lambda: _FakeTracer())
    monkeypatch.setattr(
        orchestrator_main, "_call_prompt_agent", lambda agent, msg: f"answer:{agent}"
    )

    out = orchestrator_main._invoke_prompt_agent("it-helpdesk-triage", "laptop slow")

    assert out == "answer:it-helpdesk-triage"
    assert span.attrs["gen_ai.agent.name"] == "it-helpdesk-triage"
    assert span.attrs["gen_ai.operation.name"] == "invoke_agent"
    assert span.attrs["gen_ai.tool.name"] == "troubleshoot_from_knowledge_base"
    assert "it-helpdesk-triage" in span.attrs["__name__"]


def test_invoke_prompt_agent_without_tracer(orchestrator_main, monkeypatch) -> None:
    """No tracer available -> plain call, still returns the sub-agent's text."""
    monkeypatch.setattr(orchestrator_main, "_get_tracer", lambda: None)
    monkeypatch.setattr(
        orchestrator_main, "_call_prompt_agent", lambda agent, msg: f"plain:{agent}"
    )
    assert (
        orchestrator_main._invoke_prompt_agent("it-helpdesk-incident", "status")
        == "plain:it-helpdesk-incident"
    )
