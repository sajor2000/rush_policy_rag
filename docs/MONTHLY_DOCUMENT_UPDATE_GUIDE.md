# Monthly Document Update Guide

This guide explains what to do when new policy documents arrive and how the system handles duplicate detection.

## Quick Reference

```bash
# 1. Upload new/updated PDFs to staging
az storage blob upload-batch \
  --account-name policytechrush \
  --destination policies-source \
  --source /path/to/new-pdfs/ \
  --overwrite

# 2. Preview changes (dry run)
cd apps/backend
python policy_sync.py detect

# 3. Execute sync
python policy_sync.py sync
```

## How Duplicate Detection Works

The system uses **SHA-256 content hashing** to detect whether a PDF has actually changed.

### The Process

```
┌─────────────────────────────────────────────────────────────┐
│  1. UPLOAD: New PDF uploaded to policies-source             │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  2. HASH: System computes SHA-256 hash of file content      │
│     hash = hashlib.sha256(pdf_bytes).hexdigest()            │
│     Example: "a3f2b8c9d4e5..."                              │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  3. COMPARE: Hash compared against policies-active metadata │
│     - Same hash? → SKIP (no processing needed)              │
│     - Different hash? → PROCESS (content changed)           │
│     - No existing? → NEW DOCUMENT                           │
└─────────────────────────────────────────────────────────────┘
```

### Code Location

The duplicate detection logic is in `apps/backend/policy_sync.py`:

```python
# Line 259-261: Hash computation
def compute_content_hash(self, content: bytes) -> str:
    """Compute SHA-256 hash of document content."""
    return hashlib.sha256(content).hexdigest()

# Line 263-274: Streaming hash for large files
def compute_content_hash_streaming(self, blob_client: BlobClient) -> str:
    """Streaming SHA-256 to avoid memory issues with large PDFs."""
    hash_obj = hashlib.sha256()
    download_stream = blob_client.download_blob()
    for chunk in download_stream.chunks():
        hash_obj.update(chunk)
    return hash_obj.hexdigest()

# Line 288-335: Change detection
def detect_changes(self, source_container, target_container):
    # Compares hashes between source and target
    # Returns: (new_files, changed_files, deleted_files)
```

## What Happens in Each Scenario

### Scenario 1: Exact Same PDF (No Changes)

```
Source: Policy-A.pdf (hash: abc123)
Target: Policy-A.pdf (hash: abc123)  ← Same hash

Result: SKIPPED
- No processing occurs
- No chunks created/deleted
- No embedding API calls
- Instant completion
```

**Why this matters**: If you re-upload 180 documents but only 18 changed, the system only processes those 18. The other 162 are skipped instantly.

### Scenario 2: Modified PDF (Content Changed)

```
Source: Policy-A.pdf (hash: xyz789)  ← New content
Target: Policy-A.pdf (hash: abc123)  ← Old content

Result: VERSION UPGRADE (v1.0 → v2.0)
- Old chunks marked as SUPERSEDED
- New PDF processed into chunks
- New chunks uploaded with v2.0
- Blob metadata updated with new hash
```

### Scenario 3: New PDF (Never Seen Before)

```
Source: New-Policy.pdf (hash: def456)
Target: (does not exist)

Result: NEW DOCUMENT (v1.0)
- PDF processed into chunks
- Chunks uploaded to search index
- PDF copied to policies-active with metadata
```

### Scenario 4: Deleted PDF (Removed from Source)

```
Source: (does not exist)
Target: Old-Policy.pdf (hash: abc123)

Result: RETIRED
- Chunks marked as RETIRED (not deleted)
- PDF moved to policies-archive
- Preserved for audit compliance
```

## Step-by-Step Monthly Process

### Step 1: Prepare Your Updates

Collect all new/updated PDFs in a local folder:

```
/monthly-updates/
├── NewPolicy-January.pdf      (new)
├── UpdatedPolicy-HR.pdf       (modified)
├── SamePolicy-Finance.pdf     (unchanged - will be skipped)
└── AnotherNew.pdf             (new)
```

### Step 2: Upload to Staging Container

```bash
# Upload all PDFs (system will detect which actually changed)
az storage blob upload-batch \
  --account-name policytechrush \
  --destination policies-source \
  --source /path/to/monthly-updates/ \
  --overwrite
```

### Step 3: Preview Changes

```bash
cd apps/backend
python policy_sync.py detect policies-source policies-active
```

Expected output:
```
New: 2
  + NewPolicy-January.pdf
  + AnotherNew.pdf

Changed: 1
  ~ UpdatedPolicy-HR.pdf

Deleted: 0

# Note: SamePolicy-Finance.pdf not listed because hash matches
```

### Step 4: Execute Sync

```bash
python policy_sync.py sync policies-source policies-active
```

Output:
```
============================================================
POLICY SYNC: policies-source → policies-active
============================================================

Processing 2 NEW documents (v1.0)...
  ✓ NewPolicy-January.pdf (12 chunks, v1.0)
  ✓ AnotherNew.pdf (8 chunks, v1.0)

Processing 1 CHANGED documents (version upgrade)...
  ✓ UpdatedPolicy-HR.pdf (v1.0 → v2.0: 15 new, 14 superseded)

============================================================
SYNC REPORT
============================================================
Documents scanned: 3
  New: 2
  Changed: 1
  Unchanged: 1 (SamePolicy-Finance.pdf)
  Deleted: 0

Chunks created: 35
Chunks superseded: 14
```

## Performance Impact of Duplicate Detection

| Scenario | Documents | Hashing Time | Processing Time |
|----------|-----------|--------------|-----------------|
| 180 docs, 0% changed | 180 | ~30 seconds | 0 (all skipped) |
| 180 docs, 10% changed | 18 | ~30 seconds | ~3-5 minutes |
| 180 docs, 100% changed | 180 | ~30 seconds | ~30-60 minutes |

The SHA-256 hashing is very fast (~0.2s per PDF), so checking 180 documents takes about 30 seconds regardless of how many changed.

## Metadata Storage

Each PDF in `policies-active` stores sync metadata in Azure Blob properties:

```json
{
  "content_hash": "a3f2b8c9d4e5f6a7b8c9d0e1f2a3b4c5...",
  "chunk_ids": "[\"chunk-001\", \"chunk-002\", ...]",
  "processed_date": "2024-01-15T10:30:00",
  "reference_number": "POL-2024-001",
  "title": "Patient Safety Protocol",
  "version_number": "2.0",
  "version_sequence": "2",
  "policy_status": "ACTIVE"
}
```

This metadata enables:
- Instant duplicate detection (compare hashes)
- Version tracking (v1 → v2 → v3)
- Audit trail (when processed, by whom)
- Rollback capability (restore previous version)

## Troubleshooting

### Q: A PDF was re-uploaded but shows as "unchanged"

The file content is byte-for-byte identical. Even if the filename or upload date changed, the SHA-256 hash is the same.

To force reprocessing:
```bash
# Option 1: Use full reindex
python policy_sync.py reindex policies-active

# Option 2: Process single file manually
python policy_sync.py process /path/to/file.pdf
```

### Q: How do I check a PDF's current hash?

```bash
# View blob metadata in Azure Portal, or:
az storage blob show \
  --account-name policytechrush \
  --container-name policies-active \
  --name "PolicyName.pdf" \
  --query "metadata.content_hash"
```

### Q: A document should be retired but wasn't detected

Make sure it was **removed** from `policies-source`. The sync only retires documents that exist in `policies-active` but NOT in `policies-source`.

```bash
# Remove from source to trigger retirement
az storage blob delete \
  --account-name policytechrush \
  --container-name policies-source \
  --name "OldPolicy.pdf"

# Then run sync
python policy_sync.py sync
```
