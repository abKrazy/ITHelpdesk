"""``create_hosted_orchestrator`` must register the MAF orchestrator as a Foundry
**Hosted Agent** via the public ``AIProjectClient.agents.create_version`` API with
a container-based ``HostedAgentDefinition``.

These tests fake the ``azure.ai.projects`` SDK so they run offline, and assert the
Hosted Agent call shape: container image, ``responses`` ingress protocol, the
sub-agent env vars, and that the agent id is persisted via ``azd env set``.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

import helpdesk.shared as shared
from helpdesk.agents import setup


class _FakeAgentsOps:
    def __init__(self) -> None:
        self.create_calls: list[dict] = []

    def create_version(self, *, agent_name, definition):
        self.create_calls.append({"agent_name": agent_name, "definition": definition})
        return SimpleNamespace(id=f"{agent_name}:1", name=agent_name, version=1)


class _FakeProjectClient:
    instances: list["_FakeProjectClient"] = []

    def __init__(self, *, endpoint, credential):
        self.endpoint = endpoint
        self.credential = credential
        self.agents = _FakeAgentsOps()
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

    sys.modules["azure.ai.projects"].AIProjectClient = _FakeProjectClient
    models = sys.modules["azure.ai.projects.models"]

    class HostedAgentDefinition:
        def __init__(
            self,
            *,
            cpu,
            memory,
            environment_variables,
            container_configuration,
            protocol_versions,
        ):
            self.cpu = cpu
            self.memory = memory
            self.environment_variables = environment_variables
            self.container_configuration = container_configuration
            self.protocol_versions = protocol_versions
            self.kind = "hosted"

    class ContainerConfiguration:
        def __init__(self, *, image):
            self.image = image

    class ProtocolVersionRecord:
        def __init__(self, *, protocol, version):
            self.protocol = protocol
            self.version = version

    models.HostedAgentDefinition = HostedAgentDefinition
    models.ContainerConfiguration = ContainerConfiguration
    models.ProtocolVersionRecord = ProtocolVersionRecord


def test_create_hosted_orchestrator_registers_container_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeProjectClient.instances.clear()
    _install_fake_projects_sdk(monkeypatch)
    monkeypatch.setattr(shared, "get_credential", lambda: SimpleNamespace(), raising=False)
    monkeypatch.delenv("FOUNDRY_RESPONSES_PROTOCOL_VERSION", raising=False)

    persisted: dict[str, str] = {}
    monkeypatch.setattr(
        setup, "_azd_env_set", lambda name, value: persisted.__setitem__(name, value)
    )

    image = "acr123.azurecr.io/it-helpdesk-orchestrator:tok"
    agent_id = setup.create_hosted_orchestrator(
        project_endpoint="https://x.services.ai.azure.com/api/projects/p",
        chat_deployment="gpt-4o",
        image=image,
    )

    assert agent_id == setup._ORCHESTRATOR_NAME == "it-helpdesk-orchestrator"

    client = _FakeProjectClient.instances[-1]
    assert client.closed  # context manager closed the client
    assert len(client.agents.create_calls) == 1
    call = client.agents.create_calls[0]
    assert call["agent_name"] == "it-helpdesk-orchestrator"

    definition = call["definition"]
    assert definition.kind == "hosted"
    assert definition.container_configuration.image == image
    assert definition.cpu and definition.memory
    # Ingress protocol must be the OpenAI Responses protocol.
    assert definition.protocol_versions[0].protocol == "responses"
    assert definition.protocol_versions[0].version  # a concrete version pin
    # Sub-agent names are passed to the container.
    env = definition.environment_variables
    assert env["TRIAGE_AGENT_NAME"] == "it-helpdesk-triage"
    assert env["INCIDENT_AGENT_NAME"] == "it-helpdesk-incident"
    assert env["AZURE_AI_MODEL_DEPLOYMENT_NAME"] == "gpt-4o"
    # FOUNDRY_* and AGENT_* are reserved for platform use and auto-injected by
    # Foundry Hosted Agents — passing them in create_version fails with
    # "reserved for platform use", so they MUST NOT be in environment_variables.
    assert "FOUNDRY_PROJECT_ENDPOINT" not in env
    assert not any(k.startswith(("FOUNDRY_", "AGENT_")) for k in env)

    assert persisted["AZURE_AI_ORCHESTRATOR_AGENT_ID"] == "it-helpdesk-orchestrator"


def test_create_hosted_orchestrator_honours_protocol_version_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeProjectClient.instances.clear()
    _install_fake_projects_sdk(monkeypatch)
    monkeypatch.setattr(shared, "get_credential", lambda: SimpleNamespace(), raising=False)
    monkeypatch.setattr(setup, "_azd_env_set", lambda name, value: None)
    monkeypatch.setenv("FOUNDRY_RESPONSES_PROTOCOL_VERSION", "9.9.9")

    setup.create_hosted_orchestrator(
        project_endpoint="https://x/api/projects/p",
        chat_deployment="gpt-4o",
        image="acr/it-helpdesk-orchestrator:latest",
    )

    call = _FakeProjectClient.instances[-1].agents.create_calls[0]
    assert call["definition"].protocol_versions[0].version == "9.9.9"
