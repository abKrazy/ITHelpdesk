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


class _FakeProjectClient:
    instances: list["_FakeProjectClient"] = []

    def __init__(self, *, endpoint, credential):
        self.endpoint = endpoint
        self.credential = credential
        self.agents = _FakeAgentsOps(existing=["it-helpdesk-orchestrator"])
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
        def __init__(self, *, model, instructions):
            self.model = model
            self.instructions = instructions
            self.kind = "prompt"

    models.PromptAgentDefinition = PromptAgentDefinition


def test_create_foundry_agents_uses_new_experience(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeProjectClient.instances.clear()
    _install_fake_projects_sdk(monkeypatch)
    monkeypatch.setattr(shared, "get_credential", lambda: SimpleNamespace(), raising=False)

    persisted: dict[str, str] = {}
    monkeypatch.setattr(setup, "_azd_env_set", lambda name, value: persisted.__setitem__(name, value))

    ids = setup.create_foundry_agents(
        project_endpoint="https://x.services.ai.azure.com/api/projects/p",
        chat_deployment="gpt-4o",
    )

    # One agent per spec, keyed by name; IDs are the stable agent names (no asst_).
    assert set(ids) == {name for name, _ in setup._AGENT_SPECS}
    assert ids["it-helpdesk-triage"] == "it-helpdesk-triage"
    assert all(not v.startswith("asst_") for v in ids.values())

    client = _FakeProjectClient.instances[-1]
    assert client.closed  # context manager closed the client

    # create_version called once per spec with the right model + instructions.
    created = {c["agent_name"]: c["definition"] for c in client.agents.create_calls}
    assert set(created) == {name for name, _ in setup._AGENT_SPECS}
    for name, instructions in setup._AGENT_SPECS:
        assert created[name].model == "gpt-4o"
        assert created[name].instructions == instructions

    # IDs persisted via azd env under the expected variable names.
    assert persisted[setup._AGENT_ID_ENV["it-helpdesk-triage"]] == "it-helpdesk-triage"
    assert set(persisted) == set(setup._AGENT_ID_ENV.values())
