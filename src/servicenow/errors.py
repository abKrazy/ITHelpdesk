"""ServiceNow integration error taxonomy.

Switch's contract with the Orchestrator is that failures are *legible*: the agent
must be able to tell "ticket not found" from "instance unreachable" from "auth
failed" and react accordingly (retry, escalate, surface to the user). These types
encode that distinction. ``IncidentNotFound`` is intentionally NOT defined here —
it is imported from Trinity's contract module so the type identity matches exactly
(see :mod:`servicenow.client`).
"""

from __future__ import annotations


class ServiceNowError(RuntimeError):
    """Base class for every ServiceNow integration failure."""


class ServiceNowUnreachable(ServiceNowError):
    """The APIM MCP endpoint / ServiceNow instance could not be reached.

    Raised for connection failures and timeouts, after retries are exhausted.
    Transient — the caller may reasonably retry later.
    """


class ServiceNowAuthError(ServiceNowError):
    """APIM rejected the request or ServiceNow returned 401/403.

    Not transient — retrying with the same credentials will not help.
    """


class ServiceNowToolError(ServiceNowError):
    """The MCP server did not expose the expected Table API tool, or returned an
    unparseable / error result that is not a not-found or auth condition.
    """
