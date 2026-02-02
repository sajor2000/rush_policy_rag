# Contributing to RUSH Policy RAG

Thank you for your interest in contributing to the RUSH Policy RAG system. This document provides guidelines and instructions for contributing.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Making Changes](#making-changes)
- [Pull Request Process](#pull-request-process)
- [Code Style Guidelines](#code-style-guidelines)
- [Testing](#testing)

## Code of Conduct

Please read and follow our [Code of Conduct](CODE_OF_CONDUCT.md) to maintain a respectful and inclusive community.

## Getting Started

1. **Fork the repository** and clone your fork locally
2. **Set up your development environment** (see below)
3. **Create a feature branch** from `develop` (not `main`)
4. **Make your changes** following our style guidelines
5. **Submit a pull request** with a clear description

## Development Setup

### Prerequisites

- Node.js 20.x (see `.nvmrc`)
- Python 3.11+
- Azure CLI (for deployment)
- Docker (optional, for containerized development)

### Backend Setup

```bash
cd apps/backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # Configure your environment variables
python main.py
```

### Frontend Setup

```bash
cd apps/frontend
npm install
cp .env.example .env.local  # Configure your environment variables
npm run dev
```

### Environment Variables

Copy `.env.example` to `.env` and configure the required Azure services:
- Azure AI Search (required)
- Azure OpenAI (required)
- Azure Blob Storage (required)
- Cohere Rerank via Azure AI Foundry (required)

## Making Changes

### Branch Naming Convention

- `feature/` - New features (e.g., `feature/add-pdf-preview`)
- `fix/` - Bug fixes (e.g., `fix/search-timeout`)
- `refactor/` - Code refactoring (e.g., `refactor/chat-service`)
- `docs/` - Documentation updates (e.g., `docs/api-reference`)
- `test/` - Test additions/updates (e.g., `test/query-validation`)

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `style`: Formatting (no code change)
- `refactor`: Code refactoring
- `test`: Adding tests
- `chore`: Maintenance tasks

**Examples:**
```
feat(search): add semantic reranking with Cohere
fix(pdf): handle special characters in filenames
docs(api): update chat endpoint documentation
```

## Pull Request Process

1. **Update documentation** if you're changing behavior
2. **Add tests** for new functionality
3. **Ensure all tests pass** locally
4. **Update CHANGELOG.md** with your changes
5. **Request review** from at least one maintainer

### PR Checklist

Before submitting your PR, ensure:

**Code Quality:**

- [ ] Code follows the project style guidelines
- [ ] No linting errors (`black`, `isort`, `flake8` for Python; `eslint` for TypeScript)
- [ ] No TypeScript errors (`npm run check` in frontend)
- [ ] No sensitive data (API keys, credentials) in code
- [ ] Commits are atomic and well-described

**Testing:**

- [ ] Backend tests pass: `cd apps/backend && pytest tests/ -v`
- [ ] Frontend builds: `cd apps/frontend && npm run build`
- [ ] TypeScript check passes: `cd apps/frontend && npm run check`
- [ ] New features have corresponding tests
- [ ] Existing tests still pass

**Documentation:**

- [ ] CHANGELOG.md updated with changes
- [ ] README updated if adding new features/config
- [ ] API documentation updated if endpoints changed
- [ ] Code comments added for complex logic

**Security (for security-sensitive changes):**

- [ ] Input validation added for user inputs
- [ ] No SQL/OData injection vulnerabilities
- [ ] Secrets not logged or exposed
- [ ] Rate limiting considered if applicable

### Security Review

Request a security review for changes involving:

- Authentication/authorization logic (`app/core/auth.py`)
- Input validation (`app/core/security.py`)
- File handling or blob storage
- New API endpoints
- Environment variable handling
- Any code processing user input

To request: Add `security-review` label to your PR.

## Code Style Guidelines

### Python (Backend)

- Follow [PEP 8](https://pep8.org/)
- Use type hints for function signatures
- Maximum line length: 100 characters
- Use `black` for formatting, `isort` for imports

```python
# Good
def search_policies(
    query: str,
    top_k: int = 10,
    filter_expression: Optional[str] = None,
) -> list[SearchResult]:
    """Search policies with optional filtering."""
    pass
```

### TypeScript (Frontend)

- Use TypeScript strict mode
- Prefer functional components with hooks
- Use `@/` path alias for imports
- Follow Next.js App Router conventions

```typescript
// Good
import { useState } from 'react';
import { Button } from '@/components/ui/button';

interface Props {
  onSubmit: (query: string) => void;
}

export function SearchInput({ onSubmit }: Props) {
  const [query, setQuery] = useState('');
  // ...
}
```

### General

- Write self-documenting code; add comments only for complex logic
- Keep functions small and focused (single responsibility)
- Handle errors gracefully with meaningful messages
- Avoid hardcoded values; use configuration/constants

## Testing

### Backend Tests

```bash
cd apps/backend
pytest tests/ -v
pytest tests/ --cov=app --cov-report=html  # With coverage
```

### Frontend Tests

```bash
cd apps/frontend
npm run test
npm run test:coverage  # With coverage
```

### Test Requirements

- Unit tests for new utility functions
- Integration tests for API endpoints
- Mock external services (Azure, Cohere) in tests
- Aim for 80%+ code coverage on new code

## Questions?

- Open an issue for bugs or feature requests
- Check existing issues before creating new ones
- Join discussions in pull requests

Thank you for contributing!
