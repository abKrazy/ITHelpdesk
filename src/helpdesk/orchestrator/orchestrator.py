"""Orchestrator routing logic (Microsoft Agent Framework, hosted-agent shape).

The Orchestrator is the single entry point the UI invokes. It decides which
specialist agent handles a request and relays the consolidated reply:

  * incident lookup  — "lookup details for incident INC0000057"        (§3.3)
  * triage -> create — "Unable to log into Epic. Create a new incident." (§3.2)
  * incident update  — "update urgency for INC0010027 to low"           (§3.4)
  * triage resolve   — a general how-to question resolved from the KB    (§3.1)

The routing is deterministic so it can be evaluated offline (``HELPDESK_MOCK=1``)
against the sample prompts without any live Azure dependency. In live mode the
same agents are backed by Foundry; the routing contract is identical.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..agents.incident import IncidentAgent, IncidentResult
from ..agents.prompts import ORCHESTRATOR_INSTRUCTIONS
from ..agents.triage import TriageAgent, TriageResult

_INC_RE = re.compile(r"\bINC\d{4,}\b", re.IGNORECASE)
_CREATE_RE = re.compile(
    r"\b(create|open|raise|log|file)\b.{0,20}\b(incident|ticket|case)\b|\bnew incident\b",
    re.IGNORECASE,
)
_UPDATE_RE = re.compile(r"\b(update|change|set|modify|escalate|lower)\b", re.IGNORECASE)


@dataclass
class OrchestratorResponse:
    """Consolidated result the UI renders."""

    reply: str
    route: list[str] = field(default_factory=list)
    triage: TriageResult | None = None
    incident: IncidentResult | None = None


class Orchestrator:
    """Deterministic router over the triage + incident agents."""

    instructions = ORCHESTRATOR_INSTRUCTIONS

    def __init__(
        self,
        triage_agent: TriageAgent | None = None,
        incident_agent: IncidentAgent | None = None,
    ) -> None:
        self._triage = triage_agent or TriageAgent()
        self._incident = incident_agent or IncidentAgent()

    def run(self, user_message: str) -> OrchestratorResponse:
        text = user_message.strip()
        has_number = bool(_INC_RE.search(text))
        wants_create = bool(_CREATE_RE.search(text))
        wants_update = has_number and bool(_UPDATE_RE.search(text))

        # 1. Existing incident + change request -> incident update (§3.4)
        if wants_update:
            return self._incident_only(text, "update")

        # 2. Existing incident, no change verb -> incident lookup (§3.3)
        if has_number and not wants_create:
            return self._incident_only(text, "lookup")

        # 3. Explicit "create incident" -> triage first (KB + assignment group),
        #    then hand off to incident create (§3.2).
        if wants_create:
            triage = self._triage.run(text)
            incident = self._incident.create(
                text,
                assignment_group=triage.assignment_group,
                short_description=self._short_desc(text, triage),
            )
            reply = self._compose_create_reply(triage, incident)
            return OrchestratorResponse(
                reply=reply,
                route=["triage", "incident"],
                triage=triage,
                incident=incident,
            )

        # 4. General request -> triage; escalate only if it couldn't resolve and
        #    the user signalled they want a ticket (§3.1 -> §3.2).
        triage = self._triage.run(text)
        if triage.resolved or not triage.escalate_requested:
            return OrchestratorResponse(
                reply=triage.answer, route=["triage"], triage=triage
            )
        incident = self._incident.create(
            text,
            assignment_group=triage.assignment_group,
            short_description=self._short_desc(text, triage),
        )
        return OrchestratorResponse(
            reply=self._compose_create_reply(triage, incident),
            route=["triage", "incident"],
            triage=triage,
            incident=incident,
        )

    # -- helpers ----------------------------------------------------------
    def _incident_only(self, text: str, action: str) -> OrchestratorResponse:
        incident = (
            self._incident.update(text) if action == "update" else self._incident.lookup(text)
        )
        return OrchestratorResponse(
            reply=incident.message, route=["incident"], incident=incident
        )

    @staticmethod
    def _short_desc(text: str, triage: TriageResult) -> str:
        first = re.split(r"(?<=[.!?])\s+", text)[0].strip()
        return first[:160] if first else text[:160]

    @staticmethod
    def _compose_create_reply(triage: TriageResult, incident: IncidentResult) -> str:
        parts = []
        if triage.citations:
            parts.append(f"(Referenced KB: {'; '.join(triage.citations)})")
        parts.append(incident.message)
        return "\n".join(parts)
