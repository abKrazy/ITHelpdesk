# Squad Team

> ITHelpdesk

## Coordinator

| Name | Role | Notes |
|------|------|-------|
| Squad | Coordinator | Routes work, enforces handoffs and reviewer gates. |

## Members

| Name | Role | Charter | Status |
|------|------|---------|--------|
| Morpheus | Lead / Solution Architect | .squad/agents/morpheus/charter.md | 🏗️ Active |
| Tank | Infra / Platform Engineer | .squad/agents/tank/charter.md | ⚙️ Active |
| Trinity | AI / Agent Engineer | .squad/agents/trinity/charter.md | 🤖 Active |
| Switch | Backend / Integration Engineer | .squad/agents/switch/charter.md | 🔧 Active |
| Dozer | QA & Docs | .squad/agents/dozer/charter.md | 🧪 Active |
| Scribe | Session Logger | .squad/agents/scribe/charter.md | 📋 Built-in |
| Ralph | Work Monitor | .squad/agents/ralph/charter.md | 🔄 Built-in |
| Rai | RAI Reviewer | .squad/agents/Rai/charter.md | 🛡️ Built-in |
| Fact Checker | Fact Checker | .squad/agents/fact-checker/charter.md | 🔍 Built-in |


## Coding Agent

<!-- copilot-auto-assign: false -->

| Name | Role | Charter | Status |
|------|------|---------|--------|
| @copilot | Coding Agent | — | 🤖 Coding Agent |

### Capabilities

**🟢 Good fit — auto-route when enabled:**
- Bug fixes with clear reproduction steps
- Test coverage (adding missing tests, fixing flaky tests)
- Lint/format fixes and code style cleanup
- Dependency updates and version bumps
- Small isolated features with clear specs
- Boilerplate/scaffolding generation
- Documentation fixes and README updates

**🟡 Needs review — route to @copilot but flag for squad member PR review:**
- Medium features with clear specs and acceptance criteria
- Refactoring with existing test coverage
- API endpoint additions following established patterns
- Migration scripts with well-defined schemas

**🔴 Not suitable — route to squad member instead:**
- Architecture decisions and system design
- Multi-system integration requiring coordination
- Ambiguous requirements needing clarification
- Security-critical changes (auth, encryption, access control)
- Performance-critical paths requiring benchmarking
- Changes requiring cross-team discussion

## Project Context

- **Owner:** abKrazy
- **Project:** Azure Solution Accelerator (GitHub) for a ServiceNow ticketing AI agent — triage against knowledge bases, create/assign/check/update ServiceNow incidents. One-click `azd up` deploy provisioning Azure infra, Foundry agents, and a custom Orchestrator agent.
- **Stack:** Azure Developer CLI (`azd`), Bicep, Azure AI Foundry, custom Orchestrator agent, ServiceNow REST API. Application language: Python.
- **Created:** 2026-07-08
- **Universe:** The Matrix
