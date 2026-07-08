# Work Routing

How to decide who handles what.

## Routing Table

| Work Type | Route To | Examples |
|-----------|----------|----------|
| Architecture & solution shape | Morpheus | `azd` project layout, component contracts, how Orchestrator + Foundry agents + infra fit together |
| Infrastructure & deploy | Tank | Bicep modules, `azure.yaml`, resource provisioning, managed identity/RBAC, one-click `azd up` |
| AI agents & orchestration | Trinity | Custom Orchestrator, Foundry agent definitions, triage logic, KB grounding, tool schemas, prompts |
| ServiceNow integration | Switch | Create/assign/check/update incidents, ServiceNow REST client, auth/config, tool implementations |
| Testing | Dozer | Write tests, find edge cases, agent evals, fresh-clone validation |
| Documentation | Dozer | README, deploy walkthrough, configuration & troubleshooting docs |
| Code review | Morpheus | Review PRs, check quality, enforce component boundaries |
| Scope & priorities | Morpheus | What to build next, trade-offs, sequencing decisions |
| Session logging | Scribe | Automatic — never needs routing |
| RAI review | Rai | Content safety, bias checks, credential detection, ethical review |

## Issue Routing

| Label | Action | Who |
|-------|--------|-----|
| `squad` | Triage: analyze issue, assign `squad:{member}` label | Morpheus (Lead) |
| `squad:{name}` | Pick up issue and complete the work | Named member |

### How Issue Assignment Works

1. When a GitHub issue gets the `squad` label, **Morpheus** triages it — analyzing content, assigning the right `squad:{member}` label, and commenting with triage notes.
2. When a `squad:{member}` label is applied, that member picks up the issue in their next session.
3. Members can reassign by removing their label and adding another member's label.
4. The `squad` label is the "inbox" — untriaged issues waiting for Lead review.

## Rules

1. **Eager by default** — spawn all agents who could usefully start work, including anticipatory downstream work.
2. **Scribe always runs** after substantial work, always as `mode: "background"`. Never blocks.
3. **Quick facts → coordinator answers directly.** Don't spawn an agent for "what region does it deploy to?"
4. **When two agents could handle it**, pick the one whose domain is the primary concern. (ServiceNow tool *implementation* → Switch; how the agent *calls* it → Trinity.)
5. **"Team, ..." → fan-out.** Spawn all relevant agents in parallel as `mode: "background"`.
6. **Anticipate downstream work.** If a feature is being built, spawn Dozer to write test cases from requirements simultaneously.
7. **Issue-labeled work** — when a `squad:{member}` label is applied to an issue, route to that member. The Lead handles all `squad` (base label) triage.
