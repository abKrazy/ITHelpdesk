#!/bin/sh
# =============================================================================
# postprovision.sh — post-deploy wiring (POSIX). Owner: Tank + Trinity.
# =============================================================================
# Thin wrapper: azd exports outputs as env vars; the Python worker does the work.
# -----------------------------------------------------------------------------
set -e
echo "Running postprovision..."

set_admin_aad_redirect_uri() {
  if [ -z "${ADMIN_AAD_CLIENT_ID:-}" ] || [ -z "${SERVICE_API_URI:-}" ]; then
    return 0
  fi
  REDIRECT_URI="${SERVICE_API_URI%/}/.auth/login/aad/callback"
  EXISTING_URIS="$(az ad app show --id "$ADMIN_AAD_CLIENT_ID" --query "web.redirectUris" -o tsv 2>/dev/null || true)"
  if printf '%s\n' "$EXISTING_URIS" | grep -Fx "$REDIRECT_URI" >/dev/null 2>&1; then
    echo "API Easy Auth redirect URI ensured: $REDIRECT_URI"
    return 0
  fi
  # shellcheck disable=SC2086 # Azure redirect URIs have no spaces; split into argv intentionally.
  if ! az ad app update --id "$ADMIN_AAD_CLIENT_ID" --web-redirect-uris $EXISTING_URIS "$REDIRECT_URI" >/dev/null 2>&1; then
    echo "WARNING: Could not update the API Easy Auth redirect URI on the Entra app registration. Add '$REDIRECT_URI' manually to app registration $ADMIN_AAD_CLIENT_ID." >&2
    return 0
  fi
  echo "API Easy Auth redirect URI ensured: $REDIRECT_URI"
}

set_admin_aad_redirect_uri

# --- Phase 2: build the orchestrator image server-side, then register it -------
# `az acr build` uploads ./src/orchestrator to ACR and builds it there (no local
# Docker daemon needed). The Python worker then registers the pushed image as a
# Foundry Hosted Agent. Skipped in mock mode or when the ACR output is absent.
case "$(printf '%s' "${HELPDESK_MOCK:-}" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on) MOCK=1 ;;
  *) MOCK=0 ;;
esac
if [ "$MOCK" -eq 0 ] && [ -n "${AZURE_CONTAINER_REGISTRY_NAME:-}" ]; then
  TAG="${AZURE_RESOURCE_TOKEN:-latest}"
  IMAGE_REF="it-helpdesk-orchestrator:$TAG"
  echo "Building orchestrator image '$IMAGE_REF' via ACR '$AZURE_CONTAINER_REGISTRY_NAME'..."
  # --no-logs keeps parity with the Windows hook (where the streamed build log
  # crashes colorama on cp1252 consoles). Still waits for the build and returns
  # its exit code; retrieve full logs later via `az acr task logs` if needed.
  az acr build --registry "$AZURE_CONTAINER_REGISTRY_NAME" --image "$IMAGE_REF" --no-logs "$(dirname "$0")/../src/orchestrator"
  LOGIN_SERVER="${ACR_LOGIN_SERVER:-${AZURE_CONTAINER_REGISTRY_NAME}.azurecr.io}"
  export ORCHESTRATOR_IMAGE="$LOGIN_SERVER/$IMAGE_REF"
  echo "Orchestrator image: $ORCHESTRATOR_IMAGE"
fi

# azd exports outputs as env vars; the Python worker reads them. Run the worker
# inside an isolated venv with PINNED deps so it never depends on whatever the
# deployer has in global site-packages (a drifted global azure-search-documents
# was crashing Foundry setup with "cannot import name 'KnowledgeBase'"). Mock mode
# needs no Azure SDKs, so it uses system Python directly.
SCRIPT_DIR="$(dirname "$0")"
if [ "$MOCK" -eq 1 ]; then
  python "$SCRIPT_DIR/postprovision.py"
else
  REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
  VENV_DIR="$REPO_ROOT/.venv-provision"
  VENV_PY="$VENV_DIR/bin/python"
  if [ ! -x "$VENV_PY" ]; then
    echo "Creating provisioning venv at $VENV_DIR ..."
    python3 -m venv "$VENV_DIR" || python -m venv "$VENV_DIR"
  fi
  echo "Installing pinned postprovision dependencies (scripts/requirements-postprovision.txt)..."
  "$VENV_PY" -m pip install --disable-pip-version-check --quiet --upgrade pip
  "$VENV_PY" -m pip install --disable-pip-version-check --quiet -r "$SCRIPT_DIR/requirements-postprovision.txt"
  "$VENV_PY" "$SCRIPT_DIR/postprovision.py"
fi
