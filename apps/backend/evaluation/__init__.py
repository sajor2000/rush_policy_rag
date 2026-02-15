"""
RAG Evaluation module for RUSH PolicyTech agent.

Provides hybrid evaluation using:
- Azure AI Foundry evaluators (production monitoring)
- Azure AI Agent evaluators (hallucination detection + RISEN compliance)
- DeepEval (CI/CD integration + claim-level diagnostics)
"""

from .agent_evaluator import (
    AgentEvaluationResult,
    CompletenessResult,
    HallucinationResult,
    IntentResolutionResult,
    PolicyAgentEvaluator,
    TaskAdherenceResult,
)
from .azure_evaluator import AzureRAGEvaluator, EvaluationResult
from .test_dataset import TestCase, TestDataset, create_initial_dataset

# DeepEval imports (optional - only available if deepeval is installed)
try:
    from app.evaluation.diagnostics import (
        ClaimClassification,
        RAGDiagnostic,
        RAGDiagnostics,
        classify_claim,
        decompose_to_claims,
        diagnose_rag_failure,
    )
    from app.evaluation.metrics import (
        RUSH_POLICY_METRICS,
        DeepEvalMetrics,
        DeepEvalResult,
    )
    from app.evaluation.metrics import evaluate_batch as deepeval_evaluate_batch
    from app.evaluation.metrics import evaluate_single as deepeval_evaluate_single

    DEEPEVAL_AVAILABLE = True
except ImportError:
    DEEPEVAL_AVAILABLE = False
    DeepEvalMetrics = None
    RUSH_POLICY_METRICS = None
    deepeval_evaluate_single = None
    deepeval_evaluate_batch = None
    DeepEvalResult = None
    decompose_to_claims = None
    classify_claim = None
    diagnose_rag_failure = None
    ClaimClassification = None
    RAGDiagnostic = None
    RAGDiagnostics = None

__all__ = [
    # RAG evaluators
    "AzureRAGEvaluator",
    "EvaluationResult",
    # Agent evaluators (hallucination + RISEN compliance + intent + completeness)
    "PolicyAgentEvaluator",
    "HallucinationResult",
    "TaskAdherenceResult",
    "IntentResolutionResult",
    "CompletenessResult",
    "AgentEvaluationResult",
    # Test dataset
    "TestDataset",
    "TestCase",
    "create_initial_dataset",
    # DeepEval (if available)
    "DEEPEVAL_AVAILABLE",
    "DeepEvalMetrics",
    "RUSH_POLICY_METRICS",
    "deepeval_evaluate_single",
    "deepeval_evaluate_batch",
    "DeepEvalResult",
    "decompose_to_claims",
    "classify_claim",
    "diagnose_rag_failure",
    "ClaimClassification",
    "RAGDiagnostic",
    "RAGDiagnostics",
]
