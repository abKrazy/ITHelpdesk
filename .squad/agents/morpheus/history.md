# Project Context

- **Owner:** abKrazy
- **Project:** Azure Solution Accelerator (GitHub) for a ServiceNow ticketing AI agent. Flow: triage the user request against existing knowledge bases → if unresolved, create a ServiceNow incident and assign to the right team → check ticket status → update tickets. Ships as a one-click `azd up` deploy that provisions all Azure infrastructure/resources, the Foundry agents, and a custom Orchestrator agent.
- **Stack:** Azure Developer CLI (`azd`), Bicep (IaC), Azure AI Foundry (agents), custom Orchestrator agent, ServiceNow REST API. App language TBD (likely Python).
- **Created:** 2026-07-08T10:38:03-05:00

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->
- 📌 Team update (2026-07-08T11:18:19-05:00): ServiceNow accelerator baseline is built and green: azd/Bicep scaffold, APIM MCP, Python `helpdesk` agents/UI, and top-level `servicenow.build_client` seam validated by pip install, ruff, pytest, and Bicep build. Architecture remains UI-only azd service plus Foundry-hosted Orchestrator/triage/incident agents.

📌 Team update (2025-06-13T00:00:00Z): Morpheus rejected the final UI deploy contract for three coupled blockers, then re-reviewed Switch's lockout revision and cleared the seams to ship after all blockers and stale comments were fixed. — decided by Morpheus
