# =============================================================================
# preprovision.ps1 — collect ServiceNow inputs before `azd provision` (Windows)
# Owner: Tank (plumbing). Idempotent: only prompts for values not already set.
# =============================================================================
# Sets these azd environment values (consumed by infra/main.parameters.json):
#   SERVICENOW_INSTANCE_URL  (default: https://dev283128.service-now.com)
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
  azd env set $key $value | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Write-Error "azd env set $key failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
  }
}

$defaultInstance = 'https://dev283128.service-now.com'

if (-not (Get-AzdEnvValue 'SERVICENOW_INSTANCE_URL')) {
  $inst = Read-Host "ServiceNow instance URL [$defaultInstance]"
  if ([string]::IsNullOrWhiteSpace($inst)) { $inst = $defaultInstance }
  Set-AzdEnvValue 'SERVICENOW_INSTANCE_URL' $inst
}

if (-not (Get-AzdEnvValue 'SERVICENOW_USERNAME')) {
  $user = Read-Host "ServiceNow username"
  Set-AzdEnvValue 'SERVICENOW_USERNAME' $user
}

if (-not (Get-AzdEnvValue 'SERVICENOW_PASSWORD')) {
  $sec = Read-Host "ServiceNow password" -AsSecureString
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
  $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
  Set-AzdEnvValue 'SERVICENOW_PASSWORD' $plain
  $plain = $null
}

Write-Host "ServiceNow inputs captured."
