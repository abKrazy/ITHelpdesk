# =============================================================================
# preprovision.ps1 — collect ServiceNow inputs before `azd provision` (Windows)
# Owner: Tank (plumbing). Idempotent: only prompts for values not already set.
# =============================================================================
# Sets these azd environment values (consumed by infra/main.parameters.json):
#   SERVICENOW_INSTANCE_URL  (required — no default; prompts until provided)
#   SERVICENOW_USERNAME
#   SERVICENOW_PASSWORD      (secret — stored in the azd .env; flows to Key Vault)
# -----------------------------------------------------------------------------
$ErrorActionPreference = 'Stop'

function Get-AzdEnvValue([string]$key) {
  $val = (azd env get-value $key 2>$null)
  if ($LASTEXITCODE -ne 0) { return '' }
  return $val
}

function Set-AzdEnvValue([string]$key, [string]$value) {
  # Redirect stderr too: azd prints its "Update available" banner to stderr on
  # every invocation, which otherwise spams the terminal between prompts.
  azd env set $key $value 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Write-Error "azd env set $key failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
  }
}

if (-not (Get-AzdEnvValue 'SERVICENOW_INSTANCE_URL')) {
  do {
    $inst = (Read-Host "ServiceNow instance URL (e.g. https://<your-instance>.service-now.com)").Trim()
    if ([string]::IsNullOrWhiteSpace($inst)) { Write-Host "  A ServiceNow instance URL is required." }
  } while ([string]::IsNullOrWhiteSpace($inst))
  Set-AzdEnvValue 'SERVICENOW_INSTANCE_URL' $inst
  $prompted = $true
}

if (-not (Get-AzdEnvValue 'SERVICENOW_USERNAME')) {
  $user = Read-Host "ServiceNow username"
  Set-AzdEnvValue 'SERVICENOW_USERNAME' $user
  $prompted = $true
}

if (-not (Get-AzdEnvValue 'SERVICENOW_PASSWORD')) {
  $sec = Read-Host "ServiceNow password" -AsSecureString
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
  $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
  Set-AzdEnvValue 'SERVICENOW_PASSWORD' $plain
  $plain = $null
  $prompted = $true
}

# Only emit output when we actually prompted. On re-runs (values already set)
# staying silent avoids a lingering line that azd's live progress UI pins to
# the bottom of the terminal (interactive hooks write outside azd's render
# region).
if ($prompted) { Write-Host "ServiceNow inputs captured." }
