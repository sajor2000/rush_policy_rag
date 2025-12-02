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

// Cohere Rerank parameters (required for healthcare RAG)
@secure()
@description('Cohere Rerank endpoint URL')
param cohereRerankerEndpoint string = ''

@secure()
@description('Cohere Rerank API key')
param cohereRerankerApiKey string = ''

@description('Enable Cohere Rerank cross-encoder')
param useCohereRerank bool = true

@description('Cohere Rerank model name')
param cohereRerankerModel string = 'cohere-rerank-v3-5'

@description('Number of documents to retain after reranking')
param cohereRerankerTopN int = 10

@description('Minimum relevance score threshold')
param cohereRerankerMinScore string = '0.15'

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
    // Note: For Application Insights integration, configure APPLICATIONINSIGHTS_CONNECTION_STRING
    // on each container app instead of at the environment level
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
        {
          name: 'cohere-endpoint'
          value: cohereRerankerEndpoint
        }
        {
          name: 'cohere-api-key'
          value: cohereRerankerApiKey
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
          // CORS origins for frontend container app
          // Azure Container Apps FQDN format: {app-name}.{region}.azurecontainerapps.io
          allowedOrigins: union(
            [
              'https://rush-policy-frontend-${environment}.${location}.azurecontainerapps.io'
              'https://rush-policy-frontend.${location}.azurecontainerapps.io'
              // Development/testing origins
              'http://localhost:3000'
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
              name: 'SEARCH_INDEX_NAME'
              value: 'rush-policies'
            }
            {
              name: 'SEARCH_SEMANTIC_CONFIG'
              value: 'default-semantic'
            }
            {
              name: 'AOAI_ENDPOINT'
              value: aoaiEndpoint
            }
            {
              name: 'AOAI_API_KEY'
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
            {
              name: 'LOG_FORMAT'
              value: 'json'
            }
            {
              name: 'USE_COHERE_RERANK'
              value: string(useCohereRerank)
            }
            {
              name: 'COHERE_RERANK_ENDPOINT'
              secretRef: 'cohere-endpoint'
            }
            {
              name: 'COHERE_RERANK_API_KEY'
              secretRef: 'cohere-api-key'
            }
            {
              name: 'COHERE_RERANK_MODEL'
              value: cohereRerankerModel
            }
            {
              name: 'COHERE_RERANK_TOP_N'
              value: string(cohereRerankerTopN)
            }
            {
              name: 'COHERE_RERANK_MIN_SCORE'
              value: cohereRerankerMinScore
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
