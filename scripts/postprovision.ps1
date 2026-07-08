# =============================================================================
# postprovision.ps1 — post-deploy wiring (Windows). Owner: Tank + Trinity.
# =============================================================================
# Thin wrapper: loads azd env values and calls the Python worker that does the
# real work (KB upload + AI Search index + Foundry agent creation).
# -----------------------------------------------------------------------------
$ErrorActionPreference = 'Stop'
Write-Host "Running postprovision..."
# azd exports outputs as env vars into this process; the Python worker reads them.
python "$PSScriptRoot/postprovision.py"
if ($LASTEXITCODE -ne 0) {
  Write-Error "postprovision.py failed with exit code $LASTEXITCODE"
  exit $LASTEXITCODE
}
