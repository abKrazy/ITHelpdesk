"""AG-UI backend proxy for the IT Helpdesk orchestrator."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

from agent_framework import AgentResponse, AgentResponseUpdate, BaseAgent, Content, Message, tool

from ..orchestrator import Orchestrator
from ..shared.config import Settings

_LOGGER = logging.getLogger(__name__)

ROUTE_ORCHESTRATOR_TOOL_NAME = "route_orchestrator"
TRIAGE_TOOL_NAME = "troubleshoot_from_knowledge_base"
INCIDENT_TOOL_NAME = "manage_servicenow_incident"
CITATIONS_TOOL_NAME = "citations"
PROPOSAL_TOOL_NAME = "servicenow_write_proposal"
APPROVAL_TOOL_NAME = "servicenow_write_approval"

APPROVED_PROPOSAL_PREFIX = "EXECUTE_APPROVED_SERVICENOW_WRITE_PROPOSAL:"

_ROUTE_TO_TOOL = {
    "triage": TRIAGE_TOOL_NAME,
    "incident": INCIDENT_TOOL_NAME,
}
_HANDOFF_TOOLS = {TRIAGE_TOOL_NAME, INCIDENT_TOOL_NAME}
_REJECTED_RESULT = "Error: Tool call invocation was rejected by user."


class HelpdeskAGUIProxyAgent(BaseAgent):
    """Translate Orchestrator proposal-mode output into AG-UI events."""

    def __init__(
        self,
        *,
        settings_factory: Callable[[], Settings],
        mock_orchestrator_factory: Callable[[], Orchestrator],
        openai_client_factory: Callable[[], Any],
    ) -> None:
        super().__init__(
            name="it-helpdesk-agui-proxy",
            description="AG-UI proxy for the IT Helpdesk hosted orchestrator.",
        )
        self._settings_factory = settings_factory
        self._mock_orchestrator_factory = mock_orchestrator_factory
        self._openai_client_factory = openai_client_factory

        async def _approval_tool(proposal_json: str) -> str:
            return await self._execute_approved_proposal(proposal_json)

        self.default_options = {
            "tools": [
                tool(
                    _approval_tool,
                    name=APPROVAL_TOOL_NAME,
                    description="Executes a human-approved ServiceNow write proposal.",
                )
            ]
        }

    def run(
        self,
        messages: Any = None,
        *,
        stream: bool = False,
        session: Any = None,
        **kwargs: Any,
    ):  # type: ignore[override]
        if stream:
            return self._run_stream(messages or [])
        return self._run(messages or [])

    async def _run(self, messages: Any) -> AgentResponse:
        text_parts: list[str] = []
        async for update in self._run_stream(messages):
            for content in update.contents:
                if content.type == "text" and content.text:
                    text_parts.append(content.text)
        return AgentResponse(
            messages=[Message("assistant", [Content.from_text("".join(text_parts))])]
        )

    async def _run_stream(self, messages: Any) -> AsyncIterator[AgentResponseUpdate]:
        approval_result = _approval_result_from_messages(messages)
        if approval_result is not None:
            if _is_rejected_approval_result(approval_result):
                yield AgentResponseUpdate(
                    contents=[
                        Content.from_text(
                            "ServiceNow change cancelled. No incident was created or updated."
                        )
                    ],
                    role="assistant",
                )
                return
            yield AgentResponseUpdate(
                contents=[Content.from_text(f"Approved ServiceNow change executed.\n{approval_result}")],
                role="assistant",
            )
            return

        yield _tool_pair(ROUTE_ORCHESTRATOR_TOOL_NAME)
        settings = self._settings_factory()
        if settings.mock_mode:
            async for update in self._run_mock(messages):
                yield update
        else:
            async for update in self._run_live(messages, settings):
                yield update

    async def _run_mock(self, messages: Any) -> AsyncIterator[AgentResponseUpdate]:
        user_message, history = _latest_user_and_history(messages)
        result = self._mock_orchestrator_factory().run(
            user_message,
            history=history,
            propose_writes=True,
        )
        for hop in result.route:
            tool_name = _ROUTE_TO_TOOL.get(hop)
            if tool_name:
                yield _tool_pair(tool_name)

        proposal = result.servicenow_write_proposal
        if proposal is not None:
            yield _approval_intro()
            yield _approval_request(proposal)
            return

        reply, citations = _mock_reply_and_citations(result)
        yield AgentResponseUpdate(
            contents=[Content.from_text(reply)],
            role="assistant",
        )
        if citations:
            yield _citations_tool(citations)

    async def _run_live(
        self,
        messages: Any,
        settings: Settings,
    ) -> AsyncIterator[AgentResponseUpdate]:
        conversation = _messages_to_responses_input(messages)
        client = self._openai_client_factory()
        model = settings.chat_deployment or "gpt-5.4"
        proposal: dict[str, Any] | None = None
        got_text = False
        seen_handoffs: set[str] = set()
        call_names: dict[str, str] = {}

        def make_stream():
            return client.responses.create(model=model, input=conversation, stream=True)

        async for event in _iter_in_thread(make_stream):
            etype = getattr(event, "type", None)
            if etype == "response.output_item.added":
                item = getattr(event, "item", None)
                if getattr(item, "type", None) == "function_call":
                    call_id = str(getattr(item, "id", None) or getattr(item, "call_id", "") or "")
                    name = str(getattr(item, "name", "") or "")
                    if call_id and name:
                        call_names[call_id] = name
                    if name in _HANDOFF_TOOLS and name not in seen_handoffs:
                        seen_handoffs.add(name)
                        yield _tool_pair(name)
            elif etype == "response.function_call_arguments.done":
                name = str(getattr(event, "name", "") or "")
                item_id = str(getattr(event, "item_id", "") or "")
                name = name or call_names.get(item_id, "")
                arguments = str(getattr(event, "arguments", "") or "{}")
                if name == CITATIONS_TOOL_NAME:
                    citations = _citations_from_arguments(arguments)
                    if citations:
                        yield _citations_tool(citations)
                elif name == PROPOSAL_TOOL_NAME:
                    proposal = _proposal_from_arguments(arguments)
            elif etype == "response.output_text.delta":
                delta = str(getattr(event, "delta", "") or "")
                if delta:
                    got_text = True
                    yield AgentResponseUpdate(
                        contents=[Content.from_text(delta)],
                        role="assistant",
                    )
            elif etype == "response.completed":
                break

        if proposal is not None:
            yield _approval_intro()
            yield _approval_request(proposal)
        elif not got_text:
            yield AgentResponseUpdate(
                contents=[Content.from_text("(the orchestrator returned no content)")],
                role="assistant",
            )

    async def _execute_approved_proposal(self, proposal_json: str) -> str:
        proposal = _loads_proposal(proposal_json)
        settings = self._settings_factory()
        if settings.mock_mode:
            result = self._mock_orchestrator_factory().execute_approved_proposal(proposal)
            return result.reply or "(the incident agent returned no content)"

        command = f"{APPROVED_PROPOSAL_PREFIX}\n{json.dumps(proposal, ensure_ascii=False)}"
        conversation = [{"role": "user", "content": command}]
        client = self._openai_client_factory()
        response = await asyncio.to_thread(
            client.responses.create,
            model=settings.chat_deployment or "gpt-5.4",
            input=conversation,
        )
        return _extract_output_text(response)


def _tool_pair(name: str) -> AgentResponseUpdate:
    call_id = f"call_{uuid.uuid4().hex[:24]}"
    return AgentResponseUpdate(
        contents=[
            Content.from_function_call(call_id, name),
            Content.from_function_result(call_id, result=""),
        ],
        role="assistant",
    )


def _approval_intro() -> AgentResponseUpdate:
    return AgentResponseUpdate(
        contents=[Content.from_text("Please review and approve the ServiceNow change.")],
        role="assistant",
    )


def _approval_request(proposal: dict[str, Any]) -> AgentResponseUpdate:
    call_id = f"approval_{uuid.uuid4().hex[:24]}"
    proposal_json = json.dumps(proposal, ensure_ascii=False, separators=(",", ":"))
    function_call = Content.from_function_call(
        call_id,
        APPROVAL_TOOL_NAME,
        arguments={"proposal_json": proposal_json},
    )
    return AgentResponseUpdate(
        contents=[
            function_call,
            Content("function_approval_request", id=call_id, function_call=function_call),
        ],
        role="assistant",
    )


def _citations_tool(citations: list[dict[str, Any]]) -> AgentResponseUpdate:
    call_id = f"call_{uuid.uuid4().hex[:24]}"
    return AgentResponseUpdate(
        contents=[
            Content.from_function_call(
                call_id,
                CITATIONS_TOOL_NAME,
                arguments={"citations": citations},
            ),
            Content.from_function_result(call_id, result=""),
        ],
        role="assistant",
    )


def _mock_reply_and_citations(result: Any) -> tuple[str, list[dict[str, Any]]]:
    reply = result.reply or "(no response)"
    triage = getattr(result, "triage", None)
    hits = list(getattr(triage, "hits", None) or [])
    if not hits:
        return reply, []

    top = hits[0]
    marker = "【4:0†source】"
    if marker not in reply:
        reply = f"{reply.rstrip()} {marker}"

    source_id = str(getattr(top, "doc_id", "") or getattr(top, "source", "") or "kb-source")
    source_name = str(getattr(top, "source", "") or f"{source_id}.md")
    chunk_id = f"{source_id}-mock-0"
    return reply, [
        {
            "index": 1,
            "sourceId": source_id,
            "sourceTitle": str(getattr(top, "title", "") or source_name),
            "sourceName": source_name,
            "assignmentGroup": str(getattr(top, "assignment_group", "") or ""),
            "markers": [marker],
            "chunkIds": [chunk_id],
            "url": f"mcp://searchindex/{chunk_id}",
        }
    ]


def _approval_result_from_messages(messages: Any) -> str | None:
    for message in messages or []:
        for content in getattr(message, "contents", None) or getattr(message, "content", None) or []:
            if getattr(content, "type", None) != "function_result":
                continue
            call_id = str(getattr(content, "call_id", "") or "")
            if call_id.startswith("approval_"):
                return str(getattr(content, "result", "") or "")
    return None


def _is_rejected_approval_result(result: str) -> bool:
    return "rejected by user" in result.lower() or result == _REJECTED_RESULT


def _text_from_message(message: Any) -> str:
    text = getattr(message, "text", None)
    if isinstance(text, str) and text:
        return text
    parts: list[str] = []
    for content in getattr(message, "contents", None) or getattr(message, "content", None) or []:
        if getattr(content, "type", None) == "text" and getattr(content, "text", None):
            parts.append(str(content.text))
    return "".join(parts)


def _message_role(message: Any) -> str:
    role = getattr(message, "role", "user")
    return str(getattr(role, "value", role) or "user").lower()


def _latest_user_and_history(messages: Any) -> tuple[str, list[dict[str, str]]]:
    turns = [
        {"role": _message_role(message), "content": _text_from_message(message)}
        for message in messages or []
    ]
    turns = [turn for turn in turns if turn["content"]]
    for index in range(len(turns) - 1, -1, -1):
        if turns[index]["role"] == "user":
            return turns[index]["content"], turns[:index]
    return "", turns


def _messages_to_responses_input(messages: Any) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for message in messages or []:
        role = _message_role(message)
        if role not in {"user", "assistant", "system", "developer", "tool"}:
            role = "user"
        text = _text_from_message(message)
        if text:
            items.append({"role": role, "content": text})
    return items


async def _iter_in_thread(make_iterator: Callable[[], Any]) -> AsyncIterator[Any]:
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    sentinel = object()

    def worker() -> None:
        try:
            for item in make_iterator():
                loop.call_soon_threadsafe(queue.put_nowait, item)
        except Exception as exc:  # pragma: no cover - defensive live path
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, sentinel)

    task = loop.run_in_executor(None, worker)
    try:
        while True:
            item = await queue.get()
            if item is sentinel:
                break
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        await task


def _loads_proposal(proposal_json: str) -> dict[str, Any]:
    try:
        proposal = json.loads(proposal_json)
    except json.JSONDecodeError as exc:
        raise ValueError("approval payload proposal_json is not valid JSON") from exc
    if not isinstance(proposal, dict):
        raise ValueError("approval payload proposal_json must be a JSON object")
    return proposal


def _proposal_from_arguments(arguments: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(arguments)
    except json.JSONDecodeError:
        _LOGGER.warning("Ignoring malformed ServiceNow proposal payload")
        return None
    if isinstance(payload, dict) and isinstance(payload.get(PROPOSAL_TOOL_NAME), dict):
        return payload[PROPOSAL_TOOL_NAME]
    return payload if isinstance(payload, dict) else None


def _citations_from_arguments(arguments: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(arguments)
    except json.JSONDecodeError:
        _LOGGER.warning("Ignoring malformed citations payload")
        return []
    citations = payload.get("citations") if isinstance(payload, dict) else None
    return citations if isinstance(citations, list) else []


def _extract_output_text(resp: Any) -> str:
    text = getattr(resp, "output_text", None)
    if text:
        return str(text).strip()
    parts: list[str] = []
    for item in getattr(resp, "output", None) or []:
        for content in getattr(item, "content", None) or []:
            chunk = getattr(content, "text", None)
            if chunk:
                parts.append(str(chunk))
    return "\n".join(parts).strip() or "(the orchestrator returned no content)"


