#!/usr/bin/env python3
"""
Agent Reliability Evaluation for RUSH PolicyTech.

Evaluates agent responses using Azure AI native evaluators:
- GroundednessProEvaluator: Strict binary hallucination detection
- TaskAdherenceEvaluator: RISEN prompt compliance verification
- IntentResolutionEvaluator: User intent understanding validation
- ResponseCompletenessEvaluator: Response coverage verification

For healthcare RAG, all evaluators must pass for a response to be considered reliable.

Usage:
    # Run full agent evaluation (hallucination + adherence)
    python scripts/run_agent_evaluation.py

    # Hallucination detection only
    python scripts/run_agent_evaluation.py --mode hallucination

    # RISEN compliance only
    python scripts/run_agent_evaluation.py --mode adherence

    # Intent resolution only
    python scripts/run_agent_evaluation.py --mode intent

    # Response completeness only
    python scripts/run_agent_evaluation.py --mode completeness

    # Run ALL 4 evaluators with comparison matrix
    python scripts/run_agent_evaluation.py --mode all-evaluators

    # Use custom test dataset
    python scripts/run_agent_evaluation.py --dataset path/to/dataset.json

    # Export detailed results
    python scripts/run_agent_evaluation.py --output-json results.json --output-csv results.csv

    # Evaluate specific categories
    python scripts/run_agent_evaluation.py --category adversarial
"""

import ssl_fix  # Corporate proxy SSL fix - must be first import!

import asyncio
import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "backend"))

from dotenv import load_dotenv

# Load environment variables
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

from evaluation import (
    PolicyAgentEvaluator,
    TestDataset,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class Colors:
    """ANSI color codes for terminal output."""
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'


def print_colored(text: str, color: str = Colors.ENDC) -> None:
    """Print colored text to terminal."""
    print(f"{color}{text}{Colors.ENDC}")


class PolicyRAGClient:
    """Client to query the RAG agent for evaluation."""

    def __init__(self, backend_url: str = "http://localhost:8000"):
        self.backend_url = backend_url

    async def query(self, question: str) -> Dict[str, Any]:
        """
        Query the RAG agent and return response with context.

        Returns:
            dict with: response, contexts, sources
        """
        import aiohttp

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    f"{self.backend_url}/api/chat",
                    json={"message": question},
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as resp:
                    if resp.status != 200:
                        return {
                            "response": f"Error: {resp.status}",
                            "contexts": [],
                            "sources": []
                        }

                    data = await resp.json()

                    # Extract contexts from sources
                    contexts = []
                    if "sources" in data:
                        for source in data["sources"]:
                            if "content" in source:
                                contexts.append(source["content"])
                            elif "citation" in source:
                                contexts.append(source["citation"])

                    return {
                        "response": data.get("response", ""),
                        "contexts": contexts,
                        "sources": data.get("sources", [])
                    }
            except Exception as e:
                logger.error(f"Query failed: {e}")
                return {
                    "response": f"Error: {str(e)}",
                    "contexts": [],
                    "sources": []
                }


async def get_rag_responses(
    test_cases: List[Dict[str, Any]],
    backend_url: str = "http://localhost:8000"
) -> List[Dict[str, Any]]:
    """Query the RAG agent for all test cases."""
    client = PolicyRAGClient(backend_url)
    responses = []

    for i, test in enumerate(test_cases):
        logger.info(f"Querying test case {i+1}/{len(test_cases)}: {test['query'][:50]}...")
        response = await client.query(test["query"])
        responses.append(response)

        # Small delay to avoid rate limiting
        await asyncio.sleep(0.5)

    return responses


async def run_hallucination_check(
    evaluator: PolicyAgentEvaluator,
    test_cases: List[Dict[str, Any]],
    rag_responses: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Run GroundednessProEvaluator for strict hallucination detection."""
    logger.info("Running Hallucination Detection (GroundednessProEvaluator)...")

    results = []
    for i, (test, response) in enumerate(zip(test_cases, rag_responses)):
        logger.info(f"Checking hallucination {i+1}/{len(test_cases)}...")

        result = await evaluator.check_hallucination(
            query=test["query"],
            response=response["response"],
            context=response["contexts"]
        )
        results.append({
            "query": test["query"],
            "response": response["response"][:300],
            "is_grounded": result.is_grounded,
            "reason": result.reason,
            "ungrounded_claims": result.ungrounded_claims,
            "category": test.get("metadata", {}).get("category", "unknown")
        })

    # Summary
    grounded = sum(1 for r in results if r["is_grounded"])
    total = len(results)

    return {
        "mode": "hallucination",
        "evaluator": "GroundednessProEvaluator",
        "summary": {
            "total_cases": total,
            "grounded": grounded,
            "hallucinations": total - grounded,
            "grounding_rate": f"{(grounded/total)*100:.1f}%"
        },
        "results": results,
        "hallucination_cases": [r for r in results if not r["is_grounded"]]
    }


async def run_adherence_check(
    evaluator: PolicyAgentEvaluator,
    test_cases: List[Dict[str, Any]],
    rag_responses: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Run TaskAdherenceEvaluator for RISEN prompt compliance."""
    logger.info("Running RISEN Compliance Check (TaskAdherenceEvaluator)...")

    results = []
    for i, (test, response) in enumerate(zip(test_cases, rag_responses)):
        logger.info(f"Checking adherence {i+1}/{len(test_cases)}...")

        result = await evaluator.check_task_adherence(
            query=test["query"],
            response=response["response"]
        )
        results.append({
            "query": test["query"],
            "response": response["response"][:300],
            "score": result.adherence_score,
            "passed": result.passed,
            "reason": result.reason,
            "violations": result.violations,
            "category": test.get("metadata", {}).get("category", "unknown")
        })

    # Summary
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    avg_score = sum(r["score"] for r in results) / total if total > 0 else 0

    # Count violations by type
    all_violations = []
    for r in results:
        all_violations.extend(r["violations"])

    violation_counts = {}
    for v in all_violations:
        violation_counts[v] = violation_counts.get(v, 0) + 1

    return {
        "mode": "adherence",
        "evaluator": "TaskAdherenceEvaluator",
        "summary": {
            "total_cases": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": f"{(passed/total)*100:.1f}%",
            "average_score": round(avg_score, 2),
            "threshold": evaluator.TASK_ADHERENCE_THRESHOLD
        },
        "violation_breakdown": dict(sorted(violation_counts.items(), key=lambda x: -x[1])),
        "results": results,
        "failed_cases": [r for r in results if not r["passed"]]
    }


async def run_full_evaluation(
    evaluator: PolicyAgentEvaluator,
    test_cases: List[Dict[str, Any]],
    rag_responses: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Run combined hallucination + adherence evaluation."""
    logger.info("Running Full Agent Evaluation...")

    results = []
    for i, (test, response) in enumerate(zip(test_cases, rag_responses)):
        logger.info(f"Full evaluation {i+1}/{len(test_cases)}: {test['query'][:40]}...")

        result = await evaluator.evaluate_response(
            query=test["query"],
            response=response["response"],
            context=response["contexts"],
            ground_truth=test.get("ground_truth", ""),
            category=test.get("metadata", {}).get("category", "unknown")
        )
        results.append(result)

    report = evaluator.generate_report(results)
    report["results"] = [r.to_dict() for r in results]

    return report


async def run_intent_check(
    evaluator: PolicyAgentEvaluator,
    test_cases: List[Dict[str, Any]],
    rag_responses: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Run IntentResolutionEvaluator to verify user intent understanding."""
    logger.info("Running Intent Resolution Check (IntentResolutionEvaluator)...")

    results = []
    for i, (test, response) in enumerate(zip(test_cases, rag_responses)):
        logger.info(f"Checking intent {i+1}/{len(test_cases)}...")

        result = await evaluator.check_intent_resolution(
            query=test["query"],
            response=response["response"]
        )
        results.append({
            "query": test["query"],
            "response": response["response"][:300],
            "intent_score": result.intent_score,
            "passed": result.passed,
            "reason": result.reason,
            "category": test.get("metadata", {}).get("category", "unknown")
        })

    # Summary
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    avg_score = sum(r["intent_score"] for r in results) / total if total > 0 else 0

    return {
        "mode": "intent",
        "evaluator": "IntentResolutionEvaluator",
        "summary": {
            "total_cases": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": f"{(passed/total)*100:.1f}%",
            "average_score": round(avg_score, 2),
            "threshold": evaluator.INTENT_RESOLUTION_THRESHOLD
        },
        "results": results,
        "failed_cases": [r for r in results if not r["passed"]]
    }


async def run_completeness_check(
    evaluator: PolicyAgentEvaluator,
    test_cases: List[Dict[str, Any]],
    rag_responses: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Run ResponseCompletenessEvaluator to verify response coverage."""
    logger.info("Running Response Completeness Check (ResponseCompletenessEvaluator)...")

    results = []
    for i, (test, response) in enumerate(zip(test_cases, rag_responses)):
        logger.info(f"Checking completeness {i+1}/{len(test_cases)}...")

        result = await evaluator.check_response_completeness(
            query=test["query"],
            response=response["response"],
            context=response["contexts"],
            ground_truth=test.get("ground_truth", "")
        )
        results.append({
            "query": test["query"],
            "response": response["response"][:300],
            "completeness_score": result.completeness_score,
            "passed": result.passed,
            "reason": result.reason,
            "missing_aspects": result.missing_aspects,
            "category": test.get("metadata", {}).get("category", "unknown")
        })

    # Summary
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    avg_score = sum(r["completeness_score"] for r in results) / total if total > 0 else 0

    # Aggregate missing aspects
    all_missing = []
    for r in results:
        all_missing.extend(r.get("missing_aspects", []))

    missing_counts = {}
    for m in all_missing:
        missing_counts[m] = missing_counts.get(m, 0) + 1

    return {
        "mode": "completeness",
        "evaluator": "ResponseCompletenessEvaluator",
        "summary": {
            "total_cases": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": f"{(passed/total)*100:.1f}%",
            "average_score": round(avg_score, 2),
            "threshold": evaluator.COMPLETENESS_THRESHOLD
        },
        "missing_aspects_breakdown": dict(sorted(missing_counts.items(), key=lambda x: -x[1])),
        "results": results,
        "failed_cases": [r for r in results if not r["passed"]]
    }


async def run_all_evaluators(
    evaluator: PolicyAgentEvaluator,
    test_cases: List[Dict[str, Any]],
    rag_responses: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Run ALL 4 evaluators and return comparison matrix."""
    logger.info("Running ALL 4 Evaluators (Comprehensive Analysis)...")

    results = []
    for i, (test, response) in enumerate(zip(test_cases, rag_responses)):
        logger.info(f"All evaluators {i+1}/{len(test_cases)}: {test['query'][:40]}...")

        result = await evaluator.evaluate_response(
            query=test["query"],
            response=response["response"],
            context=response["contexts"],
            ground_truth=test.get("ground_truth", ""),
            category=test.get("metadata", {}).get("category", "unknown"),
            run_all_evaluators=True
        )
        results.append(result)

    report = evaluator.generate_report(results)
    report["mode"] = "all-evaluators"
    report["results"] = [r.to_dict() for r in results]

    # Add comparison matrix
    matrix = {
        "groundedness": {"passed": 0, "failed": 0},
        "adherence": {"passed": 0, "failed": 0},
        "intent": {"passed": 0, "failed": 0},
        "completeness": {"passed": 0, "failed": 0}
    }

    for r in results:
        # Groundedness
        if r.is_grounded:
            matrix["groundedness"]["passed"] += 1
        else:
            matrix["groundedness"]["failed"] += 1

        # Adherence
        if r.adherence_passed:
            matrix["adherence"]["passed"] += 1
        else:
            matrix["adherence"]["failed"] += 1

        # Intent (if evaluated)
        if r.intent_score is not None:
            if r.intent_passed:
                matrix["intent"]["passed"] += 1
            else:
                matrix["intent"]["failed"] += 1

        # Completeness (if evaluated)
        if r.completeness_score is not None:
            if r.completeness_passed:
                matrix["completeness"]["passed"] += 1
            else:
                matrix["completeness"]["failed"] += 1

    # Calculate rates
    total = len(results)
    for key in matrix:
        p = matrix[key]["passed"]
        matrix[key]["rate"] = f"{(p/total)*100:.1f}%" if total > 0 else "N/A"

    report["evaluator_comparison_matrix"] = matrix

    return report


def export_to_csv(results: Dict[str, Any], output_path: str) -> None:
    """Export evaluation results to CSV for human review."""
    import csv

    mode = results.get("mode", "full")

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)

        if mode == "hallucination":
            headers = ["query", "is_grounded", "reason", "ungrounded_claims", "category"]
            writer.writerow(headers)
            for r in results.get("results", []):
                writer.writerow([
                    r["query"],
                    r["is_grounded"],
                    r["reason"],
                    "; ".join(r.get("ungrounded_claims", [])),
                    r.get("category", "")
                ])

        elif mode == "adherence":
            headers = ["query", "score", "passed", "reason", "violations", "category"]
            writer.writerow(headers)
            for r in results.get("results", []):
                writer.writerow([
                    r["query"],
                    r["score"],
                    r["passed"],
                    r["reason"],
                    "; ".join(r.get("violations", [])),
                    r.get("category", "")
                ])

        elif mode == "intent":
            headers = ["query", "intent_score", "passed", "reason", "category"]
            writer.writerow(headers)
            for r in results.get("results", []):
                writer.writerow([
                    r["query"],
                    r["intent_score"],
                    r["passed"],
                    r["reason"],
                    r.get("category", "")
                ])

        elif mode == "completeness":
            headers = ["query", "completeness_score", "passed", "reason", "missing_aspects", "category"]
            writer.writerow(headers)
            for r in results.get("results", []):
                writer.writerow([
                    r["query"],
                    r["completeness_score"],
                    r["passed"],
                    r["reason"],
                    "; ".join(r.get("missing_aspects", [])),
                    r.get("category", "")
                ])

        elif mode == "all-evaluators":
            headers = [
                "query", "category",
                "is_grounded", "grounding_reason",
                "adherence_score", "adherence_passed",
                "intent_score", "intent_passed",
                "completeness_score", "completeness_passed",
                "overall_passed"
            ]
            writer.writerow(headers)
            for r in results.get("results", []):
                writer.writerow([
                    r["query"],
                    r.get("category", ""),
                    r["is_grounded"],
                    r["grounding_reason"],
                    r["adherence_score"],
                    r["adherence_passed"],
                    r.get("intent_score", "N/A"),
                    r.get("intent_passed", "N/A"),
                    r.get("completeness_score", "N/A"),
                    r.get("completeness_passed", "N/A"),
                    r["overall_passed"]
                ])

        else:  # Full evaluation (hallucination + adherence)
            headers = [
                "query", "is_grounded", "grounding_reason",
                "adherence_score", "adherence_passed", "adherence_reason",
                "violations", "overall_passed", "critical_failures"
            ]
            writer.writerow(headers)
            for r in results.get("results", []):
                writer.writerow([
                    r["query"],
                    r["is_grounded"],
                    r["grounding_reason"],
                    r["adherence_score"],
                    r["adherence_passed"],
                    r["adherence_reason"],
                    "; ".join(r.get("violations", [])),
                    r["overall_passed"],
                    "; ".join(r.get("critical_failures", []))
                ])

    logger.info(f"Results exported to {output_path}")


def print_summary(results: Dict[str, Any]) -> None:
    """Print evaluation summary to console."""
    print()
    print_colored("=" * 70, Colors.HEADER)
    print_colored("  RUSH POLICYTECH AGENT EVALUATION REPORT", Colors.BOLD)
    print_colored("=" * 70, Colors.HEADER)

    mode = results.get("mode", "full")

    if mode == "hallucination":
        summary = results.get("summary", {})
        print()
        print_colored("  HALLUCINATION DETECTION (GroundednessProEvaluator)", Colors.CYAN)
        print_colored("  " + "-" * 50, Colors.CYAN)

        grounding_rate = summary.get("grounding_rate", "N/A")
        hallucinations = summary.get("hallucinations", 0)

        rate_color = Colors.GREEN if hallucinations == 0 else Colors.RED
        print(f"  Total Cases:     {summary.get('total_cases', 0)}")
        print(f"  Grounded:        {summary.get('grounded', 0)}")
        print(f"  Hallucinations:  {rate_color}{hallucinations}{Colors.ENDC}")
        print(f"  Grounding Rate:  {rate_color}{grounding_rate}{Colors.ENDC}")

        if hallucinations > 0:
            print()
            print_colored("  HALLUCINATION CASES (CRITICAL):", Colors.RED)
            for case in results.get("hallucination_cases", [])[:5]:
                print(f"    - {case['query'][:60]}...")
                print(f"      Reason: {case['reason'][:80]}...")

    elif mode == "adherence":
        summary = results.get("summary", {})
        print()
        print_colored("  RISEN COMPLIANCE (TaskAdherenceEvaluator)", Colors.CYAN)
        print_colored("  " + "-" * 50, Colors.CYAN)

        pass_rate = summary.get("pass_rate", "N/A")
        failed = summary.get("failed", 0)

        rate_color = Colors.GREEN if failed == 0 else Colors.YELLOW
        print(f"  Total Cases:     {summary.get('total_cases', 0)}")
        print(f"  Passed:          {summary.get('passed', 0)}")
        print(f"  Failed:          {rate_color}{failed}{Colors.ENDC}")
        print(f"  Pass Rate:       {rate_color}{pass_rate}{Colors.ENDC}")
        print(f"  Average Score:   {summary.get('average_score', 0)}/5.0")
        print(f"  Threshold:       {summary.get('threshold', 4.0)}")

        violations = results.get("violation_breakdown", {})
        if violations:
            print()
            print_colored("  RISEN VIOLATIONS:", Colors.YELLOW)
            for v, count in list(violations.items())[:5]:
                print(f"    [{count}x] {v}")

    elif mode == "intent":
        summary = results.get("summary", {})
        print()
        print_colored("  INTENT RESOLUTION (IntentResolutionEvaluator)", Colors.CYAN)
        print_colored("  " + "-" * 50, Colors.CYAN)

        pass_rate = summary.get("pass_rate", "N/A")
        failed = summary.get("failed", 0)

        rate_color = Colors.GREEN if failed == 0 else Colors.YELLOW
        print(f"  Total Cases:     {summary.get('total_cases', 0)}")
        print(f"  Passed:          {summary.get('passed', 0)}")
        print(f"  Failed:          {rate_color}{failed}{Colors.ENDC}")
        print(f"  Pass Rate:       {rate_color}{pass_rate}{Colors.ENDC}")
        print(f"  Average Score:   {summary.get('average_score', 0)}/5.0")
        print(f"  Threshold:       {summary.get('threshold', 4.0)}")

        if failed > 0:
            print()
            print_colored("  FAILED INTENT CASES:", Colors.YELLOW)
            for case in results.get("failed_cases", [])[:5]:
                print(f"    - {case['query'][:60]}...")
                print(f"      Reason: {case['reason'][:80]}...")

    elif mode == "completeness":
        summary = results.get("summary", {})
        print()
        print_colored("  RESPONSE COMPLETENESS (ResponseCompletenessEvaluator)", Colors.CYAN)
        print_colored("  " + "-" * 50, Colors.CYAN)

        pass_rate = summary.get("pass_rate", "N/A")
        failed = summary.get("failed", 0)

        rate_color = Colors.GREEN if failed == 0 else Colors.YELLOW
        print(f"  Total Cases:     {summary.get('total_cases', 0)}")
        print(f"  Passed:          {summary.get('passed', 0)}")
        print(f"  Failed:          {rate_color}{failed}{Colors.ENDC}")
        print(f"  Pass Rate:       {rate_color}{pass_rate}{Colors.ENDC}")
        print(f"  Average Score:   {summary.get('average_score', 0)}/5.0")
        print(f"  Threshold:       {summary.get('threshold', 4.0)}")

        missing = results.get("missing_aspects_breakdown", {})
        if missing:
            print()
            print_colored("  COMMON MISSING ASPECTS:", Colors.YELLOW)
            for aspect, count in list(missing.items())[:5]:
                print(f"    [{count}x] {aspect[:60]}...")

    elif mode == "all-evaluators":
        summary = results.get("summary", {})
        matrix = results.get("evaluator_comparison_matrix", {})
        print()
        print_colored("  COMPREHENSIVE EVALUATION (ALL 4 EVALUATORS)", Colors.CYAN)
        print_colored("  " + "-" * 50, Colors.CYAN)

        total = summary.get("total_cases", 0)
        print(f"  Total Cases:     {total}")
        print()

        print_colored("  EVALUATOR COMPARISON MATRIX:", Colors.BOLD)
        print("  " + "-" * 50)
        print(f"  {'Evaluator':<25} {'Passed':<10} {'Failed':<10} {'Rate':<10}")
        print("  " + "-" * 50)

        for eval_name, stats in matrix.items():
            rate = stats.get("rate", "N/A")
            rate_color = Colors.GREEN if "100" in rate else (Colors.YELLOW if float(rate.replace("%", "")) >= 80 else Colors.RED)
            print(f"  {eval_name.capitalize():<25} {stats['passed']:<10} {stats['failed']:<10} {rate_color}{rate:<10}{Colors.ENDC}")

        print("  " + "-" * 50)

        # Category breakdown if available
        category_breakdown = results.get("category_breakdown", {})
        if category_breakdown:
            print()
            print_colored("  CATEGORY BREAKDOWN:", Colors.BOLD)
            for cat, stats in category_breakdown.items():
                rate = stats.get("rate", "N/A")
                rate_color = Colors.GREEN if "100" in str(rate) else Colors.YELLOW
                print(f"    {cat:<15}: {rate_color}{stats['passed']}/{stats['total']} ({rate}){Colors.ENDC}")

    else:  # Full evaluation (hallucination + adherence)
        summary = results.get("summary", {})
        print()
        print_colored("  OVERALL RELIABILITY", Colors.CYAN)
        print_colored("  " + "-" * 50, Colors.CYAN)

        overall_rate = summary.get("overall_pass_rate", "N/A")
        passed = summary.get("overall_passed", 0)
        total = summary.get("total_cases", 0)

        rate_color = Colors.GREEN if passed == total else Colors.RED
        print(f"  Total Cases:     {total}")
        print(f"  Overall Passed:  {rate_color}{passed}{Colors.ENDC}")
        print(f"  Pass Rate:       {rate_color}{overall_rate}{Colors.ENDC}")

        print()
        print_colored("  HALLUCINATION CHECK", Colors.CYAN)
        grounding_rate = summary.get("grounding_rate", "N/A")
        hallucination_color = Colors.GREEN if "100" in grounding_rate else Colors.RED
        print(f"  Grounding Rate:  {hallucination_color}{grounding_rate}{Colors.ENDC}")

        hallucinations = results.get("hallucinations", {})
        if hallucinations.get("count", 0) > 0:
            print_colored(f"  Hallucinations Found: {hallucinations['count']}", Colors.RED)

        print()
        print_colored("  RISEN COMPLIANCE", Colors.CYAN)
        adherence_rate = summary.get("adherence_rate", "N/A")
        adherence_color = Colors.GREEN if "100" in adherence_rate else Colors.YELLOW
        print(f"  Adherence Rate:  {adherence_color}{adherence_rate}{Colors.ENDC}")
        print(f"  Avg Score:       {results.get('scores', {}).get('average_adherence', 0)}/5.0")

        violations = results.get("risen_violations", {})
        if violations.get("total", 0) > 0:
            print()
            print_colored("  TOP RISEN VIOLATIONS:", Colors.YELLOW)
            for v, count in list(violations.get("by_type", {}).items())[:3]:
                print(f"    [{count}x] {v[:60]}...")

        critical = results.get("critical_failures", [])
        if critical:
            print()
            print_colored("  CRITICAL FAILURES (REVIEW REQUIRED):", Colors.RED)
            for case in critical[:3]:
                print(f"    Query: {case['query'][:50]}...")
                for f in case.get("failures", [])[:2]:
                    print(f"      - {f[:70]}...")

    print()
    print_colored("=" * 70, Colors.HEADER)
    print()


async def main():
    parser = argparse.ArgumentParser(
        description="RUSH PolicyTech Agent Reliability Evaluation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/run_agent_evaluation.py                           # Full evaluation
  python scripts/run_agent_evaluation.py --mode hallucination      # Hallucination only
  python scripts/run_agent_evaluation.py --mode adherence          # RISEN compliance only
  python scripts/run_agent_evaluation.py --mode intent             # Intent resolution only
  python scripts/run_agent_evaluation.py --mode completeness       # Completeness only
  python scripts/run_agent_evaluation.py --mode all-evaluators     # All 4 evaluators
  python scripts/run_agent_evaluation.py --category adversarial    # Test adversarial cases
  python scripts/run_agent_evaluation.py --output-csv results.csv  # Export to CSV
        """
    )
    parser.add_argument(
        "--dataset",
        default="apps/backend/data/test_dataset.json",
        help="Path to test dataset JSON file"
    )
    parser.add_argument(
        "--mode",
        choices=["full", "hallucination", "adherence", "intent", "completeness", "all-evaluators"],
        default="full",
        help="Evaluation mode: full (grounding+adherence), hallucination, adherence, intent, completeness, or all-evaluators"
    )
    parser.add_argument(
        "--backend-url",
        default="http://localhost:8000",
        help="Backend API URL"
    )
    parser.add_argument(
        "--output-json",
        default="agent_evaluation_results.json",
        help="Output JSON file for results"
    )
    parser.add_argument(
        "--output-csv",
        help="Optional: Export results to CSV for human review"
    )
    parser.add_argument(
        "--skip-queries",
        action="store_true",
        help="Skip querying RAG (use ground truth as response for testing)"
    )
    parser.add_argument(
        "--category",
        help="Filter test cases by category (general, edge_case, adversarial, not_found)"
    )

    args = parser.parse_args()

    print_colored("\nRUSH PolicyTech Agent Evaluation", Colors.BOLD)
    print_colored("Using Azure AI Native Evaluators\n", Colors.CYAN)

    # Load test dataset
    logger.info(f"Loading test dataset from {args.dataset}")
    dataset_path = Path(args.dataset)

    if not dataset_path.exists():
        logger.warning(f"Dataset not found at {args.dataset}, creating sample dataset...")
        dataset = TestDataset()
        dataset.generate_sample_dataset()
        dataset_path.parent.mkdir(parents=True, exist_ok=True)
        dataset.save(str(dataset_path))
    else:
        dataset = TestDataset(str(dataset_path))

    if args.category:
        test_cases = dataset.get_by_category(args.category)
        logger.info(f"Filtered to {len(test_cases)} cases in category: {args.category}")
    else:
        test_cases = dataset.get_all()

    if not test_cases:
        logger.error("No test cases found!")
        return

    logger.info(f"Loaded {len(test_cases)} test cases")

    # Get RAG responses
    if args.skip_queries:
        logger.info("Skipping RAG queries (using ground truth as response)")
        rag_responses = [
            {
                "response": tc["ground_truth"],
                "contexts": tc.get("contexts", []),
                "sources": []
            }
            for tc in test_cases
        ]
    else:
        rag_responses = await get_rag_responses(test_cases, args.backend_url)

    # Initialize evaluator
    evaluator = PolicyAgentEvaluator()

    # Run evaluation based on mode
    if args.mode == "hallucination":
        results = await run_hallucination_check(evaluator, test_cases, rag_responses)
    elif args.mode == "adherence":
        results = await run_adherence_check(evaluator, test_cases, rag_responses)
    elif args.mode == "intent":
        results = await run_intent_check(evaluator, test_cases, rag_responses)
    elif args.mode == "completeness":
        results = await run_completeness_check(evaluator, test_cases, rag_responses)
    elif args.mode == "all-evaluators":
        results = await run_all_evaluators(evaluator, test_cases, rag_responses)
    else:
        results = await run_full_evaluation(evaluator, test_cases, rag_responses)

    # Add metadata
    results["timestamp"] = datetime.now().isoformat()
    results["dataset"] = args.dataset
    results["total_cases"] = len(test_cases)
    if args.category:
        results["category_filter"] = args.category

    # Save results
    output_path = Path(args.output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    logger.info(f"Results saved to {output_path}")

    # Export CSV if requested
    if args.output_csv:
        export_to_csv(results, args.output_csv)

    # Print summary
    print_summary(results)


if __name__ == "__main__":
    asyncio.run(main())
