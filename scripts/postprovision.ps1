# =============================================================================
# postprovision.ps1 — post-deploy wiring (Windows). Owner: Tank + Trinity.
# =============================================================================
# Thin wrapper: loads azd env values and calls the Python worker that does the
# real work (KB upload + AI Search index + Foundry agent creation).
# -----------------------------------------------------------------------------
$ErrorActionPreference = 'Stop'
Write-Host "Running postprovision..."

# --- Phase 2: build the orchestrator image server-side, then register it -------
# `az acr build` uploads ./src/orchestrator to ACR and builds it there (no local
# Docker daemon needed — ideal for hackathon laptops). The Python worker then
# registers the pushed image as a Foundry Hosted Agent. Skipped in mock mode or
# when the ACR output is absent.
$mock = ($env:HELPDESK_MOCK -match '^(1|true|yes|on)$')
if (-not $mock -and $env:AZURE_CONTAINER_REGISTRY_NAME) {
  $tag = if ($env:AZURE_RESOURCE_TOKEN) { $env:AZURE_RESOURCE_TOKEN } else { 'latest' }
  $imageRef = "it-helpdesk-orchestrator:$tag"
  Write-Host "Building orchestrator image '$imageRef' via ACR '$($env:AZURE_CONTAINER_REGISTRY_NAME)'..."
  # --no-logs: the ACR build-log streamer routes through colorama, which crashes
  # on Windows consoles (cp1252) when pip emits Unicode progress output
  # (UnicodeEncodeError in ansitowin32). --no-logs skips streaming but still waits
  # for the remote build to finish and returns its exit code, so hackathon laptops
  # on Windows don't fail the deploy. Full logs remain in `az acr task logs`.
  az acr build --registry $env:AZURE_CONTAINER_REGISTRY_NAME --image $imageRef --no-logs "$PSScriptRoot/../src/orchestrator"
  if ($LASTEXITCODE -ne 0) {
    Write-Error "az acr build failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
  }
  $loginServer = if ($env:ACR_LOGIN_SERVER) { $env:ACR_LOGIN_SERVER } else { "$($env:AZURE_CONTAINER_REGISTRY_NAME).azurecr.io" }
  $env:ORCHESTRATOR_IMAGE = "$loginServer/$imageRef"
  Write-Host "Orchestrator image: $($env:ORCHESTRATOR_IMAGE)"
}

# azd exports outputs as env vars into this process; the Python worker reads them.
$venvPython = (Resolve-Path (Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe')).Path
& $venvPython "$PSScriptRoot/postprovision.py"
if ($LASTEXITCODE -ne 0) {
  Write-Error "postprovision.py failed with exit code $LASTEXITCODE"
  exit $LASTEXITCODE
}
