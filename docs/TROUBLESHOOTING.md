# Troubleshooting Guide

> Common issues and solutions for the RUSH Policy RAG system.
>
> Last Updated: 2026-01-11

## Quick Diagnostics

```bash
# Check backend health
curl http://localhost:8000/health | jq

# Check frontend
curl -I http://localhost:3000

# View backend logs
cd apps/backend && tail -f logs/app.log
```

---

## Startup Issues

### Backend Won't Start

#### Missing AOAI_API_KEY

**Error:**
```
openai.OpenAIError: Missing credentials. Please pass one of `api_key`...
```

**Solution:**
```bash
# Check .env file
cat apps/backend/.env | grep AOAI

# Use AOAI_API_KEY not AOAI_API (deprecated)
export AOAI_API_KEY=your-key
```

#### Search Index Not Found

**Error:**
```
azure.core.exceptions.ResourceNotFoundError: The index 'rush-policies' does not exist
```

**Solution:**
1. Verify search endpoint: `echo $SEARCH_ENDPOINT`
2. Check index exists in Azure Portal
3. Run ingestion if index is empty

#### Circular Import Error

**Error:**
```
ImportError: cannot import name 'PolicySearchIndex' from partially initialized module
```

**Solution:**
Check `apps/backend/app/services/__init__.py` for lazy imports. Services should be imported directly from their modules.

#### Port Already in Use

**Error:**
```
ERROR: [Errno 48] Address already in use
```

**Solution:**
```bash
# Find process using port
lsof -i :8000

# Kill it
kill -9 <PID>

# Or use different port
uvicorn main:app --port 8001
```

---

## Frontend Issues

### Backend Connection Failed

**Error:**
```
Error: fetch failed - ECONNREFUSED
```

**Solution:**
1. Ensure backend is running on expected port
2. Check `BACKEND_URL` in frontend `.env.local`
3. Verify CORS allows frontend origin

### Build Errors

**Error:**
```
Type error: Property 'X' does not exist on type 'Y'
```

**Solution:**
```bash
# Clear cache and rebuild
rm -rf .next
npm run build

# Check TypeScript errors
npm run check
```

### PDF Viewer Not Working

**Error:**
```
404 Not Found for PDF
```

**Solution:**
1. PDFs must be uploaded to Azure Blob Storage
2. Check `STORAGE_CONNECTION_STRING` in backend
3. Verify PDF exists in `policies-active` container

---

## Search Issues

### No Results Returned

**Symptoms:** Query returns empty results

**Debug Steps:**
1. Check document count:
   ```bash
   curl http://localhost:8000/health | jq '.search_index.document_count'
   ```
2. If 0, run ingestion pipeline
3. Test direct search:
   ```bash
   curl -X POST http://localhost:8000/api/chat \
     -H "Content-Type: application/json" \
     -d '{"message": "visitor policy"}'
   ```

### Low Relevance Results

**Symptoms:** Results don't match query well

**Solutions:**
1. Enable Cohere Rerank:
   ```bash
   USE_COHERE_RERANK=true
   ```
2. Check synonym expansion is working
3. Verify semantic configuration in Azure AI Search

### Disambiguation Not Triggering

**Symptoms:** "What is the IV policy?" returns results instead of asking for clarification

**Cause:** Query validation runs after cache check

**Solution:** Ensure query validation runs BEFORE cache lookup (fixed in v3.0.0+)

---

## Cohere Rerank Issues

### 401 Unauthorized

**Error:**
```
httpx.HTTPStatusError: 401 Unauthorized
```

**Solution:**
```bash
# Verify API key
echo $COHERE_RERANK_API_KEY

# Check endpoint format (should include full URL)
echo $COHERE_RERANK_ENDPOINT
# Should be: https://your-deployment.models.ai.azure.com
```

### Timeout Errors

**Error:**
```
httpx.ReadTimeout
```

**Solution:**
1. Check Azure AI Foundry deployment status
2. Reduce `COHERE_RERANK_TOP_N` (fewer docs to rerank)
3. Increase timeout in service configuration

---

## Ingestion Issues

### Docling Not Found

**Error:**
```
ModuleNotFoundError: No module named 'docling'
```

**Solution:**
```bash
pip install docling docling-core
```

### Empty Chunks

**Symptoms:** PDFs process but create 0 chunks

**Causes:**
1. PDF is scanned/image-only (OCR not enabled)
2. PDF is password protected
3. PDF uses non-standard encoding

**Debug:**
```bash
python scripts/debug_pdf_structure.py path/to/file.pdf
```

### Missing Metadata

**Symptoms:** Title, reference number not extracted

**Causes:**
1. Header table format doesn't match expected patterns
2. "Applies To" checkboxes use non-standard format

**Debug:**
```bash
python scripts/test_checkbox_extraction.py path/to/file.pdf
```

---

## Azure Service Issues

### Azure Search Connection Failed

**Error:**
```
azure.core.exceptions.ServiceRequestError: Connection failed
```

**Solution:**
1. Verify endpoint URL is correct
2. Check network connectivity
3. Verify API key hasn't expired

### Azure OpenAI Rate Limited

**Error:**
```
openai.RateLimitError: Rate limit exceeded
```

**Solution:**
1. Implement backoff/retry (already in service)
2. Check TPM quota in Azure Portal
3. Consider scaling up deployment

### Blob Storage Access Denied

**Error:**
```
azure.core.exceptions.ClientAuthenticationError: Server failed to authenticate
```

**Solution:**
1. Verify connection string is correct
2. Check SAS token hasn't expired
3. Verify container exists and has correct permissions

---

## Deployment Issues

### Container App Not Starting

**Symptoms:** Revision stuck in "Activating" or "Degraded"

**Debug:**
```bash
# Check logs
az containerapp logs show \
  --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --tail 100

# Check revision status
az containerapp revision list \
  --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --query "[].{name:name, state:properties.runningState}"
```

**Common Causes:**
1. Missing environment variables
2. Health probe failing
3. Container crash loop

### Environment Variables Missing

**Symptoms:** Works locally but fails in Azure

**Solution:**
```bash
# List current env vars
az containerapp show \
  --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --query "properties.template.containers[0].env[].name"

# Add missing variable
az containerapp update \
  --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --set-env-vars "VARIABLE_NAME=value"
```

### Multiple Active Revisions

**Symptoms:** Inconsistent behavior, some requests work, others don't

**Cause:** Traffic split between revisions

**Solution:**
```bash
# List revisions
az containerapp revision list \
  --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --query "[?properties.active].name"

# Deactivate old revision
az containerapp revision deactivate \
  --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --revision <old-revision-name>
```

---

## Performance Issues

### Slow First Query (Cold Start)

**Symptoms:** First query takes 5-10 seconds

**Cause:** Service warmup (Azure connections, model loading)

**Mitigation:**
- Warmup is automatic on startup
- Cache is primed after first query
- Consider keep-alive probes

### Cached Response Not Updating

**Symptoms:** Old responses returned after code change

**Solution:**
1. Cache expires after 24 hours (configurable)
2. Restart backend to clear cache
3. Or disable cache temporarily:
   ```bash
   # In code, cache is skipped for certain response types
   ```

---

## Getting Help

1. **Check logs first**: Most issues have clear error messages
2. **Verify environment**: Compare `.env` with `.env.example`
3. **Test incrementally**: Isolate which service is failing
4. **Check Azure Portal**: Service health, quotas, connectivity

### Useful Commands

```bash
# Backend health with full details
curl http://localhost:8000/health | jq

# Test chat endpoint
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "test query"}' | jq

# Check Azure subscription
az account show

# List Container App revisions
az containerapp revision list --name rush-policy-backend -g RU-A-NonProd-AI-Innovation-RG -o table
```
