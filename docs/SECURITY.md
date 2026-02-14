# Security Documentation

> Security architecture and practices for the RUSH Policy RAG system.
>
> Last Updated: 2026-02-12

## Overview

The RUSH Policy RAG system handles sensitive healthcare policy information. This document outlines the security measures in place.

---

## Authentication

### Azure AD Integration (Optional)

Enable Azure AD authentication for production:

```bash
REQUIRE_AAD_AUTH=true
AZURE_AD_TENANT_ID=<tenant-id>
AZURE_AD_CLIENT_ID=<app-client-id>
AZURE_AD_ALLOWED_CLIENT_IDS=<comma-separated-app-ids>
```

**Implementation**: `apps/backend/app/core/auth.py`

- JWT token validation
- Tenant and audience verification
- Role-based access (optional)

### Admin Endpoints

Protected by API key:

```bash
ADMIN_API_KEY=<secure-random-key>
```

Admin endpoints (`/api/admin/*`) require header:
```
X-Admin-Key: <api-key>
```

---

## Input Validation

### OData Injection Prevention

**File**: `apps/backend/app/core/security.py`

All user inputs are validated before use in Azure AI Search queries:

```python
# Dangerous patterns blocked
ODATA_INJECTION_PATTERNS = [
    r'\$filter', r'\$select', r'\$orderby',
    r'\$top', r'\$skip', r'\$count',
    r'--', r';', r'/\*', r'\*/'
]
```

### Query Validation

**File**: `apps/backend/app/services/query_validation.py`

- Maximum query length: 2,000 characters
- Blocked adversarial patterns (jailbreak attempts)
- Gibberish/unclear query detection
- Out-of-scope topic filtering

### Prompt Injection Defense

**File**: `apps/backend/app/services/query_validation.py` (lines 368-404)

Multi-layer unicode normalization pipeline applied to all user queries before adversarial pattern matching:

1. **NFD Decomposition** — splits precomposed characters (e.g., `o` + combining accent)
2. **Diacritical Stripping** — removes combining diacriticals (U+0300-U+036F)
3. **NFKC Normalization** — maps fullwidth characters to ASCII equivalents
4. **Zero-Width Character Stripping** — replaces U+200B, U+200C, U+200D, U+FEFF, U+2060, U+00AD, U+202E with spaces to preserve word boundaries
5. **Cyrillic Homoglyph Mapping** — maps 12 common Cyrillic lookalike characters (`о` → `o`, `а` → `a`, `е` → `e`, etc.) to Latin equivalents

This prevents bypass attempts using visually identical but technically different Unicode characters.

### XSS Protection

**Files**: `apps/frontend/src/lib/sanitize.ts`, `apps/frontend/src/lib/chatMessageFormatting.ts`

All LLM-generated content is sanitized before rendering:

- **DOMPurify** integration strips malicious HTML/JS from model responses
- Sanitization is called from the message formatting layer before any DOM insertion
- Prevents stored XSS via model output manipulation

### Request Size Limits

```bash
MAX_REQUEST_SIZE=1048576  # 1MB default
```

---

## Resilience

### Circuit Breaker

**File**: `apps/backend/app/core/circuit_breaker.py`

Implements the circuit breaker pattern for Azure OpenAI calls:

- **Closed** (normal): Requests pass through; failures tracked
- **Open** (tripped): Requests fail fast with a cached/fallback response after consecutive failures
- **Half-Open** (probing): Allows a single request through to test recovery

Prevents cascading failures during Azure OpenAI outages or rate-limit storms.

---

## Rate Limiting

**File**: `apps/backend/app/core/rate_limit.py`

Using `slowapi`:

- Default: 30 requests per minute per IP
- Configurable per endpoint
- Returns 429 Too Many Requests when exceeded

---

## CORS Configuration

**File**: `apps/backend/main.py`

```python
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:3000")
```

Production should restrict to specific origins:

```bash
CORS_ORIGINS=https://your-frontend.azurecontainerapps.io
```

---

## Security Headers

### Backend (FastAPI)

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `X-XSS-Protection: 1; mode=block`

### Frontend (Next.js)

**File**: `apps/frontend/next.config.js`

```javascript
{
  'Content-Security-Policy': "default-src 'self'; ...",
  'X-Frame-Options': 'DENY',
  'X-Content-Type-Options': 'nosniff',
  'Strict-Transport-Security': 'max-age=31536000; includeSubDomains',
  'Referrer-Policy': 'strict-origin-when-cross-origin'
}
```

---

## Secrets Management

### Never Commit

Files in `.gitignore`:
- `.env` files with credentials
- `*.pem`, `*.key`, `*.crt` certificates
- `credentials.json`
- `secrets/` directory

### Azure Container Apps

Use managed secrets:

```bash
az containerapp secret set \
  --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --secrets aoai-api-key=<value>

az containerapp update \
  --set-env-vars "AOAI_API_KEY=secretref:aoai-api-key"
```

### Key Rotation

Recommended rotation schedule:
- API keys: Every 90 days
- Service credentials: Every 180 days

---

## Data Protection

### In Transit

- All Azure services use HTTPS/TLS 1.2+
- Container Apps enforce HTTPS
- Internal service communication via Azure private networking

### At Rest

- Azure AI Search: Encrypted at rest (Microsoft-managed keys)
- Azure Blob Storage: Encrypted at rest
- Azure OpenAI: No data retention for API calls

### HIPAA Awareness

This system retrieves **organizational policy documents** and does **not** store or process electronic Protected Health Information (ePHI). However:

- **Risk**: User-submitted chat queries MAY inadvertently contain patient information (e.g., "What is the policy for patient John Doe's insulin pump?")
- **Mitigations**:
  - Chat audit logs truncate questions to 2,000 characters and responses to 5,000 characters
  - Audit log retention is capped at **90 days** (configurable via `CHAT_AUDIT_RETENTION_DAYS`)
  - No user identifiers (name, email, IP) are stored in audit records unless Azure AD is enabled
  - Audit logging is optional and can be disabled via `CHAT_AUDIT_ENABLED=false`
- **Code**: See `apps/backend/app/services/chat_audit_service.py` for implementation

### No PII Storage

The system:
- Does NOT store user queries long-term (optional audit logging)
- Does NOT store authentication tokens
- Does NOT log sensitive request bodies

---

## Audit Logging

### Chat Audit Service

**File**: `apps/backend/app/services/chat_audit_service.py`

Optional logging to Azure Blob Storage:
- Query text (sanitized)
- Timestamp
- Response confidence
- Sources used

Does NOT log:
- User identifiers (unless AAD enabled)
- IP addresses
- Full response content

---

## Adversarial Protection

### RISEN Prompt Framework

**File**: `apps/backend/policytech_prompt.txt`

System prompt includes:
- Role restrictions (policy-only responses)
- Jailbreak refusal patterns
- Citation requirements
- Hallucination prevention

### Query-Level Defenses

**File**: `apps/backend/app/services/chat_service.py`

```python
if self._is_adversarial_query(request.message):
    return ChatResponse(
        response=ADVERSARIAL_REFUSAL_MESSAGE,
        safety_flags=["ADVERSARIAL_BLOCKED"]
    )
```

Blocks patterns like:
- "Ignore previous instructions"
- "You are now..."
- Base64/encoded payloads
- Role override attempts

---

## Dependency Security

### Static Analysis Tools

| Tool | Scope | Integration | Configuration |
|------|-------|-------------|---------------|
| **Semgrep** | Python + JS | Pre-commit + CI | `.semgrep.yml` (10 custom rules) |
| **Bandit** | Python security | Pre-commit | Default ruleset |
| **CodeQL** | Python + JS | GitHub Actions CI | `.github/workflows/ci.yml` |
| **pip-audit** | Python dependencies | CI (`dependency-audit` job) | Checks known CVEs |
| **npm audit** | Node.js dependencies | CI (`dependency-audit` job) | Checks npm advisories |

### Backend (Python)

```bash
# Check for vulnerabilities
pip-audit

# Update dependencies
pip install --upgrade -r requirements.txt
```

### Frontend (Node.js)

```bash
# Check for vulnerabilities
npm audit

# Fix automatically
npm audit fix
```

### CI/CD

GitHub Actions runs the following security checks on every PR:

1. **CodeQL** — Scans Python and JavaScript for SQL injection, XSS, insecure dependencies
2. **pip-audit** — Checks Python dependencies against known vulnerability databases
3. **npm audit** — Checks Node.js dependencies against npm security advisories
4. **Bandit** — Python-specific security linter (informational, non-blocking)

---

## CI/CD Security

### Branch Protection

- Pull requests required for all changes to `main`
- At least 1 approving review required
- `CI Gate` status check must pass before merge
- Administrators included (audit compliance)

### Environment-Scoped Secrets

Deployment credentials (`AZURE_CREDENTIALS`) are scoped to GitHub Environments (`nonprod`, `production`), not stored at the repository level. This ensures:

- NonProd deployments cannot use production credentials
- Production deployments require manual approval via GitHub Environment reviewers
- Secret access is limited to workflows that reference the specific environment

### Deployment Gating

1. All 7 CI jobs (lint, test, security scan, etc.) must pass
2. The `ci-gate` summary job aggregates results into a single status check
3. `deploy.yml` only triggers via `workflow_run` when CI succeeds on `main`
4. Manual dispatch is available for emergencies but logs a warning annotation

### Audit Traceability

Every deployment is traceable:

```
Git commit SHA → Docker image tag → Container App revision
```

- Image tags use the commit SHA (deterministic, immutable)
- GitHub Actions deployment summary records exact images deployed
- GitHub Environment history tracks all deployments with timestamps

See [CICD_PIPELINE.md](CICD_PIPELINE.md) for full pipeline documentation.

---

## Production Checklist

Before deploying to production:

- [ ] `REQUIRE_AAD_AUTH=true` or API gateway authentication
- [ ] `ADMIN_API_KEY` set to secure random value
- [ ] `CORS_ORIGINS` restricted to production domains
- [ ] `FAIL_ON_MISSING_CONFIG=true`
- [ ] All secrets in Azure Key Vault or Container Apps secrets
- [ ] HTTPS enforced (Container Apps default)
- [ ] Rate limiting configured appropriately
- [ ] Audit logging enabled (if required)
- [ ] Security headers verified
- [ ] Dependency vulnerabilities checked

---

## Incident Response

### Suspected Security Issue

1. **Contain**: Disable affected endpoint/service
2. **Assess**: Check audit logs for scope
3. **Notify**: Contact security team
4. **Remediate**: Apply fix, rotate credentials if needed
5. **Document**: Post-incident report

### Credential Exposure

If credentials are exposed:

1. Immediately rotate the exposed credential
2. Check audit logs for unauthorized access
3. Update all systems using the credential
4. Review access patterns for anomalies

---

## Contact

For security concerns, contact the development team or create a private security advisory on GitHub.
