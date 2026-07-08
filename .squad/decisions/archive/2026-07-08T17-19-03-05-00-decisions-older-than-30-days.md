# Archived Squad Decisions

### 2025-06-13T00:00:00Z: Final README verification completed; RBAC wording corrected by coordinator
**By:** Fact Checker, coordinator
**What:** Final README deploy-guide claims were verified against Bicep, azure.yaml, hooks, and Python metadata. No contradicted claims were found. The one RBAC wording advisory was resolved directly by the coordinator: deploying users get Azure AI Developer and Cognitive Services OpenAI User only, not Cognitive Services User.
**References:** fact-checker-final-review.md; README.md; infra/modules/foundry.bicep
**Why:** Keeps customer-facing hackathon prerequisites aligned with the deployed RBAC contract and records the coordinator-owned README wording fix because no Dozer follow-up inbox file was written.

### 2025-06-13T00:00:00Z: RAI final review yellow; prompt-hardening advisory accepted
**By:** Rai, Trinity
**What:** Rai found no critical Responsible AI blockers and cleared ship with advisory recommendations for prompt-injection boundaries and side-effect confirmation. Trinity implemented the advisory in `src/helpdesk/agents/prompts.py` only.
**References:** rai-final-review.md; trinity-prompt-hardening.md
**Why:** The accelerator can create/update ServiceNow tickets, so live Foundry prompts now explicitly treat user/KB content as untrusted data and require confirmation for create/update unless the current turn already explicitly requested the exact action.

### 2025-06-13T00:00:00Z: UI deploy contract rejected, reassigned under Reviewer lockout, then cleared
**By:** coordinator, Morpheus, Switch
**What:** Morpheus rejected the final seam review due to three coupled UI deploy blockers: `azure.yaml` pointed at missing `./src/ui`, App Service started `app:app` with undeclared gunicorn, and the deploy root lacked a complete live dependency manifest. Under Reviewer lockout, coordinator reassigned the atomic revision to Switch, who fixed all three blockers plus stale layout comments. Morpheus re-reviewed and cleared the seams to ship.
**References:** coordinator-ui-deploy-contract-rejected-by-morpheus-revision-r.md; morpheus-final-review.md; switch-ui-deploy-fix.md; morpheus-rereview.md
**Why:** The original authors were locked out of revising their rejected work, and the deploy path needed one coordinated fix spanning azure.yaml, App Service startup, and packaging. Validation passed: live-import smoke test, 48 mock pytests, ruff, and Bicep build.

### 2025-06-13T00:00:00Z: QA hardening and hackathon README completed
**By:** Dozer
**What:** Dozer expanded offline mock-mode coverage from 12 to 48 tests, added OpenAPI, UI, KB, and orchestrator flow coverage, rewrote test documentation, and replaced the README stub with a hackathon-grade deploy guide grounded in the implementation.
**References:** dozer-qa-hardening-and-readme.md
**Why:** The solution accelerator now has broad offline validation for the sample prompts and customer-facing deployment instructions before final review and ship readiness.
