"""``create_foundry_agents`` must use the NEW Foundry Agent experience.

The new experience creates versioned *Prompt Agents* through
``AIProjectClient.agents.create_version`` (data-plane v1), NOT the legacy
``azure.ai.agents.AgentsClient`` assistants API (``asst_`` IDs). These tests fake
the ``azure.ai.projects`` SDK so they run offline, and assert the new call shape.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

import helpdesk.shared as shared
from helpdesk.agents import setup


class _FakeAgentsOps:
    def __init__(self, existing: list[str]) -> None:
        self._existing = list(existing)
        self.create_calls: list[dict] = []

    def list(self):
        return [SimpleNamespace(name=name, id=name) for name in self._existing]

    def create_version(self, *, agent_name, definition):
        self.create_calls.append({"agent_name": agent_name, "definition": definition})
        # New-experience agents are identified by name; id == "name:version".
        return SimpleNamespace(id=f"{agent_name}:1", name=agent_name, version=1)


class _FakeIndexesOps:
    def __init__(self) -> None:
        self.create_calls: list[dict] = []

    def create_or_update(self, *, name, version, index):
        self.create_calls.append({"name": name, "version": version, "index": index})
        return SimpleNamespace(name=name, version=version)


class _FakeProjectClient:
    instances: list["_FakeProjectClient"] = []

    def __init__(self, *, endpoint, credential):
        self.endpoint = endpoint
        self.credential = credential
        self.agents = _FakeAgentsOps(existing=["it-helpdesk-triage"])
        self.indexes = _FakeIndexesOps()
        self.connections = SimpleNamespace(
            list=lambda: [
                SimpleNamespace(
                    name="search-connection",
                    type="AzureAISearch",
                    target="https://search.example.net",
                )
            ]
        )
        self.closed = False
        _FakeProjectClient.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False


def _install_fake_projects_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ["azure", "azure.ai", "azure.ai.projects", "azure.ai.projects.models"]:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))

    projects = sys.modules["azure.ai.projects"]
    projects.AIProjectClient = _FakeProjectClient

    models = sys.modules["azure.ai.projects.models"]

    class PromptAgentDefinition:
        def __init__(self, *, model, instructions, tools=None):
            self.model = model
            self.instructions = instructions
            self.tools = tools or []
            self.kind = "prompt"

    class AISearchIndexResource:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class AzureAISearchToolResource:
        def __init__(self, *, indexes):
            self.indexes = indexes

    class AzureAISearchTool:
        def __init__(self, *, azure_ai_search):
            self.azure_ai_search = azure_ai_search

    class AzureAISearchQueryType:
        VECTOR_SEMANTIC_HYBRID = "vector_semantic_hybrid"

    class AzureAISearchIndex:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class ConnectionType:
        AZURE_AI_SEARCH = "AzureAISearch"

    models.PromptAgentDefinition = PromptAgentDefinition
    models.AISearchIndexResource = AISearchIndexResource
    models.AzureAISearchToolResource = AzureAISearchToolResource
    models.AzureAISearchTool = AzureAISearchTool
    models.AzureAISearchQueryType = AzureAISearchQueryType
    models.AzureAISearchIndex = AzureAISearchIndex
    models.ConnectionType = ConnectionType


def _install_fake_incident_definition(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("helpdesk.agents.definitions.incident_agent")
    module.INCIDENT_INSTRUCTIONS = "incident instructions"

    def build_incident_definition(*, chat_deployment, apim_mcp_url, mcp_connection_id):  # noqa: ARG001
        from azure.ai.projects.models import PromptAgentDefinition

        return PromptAgentDefinition(
            model=chat_deployment,
            instructions=module.INCIDENT_INSTRUCTIONS,
            tools=[SimpleNamespace(apim_mcp_url=apim_mcp_url, mcp_connection_id=mcp_connection_id)],
        )

    module.build_incident_definition = build_incident_definition
    monkeypatch.setitem(sys.modules, module.__name__, module)


def test_create_foundry_agents_uses_new_experience(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeProjectClient.instances.clear()
    _install_fake_projects_sdk(monkeypatch)
    _install_fake_incident_definition(monkeypatch)
    monkeypatch.setattr(shared, "get_credential", lambda: SimpleNamespace(), raising=False)

    persisted: dict[str, str] = {}
    monkeypatch.setattr(setup, "_azd_env_set", lambda name, value: persisted.__setitem__(name, value))

    ids = setup.create_foundry_agents(
        project_endpoint="https://x.services.ai.azure.com/api/projects/p",
        chat_deployment="gpt-4o",
        search_endpoint="https://search.example.net",
        search_index_name="it-helpdesk-kb",
        apim_mcp_url="https://apim.example.net/mcp",
        mcp_connection_id="servicenow-apim-mcp",
    )

    # One native-tool Prompt Agent per Phase 1 spec; no orchestrator Prompt Agent.
    assert set(ids) == set(setup._AGENT_NAMES)
    assert ids["it-helpdesk-triage"] == "it-helpdesk-triage"
    assert "it-helpdesk-orchestrator" not in ids
    assert all(not v.startswith("asst_") for v in ids.values())

    client = _FakeProjectClient.instances[-1]
    assert client.closed  # context manager closed the client

    # A Foundry Knowledge base (managed Index) is registered from the Search
    # connection before the triage agent is created.
    assert len(client.indexes.create_calls) == 1
    kb = client.indexes.create_calls[0]
    assert kb["name"] == "it-helpdesk-kb"
    assert kb["version"] == "1"
    assert kb["index"].connection_name == "search-connection"
    assert kb["index"].index_name == "it-helpdesk-kb"

    # create_version called once per spec with the right model + instructions.
    created = {c["agent_name"]: c["definition"] for c in client.agents.create_calls}
    assert set(created) == set(setup._AGENT_NAMES)
    assert created["it-helpdesk-triage"].model == "gpt-4o"
    # Triage grounds on the Knowledge base via index_asset_id, NOT a raw connection.
    triage_index = created["it-helpdesk-triage"].tools[0].azure_ai_search.indexes[0]
    assert triage_index.index_asset_id == "it-helpdesk-kb/versions/1"
    assert getattr(triage_index, "project_connection_id", None) is None
    assert created["it-helpdesk-incident"].model == "gpt-4o"
    assert created["it-helpdesk-incident"].instructions == "incident instructions"
    assert created["it-helpdesk-incident"].tools[0].mcp_connection_id == "servicenow-apim-mcp"

    # IDs persisted via azd env under the expected variable names.
    assert persisted[setup._AGENT_ID_ENV["it-helpdesk-triage"]] == "it-helpdesk-triage"
    assert set(persisted) == set(setup._AGENT_ID_ENV.values())


def test_triage_definition_imports_and_builds_with_native_search_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_projects_sdk(monkeypatch)

    from helpdesk.agents.definitions.triage_agent import (
        TRIAGE_INSTRUCTIONS,
        build_triage_definition,
        ensure_search_connection,
    )

    assert "Deflect-first" in TRIAGE_INSTRUCTIONS
    assert "no ticket is being created yet" in TRIAGE_INSTRUCTIONS

    definition = build_triage_definition(
        chat_deployment="gpt-4o",
        index_asset_id="it-helpdesk-kb/versions/1",
    )

    index = definition.tools[0].azure_ai_search.indexes[0]
    assert definition.model == "gpt-4o"
    assert index.index_asset_id == "it-helpdesk-kb/versions/1"
    assert getattr(index, "project_connection_id", None) is None
    assert index.query_type == "vector_semantic_hybrid"
    assert index.top_k == 5

    project = SimpleNamespace(
        connections=SimpleNamespace(
            list=lambda: [
                SimpleNamespace(
                    name="other-search",
                    type="CognitiveSearch",
                    target="https://other.example.net",
                ),
                SimpleNamespace(
                    name="search-connection",
                    type="AzureAISearch",
                    target="https://search.example.net/",
                ),
            ]
        )
    )
    assert (
        ensure_search_connection(project, search_endpoint="https://search.example.net")
        == "search-connection"
    )
