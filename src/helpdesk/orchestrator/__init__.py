"""Orchestrator agent — the single entry point the UI talks to.

Routes each user request to a specialist agent (triage / incident) and relays the
result. It never resolves or modifies tickets itself (ARCHITECTURE.md §3).
"""

from .orchestrator import Orchestrator, OrchestratorResponse, TICKET_OFFER_MARKER

__all__ = ["Orchestrator", "OrchestratorResponse", "TICKET_OFFER_MARKER"]
