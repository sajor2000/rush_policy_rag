# PDF Indexing Guide - Complete Workflow

## Prerequisites

1. **Azure AI Search Index**: Must be recreated with `page_number` field
2. **PDFs Ready**: All policy PDFs in one folder
3. **Virtual Environment**: Backend venv activated

---

## Step 1: Set Up Folder Structure

```bash
cd "$(git rev-parse --show-toplevel)"
chmod +x scripts/setup_pdf_folders.sh
./scripts/setup_pdf_folders.sh
```

**Expected output**:
```
✅ Folder structure created:
  - pdf_staging/01_new_pdfs (place your PDFs here)
  - pdf_staging/02_test_batch (test batch of 30)
  - pdf_staging/03_validated (passed validation)
  - pdf_staging/04_audit_reports (quality reports)
```

---

## Step 2: Place Your PDFs

**Copy ALL your policy PDFs to**:
```
pdf_staging/01_new_pdfs/
```

Example:
```bash
cp /path/to/your/policies/*.pdf pdf_staging/01_new_pdfs/
```

---

## Step 3: Detect Mac Studio Hardware

```bash
python scripts/detect_mac_hardware.py
```

**Expected output**:
```
======================================================================
MAC STUDIO HARDWARE DETECTION
======================================================================

CPU: Apple M1 Max
  Physical Cores: 10
  Logical Cores:  10

Memory: 64.0 GB

======================================================================
RECOMMENDED SETTINGS FOR PDF PROCESSING
======================================================================

✅ Optimal Worker Count: 8
   (Reserves 2 cores for system, ensures 2GB RAM per worker)

💾 Hardware config saved to: hardware_config.json
```

---

## Step 4: Select 30 PDFs for Test Batch

**Manually copy 30 PDFs** from `01_new_pdfs` to `02_test_batch`:

```bash
# Select first 30 PDFs
ls pdf_staging/01_new_pdfs/*.pdf | head -30 | xargs -I {} cp {} pdf_staging/02_test_batch/

# Verify count
ls pdf_staging/02_test_batch/*.pdf | wc -l
# Should show: 30
```

---

## Step 5: Run Quality Audit on Test Batch

```bash
python scripts/audit_quality.py \
  --input pdf_staging/02_test_batch \
  --report pdf_staging/04_audit_reports/test_batch_$(date +%Y%m%d_%H%M%S).json
```

**Expected output**:
```
======================================================================
QUALITY AUDIT - 30 PDFs
======================================================================

[1/30] policy_001.pdf... ✅ 12 chunks, 100.0% pages
[2/30] policy_002.pdf... ✅ 8 chunks, 100.0% pages
...
[30/30] policy_030.pdf... ✅ 15 chunks, 100.0% pages

======================================================================
AUDIT SUMMARY
======================================================================

Total PDFs:          30
Successful:          30
Failed:              0

Quality Metrics:
  Page Extraction:   98.5% (target: ≥90%)
  Reference Numbers: 30/30 (target: 90%+)
  Titles:            30/30 (target: 90%+)
  Avg Chunks/PDF:    10.3

======================================================================
✅ QUALITY GATE: PASSED
   Proceed with full batch indexing
======================================================================
```

### If Quality Gate Fails

Review the detailed report in `pdf_staging/04_audit_reports/`:
```bash
cat pdf_staging/04_audit_reports/test_batch_*.json | jq '.details[] | select(.success == false)'
```

Fix issues before proceeding to Step 6.

---

## Step 6: Recreate Azure AI Search Index

**This deletes existing index and creates new one with `page_number` field**.

```bash
cd apps/backend

# 1. Delete old index
python azure_policy_index.py delete

# 2. Create new index (includes page_number field)
python azure_policy_index.py create
```

**Expected output**:
```
Deleting index rush-policies...
✅ Index deleted

Creating index rush-policies with 36 fields...
✅ Index created with page_number field
```

---

## Step 7: Index Test Batch (30 PDFs)

```bash
python scripts/optimized_batch_ingest.py \
  --input pdf_staging/02_test_batch \
  --upload-to-blob
```

**Expected output**:
```
📤 Uploading 30 PDFs to policies-source...
  [1/30] policy_001.pdf
  ...
✅ Uploaded to policies-source

======================================================================
OPTIMIZED BATCH PROCESSING
======================================================================
  PDFs:    30
  Workers: 8 (parallel processes)
======================================================================

[1/30] ✅ policy_001.pdf: 12 chunks in 2.3s
[2/30] ✅ policy_002.pdf: 8 chunks in 1.8s
...
[30/30] ✅ policy_030.pdf: 15 chunks in 2.1s

======================================================================
PROCESSING SUMMARY
======================================================================
Total PDFs:          30
Successful:          30
Failed:              0
Total Chunks:        309
Avg Time/PDF:        2.1s
Total Time:          18.4s
Throughput:          1.63 PDFs/sec
======================================================================
```

### Verify in Azure Search

```bash
# Check document count
python -c "
from azure_policy_index import PolicySearchIndex
index = PolicySearchIndex()
results = index.search('*', top=1)
print(f'Index contains chunks from ~{len(results)} policies')
"
```

---

## Step 8: Move Validated PDFs

**After successful test batch**:

```bash
mv pdf_staging/02_test_batch/*.pdf pdf_staging/03_validated/
```

---

## Step 9: Full Batch Indexing

**Process ALL remaining PDFs**:

```bash
python scripts/optimized_batch_ingest.py \
  --input pdf_staging/01_new_pdfs \
  --upload-to-blob
```

**For ~50 PDFs, expect**:
- Time: ~60-90 seconds (with 8 workers)
- Throughput: ~0.5-1 PDF/sec

**Monitor progress** in real-time.

---

## Step 10: Final Verification

```bash
# Count total chunks in index
python -c "
from azure_policy_index import PolicySearchIndex
index = PolicySearchIndex()
client = index.get_search_client()
result = client.search('*', include_total_count=True, top=0)
print(f'✅ Total chunks in index: {result.get_count()}')
"

# Test page number field
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "catheter", "top": 3}' | jq '.results[0].page_number'
# Should return a number, not null
```

---

## Troubleshooting

### Error: "No PDF files found"
- Check folder path: `ls pdf_staging/01_new_pdfs/`
- Verify PDFs copied correctly

### Error: "Quality gate failed"
- Review audit report in `04_audit_reports/`
- Check specific failures in JSON report
- Verify PDFs are valid (not corrupted)

### Error: "Azure index not found"
- Ensure Step 6 completed successfully
- Run: `python azure_policy_index.py create`

### Low throughput (<0.3 PDF/sec)
- Check worker count matches hardware
- Ensure venv activated
- Monitor CPU usage: `top -o cpu`

---

## Success Criteria

✅ Test batch: 30/30 PDFs processed successfully
✅ Quality gate passed (page extraction >90%)
✅ Full batch: All PDFs indexed without errors
✅ Page numbers in search results (not null)
✅ Backend health check passes

---

## Quick Reference Commands

```bash
# 1. Setup
chmod +x scripts/setup_pdf_folders.sh && ./scripts/setup_pdf_folders.sh

# 2. Hardware detection
python scripts/detect_mac_hardware.py

# 3. Copy test batch (30 PDFs)
ls pdf_staging/01_new_pdfs/*.pdf | head -30 | xargs -I {} cp {} pdf_staging/02_test_batch/

# 4. Quality audit
python scripts/audit_quality.py --input pdf_staging/02_test_batch --report pdf_staging/04_audit_reports/test_$(date +%Y%m%d_%H%M%S).json

# 5. Recreate index
cd apps/backend && python azure_policy_index.py delete && python azure_policy_index.py create && cd ../..

# 6. Index test batch
python scripts/optimized_batch_ingest.py --input pdf_staging/02_test_batch --upload-to-blob

# 7. Move validated
mv pdf_staging/02_test_batch/*.pdf pdf_staging/03_validated/

# 8. Full batch
python scripts/optimized_batch_ingest.py --input pdf_staging/01_new_pdfs --upload-to-blob

# 9. Verify
curl -X POST http://localhost:8000/api/search -H "Content-Type: application/json" -d '{"query": "catheter", "top": 3}' | jq '.results[0].page_number'
```

---

## Folder Structure Reference

```
<repo-root>/
├── pdf_staging/
│   ├── 01_new_pdfs/        ← START HERE: Place all your PDFs
│   ├── 02_test_batch/      ← Copy 30 PDFs here for testing
│   ├── 03_validated/       ← Move successful test PDFs here
│   └── 04_audit_reports/   ← Quality reports saved here
│
└── hardware_config.json    ← Auto-generated by detect_mac_hardware.py
```

---

## Performance Expectations

| Hardware | Workers | PDFs/sec | Time for 50 PDFs |
|----------|---------|----------|------------------|
| Mac Studio M1 Max (10 cores) | 8 | ~0.8-1.2 | 40-60s |
| Mac Studio M1 Ultra (20 cores) | 12 | ~1.5-2.0 | 25-35s |
| MacBook Pro M1 (8 cores) | 4 | ~0.4-0.6 | 80-120s |

---

## Need Help?

- Check logs: All scripts output detailed progress
- Review audit reports: `pdf_staging/04_audit_reports/*.json`
- Verify hardware config: `cat hardware_config.json | jq`
- Test single PDF: `python scripts/audit_quality.py --input path/to/pdf_folder`

---

**Last Updated**: January 2026
**Version**: 1.0
