# Project Context

- **Owner:** abKrazy
- **Project:** Azure Solution Accelerator (GitHub) for a ServiceNow ticketing AI agent. Flow: triage the user request against existing knowledge bases → if unresolved, create a ServiceNow incident and assign to the right team → check ticket status → update tickets. Ships as a one-click `azd up` deploy that provisions all Azure infrastructure/resources, the Foundry agents, and a custom Orchestrator agent.
- **Stack:** Azure Developer CLI (`azd`), Bicep (IaC), Azure AI Foundry (agents), custom Orchestrator agent, ServiceNow REST API. App language TBD (likely Python).
- **Created:** 2026-07-08T10:38:03-05:00

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->
- 📌 Team update (2026-07-08T11:18:19-05:00): Backend integration is green with the finalized package seam: live ServiceNow MCP client stays importable as top-level `servicenow.build_client(mcp_endpoint)` and implements Trinity's `helpdesk.agents.servicenow_client` protocol; dynamic MCP discovery owns field/enum mapping.

📌 Team update (2025-06-13T00:00:00Z): Under Reviewer lockout, Switch owned the atomic UI deploy contract revision after Morpheus's rejection and fixed the deploy root, App Service start command, deploy-root requirements, and stale layout comments; validation was green and Morpheus cleared the re-review. — decided by coordinator/Morpheus
- 📌 Team update (2026-07-08T17:19:03-05:00): Live APIM MCP path is stable: `apis/tools` deploy serially with `@batchSize(1)`, all six tools are restored, and the ServiceNow client recognizes APIM `TableRecord` write-body schemas for create/update. Final live validation passed for lookup, triage/create, update, and round-trip persistence. — decided by Switch/Coordinator
