# PolicyTech Document Sourcing

> How policy documents flow from the authoritative PolicyTech system into the RUSH Policy RAG search index.

---

## 1. System of Record

**PolicyTech** (NavexOne platform at `rushumc.navexone.com`) is the single authoritative source for all RUSH University System for Health policies. The RAG system is a **read-only retrieval layer** — it does not create, modify, or approve policies. All policy governance (drafting, review, approval, publication) happens in PolicyTech.

| Concern | Responsibility |
|---------|---------------|
| Policy authorship and approval | PolicyTech (NavexOne) |
| Policy storage and versioning | PolicyTech (NavexOne) |
| Policy retrieval for end users | RUSH Policy RAG Agent |
| Search index and embeddings | Azure AI Search (managed by AI Innovation) |

---

## 2. End-to-End Document Flow

```
┌──────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│  POLICYTECH (NavexOne)                                                   │
│  ─────────────────────                                                   │
│  PolicyTech supervisor reviews and approves policies monthly.            │
│  Updated PDFs are downloaded for handoff.                                │
│                                                                          │
│         │                                                                │
│         │  Manual handoff (encrypted transfer)                           │
│         ▼                                                                │
│                                                                          │
│  AI INNOVATION TEAM                                                      │
│  ─────────────────────                                                   │
│  Receives PDFs, uploads to Azure Blob staging container.                 │
│                                                                          │
│         │                                                                │
│         │  az storage blob upload-batch → policies-source                │
│         ▼                                                                │
│                                                                          │
│  AZURE BLOB STORAGE                                                      │
│  ─────────────────────                                                   │
│  policies-source/    ← Staging (new/updated PDFs uploaded here)          │
│  policies-active/    ← Production (auto-synced after pipeline passes)    │
│  policies-archive/   ← Audit trail (superseded/retired PDFs)            │
│                                                                          │
│         │                                                                │
│         │  python scripts/monthly_hr_release_gate.py                     │
│         ▼                                                                │
│                                                                          │
│  MONTHLY RELEASE PIPELINE                                                │
│  ─────────────────────                                                   │
│  SHA-256 change detection → Chunking → Embedding → Quality gates        │
│  → Manual approval → Alias swap → Deploy                                 │
│                                                                          │
│         │                                                                │
│         │  Blue/green alias cutover                                      │
│         ▼                                                                │
│                                                                          │
│  AZURE AI SEARCH INDEX                                                   │
│  ─────────────────────                                                   │
│  rush-policies-active alias → production search index                    │
│  3072-dim embeddings, 29-field schema, vectorSemanticHybrid              │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Manual Handoff Workflow

There is **no automated integration** between PolicyTech and the RAG system. The handoff is a deliberate manual process:

| Step | Who | Action |
|------|-----|--------|
| 1 | PolicyTech Supervisor | Reviews monthly policy updates in NavexOne |
| 2 | PolicyTech Supervisor | Downloads approved/updated PDFs |
| 3 | PolicyTech Supervisor | Delivers PDFs to AI Innovation team (encrypted transfer) |
| 4 | AI Innovation Team | Validates PDF quality (searchable text, not scanned images) |
| 5 | AI Innovation Team | Uploads PDFs to `policies-source` Azure Blob container |
| 6 | AI Innovation Team | Runs the monthly release pipeline |

**Why manual?** PolicyTech (NavexOne) does not expose a bulk-export API suitable for automated ingestion. The manual handoff also provides a human review checkpoint before documents enter the RAG pipeline.

---

## 4. Upload to Staging

The AI Innovation team uploads received PDFs to the `policies-source` Azure Blob container:

```bash
# Upload a batch of PDFs to staging
az storage blob upload-batch \
  --account-name policytechrush \
  --destination policies-source \
  --source /path/to/monthly-pdfs/ \
  --overwrite
```

At this point, no processing occurs. The PDFs sit in staging until the monthly release pipeline is executed.

---

## 5. Change Detection (SHA-256 Hashing)

When the pipeline runs, every PDF in `policies-source` is compared against `policies-active` using SHA-256 content hashing. This determines which documents are new, changed, or unchanged.

```
For each PDF in policies-source:
  1. Compute SHA-256 hash of file content
  2. Compare against stored hash in policies-active metadata
  3. Classify:
     - No match in active → NEW document
     - Hash differs       → CHANGED document (version upgrade)
     - Hash identical     → UNCHANGED (skipped entirely)

For each PDF in policies-active NOT in policies-source:
  → DELETED (marked RETIRED)
```

**Performance**: SHA-256 hashing takes ~0.2s per PDF. Checking 180 documents completes in ~30 seconds regardless of how many changed. Only documents classified as NEW or CHANGED are processed through the chunking and embedding pipeline.

### Code Reference

The hashing logic is in `apps/backend/policy_sync.py`:

- `compute_content_hash()` (line 259): Standard SHA-256 hash
- `compute_content_hash_streaming()` (line 263): Streaming hash for large files
- `detect_changes()` (line 288): Compares source vs. active hashes

---

## 6. Re-indexing Previous-Batch Documents

When a document is updated (hash changed), the pipeline does **not** delete old chunks. Instead, it uses a versioned lifecycle:

| Document State | What Happens | Old Chunks | New Chunks |
|----------------|-------------|------------|------------|
| **NEW** | First ingestion | N/A | Created at v1.0, status=ACTIVE |
| **CHANGED** | Version bump | Marked SUPERSEDED, `superseded_by` set | Created at v2.0, status=ACTIVE |
| **UNCHANGED** | Skipped entirely | No change | No change |
| **DELETED** | Retirement | Marked RETIRED, PDF archived | N/A |

### Version Transition Example (v1 → v2)

```
Before pipeline run:
  Index contains: Policy-A chunks (v1.0, ACTIVE)

After pipeline detects Policy-A has changed:
  1. All v1.0 chunks → policy_status=SUPERSEDED, superseded_by="2.0"
  2. Old PDF → moved to policies-archive/
  3. New PDF → chunked, embedded, uploaded as v2.0 chunks (ACTIVE)
  4. New PDF → copied to policies-active/ with updated metadata
```

### Search Behavior

```
Default user search:  filter = "policy_status eq 'ACTIVE'"
  → Returns only current (v2.0) chunks

Audit/historical search:  filter = "reference_number eq 'NPO-2025-001'"
  → Returns all versions (v1.0 SUPERSEDED + v2.0 ACTIVE)
```

### Rollback

If a new version has issues, the pipeline supports rollback:

```bash
python policy_sync.py rollback \
  --reference "NPO-2025-001" \
  --to-version "1.0" \
  --reason "Compliance issue in v2.0"
```

This re-activates the old chunks and marks the new version as SUPERSEDED. See [MONTHLY_UPDATE_PROCEDURES.md](MONTHLY_UPDATE_PROCEDURES.md) for full rollback procedures.

---

## 7. Quality Gates

Before any monthly update reaches production, the release pipeline runs 5 automated quality gates:

| Gate | Tool | Threshold | What It Checks |
|------|------|-----------|---------------|
| Ingestion audit | `audit_ingestion_quality.py` | Schema validation | All chunks have required fields, valid embeddings |
| HR regression | `verify_hr_retrieval_regressions.py` | 14/14 cases pass | Critical HR policy queries still return correct results |
| Targeted tests | `pytest` (7 test files) | All pass | Unit and integration tests |
| PromptFoo compliance | `promptfoo eval` | Pass >= 90%, Safety <= 5%, Citations >= 90% | End-to-end RAG quality on 100 test cases |
| Baseline comparison | Cross-release check | No metric drops beyond threshold | Compares against previous release scores |

If **any gate fails**, the release is blocked. The pipeline operator must resolve the issue before re-running.

See [AUDIT_AND_QUALITY.md](AUDIT_AND_QUALITY.md) for detailed evaluation metrics and thresholds.

---

## 8. Audit Trail and Retention

All document states are preserved for compliance:

| Status | Retention | In Search Results | Available for Audit |
|--------|-----------|-------------------|-------------------|
| ACTIVE | Indefinite | Yes | Yes |
| SUPERSEDED | 2 years | No (filtered) | Yes |
| RETIRED | 2 years | No (filtered) | Yes |
| DRAFT | 30 days | No (filtered) | Yes |

**What is preserved:**
- Every version of every policy (chunks remain in the search index with status metadata)
- Superseded and retired PDFs (moved to `policies-archive/` container, not deleted)
- Blob metadata: version number, content hash, processing date, chunk IDs
- Monthly release pipeline logs and evaluation baselines

**Chat audit trail** (separate from document versioning): Every user query and RAG response is captured in Azure Blob Storage with 90-day retention. See [AUDIT_AND_QUALITY.md](AUDIT_AND_QUALITY.md).

---

## 9. Monthly Release Timeline

| Day | Activity | Owner |
|-----|----------|-------|
| T-5 | PolicyTech supervisor downloads updated PDFs | PolicyTech Supervisor |
| T-3 | PDFs delivered to AI Innovation team | PolicyTech Supervisor |
| T-2 | PDF quality validation, upload to `policies-source` | AI Innovation Team |
| T-1 | Backup current index: `python azure_policy_index.py backup` | AI Innovation Team |
| T | Run release pipeline: `python scripts/monthly_hr_release_gate.py` | AI Innovation Team |
| T | Review quality gate results, approve promotion | AI Innovation Team |
| T+1 | Verify production search, spot-check queries | AI Innovation Team |
| T+1 | Send update summary to stakeholders | AI Innovation Team |

For detailed step-by-step commands, see [MONTHLY_UPDATE_PROCEDURES.md](MONTHLY_UPDATE_PROCEDURES.md).
