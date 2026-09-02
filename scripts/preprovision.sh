#!/bin/sh
# =============================================================================
# preprovision.sh — collect ServiceNow inputs before `azd provision` (POSIX)
# Owner: Tank (plumbing). Idempotent: only prompts for values not already set.
# =============================================================================
# Sets: SERVICENOW_INSTANCE_URL, SERVICENOW_USERNAME, SERVICENOW_PASSWORD
# -----------------------------------------------------------------------------
set -e

get_val() { azd env get-value "$1" 2>/dev/null || true; }

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
