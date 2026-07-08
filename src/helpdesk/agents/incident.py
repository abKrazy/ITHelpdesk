"""Incident agent — ServiceNow incident tools (data flows §3.2–§3.4).

The incident agent creates, looks up, and updates ServiceNow incidents. It never
talks to ServiceNow directly; every side effect goes through the typed
:class:`agents.servicenow_client.ServiceNowClient` protocol (the APIM MCP client
in live mode, the in-memory mock offline).

Intent is parsed deterministically from the user message so the same logic runs
online and offline:
  * lookup  — an ``INC…`` number + status/details wording, or a bare number.
  * update  — an ``INC…`` number + change wording (urgency/priority/state).
  * create  — "create/open/new incident" wording.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .prompts import INCIDENT_INSTRUCTIONS
from .servicenow_client import (
    STATE_LABEL,
    URGENCY_LABEL,
    URGENCY_MAP,
    Incident,
    IncidentNotFound,
    ServiceNowClient,
    get_servicenow_client,
)

_INC_RE = re.compile(r"\bINC\d{4,}\b", re.IGNORECASE)
_UPDATE_RE = re.compile(r"\b(update|change|set|modify|escalate|lower|raise)\b", re.IGNORECASE)
_CREATE_RE = re.compile(
    r"\b(create|open|raise|log|file)\b.{0,20}\b(incident|ticket|case)\b|\bnew incident\b",
    re.IGNORECASE,
)
_LOOKUP_RE = re.compile(r"\b(lookup|look up|status|details|detail|show|find|get|check)\b", re.IGNORECASE)
_URGENCY_RE = re.compile(r"\burgency\b", re.IGNORECASE)
_PRIORITY_RE = re.compile(r"\bpriority\b", re.IGNORECASE)
_LEVEL_RE = re.compile(r"\b(low|medium|moderate|high|critical)\b", re.IGNORECASE)

_LEVEL_TO_URGENCY = {
    "low": URGENCY_MAP["low"],
    "medium": URGENCY_MAP["medium"],
    "moderate": URGENCY_MAP["medium"],
    "high": URGENCY_MAP["high"],
    "critical": URGENCY_MAP["high"],
}


@dataclass
class IncidentResult:
    """Outcome of an incident-agent action."""

    action: str  # "lookup" | "create" | "update" | "error"
    message: str
    incident: dict[str, str] | None = None
    fields_changed: dict[str, str] = field(default_factory=dict)
    ok: bool = True


class IncidentAgent:
    """Typed ServiceNow tools wrapped as an agent. Deterministic; mock-capable."""

    instructions = INCIDENT_INSTRUCTIONS

    def __init__(self, client: ServiceNowClient | None = None) -> None:
        self._client = client or get_servicenow_client()

    # -- intent -----------------------------------------------------------
    @staticmethod
    def detect_intent(user_message: str) -> str:
        has_number = bool(_INC_RE.search(user_message))
        if _CREATE_RE.search(user_message):
            return "create"
        if has_number and _UPDATE_RE.search(user_message):
            return "update"
        if has_number:
            return "lookup"
        if _LOOKUP_RE.search(user_message):
            return "lookup"
        return "create"

    @staticmethod
    def _extract_number(user_message: str) -> str | None:
        m = _INC_RE.search(user_message)
        return m.group(0).upper() if m else None

    # -- entry point ------------------------------------------------------
    def run(
        self,
        user_message: str,
        *,
        assignment_group: str = "",
        short_description: str = "",
    ) -> IncidentResult:
        intent = self.detect_intent(user_message)
        if intent == "lookup":
            return self.lookup(user_message)
        if intent == "update":
            return self.update(user_message)
        return self.create(
            user_message,
            assignment_group=assignment_group,
            short_description=short_description,
        )

    # -- actions ----------------------------------------------------------
    def lookup(self, user_message: str) -> IncidentResult:
        number = self._extract_number(user_message)
        if not number:
            return IncidentResult(
                action="error",
                ok=False,
                message="No incident number found to look up.",
            )
        try:
            inc = self._client.get_incident(number)
        except IncidentNotFound:
            return IncidentResult(
                action="lookup", ok=False, message=f"Incident {number} was not found."
            )
        d = inc.to_dict()
        msg = (
            f"Incident {inc.number}: {d.get('short_description', '')}\n"
            f"  State: {STATE_LABEL.get(inc.state, inc.state)}  "
            f"Urgency: {URGENCY_LABEL.get(inc.urgency, inc.urgency)}\n"
            f"  Assignment group: {inc.assignment_group}"
        )
        return IncidentResult(action="lookup", message=msg, incident=d)

    def create(
        self,
        user_message: str,
        *,
        assignment_group: str = "",
        short_description: str = "",
    ) -> IncidentResult:
        short = short_description or self._summarize(user_message)
        inc = self._client.create_incident(
            short_description=short,
            description=user_message.strip(),
            assignment_group=assignment_group,
            urgency=URGENCY_MAP["medium"],
        )
        d = inc.to_dict()
        msg = (
            f"Created incident {inc.number} and assigned it to "
            f"{inc.assignment_group or 'the default queue'}."
        )
        return IncidentResult(
            action="create",
            message=msg,
            incident=d,
            fields_changed={"assignment_group": inc.assignment_group},
        )

    def update(self, user_message: str) -> IncidentResult:
        number = self._extract_number(user_message)
        if not number:
            return IncidentResult(
                action="error", ok=False, message="No incident number found to update."
            )
        fields = self._parse_update_fields(user_message)
        if not fields:
            return IncidentResult(
                action="update",
                ok=False,
                message=f"I couldn't tell what to change on {number}.",
            )
        try:
            inc = self._client.update_incident(number, fields)
        except IncidentNotFound:
            return IncidentResult(
                action="update", ok=False, message=f"Incident {number} was not found."
            )
        changes = ", ".join(
            f"{field}={self._field_label(field, value)}" for field, value in fields.items()
        )
        return IncidentResult(
            action="update",
            message=f"Updated incident {inc.number}: {changes}.",
            incident=inc.to_dict(),
            fields_changed=fields,
        )

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _field_label(field: str, value: str) -> str:
        if field == "urgency":
            return URGENCY_LABEL.get(value, value)
        if field == "state":
            return STATE_LABEL.get(value, value)
        return value

    @staticmethod
    def _parse_update_fields(user_message: str) -> dict[str, str]:
        fields: dict[str, str] = {}
        level_match = _LEVEL_RE.search(user_message)
        if (_URGENCY_RE.search(user_message) or _PRIORITY_RE.search(user_message)) and level_match:
            level = level_match.group(1).lower()
            urgency = _LEVEL_TO_URGENCY.get(level)
            if urgency:
                fields["urgency"] = urgency
        return fields

    @staticmethod
    def _summarize(user_message: str) -> str:
        # First sentence, trimmed, as the incident short description.
        first = re.split(r"(?<=[.!?])\s+", user_message.strip())[0]
        return first[:160] if first else user_message.strip()[:160]


__all__ = ["IncidentAgent", "IncidentResult", "Incident"]
