---
title: "P0 Healthcare Data Integrity Fixes: Field Validation, PHI Redaction, Quarantine Safeguard"
date: 2026-02-14
category: security-issues
tags:
  - azure-search
  - data-validation
  - phi-redaction
  - hipaa-compliance
  - audit-logging
  - document-ingestion
  - healthcare
  - fail-fast
  - metadata-validation
  - code-review
severity: critical
components:
  - apps/backend/azure_policy_index.py
  - apps/backend/app/services/phi_filter.py
  - apps/backend/app/services/chat_audit_service.py
  - apps/backend/policy_sync.py
related_issues: []
time_to_resolve: "4-6 hours"
confidence: high
branch: feat/cicd-gated-deployment
commits:
  - f458469
---

# P0 Healthcare Data Integrity Fixes

Three critical (P0) findings from a comprehensive code review of the RUSH Policy RAG system, all unified by the theme of **data integrity in healthcare systems**. Each fix shifts a default from silent/permissive to fail-fast/secure.

| Fix | Problem | Impact |
|-----|---------|--------|
| [1. Field Validation](#fix-1-azure-search-pre-upload-field-validation) | Ghost entries in search index | Empty results for valid queries |
| [2. PHI Redaction](#fix-2-phi-redaction-in-audit-logs) | Unredacted PHI in audit logs | HIPAA compliance violation |
| [3. Quarantine Safeguard](#fix-3-quarantine-mode-default-change) | Silent data loss during sync | Missing policies for clinicians |

---

## Fix 1: Azure Search Pre-Upload Field Validation

### Problem Symptom

Documents with missing required fields (empty `text`, no `policy_title`, no `reference_number`) could be uploaded to Azure AI Search, creating "ghost entries" that appear in search results but return empty/useless content. Users querying for affected policies would see results with no actionable information.

### Root Cause

`PolicySearchIndex.upload_chunks()` in `azure_policy_index.py` had no validation layer before sending chunks to Azure AI Search. The chunker (`PolicyChunker`) could produce invalid chunks due to PDF parsing failures, but these were never caught before index upload.

Three failure modes:
1. **Searchable but empty** - Chunks with `content=""` indexed but returned no results
2. **Broken citations** - Chunks without `policy_title` produced malformed evidence cards
3. **Orphaned chunks** - Chunks without identifiers couldn't be filtered or grouped

### Working Solution

**File**: `apps/backend/azure_policy_index.py`

Added `_validate_chunks()` static method for pre-upload validation:

```python
@staticmethod
def _validate_chunks(chunks: List[PolicyChunk]) -> tuple:
    """Validate chunks have required fields before upload."""
    valid = []
    rejected = []
    for chunk in chunks:
        missing = []
        if not chunk.text or not chunk.text.strip():
            missing.append("text")
        if not getattr(chunk, "policy_title", "") or not chunk.policy_title.strip():
            missing.append("policy_title")
        if not (
            (getattr(chunk, "policy_number", "") and chunk.policy_number.strip())
            or (getattr(chunk, "reference_number", "") and chunk.reference_number.strip())
        ):
            missing.append("policy_number or reference_number")
        if missing:
            rejected.append({"chunk_id": chunk.chunk_id, "missing": missing})
        else:
            valid.append(chunk)
    return valid, rejected
```

Integrated into `upload_chunks()`:

```python
# Validate required fields before upload
valid_chunks, rejected = self._validate_chunks(chunks)
if rejected:
    for r in rejected:
        logger.warning(f"Chunk {r['chunk_id']} rejected: missing {', '.join(r['missing'])}")
    logger.warning(f"{len(rejected)} chunk(s) rejected due to missing required fields")

stats = {"uploaded": 0, "failed": 0, "rejected": len(rejected)}
```

### Design Decisions

- **Static method**: No instance state needed, enables reuse and testing
- **Soft rejection**: Invalid chunks are logged and counted but don't crash the entire sync
- **OR logic for identifiers**: Accept either `policy_number` OR `reference_number` (legacy docs may only have one)
- **Structured rejection report**: Returns detailed `{"chunk_id", "missing"}` for monitoring

---

## Fix 2: PHI Redaction in Audit Logs

### Problem Symptom

User-submitted queries logged to audit storage could contain PHI (Protected Health Information). While the system is for **policy retrieval** (not clinical data), users might include patient information in queries:

- "What is the policy for patient John Smith's insulin pump?"
- "MRN 1234567 needs oxygen therapy approval"
- "Call 312-555-1234 about the verbal orders policy"

Audit logs are retained for 90 days and could be accessed by ops teams without clinical data authorization.

### Root Cause

`ChatAuditService._build_audit_record()` logged raw user questions with no sanitization:

```python
# Before fix
question=request.message  # Raw user input, potentially containing PHI
```

### Working Solution

**New file**: `apps/backend/app/services/phi_filter.py`

Created regex-based PHI redaction covering HIPAA Safe Harbor identifiers (45 CFR 164.514(b)):

```python
_PHI_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    (re.compile(r"\bMRN[:\s#]*\d{6,10}\b", re.IGNORECASE), "[MRN]"),
    (re.compile(r"\b\d{1,2}[/\-]\d{1,2}[/\-]\d{4}\b"), "[DOB]"),
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
    (re.compile(r"\b(?:\(\d{3}\)\s*|\d{3}[\-.])\d{3}[\-.]\d{4}\b"), "[PHONE]"),
    (re.compile(r"(?<!\. )(?<!\A)\b[A-Z][a-z]{1,15} [A-Z][a-z]{1,15}\b"), "[NAME]"),
]

_FALSE_POSITIVE_NAMES = frozenset({
    "Smith Nephew", "Baxter International", "Rush University",
    "Rush Medical", "Rush Copley", "Rush Oak", "Saint Luke",
})

def redact_phi(text: str) -> Tuple[str, List[str]]:
    """Returns (redacted_text, flags) e.g. ('[NAME]: 1', '[MRN]: 1')."""
```

**Modified file**: `apps/backend/app/services/chat_audit_service.py`

Integrated PHI redaction before audit logging:

```python
from app.services.phi_filter import redact_phi

# In _build_audit_record():
question_redacted, phi_flags = redact_phi(request.message)
if phi_flags:
    logger.info(f"PHI redacted from audit question: {phi_flags}")
question = question_redacted[: settings.CHAT_AUDIT_MAX_QUESTION_LENGTH]
```

### Design Decisions

- **Regex-only**: Fast (~1ms), deterministic, no external API calls. Not a substitute for full NLP-based PII detection for clinical data.
- **Pre-compiled patterns**: Module-level compilation evaluated once at import
- **False positive allowlist**: Prevents redacting medical device brands ("Smith Nephew") and Rush entity names
- **Informational logging**: `phi_flags` logged separately for security monitoring without storing actual PHI
- **Best-effort scope**: Covers the most common Safe Harbor identifiers likely to appear in policy queries

### Important Context

Medical policies themselves never contain PHI. The PHI filter addresses **user-submitted queries** where someone might include patient information when asking about a policy. This is a policy retrieval tool, not a clinical data system.

---

## Fix 3: Quarantine Mode Default Change

### Problem Symptom

During monthly sync operations, documents with incomplete metadata were silently skipped:

1. Monthly sync runs with `metadata_gate_mode="quarantine"` (old default)
2. "Critical_Verbal_Orders.pdf" fails metadata validation (e.g., missing page_number)
3. Document is quarantined: logged as warning, excluded from search index
4. Sync reports "49/50 documents updated, 1 quarantined" - appears successful
5. User asks "What is the verbal orders policy?" - gets "Not found"
6. Policy exists in blob storage but is NOT searchable

### Root Cause

`policy_sync.py` defaulted to `metadata_gate_mode="quarantine"` which auto-skips invalid documents. No alerting mechanism existed when documents were quarantined. The quarantine report was written to a file but never surfaced to operators unless explicitly checked.

### Working Solution

**File**: `apps/backend/policy_sync.py`

Changed default from `"quarantine"` to `"fail"` in both `sync_delta()` and CLI entry point:

```python
# sync_delta() and sync_monthly()
metadata_gate_mode: str = "fail"  # Was "quarantine"

# CLI entry point
metadata_gate_mode = get_arg("--metadata-gate-mode", "fail")  # Was "quarantine"
```

Added quarantine safeguard that blocks deployment unless explicitly overridden:

```python
if report.documents_quarantined > 0 and not dry_run:
    msg = (
        f"{report.documents_quarantined} document(s) quarantined -- "
        f"these policies are NOT searchable. "
        f"Review quarantine report: {quarantine_report_path or 'N/A'}. "
        f"Set FORCE_QUARANTINE_DEPLOY=true to proceed anyway."
    )
    if not os.environ.get("FORCE_QUARANTINE_DEPLOY"):
        logger.critical(msg)
        raise RuntimeError(msg)
    logger.warning(f"FORCE_QUARANTINE_DEPLOY set. {msg}")
```

### Design Decisions

| Mode | Missing Metadata | Index Upload | Sync Blocks | Use Case |
|------|-----------------|--------------|-------------|----------|
| `fail` (new default) | Raises exception | No | Yes | Development, strict validation |
| `quarantine` | Skips document | No | Yes (unless `FORCE_QUARANTINE_DEPLOY=true`) | Production with explicit opt-in |
| `autofill` | Infers metadata | Yes | Only if inference fails | Legacy document migration |

- **Fail-safe default**: Production should never silently drop documents
- **Escape hatch**: `FORCE_QUARANTINE_DEPLOY=true` allows urgent deployments with known metadata issues
- **CRITICAL-level logging**: Quarantine blocks log at `logger.critical()` to trigger monitoring alerts

---

## Cross-Cutting Principles

All three fixes share a common philosophy:

### 1. Defense in Depth
- **Validation** catches bad data before it reaches Azure Search
- **PHI redaction** protects sensitive data before it's logged
- **Quarantine safeguard** blocks incomplete data before it degrades user experience

### 2. Observable Failures
- All three log detailed diagnostics with structured information
- Quarantine reports and metadata reports provide actionable artifacts
- PHI redaction flags enable security auditing without storing actual PHI

### 3. Fail-Fast Defaults
- Systems designed for convenience can fail unsafely
- Shift defaults toward security: validate before upload, redact before logging, fail before silently skipping
- Dangerous modes require explicit opt-in via environment variables

---

## Prevention Strategies

### For Ghost Entries (Fix 1)
- **CI check**: Post-upload validation querying sample documents for field population
- **Monitoring**: Daily index scan for documents missing required fields
- **Test**: `test_reject_document_with_empty_content()`, `test_reject_document_with_missing_title()`
- **Reconciliation**: Compare blob storage count vs. search index count after every sync

### For PHI Leaks (Fix 2)
- **Automated scanning**: Azure Monitor query scanning logs for PHI patterns (SSN, MRN regex)
- **Canary queries**: Weekly synthetic test queries with fake PHI to verify redaction
- **Pattern maintenance**: Update PHI regex patterns quarterly (new MRN formats, etc.)
- **Test**: Parameterized tests for each PHI type (SSN, MRN, DOB, email, phone, name)

### For Silent Data Loss (Fix 3)
- **Alerting**: Real-time notification when `documents_quarantined > 0`
- **Reconciliation**: Post-ingestion job comparing blob count vs. index count
- **User signals**: Track "Policy Not Found" feedback as data quality metric
- **CI enforcement**: Ensure deploy workflow uses `--metadata-gate-mode fail` explicitly

---

## Related Documentation

| Document | Relevance |
|----------|-----------|
| `docs/AUDIT_AND_QUALITY.md` | Audit logging architecture, HIPAA compliance, data lifecycle |
| `docs/MONTHLY_UPDATE_PROCEDURES.md` | Sync procedures, metadata validation contract |
| `docs/AZURE_INDEX_UPDATE_GUIDE.md` | Azure AI Search schema and field definitions |
| `docs/SECURITY.md` | Security architecture, input validation |
| `docs/ENV_VARS.md` | `CHAT_AUDIT_*` variables, `FORCE_QUARANTINE_DEPLOY` |
| `todos/001-complete-p0-azure-search-field-validation.md` | Original finding details |
| `todos/002-complete-p0-audit-log-phi-redaction.md` | Original finding details |
| `todos/003-complete-p0-quarantine-mode-default.md` | Original finding details |

## Files Modified

| File | Change |
|------|--------|
| `apps/backend/azure_policy_index.py` | Added `_validate_chunks()`, integrated into `upload_chunks()` |
| `apps/backend/app/services/phi_filter.py` | **New file** - regex-based PHI redaction |
| `apps/backend/app/services/chat_audit_service.py` | Integrated PHI redaction before logging |
| `apps/backend/policy_sync.py` | Changed default to "fail", added quarantine safeguard |
