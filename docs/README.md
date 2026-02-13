# RUSH Policy RAG — Documentation Index

> This documentation describes the RUSH Policy RAG Agent, a read-only retrieval system that helps staff find approved policies from PolicyTech (NavexOne). The system does not create or modify policies. All documents are sourced from the authoritative PolicyTech platform via a monthly manual handoff, processed through a quality-gated pipeline, and made searchable via Azure AI Search.
>
> Last Updated: 2026-02-12

---

## For Compliance Reviewers

Start here to understand the system's security posture, audit trail, document sourcing, and quality assurance.

| Document | What It Answers |
|----------|----------------|
| [SECURITY.md](../SECURITY.md) | How is the system secured? (Auth, input validation, HIPAA controls) |
| [SECURITY.md](SECURITY.md) | Detailed security architecture (encryption, headers, static analysis) |
| [AUDIT_AND_QUALITY.md](AUDIT_AND_QUALITY.md) | How are inputs/outputs monitored and improved over time? |
| [RAG_EVALUATION_MATRIX.md](RAG_EVALUATION_MATRIX.md) | What testing happens, when, and what thresholds apply? |
| [POLICYTECH_DOCUMENT_SOURCING.md](POLICYTECH_DOCUMENT_SOURCING.md) | How do documents get from PolicyTech into the RAG system? |
| [TECHNICAL_ARCHITECTURE_PWC.md](TECHNICAL_ARCHITECTURE_PWC.md) | Technical architecture review document |
| [RAG_DESIGN.md](RAG_DESIGN.md) | Why were specific RAG design decisions made? |
| [ARCHITECTURE_DIAGRAMS.md](ARCHITECTURE_DIAGRAMS.md) | Visual system diagrams (data flow, search pipeline) |

## For Operators

Monthly updates, deployment procedures, and troubleshooting.

| Document | What It Answers |
|----------|----------------|
| [MONTHLY_UPDATE_PROCEDURES.md](MONTHLY_UPDATE_PROCEDURES.md) | How do I process monthly policy updates? (Full procedures + quick reference) |
| [DEPLOYMENT.md](DEPLOYMENT.md) | How do I deploy to Azure Container Apps? |
| [AZURE_INDEX_UPDATE_GUIDE.md](AZURE_INDEX_UPDATE_GUIDE.md) | How do I update the Azure Search index schema? |
| [PDF_INDEXING_GUIDE.md](PDF_INDEXING_GUIDE.md) | How do I ingest and index PDFs? |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Common issues and solutions |
| [APP_COST.md](APP_COST.md) | Azure cost breakdown and estimates |

## For Developers

Getting started, testing, and contributing.

| Document | What It Answers |
|----------|----------------|
| [QUICK_START.md](QUICK_START.md) | How do I get running in 30 minutes? |
| [ONBOARDING.md](ONBOARDING.md) | Full onboarding guide |
| [ENV_VARS.md](ENV_VARS.md) | What environment variables are needed? |
| [SCRIPTS.md](SCRIPTS.md) | What scripts are available and what do they do? |
| [TESTING.md](TESTING.md) | How do I run tests? |
| [RAG_TESTING_PLAN.md](RAG_TESTING_PLAN.md) | RAG evaluation strategy and metrics |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contribution guidelines |
| [STYLE_GUIDE.md](STYLE_GUIDE.md) | RUSH brand style guide |
| [CLAUDE.md](../CLAUDE.md) | AI assistant development instructions |

---

## All Documents

| Document | Purpose | Audience | Status | Last Verified |
|----------|---------|----------|--------|---------------|
| [POLICYTECH_DOCUMENT_SOURCING.md](POLICYTECH_DOCUMENT_SOURCING.md) | PolicyTech → RAG document flow | Compliance, Operators | ✅ Current | 2026-02-12 |
| [AUDIT_AND_QUALITY.md](AUDIT_AND_QUALITY.md) | Audit logging and RAG quality assessment | Compliance, Developers | ✅ Current | 2026-02-12 |
| [RAG_EVALUATION_MATRIX.md](RAG_EVALUATION_MATRIX.md) | Evaluation context matrix (what runs when) | Compliance, Auditors | ✅ Current | 2026-02-12 |
| [SECURITY.md](../SECURITY.md) | Security policy and vulnerability reporting | Auditors | ✅ Current | 2026-02-12 |
| [SECURITY.md](SECURITY.md) | Security architecture documentation | Security Team | ✅ Current | 2026-02-12 |
| [TECHNICAL_ARCHITECTURE_PWC.md](TECHNICAL_ARCHITECTURE_PWC.md) | Technical architecture review | Architects | ✅ Current | 2026-02-12 |
| [RAG_DESIGN.md](RAG_DESIGN.md) | RAG architecture decisions | Developers | ✅ Current | 2026-02-12 |
| [ARCHITECTURE_DIAGRAMS.md](ARCHITECTURE_DIAGRAMS.md) | Visual system diagrams (Mermaid) | All | ✅ Current | 2026-02-03 |
| [RushPolicyAssistant_Architecture.pdf](RushPolicyAssistant_Architecture.pdf) | Azure cloud diagram | All | ✅ Current | 2026-02-03 |
| [MONTHLY_UPDATE_PROCEDURES.md](MONTHLY_UPDATE_PROCEDURES.md) | Monthly policy update workflow | Operators | ✅ Current | 2026-02-12 |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Step-by-step Azure deployment | DevOps | ✅ Current | 2026-01-11 |
| [AZURE_INDEX_UPDATE_GUIDE.md](AZURE_INDEX_UPDATE_GUIDE.md) | Azure Search index update procedures | DevOps | ✅ Current | 2026-01-31 |
| [PDF_INDEXING_GUIDE.md](PDF_INDEXING_GUIDE.md) | PDF ingestion and indexing guide | Operators | ✅ Current | 2026-01-31 |
| [APP_COST.md](APP_COST.md) | Azure cost breakdown | Management | ✅ Current | 2026-02-10 |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Common issues and solutions | All | ✅ Current | 2026-01-31 |
| [QUICK_START.md](QUICK_START.md) | 30-minute setup guide | Developers | ✅ Current | 2026-02-03 |
| [ONBOARDING.md](ONBOARDING.md) | Full onboarding guide | Developers | ✅ Current | 2026-01-31 |
| [ENV_VARS.md](ENV_VARS.md) | Environment variables reference | All | ✅ Current | 2026-02-12 |
| [SCRIPTS.md](SCRIPTS.md) | Script reference | Developers | ✅ Current | 2026-02-12 |
| [TESTING.md](TESTING.md) | Test strategy and commands | Developers | ✅ Current | 2026-02-12 |
| [RAG_TESTING_PLAN.md](RAG_TESTING_PLAN.md) | RAG evaluation strategy | Developers | ✅ Current | 2026-01-31 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contribution guidelines | Contributors | ✅ Current | 2026-02-03 |
| [STYLE_GUIDE.md](STYLE_GUIDE.md) | RUSH brand style guide | Contributors | ✅ Current | 2026-02-12 |
| [CHANGELOG.md](CHANGELOG.md) | Release history | All | ✅ Current | 2026-02-03 |
| [README.md](../README.md) | Project overview | All | ✅ Current | 2026-02-12 |
| [CLAUDE.md](../CLAUDE.md) | AI assistant development guide | Developers | ✅ Current | 2026-02-12 |
| [Backend README](../apps/backend/README.md) | Backend architecture | Developers | ✅ Current | 2026-01-11 |
| [Frontend README](../apps/frontend/README.md) | Frontend architecture | Developers | ✅ Current | 2026-01-11 |

---

## Folder Structure

```
docs/
├── README.md                        # This file (documentation index)
├── POLICYTECH_DOCUMENT_SOURCING.md  # PolicyTech → RAG document flow
├── AUDIT_AND_QUALITY.md             # Audit logging & RAG quality assessment
├── RAG_EVALUATION_MATRIX.md         # Evaluation context matrix (what runs when)
├── SECURITY.md                      # Security documentation
├── TECHNICAL_ARCHITECTURE_PWC.md    # Technical review document
├── RAG_DESIGN.md                    # RAG architecture decisions
├── ARCHITECTURE_DIAGRAMS.md         # Visual system diagrams (Mermaid)
├── RushPolicyAssistant_Architecture.pdf # Azure cloud architecture
├── MONTHLY_UPDATE_PROCEDURES.md     # Monthly policy update workflow
├── DEPLOYMENT.md                    # Step-by-step Azure deployment
├── AZURE_INDEX_UPDATE_GUIDE.md      # Azure Search index update procedures
├── PDF_INDEXING_GUIDE.md            # PDF ingestion and indexing guide
├── APP_COST.md                      # Azure cost breakdown
├── TROUBLESHOOTING.md               # Common issues & solutions
├── QUICK_START.md                   # 30-minute new engineer guide
├── ONBOARDING.md                    # Full onboarding guide
├── ENV_VARS.md                      # Environment variables reference
├── SCRIPTS.md                       # Script reference
├── TESTING.md                       # Testing guide
├── RAG_TESTING_PLAN.md              # RAG evaluation strategy
├── CONTRIBUTING.md                  # Contribution guidelines
├── STYLE_GUIDE.md                   # RUSH brand style guide
└── CHANGELOG.md                     # Release history
```

---

## Need Help?

1. **Development questions**: See [CLAUDE.md](../CLAUDE.md)
2. **Deployment issues**: See [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
3. **Configuration**: See [ENV_VARS.md](ENV_VARS.md)
4. **Report issues**: [GitHub Issues](https://github.com/sajor2000/rush_policy_rag/issues)
