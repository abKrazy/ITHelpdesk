#!/bin/sh
# =============================================================================
# preprovision.sh — collect ServiceNow inputs before `azd provision` (POSIX)
# Owner: Tank (plumbing). Idempotent: only prompts for values not already set.
# =============================================================================
# Sets: SERVICENOW_INSTANCE_URL, SERVICENOW_USERNAME, SERVICENOW_PASSWORD
# -----------------------------------------------------------------------------
set -e

DEFAULT_INSTANCE="https://dev283128.service-now.com"

get_val() { azd env get-value "$1" 2>/dev/null || true; }

if [ -z "$(get_val SERVICENOW_INSTANCE_URL)" ]; then
  printf "ServiceNow instance URL [%s]: " "$DEFAULT_INSTANCE"
  read -r INST
  [ -z "$INST" ] && INST="$DEFAULT_INSTANCE"
  azd env set SERVICENOW_INSTANCE_URL "$INST" >/dev/null
fi

if [ -z "$(get_val SERVICENOW_USERNAME)" ]; then
  printf "ServiceNow username: "
  read -r SNOW_USER
  azd env set SERVICENOW_USERNAME "$SNOW_USER" >/dev/null
fi

if [ -z "$(get_val SERVICENOW_PASSWORD)" ]; then
  printf "ServiceNow password: "
  stty -echo 2>/dev/null || true
  read -r SNOW_PASS
  stty echo 2>/dev/null || true
  echo ""
  azd env set SERVICENOW_PASSWORD "$SNOW_PASS" >/dev/null
fi

echo "ServiceNow inputs captured."
