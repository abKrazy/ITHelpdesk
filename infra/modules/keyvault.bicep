// =============================================================================
// modules/keyvault.bicep — STUB (Owner: Tank)
// Key Vault holding ServiceNow credentials. The runtime managed identity is
// granted "Key Vault Secrets User"; the deploying user (principalId) may be
// granted access for local dev. Password is @secure and NEVER output.
// Signature LOCKED by main.bicep.
// Prefer AVM: br/public:avm/res/key-vault/vault
// =============================================================================

@description('Deployment region.')
param location string

@description('Tags applied to all resources.')
param tags object

@description('Key Vault name.')
param keyVaultName string

@description('Principal ID of the runtime managed identity (granted Secrets User).')
param managedIdentityPrincipalId string

@description('Object ID of the deploying user (optional local-dev access).')
param principalId string = ''

@description('ServiceNow username to store as a secret.')
param serviceNowUsername string

@secure()
@description('ServiceNow password to store as a secret. Never output.')
param serviceNowPassword string

// Stable secret names other modules/apps reference.
var serviceNowUsernameSecretName = 'servicenow-username'
var serviceNowPasswordSecretName = 'servicenow-password'

// Role definition IDs (built-in).
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    enablePurgeProtection: null
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
}

resource serviceNowUsernameSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: serviceNowUsernameSecretName
  properties: {
    value: serviceNowUsername
    contentType: 'text/plain'
  }
}

resource serviceNowPasswordSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: serviceNowPasswordSecretName
  properties: {
    value: serviceNowPassword
    contentType: 'text/plain'
  }
}

// Grant the runtime managed identity read access to secrets.
resource miSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, managedIdentityPrincipalId, keyVaultSecretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Optionally grant the deploying user access for local dev.
resource userSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(keyVault.id, principalId, keyVaultSecretsUserRoleId, 'user')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: principalId
  }
}

// --- OUTPUTS (contract — do not change signatures) ---------------------------
output name string = keyVault.name
output endpoint string = keyVault.properties.vaultUri
output serviceNowUsernameSecretName string = serviceNowUsernameSecretName
#disable-next-line outputs-should-not-contain-secrets // secret NAME, not a value
output serviceNowPasswordSecretName string = serviceNowPasswordSecretName
