# Project Context

- **Owner:** abKrazy
- **Project:** Azure Solution Accelerator (GitHub) for a ServiceNow ticketing AI agent. Flow: triage the user request against existing knowledge bases → if unresolved, create a ServiceNow incident and assign to the right team → check ticket status → update tickets. Ships as a one-click `azd up` deploy that provisions all Azure infrastructure/resources, the Foundry agents, and a custom Orchestrator agent.
- **Stack:** Azure Developer CLI (`azd`), Bicep (IaC), Azure AI Foundry (agents), custom Orchestrator agent, ServiceNow REST API. App language TBD (likely Python).
- **Created:** 2026-07-08T10:38:03-05:00

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->
- 📌 Team update (2026-07-08T11:18:19-05:00): Infrastructure baseline is green and integrated with the app seams: one resource group, Key Vault-backed ServiceNow secrets into APIM named values, App Service UI, Foundry/search/storage modules, and postprovision Python entry points under the `helpdesk` package.
- 📌 Team update (2026-07-08T17:19:03-05:00): App Service now receives `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`, and `AZURE_OPENAI_CHAT_DEPLOYMENT` from Bicep/live settings, unblocking KB-grounded triage in the deployed app. Final live validation passed. — decided by Tank/Coordinator

📌 Team update (2026-07-08T22:46:07.761-05:00): Foundry IQ KB grounding added infra/modules/kb-connection.bicep and depends on project managed identity Search Index Data Reader RBAC in search-rbac.bicep; keep infra/RBAC paths aligned for future provisioning changes — decided by Trinity.
