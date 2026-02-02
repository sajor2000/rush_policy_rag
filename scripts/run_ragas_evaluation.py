#!/usr/bin/env python3
"""
Run RAGAS evaluation on generated test dataset.

This script:
1. Loads test dataset (generated or existing)
2. Queries the live backend for each test case
3. Runs RAGAS evaluation metrics
4. Generates comprehensive report

Usage:
    # Run on default dataset
    python scripts/run_ragas_evaluation.py

    # Run on specific dataset
    python scripts/run_ragas_evaluation.py \
        --dataset apps/backend/data/test_dataset_v3.json \
        --output reports/ragas_evaluation.json

    # Quick test mode
    python scripts/run_ragas_evaluation.py --sample 10

Requirements:
    pip install ragas langchain-openai httpx
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

# Add parent paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "backend"))
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "backend" / "evaluation"))

from dotenv import load_dotenv

# Load environment
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class RAGASEvaluationRunner:
    """Run RAGAS evaluation on test dataset with live backend."""

    def __init__(
        self,
        backend_url: str = None,
        use_live_backend: bool = True,
    ):
        self.backend_url = backend_url or os.getenv("BACKEND_URL", "http://localhost:8000")
        self.use_live_backend = use_live_backend
        self._http_client = None
        self._evaluator = None

    def _get_http_client(self):
        """Get or create HTTP client."""
        if self._http_client is None:
            import httpx
            self._http_client = httpx.Client(timeout=60.0)
        return self._http_client

    def _get_evaluator(self):
        """Get or create RAGAS evaluator."""
        if self._evaluator is None:
            try:
                from ragas_evaluator import RagasEvaluator
            except ImportError:
                from apps.backend.evaluation.ragas_evaluator import RagasEvaluator
            self._evaluator = RagasEvaluator()
        return self._evaluator

    def load_dataset(self, dataset_path: Path) -> List[Dict[str, Any]]:
        """Load test dataset from JSON file."""
        if not dataset_path.exists():
            raise FileNotFoundError(f"Dataset not found: {dataset_path}")

        with open(dataset_path, "r") as f:
            data = json.load(f)

        # Handle different dataset formats
        if "test_cases" in data:
            test_cases = data["test_cases"]
        elif isinstance(data, list):
            test_cases = data
        else:
            raise ValueError("Invalid dataset format")

        logger.info(f"Loaded {len(test_cases)} test cases from {dataset_path}")
        return test_cases

    def query_backend(self, query: str) -> Dict[str, Any]:
        """Query the live backend API."""
        if not self.use_live_backend:
            return {"response": "", "contexts": [], "metadata": {}}

        try:
            client = self._get_http_client()
            response = client.post(
                f"{self.backend_url}/api/chat",
                json={"message": query},
            )
            response.raise_for_status()
            data = response.json()

            # Extract context from evidence (backend returns evidence, not citations)
            contexts = []
            for evidence in data.get("evidence", []):
                if isinstance(evidence, dict):
                    # Backend uses 'snippet' field for content
                    content = evidence.get("snippet", evidence.get("content", evidence.get("text", "")))
                    if content:
                        contexts.append(content)
                elif isinstance(evidence, str):
                    contexts.append(evidence)

            return {
                "response": data.get("response", ""),
                "contexts": contexts,
                "metadata": data.get("metadata", {}),
            }

        except Exception as e:
            logger.error(f"Backend query failed for '{query[:50]}...': {e}")
            return {"response": "", "contexts": [], "metadata": {"error": str(e)}}

    def run_evaluation(
        self,
        dataset_path: Path,
        sample_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Run RAGAS evaluation on test dataset.

        Args:
            dataset_path: Path to test dataset JSON
            sample_size: Limit to N test cases (optional)

        Returns:
            Complete evaluation report
        """
        logger.info("=" * 60)
        logger.info("RAGAS Evaluation Runner")
        logger.info("=" * 60)

        # Load dataset
        test_cases = self.load_dataset(dataset_path)

        if sample_size:
            test_cases = test_cases[:sample_size]
            logger.info(f"Using sample of {sample_size} test cases")

        # Check backend health
        if self.use_live_backend:
            try:
                client = self._get_http_client()
                health = client.get(f"{self.backend_url}/health")
                if health.status_code != 200:
                    logger.warning("Backend health check failed")
            except Exception as e:
                logger.warning(f"Backend not available: {e}")
                logger.info("Evaluation will use existing responses from dataset")
                self.use_live_backend = False

        # Prepare evaluation data
        eval_data = []
        skipped_not_found = 0

        for i, tc in enumerate(test_cases):
            if i % 10 == 0:
                logger.info(f"Processing test case {i+1}/{len(test_cases)}")

            # v5 uses "input", v4 uses "question"
            query = tc.get("input", tc.get("question", tc.get("query", "")))

            # Skip cases marked as expected_not_found - these shouldn't penalize the system
            if tc.get("expected_not_found", False):
                skipped_not_found += 1
                logger.debug(f"Skipping expected_not_found case: {tc.get('id', 'unknown')}")
                continue

            if self.use_live_backend:
                # Get live response
                backend_result = self.query_backend(query)
                response = backend_result["response"]
                contexts = backend_result["contexts"]
            else:
                # Use expected answer from dataset (v5 uses expected_output, v4 uses expected_answer)
                response = tc.get("expected_output", tc.get("expected_answer", ""))
                contexts = tc.get("ground_truth_context", [])

            # v5 uses expected_output, v4 uses expected_answer for ground_truth
            ground_truth = tc.get("ground_truth", tc.get("expected_output", tc.get("expected_answer", "")))

            eval_data.append({
                "query": query,
                "response": response,
                "contexts": contexts,
                "ground_truth": ground_truth,
                "test_case_id": tc.get("id", f"tc-{i+1}"),
                "category": tc.get("category", "general"),
                "criticality": tc.get("criticality", "medium"),
            })

        if skipped_not_found > 0:
            logger.info(f"Skipped {skipped_not_found} expected_not_found cases")

        # Run RAGAS evaluation
        evaluator = self._get_evaluator()

        logger.info(f"Running RAGAS evaluation on {len(eval_data)} cases...")
        results = evaluator.evaluate_dataset(eval_data)

        # Generate report
        report = evaluator.generate_report(results)

        # Add metadata
        report["metadata"] = {
            "evaluation_date": datetime.now().isoformat(),
            "dataset_path": str(dataset_path),
            "total_cases": len(test_cases),
            "evaluated_cases": len(eval_data),
            "skipped_not_found": skipped_not_found,
            "sample_size": sample_size,
            "backend_url": self.backend_url if self.use_live_backend else "N/A",
            "used_live_backend": self.use_live_backend,
        }

        # Add per-category breakdown
        category_results = {}
        for result, data in zip(results, eval_data):
            category = data.get("category", "general")
            if category not in category_results:
                category_results[category] = {"passed": 0, "failed": 0, "scores": []}
            if result.passed:
                category_results[category]["passed"] += 1
            else:
                category_results[category]["failed"] += 1
            category_results[category]["scores"].append(result.overall_score)

        report["category_breakdown"] = {
            cat: {
                "passed": data["passed"],
                "failed": data["failed"],
                "pass_rate": f"{data['passed']/(data['passed']+data['failed'])*100:.1f}%",
                "avg_score": round(sum(data["scores"])/len(data["scores"]), 3) if data["scores"] else 0,
            }
            for cat, data in category_results.items()
        }

        # Add detailed results for failed cases
        report["detailed_failures"] = [
            {
                "id": eval_data[i]["test_case_id"],
                "query": results[i].query,
                "category": eval_data[i]["category"],
                "faithfulness": results[i].faithfulness,
                "answer_relevancy": results[i].answer_relevancy,
                "context_precision": results[i].context_precision,
                "context_recall": results[i].context_recall,
            }
            for i, r in enumerate(results)
            if not r.passed
        ][:20]  # Limit to 20

        return report

    def save_report(
        self,
        report: Dict[str, Any],
        output_path: Path,
    ):
        """Save evaluation report to JSON and HTML."""
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Save JSON
        with open(output_path, "w") as f:
            json.dump(report, f, indent=2)
        logger.info(f"JSON report saved to {output_path}")

        # Generate HTML report
        html_content = self._generate_html_report(report)
        html_path = output_path.with_suffix(".html")
        with open(html_path, "w") as f:
            f.write(html_content)
        logger.info(f"HTML report saved to {html_path}")

    def _generate_html_report(self, report: Dict[str, Any]) -> str:
        """Generate HTML report."""
        summary = report.get("summary", {})
        avg_scores = report.get("average_scores", {})
        category_breakdown = report.get("category_breakdown", {})
        failures = report.get("detailed_failures", [])

        # Generate category rows
        category_rows = ""
        for cat, data in sorted(category_breakdown.items()):
            category_rows += f"""
            <tr>
                <td>{cat}</td>
                <td>{data['passed']}/{data['passed']+data['failed']}</td>
                <td>{data['pass_rate']}</td>
                <td>{data['avg_score']}</td>
            </tr>"""

        # Generate failure rows
        failure_rows = ""
        for f in failures[:10]:
            failure_rows += f"""
            <tr>
                <td>{f['id']}</td>
                <td>{f['query'][:50]}...</td>
                <td>{f['category']}</td>
                <td class="{'fail' if f['faithfulness'] < 0.8 else ''}">{f['faithfulness']}</td>
                <td>{f['answer_relevancy']}</td>
            </tr>"""

        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>RAGAS Evaluation Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; color: #333; }}
        h1 {{ color: #006332; }}
        h2 {{ color: #30AE6E; border-bottom: 2px solid #30AE6E; padding-bottom: 5px; }}
        .summary {{ background: #DFF9EB; padding: 15px; border-radius: 8px; margin: 15px 0; display: flex; gap: 20px; }}
        .metric {{ padding: 15px; background: white; border-radius: 5px; text-align: center; min-width: 100px; }}
        .metric-value {{ font-size: 24px; font-weight: bold; color: #006332; }}
        .metric-label {{ font-size: 12px; color: #666; }}
        .pass {{ color: #30AE6E; }}
        .fail {{ color: #E74C3C; }}
        table {{ border-collapse: collapse; width: 100%; margin: 15px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 10px; text-align: left; }}
        th {{ background: #006332; color: white; }}
        tr:nth-child(even) {{ background: #f9f9f9; }}
    </style>
</head>
<body>
    <h1>RUSH Policy RAG - RAGAS Evaluation Report</h1>

    <p><strong>Generated:</strong> {report.get('metadata', {}).get('evaluation_date', 'N/A')}</p>
    <p><strong>Dataset:</strong> {report.get('metadata', {}).get('dataset_path', 'N/A')}</p>

    <h2>Summary</h2>
    <div class="summary">
        <div class="metric">
            <div class="metric-value {'pass' if float(summary.get('pass_rate', '0%').replace('%', '')) >= 80 else 'fail'}">
                {summary.get('pass_rate', 'N/A')}
            </div>
            <div class="metric-label">Pass Rate</div>
        </div>
        <div class="metric">
            <div class="metric-value">{summary.get('total_cases', 0)}</div>
            <div class="metric-label">Total Cases</div>
        </div>
        <div class="metric">
            <div class="metric-value pass">{summary.get('passed', 0)}</div>
            <div class="metric-label">Passed</div>
        </div>
        <div class="metric">
            <div class="metric-value fail">{summary.get('failed', 0)}</div>
            <div class="metric-label">Failed</div>
        </div>
    </div>

    <h2>Average Scores</h2>
    <table>
        <tr>
            <th>Metric</th>
            <th>Score</th>
            <th>Threshold</th>
            <th>Status</th>
        </tr>
        <tr>
            <td>Faithfulness</td>
            <td>{avg_scores.get('faithfulness', 'N/A')}</td>
            <td>0.80</td>
            <td class="{'pass' if avg_scores.get('faithfulness', 0) >= 0.8 else 'fail'}">
                {'PASS' if avg_scores.get('faithfulness', 0) >= 0.8 else 'FAIL'}
            </td>
        </tr>
        <tr>
            <td>Answer Relevancy</td>
            <td>{avg_scores.get('answer_relevancy', 'N/A')}</td>
            <td>0.70</td>
            <td class="{'pass' if avg_scores.get('answer_relevancy', 0) >= 0.7 else 'fail'}">
                {'PASS' if avg_scores.get('answer_relevancy', 0) >= 0.7 else 'FAIL'}
            </td>
        </tr>
        <tr>
            <td>Context Precision</td>
            <td>{avg_scores.get('context_precision', 'N/A')}</td>
            <td>0.70</td>
            <td class="{'pass' if avg_scores.get('context_precision', 0) >= 0.7 else 'fail'}">
                {'PASS' if avg_scores.get('context_precision', 0) >= 0.7 else 'FAIL'}
            </td>
        </tr>
        <tr>
            <td>Context Recall</td>
            <td>{avg_scores.get('context_recall', 'N/A')}</td>
            <td>0.70</td>
            <td class="{'pass' if avg_scores.get('context_recall', 0) >= 0.7 else 'fail'}">
                {'PASS' if avg_scores.get('context_recall', 0) >= 0.7 else 'FAIL'}
            </td>
        </tr>
    </table>

    <h2>Category Breakdown</h2>
    <table>
        <tr>
            <th>Category</th>
            <th>Passed/Total</th>
            <th>Pass Rate</th>
            <th>Avg Score</th>
        </tr>
        {category_rows}
    </table>

    <h2>Failed Cases (Top 10)</h2>
    <table>
        <tr>
            <th>ID</th>
            <th>Query</th>
            <th>Category</th>
            <th>Faithfulness</th>
            <th>Relevancy</th>
        </tr>
        {failure_rows}
    </table>

    <hr>
    <p style="color: #666; font-size: 12px;">
        Generated by RUSH Policy RAG Evaluation System
    </p>
</body>
</html>
"""
        return html


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run RAGAS evaluation on RAG test dataset"
    )
    parser.add_argument(
        "--dataset",
        type=str,
        default="apps/backend/data/test_dataset_v5.json",
        help="Path to test dataset JSON (v5 = 100 realistic staff questions with real policy content)"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="reports/ragas_evaluation.json",
        help="Output path for evaluation report"
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="Limit to N test cases"
    )
    parser.add_argument(
        "--no-live",
        action="store_true",
        help="Don't query live backend (use dataset responses)"
    )
    parser.add_argument(
        "--backend-url",
        type=str,
        default=None,
        help="Backend API URL"
    )

    args = parser.parse_args()

    # Resolve paths
    root = Path(__file__).parent.parent
    dataset_path = Path(args.dataset)
    if not dataset_path.is_absolute():
        dataset_path = root / dataset_path

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = root / output_path

    # Create runner
    runner = RAGASEvaluationRunner(
        backend_url=args.backend_url,
        use_live_backend=not args.no_live,
    )

    # Run evaluation
    report = runner.run_evaluation(
        dataset_path=dataset_path,
        sample_size=args.sample,
    )

    # Save report
    runner.save_report(report, output_path)

    # Print summary
    summary = report.get("summary", {})
    logger.info("\n" + "=" * 60)
    logger.info("Evaluation Complete")
    logger.info("=" * 60)
    logger.info(f"Pass Rate: {summary.get('pass_rate', 'N/A')}")
    logger.info(f"Total Cases: {summary.get('total_cases', 0)}")
    logger.info(f"Passed: {summary.get('passed', 0)}")
    logger.info(f"Failed: {summary.get('failed', 0)}")
    logger.info(f"Report saved to: {output_path}")

    # Exit with error if pass rate is below threshold
    pass_rate_str = summary.get("pass_rate", "0%")
    pass_rate = float(pass_rate_str.replace("%", ""))
    if pass_rate < 70:
        logger.warning(f"Pass rate {pass_rate}% below 70% threshold")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
