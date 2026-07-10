# src/servicenow — ServiceNow / APIM MCP Integration

**Owner:** Switch (Backend / Integration Engineer)

## What goes here
Everything about talking to ServiceNow **through APIM's MCP endpoint**:
- The MCP client wrapper the incident agent uses.
- Tool definitions/schemas mapped to ServiceNow Table API operations
  (create incident, read incident by number/sys_id, update fields like urgency,
  set assignment group).
- Field/enum mapping helpers (e.g. urgency "low" → ServiceNow value `3`,
  incident number ↔ sys_id resolution, `sysparm_query` construction).
- Config/auth glue (reads Key Vault secret names from env; APIM handles the
  actual Basic auth to ServiceNow).

## Source of truth
- OpenAPI spec: `assets/ServiceNow-OpenAPI-spec.json` (Table API).
- Instance URL: `SERVICENOW_INSTANCE_URL`.
- MCP endpoint: `SERVICENOW_MCP_ENDPOINT` (from APIM).

## Inputs it needs (from azd outputs)
- `SERVICENOW_MCP_ENDPOINT`, `SERVICENOW_INSTANCE_URL`
- `AZURE_KEY_VAULT_NAME`, `SERVICENOW_USERNAME_SECRET_NAME`,
  `SERVICENOW_PASSWORD_SECRET_NAME` (only if any direct-call fallback is needed)
- `AZURE_CLIENT_ID`

## Boundary
Transport + ServiceNow domain mapping only. Agent composition → `src/helpdesk/agents`.
The APIM resource + MCP exposure itself → `infra/modules/apim.bicep`.
