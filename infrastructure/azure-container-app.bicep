// Azure Container Apps deployment for RUSH Policy RAG Backend
// Deploy with: az deployment group create --resource-group <rg> --template-file azure-container-app.bicep

@description('Environment name (dev, staging, production)')
param environment string = 'production'

@description('Location for all resources')
param location string = resourceGroup().location

@description('Container image to deploy')
param containerImage string

@description('Container Registry credentials')
@secure()
param registryPassword string

@description('Azure AI Search endpoint')
param searchEndpoint string

@secure()
@description('Azure AI Search API key')
param searchApiKey string

@description('Azure OpenAI endpoint')
param aoaiEndpoint string

@secure()
@description('Azure OpenAI API key')
param aoaiApiKey string

@secure()
@description('Storage connection string')
param storageConnectionString string

@description('Application Insights connection string')
@secure()
param appInsightsConnectionString string

@description('Custom frontend domain (optional, e.g., policy.rush.edu)')
param customFrontendDomain string = ''

@description('Minimum number of replicas')
param minReplicas int = environment == 'production' ? 2 : 1

@description('Maximum number of replicas')
param maxReplicas int = environment == 'production' ? 10 : 3

// Container Apps Environment
resource containerAppEnvironment 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: 'rush-policy-env-${environment}'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'azure-monitor'
    }
    daprAIInstrumentationKey: appInsightsConnectionString
  }
}

// Container App
resource containerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'rush-policy-backend-${environment}'
  location: location
  properties: {
    environmentId: containerAppEnvironment.id
    configuration: {
      secrets: [
        {
          name: 'search-api-key'
          value: searchApiKey
        }
        {
          name: 'aoai-api-key'
          value: aoaiApiKey
        }
        {
          name: 'storage-connection-string'
          value: storageConnectionString
        }
        {
          name: 'app-insights-connection-string'
          value: appInsightsConnectionString
        }
        {
          name: 'registry-password'
          value: registryPassword
        }
      ]
      registries: [
        {
          server: 'ghcr.io'
          username: 'github'
          passwordSecretRef: 'registry-password'
        }
      ]
      ingress: {
        external: true
        targetPort: 8000
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
        corsPolicy: {
          allowedOrigins: union(
            environment == 'production' ? [
              'https://rush-policy-frontend.${containerAppEnvironment.properties.defaultDomain}'
              'https://rush-policy-frontend-production.azurecontainerapps.io'
            ] : [
              'https://rush-policy-frontend-staging.${containerAppEnvironment.properties.defaultDomain}'
              'https://rush-policy-frontend-staging.azurecontainerapps.io'
            ],
            !empty(customFrontendDomain) ? ['https://${customFrontendDomain}'] : []
          )
          allowedMethods: ['GET', 'POST', 'OPTIONS']
          allowedHeaders: ['Content-Type', 'Authorization', 'X-Admin-Key']
          maxAge: 86400
        }
      }
      dapr: {
        enabled: false
      }
    }
    template: {
      containers: [
        {
          name: 'rush-policy-backend'
          image: containerImage
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: [
            {
              name: 'SEARCH_ENDPOINT'
              value: searchEndpoint
            }
            {
              name: 'SEARCH_API_KEY'
              secretRef: 'search-api-key'
            }
            {
              name: 'AOAI_ENDPOINT'
              value: aoaiEndpoint
            }
            {
              name: 'AOAI_API'
              secretRef: 'aoai-api-key'
            }
            {
              name: 'AOAI_CHAT_DEPLOYMENT'
              value: 'gpt-4.1'
            }
            {
              name: 'AOAI_EMBEDDING_DEPLOYMENT'
              value: 'text-embedding-3-large'
            }
            {
              name: 'STORAGE_CONNECTION_STRING'
              secretRef: 'storage-connection-string'
            }
            {
              name: 'CONTAINER_NAME'
              value: 'policies-active'
            }
            {
              name: 'USE_ON_YOUR_DATA'
              value: 'true'
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              secretRef: 'app-insights-connection-string'
            }
            {
              name: 'BACKEND_PORT'
              value: '8000'
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 30
              periodSeconds: 10
              timeoutSeconds: 5
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health'
                port: 8000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 10
              periodSeconds: 5
              timeoutSeconds: 3
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http-scaling'
            http: {
              metadata: {
                concurrentRequests: '50'
              }
            }
          }
          {
            name: 'cpu-scaling'
            custom: {
              type: 'cpu'
              metadata: {
                type: 'Utilization'
                value: '70'
              }
            }
          }
        ]
      }
    }
  }
}

output fqdn string = containerApp.properties.configuration.ingress.fqdn
output containerAppName string = containerApp.name
