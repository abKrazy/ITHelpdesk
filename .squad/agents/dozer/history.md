# Project Context

- **Owner:** abKrazy
- **Project:** Azure Solution Accelerator (GitHub) for a ServiceNow ticketing AI agent. Flow: triage the user request against existing knowledge bases → if unresolved, create a ServiceNow incident and assign to the right team → check ticket status → update tickets. Ships as a one-click `azd up` deploy that provisions all Azure infrastructure/resources, the Foundry agents, and a custom Orchestrator agent.
- **Stack:** Azure Developer CLI (`azd`), Bicep (IaC), Azure AI Foundry (agents), custom Orchestrator agent, ServiceNow REST API. App language TBD (likely Python).
- **Created:** 2026-07-08T10:38:03-05:00

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->
- 📌 Team update (2026-07-08T11:18:19-05:00): QA/docs can build from a green baseline: pip install -e .[all], ruff, pytest, ServiceNow client tests, and Bicep build are passing. Coverage should target all four capabilities, mock/live edges, `helpdesk` package imports, top-level `servicenow.build_client`, and hackathon README prerequisites.

📌 Team update (2026-07-08T22:46:07.761-05:00): Foundry IQ KB grounding is covered by rewritten test_foundry_agents_setup.py and new test_triage_agent_definition.py; latest validation was 79 pytest tests plus ruff, bicep build, and secret scan — decided by Trinity.

