# Azure AI Search Index Update Guide

## ⚠️ **Critical: Index Schema Update Required**

The Azure AI Search index `rush-policies` needs to be recreated to include the `page_number` field.

---

## Quick Command Reference

### Step 1: Backup Current Index (Optional but Recommended)

```bash
cd apps/backend

# Export current index statistics
python -c "
from azure_policy_index import PolicySearchIndex
index = PolicySearchIndex()
client = index.get_search_client()

# Count documents
result = client.search('*', include_total_count=True, top=0)
print(f'📊 Current index has {result.get_count()} documents')
"
```

### Step 2: Delete Old Index

```bash
cd apps/backend

python azure_policy_index.py delete
```

**Expected output**:
```
Deleting index rush-policies...
✅ Index deleted successfully
```

### Step 3: Create New Index with Updated Schema

```bash
python azure_policy_index.py create
```

**Expected output**:
```
Creating index rush-policies...
✅ Index created successfully with 36 fields including page_number
```

### Step 4: Re-index All Policies

#### Option A: Fast Pipeline (Recommended)

```bash
python scripts/full_pipeline_ingest.py
```

**Expected**: ~85-113 seconds for 50 policies

#### Option B: Legacy Ingestion

```bash
python scripts/ingest_all_policies.py
```

**Expected**: Slower, but more verbose logging

### Step 5: Verify Page Numbers Are Indexed

```bash
python -c "
from azure_policy_index import PolicySearchIndex

index = PolicySearchIndex()
results = index.search('catheter', top=3)

print('📄 Sample search results:')
for i, r in enumerate(results, 1):
    print(f'{i}. {r.title}')
    print(f'   Reference: {r.reference_number}')
    print(f'   Page: {r.page_number or \"NOT FOUND ❌\"}')
    print(f'   Source: {r.source_file}')
    print()

# Check success rate
with_pages = sum(1 for r in results if r.page_number)
total = len(results)
print(f'✅ Page numbers found: {with_pages}/{total} ({with_pages/total*100:.0f}%)')
"
```

**Expected output**:
```
📄 Sample search results:
1. Central Line Placement and Maintenance
   Reference: 528
   Page: 3
   Source: catheter_policy_528.pdf

2. Catheter Care Protocol
   Reference: 529
   Page: 2
   Source: catheter_care_529.pdf

✅ Page numbers found: 3/3 (100%)
```

---

## Troubleshooting

### Error: "Index does not exist"

This is normal if the index was already deleted. Just proceed to Step 3 (create).

### Error: "Field page_number already exists"

Your index already has the field! No action needed. Test with:

```bash
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "catheter", "top": 1}' | jq '.results[0].page_number'
```

Should return a number (not `null`).

### Error: "Permission denied" or "Unauthorized"

Check your `.env` file has correct `SEARCH_API_KEY`:

```bash
grep SEARCH_API_KEY .env
```

### Low Page Number Extraction Rate

If verification shows <80% page numbers, check:

1. PDFs were processed with latest chunker (includes PyMuPDF fallback)
2. Re-run ingestion: `python scripts/full_pipeline_ingest.py`
3. Check validation: `python3 scripts/validate_metadata_extraction.py`

---

## Index Schema Overview

### Fields Added in Latest Version

| Field | Type | Purpose |
|-------|------|---------|
| `page_number` | Int32 | PDF page for navigation (1-indexed) |
| `category` | String | Policy category (may be null) |
| `subcategory` | String | Policy subcategory (may be null) |
| `regulatory_citations` | String | Referenced regulations |
| `related_policies` | String | Cross-referenced policy IDs |

### Total Schema

- **36 fields** total
- **9 entity boolean filters** (RUMC, RUMG, etc.)
- **6 version control fields** (for policy updates)
- **3072-dimensional vector** for semantic search

---

## Post-Update Verification Checklist

### Backend API Test

```bash
# Start backend
cd apps/backend && python main.py

# Test search endpoint (in another terminal)
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "catheter", "top": 3}' | jq '.results[] | {title, page_number, reference_number}'
```

**Expected**: All results have `page_number` field (not null)

### Frontend Integration Test

1. Start backend: `cd apps/backend && python main.py`
2. Start frontend: `cd apps/frontend && npm run dev`
3. Navigate to search page: `http://localhost:3000`
4. Search for "catheter policy"
5. Click "View PDF" on first result
6. **Verify**: PDF viewer opens to the correct page

### OpenAPI Documentation

1. Navigate to: `http://localhost:8000/docs`
2. Find `/api/search` endpoint
3. Click "Schemas" → "SearchResultItem"
4. **Verify**: Shows 44+ fields including:
   - `page_number` (integer)
   - `category` (string, nullable)
   - `reference_number` (string)
   - All entity boolean fields

---

## Rollback Plan (If Needed)

If something goes wrong:

1. **Stop ingestion**: `Ctrl+C` if running
2. **Delete new index**: `python azure_policy_index.py delete`
3. **Restore from backup**: If you exported data, re-import
4. **Report issue**: Check logs in `apps/backend/main.py`

---

## Estimated Downtime

| Operation | Time |
|-----------|------|
| Delete old index | ~5 seconds |
| Create new index | ~10 seconds |
| Re-index 50 policies | ~2-5 minutes |
| **Total** | **~3-6 minutes** |

---

## Production Deployment Notes

### Before Deploying to Production

1. **Test in development first** (you are here!)
2. **Notify users** of brief search downtime
3. **Schedule during low-traffic window** (if applicable)
4. **Have rollback plan ready**

### During Deployment

1. Put app in maintenance mode (optional)
2. Run index recreation commands above
3. Verify with test queries
4. Remove maintenance mode

### After Deployment

1. Monitor search error rates
2. Check page number extraction success rate
3. Verify PDF navigation works in production UI
4. Update documentation/changelog

---

## Support Commands

### Check Index Statistics

```bash
python -c "
from azure_policy_index import PolicySearchIndex
index = PolicySearchIndex()

# Get field names
fields = index.index_client.get_index('rush-policies').fields
print(f'📊 Index has {len(fields)} fields:')
print([f.name for f in fields])

# Check for page_number
has_page = any(f.name == 'page_number' for f in fields)
print(f'\n✅ page_number field: {\"FOUND\" if has_page else \"MISSING ❌\"}')
"
```

### Re-index Single Policy (For Testing)

```bash
python -c "
from preprocessing.chunker import PolicyChunker
from azure_policy_index import PolicySearchIndex

# Process one PDF
chunker = PolicyChunker()
result = chunker.process_pdf_with_status('apps/backend/data/test_pdfs/catheter_policy_528.pdf')

# Upload to index
index = PolicySearchIndex()
index.upload_chunks(result.chunks)

print(f'✅ Uploaded {len(result.chunks)} chunks from test policy')
"
```

---

## Questions?

If you encounter issues:

1. Check backend logs: `tail -f /tmp/backend_test.log`
2. Review validation results: `python3 scripts/validate_metadata_extraction.py`
3. Test unit tests: `cd apps/backend && pytest tests/test_search_metadata.py -v`
4. Verify environment: `grep -E 'SEARCH_|AOAI_' .env`

---

**Last Updated**: 2026-01-31
**Required Action**: Recreate Azure AI Search index with updated schema
**Estimated Time**: 3-6 minutes
