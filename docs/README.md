# Documentation Index

> RUSH Policy RAG Agent - Documentation Hub
>
> Last Updated: 2026-01-11

## Quick Links

| I want to... | Go to |
|--------------|-------|
| Set up my development environment | [Root README](../README.md) |
| Deploy to Azure | [DEPLOYMENT.md](../DEPLOYMENT.md) |
| Configure environment variables | [ENV_VARS.md](ENV_VARS.md) |
| Understand the architecture | [TECHNICAL_ARCHITECTURE_PWC.md](TECHNICAL_ARCHITECTURE_PWC.md) |
| Run tests | [TESTING.md](TESTING.md) |
| Update policies monthly | [MONTHLY_UPDATE_PROCEDURES.md](MONTHLY_UPDATE_PROCEDURES.md) |

---

## Documentation Map

### Getting Started

| Document | Description | Audience |
|----------|-------------|----------|
| [README.md](../README.md) | Project overview, quick start | All |
| [CLAUDE.md](../CLAUDE.md) | Comprehensive development guide | Developers |
| [ENV_VARS.md](ENV_VARS.md) | Environment configuration | All |

### Architecture

| Document | Description | Audience |
|----------|-------------|----------|
| [TECHNICAL_ARCHITECTURE_PWC.md](TECHNICAL_ARCHITECTURE_PWC.md) | System architecture diagrams | Architects |
| [Backend README](../apps/backend/README.md) | Backend architecture & API | Developers |
| [Frontend README](../apps/frontend/README.md) | Frontend architecture | Developers |

### Deployment

| Document | Description | Audience |
|----------|-------------|----------|
| [DEPLOYMENT.md](../DEPLOYMENT.md) | Step-by-step Azure deployment | DevOps |
| [DEPLOYMENT_QUICK_REFERENCE.md](DEPLOYMENT_QUICK_REFERENCE.md) | Deployment commands cheatsheet | DevOps |
| [AZURE_INFRASTRUCTURE.md](AZURE_INFRASTRUCTURE.md) | Azure resource details | DevOps |
| [CONTAINER_APPS_ENV_VARS.md](CONTAINER_APPS_ENV_VARS.md) | Container Apps configuration | DevOps |
| [AZURE_DEPLOYMENT_STATUS.md](AZURE_DEPLOYMENT_STATUS.md) | Current deployment status | DevOps |

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
├── ENV_VARS.md                  # Environment variables reference
├── TESTING.md                   # Testing guide
├── SECURITY.md                  # Security documentation
├── TROUBLESHOOTING.md           # Common issues & solutions
├── CHANGELOG.md                 # Release history
├── TECHNICAL_ARCHITECTURE_PWC.md # Architecture overview
├── AZURE_INFRASTRUCTURE.md      # Azure resources
├── AZURE_DEPLOYMENT_STATUS.md   # Deployment status
├── CONTAINER_APPS_ENV_VARS.md   # Container Apps config
├── DEPLOYMENT_QUICK_REFERENCE.md # Deployment cheatsheet
├── MONTHLY_UPDATE_PROCEDURES.md # Policy updates
├── MONTHLY_DOCUMENT_UPDATE_GUIDE.md # Document sync
├── deployment-rollback-tags.txt # Git tags for rollback
├── baselines/                   # Performance baselines
│   └── on_your_data_baseline_*.json
└── archive/                     # Historical documents
    ├── phase1-completion-summary.md
    ├── deployment-completion-summary.md
    └── ... (legacy docs)
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
| README.md | ✅ Current | 2026-01-11 |
| CLAUDE.md | ✅ Current | 2026-01-11 |
| ENV_VARS.md | ✅ Current | 2026-01-11 |
| DEPLOYMENT.md | ✅ Current | 2026-01-11 |
| Backend README | ✅ Current | 2026-01-11 |
| Frontend README | ✅ Current | 2026-01-11 |
| TECHNICAL_ARCHITECTURE_PWC.md | ✅ Current | 2026-01-11 |
| CHANGELOG.md | ✅ Current | 2026-01-11 |
| archive/* | 📁 Archived | N/A |

---

## Need Help?

1. **Development questions**: See [CLAUDE.md](../CLAUDE.md)
2. **Deployment issues**: See [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
3. **Configuration**: See [ENV_VARS.md](ENV_VARS.md)
4. **Report issues**: [GitHub Issues](https://github.com/sajor2000/rush_policy_rag/issues)
