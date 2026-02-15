"""
DeepEval metrics for RUSH PolicyTech RAG evaluation.

Uses DeepEval with Azure OpenAI as the judge model for:
- Faithfulness: Are claims in the response supported by context?
- Answer Relevancy: Does the response address the question?
- Contextual Precision: Are relevant chunks ranked higher?
- Policy Citation Accuracy: Are all claims attributed to policy names/numbers?
- Procedural Completeness: Are all procedural steps included?

Usage:
    from app.evaluation.metrics import evaluate_single, RUSH_POLICY_METRICS

    result = evaluate_single(
        query="What is the code blue policy?",
        response="According to policy X123...",
        context=["Context chunk 1", "Context chunk 2"],
    )
"""

import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

# Load environment
env_path = Path(__file__).resolve().parent.parent.parent.parent.parent / ".env"
load_dotenv(env_path)

# ============================================================================
# DeepEval Timeout Configuration (MUST be set before importing deepeval)
# ============================================================================
# Increase timeout for DeepEval metrics to avoid timeouts during LLM evaluation
# Default is 60 seconds, but Azure OpenAI can take longer for complex evaluations
os.environ.setdefault(
    "DEEPEVAL_PER_ATTEMPT_TIMEOUT_SECONDS_OVERRIDE", "240"
)  # 4 minutes per attempt
os.environ.setdefault("DEEPEVAL_TIMEOUT_SECONDS", "600")  # 10 minutes total
os.environ.setdefault("DEEPEVAL_MAX_RETRIES", "3")  # Retry up to 3 times

logger = logging.getLogger(__name__)


@dataclass
class DeepEvalResult:
    """Result from DeepEval evaluation."""

    query: str
    response: str
    faithfulness: float
    answer_relevancy: float
    context_precision: float
    policy_citation: float
    procedural_completeness: float
    overall_score: float
    passed: bool
    failure_reasons: List[str] = field(default_factory=list)
    metric_details: Dict[str, Any] = field(default_factory=dict)
    # Extended metrics (available when expected_output provided)
    context_recall: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DeepEvalMetrics:
    """
    DeepEval metric configuration for RUSH Policy RAG.

    Uses Azure OpenAI (gpt-4.1) as the judge model.
    Thresholds are calibrated for healthcare policy accuracy.
    """

    # Healthcare-calibrated thresholds (higher than default)
    FAITHFULNESS_THRESHOLD = 0.85
    ANSWER_RELEVANCY_THRESHOLD = 0.70
    # Context precision threshold lowered from 0.75 to 0.60 (Jan 2026)
    # Rationale: Healthcare RAG often retrieves related-but-tangential docs
    # that support the answer without being the primary source.
    # Combined with lost-in-middle reordering, this threshold is calibrated
    # to balance precision vs recall in policy retrieval.
    CONTEXT_PRECISION_THRESHOLD = 0.60
    CONTEXT_RECALL_THRESHOLD = 0.70  # When expected_output is available
    POLICY_CITATION_THRESHOLD = 0.80
    PROCEDURAL_COMPLETENESS_THRESHOLD = 0.75

    # Weights for overall score
    WEIGHTS = {
        "faithfulness": 0.30,  # Most important - no hallucinations
        "policy_citation": 0.25,  # Citations required for compliance
        "answer_relevancy": 0.20,  # Must address the question
        "context_precision": 0.15,  # Retrieval quality
        "procedural_completeness": 0.10,  # Complete procedures
    }

    def __init__(
        self,
        azure_endpoint: Optional[str] = None,
        azure_api_key: Optional[str] = None,
        deployment_name: Optional[str] = None,
    ):
        """
        Initialize DeepEval metrics with Azure OpenAI.

        Args:
            azure_endpoint: Azure OpenAI endpoint
            azure_api_key: Azure OpenAI API key
            deployment_name: Model deployment name (default: gpt-4.1-mini for eval)
        """
        self.azure_endpoint = azure_endpoint or os.getenv("AOAI_ENDPOINT")
        self.azure_api_key = azure_api_key or os.getenv("AOAI_API_KEY")
        # Use gpt-4.1-mini for evaluation (faster, cheaper, avoids token limits)
        # Falls back to AOAI_CHAT_DEPLOYMENT if AOAI_EVAL_DEPLOYMENT not set
        self.deployment_name = deployment_name or os.getenv(
            "AOAI_EVAL_DEPLOYMENT", os.getenv("AOAI_CHAT_DEPLOYMENT", "gpt-4.1-mini")
        )

        self._metrics_initialized = False
        self._metrics = None
        self._azure_model = None

    def _init_metrics(self):
        """Lazy-load DeepEval metrics and configure Azure OpenAI judge."""
        if self._metrics_initialized:
            return

        try:
            from deepeval.metrics import (
                ContextualPrecisionMetric,  # Requires expected_output
            )
            from deepeval.metrics import (
                ContextualRecallMetric,  # Requires expected_output
            )
            from deepeval.metrics import (
                ContextualRelevancyMetric,  # Works without expected_output
            )
            from deepeval.metrics import (
                AnswerRelevancyMetric,
                FaithfulnessMetric,
                GEval,
            )
            from deepeval.models import AzureOpenAIModel
            from deepeval.test_case import LLMTestCaseParams

            # Configure Azure OpenAI as judge model
            # DeepEval's AzureOpenAIModel uses base_url (not azure_endpoint) and requires model param
            # Note: max_completion_tokens is not supported - model may hit token limits on complex evaluations
            self._azure_model = AzureOpenAIModel(
                model=self.deployment_name,
                deployment_name=self.deployment_name,
                base_url=self.azure_endpoint,
                api_key=self.azure_api_key,
                api_version="2024-08-01-preview",
            )

            # Initialize metrics with healthcare thresholds
            self._faithfulness = FaithfulnessMetric(
                threshold=self.FAITHFULNESS_THRESHOLD,
                model=self._azure_model,
                include_reason=True,
            )

            self._answer_relevancy = AnswerRelevancyMetric(
                threshold=self.ANSWER_RELEVANCY_THRESHOLD,
                model=self._azure_model,
                include_reason=True,
            )

            # ContextualRelevancyMetric works without expected_output
            self._context_relevancy = ContextualRelevancyMetric(
                threshold=self.CONTEXT_PRECISION_THRESHOLD,
                model=self._azure_model,
                include_reason=True,
            )

            # ContextualPrecisionMetric and ContextualRecallMetric require expected_output
            # These are used when ground truth is available (e.g., from generated test dataset)
            self._context_precision_with_expected = ContextualPrecisionMetric(
                threshold=self.CONTEXT_PRECISION_THRESHOLD,
                model=self._azure_model,
                include_reason=True,
            )
            self._context_recall = ContextualRecallMetric(
                threshold=0.70,  # Context recall threshold
                model=self._azure_model,
                include_reason=True,
            )

            # Custom metric: Policy Citation Accuracy
            # Note: GEval requires LLMTestCaseParams enum values, not strings
            self._policy_citation = GEval(
                name="Policy Citation Accuracy",
                criteria="""Score the response based on whether all factual claims are properly
                attributed to specific policy names, reference numbers, or document titles.

                Scoring:
                - 1.0: All factual claims have explicit citations to policy documents
                - 0.8: Most claims are cited, minor omissions
                - 0.6: Some important claims lack citations
                - 0.4: Many claims are unattributed
                - 0.2: Very few or no citations provided
                - 0.0: Response makes claims with no source attribution

                Note: General statements like "according to RUSH policy" without specific
                policy names/numbers should be scored lower.""",
                evaluation_params=[
                    LLMTestCaseParams.INPUT,
                    LLMTestCaseParams.ACTUAL_OUTPUT,
                    LLMTestCaseParams.RETRIEVAL_CONTEXT,
                ],
                model=self._azure_model,
                threshold=self.POLICY_CITATION_THRESHOLD,
            )

            # Custom metric: Procedural Completeness
            self._procedural_completeness = GEval(
                name="Procedural Completeness",
                criteria="""Score the response based on whether it includes all required steps
                when describing a procedure or process from RUSH policies.

                Scoring:
                - 1.0: All steps from the source documents are included in correct order
                - 0.8: Most steps included, minor omissions of non-critical details
                - 0.6: Key steps included but some important ones missing
                - 0.4: Only partial procedure provided
                - 0.2: Critical steps missing that could lead to errors
                - 0.0: Procedure is incomplete or inaccurate

                If the query is not about a procedure, score 1.0 (not applicable).""",
                evaluation_params=[
                    LLMTestCaseParams.INPUT,
                    LLMTestCaseParams.ACTUAL_OUTPUT,
                    LLMTestCaseParams.RETRIEVAL_CONTEXT,
                ],
                model=self._azure_model,
                threshold=self.PROCEDURAL_COMPLETENESS_THRESHOLD,
            )

            # Core metrics (work without expected_output)
            self._core_metrics = {
                "faithfulness": self._faithfulness,
                "answer_relevancy": self._answer_relevancy,
                "context_precision": self._context_relevancy,  # ContextualRelevancy
                "policy_citation": self._policy_citation,
                "procedural_completeness": self._procedural_completeness,
            }

            # Extended metrics (require expected_output)
            self._extended_metrics = {
                "context_precision": self._context_precision_with_expected,
                "context_recall": self._context_recall,
            }

            # Default to core metrics
            self._metrics = self._core_metrics

            self._metrics_initialized = True
            logger.info("DeepEval metrics initialized with Azure OpenAI judge model")

        except ImportError as e:
            raise ImportError(
                "DeepEval not installed. Run: pip install deepeval>=0.21.0"
            ) from e

    # Maximum characters per context chunk to prevent token overflow
    MAX_CONTEXT_CHARS = 4000
    MAX_TOTAL_CONTEXT_CHARS = 12000  # Total across all chunks

    def _truncate_context(self, context: List[str]) -> List[str]:
        """
        Truncate context to prevent token overflow in evaluation.

        Azure OpenAI has token limits that can cause FaithfulnessMetric to fail
        when context is too long. This truncates individual chunks and total context.
        """
        truncated = []
        total_chars = 0

        for chunk in context:
            # Truncate individual chunk
            if len(chunk) > self.MAX_CONTEXT_CHARS:
                chunk = chunk[: self.MAX_CONTEXT_CHARS] + "..."

            # Check total limit
            if total_chars + len(chunk) > self.MAX_TOTAL_CONTEXT_CHARS:
                remaining = self.MAX_TOTAL_CONTEXT_CHARS - total_chars
                if remaining > 100:  # Only add if meaningful content remains
                    truncated.append(chunk[:remaining] + "...")
                break

            truncated.append(chunk)
            total_chars += len(chunk)

        return truncated

    def evaluate_test_case(
        self,
        query: str,
        response: str,
        context: List[str],
        expected_output: Optional[str] = None,
    ) -> DeepEvalResult:
        """
        Evaluate a single test case with all metrics.

        Args:
            query: The user's question
            response: The agent's response
            context: Retrieved document chunks
            expected_output: Expected/ground truth answer (optional)

        Returns:
            DeepEvalResult with all metric scores
        """
        self._init_metrics()

        from deepeval.test_case import LLMTestCase

        # Truncate context to prevent token overflow
        truncated_context = self._truncate_context(context)

        # Create DeepEval test case
        test_case = LLMTestCase(
            input=query,
            actual_output=response,
            retrieval_context=truncated_context,
            expected_output=expected_output,
        )

        scores = {}
        failure_reasons = []
        metric_details = {}

        # Select metrics based on whether expected_output is available
        # Core metrics always run; extended metrics (precision/recall) need expected_output
        metrics_to_run = dict(self._core_metrics)

        if expected_output:
            # Replace context_precision with ContextualPrecision and add ContextualRecall
            metrics_to_run["context_precision"] = self._context_precision_with_expected
            metrics_to_run["context_recall"] = self._context_recall
            logger.debug("Using extended metrics (expected_output provided)")
        else:
            logger.debug("Using core metrics only (no expected_output)")

        # Run each metric
        for name, metric in metrics_to_run.items():
            try:
                metric.measure(test_case)
                scores[name] = metric.score if metric.score is not None else 0.0
                metric_details[name] = {
                    "score": scores[name],
                    "threshold": metric.threshold,
                    "passed": (
                        metric.is_successful()
                        if hasattr(metric, "is_successful")
                        else scores[name] >= metric.threshold
                    ),
                    "reason": metric.reason if hasattr(metric, "reason") else None,
                }

                if scores[name] < metric.threshold:
                    reason = (
                        metric.reason
                        if hasattr(metric, "reason") and metric.reason
                        else f"{name} below threshold"
                    )
                    failure_reasons.append(f"{name}: {reason}")

            except Exception as e:
                error_msg = str(e)
                logger.warning(f"Metric {name} failed: {error_msg}")

                # Handle token limit errors gracefully
                if "length limit was reached" in error_msg:
                    # Model hit token limit - this can happen with complex faithfulness evaluation
                    # Log as warning but don't treat as complete failure
                    logger.warning(
                        f"Metric {name} hit token limit - evaluation inconclusive"
                    )
                    scores[name] = 0.5  # Inconclusive score
                    metric_details[name] = {
                        "score": 0.5,
                        "error": "token_limit_reached",
                        "note": "Evaluation inconclusive due to model token limit. Manual review recommended.",
                    }
                    failure_reasons.append(
                        f"{name}: evaluation inconclusive (token limit)"
                    )
                else:
                    scores[name] = 0.0
                    metric_details[name] = {"error": error_msg}
                    failure_reasons.append(f"{name}: evaluation error")

        # Calculate weighted overall score
        overall_score = sum(
            self.WEIGHTS.get(name, 0) * score for name, score in scores.items()
        )

        # Determine pass/fail based on all thresholds
        passed = all(
            scores.get(name, 0) >= getattr(self, f"{name.upper()}_THRESHOLD", 0.7)
            for name in [
                "faithfulness",
                "answer_relevancy",
                "context_precision",
                "policy_citation",
                "procedural_completeness",
            ]
        )

        return DeepEvalResult(
            query=query,
            response=response[:500] + "..." if len(response) > 500 else response,
            faithfulness=round(scores.get("faithfulness", 0), 3),
            answer_relevancy=round(scores.get("answer_relevancy", 0), 3),
            context_precision=round(scores.get("context_precision", 0), 3),
            policy_citation=round(scores.get("policy_citation", 0), 3),
            procedural_completeness=round(scores.get("procedural_completeness", 0), 3),
            overall_score=round(overall_score, 3),
            passed=passed,
            failure_reasons=failure_reasons,
            metric_details=metric_details,
            context_recall=(
                round(scores.get("context_recall", 0), 3)
                if "context_recall" in scores
                else None
            ),
        )

    def evaluate_batch(
        self,
        test_cases: List[Dict[str, Any]],
    ) -> List[DeepEvalResult]:
        """
        Evaluate a batch of test cases.

        Args:
            test_cases: List of dicts with: query, response, context, expected_output

        Returns:
            List of DeepEvalResult objects
        """
        results = []
        for i, tc in enumerate(test_cases):
            logger.info(f"Evaluating test case {i+1}/{len(test_cases)}")
            result = self.evaluate_test_case(
                query=tc["query"],
                response=tc["response"],
                context=tc.get("context", []),
                expected_output=tc.get("expected_output"),
            )
            results.append(result)
        return results

    def generate_report(self, results: List[DeepEvalResult]) -> Dict[str, Any]:
        """Generate comprehensive report from DeepEval results."""
        if not results:
            return {"error": "No results to report"}

        total = len(results)
        passed = sum(1 for r in results if r.passed)

        # Calculate averages
        avg_scores = {
            "faithfulness": sum(r.faithfulness for r in results) / total,
            "answer_relevancy": sum(r.answer_relevancy for r in results) / total,
            "context_precision": sum(r.context_precision for r in results) / total,
            "policy_citation": sum(r.policy_citation for r in results) / total,
            "procedural_completeness": sum(r.procedural_completeness for r in results)
            / total,
            "overall": sum(r.overall_score for r in results) / total,
        }

        # Add context_recall if available (when expected_output was provided)
        recall_values = [
            r.context_recall for r in results if r.context_recall is not None
        ]
        if recall_values:
            avg_scores["context_recall"] = sum(recall_values) / len(recall_values)

        # Identify problem areas
        low_faithfulness = [
            r for r in results if r.faithfulness < self.FAITHFULNESS_THRESHOLD
        ]
        low_citation = [
            r for r in results if r.policy_citation < self.POLICY_CITATION_THRESHOLD
        ]

        return {
            "summary": {
                "total_cases": total,
                "passed": passed,
                "failed": total - passed,
                "pass_rate": f"{(passed/total)*100:.1f}%",
            },
            "average_scores": {k: round(v, 3) for k, v in avg_scores.items()},
            "thresholds": {
                "faithfulness": self.FAITHFULNESS_THRESHOLD,
                "answer_relevancy": self.ANSWER_RELEVANCY_THRESHOLD,
                "context_precision": self.CONTEXT_PRECISION_THRESHOLD,
                "context_recall": self.CONTEXT_RECALL_THRESHOLD,
                "policy_citation": self.POLICY_CITATION_THRESHOLD,
                "procedural_completeness": self.PROCEDURAL_COMPLETENESS_THRESHOLD,
            },
            "problem_areas": {
                "hallucination_risk": len(low_faithfulness),
                "citation_issues": len(low_citation),
            },
            "failed_cases": [r.to_dict() for r in results if not r.passed][:10],
            "hallucination_risk_cases": [
                {
                    "query": r.query,
                    "faithfulness": r.faithfulness,
                    "reasons": r.failure_reasons,
                }
                for r in low_faithfulness[:5]
            ],
        }


# Singleton instance for convenience
_default_metrics: Optional[DeepEvalMetrics] = None


def get_metrics() -> DeepEvalMetrics:
    """Get or create default DeepEvalMetrics instance."""
    global _default_metrics
    if _default_metrics is None:
        _default_metrics = DeepEvalMetrics()
    return _default_metrics


def evaluate_single(
    query: str,
    response: str,
    context: List[str],
    expected_output: Optional[str] = None,
) -> DeepEvalResult:
    """
    Convenience function to evaluate a single test case.

    Args:
        query: The user's question
        response: The agent's response
        context: Retrieved document chunks
        expected_output: Expected answer (optional)

    Returns:
        DeepEvalResult with all metric scores
    """
    return get_metrics().evaluate_test_case(query, response, context, expected_output)


def evaluate_batch(test_cases: List[Dict[str, Any]]) -> List[DeepEvalResult]:
    """
    Convenience function to evaluate multiple test cases.

    Args:
        test_cases: List of dicts with query, response, context, expected_output

    Returns:
        List of DeepEvalResult objects
    """
    return get_metrics().evaluate_batch(test_cases)


# Export metric definitions for reference
RUSH_POLICY_METRICS = {
    "faithfulness": {
        "threshold": DeepEvalMetrics.FAITHFULNESS_THRESHOLD,
        "weight": DeepEvalMetrics.WEIGHTS["faithfulness"],
        "description": "Claims in response must be supported by retrieved context",
    },
    "answer_relevancy": {
        "threshold": DeepEvalMetrics.ANSWER_RELEVANCY_THRESHOLD,
        "weight": DeepEvalMetrics.WEIGHTS["answer_relevancy"],
        "description": "Response must address the user's question",
    },
    "context_precision": {
        "threshold": DeepEvalMetrics.CONTEXT_PRECISION_THRESHOLD,
        "weight": DeepEvalMetrics.WEIGHTS["context_precision"],
        "description": "Relevant chunks should be ranked higher in retrieval (threshold calibrated for healthcare RAG with lost-in-middle mitigation)",
    },
    "policy_citation": {
        "threshold": DeepEvalMetrics.POLICY_CITATION_THRESHOLD,
        "weight": DeepEvalMetrics.WEIGHTS["policy_citation"],
        "description": "All factual claims must cite specific policy names/numbers",
    },
    "procedural_completeness": {
        "threshold": DeepEvalMetrics.PROCEDURAL_COMPLETENESS_THRESHOLD,
        "weight": DeepEvalMetrics.WEIGHTS["procedural_completeness"],
        "description": "Procedures must include all required steps from source",
    },
}


if __name__ == "__main__":
    import json

    # Example usage
    result = evaluate_single(
        query="What is the code blue policy?",
        response="According to RUSH Policy RU-123, Code Blue is activated when...",
        context=[
            "Policy RU-123: Code Blue Protocol. When a patient experiences cardiac arrest...",
            "The Code Blue team consists of...",
        ],
        expected_output="Code Blue is activated for cardiac arrest situations.",
    )

    print(json.dumps(result.to_dict(), indent=2))
