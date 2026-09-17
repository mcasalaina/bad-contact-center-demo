targetScope = 'resourceGroup'

@description('Azure region for the Container App.')
param location string = resourceGroup().location

@description('Existing Container Apps managed environment name.')
param managedEnvironmentName string

@description('Existing Azure Container Registry name.')
param registryName string

@description('Existing Azure AI account name that provides Voice Live.')
param voiceLiveAccountName string

@description('Immutable container image tag built in the existing registry.')
param imageTag string

@description('Deploy the Container App after managed-identity roles have propagated.')
param deployContainerApp bool = true

@description('Voice Live model used when a client omits a model selection.')
param voiceLiveModel string = 'gpt-realtime-2.1'

@description('Voice Live voice used when a client omits a voice selection.')
param voiceLiveVoice string = 'coral'

var appName = 'lyrenza-hotel-dallas'
var identityName = '${appName}-id'
var imageName = 'bad-contact-center-demo'

resource managedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: managedEnvironmentName
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}

resource voiceLiveAccount 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: voiceLiveAccountName
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

resource acrPullRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  scope: subscription()
  name: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
}

resource cognitiveServicesUserRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  scope: subscription()
  name: 'a97b65f3-24c7-4388-baec-2e87135dc908'
}

resource foundryUserRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  scope: subscription()
  name: '53ca6127-db72-4b80-b1b0-d745d6d5456d'
}

resource identityAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, acrPullRole.id)
  scope: registry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRole.id
  }
}

resource identityVoiceLiveUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(voiceLiveAccount.id, identity.id, cognitiveServicesUserRole.id)
  scope: voiceLiveAccount
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: cognitiveServicesUserRole.id
  }
}

resource identityFoundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(voiceLiveAccount.id, identity.id, foundryUserRole.id)
  scope: voiceLiveAccount
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: foundryUserRole.id
  }
}

resource web 'Microsoft.App/containerApps@2024-03-01' = if (deployContainerApp) {
  name: appName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: managedEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'web'
          image: '${registry.properties.loginServer}/${imageName}:${imageTag}'
          env: [
            {
              name: 'AZURE_CLIENT_ID'
              value: identity.properties.clientId
            }
            {
              name: 'AZURE_VOICELIVE_ENDPOINT'
              value: 'https://${voiceLiveAccount.name}.services.ai.azure.com/'
            }
            {
              name: 'AZURE_VOICELIVE_MODEL'
              value: voiceLiveModel
            }
            {
              name: 'AZURE_VOICELIVE_VOICE'
              value: voiceLiveVoice
            }
            {
              name: 'ALLOWED_TENANT_IDS'
              value: ''
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8080
                scheme: 'HTTP'
              }
              initialDelaySeconds: 10
              periodSeconds: 20
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health'
                port: 8080
                scheme: 'HTTP'
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
      }
    }
  }
  dependsOn: [
    identityAcrPull
    identityVoiceLiveUser
    identityFoundryUser
  ]
}

output containerAppName string = deployContainerApp ? web.name : ''
output containerAppUrl string = deployContainerApp ? 'https://${web!.properties.configuration.ingress.fqdn}' : ''
output identityClientId string = identity.properties.clientId
output identityPrincipalId string = identity.properties.principalId
