"""Specialist Foundry agents (triage + incident) and their provisioning.

Public surface used elsewhere:
  * ``from helpdesk.agents.setup import build_search_index, create_foundry_agents``
    (called by scripts/postprovision.py — Tank owns that file).
  * ``TriageAgent`` / ``IncidentAgent`` — the composable agent logic the
    Orchestrator hands off to (usable in mock mode without live Azure).
"""

from .triage import TriageAgent, TriageResult
from .incident import IncidentAgent, IncidentResult

__all__ = [
    "TriageAgent",
    "TriageResult",
    "IncidentAgent",
    "IncidentResult",
]
