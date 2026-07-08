"""ServiceNow client abstraction used by the incident agent.

The incident agent NEVER talks to ServiceNow directly — it goes through the APIM
MCP endpoint (``SERVICENOW_MCP_ENDPOINT``). The transport + ServiceNow field/enum
mapping is owned by Switch in ``src/servicenow``. Because that module may not be
present yet, this file defines:

  * :class:`ServiceNowClient` — the protocol the incident agent depends on
    (create / get / update incident). This IS the integration contract with
    Switch.
  * :class:`MockServiceNowClient` — an in-memory fake seeded with the sample
    incidents so the whole stack runs offline (CI / local smoke test).
  * :func:`get_servicenow_client` — factory that, in live mode, adapts Switch's
    ``src/servicenow`` MCP client to this protocol, and raises a clear
    "integration pending" error if it isn't wired yet. This is the seam.

Urgency enum mapping (low/medium/high -> ServiceNow 3/2/1) is authoritatively
owned by ``src/servicenow``; a minimal copy lives here only to drive mock mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

# Minimal ServiceNow urgency/impact enum map for MOCK mode only.
# Authoritative mapping lives in src/servicenow (Switch).
URGENCY_MAP = {"high": "1", "medium": "2", "low": "3"}
URGENCY_LABEL = {"1": "High", "2": "Medium", "3": "Low"}


@dataclass
class Incident:
    """A ServiceNow incident record (subset of Table API fields)."""

    number: str
    sys_id: str
    short_description: str = ""
    description: str = ""
    assignment_group: str = ""
    urgency: str = "3"
    state: str = "1"  # 1 = New
    fields: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, str]:
        d = {
            "number": self.number,
            "sys_id": self.sys_id,
            "short_description": self.short_description,
            "description": self.description,
            "assignment_group": self.assignment_group,
            "urgency": self.urgency,
            "urgency_label": URGENCY_LABEL.get(self.urgency, self.urgency),
            "state": self.state,
        }
        d.update(self.fields)
        return d


class IncidentNotFound(Exception):
    """Raised when a lookup/update targets a non-existent incident number."""


class ServiceNowClient(Protocol):
    """Integration contract implemented by Switch's MCP client (src/servicenow)."""

    def create_incident(
        self,
        short_description: str,
        description: str = "",
        assignment_group: str = "",
        urgency: str = "3",
    ) -> Incident: ...

    def get_incident(self, number: str) -> Incident: ...

    def update_incident(self, number: str, fields: dict[str, str]) -> Incident: ...


class MockServiceNowClient:
    """In-memory ServiceNow used for offline runs.

    Seeded with the two sample incidents referenced by the validation prompts
    (``INC0000057`` lookup, ``INC0010027`` update).
    """

    def __init__(self) -> None:
        self._by_number: dict[str, Incident] = {}
        self._seq = 100
        self._seed()

    def _seed(self) -> None:
        self._by_number["INC0000057"] = Incident(
            number="INC0000057",
            sys_id="a1b2c3d4e5f60000000000000000057a",
            short_description="Unable to access shared network drive",
            description="User reports the mapped drive is unavailable after reboot.",
            assignment_group="End User Computing",
            urgency="2",
            state="2",  # In Progress
        )
        self._by_number["INC0010027"] = Incident(
            number="INC0010027",
            sys_id="a1b2c3d4e5f60000000000000010027b",
            short_description="Outlook not syncing email",
            description="Emails delayed by several hours on the desktop client.",
            assignment_group="Messaging and Collaboration",
            urgency="2",
            state="1",
        )

    def create_incident(
        self,
        short_description: str,
        description: str = "",
        assignment_group: str = "",
        urgency: str = "3",
    ) -> Incident:
        self._seq += 1
        number = f"INC{self._seq:07d}"
        inc = Incident(
            number=number,
            sys_id=f"{'0' * 20}{self._seq:012d}",
            short_description=short_description,
            description=description,
            assignment_group=assignment_group,
            urgency=urgency,
            state="1",
        )
        self._by_number[number] = inc
        return inc

    def get_incident(self, number: str) -> Incident:
        inc = self._by_number.get(number.strip().upper())
        if inc is None:
            raise IncidentNotFound(number)
        return inc

    def update_incident(self, number: str, fields: dict[str, str]) -> Incident:
        inc = self.get_incident(number)
        for key, value in fields.items():
            if hasattr(inc, key):
                setattr(inc, key, value)
            else:
                inc.fields[key] = value
        return inc


def get_servicenow_client() -> ServiceNowClient:
    """Factory: Switch's live MCP client when configured, else the mock.

    Live path is the integration seam with ``src/servicenow``. Switch's module is
    expected to expose ``build_client(mcp_endpoint) -> ServiceNowClient`` (or a
    compatible adapter). Until that lands, live mode raises a clear error rather
    than silently faking data.
    """
    from ..shared import get_settings

    settings = get_settings()
    if settings.mock_mode or not settings.servicenow_mcp_endpoint:
        return MockServiceNowClient()

    try:
        # Integration seam — Switch owns the ServiceNow/APIM MCP client. Prefer the
        # umbrella package path; fall back to a top-level ``servicenow`` package.
        try:
            from helpdesk.servicenow import build_client  # type: ignore
        except Exception:
            from servicenow import build_client  # type: ignore
    except Exception as exc:  # pragma: no cover - live-only path.
        raise RuntimeError(
            "SERVICENOW_MCP_ENDPOINT is set but no ServiceNow client exposes "
            "build_client(mcp_endpoint). Expected at 'helpdesk.servicenow' (preferred) "
            "or top-level 'servicenow'. This is the Switch <-> Trinity integration seam "
            "(ARCHITECTURE.md §8)."
        ) from exc
    return build_client(settings.servicenow_mcp_endpoint)  # type: ignore[no-any-return]
