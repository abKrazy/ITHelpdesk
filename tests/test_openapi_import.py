"""Sanity checks for the bundled ServiceNow Table API OpenAPI spec.

``assets/ServiceNow-OpenAPI-spec.json`` is imported into APIM at deploy time
(``infra/modules/apim.bicep``) and re-exposed as MCP tools that the incident
agent drives (ARCHITECTURE.md §3.2–3.4). If the spec can't be parsed, or is
missing the create / read / update operations the incident flow relies on, the
whole ServiceNow integration breaks at deploy time — so we guard it here.

These are the SAME operations the live MCP client classifies by input schema
(see ``.squad/decisions/inbox/switch-servicenow-mcp-client.md``): the spec ships
WITHOUT ``operationId``s, so this test asserts on HTTP method + path, not names.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_SPEC_PATH = Path(__file__).resolve().parents[1] / "assets" / "ServiceNow-OpenAPI-spec.json"

_TABLE_COLLECTION = "/api/now/table/{tableName}"
_TABLE_ITEM = "/api/now/table/{tableName}/{sys_id}"


@pytest.fixture(scope="module")
def spec() -> dict:
    assert _SPEC_PATH.is_file(), f"OpenAPI spec not found at {_SPEC_PATH}"
    return json.loads(_SPEC_PATH.read_text(encoding="utf-8"))


def test_spec_is_valid_openapi_3(spec: dict) -> None:
    """The document parses and declares an OpenAPI 3.x version + info block."""
    version = spec.get("openapi", "")
    assert version.startswith("3."), f"expected OpenAPI 3.x, got {version!r}"
    assert spec.get("info", {}).get("title"), "spec must declare info.title"
    assert isinstance(spec.get("paths"), dict) and spec["paths"], "spec must have paths"


def test_incident_flow_operations_present(spec: dict) -> None:
    """Create / read / update operations the incident flow depends on all exist."""
    paths = spec["paths"]
    assert _TABLE_COLLECTION in paths, f"missing path {_TABLE_COLLECTION}"
    assert _TABLE_ITEM in paths, f"missing path {_TABLE_ITEM}"

    collection_ops = {m.lower() for m in paths[_TABLE_COLLECTION]}
    item_ops = {m.lower() for m in paths[_TABLE_ITEM]}

    # §3.2 create -> POST to the table collection.
    assert "post" in collection_ops, "create (POST collection) operation missing"
    # §3.3 read -> GET (query by number on the collection, fetch by sys_id on item).
    assert "get" in collection_ops, "query (GET collection) operation missing"
    assert "get" in item_ops, "read-by-id (GET item) operation missing"
    # §3.4 update -> PATCH the item (client prefers PATCH over PUT).
    assert "patch" in item_ops, "update (PATCH item) operation missing"


def test_query_and_key_parameters_present(spec: dict) -> None:
    """The parameters the client sends (sysparm_query, tableName, sys_id) exist."""
    paths = spec["paths"]

    get_collection = paths[_TABLE_COLLECTION]["get"]
    collection_params = {p.get("name") for p in get_collection.get("parameters", [])}
    assert "tableName" in collection_params
    # Number lookup is expressed as sysparm_query=number=INC....
    assert "sysparm_query" in collection_params

    patch_item = paths[_TABLE_ITEM]["patch"]
    item_params = {p.get("name") for p in patch_item.get("parameters", [])}
    assert "tableName" in item_params
    assert "sys_id" in item_params, "update must be addressable by sys_id"


def test_table_record_schema_defined(spec: dict) -> None:
    """A record schema is defined so request/response bodies are typed."""
    schemas = spec.get("components", {}).get("schemas", {})
    assert "TableRecord" in schemas, "components.schemas.TableRecord is required"
