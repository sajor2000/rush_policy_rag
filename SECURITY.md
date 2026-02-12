# Security Policy

## Reporting Vulnerabilities

If you discover a security vulnerability in this project, please report it responsibly:

1. **Do NOT open a public GitHub issue.**
2. Contact the development team directly or create a [private security advisory](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability) on this repository.
3. Include steps to reproduce, impact assessment, and any suggested fixes.

We aim to acknowledge reports within **2 business days** and provide a remediation timeline within **5 business days**.

## HIPAA Awareness

This system retrieves **organizational policy documents** and does **not** store electronic Protected Health Information (ePHI). However, user-submitted chat queries may inadvertently contain patient information. See the full security documentation for mitigations (truncation, 90-day retention, no user identifiers).

## Security Controls Summary

- **Authentication**: Azure AD (JWT) with tenant/audience verification
- **Input Validation**: OData injection prevention, prompt injection defense (unicode normalization, homoglyph mapping), query length limits
- **XSS Protection**: DOMPurify sanitization on all rendered content
- **Rate Limiting**: Per-IP rate limiting via slowapi (30 req/min default)
- **Resilience**: Circuit breaker pattern for Azure OpenAI outages
- **Static Analysis**: Semgrep (10 custom rules), Bandit (pre-commit), CodeQL (CI), pip-audit, npm audit
- **Security Headers**: CSP, HSTS, X-Frame-Options, X-Content-Type-Options

## Full Documentation

For complete security architecture, see [docs/SECURITY.md](docs/SECURITY.md).
