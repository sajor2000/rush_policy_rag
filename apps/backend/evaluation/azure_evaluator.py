"""
Azure AI Foundry RAG Evaluator for RUSH PolicyTech Agent.

Uses Azure's native evaluation SDK for production monitoring of:
- Groundedness: Is the response grounded in retrieved context?
- Relevance: Does the response address the query?
- Coherence: Is the response logically structured?
- Retrieval: Are the right documents being retrieved?

Usage:
    evaluator = AzureRAGEvaluator()
    results = await evaluator.evaluate_response(
        query="Who can accept verbal orders?",
        response="Registered nurses, pharmacists...",
        context=["Policy chunk 1...", "Policy chunk 2..."]
    )
"""

import os
import asyncio
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from pathlib import Path

from dotenv import load_dotenv

# Load environment
env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(env_path)

logger = logging.getLogger(__name__)


@dataclass
class EvaluationResult:
    """Result from a single evaluation run."""
    query: str
    response: str
    groundedness_score: float  # 1-5 scale
    relevance_score: float     # 1-5 scale
    coherence_score: float     # 1-5 scale
    retrieval_score: float     # 0-1 scale
    groundedness_reason: str
    relevance_reason: str
    coherence_reason: str
    retrieval_reason: str
    passed: bool               # Overall pass/fail
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AzureRAGEvaluator:
    """
    Azure AI Foundry-based RAG evaluator for production monitoring.
    
    Thresholds:
        - Groundedness >= 4.0 (must be well-grounded in context)
        - Relevance >= 3.5 (should address the query)
        - Coherence >= 3.5 (should be logically structured)
        - Retrieval >= 0.7 (good document retrieval)
    """
    
    GROUNDEDNESS_THRESHOLD = 4.0
    RELEVANCE_THRESHOLD = 3.5
    COHERENCE_THRESHOLD = 3.5
    RETRIEVAL_THRESHOLD = 0.7
    
    def __init__(
        self,
        azure_endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        deployment_name: Optional[str] = None
    ):
        """
        Initialize the Azure RAG evaluator.
        
        Args:
            azure_endpoint: Azure OpenAI endpoint (defaults to env var)
            api_key: Azure OpenAI API key (defaults to env var)
            deployment_name: Model deployment name for evaluation
        """
        self.azure_endpoint = azure_endpoint or os.getenv("AOAI_ENDPOINT")
        self.api_key = api_key or os.getenv("AOAI_API_KEY")
        self.deployment_name = deployment_name or os.getenv("AOAI_CHAT_DEPLOYMENT", "gpt-4.1")
        
        self._evaluators_initialized = False
        self._groundedness_evaluator = None
        self._relevance_evaluator = None
        self._coherence_evaluator = None
        self._retrieval_evaluator = None
    
    def _init_evaluators(self):
        """Lazy-load Azure AI Evaluation SDK evaluators."""
        if self._evaluators_initialized:
            return
        
        try:
            from azure.ai.evaluation import (
                GroundednessEvaluator,
                RelevanceEvaluator,
                CoherenceEvaluator,
                RetrievalEvaluator,
            )
            
            model_config = {
                "azure_endpoint": self.azure_endpoint,
                "api_key": self.api_key,
                "azure_deployment": self.deployment_name,
            }
            
            self._groundedness_evaluator = GroundednessEvaluator(model_config=model_config)
            self._relevance_evaluator = RelevanceEvaluator(model_config=model_config)
            self._coherence_evaluator = CoherenceEvaluator(model_config=model_config)
            self._retrieval_evaluator = RetrievalEvaluator(model_config=model_config)
            
            self._evaluators_initialized = True
            logger.info("Azure AI Evaluation SDK evaluators initialized")
            
        except ImportError as e:
            raise ImportError(
                "azure-ai-evaluation not installed. Run: pip install azure-ai-evaluation"
            ) from e
    
    async def evaluate_response(
        self,
        query: str,
        response: str,
        context: List[str],
        ground_truth: Optional[str] = None
    ) -> EvaluationResult:
        """
        Evaluate a single RAG response using Azure AI evaluators.
        
        Args:
            query: The user's question
            response: The agent's response
            context: Retrieved document chunks
            ground_truth: Expected answer (optional, for retrieval eval)
            
        Returns:
            EvaluationResult with scores and reasoning
        """
        self._init_evaluators()
        
        # Prepare context as single string for evaluators
        context_str = "\n\n---\n\n".join(context)
        
        # Run evaluations (these are sync, wrap in executor for async)
        loop = asyncio.get_event_loop()
        
        groundedness_result = await loop.run_in_executor(
            None,
            lambda: self._groundedness_evaluator(
                query=query,
                response=response,
                context=context_str
            )
        )
        
        relevance_result = await loop.run_in_executor(
            None,
            lambda: self._relevance_evaluator(
                query=query,
                response=response,
                context=context_str
            )
        )
        
        coherence_result = await loop.run_in_executor(
            None,
            lambda: self._coherence_evaluator(
                query=query,
                response=response
            )
        )
        
        retrieval_result = await loop.run_in_executor(
            None,
            lambda: self._retrieval_evaluator(
                query=query,
                context=context_str,
                ground_truth=ground_truth or ""
            )
        )
        
        # Extract scores (handle different result formats)
        groundedness_score = self._extract_score(groundedness_result, "groundedness")
        relevance_score = self._extract_score(relevance_result, "relevance")
        coherence_score = self._extract_score(coherence_result, "coherence")
        retrieval_score = self._extract_score(retrieval_result, "retrieval")
        
        # Determine pass/fail
        passed = (
            groundedness_score >= self.GROUNDEDNESS_THRESHOLD and
            relevance_score >= self.RELEVANCE_THRESHOLD and
            coherence_score >= self.COHERENCE_THRESHOLD and
            retrieval_score >= self.RETRIEVAL_THRESHOLD
        )
        
        return EvaluationResult(
            query=query,
            response=response[:500] + "..." if len(response) > 500 else response,
            groundedness_score=groundedness_score,
            relevance_score=relevance_score,
            coherence_score=coherence_score,
            retrieval_score=retrieval_score,
            groundedness_reason=self._extract_reason(groundedness_result),
            relevance_reason=self._extract_reason(relevance_result),
            coherence_reason=self._extract_reason(coherence_result),
            retrieval_reason=self._extract_reason(retrieval_result),
            passed=passed
        )
    
    def _extract_score(self, result: Dict, metric_name: str) -> float:
        """Extract score from evaluator result."""
        if isinstance(result, dict):
            # Try common key patterns
            for key in [f"{metric_name}_score", metric_name, "score", "gpt_score"]:
                if key in result:
                    try:
                        return float(result[key])
                    except (TypeError, ValueError):
                        pass
        return 0.0
    
    def _extract_reason(self, result: Dict) -> str:
        """Extract reasoning from evaluator result."""
        if isinstance(result, dict):
            for key in ["reason", "reasoning", "explanation", "gpt_reason"]:
                if key in result:
                    return str(result[key])
        return ""
    
    async def evaluate_batch(
        self,
        test_cases: List[Dict[str, Any]]
    ) -> List[EvaluationResult]:
        """
        Evaluate a batch of test cases.
        
        Args:
            test_cases: List of dicts with keys: query, response, context, ground_truth
            
        Returns:
            List of EvaluationResult objects
        """
        results = []
        for i, case in enumerate(test_cases):
            logger.info(f"Evaluating case {i+1}/{len(test_cases)}: {case['query'][:50]}...")
            result = await self.evaluate_response(
                query=case["query"],
                response=case["response"],
                context=case.get("context", []),
                ground_truth=case.get("ground_truth")
            )
            results.append(result)
        
        return results
    
    def generate_report(self, results: List[EvaluationResult]) -> Dict[str, Any]:
        """Generate summary report from batch evaluation results."""
        if not results:
            return {"error": "No results to report"}
        
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        
        avg_groundedness = sum(r.groundedness_score for r in results) / total
        avg_relevance = sum(r.relevance_score for r in results) / total
        avg_coherence = sum(r.coherence_score for r in results) / total
        avg_retrieval = sum(r.retrieval_score for r in results) / total
        
        failed_cases = [r for r in results if not r.passed]
        
        return {
            "summary": {
                "total_cases": total,
                "passed": passed,
                "failed": total - passed,
                "pass_rate": f"{(passed/total)*100:.1f}%"
            },
            "average_scores": {
                "groundedness": round(avg_groundedness, 2),
                "relevance": round(avg_relevance, 2),
                "coherence": round(avg_coherence, 2),
                "retrieval": round(avg_retrieval, 2)
            },
            "thresholds": {
                "groundedness": self.GROUNDEDNESS_THRESHOLD,
                "relevance": self.RELEVANCE_THRESHOLD,
                "coherence": self.COHERENCE_THRESHOLD,
                "retrieval": self.RETRIEVAL_THRESHOLD
            },
            "failed_cases": [r.to_dict() for r in failed_cases[:10]]  # Top 10 failures
        }


# CLI for standalone usage
if __name__ == "__main__":
    import json
    
    async def main():
        evaluator = AzureRAGEvaluator()
        
        # Example evaluation
        result = await evaluator.evaluate_response(
            query="Who can accept verbal orders?",
            response="According to RUSH policy, verbal orders may be accepted by registered nurses, pharmacists, and respiratory therapists.",
            context=[
                "Verbal orders may be accepted by: Registered Nurses (RN), Pharmacists, Respiratory Therapists...",
                "The receiving practitioner must read back and verify the order..."
            ],
            ground_truth="Registered nurses, pharmacists, and respiratory therapists can accept verbal orders."
        )
        
        print(json.dumps(result.to_dict(), indent=2))
    
    asyncio.run(main())

