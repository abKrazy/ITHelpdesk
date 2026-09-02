"""Record knowledge gaps without interrupting helpdesk turns.

This module is intentionally best-effort: telemetry must never break a user turn,
even when OpenTelemetry is unavailable or misconfigured. To protect privacy, the
raw question is only attached to spans when explicit content recording is enabled;
otherwise only length and a short hash are emitted.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, replace

SPAN_NAME = "knowledge_gap"

REASON_TRIAGE_UNRESOLVED = "triage_unresolved"
REASON_TRIAGE_NO_CITATIONS = "triage_no_citations"
REASON_INCIDENT_CREATED = "incident_created"
REASON_KB_INSUFFICIENT = "kb_insufficient"

_TRUTHY = {"1", "true", "yes", "on"}
_ATTR_PREFIX = "helpdesk.kb_gap"
_ATTR_REASON = f"{_ATTR_PREFIX}.reason"
_ATTR_HAD_CITATIONS = f"{_ATTR_PREFIX}.had_citations"
_ATTR_QUESTION_LENGTH = f"{_ATTR_PREFIX}.question_length"
_ATTR_QUESTION_HASH = f"{_ATTR_PREFIX}.question_hash"
_ATTR_TOOL = f"{_ATTR_PREFIX}.tool"
_ATTR_QUESTION = f"{_ATTR_PREFIX}.question"


@dataclass(frozen=True)
class KnowledgeGap:
    """Description of a detected gap and whether telemetry was recorded."""

    reason: str
    tool: str | None
    had_citations: bool
    question_length: int
    question_hash: str
    question: str | None
    recorded: bool


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUTHY


def harvest_enabled(environ: dict | None = None) -> bool:
    """Return whether knowledge-gap harvesting is enabled.

    Harvesting defaults on so missing configuration still captures privacy-safe
    gap signals. Explicit values must be truthy to stay enabled.
    """

    env = os.environ if environ is None else environ
    value = env.get("KB_GAP_HARVEST_ENABLED")
    return True if value is None or value == "" else _is_truthy(value)


def record_knowledge_gap(
    question: str,
    reason: str,
    *,
    had_citations: bool = False,
    tool: str | None = None,
    environ: dict | None = None,
    tracer=None,
) -> KnowledgeGap | None:
    """Record a privacy-aware OpenTelemetry span for a knowledge-base gap.

    The raw question is included only when Azure content recording is explicitly
    enabled; otherwise the span receives length plus a deterministic short hash.
    This function is best-effort and catches every exception so observability
    problems never interrupt the helpdesk experience.
    """

    try:
        env = os.environ if environ is None else environ
        stripped_question = question.strip()
        if not stripped_question or not harvest_enabled(env):
            return None

        content_ok = _is_truthy(env.get("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED"))
        question_hash = hashlib.sha256(stripped_question.encode("utf-8")).hexdigest()[:16]
        gap = KnowledgeGap(
            reason=reason,
            tool=tool,
            had_citations=had_citations,
            question_length=len(stripped_question),
            question_hash=question_hash,
            question=stripped_question if content_ok else None,
            recorded=False,
        )

        if tracer is None:
            try:
                from opentelemetry import trace

                tracer = trace.get_tracer("it-helpdesk-knowledge-gap")
            except Exception:
                tracer = None

        if tracer is None:
            return gap

        with tracer.start_as_current_span(SPAN_NAME) as span:
            span.set_attribute("gen_ai.operation.name", SPAN_NAME)
            span.set_attribute(_ATTR_REASON, reason)
            span.set_attribute(_ATTR_HAD_CITATIONS, had_citations)
            span.set_attribute(_ATTR_QUESTION_LENGTH, len(stripped_question))
            span.set_attribute(_ATTR_QUESTION_HASH, question_hash)
            if tool:
                span.set_attribute(_ATTR_TOOL, tool)
            if content_ok:
                span.set_attribute(_ATTR_QUESTION, stripped_question)

        return replace(gap, recorded=True)
    except Exception:
        return None
