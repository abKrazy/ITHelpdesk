#!/bin/sh
# =============================================================================
# postprovision.sh — post-deploy wiring (POSIX). Owner: Tank + Trinity.
# =============================================================================
# Thin wrapper: azd exports outputs as env vars; the Python worker does the work.
# -----------------------------------------------------------------------------
set -e
echo "Running postprovision..."

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

"$(dirname "$0")/../.venv/bin/python" "$(dirname "$0")/postprovision.py"
