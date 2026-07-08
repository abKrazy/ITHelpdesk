"""Tests for the live ServiceNow MCP client (Switch, src/servicenow).

These exercise create / get / update against a FAKE MCP transport — no live
ServiceNow or APIM is required. The fake mimics how APIM re-exposes the Table API
OpenAPI spec as MCP tools: because the spec ships without ``operationId``s, tool
names are auto-generated (``post-…``, ``get-…``, ``patch-…``) and the client must
discover them from ``list_tools`` and classify them by input schema.

The three canonical prompts drive the assertions:
  * create for "Unable to log into Epic"          (ARCHITECTURE.md §3.2)
  * get INC0000057                                 (§3.3)
  * update INC0010027 urgency -> low               (§3.4)
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from servicenow import (
    Incident,
    IncidentNotFound,
    MCPServiceNowClient,
    ServiceNowAuthError,
    ServiceNowUnreachable,
)
from servicenow import mapping

# --- Fake MCP tools mirroring APIM's auto-generated Table API tools ----------
_TABLE_FIELDS = {
    "tableName": {"type": "string"},
    "short_description": {"type": "string"},
    "description": {"type": "string"},
    "assignment_group": {"type": "string"},
    "urgency": {"type": "string"},
    "impact": {"type": "string"},
    "state": {"type": "string"},
}
_QUERY_FIELDS = {
    "tableName": {"type": "string"},
    "sysparm_query": {"type": "string"},
    "sysparm_limit": {"type": "string"},
    "sysparm_fields": {"type": "string"},
    "sysparm_exclude_reference_link": {"type": "string"},
}
_ITEM_FIELDS = {
    "tableName": {"type": "string"},
    "sys_id": {"type": "string"},
    "sysparm_exclude_reference_link": {"type": "string"},
}
_ITEM_WRITE_FIELDS = {**_ITEM_FIELDS, **{k: v for k, v in _TABLE_FIELDS.items() if k != "tableName"}}


def _tool(name: str, props: dict) -> Tool:
    return Tool(name=name, inputSchema={"type": "object", "properties": props})


FAKE_TOOLS = [
    _tool("post-api-now-table-tablename", _TABLE_FIELDS),
    _tool("get-api-now-table-tablename", _QUERY_FIELDS),
    _tool("get-api-now-table-tablename-sys-id", _ITEM_FIELDS),
    _tool("put-api-now-table-tablename-sys-id", _ITEM_WRITE_FIELDS),
    _tool("patch-api-now-table-tablename-sys-id", _ITEM_WRITE_FIELDS),
    _tool("delete-api-now-table-tablename-sys-id", _ITEM_FIELDS),
]


class FakeServiceNow:
    """In-memory ServiceNow Table API seeded with the sample incidents."""

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self._seq = 1000
        self._seed()

    def _seed(self) -> None:
        self.records["a1b2c3d4e5f60000000000000000057a"] = {
            "sys_id": "a1b2c3d4e5f60000000000000000057a",
            "number": "INC0000057",
            "short_description": "Unable to access shared network drive",
            "description": "Mapped drive unavailable after reboot.",
            "assignment_group": "End User Computing",
            "urgency": "2",
            "state": "2",
        }
        self.records["a1b2c3d4e5f60000000000000010027b"] = {
            "sys_id": "a1b2c3d4e5f60000000000000010027b",
            "number": "INC0010027",
            "short_description": "Outlook not syncing email",
            "description": "Emails delayed on desktop client.",
            "assignment_group": "Messaging and Collaboration",
            "urgency": "2",
            "state": "1",
        }

    # -- tool dispatch (records every call) -------------------------------
    def dispatch(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        if name.startswith("post-"):
            return {"result": self._create(arguments)}
        if name == "get-api-now-table-tablename":
            return {"result": self._query(arguments)}
        if name.startswith(("patch-", "put-")):
            return {"result": self._update(arguments)}
        if name.startswith("get-"):
            return {"result": self._get(arguments)}
        raise AssertionError(f"unexpected tool {name}")

    def _create(self, arguments: dict) -> dict:
        self._seq += 1
        sys_id = f"{'0' * 20}{self._seq:012d}"
        record = {k: v for k, v in arguments.items() if k != "tableName"}
        record.setdefault("urgency", "3")
        record.setdefault("state", "1")
        record["sys_id"] = sys_id
        record["number"] = f"INC{self._seq:07d}"
        self.records[sys_id] = record
        return record

    def _query(self, arguments: dict) -> list[dict]:
        query = arguments.get("sysparm_query", "")
        number = query.split("number=", 1)[1] if "number=" in query else None
        hits = [r for r in self.records.values() if r["number"] == number]
        return hits[: int(arguments.get("sysparm_limit", "1"))]

    def _get(self, arguments: dict) -> dict:
        return self.records.get(arguments.get("sys_id"), {})

    def _update(self, arguments: dict) -> dict:
        record = self.records[arguments["sys_id"]]
        for key, value in arguments.items():
            if key in ("tableName", "sys_id") or key.startswith("sysparm_"):
                continue
            record[key] = value
        return record


class FakeSession:
    def __init__(self, backend: FakeServiceNow) -> None:
        self._backend = backend

    async def initialize(self):
        return None

    async def list_tools(self):
        return ListToolsResult(tools=FAKE_TOOLS)

    async def call_tool(self, name: str, arguments: dict):
        payload = self._backend.dispatch(name, arguments)
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload))])


def _factory(backend: FakeServiceNow):
    @asynccontextmanager
    async def factory():
        yield FakeSession(backend)

    return factory


def _client(backend: FakeServiceNow, **kw) -> MCPServiceNowClient:
    return MCPServiceNowClient(
        "https://apim.example/servicenow/mcp",
        session_factory=_factory(backend),
        backoff_base=0.0,
        **kw,
    )


# --- Tests -------------------------------------------------------------------
def test_create_incident_maps_fields_and_urgency():
    backend = FakeServiceNow()
    client = _client(backend)

    inc = client.create_incident(
        short_description="Unable to log into Epic",
        description="User cannot authenticate to Epic EHR.",
        assignment_group="Clinical Applications",
        urgency="high",
    )

    assert isinstance(inc, Incident)
    assert inc.number.startswith("INC")
    assert inc.short_description == "Unable to log into Epic"
    name, args = backend.calls[-1]
    assert name == "post-api-now-table-tablename"
    assert args["tableName"] == "incident"
    assert args["short_description"] == "Unable to log into Epic"
    assert args["assignment_group"] == "Clinical Applications"
    # high -> "1" (authoritative mapping lives in src/servicenow).
    assert args["urgency"] == "1"


def test_create_defaults_urgency_low():
    backend = FakeServiceNow()
    inc = _client(backend).create_incident("Printer offline")
    assert inc.urgency == "3"
    assert backend.calls[-1][1]["urgency"] == "3"


def test_get_incident_by_number():
    backend = FakeServiceNow()
    client = _client(backend)

    inc = client.get_incident("INC0000057")

    assert inc.number == "INC0000057"
    assert inc.sys_id == "a1b2c3d4e5f60000000000000000057a"
    assert inc.assignment_group == "End User Computing"
    name, args = backend.calls[-1]
    assert name == "get-api-now-table-tablename"
    assert args["sysparm_query"] == "number=INC0000057"


def test_get_incident_not_found():
    backend = FakeServiceNow()
    with pytest.raises(IncidentNotFound):
        _client(backend).get_incident("INC9999999")


def test_update_incident_resolves_sys_id_then_patches():
    backend = FakeServiceNow()
    client = _client(backend)

    inc = client.update_incident("INC0010027", {"urgency": "low"})

    assert inc.urgency == "3"
    # First a query to resolve number -> sys_id, then a PATCH (never PUT).
    query_call, patch_call = backend.calls[-2], backend.calls[-1]
    assert query_call[0] == "get-api-now-table-tablename"
    assert query_call[1]["sysparm_query"] == "number=INC0010027"
    assert patch_call[0] == "patch-api-now-table-tablename-sys-id"
    assert patch_call[1]["sys_id"] == "a1b2c3d4e5f60000000000000010027b"
    assert patch_call[1]["urgency"] == "3"


def test_update_incident_not_found():
    backend = FakeServiceNow()
    with pytest.raises(IncidentNotFound):
        _client(backend).update_incident("INC9999999", {"urgency": "low"})


def test_transient_error_is_retried():
    backend = FakeServiceNow()
    attempts = {"n": 0}

    @asynccontextmanager
    async def flaky_factory():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ServiceNowUnreachable("connection refused")
        yield FakeSession(backend)

    client = MCPServiceNowClient(
        "https://apim.example/servicenow/mcp",
        session_factory=flaky_factory,
        backoff_base=0.0,
        max_retries=3,
    )
    inc = client.get_incident("INC0000057")
    assert inc.number == "INC0000057"
    assert attempts["n"] == 2  # failed once, succeeded on retry


def test_auth_error_is_not_retried():
    attempts = {"n": 0}

    @asynccontextmanager
    async def auth_factory():
        attempts["n"] += 1
        raise ServiceNowAuthError("401 Unauthorized")
        yield  # pragma: no cover

    client = MCPServiceNowClient(
        "https://apim.example/servicenow/mcp",
        session_factory=auth_factory,
        backoff_base=0.0,
        max_retries=3,
    )
    with pytest.raises(ServiceNowAuthError):
        client.get_incident("INC0000057")
    assert attempts["n"] == 1  # auth failures are terminal, no retry


def test_mapping_enum_round_trip():
    assert mapping.normalize_urgency("low") == "3"
    assert mapping.normalize_urgency("High") == "1"
    assert mapping.normalize_urgency("2") == "2"
    assert mapping.urgency_label("3") == "Low"
    assert mapping.normalize_state("resolved") == "6"
    assert mapping.normalize_fields({"urgency": "low", "work_notes": "x"}) == {
        "urgency": "3",
        "work_notes": "x",
    }
