// =============================================================================
// modules/acr.bicep — Owner: Tank
// Azure Container Registry for the Phase-2 Foundry Hosted Agent image.
// =============================================================================

@description('Deployment region.')
param location string

@description('Tags applied to all resources.')
param tags object

@minLength(5)
@maxLength(50)
@description('Globally unique Azure Container Registry name. Lowercase alphanumeric only.')
param acrName string

@description('Principal ID of the Foundry project managed identity. Granted AcrPull for hosted-agent image pulls.')
param foundryProjectPrincipalId string

@description('Object ID of the deploying user/principal. Granted AcrPush for hosted-agent image pushes when provided.')
param principalId string = ''

var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var acrPushRoleId = '8311e382-0749-4cb8-b61a-304f252e45ec'

resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: acrName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    policies: {
      azureADAuthenticationAsArmPolicy: {
        status: 'enabled'
      }
    }
    publicNetworkAccess: 'Enabled'
  }
}

resource foundryProjectAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, foundryProjectPrincipalId, acrPullRoleId)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: foundryProjectPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource deployerAcrPush 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(registry.id, principalId, acrPushRoleId, 'deployer')
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPushRoleId)
    principalId: principalId
  }
}

output name string = registry.name
output loginServer string = registry.properties.loginServer
output resourceId string = registry.id
