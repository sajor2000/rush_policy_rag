"""
RAG Evaluation module for RUSH PolicyTech agent.

Provides hybrid evaluation using:
- Azure AI Foundry evaluators (production monitoring)
- Azure AI Agent evaluators (hallucination detection + RISEN compliance)
- RAGAS framework (offline deep analysis)
"""

from .azure_evaluator import AzureRAGEvaluator, EvaluationResult
from .ragas_evaluator import RagasEvaluator, RagasResult
from .test_dataset import TestDataset, TestCase, create_initial_dataset
from .agent_evaluator import (
    PolicyAgentEvaluator,
    HallucinationResult,
    TaskAdherenceResult,
    IntentResolutionResult,
    CompletenessResult,
    AgentEvaluationResult,
)

__all__ = [
    # RAG evaluators
    "AzureRAGEvaluator",
    "EvaluationResult",
    "RagasEvaluator",
    "RagasResult",
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
]

