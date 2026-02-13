#!/usr/bin/env python3
"""
RAGAS v0.4 Regression Testing Script for RUSH Policy RAG System

This script compares before/after RAGAS metrics using a curated golden test set.
It queries the live backend, builds RAGAS evaluation samples, and checks for regressions.

Usage:
    python scripts/run_ragas_regression.py --golden-set data/golden_test_set.json
    python scripts/run_ragas_regression.py --baseline reports/ragas/baseline.json
    python scripts/run_ragas_regression.py --save-baseline
    python scripts/run_ragas_regression.py --dry-run
    python scripts/run_ragas_regression.py --backend-url http://localhost:8000
    python scripts/run_ragas_regression.py --output reports/ragas/results.json

Environment Variables Required:
    AOAI_ENDPOINT
    AOAI_API_KEY
    AOAI_EVAL_DEPLOYMENT (default: gpt-4.1-mini)
    AOAI_EMBEDDING_DEPLOYMENT (default: text-embedding-3-large)
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Setup paths
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "apps" / "backend"))

# Import SSL fix for Azure OpenAI
import ssl_fix  # noqa: F401

import httpx
from dotenv import load_dotenv

# RAGAS v0.4 imports
from ragas import EvaluationDataset, SingleTurnSample, evaluate
from ragas.llms import LangchainLLMWrapper
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.metrics import (
    Faithfulness,
    LLMContextRecall,
    FactualCorrectness,
    ResponseRelevancy,
)
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings

# Load environment variables
load_dotenv(REPO_ROOT / ".env")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Regression thresholds
REGRESSION_THRESHOLDS = {
    "faithfulness": {"max_drop": 0.05, "severity": "FAIL"},
    "llm_context_recall": {"max_drop": 0.05, "severity": "FAIL"},
    "factual_correctness": {
        "warn_drop": 0.05,
        "fail_drop": 0.10,
        "severity": "WARN/FAIL",
    },
    "response_relevancy": {"max_drop": 0.05, "severity": "FAIL"},
}

# Default paths
DEFAULT_GOLDEN_SET = REPO_ROOT / "data" / "golden_test_set.json"
DEFAULT_BASELINE = REPO_ROOT / "reports" / "ragas" / "baseline.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "reports" / "ragas"


def setup_ragas_evaluator():
    """
    Setup RAGAS evaluator with Azure OpenAI as the LLM and embeddings.

    Returns:
        Tuple of (evaluator_llm, evaluator_embeddings, metrics)
    """
    logger.info("Setting up RAGAS evaluator with Azure OpenAI...")

    # Validate required environment variables
    required_vars = ["AOAI_ENDPOINT", "AOAI_API_KEY"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        raise ValueError(f"Missing required environment variables: {missing_vars}")

    # Setup Azure OpenAI LLM for evaluation
    azure_llm = AzureChatOpenAI(
        azure_endpoint=os.getenv("AOAI_ENDPOINT"),
        api_key=os.getenv("AOAI_API_KEY"),
        azure_deployment=os.getenv("AOAI_EVAL_DEPLOYMENT", "gpt-4.1-mini"),
        api_version="2024-08-01-preview",
        temperature=0,
    )
    evaluator_llm = LangchainLLMWrapper(azure_llm)

    # Setup Azure OpenAI Embeddings
    azure_embeddings = AzureOpenAIEmbeddings(
        azure_endpoint=os.getenv("AOAI_ENDPOINT"),
        api_key=os.getenv("AOAI_API_KEY"),
        azure_deployment=os.getenv("AOAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large"),
        api_version="2024-08-01-preview",
    )
    evaluator_embeddings = LangchainEmbeddingsWrapper(azure_embeddings)

    # Initialize metrics
    metrics = [
        Faithfulness(llm=evaluator_llm),
        LLMContextRecall(llm=evaluator_llm),
        FactualCorrectness(llm=evaluator_llm),
        ResponseRelevancy(llm=evaluator_llm, embeddings=evaluator_embeddings),
    ]

    logger.info(f"RAGAS evaluator setup complete with {len(metrics)} metrics")
    return evaluator_llm, evaluator_embeddings, metrics


async def query_backend(
    client: httpx.AsyncClient,
    backend_url: str,
    question: str,
    timeout: float = 60.0,
) -> Optional[Dict[str, Any]]:
    """
    Query the backend API with a question.

    Args:
        client: httpx AsyncClient
        backend_url: Backend base URL
        question: User question
        timeout: Request timeout in seconds

    Returns:
        Dict with response, evidence, and found status, or None on error
    """
    try:
        response = await client.post(
            f"{backend_url}/api/chat",
            json={"message": question},
            timeout=timeout,
        )

        if response.status_code == 200:
            data = response.json()
            return {
                "response": data.get("response", ""),
                "evidence": data.get("evidence", []),
                "found": data.get("found", True),
            }
        else:
            logger.warning(
                f"Backend returned status {response.status_code} for question: {question[:100]}"
            )
            return None

    except httpx.TimeoutException:
        logger.error(f"Timeout querying backend for question: {question[:100]}")
        return None
    except Exception as e:
        logger.error(f"Error querying backend: {e}")
        return None


def extract_contexts(evidence_list: List[Dict[str, Any]]) -> List[str]:
    """
    Extract text contexts from evidence list for RAGAS retrieved_contexts.

    Args:
        evidence_list: List of evidence dicts from backend

    Returns:
        List of text snippets
    """
    contexts = []
    for ev in evidence_list:
        if isinstance(ev, dict):
            # Try multiple field names
            snippet = (
                ev.get("snippet", "")
                or ev.get("content", "")
                or ev.get("text", "")
            )
            if snippet and len(snippet) > 10:
                contexts.append(snippet)
    return contexts


def load_golden_set(golden_set_path: Path) -> List[Dict[str, Any]]:
    """
    Load golden test set from JSON file.

    Args:
        golden_set_path: Path to golden test set JSON

    Returns:
        List of test cases
    """
    logger.info(f"Loading golden test set from {golden_set_path}")

    if not golden_set_path.exists():
        raise FileNotFoundError(f"Golden test set not found: {golden_set_path}")

    with open(golden_set_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Handle multiple formats: direct list, {"cases": [...]}, or {"test_cases": [...]}
    if isinstance(data, list):
        cases = data
    else:
        cases = data.get("test_cases", data.get("cases", []))

    logger.info(f"Loaded {len(cases)} test cases from golden set")
    return cases


async def run_evaluation(
    golden_set_path: Path,
    backend_url: str,
    rate_limit: float = 2.5,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Run RAGAS evaluation on golden test set.

    Args:
        golden_set_path: Path to golden test set
        backend_url: Backend URL
        rate_limit: Seconds to wait between queries
        dry_run: If True, skip live queries and evaluation

    Returns:
        Evaluation results dict
    """
    # Load golden test set
    golden_cases = load_golden_set(golden_set_path)

    # Analyze test set
    total_cases = len(golden_cases)
    expected_not_found_cases = [
        c for c in golden_cases if c.get("expected_not_found", False)
    ]
    evaluable_cases = [
        c for c in golden_cases if not c.get("expected_not_found", False)
    ]

    logger.info(f"Total cases: {total_cases}")
    logger.info(f"Expected not found cases (skipped from RAGAS): {len(expected_not_found_cases)}")
    logger.info(f"Evaluable cases: {len(evaluable_cases)}")

    # Print category breakdown
    categories = {}
    for case in golden_cases:
        cat = case.get("category", "unknown")
        categories[cat] = categories.get(cat, 0) + 1
    logger.info(f"Categories: {json.dumps(categories, indent=2)}")

    if dry_run:
        logger.info("DRY RUN MODE: Skipping live queries and evaluation")
        return {
            "version": "1.0",
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "golden_set": str(golden_set_path),
            "backend_url": backend_url,
            "dry_run": True,
            "total_cases": total_cases,
            "evaluable_cases": len(evaluable_cases),
            "skipped_cases": len(expected_not_found_cases),
            "categories": categories,
            "metrics": {
                "faithfulness": 0.85,
                "llm_context_recall": 0.80,
                "factual_correctness": 0.75,
                "response_relevancy": 0.82,
            },
            "note": "DRY RUN - Placeholder scores only",
        }

    # Setup RAGAS
    evaluator_llm, evaluator_embeddings, metrics = setup_ragas_evaluator()

    # Query backend for all cases
    logger.info(f"Querying backend at {backend_url} for {total_cases} cases...")
    samples = []
    per_case_results = []

    async with httpx.AsyncClient() as client:
        for idx, case in enumerate(golden_cases, 1):
            question = case.get("question", "")
            expected_answer = case.get("expected_answer", "")
            expected_not_found = case.get("expected_not_found", False)
            case_id = case.get("id", f"case_{idx}")
            category = case.get("category", "unknown")

            logger.info(f"[{idx}/{total_cases}] Processing case {case_id} ({category})")

            # Query backend
            backend_response = await query_backend(
                client, backend_url, question, timeout=60.0
            )

            if backend_response is None:
                logger.warning(f"Skipping case {case_id} due to backend error")
                per_case_results.append({
                    "case_id": case_id,
                    "question": question,
                    "category": category,
                    "status": "ERROR",
                    "error": "Backend query failed",
                })
                continue

            response_text = backend_response["response"]
            evidence = backend_response["evidence"]
            found = backend_response["found"]

            # Extract contexts
            contexts = extract_contexts(evidence)

            # Record per-case result
            per_case_results.append({
                "case_id": case_id,
                "question": question,
                "category": category,
                "expected_not_found": expected_not_found,
                "backend_found": found,
                "response_length": len(response_text),
                "num_contexts": len(contexts),
                "status": "QUERIED",
            })

            # Skip expected_not_found cases from RAGAS evaluation
            if expected_not_found:
                logger.info(f"Skipping RAGAS for case {case_id} (expected_not_found)")
                continue

            # Build RAGAS sample
            if not response_text or not contexts:
                logger.warning(
                    f"Case {case_id} has no response or contexts, skipping from RAGAS"
                )
                per_case_results[-1]["status"] = "SKIPPED_NO_RESPONSE"
                continue

            sample = SingleTurnSample(
                user_input=question,
                response=response_text,
                retrieved_contexts=contexts,
                reference=expected_answer,
            )
            samples.append(sample)
            per_case_results[-1]["status"] = "EVALUATED"

            # Rate limiting
            if idx < total_cases:
                await asyncio.sleep(rate_limit)

    logger.info(f"Collected {len(samples)} samples for RAGAS evaluation")

    if len(samples) == 0:
        logger.error("No samples to evaluate!")
        return {
            "version": "1.0",
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "golden_set": str(golden_set_path),
            "backend_url": backend_url,
            "total_cases": total_cases,
            "evaluated_cases": 0,
            "skipped_cases": total_cases,
            "error": "No valid samples for evaluation",
            "per_case_results": per_case_results,
        }

    # Run RAGAS evaluation
    logger.info(f"Running RAGAS evaluation with {len(metrics)} metrics...")
    dataset = EvaluationDataset(samples=samples)

    try:
        eval_result = evaluate(dataset=dataset, metrics=metrics)
        logger.info("RAGAS evaluation complete")
    except Exception as e:
        logger.error(f"RAGAS evaluation failed: {e}")
        return {
            "version": "1.0",
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "golden_set": str(golden_set_path),
            "backend_url": backend_url,
            "total_cases": total_cases,
            "evaluated_cases": len(samples),
            "skipped_cases": total_cases - len(samples),
            "error": f"RAGAS evaluation failed: {str(e)}",
            "per_case_results": per_case_results,
        }

    # Extract metrics
    metrics_dict = {
        "faithfulness": float(eval_result.get("faithfulness", 0.0)),
        "llm_context_recall": float(eval_result.get("context_recall", 0.0)),
        "factual_correctness": float(eval_result.get("factual_correctness", 0.0)),
        "response_relevancy": float(eval_result.get("answer_relevancy", 0.0)),
    }

    logger.info(f"Metrics: {json.dumps(metrics_dict, indent=2)}")

    # Build results
    results = {
        "version": "1.0",
        "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
        "golden_set": str(golden_set_path),
        "backend_url": backend_url,
        "total_cases": total_cases,
        "evaluated_cases": len(samples),
        "skipped_cases": total_cases - len(samples),
        "metrics": metrics_dict,
        "per_case_results": per_case_results,
    }

    return results


def compare_to_baseline(
    results: Dict[str, Any],
    baseline_path: Path,
) -> Dict[str, Any]:
    """
    Compare evaluation results to baseline and check for regressions.

    Args:
        results: Current evaluation results
        baseline_path: Path to baseline JSON

    Returns:
        Regression check results
    """
    if not baseline_path.exists():
        logger.warning(f"Baseline file not found: {baseline_path}")
        return {
            "baseline_file": str(baseline_path),
            "status": "NO_BASELINE",
            "message": "No baseline found for comparison",
        }

    logger.info(f"Comparing to baseline: {baseline_path}")

    with open(baseline_path, "r", encoding="utf-8") as f:
        baseline = json.load(f)

    baseline_metrics = baseline.get("metrics", {})
    current_metrics = results.get("metrics", {})

    deltas = {}
    overall_status = "PASS"
    failures = []
    warnings = []

    for metric_name, thresholds in REGRESSION_THRESHOLDS.items():
        baseline_val = baseline_metrics.get(metric_name, 0.0)
        current_val = current_metrics.get(metric_name, 0.0)
        delta = current_val - baseline_val

        # Determine status
        status = "PASS"
        severity = thresholds.get("severity", "FAIL")

        if severity == "FAIL":
            max_drop = thresholds.get("max_drop", 0.05)
            if delta < -max_drop:
                status = "FAIL"
                overall_status = "FAIL"
                failures.append(
                    f"{metric_name}: {baseline_val:.3f} → {current_val:.3f} (Δ {delta:.3f}, threshold: -{max_drop})"
                )
        elif severity == "WARN/FAIL":
            warn_drop = thresholds.get("warn_drop", 0.05)
            fail_drop = thresholds.get("fail_drop", 0.10)
            if delta < -fail_drop:
                status = "FAIL"
                overall_status = "FAIL"
                failures.append(
                    f"{metric_name}: {baseline_val:.3f} → {current_val:.3f} (Δ {delta:.3f}, threshold: -{fail_drop})"
                )
            elif delta < -warn_drop:
                status = "WARN"
                if overall_status == "PASS":
                    overall_status = "WARN"
                warnings.append(
                    f"{metric_name}: {baseline_val:.3f} → {current_val:.3f} (Δ {delta:.3f}, threshold: -{warn_drop})"
                )

        deltas[metric_name] = {
            "baseline": baseline_val,
            "current": current_val,
            "delta": delta,
            "status": status,
        }

    regression_check = {
        "baseline_file": str(baseline_path),
        "baseline_timestamp": baseline.get("timestamp", "unknown"),
        "status": overall_status,
        "deltas": deltas,
    }

    if failures:
        regression_check["failures"] = failures
    if warnings:
        regression_check["warnings"] = warnings

    logger.info(f"Regression check: {overall_status}")
    if failures:
        for failure in failures:
            logger.error(f"  FAIL: {failure}")
    if warnings:
        for warning in warnings:
            logger.warning(f"  WARN: {warning}")

    return regression_check


def save_results(
    results: Dict[str, Any],
    output_path: Path,
    save_baseline: bool = False,
):
    """
    Save evaluation results to JSON file.

    Args:
        results: Evaluation results dict
        output_path: Output file path
        save_baseline: If True, also save to baseline.json
    """
    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Save main results
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    logger.info(f"Results saved to {output_path}")

    # Save baseline if requested
    if save_baseline:
        baseline_path = output_path.parent / "baseline.json"
        with open(baseline_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        logger.info(f"Baseline saved to {baseline_path}")


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="RAGAS v0.4 Regression Testing for RUSH Policy RAG"
    )
    parser.add_argument(
        "--golden-set",
        type=Path,
        default=DEFAULT_GOLDEN_SET,
        help="Path to golden test set JSON",
    )
    parser.add_argument(
        "--backend-url",
        type=str,
        default="http://localhost:8000",
        help="Backend base URL",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        help="Path to baseline JSON for comparison",
    )
    parser.add_argument(
        "--save-baseline",
        action="store_true",
        help="Save results as new baseline",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output JSON path (default: reports/ragas/ragas_<timestamp>.json)",
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=2.5,
        help="Seconds to wait between backend queries (default: 2.5)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load golden set and print stats, but skip queries and evaluation",
    )

    args = parser.parse_args()

    # Set default output path
    if args.output is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        args.output = DEFAULT_OUTPUT_DIR / f"ragas_{timestamp}.json"

    # Set default baseline path if not provided
    if args.baseline is None and not args.dry_run:
        args.baseline = DEFAULT_BASELINE

    logger.info("=" * 80)
    logger.info("RAGAS v0.4 Regression Testing for RUSH Policy RAG")
    logger.info("=" * 80)
    logger.info(f"Golden set: {args.golden_set}")
    logger.info(f"Backend URL: {args.backend_url}")
    logger.info(f"Output: {args.output}")
    logger.info(f"Rate limit: {args.rate_limit}s")
    logger.info(f"Dry run: {args.dry_run}")
    if args.baseline:
        logger.info(f"Baseline: {args.baseline}")
    logger.info("=" * 80)

    # Run evaluation
    try:
        results = await run_evaluation(
            golden_set_path=args.golden_set,
            backend_url=args.backend_url,
            rate_limit=args.rate_limit,
            dry_run=args.dry_run,
        )
    except Exception as e:
        logger.error(f"Evaluation failed: {e}", exc_info=True)
        sys.exit(1)

    # Compare to baseline if provided and not dry run
    if not args.dry_run and args.baseline:
        regression_check = compare_to_baseline(results, args.baseline)
        results["regression_check"] = regression_check

        # Exit with error code if regression detected
        if regression_check.get("status") == "FAIL":
            logger.error("REGRESSION DETECTED - Exiting with error code 1")
            save_results(results, args.output, args.save_baseline)
            sys.exit(1)

    # Save results
    save_results(results, args.output, args.save_baseline)

    # Print summary
    logger.info("=" * 80)
    logger.info("EVALUATION COMPLETE")
    logger.info("=" * 80)
    logger.info(f"Total cases: {results.get('total_cases', 0)}")
    logger.info(f"Evaluated cases: {results.get('evaluated_cases', 0)}")
    logger.info(f"Skipped cases: {results.get('skipped_cases', 0)}")

    if "metrics" in results:
        logger.info("\nMetrics:")
        for metric_name, value in results["metrics"].items():
            logger.info(f"  {metric_name}: {value:.3f}")

    if "regression_check" in results:
        status = results["regression_check"]["status"]
        logger.info(f"\nRegression check: {status}")

    logger.info(f"\nResults saved to: {args.output}")
    logger.info("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
