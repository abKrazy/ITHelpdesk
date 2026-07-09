"""Offline tests for the native Foundry incident Prompt Agent definition."""

from __future__ import annotations

import sys
import types

import pytest

from helpdesk.agents.definitions import incident_agent


def _install_fake_projects_models(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ["azure", "azure.ai", "azure.ai.projects", "azure.ai.projects.models"]:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))

    models = sys.modules["azure.ai.projects.models"]

    class MCPTool:
        def __init__(self, *, server_label, server_url, require_approval, headers):
            self.server_label = server_label
            self.server_url = server_url
            self.require_approval = require_approval
            self.headers = headers

    class PromptAgentDefinition:
        def __init__(self, *, model, instructions, tools):
            self.model = model
            self.instructions = instructions
            self.tools = tools

    models.MCPTool = MCPTool
    models.PromptAgentDefinition = PromptAgentDefinition


def test_build_incident_definition_attaches_mcp_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_projects_models(monkeypatch)

    definition = incident_agent.build_incident_definition(
        chat_deployment="gpt-4o",
        apim_mcp_url="https://apim.azure-api.net/servicenow/mcp",
        apim_key="fake-apim-key",
    )

    assert definition.model == "gpt-4o"
    assert definition.instructions == incident_agent.INCIDENT_INSTRUCTIONS
    assert len(definition.tools) == 1
    tool = definition.tools[0]
    assert tool.server_label == "servicenow-apim"
    assert tool.server_url == "https://apim.azure-api.net/servicenow/mcp"
    assert tool.require_approval == "never"
    assert tool.headers == {"Ocp-Apim-Subscription-Key": "fake-apim-key"}
