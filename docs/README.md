# Documentation Index

> RUSH Policy RAG Agent - Documentation Hub
>
> Last Updated: 2026-02-03

## Quick Links

| I want to... | Go to |
|--------------|-------|
| **Get running in 30 minutes** | [QUICK_START.md](QUICK_START.md) |
| Understand RAG design decisions | [RAG_DESIGN.md](RAG_DESIGN.md) |
| See architecture diagrams | [ARCHITECTURE_DIAGRAMS.md](ARCHITECTURE_DIAGRAMS.md) |
| Full onboarding guide | [ONBOARDING.md](ONBOARDING.md) |
| Deploy to Azure | [DEPLOYMENT.md](../DEPLOYMENT.md) |
| Understand scripts and tooling | [SCRIPTS.md](SCRIPTS.md) |
| Configure environment variables | [ENV_VARS.md](ENV_VARS.md) |
| Run tests | [TESTING.md](TESTING.md) |
| Update policies monthly | [MONTHLY_UPDATE_PROCEDURES.md](MONTHLY_UPDATE_PROCEDURES.md) |

---

## Documentation Map

### Getting Started

| Document | Description | Audience |
|----------|-------------|----------|
| [QUICK_START.md](QUICK_START.md) | **30-minute setup guide** | All |
| [ONBOARDING.md](ONBOARDING.md) | Full onboarding guide | All |
| [README.md](../README.md) | Project overview | All |
| [CLAUDE.md](../CLAUDE.md) | AI assistant development guide | Developers |
| [ENV_VARS.md](ENV_VARS.md) | Environment configuration | All |
| [SCRIPTS.md](SCRIPTS.md) | Script reference | Developers |

### Architecture

| Document | Description | Audience |
|----------|-------------|----------|
| [RAG_DESIGN.md](RAG_DESIGN.md) | **RAG architecture decisions** | Developers |
| [ARCHITECTURE_DIAGRAMS.md](ARCHITECTURE_DIAGRAMS.md) | **Visual system diagrams** | All |
| [TECHNICAL_ARCHITECTURE_PWC.md](TECHNICAL_ARCHITECTURE_PWC.md) | Technical review document | Architects |
| [RushPolicyAssistant_Architecture.pdf](RushPolicyAssistant_Architecture.pdf) | Azure cloud diagram | All |
| [Backend README](../apps/backend/README.md) | Backend architecture | Developers |
| [Frontend README](../apps/frontend/README.md) | Frontend architecture | Developers |

### Deployment

| Document | Description | Audience |
|----------|-------------|----------|
| [DEPLOYMENT.md](../DEPLOYMENT.md) | Step-by-step Azure deployment | DevOps |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Deployment and runtime troubleshooting | DevOps |

### Operations

| Document | Description | Audience |
|----------|-------------|----------|
| [MONTHLY_UPDATE_PROCEDURES.md](MONTHLY_UPDATE_PROCEDURES.md) | Policy update workflow | Operators |
| [MONTHLY_DOCUMENT_UPDATE_GUIDE.md](MONTHLY_DOCUMENT_UPDATE_GUIDE.md) | Document sync procedures | Operators |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Common issues & solutions | All |

### Quality & Security

| Document | Description | Audience |
|----------|-------------|----------|
| [TESTING.md](TESTING.md) | Test strategy & commands | Developers |
| [SECURITY.md](SECURITY.md) | Security architecture | Security Team |
| [CHANGELOG.md](CHANGELOG.md) | Release history | All |

### Contributing

| Document | Description | Audience |
|----------|-------------|----------|
| [CONTRIBUTING.md](../CONTRIBUTING.md) | Contribution guidelines | Contributors |

---

## Folder Structure

```
docs/
├── README.md                    # This file (documentation index)
├── QUICK_START.md               # 30-minute new engineer guide
├── RAG_DESIGN.md                # RAG architecture decisions
├── ARCHITECTURE_DIAGRAMS.md     # Visual system diagrams (Mermaid)
├── ONBOARDING.md                # Full onboarding guide
├── DEV_PROD_PLAN.md             # Dev vs prod plan
├── SCRIPTS.md                   # Script reference
├── ENV_VARS.md                  # Environment variables reference
├── TESTING.md                   # Testing guide
├── RAG_TESTING_PLAN.md          # RAG evaluation strategy
├── SECURITY.md                  # Security documentation
├── TROUBLESHOOTING.md           # Common issues & solutions
├── CHANGELOG.md                 # Release history
├── TECHNICAL_ARCHITECTURE_PWC.md # Technical review document
├── RushPolicyAssistant_Architecture.pdf # Azure cloud architecture
├── MONTHLY_UPDATE_PROCEDURES.md # Policy updates
├── MONTHLY_DOCUMENT_UPDATE_GUIDE.md # Document sync
├── baselines/                   # Performance baselines
│   └── on_your_data_baseline_*.json
└── archive/                     # Historical documents (not actively maintained)
    ├── README.md                # Archive explanation
    ├── FIXES_APPLIED.md
    ├── FIXES_SUMMARY.md
    ├── CODE_AUDIT_FIXES.md
    ├── CODE_AUDIT_FIXES_SUMMARY.md
    ├── IMPLEMENTATION_SUMMARY.md
    └── RAG_OPTIMIZATION_SPRINT_PLAN.md
```

---

## Document Status

| Status | Meaning |
|--------|---------|
| ✅ Current | Up-to-date with codebase |
| ⚠️ Review | May need updates |
| 📁 Archived | Historical reference only |

| Document | Status | Last Verified |
|----------|--------|---------------|
| QUICK_START.md | ✅ Current | 2026-02-03 |
| RAG_DESIGN.md | ✅ Current | 2026-02-03 |
| ARCHITECTURE_DIAGRAMS.md | ✅ Current | 2026-02-03 |
| CLAUDE.md | ✅ Current | 2026-02-03 |
| README.md | ✅ Current | 2026-02-03 |
| ONBOARDING.md | ✅ Current | 2026-01-31 |
| ENV_VARS.md | ✅ Current | 2026-01-11 |
| DEPLOYMENT.md | ✅ Current | 2026-01-11 |
| SCRIPTS.md | ✅ Current | 2026-01-31 |
| RAG_TESTING_PLAN.md | ✅ Current | 2026-01-31 |
| Backend README | ✅ Current | 2026-01-11 |
| Frontend README | ✅ Current | 2026-01-11 |
| archive/* | 📁 Archived | N/A |

---

## Need Help?

1. **Development questions**: See [CLAUDE.md](../CLAUDE.md)
2. **Deployment issues**: See [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
3. **Configuration**: See [ENV_VARS.md](ENV_VARS.md)
4. **Report issues**: [GitHub Issues](https://github.com/sajor2000/rush_policy_rag/issues)
