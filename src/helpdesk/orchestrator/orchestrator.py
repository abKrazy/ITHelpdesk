"""Deterministic orchestrator — the **offline / mock** backend for the UI.

In production the UI talks to the Microsoft Agent Framework **Foundry Hosted
Agent** ``it-helpdesk-orchestrator`` (see ``src/orchestrator/main.py``), whose LLM
decides routing and invokes the Triage + Incident Prompt Agents. This module is
the deterministic stand-in used when ``HELPDESK_MOCK=1`` (CI, local smoke tests,
offline dev) so the UI and the sample-prompt validation run without any live
Azure dependency. It mirrors the hosted orchestrator's routing contract:

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
from typing import Any

from ..agents.incident import IncidentAgent, IncidentResult
from ..agents.prompts import ORCHESTRATOR_INSTRUCTIONS
from ..agents.triage import TriageAgent, TriageResult

_INC_RE = re.compile(r"\bINC\d{4,}\b", re.IGNORECASE)
_CREATE_RE = re.compile(
    r"\b(create|open|raise|log|file)\b.{0,20}\b(incident|ticket|case)\b|\bnew incident\b",
    re.IGNORECASE,
)
_UPDATE_RE = re.compile(r"\b(update|change|set|modify|escalate|lower)\b", re.IGNORECASE)
_CONFIRM_RE = re.compile(
    r"\b(go ahead|yes(?: please)?|yep|yeah|do it|please do|go for it)\b"
    r"|\b(yes|please)\b.{0,20}\b(file|create|open|raise)\b"
    r"|\b(file|create|open|raise)\b.{0,20}\bit\b"
    r"|that didn'?t work|didn'?t help|still (?:not )?working|no luck|not resolved",
    re.IGNORECASE,
)

TICKET_OFFER_MARKER = "reply 'go ahead' and I'll file a ticket"


@dataclass
class OrchestratorResponse:
    """Consolidated result the UI renders."""

    reply: str
    route: list[str] = field(default_factory=list)
    triage: TriageResult | None = None
    incident: IncidentResult | None = None
    servicenow_write_proposal: dict[str, Any] | None = None


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

    def run(
        self,
        user_message: str,
        history: list[dict[str, str] | Any] | None = None,
        *,
        propose_writes: bool = False,
    ) -> OrchestratorResponse:
        text = user_message.strip()
        prior_turns = (history or [])[-10:]
        has_number = bool(_INC_RE.search(text))
        wants_create = bool(_CREATE_RE.search(text))
        wants_update = has_number and bool(_UPDATE_RE.search(text))

        # 1. Existing incident + change request -> incident update (§3.4)
        if wants_update:
            if propose_writes:
                return self._proposal_only(self._update_proposal(text), ["incident"])
            return self._incident_only(text, "update")

        # 2. Existing incident, no change verb -> incident lookup (§3.3)
        if has_number and not wants_create:
            return self._incident_only(text, "lookup")

        # 3. Confirmation of a prior KB deflection offer -> create from the
        #    original problem, not from the short confirmation text.
        original_problem = self._prior_ticket_offer_problem(prior_turns)
        if original_problem and self._is_confirmation(text):
            triage = self._triage.run(original_problem)
            if propose_writes:
                proposal = self._create_proposal(
                    original_problem,
                    triage.assignment_group,
                    short_description=self._short_desc(original_problem, triage),
                )
                return self._proposal_only(proposal, ["triage", "incident"], triage=triage)
            incident = self._incident.create(
                original_problem,
                assignment_group=triage.assignment_group,
                short_description=self._short_desc(original_problem, triage),
            )
            return OrchestratorResponse(
                reply=self._compose_create_reply(triage, incident),
                route=["triage", "incident"],
                triage=triage,
                incident=incident,
            )

        # 4. Explicit "create incident" -> triage first. If the KB confidently
        #    has steps, deflect with those steps and wait for confirmation.
        if wants_create:
            problem_text = self._problem_text_for_triage(text)
            triage = self._triage.run(problem_text)
            if triage.has_confident_resolution:
                return OrchestratorResponse(
                    reply=self._compose_deflection_offer(triage),
                    route=["triage"],
                    triage=triage,
                )
            if propose_writes:
                proposal = self._create_proposal(
                    text,
                    triage.assignment_group,
                    short_description=self._short_desc(problem_text, triage),
                )
                return self._proposal_only(proposal, ["triage", "incident"], triage=triage)
            incident = self._incident.create(
                text,
                assignment_group=triage.assignment_group,
                short_description=self._short_desc(problem_text, triage),
            )
            reply = self._compose_create_reply(triage, incident)
            return OrchestratorResponse(
                reply=reply,
                route=["triage", "incident"],
                triage=triage,
                incident=incident,
            )

        # 5. General request -> triage; escalate only if it couldn't resolve and
        #    the user signalled they want a ticket (§3.1 -> §3.2).
        triage = self._triage.run(text)
        if triage.resolved or not triage.escalate_requested:
            return OrchestratorResponse(
                reply=triage.answer, route=["triage"], triage=triage
            )
        if propose_writes:
            proposal = self._create_proposal(
                text,
                triage.assignment_group,
                short_description=self._short_desc(text, triage),
            )
            return self._proposal_only(proposal, ["triage", "incident"], triage=triage)
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

    def execute_approved_proposal(self, proposal: dict[str, Any]) -> OrchestratorResponse:
        """Execute a human-approved ServiceNow write proposal in mock mode."""
        operation = str(proposal.get("operation") or "").lower()
        if operation == "create":
            description = str(proposal.get("description") or "").strip()
            short_description = str(proposal.get("short_description") or description).strip()
            incident = self._incident.create(
                description or short_description,
                assignment_group=str(proposal.get("assignment_group") or ""),
                short_description=short_description,
            )
            return OrchestratorResponse(
                reply=incident.message, route=["incident"], incident=incident
            )
        if operation == "update":
            incident_number = str(proposal.get("incident_number") or "").strip()
            delta = proposal.get("delta") if isinstance(proposal.get("delta"), dict) else {}
            request = self._update_request_from_delta(incident_number, delta)
            incident = self._incident.update(request)
            return OrchestratorResponse(
                reply=incident.message, route=["incident"], incident=incident
            )
        return OrchestratorResponse(
            reply="I couldn't execute the approved ServiceNow proposal because the operation was unknown.",
            route=["incident"],
        )

    @staticmethod
    def _short_desc(text: str, triage: TriageResult) -> str:
        first = re.split(r"(?<=[.!?])\s+", text)[0].strip()
        return first[:160] if first else text[:160]

    @staticmethod
    def _create_proposal(
        description: str,
        assignment_group: str,
        *,
        short_description: str,
        urgency: str = "2",
    ) -> dict[str, Any]:
        return {
            "operation": "create",
            "short_description": short_description,
            "description": description.strip(),
            "assignment_group": assignment_group,
            "urgency": urgency,
        }

    @staticmethod
    def _update_proposal(text: str) -> dict[str, Any]:
        number_match = _INC_RE.search(text)
        delta: dict[str, str] = {}
        lowered = text.lower()
        if any(word in lowered for word in ("high", "critical", "urgent")):
            delta["urgency"] = "1"
        elif any(word in lowered for word in ("medium", "moderate")):
            delta["urgency"] = "2"
        elif "low" in lowered:
            delta["urgency"] = "3"
        return {
            "operation": "update",
            "incident_number": number_match.group(0).upper() if number_match else "",
            "delta": delta,
            "summary": text.strip(),
        }

    @staticmethod
    def _update_request_from_delta(incident_number: str, delta: dict[str, Any]) -> str:
        if "urgency" in delta:
            labels = {"1": "high", "2": "medium", "3": "low"}
            urgency = labels.get(str(delta["urgency"]), str(delta["urgency"]))
            return f"update urgency for {incident_number} to {urgency}"
        return f"update {incident_number}"

    @staticmethod
    def _proposal_only(
        proposal: dict[str, Any],
        route: list[str],
        *,
        triage: TriageResult | None = None,
    ) -> OrchestratorResponse:
        return OrchestratorResponse(
            reply="ServiceNow write approval is required before I make that change.",
            route=route,
            triage=triage,
            servicenow_write_proposal=proposal,
        )

    @staticmethod
    def _problem_text_for_triage(text: str) -> str:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        non_create = [s for s in sentences if not _CREATE_RE.search(s)]
        if non_create:
            return " ".join(non_create)

        cleaned = re.sub(
            r"\b(?:please\s+)?(?:create|open|raise|log|file)\b.{0,20}"
            r"\b(?:incident|ticket|case)\b\s*(?:for|about|regarding)?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()
        return cleaned or text

    @staticmethod
    def _compose_create_reply(triage: TriageResult, incident: IncidentResult) -> str:
        parts = []
        if triage.citations:
            parts.append(f"(Referenced KB: {'; '.join(triage.citations)})")
        parts.append(incident.message)
        return "\n".join(parts)

    @staticmethod
    def _compose_deflection_offer(triage: TriageResult) -> str:
        top = triage.hits[0]
        assignment_group = triage.assignment_group or "the appropriate support team"
        parts = [
            f"Here's how to resolve this (from '{top.title}'):",
            top.resolution_steps,
        ]
        if triage.citations:
            parts.append(f"(Referenced KB: {'; '.join(triage.citations)})")
        parts.append(
            f"If these steps don't resolve it, {TICKET_OFFER_MARKER} "
            f"(assigned to {assignment_group})."
        )
        return "\n".join(parts)

    @staticmethod
    def _is_confirmation(text: str) -> bool:
        return bool(_CONFIRM_RE.search(text.strip()))

    @staticmethod
    def _turn_role(turn: dict[str, str] | Any) -> str:
        if isinstance(turn, dict):
            return str(turn.get("role", "")).lower()
        return str(getattr(turn, "role", "")).lower()

    @staticmethod
    def _turn_content(turn: dict[str, str] | Any) -> str:
        if isinstance(turn, dict):
            return str(turn.get("content", ""))
        return str(getattr(turn, "content", ""))

    @classmethod
    def _prior_ticket_offer_problem(cls, history: list[dict[str, str] | Any]) -> str:
        offer_index = None
        for index in range(len(history) - 1, -1, -1):
            if cls._turn_role(history[index]) == "assistant" and (
                TICKET_OFFER_MARKER in cls._turn_content(history[index])
            ):
                offer_index = index
                break
        if offer_index is None:
            return ""
        for turn in reversed(history[:offer_index]):
            if cls._turn_role(turn) == "user":
                return cls._turn_content(turn).strip()
        return ""
