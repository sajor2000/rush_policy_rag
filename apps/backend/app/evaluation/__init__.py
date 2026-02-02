"""
DeepEval-based RAG evaluation module for RUSH PolicyTech Agent.

This module provides:
- DeepEval metrics for CI/CD integration
- Claim-level diagnostics (RAGChecker-style)
- Lost-in-the-Middle detection
- Weekly evaluation reporting
"""

from .metrics import (
    DeepEvalMetrics,
    RUSH_POLICY_METRICS,
    evaluate_single,
    evaluate_batch,
    DeepEvalResult,
)
from .diagnostics import (
    decompose_to_claims,
    classify_claim,
    diagnose_rag_failure,
    ClaimClassification,
    RAGDiagnostic,
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
