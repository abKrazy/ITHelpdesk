"""MAF Foundry **Hosted Agent** — the IT Helpdesk Orchestrator.

This is the single brain the UI talks to. It is a Microsoft Agent Framework
(``agent-framework``) agent, packaged as a container and deployed as a **Foundry
Hosted Agent** (Preview). At runtime Foundry serves it over the OpenAI Responses
protocol via :class:`ResponsesHostServer` (``POST /responses`` on port 8088).

The orchestrator's LLM decides — turn by turn, with full conversation memory —
which of its two tools to call. Each tool invokes one of the two **Foundry Prompt
Agents** (created in Phase 1 by ``scripts/postprovision.py``) by *agent reference*
through the project's OpenAI Responses endpoint:

  * ``troubleshoot_from_knowledge_base`` -> ``it-helpdesk-triage``  (AI Search RAG)
  * ``manage_servicenow_incident``       -> ``it-helpdesk-incident`` (APIM MCP tool)

Deployment contract (see ``scripts/postprovision.py`` -> ``create_hosted_orchestrator``):
  * The container is built server-side with ``az acr build`` and registered via
    ``AIProjectClient.agents.create_version(... HostedAgentDefinition(container_configuration=...))``.
  * Foundry injects ``FOUNDRY_PROJECT_ENDPOINT`` and ``AZURE_AI_MODEL_DEPLOYMENT_NAME``
    at run time; we also pass them explicitly as env vars for robustness.
"""

from __future__ import annotations

import logging
import os
from typing import Annotated

from agent_framework import Agent, tool
from agent_framework.foundry import FoundryChatClient

_LOGGER = logging.getLogger("orchestrator")

# --- Environment (auto-injected by Foundry at run time; explicit fallbacks) ----
PROJECT_ENDPOINT = (
    os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    or os.environ.get("AZURE_AI_PROJECT_ENDPOINT")
    or ""
)
MODEL = (
    os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME")
    or os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT")
    or "gpt-4o"
)
TRIAGE_AGENT_NAME = os.environ.get("TRIAGE_AGENT_NAME", "it-helpdesk-triage")
INCIDENT_AGENT_NAME = os.environ.get("INCIDENT_AGENT_NAME", "it-helpdesk-incident")
PORT = int(os.environ.get("PORT", "8088"))

ORCHESTRATOR_INSTRUCTIONS = """\
You are the IT Helpdesk Orchestrator. You coordinate two specialist sub-agents to
help an end user, and you carry the whole conversation so you always know the
context of earlier turns (including any incident number already created).

RELAY VERBATIM (most important rule). The user CANNOT see the outputs of your
tools or sub-agents — they only ever see YOUR reply. Whatever a tool returns is
invisible to them until you paste it. Therefore, when a tool returns content you
want to give the user, you MUST copy that content — every numbered
troubleshooting step and any 【…†source】 citations — VERBATIM into your reply.
NEVER say "I've shared/provided the steps", "see above", "here are some steps",
or otherwise refer to steps without actually including their full text. If you
have steps, PASTE them in full, THEN ask whether they resolved the issue and
offer a ticket. Summarizing instead of pasting is a failure.

You have exactly two tools:
1. troubleshoot_from_knowledge_base — searches the IT knowledge base (RAG) for
   self-service troubleshooting steps.
2. manage_servicenow_incident — creates, looks up, or updates ServiceNow
   incidents (tickets).

Follow these rules strictly:

DEFLECT FIRST. For ANY technical problem or "how do I…" question — even when the
user immediately asks to "create/open/file/log a ticket" — you MUST call
troubleshoot_from_knowledge_base FIRST and present its steps. Do NOT create a
ticket on the first turn of a new problem. Copy the troubleshoot_from_knowledge_base
tool's FULL answer — every numbered troubleshooting step and any 【…†source】
citations — verbatim into your reply. After pasting the steps, ask whether they
resolved the issue and offer to open a ticket if not.

CREATE ONLY ON CONFIRMATION. Call manage_servicenow_incident to create a ticket
only after the user has seen the KB steps and indicates they didn't help or
explicitly confirms they want a ticket ("go ahead", "yes, file it", "that didn't
work"). When you create it, pass the original problem description and the
recommended assignment group from the triage step.

FOLLOW-UP QUESTIONS ABOUT AN EXISTING TICKET GO TO THE INCIDENT TOOL. Once a
ticket exists in this conversation, any question about it — its status, state,
priority, urgency, assignment group — or any request to change/update it MUST be
answered by calling manage_servicenow_incident (include the INC number from the
conversation). NEVER answer a question about an existing ticket from the
knowledge base.

Also route to manage_servicenow_incident whenever the user references an incident
number (e.g. "INC0010036") to check status or update fields.

Be concise and helpful. Never invent ticket numbers, statuses, or KB content.
Remember: the user sees only your reply, so relay the sub-agent's answer — the
full troubleshooting steps, KB citations, and the incident number — verbatim.
Never merely claim you have provided steps; include their full text.
"""


# --- Sub-agent invocation ------------------------------------------------------
_oai_client = None


def _get_openai_client():
    """Lazily build (and cache) an OpenAI client bound to the Foundry project.

    Built lazily so the module imports offline (tests) without Azure creds. In the
    hosted container, DefaultAzureCredential resolves the agent's managed identity.
    """
    global _oai_client
    if _oai_client is None:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential

        if not PROJECT_ENDPOINT:
            raise RuntimeError(
                "FOUNDRY_PROJECT_ENDPOINT (or AZURE_AI_PROJECT_ENDPOINT) is not set; "
                "the hosted orchestrator cannot reach its Foundry project."
            )
        project = AIProjectClient(
            endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential()
        )
        _oai_client = project.get_openai_client()
    return _oai_client


def _extract_output_text(resp) -> str:
    text = getattr(resp, "output_text", None)
    if text:
        return str(text).strip()
    parts: list[str] = []
    for item in getattr(resp, "output", None) or []:
        for content in getattr(item, "content", None) or []:
            chunk = getattr(content, "text", None)
            if chunk:
                parts.append(str(chunk))
    return "\n".join(parts).strip() or "(the sub-agent returned no content)"


def _invoke_prompt_agent(agent_name: str, message: str) -> str:
    """Invoke a Foundry Prompt Agent by *agent reference* and return its text."""
    client = _get_openai_client()
    resp = client.responses.create(
        model=MODEL,
        input=message,
        extra_body={"agent_reference": {"name": agent_name, "type": "agent_reference"}},
    )
    return _extract_output_text(resp)


# --- Tools ---------------------------------------------------------------------
def troubleshoot_from_knowledge_base(
    problem: Annotated[
        str,
        "The user's IT problem or 'how do I' question, in natural language. "
        "Send the underlying problem even if the user asked to file a ticket.",
    ],
) -> str:
    """Search the IT knowledge base (Azure AI Search RAG) for self-service
    troubleshooting steps. ALWAYS call this FIRST for any technical problem, and
    BEFORE creating any ticket, to try to resolve the issue without a ticket."""
    return _invoke_prompt_agent(TRIAGE_AGENT_NAME, problem)


def manage_servicenow_incident(
    request: Annotated[
        str,
        "The incident action in natural language. Examples: 'create an incident "
        "for: my laptop is running slow; assign to Desktop Support', 'check the "
        "status of INC0010036', 'update the urgency of INC0010036 to high'. "
        "Always include the INC number for a status check or update.",
    ],
) -> str:
    """Create, look up, or update a ServiceNow incident via the ServiceNow MCP
    tool. Use this to FILE a ticket after the user confirms the KB steps didn't
    help, and to CHECK or UPDATE any existing ticket (status, priority, urgency,
    assignment group) by its INC number."""
    return _invoke_prompt_agent(INCIDENT_AGENT_NAME, request)


TOOLS = [
    tool(troubleshoot_from_knowledge_base, approval_mode="never_require"),
    tool(manage_servicenow_incident, approval_mode="never_require"),
]


def build_agent() -> Agent:
    """Construct the MAF orchestrator agent (LLM brain + two sub-agent tools)."""
    from azure.identity import DefaultAzureCredential

    chat_client = FoundryChatClient(
        project_endpoint=PROJECT_ENDPOINT,
        model=MODEL,
        credential=DefaultAzureCredential(),
    )
    return Agent(
        chat_client,
        ORCHESTRATOR_INSTRUCTIONS,
        name="it-helpdesk-orchestrator",
        description="Coordinates KB triage and ServiceNow incident sub-agents.",
        tools=TOOLS,
        # The hosting infrastructure persists conversation history; store=False
        # avoids duplicating it (per the Foundry hosted-agent Responses guidance).
        default_options={"store": False},
    )


def main() -> None:
    from agent_framework_foundry_hosting import ResponsesHostServer

    logging.basicConfig(level=logging.INFO)
    _LOGGER.info(
        "Starting IT Helpdesk Orchestrator hosted agent on port %s "
        "(project=%s, model=%s, triage=%s, incident=%s)",
        PORT,
        PROJECT_ENDPOINT or "<unset>",
        MODEL,
        TRIAGE_AGENT_NAME,
        INCIDENT_AGENT_NAME,
    )
    ResponsesHostServer(build_agent()).run(port=PORT)


if __name__ == "__main__":
    main()
