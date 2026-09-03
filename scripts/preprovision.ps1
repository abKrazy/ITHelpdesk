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

function Try-SetAzdEnvValue([string]$key, [string]$value) {
  azd env set $key $value 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Write-Warning "Could not persist $key to the azd environment. API Easy Auth will stay disabled until ADMIN_AAD_CLIENT_ID is set manually."
    return $false
  }
  return $true
}

function Ensure-AdminAadAppRegistration {
  if (Get-AzdEnvValue 'ADMIN_AAD_CLIENT_ID') {
    if (-not (Get-AzdEnvValue 'ADMIN_AAD_TENANT_ID')) {
      $tenant = (az account show --query tenantId -o tsv 2>$null)
      if ($LASTEXITCODE -eq 0 -and $tenant) { Try-SetAzdEnvValue 'ADMIN_AAD_TENANT_ID' $tenant | Out-Null }
    }
    return
  }

  $tenantId = (az account show --query tenantId -o tsv 2>$null)
  if ($LASTEXITCODE -ne 0 -or -not $tenantId) {
    Write-Warning "Could not read the current Azure tenant. Skipping API Easy Auth app registration. Set ADMIN_AAD_CLIENT_ID and ADMIN_AAD_TENANT_ID manually, then rerun azd provision."
    return
  }

  $displayName = Get-AzdEnvValue 'ADMIN_AAD_APP_DISPLAY_NAME'
  if (-not $displayName) {
    $envName = Get-AzdEnvValue 'AZURE_ENV_NAME'
    if (-not $envName) { $envName = 'local' }
    $displayName = "ithelpdesk-admin-$envName"
  }

  $escapedDisplayName = $displayName.Replace("'", "''")
  $filter = "displayName eq '$escapedDisplayName'"
  $clientId = (az ad app list --filter $filter --query "[0].appId" -o tsv 2>$null)
  if ($LASTEXITCODE -ne 0) { $clientId = '' }

  if (-not $clientId) {
    $clientId = (az ad app create --display-name $displayName --sign-in-audience AzureADMyOrg --query appId -o tsv 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $clientId) {
      Write-Warning @"
Could not create the Entra app registration '$displayName'. API Easy Auth will be skipped for this deploy.
Manual fallback:
  1. Ask a tenant admin to create a single-tenant app registration named '$displayName'.
  2. After infra exists, add this redirect URI to the app: https://<api-app>.azurewebsites.net/.auth/login/aad/callback
  3. Create a client secret and store it in the API app setting MICROSOFT_PROVIDER_AUTHENTICATION_SECRET.
  4. Run:
     azd env set ADMIN_AAD_CLIENT_ID <application-client-id>
     azd env set ADMIN_AAD_TENANT_ID $tenantId
     azd provision
"@
      return
    }
    Write-Host "Created Entra app registration '$displayName' for API Easy Auth."
  }

  Try-SetAzdEnvValue 'ADMIN_AAD_CLIENT_ID' $clientId | Out-Null
  Try-SetAzdEnvValue 'ADMIN_AAD_TENANT_ID' $tenantId | Out-Null
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

Ensure-AdminAadAppRegistration
