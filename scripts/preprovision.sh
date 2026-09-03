#!/bin/sh
# =============================================================================
# preprovision.sh — collect ServiceNow inputs before `azd provision` (POSIX)
# Owner: Tank (plumbing). Idempotent: only prompts for values not already set.
# =============================================================================
# Sets: SERVICENOW_INSTANCE_URL, SERVICENOW_USERNAME, SERVICENOW_PASSWORD
# -----------------------------------------------------------------------------
set -e

get_val() { azd env get-value "$1" 2>/dev/null || true; }

try_set_val() {
  if ! azd env set "$1" "$2" >/dev/null 2>&1; then
    echo "WARNING: Could not persist $1 to the azd environment. API Easy Auth will stay disabled until ADMIN_AAD_CLIENT_ID is set manually." >&2
    return 1
  fi
}

ensure_admin_aad_app_registration() {
  EXISTING_CLIENT_ID="$(get_val ADMIN_AAD_CLIENT_ID)"
  if [ -n "$EXISTING_CLIENT_ID" ]; then
    if [ -z "$(get_val ADMIN_AAD_TENANT_ID)" ]; then
      TENANT_ID="$(az account show --query tenantId -o tsv 2>/dev/null || true)"
      [ -n "$TENANT_ID" ] && try_set_val ADMIN_AAD_TENANT_ID "$TENANT_ID" || true
    fi
    return 0
  fi

  TENANT_ID="$(az account show --query tenantId -o tsv 2>/dev/null || true)"
  if [ -z "$TENANT_ID" ]; then
    echo "WARNING: Could not read the current Azure tenant. Skipping API Easy Auth app registration. Set ADMIN_AAD_CLIENT_ID and ADMIN_AAD_TENANT_ID manually, then rerun azd provision." >&2
    return 0
  fi

  DISPLAY_NAME="$(get_val ADMIN_AAD_APP_DISPLAY_NAME)"
  if [ -z "$DISPLAY_NAME" ]; then
    ENV_NAME="$(get_val AZURE_ENV_NAME)"
    [ -z "$ENV_NAME" ] && ENV_NAME="local"
    DISPLAY_NAME="ithelpdesk-admin-$ENV_NAME"
  fi

  ESCAPED_DISPLAY_NAME="$(printf '%s' "$DISPLAY_NAME" | sed "s/'/''/g")"
  CLIENT_ID="$(az ad app list --filter "displayName eq '$ESCAPED_DISPLAY_NAME'" --query "[0].appId" -o tsv 2>/dev/null || true)"
  if [ -z "$CLIENT_ID" ]; then
    CLIENT_ID="$(az ad app create --display-name "$DISPLAY_NAME" --sign-in-audience AzureADMyOrg --query appId -o tsv 2>/dev/null || true)"
    if [ -z "$CLIENT_ID" ]; then
      cat >&2 <<EOF
WARNING: Could not create the Entra app registration '$DISPLAY_NAME'. API Easy Auth will be skipped for this deploy.
Manual fallback:
  1. Ask a tenant admin to create a single-tenant app registration named '$DISPLAY_NAME'.
  2. After infra exists, add this redirect URI to the app: https://<api-app>.azurewebsites.net/.auth/login/aad/callback
  3. Create a client secret and store it in the API app setting MICROSOFT_PROVIDER_AUTHENTICATION_SECRET.
  4. Run:
     azd env set ADMIN_AAD_CLIENT_ID <application-client-id>
     azd env set ADMIN_AAD_TENANT_ID $TENANT_ID
     azd provision
EOF
      return 0
    fi
    echo "Created Entra app registration '$DISPLAY_NAME' for API Easy Auth."
  fi

  try_set_val ADMIN_AAD_CLIENT_ID "$CLIENT_ID" || true
  try_set_val ADMIN_AAD_TENANT_ID "$TENANT_ID" || true
}

if [ -z "$(get_val SERVICENOW_INSTANCE_URL)" ]; then
  INST=""
  while [ -z "$INST" ]; do
    printf "ServiceNow instance URL (e.g. https://<your-instance>.service-now.com): "
    read -r INST
    [ -z "$INST" ] && echo "  A ServiceNow instance URL is required."
  done
  azd env set SERVICENOW_INSTANCE_URL "$INST" >/dev/null 2>&1
  PROMPTED=1
fi

if [ -z "$(get_val SERVICENOW_USERNAME)" ]; then
  printf "ServiceNow username: "
  read -r SNOW_USER
  azd env set SERVICENOW_USERNAME "$SNOW_USER" >/dev/null 2>&1
  PROMPTED=1
fi

if [ -z "$(get_val SERVICENOW_PASSWORD)" ]; then
  printf "ServiceNow password: "
  stty -echo 2>/dev/null || true
  read -r SNOW_PASS
  stty echo 2>/dev/null || true
  echo ""
  azd env set SERVICENOW_PASSWORD "$SNOW_PASS" >/dev/null 2>&1
  PROMPTED=1
fi

# Only emit output when we actually prompted. On re-runs (values already set)
# staying silent avoids a lingering line that azd's live progress UI pins to
# the bottom of the terminal (interactive hooks write outside azd's render
# region).
[ -n "$PROMPTED" ] && echo "ServiceNow inputs captured."

ensure_admin_aad_app_registration
