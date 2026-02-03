# Architecture Diagrams

Visual diagrams of the RUSH Policy RAG Agent architecture.

## 1. High-Level System Architecture

```mermaid
flowchart TB
    subgraph users [Users]
        Browser[Web Browser]
    end

    subgraph azure_apps [Azure Container Apps]
        Frontend[Next.js Frontend<br/>:3000]
        Backend[FastAPI Backend<br/>:8000]
    end

    subgraph azure_ai [Azure AI Services]
        Search[Azure AI Search<br/>rush-policies index]
        OpenAI[Azure OpenAI<br/>GPT-4.1]
        Cohere[Cohere Rerank 4.0<br/>Azure AI Foundry]
    end

    subgraph azure_storage [Azure Storage]
        Source[policies-source<br/>Staging]
        Active[policies-active<br/>Production]
        Archive[policies-archive<br/>Deleted]
    end

    Browser --> Frontend
    Frontend --> Backend
    Backend --> Search
    Backend --> OpenAI
    Backend --> Cohere
    Backend --> Active
    Source --> Active
    Active --> Archive
```

## 2. Document Ingestion Pipeline

```mermaid
flowchart LR
    subgraph input [Input]
        PDF[PDF Files]
    end

    subgraph blob [Azure Blob Storage]
        Source[policies-source]
    end

    subgraph processing [Processing]
        PyMuPDF[PyMuPDF<br/>Checkbox Extraction]
        Docling[Docling<br/>TableFormer ACCURATE]
        Chunker[PolicyChunker<br/>1500 char chunks]
        Metadata[Metadata Extractor<br/>Title, Ref#, Date]
    end

    subgraph embedding [Embedding]
        Embed[text-embedding-3-large<br/>3072 dimensions]
    end

    subgraph index [Azure AI Search]
        Index[rush-policies<br/>29-field schema]
    end

    subgraph prod [Production]
        Active[policies-active]
    end

    PDF --> Source
    Source --> PyMuPDF
    Source --> Docling
    PyMuPDF --> Chunker
    Docling --> Chunker
    Chunker --> Metadata
    Metadata --> Embed
    Embed --> Index
    Source -.->|sync| Active
```

### Ingestion Steps

1. **Upload**: PDFs uploaded to `policies-source` blob container
2. **Extract**: PyMuPDF extracts checkboxes, Docling extracts content
3. **Chunk**: PolicyChunker creates ~1500 char hierarchical chunks
4. **Metadata**: Extract title, reference number, applies-to entities
5. **Embed**: Generate 3072-dim vectors with text-embedding-3-large
6. **Index**: Upload to Azure AI Search with 29-field schema
7. **Sync**: Copy processed PDFs to `policies-active` for viewing

## 3. Query Pipeline (RAG)

```mermaid
flowchart TB
    subgraph input [User Input]
        Query[User Query]
    end

    subgraph expansion [Query Expansion]
        Synonyms[SynonymService<br/>132 healthcare rules]
    end

    subgraph retrieval [Retrieval - Azure AI Search]
        Vector[Vector Search<br/>text-embedding-3-large]
        BM25[BM25 + Synonyms<br/>Keyword Match]
        L2[L2 Semantic Ranker<br/>First-pass rerank]
        Hybrid{vectorSemanticHybrid}
    end

    subgraph rerank [Reranking - Cohere]
        CohereRerank[Cohere Rerank 4.0 Pro<br/>Cross-encoder]
        Filter[Score Filter<br/>min 0.40]
        TopN[Top 5 Results]
    end

    subgraph context [Context Expansion]
        Siblings[Sibling Chunks<br/>chunk_index ± 1]
    end

    subgraph generation [Generation - Azure OpenAI]
        Prompt[RISEN Prompt<br/>policytech_prompt.txt]
        GPT[GPT-4.1<br/>Chat Completions]
    end

    subgraph output [Response]
        Answer[Answer + Citations]
    end

    Query --> Synonyms
    Synonyms --> Vector
    Synonyms --> BM25
    Vector --> Hybrid
    BM25 --> Hybrid
    Hybrid --> L2
    L2 -->|100 candidates| CohereRerank
    CohereRerank --> Filter
    Filter --> TopN
    TopN --> Siblings
    Siblings --> Prompt
    Prompt --> GPT
    GPT --> Answer
```

### Query Steps

1. **Expand**: Add synonyms (ED → emergency department)
2. **Search**: Azure AI Search with vectorSemanticHybrid (100 candidates)
3. **Rerank**: Cohere cross-encoder reorders by relevance
4. **Filter**: Remove low-score documents (< 0.40)
5. **Expand Context**: Fetch sibling chunks for procedural completeness
6. **Generate**: GPT-4.1 with RISEN prompt framework
7. **Respond**: Answer with policy citations

## 4. Deployment Architecture

```mermaid
flowchart TB
    subgraph dev [Development]
        LocalBackend[Backend :8000]
        LocalFrontend[Frontend :3000]
    end

    subgraph ci [CI/CD]
        GitHub[GitHub Actions]
        ACR[Azure Container Registry<br/>aiinnovation]
    end

    subgraph prod [Production - Azure Container Apps]
        Env[rush-policy-env-production]
        BackendApp[rush-policy-backend]
        FrontendApp[rush-policy-frontend]
    end

    subgraph dns [Public URLs]
        BackendURL[rush-policy-backend...azurecontainerapps.io]
        FrontendURL[rush-policy-frontend...azurecontainerapps.io]
    end

    LocalBackend -->|docker build| ACR
    LocalFrontend -->|docker build| ACR
    GitHub -->|az acr build| ACR
    ACR -->|az containerapp update| BackendApp
    ACR -->|az containerapp update| FrontendApp
    BackendApp --> Env
    FrontendApp --> Env
    Env --> BackendURL
    Env --> FrontendURL
```

### Deployment Steps

1. **Build**: `docker build --platform linux/amd64`
2. **Push**: `docker push aiinnovation.azurecr.io/...`
3. **Update**: `az containerapp update --image ...`

**Critical**: Always use `--platform linux/amd64` for Azure.

## 5. Service Dependencies

```mermaid
flowchart LR
    subgraph backend [Backend Services]
        ChatService[ChatService<br/>RAG orchestrator]
        OnYourData[OnYourDataService<br/>Azure OpenAI]
        CohereService[CohereRerankService<br/>Cross-encoder]
        ContextExp[ContextExpander<br/>Sibling chunks]
        SynonymSvc[SynonymService<br/>Query expansion]
        CacheService[CacheService<br/>Response cache]
    end

    subgraph external [External Services]
        AzureSearch[Azure AI Search]
        AzureOpenAI[Azure OpenAI]
        CohereAPI[Cohere API]
        BlobStorage[Azure Blob]
    end

    ChatService --> OnYourData
    ChatService --> CohereService
    ChatService --> ContextExp
    ChatService --> SynonymSvc
    ChatService --> CacheService
    OnYourData --> AzureSearch
    OnYourData --> AzureOpenAI
    CohereService --> CohereAPI
    ContextExp --> AzureSearch
    ChatService --> BlobStorage
```

## 6. Data Flow Summary

| Stage | Input | Process | Output |
|-------|-------|---------|--------|
| **Ingestion** | PDF files | Docling + PyMuPDF → Chunking → Embedding | Search index |
| **Query** | User question | Synonym expansion | Expanded query |
| **Retrieval** | Expanded query | vectorSemanticHybrid | 100 candidates |
| **Reranking** | 100 candidates | Cohere cross-encoder | Top 5 docs |
| **Expansion** | Top 5 docs | Sibling chunk fetch | Complete context |
| **Generation** | Context + prompt | GPT-4.1 | Answer + citations |

## Related Documentation

- [RAG_DESIGN.md](RAG_DESIGN.md) - Detailed design decisions
- [QUICK_START.md](QUICK_START.md) - Get started in 30 minutes
- [DEPLOYMENT.md](../DEPLOYMENT.md) - Full deployment guide
- [TECHNICAL_ARCHITECTURE_PWC.md](TECHNICAL_ARCHITECTURE_PWC.md) - Technical review document
