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
    or "gpt-5.4"
)
# The triage Prompt Agent may run on its OWN (typically smaller/cheaper) chat
# deployment — e.g. gpt-5.4-mini — while the orchestrator + incident agent stay
# on the main deployment. When invoking a Prompt Agent by ``agent_reference``,
# the Foundry Responses API REQUIRES the ``model`` we pass to equal that agent's
# own deployment ("Model must match the agent's model '<x>' when agent is
# specified"). So triage must be invoked with TRIAGE_MODEL, not MODEL. Falls back
# to MODEL when triage shares the orchestrator's deployment (nothing to override).
TRIAGE_MODEL = (
    os.environ.get("TRIAGE_MODEL_DEPLOYMENT_NAME")
    or os.environ.get("AZURE_OPENAI_TRIAGE_CHAT_DEPLOYMENT")
    or MODEL
)
TRIAGE_AGENT_NAME = os.environ.get("TRIAGE_AGENT_NAME", "it-helpdesk-triage")
INCIDENT_AGENT_NAME = os.environ.get("INCIDENT_AGENT_NAME", "it-helpdesk-incident")
PORT = int(os.environ.get("PORT", "8088"))

# Cloud role name for App Insights (== OTEL service.name). Honors the injected
# OTEL_SERVICE_NAME env var; defaults to the orchestrator's own name.
SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "it-helpdesk-orchestrator")

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

CLASSIFY INTENT FIRST (do this before anything else, including DEFLECT FIRST).
Before choosing a tool, decide which of these two intents the user's message is:

  (A) NEW PROBLEM REPORT / TROUBLESHOOTING HELP — the user is reporting a new
      technical problem or symptom, or asking how to do/fix something. Examples:
      "my laptop is running slow", "I can't connect to VPN", "how do I reset my
      password", "my email won't sync". This still counts as (A) even when the
      user immediately asks to open/create a ticket for that NEW problem.
      -> Follow DEFLECT FIRST: call troubleshoot_from_knowledge_base FIRST, paste
         its steps verbatim, then offer a ticket.

  (B) TICKET STATUS / LOOKUP / UPDATE / MANAGEMENT — the user is checking or
      changing an EXISTING ticket. This includes: asking about a ticket's status,
      state, priority, urgency, assignment group, or resolution; asking "what's
      the status of INC…", "is my ticket resolved"; updating or changing ANY field
      on a ticket; or referencing an existing INC number for any read or update.
      -> Call manage_servicenow_incident ONLY. NEVER call
         troubleshoot_from_knowledge_base for these. The knowledge base cannot
         answer questions about a specific ticket — it has no ticket data, so KB
         retrieval for a status/lookup/update intent is always wrong.

  Concrete (B) examples that MUST skip triage entirely (no KB retrieval):
    - "what is the priority of INC0010045?"
    - "check the status of my ticket"
    - "change the urgency of INC0010045 to high"
    - "is INC0010045 resolved yet?"

  DEFLECT FIRST applies ONLY to intent (A). If the intent is (B), do NOT run
  knowledge-base retrieval at all — route straight to manage_servicenow_incident.

DEFLECT FIRST. For ANY technical problem or "how do I…" question (intent A) — even
when the user immediately asks to "create/open/file/log a ticket" — you MUST call
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


# --- Telemetry / OpenTelemetry -> Application Insights -------------------------
# The hosted container injects APPLICATIONINSIGHTS_CONNECTION_STRING,
# OTEL_SERVICE_NAME, and AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED (see
# helpdesk.agents.setup.create_hosted_orchestrator). We wire Azure Monitor as the
# OTel provider and turn on Microsoft Agent Framework's built-in GenAI
# instrumentation so orchestrator runs, model calls, and the two sub-agent tool
# handoffs emit spans to the same App Insights the Foundry project is connected to
# (visible in the portal Tracing tab). Telemetry is additive and best-effort — it
# must never crash the agent, so setup is guarded and no-ops when unconfigured.
_telemetry_configured = False
_tracer = None


def _content_recording_enabled() -> bool:
    """Whether GenAI message content may be recorded on spans (sensitive data)."""
    return os.environ.get(
        "AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", ""
    ).strip().lower() in ("1", "true", "yes", "on")


def _resolve_connection_string() -> str:
    """Resolve the App Insights connection string for telemetry export.

    Prefers the ``APPLICATIONINSIGHTS_CONNECTION_STRING`` env var (injected into
    the hosted container). Falls back to the Foundry project's default AppInsights
    connection via ``AIProjectClient(...).telemetry.get_connection_string()``.
    Returns "" when telemetry is not configured (local/mock/offline) so callers
    no-op instead of crashing.
    """
    conn = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "").strip()
    if conn:
        return conn
    if not PROJECT_ENDPOINT:
        return ""
    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential

        project = AIProjectClient(
            endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential()
        )
        return (project.telemetry.get_connection_string() or "").strip()
    except Exception as exc:  # pragma: no cover - live-only fallback
        _LOGGER.warning("Could not resolve App Insights connection string: %s", exc)
        return ""


def configure_telemetry() -> bool:
    """Configure OpenTelemetry export to Application Insights once, at startup.

    Wires Azure Monitor as the OTel provider and enables Microsoft Agent
    Framework's GenAI-semantic-convention instrumentation so agent/model/tool
    spans flow to App Insights and the Foundry Tracing tab. Content (message)
    capture is opt-in via ``AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED``.

    Guarded so it no-ops (never raises) when no connection string is available.
    Returns True when telemetry was configured, False otherwise.
    """
    global _telemetry_configured
    if _telemetry_configured:
        return True

    conn = _resolve_connection_string()
    if not conn:
        _LOGGER.info(
            "No App Insights connection string (env or project fallback); telemetry "
            "export disabled — traces will not flow to Application Insights."
        )
        return False

    try:
        # Cloud role name -> App Insights cloud_RoleName. Honor OTEL_SERVICE_NAME.
        os.environ.setdefault("OTEL_SERVICE_NAME", SERVICE_NAME)

        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(connection_string=conn)

        # Agent Framework instrumentation is on by default; enable it explicitly and
        # opt into sensitive (message content) capture only when requested.
        from agent_framework.observability import (
            enable_instrumentation,
            enable_sensitive_telemetry,
        )

        enable_instrumentation()
        if _content_recording_enabled():
            enable_sensitive_telemetry()

        _telemetry_configured = True
        _LOGGER.info(
            "Telemetry configured -> Application Insights (service=%s, content_recording=%s)",
            os.environ.get("OTEL_SERVICE_NAME", SERVICE_NAME),
            _content_recording_enabled(),
        )
        return True
    except Exception as exc:  # pragma: no cover - never crash startup on telemetry
        _LOGGER.warning("Telemetry configuration failed (continuing without it): %s", exc)
        return False


def _get_tracer():
    """Return a cached OpenTelemetry tracer, or None if OTel is unavailable."""
    global _tracer
    if _tracer is None:
        try:
            from opentelemetry import trace

            _tracer = trace.get_tracer("it-helpdesk-orchestrator")
        except Exception:  # pragma: no cover - otel always present in container
            _tracer = False
    return _tracer or None


# --- Sub-agent invocation ------------------------------------------------------
_oai_client = None

# Maps a sub-agent name back to the orchestrator tool that fronts it, so the
# handoff span can be attributed with the tool name a user's turn triggered.
_TOOL_BY_AGENT = {
    TRIAGE_AGENT_NAME: "troubleshoot_from_knowledge_base",
    INCIDENT_AGENT_NAME: "manage_servicenow_incident",
}

# Maps a sub-agent name to the chat deployment it is published on. An
# ``agent_reference`` Responses call MUST pass the referenced agent's own model,
# so this is the source of truth for the ``model`` param per sub-agent. Triage
# may run on a cheaper deployment (e.g. gpt-5.4-mini); incident stays on MODEL.
_MODEL_BY_AGENT = {
    TRIAGE_AGENT_NAME: TRIAGE_MODEL,
    INCIDENT_AGENT_NAME: MODEL,
}


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


def _call_prompt_agent(agent_name: str, message: str) -> str:
    """Do the raw Responses call to a Foundry Prompt Agent by *agent reference*.

    The ``model`` MUST equal the referenced agent's own deployment — the Foundry
    Responses API rejects a mismatch with 400 ``invalid_payload`` ("Model must
    match the agent's model '<x>' when agent is specified"). Because a sub-agent
    can run on a different deployment than the orchestrator (e.g. triage on
    gpt-5.4-mini), resolve the model per agent instead of always passing MODEL.
    """
    client = _get_openai_client()
    model = _MODEL_BY_AGENT.get(agent_name, MODEL)
    resp = client.responses.create(
        model=model,
        input=message,
        extra_body={"agent_reference": {"name": agent_name, "type": "agent_reference"}},
    )
    return _extract_output_text(resp)


def _invoke_prompt_agent(agent_name: str, message: str) -> str:
    """Invoke a Foundry Prompt Agent by *agent reference* and return its text.

    Wrapped in an explicit OpenTelemetry span so each sub-agent handoff (triage or
    incident) is visible in the Foundry Tracing tab with the target agent name and
    the orchestrator tool that fronts it — even where the framework's own
    instrumentation doesn't cover the raw Responses call. The span no-ops cleanly
    when OpenTelemetry / telemetry export isn't configured (local/mock runs).
    """
    tracer = _get_tracer()
    if tracer is None:
        return _call_prompt_agent(agent_name, message)

    with tracer.start_as_current_span(f"invoke_agent {agent_name}") as span:
        span.set_attribute("gen_ai.operation.name", "invoke_agent")
        span.set_attribute("gen_ai.agent.name", agent_name)
        tool_name = _TOOL_BY_AGENT.get(agent_name)
        if tool_name:
            span.set_attribute("gen_ai.tool.name", tool_name)
        if _content_recording_enabled():
            span.set_attribute("gen_ai.input.messages", message)
        try:
            text = _call_prompt_agent(agent_name, message)
        except Exception as exc:  # pragma: no cover - record then re-raise
            span.record_exception(exc)
            raise
        if _content_recording_enabled():
            span.set_attribute("gen_ai.output.messages", text)
        return text


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
    # Wire OpenTelemetry -> Application Insights before serving so every agent
    # run, model call, and sub-agent handoff exports a span. No-ops safely when
    # the connection string is absent (local/mock).
    configure_telemetry()
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
