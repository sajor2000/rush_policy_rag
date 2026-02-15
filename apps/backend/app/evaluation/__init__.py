"""
DeepEval-based RAG evaluation module for RUSH PolicyTech Agent.

This module provides:
- DeepEval metrics for CI/CD integration
- Claim-level diagnostics (RAGChecker-style)
- Lost-in-the-Middle detection
- Weekly evaluation reporting
"""

from .diagnostics import (
    ClaimClassification,
    RAGDiagnostic,
    classify_claim,
    decompose_to_claims,
    diagnose_rag_failure,
)
from .metrics import (
    RUSH_POLICY_METRICS,
    DeepEvalMetrics,
    DeepEvalResult,
    evaluate_batch,
    evaluate_single,
)

__all__ = [
    # Metrics
    "DeepEvalMetrics",
    "RUSH_POLICY_METRICS",
    "evaluate_single",
    "evaluate_batch",
    "DeepEvalResult",
    # Diagnostics
    "decompose_to_claims",
    "classify_claim",
    "diagnose_rag_failure",
    "ClaimClassification",
    "RAGDiagnostic",
]
