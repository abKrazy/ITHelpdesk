"""Live ServiceNow MCP client.

The incident agent never talks to ServiceNow directly. Instead APIM imports the
ServiceNow Table API (``assets/ServiceNow-OpenAPI-spec.json``) and re-exposes its
operations as **MCP tools** on a streamable-HTTP endpoint
(``SERVICENOW_MCP_ENDPOINT`` = ``{gateway}/servicenow/mcp``; see
``infra/modules/apim.bicep``). This module is the client for that endpoint.

Design notes
------------
* **Auth to APIM.** The MCP API is imported with ``subscriptionRequired: false``,
  so no subscription key is required by default; the gateway injects ServiceNow
  Basic auth from Key Vault-backed named values (Switch's ``apim.bicep``), so the
  client never handles ServiceNow credentials. For hardened deployments an APIM
  subscription key and/or bearer token can be supplied via environment variables
  and are sent as headers (see :func:`build_client`).
* **Tool discovery.** The OpenAPI spec ships without ``operationId``s, so APIM
  auto-generates tool names. Rather than hard-code guessed names, the client
  calls ``list_tools`` and classifies each tool by its input schema (presence of
  ``sys_id`` / ``sysparm_query`` / body fields) into the four logical operations
  it needs: create, query-by-number, get-by-sys_id, update. Names can also be
  pinned via ``SERVICENOW_MCP_TOOL_*`` env vars.
* **Sync surface over async transport.** The ``mcp`` client is async; Trinity's
  :class:`ServiceNowClient` protocol is synchronous, so each method bridges to the
  event loop (running in a worker thread when already inside a loop).
* **Resilience.** Connection/timeout failures are retried with bounded
  exponential backoff; auth and not-found conditions are surfaced immediately
  with distinct exception types.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import os
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncContextManager, Callable

from . import mapping
from .errors import (
    ServiceNowAuthError,
    ServiceNowError,
    ServiceNowToolError,
    ServiceNowUnreachable,
)

# ---------------------------------------------------------------------------
# Trinity's contract (Incident dataclass + IncidentNotFound) — loaded so type
# identity matches exactly regardless of the final package layout.
# ---------------------------------------------------------------------------
_CONTRACT_CANDIDATES = (
    "agents.servicenow_client",
    "src.agents.servicenow_client",
    "helpdesk.agents.servicenow_client",
)


def _load_contract() -> Any:
    """Return the module defining ``Incident`` / ``IncidentNotFound``.

    Resolution order (first hit wins, guaranteeing shared type identity with the
    caller):

    1. An already-imported module in ``sys.modules`` exposing the seam
       (``get_servicenow_client`` + ``Incident``) — this is the module Trinity's
       runtime is using when it calls :func:`build_client`.
    2. Known import paths for the current / candidate package layouts.
    3. Direct file load of ``../agents/servicenow_client.py`` relative to this
       file (works even though ``agents/__init__`` may not import yet).
    """
    for module in list(sys.modules.values()):
        if module is None:
            continue
        if hasattr(module, "Incident") and hasattr(module, "IncidentNotFound") and hasattr(
            module, "get_servicenow_client"
        ):
            return module

    for name in _CONTRACT_CANDIDATES:
        try:
            return importlib.import_module(name)
        except Exception:  # noqa: BLE001 - fall through to file load.
            continue

    contract_path = Path(__file__).resolve().parent.parent / "agents" / "servicenow_client.py"
    spec = importlib.util.spec_from_file_location("servicenow._contract", contract_path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive.
        raise ImportError(f"Cannot load ServiceNow contract from {contract_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("servicenow._contract", module)
    spec.loader.exec_module(module)
    return module


_contract = _load_contract()
Incident = _contract.Incident
IncidentNotFound = _contract.IncidentNotFound


# ---------------------------------------------------------------------------
# Logical operations exposed by the ServiceNow Table API (mapped to MCP tools).
# ---------------------------------------------------------------------------
OP_CREATE = "create"
OP_QUERY = "query"
OP_GET = "get"
OP_UPDATE = "update"

_ENV_TOOL_OVERRIDES = {
    OP_CREATE: "SERVICENOW_MCP_TOOL_CREATE",
    OP_QUERY: "SERVICENOW_MCP_TOOL_QUERY",
    OP_GET: "SERVICENOW_MCP_TOOL_GET",
    OP_UPDATE: "SERVICENOW_MCP_TOOL_UPDATE",
}

# Body fields that only appear on write operations (POST/PUT/PATCH).
_BODY_FIELD_HINTS = {
    "short_description",
    "description",
    "urgency",
    "impact",
    "state",
    "assignment_group",
    "work_notes",
    "comments",
}
# APIM MCP-from-REST for the ServiceNow Table API names the request-body
# object "TableRecord"; keep it with the generic body containers so both
# tool classification and request argument shaping recognize write tools.
_BODY_CONTAINER_KEYS = ("body", "requestBody", "payload", "TableRecord")


def _tool_props(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "inputSchema", None) or {}
    return schema.get("properties", {}) if isinstance(schema, dict) else {}


def _classify_tool(tool: Any) -> str | None:
    """Best-effort map an MCP tool to one of the four logical operations."""
    props = _tool_props(tool)
    text = f"{getattr(tool, 'name', '')} {getattr(tool, 'description', '') or ''}".lower()

    has_sys_id = "sys_id" in props
    has_query = "sysparm_query" in props
    has_body = any(f in props for f in _BODY_FIELD_HINTS) or any(
        k in props for k in _BODY_CONTAINER_KEYS
    )
    is_delete = "delete" in text

    if is_delete:
        return None
    if not has_sys_id and has_body:
        return OP_CREATE
    if not has_sys_id and has_query:
        return OP_QUERY
    if has_sys_id and has_body:
        return OP_UPDATE
    if has_sys_id:
        return OP_GET
    return None


def _resolve_tools(tools: list[Any]) -> dict[str, Any]:
    """Return {logical_op: tool} chosen from the discovered tool list."""
    resolved: dict[str, Any] = {}
    for tool in tools:
        op = _classify_tool(tool)
        if op is None:
            continue
        if op == OP_UPDATE:
            # Prefer PATCH over PUT when both are present.
            name = getattr(tool, "name", "").lower()
            existing = resolved.get(op)
            if existing is None or "patch" in name:
                resolved[op] = tool
        else:
            resolved.setdefault(op, tool)
    return resolved


# ---------------------------------------------------------------------------
# Async -> sync bridge.
# ---------------------------------------------------------------------------
def _run_sync(coro: Any) -> Any:
    """Run ``coro`` to completion from synchronous code, safely.

    If no event loop is running we use :func:`asyncio.run`; otherwise the
    coroutine is driven on a fresh loop in a worker thread so we never collide
    with an already-running loop (e.g. inside an async agent).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    box: dict[str, Any] = {}

    def _worker() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller thread.
            box["error"] = exc

    thread = threading.Thread(target=_worker, name="servicenow-mcp", daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


# ---------------------------------------------------------------------------
# Result parsing helpers.
# ---------------------------------------------------------------------------
def _flatten_value(value: Any) -> str:
    """ServiceNow reference fields come back as {value, display_value} — flatten."""
    if isinstance(value, dict):
        for key in ("display_value", "value"):
            if key in value:
                return str(value[key])
        return ""
    return "" if value is None else str(value)


def _record_to_incident(record: dict[str, Any]) -> Incident:
    core = {
        "number",
        "sys_id",
        "short_description",
        "description",
        "assignment_group",
        "urgency",
        "state",
    }
    extra = {
        k: _flatten_value(v) for k, v in record.items() if k not in core and v is not None
    }
    return Incident(
        number=_flatten_value(record.get("number")),
        sys_id=_flatten_value(record.get("sys_id")),
        short_description=_flatten_value(record.get("short_description")),
        description=_flatten_value(record.get("description")),
        assignment_group=_flatten_value(record.get("assignment_group")),
        urgency=_flatten_value(record.get("urgency")) or "3",
        state=_flatten_value(record.get("state")) or "1",
        fields=extra,
    )


class MCPServiceNowClient:
    """Synchronous ServiceNow client backed by the APIM MCP endpoint.

    Implements Trinity's :class:`ServiceNowClient` protocol
    (``create_incident`` / ``get_incident`` / ``update_incident``).
    """

    def __init__(
        self,
        mcp_endpoint: str,
        *,
        table: str = mapping.TABLE_INCIDENT,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        backoff_cap: float = 8.0,
        session_factory: Callable[[], AsyncContextManager[Any]] | None = None,
        tool_overrides: dict[str, str] | None = None,
    ) -> None:
        if not mcp_endpoint:
            raise ServiceNowError("SERVICENOW_MCP_ENDPOINT is required to build a live client.")
        self._endpoint = mcp_endpoint
        self._table = table
        self._headers = headers or {}
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._session_factory = session_factory or self._default_session_factory
        self._tool_overrides = tool_overrides or {}
        self._tool_cache: dict[str, Any] = {}
        self._tool_name_cache: dict[str, str] = {}

    # -- transport ---------------------------------------------------------
    @asynccontextmanager
    async def _default_session_factory(self):  # pragma: no cover - needs live/network.
        # Imported lazily so the module imports even if `mcp` isn't installed
        # (mock-mode installs may omit the servicenow extra).
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(
            self._endpoint, headers=self._headers, timeout=self._timeout
        ) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                yield session

    # -- tool resolution ---------------------------------------------------
    async def _get_tool(self, session: Any, op: str) -> tuple[str, Any]:
        """Resolve (tool_name, tool) for a logical op, discovering + caching."""
        # Explicit override (env / ctor) short-circuits discovery.
        override = self._tool_overrides.get(op) or os.environ.get(_ENV_TOOL_OVERRIDES[op], "")
        if override:
            self._tool_name_cache[op] = override
            return override, self._tool_cache.get(op)

        if op in self._tool_cache:
            return self._tool_name_cache[op], self._tool_cache[op]

        listing = await session.list_tools()
        tools = list(getattr(listing, "tools", listing) or [])
        resolved = _resolve_tools(tools)
        if op not in resolved:
            available = ", ".join(sorted(getattr(t, "name", "?") for t in tools)) or "<none>"
            raise ServiceNowToolError(
                f"MCP server exposes no tool for operation {op!r}. "
                f"Available tools: {available}. "
                f"Pin one with {_ENV_TOOL_OVERRIDES[op]}."
            )
        for resolved_op, tool in resolved.items():
            self._tool_cache[resolved_op] = tool
            self._tool_name_cache[resolved_op] = getattr(tool, "name", resolved_op)
        return self._tool_name_cache[op], self._tool_cache[op]

    def _place_body(self, tool: Any, body: dict[str, str]) -> dict[str, Any]:
        """Nest the request body where the tool schema expects it (or flatten)."""
        props = _tool_props(tool)
        for key in _BODY_CONTAINER_KEYS:
            if key in props:
                return {key: body}
        return dict(body)

    def _prepare_params(self, tool: Any, params: dict[str, str]) -> dict[str, Any]:
        """Keep only path/query params the tool actually declares (if we know)."""
        props = _tool_props(tool)
        if not props:
            return dict(params)
        return {k: v for k, v in params.items() if k in props}

    # -- MCP call + result handling ---------------------------------------
    async def _call(self, session: Any, op: str, params: dict[str, str], body: dict[str, str] | None):
        tool_name, tool = await self._get_tool(session, op)
        arguments: dict[str, Any] = dict(self._prepare_params(tool, params)) if tool else dict(params)
        if body is not None:
            arguments.update(self._place_body(tool, body) if tool else body)
        try:
            result = await session.call_tool(tool_name, arguments)
        except Exception as exc:  # noqa: BLE001 - transport-level failure.
            raise _classify_transport_error(exc)
        return self._parse_result(result)

    def _parse_result(self, result: Any) -> Any:
        payload = _extract_payload(result)
        # ServiceNow wraps records under "result".
        return payload.get("result", payload) if isinstance(payload, dict) else payload

    # -- retry orchestration ----------------------------------------------
    async def _execute(self, op_coro: Callable[[Any], Any]) -> Any:
        attempt = 0
        last_error: Exception | None = None
        while True:
            try:
                async with self._session_factory() as session:
                    if hasattr(session, "initialize"):
                        await session.initialize()
                    return await op_coro(session)
            except ServiceNowUnreachable as exc:
                last_error = exc
                if attempt >= self._max_retries:
                    raise
                delay = min(self._backoff_base * (2**attempt), self._backoff_cap)
                await asyncio.sleep(delay)
                attempt += 1
            # ServiceNowAuthError / ServiceNowToolError / IncidentNotFound: no retry.
        # unreachable
        raise last_error  # pragma: no cover

    # -- public protocol surface ------------------------------------------
    def create_incident(
        self,
        short_description: str,
        description: str = "",
        assignment_group: str = "",
        urgency: str = "3",
    ) -> Incident:
        body = mapping.build_create_payload(
            short_description, description, assignment_group, urgency
        )

        async def op(session: Any) -> Incident:
            record = await self._call(
                session, OP_CREATE, {"tableName": self._table}, body
            )
            if not isinstance(record, dict) or not record:
                raise ServiceNowToolError("Create returned no incident record.")
            return _record_to_incident(record)

        return _run_sync(self._execute(op))

    def get_incident(self, number: str) -> Incident:
        query = mapping.number_query(number)

        async def op(session: Any) -> Incident:
            params = {
                "tableName": self._table,
                "sysparm_query": query,
                "sysparm_limit": "1",
                "sysparm_exclude_reference_link": "true",
            }
            records = await self._call(session, OP_QUERY, params, None)
            if isinstance(records, dict):
                records = [records]
            if not records:
                raise IncidentNotFound(number)
            return _record_to_incident(records[0])

        return _run_sync(self._execute(op))

    def update_incident(self, number: str, fields: dict[str, str]) -> Incident:
        body = mapping.normalize_fields(fields)

        async def op(session: Any) -> Incident:
            # Resolve number -> sys_id first (ARCHITECTURE.md §3.4).
            lookup = {
                "tableName": self._table,
                "sysparm_query": mapping.number_query(number),
                "sysparm_limit": "1",
                "sysparm_fields": "sys_id,number",
                "sysparm_exclude_reference_link": "true",
            }
            records = await self._call(session, OP_QUERY, lookup, None)
            if isinstance(records, dict):
                records = [records]
            if not records:
                raise IncidentNotFound(number)
            sys_id = _flatten_value(records[0].get("sys_id"))
            if not sys_id:
                raise ServiceNowToolError(f"Lookup for {number} returned no sys_id.")
            record = await self._call(
                session,
                OP_UPDATE,
                {"tableName": self._table, "sys_id": sys_id,
                 "sysparm_exclude_reference_link": "true"},
                body,
            )
            if not isinstance(record, dict) or not record:
                raise ServiceNowToolError("Update returned no incident record.")
            return _record_to_incident(record)

        return _run_sync(self._execute(op))


# ---------------------------------------------------------------------------
# Module-level result / error helpers.
# ---------------------------------------------------------------------------
def _extract_payload(result: Any) -> Any:
    """Turn an MCP ``CallToolResult`` into a Python dict/list.

    Prefers ``structuredContent``; falls back to concatenated text content parsed
    as JSON. Raises on error results, distinguishing auth failures.
    """
    if getattr(result, "isError", False):
        text = _result_text(result)
        raise _classify_result_error(text)

    structured = getattr(result, "structuredContent", None)
    if structured:
        return structured

    text = _result_text(result).strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ServiceNowToolError(f"Unparseable MCP tool result: {text[:200]!r}") from exc


def _result_text(result: Any) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "".join(parts)


def _classify_result_error(text: str) -> ServiceNowError:
    lowered = text.lower()
    if "401" in lowered or "unauthorized" in lowered or "403" in lowered or "forbidden" in lowered:
        return ServiceNowAuthError(f"ServiceNow authentication failed: {text[:200]}")
    return ServiceNowToolError(f"MCP tool returned an error: {text[:200]}")


def _classify_transport_error(exc: Exception) -> ServiceNowError:
    """Map an ``mcp``/``httpx`` transport exception to our taxonomy."""
    if isinstance(exc, ServiceNowError):
        return exc
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "401" in text or "unauthorized" in text or "403" in text or "forbidden" in text:
        return ServiceNowAuthError(f"Authentication to APIM/ServiceNow failed: {exc}")
    if any(tok in name for tok in ("timeout", "connect", "network", "read", "pool")) or any(
        tok in text for tok in ("timed out", "connection", "unreachable", "refused")
    ):
        return ServiceNowUnreachable(f"APIM MCP endpoint unreachable: {exc}")
    return ServiceNowToolError(f"MCP transport error: {exc}")


def build_client(mcp_endpoint: str) -> MCPServiceNowClient:
    """Factory used by ``get_servicenow_client()`` (the Switch<->Trinity seam).

    Reads optional auth headers from the environment. By default the imported MCP
    API requires no subscription (see ``apim.bicep``); these are only used for
    hardened deployments:

      * ``SERVICENOW_MCP_SUBSCRIPTION_KEY`` -> ``Ocp-Apim-Subscription-Key`` header
      * ``SERVICENOW_MCP_ACCESS_TOKEN``     -> ``Authorization: Bearer …`` header
    """
    headers: dict[str, str] = {}
    sub_key = os.environ.get("SERVICENOW_MCP_SUBSCRIPTION_KEY", "")
    if sub_key:
        headers["Ocp-Apim-Subscription-Key"] = sub_key
    token = os.environ.get("SERVICENOW_MCP_ACCESS_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    instance = os.environ.get("SERVICENOW_INSTANCE_URL", "")
    table = os.environ.get("SERVICENOW_TABLE", mapping.TABLE_INCIDENT)
    client = MCPServiceNowClient(
        mcp_endpoint, table=table, headers=headers or None
    )
    # `instance` is informational (used for building record URLs); retained for
    # callers that want to render links.
    client.instance_url = instance  # type: ignore[attr-defined]
    return client


__all__ = [
    "MCPServiceNowClient",
    "build_client",
    "Incident",
    "IncidentNotFound",
    "ServiceNowError",
    "ServiceNowUnreachable",
    "ServiceNowAuthError",
    "ServiceNowToolError",
]
