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

var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: acrName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
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

output name string = registry.name
output loginServer string = registry.properties.loginServer
output resourceId string = registry.id
