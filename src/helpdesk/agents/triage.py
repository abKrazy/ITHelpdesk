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

# Below this normalised local score we treat the KB as having no confident answer.
_RESOLVE_THRESHOLD = 0.25
# Azure AI Search semantic reranker scores are on a 0..4 scale and are more
# comparable across live hybrid queries than RRF @search.score values.
_SEMANTIC_RESOLVE_THRESHOLD = 2.0

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

    @property
    def has_confident_resolution(self) -> bool:
        """True when the top KB hit has actionable steps above the confidence bar."""
        return bool(self.hits and self.hits[0].resolution_steps and _is_confident(self.hits[0]))


def _is_confident(hit: KbHit) -> bool:
    if hit.reranker_score is not None:
        return hit.reranker_score >= _SEMANTIC_RESOLVE_THRESHOLD
    return hit.score >= _RESOLVE_THRESHOLD


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

        # The user explicitly asked for a ticket: don't claim the issue is
        # resolved, but still return KB steps/score/assignment for the orchestrator
        # to decide whether to deflect first or create immediately.
        if escalate:
            return TriageResult(
                resolved=False,
                answer=(
                    f"Based on '{top.title}', the recommended assignment group is "
                    f"{top.assignment_group or 'the appropriate support team'}.\n"
                    f"{top.resolution_steps}"
                ),
                assignment_group=top.assignment_group,
                citations=citations,
                escalate_requested=True,
                top_score=top.score,
                hits=hits,
            )

        if _is_confident(top) and top.resolution_steps:
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
