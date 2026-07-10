"""Offline tests for the native Foundry triage Prompt Agent definition.

Mirrors ``test_incident_agent_definition``: the triage agent grounds on the
Foundry IQ knowledge base via an ``MCPTool`` (``knowledge_base_retrieve``) whose
auth flows through a RemoteTool project connection referenced by NAME.
"""

from __future__ import annotations

import sys
import types

import pytest

from helpdesk.agents.definitions import triage_agent


def _install_fake_projects_models(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ["azure", "azure.ai", "azure.ai.projects", "azure.ai.projects.models"]:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))

    models = sys.modules["azure.ai.projects.models"]

    class MCPTool:
        def __init__(
            self,
            *,
            server_label,
            server_url,
            require_approval,
            allowed_tools=None,
            project_connection_id,
        ):
            self.server_label = server_label
            self.server_url = server_url
            self.require_approval = require_approval
            self.allowed_tools = allowed_tools
            self.project_connection_id = project_connection_id

    class PromptAgentDefinition:
        def __init__(self, *, model, instructions, tools, reasoning=None):
            self.model = model
            self.instructions = instructions
            self.tools = tools
            self.reasoning = reasoning

    class Reasoning:
        def __init__(self, *, effort):
            self.effort = effort

    models.MCPTool = MCPTool
    models.PromptAgentDefinition = PromptAgentDefinition
    models.Reasoning = Reasoning


def test_kb_mcp_url_shape() -> None:
    url = triage_agent.kb_mcp_url("https://srch.search.windows.net/")
    assert url == (
        "https://srch.search.windows.net/knowledgebases/it-helpdesk-kb/mcp"
        "?api-version=2026-05-01-preview"
    )


def test_triage_instructions_deflect_first_and_cite() -> None:
    assert "Deflect-first" in triage_agent.TRIAGE_INSTRUCTIONS
    assert "no ticket is being created yet" in triage_agent.TRIAGE_INSTRUCTIONS
    assert "knowledge_base_retrieve" in triage_agent.TRIAGE_INSTRUCTIONS
    assert "cite" in triage_agent.TRIAGE_INSTRUCTIONS.lower()


def test_build_triage_definition_attaches_kb_mcp_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_projects_models(monkeypatch)

    definition = triage_agent.build_triage_definition(
        chat_deployment="gpt-5.4",
        kb_mcp_url=(
            "https://srch.search.windows.net/knowledgebases/it-helpdesk-kb/mcp"
            "?api-version=2026-05-01-preview"
        ),
        kb_connection_name="it-helpdesk-kb-mcp",
    )

    assert definition.model == "gpt-5.4"
    assert definition.instructions == triage_agent.TRIAGE_INSTRUCTIONS
    assert len(definition.tools) == 1
    tool = definition.tools[0]
    assert tool.server_label == "knowledge-base"
    assert tool.server_url.endswith("/knowledgebases/it-helpdesk-kb/mcp?api-version=2026-05-01-preview")
    assert tool.require_approval == "never"
    # Grounds ONLY via the knowledge base retrieve MCP tool, by connection NAME.
    assert tool.allowed_tools == ["knowledge_base_retrieve"]
    assert tool.project_connection_id == "it-helpdesk-kb-mcp"
    # No inline Azure AI Search / managed-Index grounding remains.
    assert not hasattr(tool, "azure_ai_search")
    assert not hasattr(tool, "index_asset_id")


@pytest.mark.parametrize("missing", ["chat_deployment", "kb_mcp_url", "kb_connection_name"])
def test_build_triage_definition_requires_all_inputs(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    _install_fake_projects_models(monkeypatch)
    kwargs = {
        "chat_deployment": "gpt-5.4",
        "kb_mcp_url": "https://srch/knowledgebases/it-helpdesk-kb/mcp",
        "kb_connection_name": "it-helpdesk-kb-mcp",
    }
    kwargs[missing] = ""
    with pytest.raises(ValueError):
        triage_agent.build_triage_definition(**kwargs)
