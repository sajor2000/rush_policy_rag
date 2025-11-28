#!/usr/bin/env python3
"""
RAG Evaluation Runner for RUSH PolicyTech Agent.

Runs batch evaluations using both Azure AI and RAGAS evaluators,
generates reports, and exports results for human review.

Usage:
    # Run full evaluation with both frameworks
    python scripts/run_evaluation.py
    
    # Run Azure evaluators only
    python scripts/run_evaluation.py --evaluator azure
    
    # Run RAGAS only
    python scripts/run_evaluation.py --evaluator ragas
    
    # Use custom test dataset
    python scripts/run_evaluation.py --dataset path/to/dataset.json
    
    # Export results to CSV
    python scripts/run_evaluation.py --output-csv results.csv
"""

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
    AzureRAGEvaluator,
    RagasEvaluator,
    TestDataset,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


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


async def run_azure_evaluation(
    test_cases: List[Dict[str, Any]],
    rag_responses: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Run Azure AI evaluation."""
    logger.info("Running Azure AI evaluation...")
    
    evaluator = AzureRAGEvaluator()
    
    # Prepare test cases with responses
    eval_cases = []
    for i, (test, response) in enumerate(zip(test_cases, rag_responses)):
        eval_cases.append({
            "query": test["query"],
            "response": response["response"],
            "context": response["contexts"],
            "ground_truth": test.get("ground_truth", "")
        })
    
    results = await evaluator.evaluate_batch(eval_cases)
    report = evaluator.generate_report(results)
    
    return {
        "evaluator": "azure",
        "results": [r.to_dict() for r in results],
        "report": report
    }


def run_ragas_evaluation(
    test_cases: List[Dict[str, Any]],
    rag_responses: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Run RAGAS evaluation."""
    logger.info("Running RAGAS evaluation...")
    
    evaluator = RagasEvaluator()
    
    # Prepare test cases with responses
    eval_cases = []
    for test, response in zip(test_cases, rag_responses):
        eval_cases.append({
            "query": test["query"],
            "response": response["response"],
            "contexts": response["contexts"],
            "ground_truth": test.get("ground_truth", "")
        })
    
    results = evaluator.evaluate_dataset(eval_cases)
    report = evaluator.generate_report(results)
    
    return {
        "evaluator": "ragas",
        "results": [r.to_dict() for r in results],
        "report": report
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


def export_to_csv(
    results: Dict[str, Any],
    output_path: str
) -> None:
    """Export evaluation results to CSV for human review."""
    import csv
    
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        
        # Header
        headers = [
            "query", "response", "passed",
            "groundedness", "relevance", "coherence", "retrieval",
            "faithfulness", "answer_relevancy", "context_precision", "context_recall"
        ]
        writer.writerow(headers)
        
        # Combine results from both evaluators
        azure_results = {r["query"]: r for r in results.get("azure", {}).get("results", [])}
        ragas_results = {r["query"]: r for r in results.get("ragas", {}).get("results", [])}
        
        all_queries = set(azure_results.keys()) | set(ragas_results.keys())
        
        for query in all_queries:
            azure = azure_results.get(query, {})
            ragas = ragas_results.get(query, {})
            
            row = [
                query,
                azure.get("response", ragas.get("response", "")),
                azure.get("passed", ragas.get("passed", "")),
                azure.get("groundedness_score", ""),
                azure.get("relevance_score", ""),
                azure.get("coherence_score", ""),
                azure.get("retrieval_score", ""),
                ragas.get("faithfulness", ""),
                ragas.get("answer_relevancy", ""),
                ragas.get("context_precision", ""),
                ragas.get("context_recall", "")
            ]
            writer.writerow(row)
    
    logger.info(f"Results exported to {output_path}")


def print_summary(results: Dict[str, Any]) -> None:
    """Print evaluation summary to console."""
    print("\n" + "="*60)
    print("RAG EVALUATION SUMMARY")
    print("="*60)
    
    if "azure" in results:
        azure = results["azure"]["report"]
        print("\n📊 AZURE AI EVALUATION")
        print(f"   Pass Rate: {azure['summary']['pass_rate']}")
        print(f"   Total Cases: {azure['summary']['total_cases']}")
        print(f"   Avg Groundedness: {azure['average_scores']['groundedness']}/5")
        print(f"   Avg Relevance: {azure['average_scores']['relevance']}/5")
        print(f"   Avg Coherence: {azure['average_scores']['coherence']}/5")
        print(f"   Avg Retrieval: {azure['average_scores']['retrieval']}")
    
    if "ragas" in results:
        ragas = results["ragas"]["report"]
        print("\n📊 RAGAS EVALUATION")
        print(f"   Pass Rate: {ragas['summary']['pass_rate']}")
        print(f"   Overall Score: {ragas['summary']['overall_score']}")
        print(f"   Faithfulness: {ragas['average_scores']['faithfulness']}")
        print(f"   Answer Relevancy: {ragas['average_scores']['answer_relevancy']}")
        print(f"   Context Precision: {ragas['average_scores']['context_precision']}")
        print(f"   Context Recall: {ragas['average_scores']['context_recall']}")
        
        if ragas.get("problem_areas", {}).get("hallucination_risk", 0) > 0:
            print(f"\n   ⚠️  HALLUCINATION RISK: {ragas['problem_areas']['hallucination_risk']} cases")
    
    print("\n" + "="*60)


async def main():
    parser = argparse.ArgumentParser(description="Run RAG evaluation")
    parser.add_argument(
        "--dataset",
        default="apps/backend/data/test_dataset.json",
        help="Path to test dataset JSON file"
    )
    parser.add_argument(
        "--evaluator",
        choices=["azure", "ragas", "both"],
        default="both",
        help="Which evaluator to run"
    )
    parser.add_argument(
        "--backend-url",
        default="http://localhost:8000",
        help="Backend API URL"
    )
    parser.add_argument(
        "--output-json",
        default="evaluation_results.json",
        help="Output JSON file for results"
    )
    parser.add_argument(
        "--output-csv",
        help="Optional: Export results to CSV for human review"
    )
    parser.add_argument(
        "--skip-queries",
        action="store_true",
        help="Skip querying RAG (use for testing with mock data)"
    )
    parser.add_argument(
        "--category",
        help="Filter test cases by category"
    )
    
    args = parser.parse_args()
    
    # Load test dataset
    logger.info(f"Loading test dataset from {args.dataset}")
    dataset = TestDataset(args.dataset)
    
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
    
    # Run evaluations
    results = {
        "timestamp": datetime.now().isoformat(),
        "dataset": args.dataset,
        "total_cases": len(test_cases)
    }
    
    if args.evaluator in ["azure", "both"]:
        try:
            results["azure"] = await run_azure_evaluation(test_cases, rag_responses)
        except Exception as e:
            logger.error(f"Azure evaluation failed: {e}")
            results["azure"] = {"error": str(e)}
    
    if args.evaluator in ["ragas", "both"]:
        try:
            results["ragas"] = run_ragas_evaluation(test_cases, rag_responses)
        except Exception as e:
            logger.error(f"RAGAS evaluation failed: {e}")
            results["ragas"] = {"error": str(e)}
    
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

