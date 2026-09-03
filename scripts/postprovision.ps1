# =============================================================================
# postprovision.ps1 — post-deploy wiring (Windows). Owner: Tank + Trinity.
# =============================================================================
# Thin wrapper: loads azd env values and calls the Python worker that does the
# real work (KB upload + AI Search index + Foundry agent creation).
# -----------------------------------------------------------------------------
$ErrorActionPreference = 'Stop'
Write-Host "Running postprovision..."

function Set-AdminAadRedirectUri {
  if (-not $env:ADMIN_AAD_CLIENT_ID -or -not $env:SERVICE_API_URI) { return }
  $redirectUri = "$($env:SERVICE_API_URI.TrimEnd('/'))/.auth/login/aad/callback"
  $existingUris = @(az ad app show --id $env:ADMIN_AAD_CLIENT_ID --query "web.redirectUris" -o tsv 2>$null)
  if ($LASTEXITCODE -ne 0) {
    Write-Warning "Could not read the Entra app registration redirect URIs. Add '$redirectUri' manually to app registration $($env:ADMIN_AAD_CLIENT_ID)."
    return
  }
  if ($existingUris -contains $redirectUri) {
    Write-Host "API Easy Auth redirect URI ensured: $redirectUri"
    return
  }
  $updatedUris = @($existingUris | Where-Object { $_ }) + $redirectUri
  az ad app update --id $env:ADMIN_AAD_CLIENT_ID --web-redirect-uris $updatedUris 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Write-Warning "Could not update the API Easy Auth redirect URI on the Entra app registration. Add '$redirectUri' manually to app registration $($env:ADMIN_AAD_CLIENT_ID)."
    return
  }
  Write-Host "API Easy Auth redirect URI ensured: $redirectUri"
}

function Set-AdminAadIdTokenIssuance {
  # App Service Easy Auth uses the OIDC hybrid flow (response_type=code id_token),
  # which requires the app registration to issue ID tokens. Without this, the
  # /.auth/login/aad/callback returns HTTP 401 after sign-in.
  if (-not $env:ADMIN_AAD_CLIENT_ID) { return }
  az ad app update --id $env:ADMIN_AAD_CLIENT_ID --set web.implicitGrantSettings.enableIdTokenIssuance=true 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Write-Warning "Could not enable ID token issuance on app registration $($env:ADMIN_AAD_CLIENT_ID). In the Azure Portal open the app registration > Authentication > Implicit grant and hybrid flows, check 'ID tokens', and save."
    return
  }
  Write-Host "API Easy Auth ID token issuance ensured (hybrid flow)."
}

function Set-ApiEasyAuthClientSecret {
  if (-not $env:ADMIN_AAD_CLIENT_ID -or -not $env:AZURE_API_APP_SERVICE_NAME -or -not $env:AZURE_RESOURCE_GROUP) { return }
  if ($env:ADMIN_AAD_TENANT_ID) {
    $tenantId = $env:ADMIN_AAD_TENANT_ID
  } else {
    $tenantId = (az account show --query tenantId -o tsv 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $tenantId) {
      Write-Warning "Could not read the current Azure tenant. API Easy Auth client-secret wiring skipped."
      return
    }
  }

  $secretSettingName = 'MICROSOFT_PROVIDER_AUTHENTICATION_SECRET'
  $existingSetting = (az webapp config appsettings list --resource-group $env:AZURE_RESOURCE_GROUP --name $env:AZURE_API_APP_SERVICE_NAME --query "[?name=='$secretSettingName'].name | [0]" -o tsv 2>$null)
  if ($LASTEXITCODE -ne 0) {
    Write-Warning "Could not inspect API app settings. API Easy Auth client-secret wiring skipped."
    return
  }

  if (-not $existingSetting) {
    $secret = (az ad app credential reset --id $env:ADMIN_AAD_CLIENT_ID --append --years 1 --display-name easyauth --query password -o tsv 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $secret) {
      Write-Warning "Could not create an Entra app client secret. API Easy Auth client-secret wiring skipped; run az login --tenant <tenant-id> --scope https://graph.microsoft.com/.default if Graph requires reauthentication."
      return
    }

    az webapp config appsettings set --resource-group $env:AZURE_RESOURCE_GROUP --name $env:AZURE_API_APP_SERVICE_NAME --settings "$secretSettingName=$secret" -o none 2>$null | Out-Null
    $secret = $null
    if ($LASTEXITCODE -ne 0) {
      Write-Warning "Could not store the API Easy Auth client secret in the web app setting '$secretSettingName'."
      return
    }
  }

  $issuer = "https://login.microsoftonline.com/$tenantId/v2.0"
  az webapp auth microsoft update --resource-group $env:AZURE_RESOURCE_GROUP --name $env:AZURE_API_APP_SERVICE_NAME --client-id $env:ADMIN_AAD_CLIENT_ID --client-secret-setting-name $secretSettingName --issuer $issuer --allowed-token-audiences $env:ADMIN_AAD_CLIENT_ID --yes -o none 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Write-Warning "Could not configure API Easy Auth to use the client-secret app setting '$secretSettingName'."
    return
  }
  Write-Host "API Easy Auth client-secret setting ensured."
}

Set-AdminAadRedirectUri
Set-AdminAadIdTokenIssuance
Set-ApiEasyAuthClientSecret

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
