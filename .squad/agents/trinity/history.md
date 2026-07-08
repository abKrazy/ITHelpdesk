# Project Context

- **Owner:** abKrazy
- **Project:** Azure Solution Accelerator (GitHub) for a ServiceNow ticketing AI agent. Flow: triage the user request against existing knowledge bases → if unresolved, create a ServiceNow incident and assign to the right team → check ticket status → update tickets. Ships as a one-click `azd up` deploy that provisions all Azure infrastructure/resources, the Foundry agents, and a custom Orchestrator agent.
- **Stack:** Azure Developer CLI (`azd`), Bicep (IaC), Azure AI Foundry (agents), custom Orchestrator agent, ServiceNow REST API. App language TBD (likely Python).
- **Created:** 2026-07-08T10:38:03-05:00

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->
- 📌 Team update (2026-07-08T11:18:19-05:00): AI/app baseline is green with `helpdesk` as the first-party umbrella package and `servicenow.build_client` as the live MCP seam. Orchestrator, triage, incident, FastAPI UI, setup, and postprovision wiring validate under pip install, ruff, pytest, and Bicep build.

📌 Team update (2025-06-13T00:00:00Z): Rai's yellow RAI advisory was accepted; Trinity hardened live LLM prompts in `src/helpdesk/agents/prompts.py` for untrusted user/KB content and create/update confirmation while keeping mock tests green. — decided by Rai/Trinity
