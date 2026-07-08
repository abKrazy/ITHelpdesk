"""ServiceNow / APIM MCP integration package (Owner: Switch).

Public surface — the integration seam with Trinity's incident agent:

    from servicenow import build_client
    client = build_client(settings.servicenow_mcp_endpoint)
    incident = client.create_incident("Unable to log into Epic", ...)

``build_client(mcp_endpoint) -> ServiceNowClient`` returns a live client that
talks to the APIM MCP endpoint and implements Trinity's ``ServiceNowClient``
protocol (create / get / update incident). Field & enum mapping and the error
taxonomy live in :mod:`servicenow.mapping` and :mod:`servicenow.errors`.
"""

from __future__ import annotations

from .client import (
    Incident,
    IncidentNotFound,
    MCPServiceNowClient,
    build_client,
)
from .errors import (
    ServiceNowAuthError,
    ServiceNowError,
    ServiceNowToolError,
    ServiceNowUnreachable,
)

__all__ = [
    "build_client",
    "MCPServiceNowClient",
    "Incident",
    "IncidentNotFound",
    "ServiceNowError",
    "ServiceNowUnreachable",
    "ServiceNowAuthError",
    "ServiceNowToolError",
]
