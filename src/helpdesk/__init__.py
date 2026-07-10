"""helpdesk — ServiceNow IT Helpdesk AI agent accelerator.

Single umbrella package so components share one import root and cross-package
imports are unambiguous (avoids collisions with unrelated top-level packages such
as the OpenAI ``agents`` SDK). Subpackages:

  * ``helpdesk.shared``       — config + Azure credential helpers.
  * ``helpdesk.agents``       — triage + incident agents, KB, search, provisioning.
  * ``helpdesk.orchestrator`` — the routing Orchestrator.
  * ``helpdesk.ui``           — FastAPI AG-UI backend (POST /agui).

Switch's ServiceNow/APIM MCP client lives at top-level ``servicenow`` (or
``helpdesk.servicenow``); see ``helpdesk.agents.servicenow_client`` for the seam.
"""
