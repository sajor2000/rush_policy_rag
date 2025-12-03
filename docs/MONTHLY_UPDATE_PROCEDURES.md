# Monthly Policy Update Procedures

## Overview

This document describes the procedures for processing monthly policy updates to the RUSH Policy RAG vector database. It covers:

1. **New Policies**: Entirely new documents added to the system
2. **Updated Policies**: Version transitions (v1 → v2) for existing policies
3. **Retired Policies**: Documents being removed or superseded
4. **Rollback Procedures**: Recovering from problematic updates

---

## Architecture: 3-Container Versioned System

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     MONTHLY UPDATE WORKFLOW                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  policies-source/  ←── UPLOAD NEW/UPDATED PDFs HERE (Staging)               │
│       │                                                                      │
│       │  1. Admin uploads policy-v2.pdf                                     │
│       │  2. Run: python policy_sync.py sync                                 │
│       ▼                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐            │
│  │  PolicySyncManager.sync_monthly_versioned()                 │            │
│  │                                                              │            │
│  │  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐       │            │
│  │  │ Detect      │ → │ Archive v1  │ → │ Ingest v2   │       │            │
│  │  │ Changes     │   │ (supersede) │   │ (new chunks)│       │            │
│  │  └─────────────┘   └─────────────┘   └─────────────┘       │            │
│  │                                                              │            │
│  └─────────────────────────────────────────────────────────────┘            │
│       │                                                                      │
│       ▼                                                                      │
│  policies-active/  ←── PRODUCTION (auto-synced, versioned metadata)         │
│       │                                                                      │
│       │  Blob metadata contains:                                            │
│       │  - version_number: "2.0"                                            │
│       │  - version_date: "2025-12-01T00:00:00Z"                            │
│       │  - supersedes_version: "1.0"                                        │
│       │  - chunk_ids: ["id1", "id2", ...]                                   │
│       ▼                                                                      │
│  policies-archive/  ←── RETIRED/SUPERSEDED (audit trail)                    │
│       │                                                                      │
│       └── policy-v1.pdf (moved here, not deleted)                           │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Azure AI Search Index Schema (Versioning Fields)

### New Fields for Version Control

| Field | Type | Purpose |
|-------|------|---------|
| `version_number` | String | Policy version (e.g., "1.0", "1.1", "2.0") |
| `version_date` | String | ISO datetime when version was released |
| `effective_date` | String | When policy takes effect (may differ from version_date) |
| `expiration_date` | String | When policy expires (null if no expiration) |
| `policy_status` | String | ACTIVE, SUPERSEDED, RETIRED, DRAFT |
| `superseded_by` | String | Version number that replaced this (e.g., "2.0") |
| `version_sequence` | Int32 | Numeric sequence for sorting (1, 2, 3...) |

### Index Schema Addition

```python
# New filterable fields for versioning
{
    "name": "version_number",
    "type": "Edm.String",
    "searchable": False,
    "filterable": True,
    "sortable": True,
    "retrievable": True
},
{
    "name": "version_date",
    "type": "Edm.String",
    "filterable": True,
    "sortable": True,
    "retrievable": True
},
{
    "name": "effective_date",
    "type": "Edm.String",
    "filterable": True,
    "sortable": True,
    "retrievable": True
},
{
    "name": "policy_status",
    "type": "Edm.String",
    "filterable": True,
    "facetable": True,
    "retrievable": True
},
{
    "name": "superseded_by",
    "type": "Edm.String",
    "filterable": True,
    "retrievable": True
},
{
    "name": "version_sequence",
    "type": "Edm.Int32",
    "filterable": True,
    "sortable": True,
    "retrievable": True
}
```

---

## Procedure 1: Processing NEW Policies

### When to Use
- A completely new policy document is being added to the system
- No previous version exists in the index

### Step-by-Step Process

```bash
# Step 1: Prepare the new policy PDF
# Ensure filename follows convention: policy-name-v1.0.pdf
# Example: patient-safety-fall-prevention-v1.0.pdf

# Step 2: Upload to staging container
az storage blob upload \
  --account-name policytechrush \
  --container-name policies-source \
  --file "patient-safety-fall-prevention-v1.0.pdf" \
  --name "patient-safety-fall-prevention-v1.0.pdf"

# Step 3: Detect changes (dry run)
cd apps/backend
python policy_sync.py detect

# Expected output:
# ┌─────────────────────────────────────────────────┐
# │ Change Detection Results                         │
# ├─────────────────────────────────────────────────┤
# │ NEW:     1 (patient-safety-fall-prevention-v1.0.pdf)
# │ CHANGED: 0                                       │
# │ DELETED: 0                                       │
# │ UNCHANGED: 1800                                  │
# └─────────────────────────────────────────────────┘

# Step 4: Execute sync (version is auto-assigned as 1.0 for new policies)
python policy_sync.py sync

# Expected output:
# Processing NEW: patient-safety-fall-prevention-v1.0.pdf
#   ✓ Extracted metadata: Title="Patient Safety: Fall Prevention", Ref#="PS-2025-001"
#   ✓ Created 15 chunks (version=1.0, status=ACTIVE)
#   ✓ Copied to policies-active with metadata
#   ✓ Total time: 2.3s

# Step 5: Verify in Azure AI Search
python azure_policy_index.py verify "PS-2025-001"

# Note: You can use optional advisory flags for logging purposes:
# python policy_sync.py sync --version "1.0" --effective-date "2025-12-01"
# These flags are informational only - versions are auto-incremented
```

### Metadata Stored

```json
{
  "blob_metadata": {
    "version_number": "1.0",
    "version_date": "2025-11-28T14:30:00Z",
    "effective_date": "2025-12-01",
    "policy_status": "ACTIVE",
    "content_hash": "sha256:abc123...",
    "chunk_ids": ["chunk-001", "chunk-002", "..."],
    "reference_number": "PS-2025-001",
    "title": "Patient Safety: Fall Prevention"
  }
}
```

---

## Procedure 2: Processing UPDATED Policies (v1 → v2 Transition)

### When to Use
- An existing policy is being updated with new content
- Need to preserve the old version for audit trail
- Version number increments (1.0 → 1.1 or 1.0 → 2.0)

### Version Numbering Convention

| Change Type | Old Version | New Version | Example |
|-------------|-------------|-------------|---------|
| Minor update (typos, clarifications) | 1.0 | 1.1 | Grammar fix |
| Major update (content changes) | 1.0 | 2.0 | New procedures |
| Complete rewrite | 1.0 | 2.0 | Full revision |

### Step-by-Step Process

```bash
# Step 1: Prepare the updated policy PDF
# Filename: same as original OR with new version suffix
# Example: npo-policy-v2.0.pdf (updating npo-policy-v1.0.pdf)

# Step 2: Upload to staging container
az storage blob upload \
  --account-name policytechrush \
  --container-name policies-source \
  --file "npo-policy-v2.0.pdf" \
  --name "npo-policy.pdf" \
  --overwrite

# Step 3: Detect changes
python policy_sync.py detect

# Expected output:
# ┌─────────────────────────────────────────────────┐
# │ Change Detection Results                         │
# ├─────────────────────────────────────────────────┤
# │ NEW:     0                                       │
# │ CHANGED: 1 (npo-policy.pdf)                      │
# │   └── Hash changed: abc123 → def456             │
# │   └── Old version: 1.0 | New version: 2.0       │
# │ DELETED: 0                                       │
# │ UNCHANGED: 1799                                  │
# └─────────────────────────────────────────────────┘

# Step 4: Execute sync (archiving happens automatically for changed files)
python policy_sync.py sync

# This performs:
# 1. Detects changed file via content hash comparison
# 2. Mark old v1.0 chunks as SUPERSEDED (not deleted)
# 3. Create new v2.0 chunks with ACTIVE status
# 4. Move old PDF to policies-archive container
# 5. Copy new PDF to policies-active container

# Expected output:
# Processing CHANGED: npo-policy.pdf
#   ✓ Found existing version 1.0 (12 chunks)
#   ✓ Marked 12 v1.0 chunks as SUPERSEDED
#   ✓ Archived npo-policy-v1.0.pdf to policies-archive
#   ✓ Processed new version 2.0
#   ✓ Created 14 chunks (version=2.0, status=ACTIVE)
#   ✓ Copied to policies-active with metadata
#   ✓ Total time: 3.1s

# Step 5: Verify version transition
python azure_policy_index.py list-versions "NPO-2025-001"

# Expected output shows version history for the policy
```

### What Happens to Old Chunks (v1 → v2)

**IMPORTANT: Old chunks are NOT deleted. They are marked as SUPERSEDED.**

```python
# Before update (v1.0 chunks):
{
    "id": "chunk-npo-001",
    "policy_status": "ACTIVE",
    "version_number": "1.0",
    "superseded_by": null
}

# After update (v1.0 chunks become):
{
    "id": "chunk-npo-001",
    "policy_status": "SUPERSEDED",
    "version_number": "1.0",
    "superseded_by": "2.0"  # Points to new version
}

# New v2.0 chunks:
{
    "id": "chunk-npo-101",
    "policy_status": "ACTIVE",
    "version_number": "2.0",
    "superseded_by": null
}
```

### Search Behavior After Update

```python
# Default search: Only returns ACTIVE chunks
search_filter = "policy_status eq 'ACTIVE'"
# → Returns only v2.0 chunks

# Audit search: Returns all versions
search_filter = "reference_number eq 'NPO-2025-001'"
# → Returns both v1.0 (SUPERSEDED) and v2.0 (ACTIVE)

# Historical search: Query specific version
search_filter = "reference_number eq 'NPO-2025-001' and version_number eq '1.0'"
# → Returns only v1.0 chunks (for audit purposes)
```

---

## Procedure 3: Deleting/Retiring Policies

### When to Use
- A policy is being permanently retired
- Policy is superseded by a different policy (not a version update)
- Policy is no longer applicable

### Step-by-Step Process

```bash
# Step 1: Remove from staging container
az storage blob delete \
  --account-name policytechrush \
  --container-name policies-source \
  --name "old-policy-to-retire.pdf"

# Step 2: Detect changes
python policy_sync.py detect

# Expected output:
# ┌─────────────────────────────────────────────────┐
# │ Change Detection Results                         │
# ├─────────────────────────────────────────────────┤
# │ NEW:     0                                       │
# │ CHANGED: 0                                       │
# │ DELETED: 1 (old-policy-to-retire.pdf)            │
# │ UNCHANGED: 1799                                  │
# └─────────────────────────────────────────────────┘

# Step 3: Retire the policy by filename
python policy_sync.py retire "old-policy-to-retire.pdf"

# This performs:
# 1. Mark all chunks for this PDF as RETIRED (not deleted from index)
# 2. Move PDF to policies-archive container
# 3. Record retirement date in metadata

# Expected output:
# Retiring: old-policy-to-retire.pdf
#   ✓ Retired 8 chunks, moved to archive
```

### Retention Policy

| Status | Retention | Search Visibility | Audit Access |
|--------|-----------|-------------------|--------------|
| ACTIVE | Indefinite | Yes (default) | Yes |
| SUPERSEDED | 2 years | No (filtered out) | Yes |
| RETIRED | 2 years | No (filtered out) | Yes |
| DRAFT | 30 days | No (filtered out) | Yes |

---

## Procedure 4: Rollback to Previous Version

### When to Use
- Errors discovered in new policy version
- Need to temporarily revert to previous version
- Compliance issue with new version

### Step-by-Step Process

```bash
# Step 1: Identify the version to rollback TO
python azure_policy_index.py list-versions "NPO-2025-001"

# Output:
# Available versions for NPO-2025-001:
# - 2.0 (ACTIVE) - Effective: 2025-12-15
# - 1.0 (SUPERSEDED) - Effective: 2025-01-01

# Step 2: Execute rollback
python policy_sync.py rollback \
  --reference "NPO-2025-001" \
  --to-version "1.0" \
  --reason "Compliance issue identified in v2.0"

# This performs:
# 1. Mark v2.0 chunks as SUPERSEDED
# 2. Mark v1.0 chunks as ACTIVE
# 3. Update blob metadata
# 4. Log rollback reason

# Expected output:
# Rolling back NPO-2025-001 from v2.0 to v1.0
#   ✓ Marked 14 v2.0 chunks as SUPERSEDED
#   ✓ Marked 12 v1.0 chunks as ACTIVE
#   ✓ Updated blob metadata in policies-active
#   ✓ Logged rollback: "Compliance issue identified in v2.0"
#   ✓ Rollback complete

# Step 3: Verify rollback
python azure_policy_index.py verify "NPO-2025-001"

# Output:
# Policy: NPO Policy (NPO-2025-001)
# Current Version: 1.0 (ACTIVE) - ROLLBACK from 2.0
# Note: Version 2.0 marked SUPERSEDED due to rollback
```

---

## Monthly Update Checklist

### Pre-Update (Day Before)

```markdown
- [ ] Collect all policy PDFs to be updated
- [ ] Verify PDF quality (not scanned, searchable text)
- [ ] Determine version numbers for each update
- [ ] Identify effective dates for each policy
- [ ] Backup current index state: `python azure_policy_index.py backup`
- [ ] Notify stakeholders of planned update window
```

### Update Day

```markdown
- [ ] Upload all PDFs to policies-source container
- [ ] Run detect: `python policy_sync.py detect`
- [ ] Review change summary with policy team
- [ ] Execute sync: `python policy_sync.py sync` (versions auto-increment)
- [ ] Verify all changes: `python azure_policy_index.py verify-all`
- [ ] Test sample queries in frontend
- [ ] Update CHANGELOG.md with changes
```

### Post-Update (Next Day)

```markdown
- [ ] Review health check: `curl http://localhost:8000/health`
- [ ] Check error logs for any issues
- [ ] Verify document counts match expectations
- [ ] Send update summary to stakeholders
- [ ] Archive update logs for audit trail
```

---

## CLI Reference

### policy_sync.py Commands

```bash
# Detect changes without applying
python policy_sync.py detect [source_container] [target_container]

# Sync changes (version auto-increments for updates: v1.0 → v2.0)
python policy_sync.py sync [source_container] [target_container]

# Dry run (detect + preview without changes)
python policy_sync.py sync --dry-run

# Advisory flags (version auto-increments, these are informational only)
python policy_sync.py sync --version "2.0" --effective-date "2025-12-01"

# Rollback to previous version
python policy_sync.py rollback \
  --reference "502" \
  --to-version "1.0" \
  --reason "Issue description"

# Retire a policy (mark as RETIRED, move to archive)
python policy_sync.py retire policy.pdf

# Process single PDF file
python policy_sync.py process /path/to/policy.pdf

# Full reindex (use sparingly - slow for large indexes)
python policy_sync.py reindex [container]
```

### azure_policy_index.py Commands

```bash
# Create/update index schema
python azure_policy_index.py create

# Upload chunks from folder
python azure_policy_index.py upload /path/to/chunks

# Test search
python azure_policy_index.py search "visitor policy"

# Get index statistics
python azure_policy_index.py stats

# Update synonym map (132 healthcare rules)
python azure_policy_index.py synonyms

# Test synonym expansion
python azure_policy_index.py test-synonyms "ED code blue"

# Verify specific policy by reference number
python azure_policy_index.py verify "502"

# List all versions of a policy
python azure_policy_index.py list-versions "502"

# Verify all active policies (summary)
python azure_policy_index.py verify-all

# Backup index metadata to JSON
python azure_policy_index.py backup [output_filename.json]
```

---

## Troubleshooting

### Issue: Chunks not appearing after sync

```bash
# Verify the policy by reference number to check status
python azure_policy_index.py verify "502"

# If chunks are missing, run the full pipeline ingestion
cd /Users/JCR/Desktop/rag_pt_rush/apps/backend
python scripts/full_pipeline_ingest.py
```

### Issue: Version conflict (same version number)

```bash
# List existing versions to see what's already indexed
python azure_policy_index.py list-versions "502"

# Version numbers are auto-incremented by policy_sync.py
# If you need to re-sync, first delete the PDF from source and re-add
az storage blob delete --account-name policytechrush \
  --container-name policies-source --name "policy.pdf"

az storage blob upload --account-name policytechrush \
  --container-name policies-source --file "policy.pdf"

python policy_sync.py sync
```

### Issue: Rollback fails

```bash
# Check if previous version chunks still exist
python azure_policy_index.py list-versions "502"

# Rollback to specific version (chunks must still exist)
python policy_sync.py rollback --reference "502" --to-version "1.0" --reason "Production issue"

# If previous version was hard-deleted, restore from backup JSON
# First, check available backups in your backup location
ls -la backups/*.json

# Re-ingest from the backup or source PDF
python scripts/full_pipeline_ingest.py
```

### Issue: Need to verify index health

```bash
# Verify all active policies in index
python azure_policy_index.py verify-all

# Get full index statistics
python azure_policy_index.py stats

# Create a backup of current index metadata
python azure_policy_index.py backup "pre-update-backup.json"
```

---

## Appendix: Version Status Flow

```
                    ┌─────────────────────────────────────┐
                    │                                     │
                    ▼                                     │
              ┌──────────┐                               │
              │  DRAFT   │ ─── Publish ──────────────────┤
              └──────────┘                               │
                                                         │
              ┌──────────┐                               │
   Create ──▶ │  ACTIVE  │ ◀── Rollback ─────────────────┤
              └──────────┘                               │
                    │                                     │
                    │ Update (new version)               │
                    ▼                                     │
              ┌──────────────┐                           │
              │  SUPERSEDED  │ ─── Rollback ─────────────┘
              └──────────────┘
                    │
                    │ Retire (after 2 years)
                    ▼
              ┌──────────┐
              │  RETIRED │ ─── Hard delete (after 7 years)
              └──────────┘
```

---

## Contact

For questions about monthly update procedures:
- **Technical Issues**: IT Innovation Team
- **Policy Content**: Policy & Procedure Committee
- **Compliance**: Corporate Compliance Office
