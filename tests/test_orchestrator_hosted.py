"""The MAF Foundry Hosted Agent entrypoint (``src/orchestrator/main.py``).

Loaded by file path (it ships as a standalone container app, not part of the
``helpdesk`` package) and exercised offline: the module must import, expose two
tools that route to the correct Foundry Prompt Agents by name, and carry the
deflect-first / follow-up routing rules in its instructions.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MAIN_PATH = Path(__file__).resolve().parents[1] / "src" / "orchestrator" / "main.py"


@pytest.fixture(scope="module")
def orchestrator_main():
    spec = importlib.util.spec_from_file_location("orchestrator_main", _MAIN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_module_imports_and_exposes_two_tools(orchestrator_main) -> None:
    tools = orchestrator_main.TOOLS
    assert len(tools) == 2
    names = {getattr(t, "name", None) for t in tools}
    assert names == {"troubleshoot_from_knowledge_base", "manage_servicenow_incident"}


def test_default_sub_agent_names(orchestrator_main) -> None:
    assert orchestrator_main.TRIAGE_AGENT_NAME == "it-helpdesk-triage"
    assert orchestrator_main.INCIDENT_AGENT_NAME == "it-helpdesk-incident"
    assert orchestrator_main.PORT == 8088


def test_tools_route_to_correct_prompt_agents(orchestrator_main, monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        orchestrator_main,
        "_invoke_prompt_agent",
        lambda agent, message: calls.append((agent, message)) or f"ok:{agent}",
    )

    assert (
        orchestrator_main.troubleshoot_from_knowledge_base("laptop slow")
        == "ok:it-helpdesk-triage"
    )
    assert (
        orchestrator_main.manage_servicenow_incident("status of INC0010036")
        == "ok:it-helpdesk-incident"
    )
    assert calls == [
        ("it-helpdesk-triage", "laptop slow"),
        ("it-helpdesk-incident", "status of INC0010036"),
    ]


def test_instructions_encode_routing_rules(orchestrator_main) -> None:
    instructions = orchestrator_main.ORCHESTRATOR_INSTRUCTIONS
    # Deflect-first: KB before any ticket.
    assert "DEFLECT FIRST" in instructions
    assert "troubleshoot_from_knowledge_base FIRST" in instructions
    # Follow-up questions about an existing ticket go to the incident tool, NOT KB.
    assert "manage_servicenow_incident" in instructions
    assert "NEVER answer a question about an existing ticket from the" in instructions


def test_extract_output_text_prefers_output_text(orchestrator_main) -> None:
    from types import SimpleNamespace

    resp = SimpleNamespace(output_text="hello world", output=None)
    assert orchestrator_main._extract_output_text(resp) == "hello world"

    # Falls back to walking output[].content[].text
    resp2 = SimpleNamespace(
        output_text=None,
        output=[SimpleNamespace(content=[SimpleNamespace(text="from parts")])],
    )
    assert orchestrator_main._extract_output_text(resp2) == "from parts"
