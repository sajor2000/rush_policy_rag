"""
RAGAS-based RAG Evaluator for RUSH PolicyTech Agent.

Uses RAGAS (Retrieval Augmented Generation Assessment) for deep offline analysis:
- Faithfulness: Are claims in the response supported by context?
- Answer Relevancy: Does the response address the question?
- Context Precision: Are relevant chunks ranked higher?
- Context Recall: Was the required context retrieved?

Usage:
    evaluator = RagasEvaluator()
    results = evaluator.evaluate_dataset(test_cases)
    report = evaluator.generate_report(results)
"""

import os
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
class RagasResult:
    """Result from RAGAS evaluation."""
    query: str
    response: str
    faithfulness: float       # 0-1: Claims supported by context
    answer_relevancy: float   # 0-1: Response addresses question
    context_precision: float  # 0-1: Relevant chunks ranked higher
    context_recall: float     # 0-1: Required context retrieved
    overall_score: float      # Weighted average
    passed: bool
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RagasEvaluator:
    """
    RAGAS-based evaluator for detailed offline RAG analysis.
    
    RAGAS provides more granular metrics than Azure evaluators,
    particularly useful for:
    - Detecting subtle hallucinations (faithfulness)
    - Evaluating retrieval quality (context precision/recall)
    - Identifying answer quality issues (relevancy)
    
    Thresholds:
        - Faithfulness >= 0.8 (high bar for policy accuracy)
        - Answer Relevancy >= 0.7
        - Context Precision >= 0.7
        - Context Recall >= 0.7
    """
    
    FAITHFULNESS_THRESHOLD = 0.8
    RELEVANCY_THRESHOLD = 0.7
    PRECISION_THRESHOLD = 0.7
    RECALL_THRESHOLD = 0.7
    
    # Weights for overall score
    WEIGHTS = {
        "faithfulness": 0.35,      # Most important for policy RAG
        "answer_relevancy": 0.25,
        "context_precision": 0.20,
        "context_recall": 0.20,
    }
    
    def __init__(
        self,
        openai_api_key: Optional[str] = None,
        azure_endpoint: Optional[str] = None,
        azure_api_key: Optional[str] = None,
        deployment_name: Optional[str] = None,
        use_azure: bool = True
    ):
        """
        Initialize RAGAS evaluator.
        
        Args:
            openai_api_key: OpenAI API key (if not using Azure)
            azure_endpoint: Azure OpenAI endpoint
            azure_api_key: Azure OpenAI API key
            deployment_name: Model deployment name
            use_azure: Whether to use Azure OpenAI (default True)
        """
        self.use_azure = use_azure
        
        if use_azure:
            self.azure_endpoint = azure_endpoint or os.getenv("AOAI_ENDPOINT")
            self.azure_api_key = azure_api_key or os.getenv("AOAI_API_KEY")
            self.deployment_name = deployment_name or os.getenv("AOAI_CHAT_DEPLOYMENT", "gpt-4.1")
        else:
            self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY")
        
        self._metrics_initialized = False
        self._metrics = None
    
    def _init_ragas(self):
        """Lazy-load RAGAS metrics and configure LLM."""
        if self._metrics_initialized:
            return
        
        try:
            from ragas import evaluate
            from ragas.metrics import (
                faithfulness,
                answer_relevancy,
                context_precision,
                context_recall,
            )
            from ragas.llms import LangchainLLMWrapper
            from langchain_openai import AzureChatOpenAI, ChatOpenAI
            
            # Configure LLM for RAGAS
            if self.use_azure:
                llm = AzureChatOpenAI(
                    azure_endpoint=self.azure_endpoint,
                    api_key=self.azure_api_key,
                    azure_deployment=self.deployment_name,
                    api_version="2024-08-01-preview",
                )
            else:
                llm = ChatOpenAI(
                    api_key=self.openai_api_key,
                    model="gpt-4",
                )
            
            # Wrap for RAGAS
            ragas_llm = LangchainLLMWrapper(llm)
            
            # Configure metrics with our LLM
            faithfulness.llm = ragas_llm
            answer_relevancy.llm = ragas_llm
            context_precision.llm = ragas_llm
            context_recall.llm = ragas_llm
            
            self._metrics = [
                faithfulness,
                answer_relevancy,
                context_precision,
                context_recall,
            ]
            self._evaluate_fn = evaluate
            self._metrics_initialized = True
            
            logger.info("RAGAS metrics initialized with Azure OpenAI" if self.use_azure else "RAGAS metrics initialized")
            
        except ImportError as e:
            raise ImportError(
                "RAGAS dependencies not installed. Run: pip install ragas langchain-openai"
            ) from e
    
    def evaluate_single(
        self,
        query: str,
        response: str,
        contexts: List[str],
        ground_truth: Optional[str] = None
    ) -> RagasResult:
        """
        Evaluate a single query-response pair.
        
        Args:
            query: The user's question
            response: The agent's response
            contexts: Retrieved document chunks
            ground_truth: Expected answer (required for context_recall)
            
        Returns:
            RagasResult with all metric scores
        """
        self._init_ragas()
        
        from datasets import Dataset
        
        # RAGAS expects specific column names
        data = {
            "question": [query],
            "answer": [response],
            "contexts": [contexts],
            "ground_truth": [ground_truth or response],  # Use response if no ground truth
        }
        
        dataset = Dataset.from_dict(data)
        
        try:
            results = self._evaluate_fn(
                dataset,
                metrics=self._metrics,
            )
            
            # Extract scores from results
            scores = results.to_pandas().iloc[0].to_dict()
            
            faithfulness_score = scores.get("faithfulness", 0.0)
            relevancy_score = scores.get("answer_relevancy", 0.0)
            precision_score = scores.get("context_precision", 0.0)
            recall_score = scores.get("context_recall", 0.0)
            
        except Exception as e:
            logger.error(f"RAGAS evaluation failed: {e}")
            # Return zeros on failure
            faithfulness_score = 0.0
            relevancy_score = 0.0
            precision_score = 0.0
            recall_score = 0.0
        
        # Calculate weighted overall score
        overall_score = (
            self.WEIGHTS["faithfulness"] * faithfulness_score +
            self.WEIGHTS["answer_relevancy"] * relevancy_score +
            self.WEIGHTS["context_precision"] * precision_score +
            self.WEIGHTS["context_recall"] * recall_score
        )
        
        # Determine pass/fail
        passed = (
            faithfulness_score >= self.FAITHFULNESS_THRESHOLD and
            relevancy_score >= self.RELEVANCY_THRESHOLD and
            precision_score >= self.PRECISION_THRESHOLD and
            recall_score >= self.RECALL_THRESHOLD
        )
        
        return RagasResult(
            query=query,
            response=response[:500] + "..." if len(response) > 500 else response,
            faithfulness=round(faithfulness_score, 3),
            answer_relevancy=round(relevancy_score, 3),
            context_precision=round(precision_score, 3),
            context_recall=round(recall_score, 3),
            overall_score=round(overall_score, 3),
            passed=passed
        )
    
    def evaluate_dataset(
        self,
        test_cases: List[Dict[str, Any]],
        batch_size: int = 10
    ) -> List[RagasResult]:
        """
        Evaluate a batch of test cases.
        
        For better performance, RAGAS can evaluate batches together.
        
        Args:
            test_cases: List of dicts with: query, response, contexts, ground_truth
            batch_size: Number of cases to evaluate at once
            
        Returns:
            List of RagasResult objects
        """
        self._init_ragas()
        
        from datasets import Dataset
        
        # Prepare dataset in RAGAS format
        data = {
            "question": [c["query"] for c in test_cases],
            "answer": [c["response"] for c in test_cases],
            "contexts": [c.get("contexts", []) for c in test_cases],
            "ground_truth": [c.get("ground_truth", c["response"]) for c in test_cases],
        }
        
        dataset = Dataset.from_dict(data)
        
        try:
            logger.info(f"Evaluating {len(test_cases)} test cases with RAGAS...")
            results = self._evaluate_fn(
                dataset,
                metrics=self._metrics,
            )
            
            df = results.to_pandas()
            
            ragas_results = []
            for i, row in df.iterrows():
                faithfulness_score = row.get("faithfulness", 0.0)
                relevancy_score = row.get("answer_relevancy", 0.0)
                precision_score = row.get("context_precision", 0.0)
                recall_score = row.get("context_recall", 0.0)
                
                overall_score = (
                    self.WEIGHTS["faithfulness"] * faithfulness_score +
                    self.WEIGHTS["answer_relevancy"] * relevancy_score +
                    self.WEIGHTS["context_precision"] * precision_score +
                    self.WEIGHTS["context_recall"] * recall_score
                )
                
                passed = (
                    faithfulness_score >= self.FAITHFULNESS_THRESHOLD and
                    relevancy_score >= self.RELEVANCY_THRESHOLD and
                    precision_score >= self.PRECISION_THRESHOLD and
                    recall_score >= self.RECALL_THRESHOLD
                )
                
                ragas_results.append(RagasResult(
                    query=test_cases[i]["query"],
                    response=test_cases[i]["response"][:500] + "..." if len(test_cases[i]["response"]) > 500 else test_cases[i]["response"],
                    faithfulness=round(faithfulness_score, 3),
                    answer_relevancy=round(relevancy_score, 3),
                    context_precision=round(precision_score, 3),
                    context_recall=round(recall_score, 3),
                    overall_score=round(overall_score, 3),
                    passed=passed
                ))
            
            return ragas_results
            
        except Exception as e:
            logger.error(f"Batch RAGAS evaluation failed: {e}")
            # Fallback to individual evaluation
            return [
                self.evaluate_single(
                    c["query"],
                    c["response"],
                    c.get("contexts", []),
                    c.get("ground_truth")
                )
                for c in test_cases
            ]
    
    def generate_report(self, results: List[RagasResult]) -> Dict[str, Any]:
        """Generate comprehensive report from RAGAS results."""
        if not results:
            return {"error": "No results to report"}
        
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        
        avg_faithfulness = sum(r.faithfulness for r in results) / total
        avg_relevancy = sum(r.answer_relevancy for r in results) / total
        avg_precision = sum(r.context_precision for r in results) / total
        avg_recall = sum(r.context_recall for r in results) / total
        avg_overall = sum(r.overall_score for r in results) / total
        
        # Identify problem areas
        low_faithfulness = [r for r in results if r.faithfulness < self.FAITHFULNESS_THRESHOLD]
        low_relevancy = [r for r in results if r.answer_relevancy < self.RELEVANCY_THRESHOLD]
        low_precision = [r for r in results if r.context_precision < self.PRECISION_THRESHOLD]
        low_recall = [r for r in results if r.context_recall < self.RECALL_THRESHOLD]
        
        return {
            "summary": {
                "total_cases": total,
                "passed": passed,
                "failed": total - passed,
                "pass_rate": f"{(passed/total)*100:.1f}%",
                "overall_score": round(avg_overall, 3)
            },
            "average_scores": {
                "faithfulness": round(avg_faithfulness, 3),
                "answer_relevancy": round(avg_relevancy, 3),
                "context_precision": round(avg_precision, 3),
                "context_recall": round(avg_recall, 3)
            },
            "thresholds": {
                "faithfulness": self.FAITHFULNESS_THRESHOLD,
                "answer_relevancy": self.RELEVANCY_THRESHOLD,
                "context_precision": self.PRECISION_THRESHOLD,
                "context_recall": self.RECALL_THRESHOLD
            },
            "problem_areas": {
                "hallucination_risk": len(low_faithfulness),
                "poor_relevancy": len(low_relevancy),
                "retrieval_precision_issues": len(low_precision),
                "retrieval_recall_issues": len(low_recall)
            },
            "failed_cases": [r.to_dict() for r in results if not r.passed][:10],
            "hallucination_risk_cases": [
                {"query": r.query, "faithfulness": r.faithfulness}
                for r in low_faithfulness[:5]
            ]
        }


# CLI for standalone usage
if __name__ == "__main__":
    import json
    
    evaluator = RagasEvaluator()
    
    result = evaluator.evaluate_single(
        query="Who can accept verbal orders?",
        response="According to RUSH policy, verbal orders may be accepted by registered nurses, pharmacists, and respiratory therapists.",
        contexts=[
            "Verbal orders may be accepted by: Registered Nurses (RN), Pharmacists, Respiratory Therapists...",
            "The receiving practitioner must read back and verify the order..."
        ],
        ground_truth="Registered nurses, pharmacists, and respiratory therapists can accept verbal orders."
    )
    
    print(json.dumps(result.to_dict(), indent=2))

