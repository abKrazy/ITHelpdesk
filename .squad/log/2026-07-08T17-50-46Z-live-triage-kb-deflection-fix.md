# Live triage KB deflection fix — 2026-07-08T17:50:46-05:00

- Trinity fixed live search confidence by using semantic hybrid Azure AI Search and reranker-score gating with score fallback.
- KB chunks now include parent `resolution_steps`, so deflection responses contain actionable steps.
- Coordinator reindexed 34 chunks, deployed the app, and verified live behavior.
- Laptop-slow create-ticket request deflected first; follow-up “go ahead” created ServiceNow `INC0010036` assigned to Desktop Support.
- Epic login sample prompt also deflected first through the Password Reset KB.
