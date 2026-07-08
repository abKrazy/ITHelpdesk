// =============================================================================
// modules/identity.bicep — STUB (Owner: Tank)
// User-assigned managed identity that the App Service UI and Foundry agents
// run as. RBAC role assignments to Search/Storage/KeyVault/Foundry are wired
// from within the respective modules (they receive managedIdentityPrincipalId).
// Signature LOCKED by main.bicep.
// Prefer AVM: br/public:avm/res/managed-identity/user-assigned-identity
// =============================================================================

@description('Deployment region.')
param location string

@description('Tags applied to all resources.')
param tags object

@description('User-assigned managed identity name.')
param managedIdentityName string

resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: managedIdentityName
  location: location
  tags: tags
}

// --- OUTPUTS (contract — do not change signatures) ---------------------------
output name string = managedIdentity.name
output resourceId string = managedIdentity.id
output principalId string = managedIdentity.properties.principalId
output clientId string = managedIdentity.properties.clientId
