"""``create_foundry_agents`` must use the NEW Foundry Agent experience.

The new experience creates versioned *Prompt Agents* through
``AIProjectClient.agents.create_version`` (data-plane v1), NOT the legacy
``azure.ai.agents.AgentsClient`` assistants API (``asst_`` IDs). These tests fake
the ``azure.ai.projects`` SDK so they run offline, and assert the new call shape.

Triage grounds on the Foundry IQ knowledge base (Azure AI Search agentic
retrieval) via an MCP tool — the same RemoteTool project-connection pattern the
incident agent uses — NOT an inline Azure AI Search tool or a managed Index. The
data-plane knowledge source + knowledge base are ensured before agent creation.
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


class _FakeProjectClient:
    instances: list["_FakeProjectClient"] = []

    def __init__(self, *, endpoint, credential):
        self.endpoint = endpoint
        self.credential = credential
        self.agents = _FakeAgentsOps(existing=["it-helpdesk-triage"])
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

    models.PromptAgentDefinition = PromptAgentDefinition
    models.MCPTool = MCPTool


def _install_fake_triage_definition(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Fake definitions.triage_agent so setup runs without azure-search-documents."""
    module = types.ModuleType("helpdesk.agents.definitions.triage_agent")
    calls: dict = {"ensure": [], "kb_mcp_url": []}

    def ensure_kb_knowledge_base(*, search_endpoint, index_name):
        calls["ensure"].append({"search_endpoint": search_endpoint, "index_name": index_name})
        return "it-helpdesk-kb"

    def kb_mcp_url(
        search_endpoint,
        *,
        knowledge_base_name="it-helpdesk-kb",
        api_version="2026-05-01-preview",
    ):
        calls["kb_mcp_url"].append(
            {"search_endpoint": search_endpoint, "knowledge_base_name": knowledge_base_name}
        )
        return (
            f"{search_endpoint}/knowledgebases/{knowledge_base_name}/mcp"
            f"?api-version={api_version}"
        )

    def build_triage_definition(*, chat_deployment, kb_mcp_url, kb_connection_name):
        from azure.ai.projects.models import MCPTool, PromptAgentDefinition

        return PromptAgentDefinition(
            model=chat_deployment,
            instructions="triage instructions",
            tools=[
                MCPTool(
                    server_label="knowledge-base",
                    server_url=kb_mcp_url,
                    require_approval="never",
                    allowed_tools=["knowledge_base_retrieve"],
                    project_connection_id=kb_connection_name,
                )
            ],
        )

    module.ensure_kb_knowledge_base = ensure_kb_knowledge_base
    module.kb_mcp_url = kb_mcp_url
    module.build_triage_definition = build_triage_definition
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return calls


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
    kb_calls = _install_fake_triage_definition(monkeypatch)
    _install_fake_incident_definition(monkeypatch)
    monkeypatch.setattr(shared, "get_credential", lambda: SimpleNamespace(), raising=False)

    persisted: dict[str, str] = {}
    monkeypatch.setattr(setup, "_azd_env_set", lambda name, value: persisted.__setitem__(name, value))

    ids = setup.create_foundry_agents(
        project_endpoint="https://x.services.ai.azure.com/api/projects/p",
        chat_deployment="gpt-5.4",
        search_endpoint="https://search.example.net",
        search_index_name="it-helpdesk-kb",
        apim_mcp_url="https://apim.example.net/mcp",
        mcp_connection_id="servicenow-apim-mcp",
        kb_connection_id="it-helpdesk-kb-mcp",
    )

    # One native-tool Prompt Agent per Phase 1 spec; no orchestrator Prompt Agent.
    assert set(ids) == set(setup._AGENT_NAMES)
    assert ids["it-helpdesk-triage"] == "it-helpdesk-triage"
    assert "it-helpdesk-orchestrator" not in ids
    assert all(not v.startswith("asst_") for v in ids.values())

    client = _FakeProjectClient.instances[-1]
    assert client.closed  # context manager closed the client

    # The Foundry IQ knowledge base (Search agentic-retrieval KS + KB) is ensured
    # data-plane over the KB index before the triage agent is created.
    assert kb_calls["ensure"] == [
        {"search_endpoint": "https://search.example.net", "index_name": "it-helpdesk-kb"}
    ]

    # create_version called once per spec with the right model + instructions.
    created = {c["agent_name"]: c["definition"] for c in client.agents.create_calls}
    assert set(created) == set(setup._AGENT_NAMES)
    assert created["it-helpdesk-triage"].model == "gpt-5.4"

    # Triage grounds via an MCP tool on the KB connection (by NAME), NOT an inline
    # Azure AI Search tool or managed Index.
    triage_tool = created["it-helpdesk-triage"].tools[0]
    assert triage_tool.server_label == "knowledge-base"
    assert triage_tool.allowed_tools == ["knowledge_base_retrieve"]
    assert triage_tool.project_connection_id == "it-helpdesk-kb-mcp"
    assert triage_tool.server_url.endswith(
        "/knowledgebases/it-helpdesk-kb/mcp?api-version=2026-05-01-preview"
    )
    assert not hasattr(triage_tool, "azure_ai_search")

    assert created["it-helpdesk-incident"].model == "gpt-5.4"
    assert created["it-helpdesk-incident"].instructions == "incident instructions"
    assert created["it-helpdesk-incident"].tools[0].mcp_connection_id == "servicenow-apim-mcp"

    # IDs persisted via azd env under the expected variable names.
    assert persisted[setup._AGENT_ID_ENV["it-helpdesk-triage"]] == "it-helpdesk-triage"
    assert set(persisted) == set(setup._AGENT_ID_ENV.values())
