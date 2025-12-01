// Azure Container Apps deployment for RUSH Policy RAG Frontend
// Deploy with: az deployment group create --resource-group <rg> --template-file azure-container-app-frontend.bicep

@description('Environment name (dev, staging, production)')
param environment string = 'production'

@description('Location for all resources')
param location string = resourceGroup().location

@description('Container image to deploy')
param containerImage string

@description('Container Registry credentials')
@secure()
param registryPassword string

@description('Backend API URL')
param backendUrl string

@description('Application Insights connection string')
@secure()
param appInsightsConnectionString string = ''

@description('Minimum number of replicas')
param minReplicas int = environment == 'production' ? 2 : 1

@description('Maximum number of replicas')
param maxReplicas int = environment == 'production' ? 10 : 3

// Reference existing Container Apps Environment (shared with backend)
resource containerAppEnvironment 'Microsoft.App/managedEnvironments@2023-05-01' existing = {
  name: 'rush-policy-env-${environment}'
}

// Frontend Container App
resource containerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'rush-policy-frontend-${environment}'
  location: location
  properties: {
    environmentId: containerAppEnvironment.id
    configuration: {
      secrets: [
        {
          name: 'registry-password'
          value: registryPassword
        }
        {
          name: 'app-insights-connection-string'
          value: appInsightsConnectionString
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
        targetPort: 3000
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      dapr: {
        enabled: false
      }
    }
    template: {
      containers: [
        {
          name: 'rush-policy-frontend'
          image: containerImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'BACKEND_URL'
              value: backendUrl
            }
            {
              // Critical: This is the client-side API URL used by Next.js
              name: 'NEXT_PUBLIC_API_URL'
              value: backendUrl
            }
            {
              name: 'NODE_ENV'
              value: environment == 'production' ? 'production' : 'development'
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              secretRef: 'app-insights-connection-string'
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/'
                port: 3000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 15
              periodSeconds: 10
              timeoutSeconds: 5
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/'
                port: 3000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 5
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
                concurrentRequests: '100'
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
