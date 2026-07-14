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
# Run the worker inside an isolated venv with PINNED deps so it never depends on
# whatever the deployer happens to have in global site-packages. A drifted global
# azure-search-documents was crashing Foundry setup with "cannot import name
# 'KnowledgeBase'". Mock mode needs no Azure SDKs, so it uses system Python directly.
if ($mock) {
  python "$PSScriptRoot/postprovision.py"
} else {
  $venvDir = Join-Path (Resolve-Path "$PSScriptRoot/..") ".venv-provision"
  $venvPy = Join-Path $venvDir "Scripts/python.exe"
  if (-not (Test-Path $venvPy)) {
    Write-Host "Creating provisioning venv at $venvDir ..."
    python -m venv $venvDir
    if ($LASTEXITCODE -ne 0) { Write-Error "Failed to create provisioning venv"; exit 1 }
  }
  Write-Host "Installing pinned postprovision dependencies (scripts/requirements-postprovision.txt)..."
  & $venvPy -m pip install --disable-pip-version-check --quiet --upgrade pip
  & $venvPy -m pip install --disable-pip-version-check --quiet -r "$PSScriptRoot/requirements-postprovision.txt"
  if ($LASTEXITCODE -ne 0) { Write-Error "Failed to install postprovision dependencies"; exit 1 }
  & $venvPy "$PSScriptRoot/postprovision.py"
}
if ($LASTEXITCODE -ne 0) {
  Write-Error "postprovision.py failed with exit code $LASTEXITCODE"
  exit $LASTEXITCODE
}
