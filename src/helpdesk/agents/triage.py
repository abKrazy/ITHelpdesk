"""Triage agent — knowledge-base grounded resolution (data flow §3.1).

The triage agent tries to resolve a user's IT problem from the knowledge base
BEFORE any ticket is created. It is grounded on :mod:`agents.search_client`
(Azure AI Search live, local KB search in mock mode) so the exact same logic runs
offline and online.

Contract used by the Orchestrator:
  * :meth:`TriageAgent.run` -> :class:`TriageResult`.
  * ``TriageResult.resolved`` — True when the KB confidently answers the request.
  * ``TriageResult.assignment_group`` — the "Recommended Assignment Group" parsed
    from the top KB match, used by the Incident agent when escalating (§3.2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .prompts import TRIAGE_INSTRUCTIONS
from .search_client import KbHit, SearchClient, get_search_client

# Below this normalised score we treat the KB as having no confident answer.
_RESOLVE_THRESHOLD = 0.25

# Phrases that signal the user explicitly wants a ticket rather than self-help.
_ESCALATE_RE = re.compile(
    r"\b(create|open|raise|log|file|new)\b.{0,20}\b(incident|ticket|case|request)\b"
    r"|\bescalat",
    re.IGNORECASE,
)


@dataclass
class TriageResult:
    """Outcome of a triage attempt."""

    resolved: bool
    answer: str
    assignment_group: str = ""
    citations: list[str] = field(default_factory=list)
    escalate_requested: bool = False
    top_score: float = 0.0
    hits: list[KbHit] = field(default_factory=list)

    @property
    def has_kb_match(self) -> bool:
        return bool(self.hits)


class TriageAgent:
    """Retrieval-grounded triage. Deterministic; safe to run offline."""

    instructions = TRIAGE_INSTRUCTIONS

    def __init__(self, search_client: SearchClient | None = None) -> None:
        self._search = search_client or get_search_client()

    def run(self, user_message: str) -> TriageResult:
        hits = self._search.search(user_message, top=3)
        escalate = bool(_ESCALATE_RE.search(user_message))

        if not hits:
            return TriageResult(
                resolved=False,
                answer=(
                    "I couldn't find a knowledge-base article for that request. "
                    "I can open a ServiceNow incident if you'd like."
                ),
                escalate_requested=escalate,
            )

        top = hits[0]
        citations = [f"{top.title} ({top.source})"]

        # The user explicitly asked for a ticket: don't claim to have resolved it,
        # but still hand back the assignment group from the matching KB article.
        if escalate:
            return TriageResult(
                resolved=False,
                answer=(
                    f"Based on '{top.title}', this should be handled by "
                    f"{top.assignment_group or 'the appropriate support team'}. "
                    "Creating an incident."
                ),
                assignment_group=top.assignment_group,
                citations=citations,
                escalate_requested=True,
                top_score=top.score,
                hits=hits,
            )

        if top.score >= _RESOLVE_THRESHOLD and top.resolution_steps:
            answer = (
                f"Here's how to resolve this (from '{top.title}'):\n"
                f"{top.resolution_steps}"
            )
            return TriageResult(
                resolved=True,
                answer=answer,
                assignment_group=top.assignment_group,
                citations=citations,
                top_score=top.score,
                hits=hits,
            )

        # A weak KB match: not confident enough to resolve; offer escalation.
        return TriageResult(
            resolved=False,
            answer=(
                f"I found a possibly related article ('{top.title}') but couldn't "
                "confidently resolve your issue. I can open a ServiceNow incident."
            ),
            assignment_group=top.assignment_group,
            citations=citations,
            top_score=top.score,
            hits=hits,
        )
