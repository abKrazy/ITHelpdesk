"""Native Foundry Prompt Agent definitions."""

from __future__ import annotations

from .triage_agent import (
    TRIAGE_INSTRUCTIONS,
    build_triage_definition,
    ensure_kb_index,
    ensure_search_connection,
)

__all__ = [
    "TRIAGE_INSTRUCTIONS",
    "build_triage_definition",
    "ensure_kb_index",
    "ensure_search_connection",
]
