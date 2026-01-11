# Security Documentation

> Security architecture and practices for the RUSH Policy RAG system.
>
> Last Updated: 2026-01-11

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

### Request Size Limits

```bash
MAX_REQUEST_SIZE=1048576  # 1MB default
```

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

GitHub CodeQL scans on every PR for:
- SQL injection
- XSS vulnerabilities
- Insecure dependencies

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
