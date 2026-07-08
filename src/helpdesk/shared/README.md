# src/shared — Shared Utilities

**Owner:** shared (changes reviewed by Morpheus for boundary creep)

## What goes here
Small, dependency-light helpers used by more than one component:
- Config loading (env → typed settings via pydantic).
- Azure credential helper (`DefaultAzureCredential` with `AZURE_CLIENT_ID`).
- Key Vault secret reader.
- Logging / tracing setup (Application Insights).
- Common constants (env var names — the azd output contract).

## Rules
- No component-specific business logic here (no ServiceNow field mapping, no
  agent prompts, no UI). If it only serves one component, it belongs in that
  component. Keep this module boring and stable — many things import it.
