"""MAF Foundry **Hosted Agent** — the IT Helpdesk Orchestrator.

This is the single brain the UI talks to. It is a Microsoft Agent Framework
(``agent-framework``) agent, packaged as a container and deployed as a **Foundry
Hosted Agent** (Preview). At runtime Foundry serves it over the OpenAI Responses
protocol via :class:`ResponsesHostServer` (``POST /responses`` on port 8088).

The orchestrator makes ONE model pass per turn — the *routing* pass. With full
conversation memory it classifies the user's intent and selects exactly one of two
sub-agents (plus the self-contained input to send it), emitting a ``function_call``
so the UI can render the handoff. It then **streams the chosen sub-agent's Responses
output straight through to the user as the terminal answer** — there is NO second
orchestrator model pass to "relay" the result. Removing that second gpt-5.x pass
(~6.5s of pure platform/tool-calling overhead per turn) is the point of this design;
the sub-agent prompts are authored so their own output is already the complete,
user-ready message, so streaming it through loses nothing.

Each sub-agent is one of the two **Foundry Prompt Agents** (created in Phase 1 by
``scripts/postprovision.py``) invoked by *agent reference* through the project's
OpenAI Responses endpoint:

  * ``troubleshoot_from_knowledge_base`` -> ``it-helpdesk-triage``  (AI Search RAG)
  * ``manage_servicenow_incident``       -> ``it-helpdesk-incident`` (APIM MCP tool)

Why a custom :class:`~agent_framework.BaseAgent` instead of the MAF tool-calling
``Agent`` + a "terminate after tool" middleware: MAF's function-calling loop, when
terminated after a tool, returns the *routing* pass's response (the ``function_call``)
and surfaces the tool's output as a ``function_result`` item — which the Responses
host renders as a tool-output item, NOT as visible assistant text. Making the
sub-agent's text the terminal answer would require injecting a synthetic text
``Content`` into the terminated response, i.e. fighting the framework. A custom agent
that owns its own ``run_stream`` gives precise, framework-supported control over the
exact Responses event sequence (one ``function_call`` chip, then ``output_text``
deltas), which is what the UI's handoff status chips depend on.

Deployment contract (see ``scripts/postprovision.py`` -> ``create_hosted_orchestrator``):
  * The container is built server-side with ``az acr build`` and registered via
    ``AIProjectClient.agents.create_version(... HostedAgentDefinition(container_configuration=...))``.
  * Foundry injects ``FOUNDRY_PROJECT_ENDPOINT`` and ``AZURE_AI_MODEL_DEPLOYMENT_NAME``
    at run time; we also pass them explicitly as env vars for robustness.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

from agent_framework import (
    AgentResponse,
    AgentResponseUpdate,
    BaseAgent,
    Content,
    Message,
)

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

# Reasoning effort for the orchestrator's OWN gpt-5.x routing pass. With the relay
# pass removed there is now exactly ONE orchestrator model pass per turn (routing),
# so this tunes only that pass. We run it at LOW effort — enough to keep routing
# correct while cutting the hidden "thinking" time. Threaded via
# ORCHESTRATOR_REASONING_EFFORT so ops can retune it (e.g. to ``minimal``/``none`` or
# back up to ``medium``) WITHOUT a container rebuild. gpt-5.x reasoning models take
# reasoning.effort on the Responses API; they reject temperature/max_tokens, so we
# never pass those. Set to "" / "default" to omit the override entirely and fall back
# to the model's default effort.
REASONING_EFFORT = os.environ.get("ORCHESTRATOR_REASONING_EFFORT", "low").strip()

# Cloud role name for App Insights (== OTEL service.name). Honors the injected
# OTEL_SERVICE_NAME env var; defaults to the orchestrator's own name.
SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "it-helpdesk-orchestrator")

ROUTING_INSTRUCTIONS = """\
You are the IT Helpdesk Orchestrator's ROUTER. You carry the whole conversation so
you always know the context of earlier turns (including any incident number already
created and the recommended assignment group surfaced by an earlier triage answer).

Your ONLY job is to route: decide which ONE specialist sub-agent should handle this
turn, and call it with a self-contained natural-language input. You do NOT answer
the user's technical question yourself and you do NOT relay or repeat the
sub-agent's output — the platform streams the chosen sub-agent's answer straight to
the user as the final reply. So never restate steps, ticket details, or citations;
just make the right call with the right input.

You have exactly two tools (call EXACTLY ONE of them for any actionable request):
1. troubleshoot_from_knowledge_base — searches the IT knowledge base (RAG) for
   self-service troubleshooting steps (the Triage agent).
2. manage_servicenow_incident — creates, looks up, or updates ServiceNow incidents
   (the Incident agent).

CLASSIFY INTENT FIRST (do this before anything else, including DEFLECT FIRST).
Before choosing a tool, decide which of these two intents the user's message is:

  (A) NEW PROBLEM REPORT / TROUBLESHOOTING HELP — the user is reporting a new
      technical problem or symptom, or asking how to do/fix something. Examples:
      "my laptop is running slow", "I can't connect to VPN", "how do I reset my
      password", "my email won't sync". This still counts as (A) even when the
      user immediately asks to open/create a ticket for that NEW problem.
      -> Follow DEFLECT FIRST: call troubleshoot_from_knowledge_base FIRST.

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
troubleshoot_from_knowledge_base FIRST. Do NOT create a ticket on the first turn of
a new problem. Pass the underlying problem (in natural language) as the ``problem``
argument, even if the user asked to file a ticket.

CREATE ONLY ON CONFIRMATION. Call manage_servicenow_incident to create a ticket
only after the user has seen the KB steps and indicates they didn't help or
explicitly confirms they want a ticket ("go ahead", "yes, file it", "that didn't
work"). When you create it, the ``request`` argument MUST be self-contained: include
the ORIGINAL problem description AND the recommended assignment group from the
earlier triage answer in this conversation, e.g. "create an incident for: my laptop
is running slow; assign to Desktop Support". Read them back out of the conversation
history — do not rely on the sub-agent to remember earlier turns.

FOLLOW-UP QUESTIONS ABOUT AN EXISTING TICKET GO TO THE INCIDENT TOOL. Once a ticket
exists in this conversation, any question about it — its status, state, priority,
urgency, assignment group — or any request to change/update it MUST be routed to
manage_servicenow_incident (include the INC number from the conversation in the
``request``). NEVER route a question about an existing ticket to the knowledge base.

Also route to manage_servicenow_incident whenever the user references an incident
number (e.g. "INC0010036") to check status or update fields.

ALWAYS call exactly one tool for any actionable request. Do NOT answer directly.
The ONLY time you may reply with plain text instead of calling a tool is to ask a
single brief clarifying question when the user's intent is genuinely ambiguous and
you cannot safely pick a tool. Never invent ticket numbers, statuses, or KB content.
"""

# Function-tool schemas advertised to the routing model on the Responses API. These
# mirror the two sub-agents; the routing model picks exactly one and crafts a
# self-contained natural-language argument. (Responses uses the flat function-tool
# shape: type/name/description/parameters at the top level.)
ROUTING_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "troubleshoot_from_knowledge_base",
        "description": (
            "Search the IT knowledge base (Azure AI Search RAG) for self-service "
            "troubleshooting steps. ALWAYS call this FIRST for any technical problem, "
            "and BEFORE creating any ticket, to try to resolve the issue without a "
            "ticket."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "problem": {
                    "type": "string",
                    "description": (
                        "The user's IT problem or 'how do I' question, in natural "
                        "language. Send the underlying problem even if the user asked "
                        "to file a ticket."
                    ),
                }
            },
            "required": ["problem"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "manage_servicenow_incident",
        "description": (
            "Create, look up, or update a ServiceNow incident via the ServiceNow MCP "
            "tool. Use this to FILE a ticket after the user confirms the KB steps "
            "didn't help, and to CHECK or UPDATE any existing ticket (status, "
            "priority, urgency, assignment group) by its INC number."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": (
                        "The incident action in natural language. Examples: 'create "
                        "an incident for: my laptop is running slow; assign to Desktop "
                        "Support', 'check the status of INC0010036', 'update the "
                        "urgency of INC0010036 to high'. Always include the INC number "
                        "for a status check or update, and the original problem + "
                        "assignment group for a create."
                    ),
                }
            },
            "required": ["request"],
            "additionalProperties": False,
        },
    },
]

# Reverse of _TOOL_BY_AGENT: which sub-agent a routing tool name maps to.
_AGENT_BY_TOOL = {
    "troubleshoot_from_knowledge_base": TRIAGE_AGENT_NAME,
    "manage_servicenow_incident": INCIDENT_AGENT_NAME,
}
# The argument field each routing tool carries (the natural-language sub-agent input).
_ARG_FIELD_BY_TOOL = {
    "troubleshoot_from_knowledge_base": "problem",
    "manage_servicenow_incident": "request",
}


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


# --- Reasoning effort ----------------------------------------------------------
def _reasoning_option() -> dict | None:
    """Return the ``reasoning`` block for the routing pass, or None to omit it.

    When ``REASONING_EFFORT`` is a concrete level (default ``low``) we pin the
    gpt-5.x ``reasoning.effort`` on the single per-turn routing pass. An empty /
    ``default`` value omits the override so the model uses its own default effort.
    We never set temperature/max_tokens: reasoning models reject them.
    """
    effort = REASONING_EFFORT.lower()
    if effort and effort != "default":
        return {"effort": effort}
    return None


# --- Routing pass (the ONE orchestrator model pass per turn) -------------------
_INPUT_ROLES = {"assistant", "system", "user", "tool", "developer"}


def _messages_to_input(messages: Any) -> list[dict[str, str]]:
    """Convert the hosting-provided conversation into Responses ``input`` items.

    The host passes the full turn history (prior user + streamed-through sub-agent
    answers) plus the new user message as ``agent_framework.Message`` objects. We
    flatten each to ``{"role", "content"}`` so the routing model classifies intent
    with full context (e.g. the INC number and assignment group from earlier turns).
    """
    if messages is None:
        return []
    if isinstance(messages, str):
        return [{"role": "user", "content": messages}]
    if isinstance(messages, Message):
        messages = [messages]

    items: list[dict[str, str]] = []
    for msg in messages:
        text = getattr(msg, "text", None)
        if isinstance(msg, str):
            text = msg
        if not text:
            continue
        raw_role = getattr(msg, "role", "user")
        role = str(getattr(raw_role, "value", raw_role) or "user").lower()
        if role in ("agent",):
            role = "assistant"
        if role not in _INPUT_ROLES:
            role = "user"
        items.append({"role": role, "content": text})
    return items


class RouteDecision:
    """Outcome of the routing pass: either a sub-agent handoff or a direct reply."""

    def __init__(
        self,
        *,
        tool_name: str | None = None,
        agent_name: str | None = None,
        sub_agent_input: str | None = None,
        call_id: str | None = None,
        arguments_json: str | None = None,
        direct_text: str | None = None,
    ) -> None:
        self.tool_name = tool_name
        self.agent_name = agent_name
        self.sub_agent_input = sub_agent_input
        self.call_id = call_id
        self.arguments_json = arguments_json
        self.direct_text = direct_text


def _tool_args_to_message(tool_name: str, arguments_json: str | None) -> str:
    """Extract the natural-language sub-agent input from the tool-call arguments."""
    field = _ARG_FIELD_BY_TOOL.get(tool_name)
    if arguments_json:
        try:
            parsed = json.loads(arguments_json)
        except (json.JSONDecodeError, TypeError):
            return arguments_json.strip()
        if isinstance(parsed, dict):
            value = parsed.get(field) if field else None
            if isinstance(value, str) and value.strip():
                return value.strip()
            # Fall back to the first string value if the schema field is missing.
            for candidate in parsed.values():
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
    return (arguments_json or "").strip()


def _route_intent(input_items: list[dict[str, str]]) -> RouteDecision:
    """Run the single routing model pass: classify intent + pick one sub-agent.

    Returns a :class:`RouteDecision`. When the model calls a tool we resolve the
    target sub-agent and the self-contained natural-language input it should get.
    When the model replies directly (a brief clarifying question) we carry that
    text through as the terminal answer instead. ``parallel_tool_calls`` is off and
    we take only the FIRST known function call — routing is a single decision.
    """
    client = _get_openai_client()
    kwargs: dict[str, Any] = {
        "model": MODEL,
        "instructions": ROUTING_INSTRUCTIONS,
        "input": input_items or "",
        "tools": ROUTING_TOOLS,
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "store": False,
    }
    reasoning = _reasoning_option()
    if reasoning:
        kwargs["reasoning"] = reasoning

    tracer = _get_tracer()
    if tracer is not None:
        with tracer.start_as_current_span(f"chat {MODEL}") as span:
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.request.model", MODEL)
            span.set_attribute("gen_ai.agent.name", SERVICE_NAME)
            resp = client.responses.create(**kwargs)
    else:
        resp = client.responses.create(**kwargs)

    for item in getattr(resp, "output", None) or []:
        if getattr(item, "type", None) != "function_call":
            continue
        name = getattr(item, "name", None)
        agent_name = _AGENT_BY_TOOL.get(name)
        if agent_name is None:
            continue  # ignore unknown tool names, keep scanning
        arguments = getattr(item, "arguments", None)
        return RouteDecision(
            tool_name=name,
            agent_name=agent_name,
            sub_agent_input=_tool_args_to_message(name, arguments),
            call_id=getattr(item, "call_id", None) or f"call_{uuid.uuid4().hex[:24]}",
            arguments_json=arguments if isinstance(arguments, str) else json.dumps(arguments or {}),
        )

    # No (known) tool call -> the model answered directly (clarifying question).
    return RouteDecision(direct_text=_extract_output_text(resp))


# --- Sub-agent streaming proxy -------------------------------------------------
def _iter_prompt_agent_text(agent_name: str, message: str) -> Iterator[str]:
    """Stream a Foundry Prompt Agent by *agent reference*, yielding TEXT deltas only.

    We forward ONLY ``response.output_text.delta`` events. The sub-agent's own
    internal tool calls (triage's ``knowledge_base_retrieve``, incident's APIM MCP)
    ride other event types on this inner stream; we deliberately drop those so they
    never surface as spurious ``function_call`` items (bogus handoff chips) on the
    OUTER Responses stream the UI consumes. Citations (【…†source】) arrive inline in
    the text deltas, so they are preserved verbatim.
    """
    client = _get_openai_client()
    model = _MODEL_BY_AGENT.get(agent_name, MODEL)
    stream = client.responses.create(
        model=model,
        input=message,
        stream=True,
        extra_body={"agent_reference": {"name": agent_name, "type": "agent_reference"}},
    )
    for event in stream:
        if getattr(event, "type", None) == "response.output_text.delta":
            delta = getattr(event, "delta", None)
            if delta:
                yield str(delta)


async def _astream_prompt_agent(agent_name: str, message: str) -> AsyncIterator[str]:
    """Async wrapper over the sync sub-agent stream, wrapped in a handoff span.

    Bridges the blocking OpenAI stream onto the event loop via a worker thread and
    a queue so the host can flush each token frame without stalling other requests.
    """
    tracer = _get_tracer()
    span_cm = (
        tracer.start_as_current_span(f"invoke_agent {agent_name}")
        if tracer is not None
        else None
    )
    if span_cm is not None:
        span = span_cm.__enter__()
        span.set_attribute("gen_ai.operation.name", "invoke_agent")
        span.set_attribute("gen_ai.agent.name", agent_name)
        tool_name = _TOOL_BY_AGENT.get(agent_name)
        if tool_name:
            span.set_attribute("gen_ai.tool.name", tool_name)
        if _content_recording_enabled():
            span.set_attribute("gen_ai.input.messages", message)

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    sentinel = object()
    collected: list[str] = []

    def _worker() -> None:
        try:
            for delta in _iter_prompt_agent_text(agent_name, message):
                loop.call_soon_threadsafe(queue.put_nowait, delta)
        except Exception as exc:  # surface to the async side
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, sentinel)

    worker = loop.run_in_executor(None, _worker)
    try:
        while True:
            item = await queue.get()
            if item is sentinel:
                break
            if isinstance(item, Exception):
                if span_cm is not None:
                    span.record_exception(item)
                raise item
            collected.append(item)
            yield item
        await worker
    finally:
        if span_cm is not None:
            if _content_recording_enabled():
                span.set_attribute("gen_ai.output.messages", "".join(collected))
            span_cm.__exit__(None, None, None)


# --- Custom orchestrator agent -------------------------------------------------
class RelayOrchestrator(BaseAgent):
    """Routing-then-proxy orchestrator with NO second (relay) model pass.

    One routing model pass selects a sub-agent (emitting a ``function_call`` so the
    UI shows the handoff), then the chosen sub-agent's Responses output is streamed
    straight through as the terminal answer. A direct clarifying reply from the
    routing pass (no tool call) is streamed through as-is.
    """

    def __init__(self) -> None:
        super().__init__(
            name="it-helpdesk-orchestrator",
            description="Routes to KB triage / ServiceNow incident sub-agents and streams their answer.",
        )

    def run(self, messages: Any = None, *, stream: bool = False, **kwargs: Any):  # type: ignore[override]
        input_items = _messages_to_input(messages)
        if stream:
            return self._run_stream(input_items)
        return self._run(input_items)

    async def _run_stream(self, input_items: list[dict[str, str]]) -> AsyncIterator[AgentResponseUpdate]:
        decision = await asyncio.to_thread(_route_intent, input_items)
        if decision.tool_name and decision.agent_name:
            # 1) Emit the handoff chip (function_call item -> UI "Calling X Agent").
            yield AgentResponseUpdate(
                contents=[
                    Content.from_function_call(
                        decision.call_id or f"call_{uuid.uuid4().hex[:24]}",
                        decision.tool_name,
                        arguments=decision.arguments_json,
                    )
                ],
                role="assistant",
            )
            # 2) Stream the sub-agent's answer through verbatim as the terminal text.
            async for delta in _astream_prompt_agent(
                decision.agent_name, decision.sub_agent_input or ""
            ):
                yield AgentResponseUpdate(
                    contents=[Content.from_text(delta)], role="assistant"
                )
        else:
            text = decision.direct_text or _NO_ROUTE_FALLBACK
            yield AgentResponseUpdate(
                contents=[Content.from_text(text)], role="assistant"
            )

    async def _run(self, input_items: list[dict[str, str]]) -> AgentResponse:
        decision = await asyncio.to_thread(_route_intent, input_items)
        if decision.tool_name and decision.agent_name:
            text = await asyncio.to_thread(
                _invoke_prompt_agent, decision.agent_name, decision.sub_agent_input or ""
            )
        else:
            text = decision.direct_text or _NO_ROUTE_FALLBACK
        return AgentResponse(
            messages=[Message("assistant", [Content.from_text(text)])]
        )


_NO_ROUTE_FALLBACK = (
    "Sorry — I couldn't tell whether you need troubleshooting help or a ticket "
    "action. Could you rephrase, or share the incident number if this is about an "
    "existing ticket?"
)


def build_agent() -> RelayOrchestrator:
    """Construct the custom routing-then-proxy orchestrator agent."""
    return RelayOrchestrator()


def main() -> None:
    from agent_framework_foundry_hosting import ResponsesHostServer

    logging.basicConfig(level=logging.INFO)
    # Wire OpenTelemetry -> Application Insights before serving so every agent
    # run, model call, and sub-agent handoff exports a span. No-ops safely when
    # the connection string is absent (local/mock).
    configure_telemetry()
    _LOGGER.info(
        "Starting IT Helpdesk Orchestrator hosted agent on port %s "
        "(project=%s, model=%s, triage=%s, incident=%s, reasoning_effort=%s)",
        PORT,
        PROJECT_ENDPOINT or "<unset>",
        MODEL,
        TRIAGE_AGENT_NAME,
        INCIDENT_AGENT_NAME,
        REASONING_EFFORT or "<model default>",
    )
    ResponsesHostServer(build_agent()).run(port=PORT)


if __name__ == "__main__":
    main()
