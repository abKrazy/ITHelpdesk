# RAI Audit Trail

> Append-only evidence log. Entries are redacted — never contains raw secrets or harmful content.

<!-- Rai appends findings below -->

## 2025-06-13T00:00:00Z — Final public accelerator RAI review

- Reviewer: Rai
- Requested by: @abKrazy
- Scope: `src/**`, `scripts/**`, `infra/**`, `assets/**`, `README.md`, `azure.yaml`, root `*.json`, `src/helpdesk/agents/prompts.py`.
- Verdict: 🟡 Yellow — ship with advisory recommendations.
- Credential scan: 63 scoped files scanned. No hardcoded password/API key/token/private key/JWT/connection string found. ServiceNow PDI host reference observed; no credential value present. Host fingerprint: `0FA2D7342D71`.
- Credential flow evidence: deploy hooks prompt for ServiceNow values; Bicep marks password secure; Key Vault stores ServiceNow username/password; APIM named values reference Key Vault secrets.
- PII scan: 10 asset files scanned; no email/phone/SSN-like/credit-card-like matches.
- Content scan: 10 README/asset doc files scanned; no harmful or exclusionary term matches.
- Prompt/grounding review: KB-only triage, citation, unresolved fallback, and no invented ticket values confirmed. Advisory recorded for stronger prompt-injection boundary and side-effect confirmation wording.
- Remediation status: No blocker. Advisory documented in `.squad/decisions/inbox/rai-final-review.md`.
