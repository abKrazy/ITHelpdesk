"""Native Foundry Prompt Agent definitions."""

from __future__ import annotations

from .triage_agent import (
    TRIAGE_INSTRUCTIONS,
    build_triage_definition,
    ensure_kb_knowledge_base,
    kb_mcp_url,
)

__all__ = [
    "TRIAGE_INSTRUCTIONS",
    "build_triage_definition",
    "ensure_kb_knowledge_base",
    "kb_mcp_url",
]
