#!/usr/bin/env python3
"""
Weekly RAG Evaluation Script.

Runs comprehensive DeepEval evaluation on production queries and generates reports.

Features:
- Fetches queries from Azure App Insights (last 7 days)
- Runs DeepEval metrics on each query
- Performs claim-level diagnostics on sample
- Generates HTML report
- Sends email to stakeholders
- Saves JSON artifacts for historical analysis

Usage:
    # Full weekly evaluation with email
    python scripts/weekly_eval.py

    # Dry run without email
    python scripts/weekly_eval.py --dry-run

    # Limit sample size
    python scripts/weekly_eval.py --sample 20

    # Use local queries instead of App Insights
    python scripts/weekly_eval.py --local-queries queries.json

Environment Variables Required:
    AOAI_ENDPOINT: Azure OpenAI endpoint
    AOAI_API_KEY: Azure OpenAI API key
    BACKEND_URL: Backend API URL

    Email (Azure Communication Services - Recommended):
    AZURE_COMM_CONNECTION_STRING: Azure Communication Services connection string
    AZURE_COMM_SENDER_ADDRESS: Sender email address (from verified domain)

    Email (SMTP - Legacy Fallback):
    SMTP_SERVER: SMTP server (default: smtp.office365.com)
    SMTP_USER: SMTP username
    SMTP_PASS: SMTP password
    SMTP_FROM: Sender email address

    LOG_ANALYTICS_WORKSPACE_ID: App Insights workspace ID (optional)
"""

import os
import sys
import json
import argparse
import logging
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict

# Azure Communication Services (optional, for email)
try:
    from azure.communication.email import EmailClient
    AZURE_COMM_AVAILABLE = True
except ImportError:
    AZURE_COMM_AVAILABLE = False

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "backend"))

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


@dataclass
class WeeklyEvalConfig:
    """Configuration for weekly evaluation."""
    sample_size: int = 100
    diagnostic_sample_size: int = 20
    email_recipients: List[str] = None
    dry_run: bool = False
    local_queries_file: Optional[str] = None
    output_dir: str = "eval_reports"

    def __post_init__(self):
        if self.email_recipients is None:
            default = os.getenv("WEEKLY_REPORT_RECIPIENTS", "juan_rojas@rush.edu")
            self.email_recipients = [r.strip() for r in default.split(",")]


@dataclass
class EvalSummary:
    """Summary of evaluation results."""
    total_queries: int
    passed_queries: int
    failed_queries: int
    pass_rate: float
    avg_faithfulness: float
    avg_answer_relevancy: float
    avg_context_precision: float
    avg_policy_citation: float
    hallucination_risk_count: int
    lost_in_middle_count: int
    retrieval_failures: int
    generation_failures: int
    evaluation_date: str
    week_start: str
    week_end: str


class WeeklyEvaluator:
    """Weekly RAG evaluation runner."""

    def __init__(self, config: WeeklyEvalConfig):
        self.config = config
        self.backend_url = os.getenv("BACKEND_URL", "http://localhost:8000")

        # Create output directory
        self.output_dir = Path(config.output_dir)
        self.output_dir.mkdir(exist_ok=True)

        # Initialize clients lazily
        self._metrics = None
        self._diagnostics = None
        self._http_client = None

    def _get_metrics(self):
        """Lazy-load DeepEval metrics."""
        if self._metrics is None:
            try:
                from app.evaluation.metrics import DeepEvalMetrics
                self._metrics = DeepEvalMetrics()
            except ImportError as e:
                logger.error(f"Failed to import DeepEvalMetrics: {e}")
                raise
        return self._metrics

    def _get_diagnostics(self):
        """Lazy-load RAG diagnostics."""
        if self._diagnostics is None:
            try:
                from app.evaluation.diagnostics import RAGDiagnostics
                self._diagnostics = RAGDiagnostics()
            except ImportError as e:
                logger.error(f"Failed to import RAGDiagnostics: {e}")
                raise
        return self._diagnostics

    def _get_http_client(self):
        """Get or create HTTP client."""
        if self._http_client is None:
            import httpx
            self._http_client = httpx.Client(timeout=60.0)
        return self._http_client

    def fetch_queries_from_app_insights(self, days: int = 7) -> List[Dict[str, Any]]:
        """
        Fetch queries from Azure App Insights.

        Args:
            days: Number of days to look back

        Returns:
            List of query records with user_query, response, timestamp
        """
        workspace_id = os.getenv("LOG_ANALYTICS_WORKSPACE_ID")
        if not workspace_id:
            logger.warning("LOG_ANALYTICS_WORKSPACE_ID not set, skipping App Insights fetch")
            return []

        try:
            from azure.identity import DefaultAzureCredential
            from azure.monitor.query import LogsQueryClient

            credential = DefaultAzureCredential()
            client = LogsQueryClient(credential)

            # KQL query for chat requests
            query = f"""
            customEvents
            | where timestamp > ago({days}d)
            | where name == "ChatRequest" or name == "chat_query"
            | extend user_query = tostring(customDimensions.query)
            | extend response = tostring(customDimensions.response)
            | extend citations = tostring(customDimensions.citations)
            | project timestamp, user_query, response, citations
            | order by timestamp desc
            | take {self.config.sample_size}
            """

            response = client.query_workspace(
                workspace_id=workspace_id,
                query=query,
                timespan=timedelta(days=days)
            )

            queries = []
            if response.tables:
                for row in response.tables[0].rows:
                    queries.append({
                        "timestamp": str(row[0]),
                        "query": row[1],
                        "response": row[2],
                        "citations": json.loads(row[3]) if row[3] else [],
                    })

            logger.info(f"Fetched {len(queries)} queries from App Insights")
            return queries

        except Exception as e:
            logger.error(f"Failed to fetch from App Insights: {e}")
            return []

    def load_local_queries(self, filepath: str) -> List[Dict[str, Any]]:
        """Load queries from local JSON file."""
        try:
            with open(filepath, "r") as f:
                data = json.load(f)
            queries = data if isinstance(data, list) else data.get("queries", [])
            logger.info(f"Loaded {len(queries)} queries from {filepath}")
            return queries[:self.config.sample_size]
        except Exception as e:
            logger.error(f"Failed to load local queries: {e}")
            return []

    def query_backend(self, query: str) -> Dict[str, Any]:
        """Query the RAG backend API."""
        try:
            client = self._get_http_client()
            response = client.post(
                f"{self.backend_url}/api/chat",
                json={"message": query},
            )
            response.raise_for_status()
            data = response.json()

            # Extract context from evidence snippets (the actual text chunks)
            # Backend returns 'evidence' with snippets, not 'citations'
            evidence = data.get("evidence", [])
            context = []
            if isinstance(evidence, list):
                for item in evidence:
                    if isinstance(item, dict) and "snippet" in item:
                        context.append(item["snippet"])
                    elif isinstance(item, str):
                        context.append(item)

            # Also include source citations for context
            sources = data.get("sources", [])
            if isinstance(sources, list) and not context:
                for src in sources:
                    if isinstance(src, dict) and "citation" in src:
                        context.append(src["citation"])

            return {
                "response": data.get("response", ""),
                "context": context,
                "sources": sources,
                "metadata": {
                    "chunks_used": data.get("chunks_used", 0),
                    "confidence": data.get("confidence", "unknown"),
                    "confidence_score": data.get("confidence_score", 0),
                },
            }
        except Exception as e:
            logger.error(f"Backend query failed: {e}")
            return {"response": "", "context": [], "metadata": {"error": str(e)}}

    def evaluate_query(self, query_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluate a single query.

        Args:
            query_data: Dict with query, response, and context

        Returns:
            Evaluation result dict
        """
        query = query_data.get("query", "")
        response = query_data.get("response", "")
        context = query_data.get("context", query_data.get("citations", []))

        # If no response, fetch from backend
        if not response:
            backend_result = self.query_backend(query)
            response = backend_result["response"]
            context = backend_result["context"]

        # Convert context to list of strings
        if isinstance(context, list):
            context_strs = [str(c) if not isinstance(c, str) else c for c in context]
        else:
            context_strs = [str(context)] if context else []

        # Run DeepEval metrics
        try:
            metrics = self._get_metrics()
            eval_result = metrics.evaluate_test_case(
                query=query,
                response=response,
                context=context_strs,
            )
            return {
                "query": query,
                "response": response[:500],
                "metrics": {
                    "faithfulness": eval_result.faithfulness,
                    "answer_relevancy": eval_result.answer_relevancy,
                    "context_precision": eval_result.context_precision,
                    "policy_citation": eval_result.policy_citation,
                    "procedural_completeness": eval_result.procedural_completeness,
                    "overall_score": eval_result.overall_score,
                },
                "passed": eval_result.passed,
                "failure_reasons": eval_result.failure_reasons,
            }
        except Exception as e:
            logger.error(f"Evaluation failed for query '{query[:50]}...': {e}")
            return {
                "query": query,
                "response": response[:500],
                "metrics": {},
                "passed": False,
                "failure_reasons": [f"Evaluation error: {str(e)}"],
            }

    def run_diagnostics(self, query_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run claim-level diagnostics on a query.

        Args:
            query_data: Dict with query, response, and context

        Returns:
            Diagnostic result dict
        """
        query = query_data.get("query", "")
        response = query_data.get("response", "")
        context = query_data.get("context", query_data.get("citations", []))

        # If no response, fetch from backend
        if not response:
            backend_result = self.query_backend(query)
            response = backend_result["response"]
            context = backend_result["context"]

        # Convert context to list of strings
        if isinstance(context, list):
            context_strs = [str(c) if not isinstance(c, str) else c for c in context]
        else:
            context_strs = [str(context)] if context else []

        try:
            diagnostics = self._get_diagnostics()
            diagnostic = diagnostics.diagnose(query, response, context_strs)
            return diagnostic.to_dict()
        except Exception as e:
            logger.error(f"Diagnostics failed for query '{query[:50]}...': {e}")
            return {
                "query": query,
                "failure_type": "error",
                "error": str(e),
            }

    def run_evaluation(self) -> Dict[str, Any]:
        """
        Run the full weekly evaluation.

        Returns:
            Complete evaluation results
        """
        logger.info("Starting weekly RAG evaluation...")

        # Determine query source
        # Weekly eval is for PRODUCTION monitoring only - no synthetic fallback
        if self.config.local_queries_file:
            queries = self.load_local_queries(self.config.local_queries_file)
        else:
            queries = self.fetch_queries_from_app_insights()

        if not queries:
            logger.warning("=" * 60)
            logger.warning("NO PRODUCTION QUERIES AVAILABLE FOR WEEKLY EVALUATION")
            logger.warning("=" * 60)
            logger.warning("Weekly eval requires real production queries from App Insights.")
            logger.warning("")
            logger.warning("Options:")
            logger.warning("  1. Configure LOG_ANALYTICS_WORKSPACE_ID for App Insights")
            logger.warning("  2. Use --local-queries to provide a queries file")
            logger.warning("  3. Run pre-production tests instead:")
            logger.warning("     pytest apps/backend/tests/test_rag_evaluation.py -v")
            logger.warning("")
            logger.warning("Note: Synthetic test datasets should NOT be used for weekly eval.")
            logger.warning("      They are for CI/CD pre-production testing only.")
            logger.warning("=" * 60)
            return {
                "error": "No production queries available",
                "suggestion": "Configure App Insights or use --local-queries",
            }

        # Evaluate all queries
        logger.info(f"Evaluating {len(queries)} queries...")
        eval_results = []
        for i, query_data in enumerate(queries):
            if i % 10 == 0:
                logger.info(f"Progress: {i}/{len(queries)} queries evaluated")
            result = self.evaluate_query(query_data)
            eval_results.append(result)

        # Run diagnostics on sample
        diagnostic_sample = queries[:self.config.diagnostic_sample_size]
        logger.info(f"Running diagnostics on {len(diagnostic_sample)} queries...")
        diagnostic_results = []
        for query_data in diagnostic_sample:
            diag = self.run_diagnostics(query_data)
            diagnostic_results.append(diag)

        # Calculate summary
        summary = self._calculate_summary(eval_results, diagnostic_results)

        # Build full report
        report = {
            "metadata": {
                "evaluation_date": datetime.now().isoformat(),
                "week_start": (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"),
                "week_end": datetime.now().strftime("%Y-%m-%d"),
                "total_queries": len(queries),
                "diagnostic_sample_size": len(diagnostic_sample),
            },
            "summary": asdict(summary),
            "metrics_breakdown": self._calculate_metrics_breakdown(eval_results),
            "diagnostics_breakdown": self._calculate_diagnostics_breakdown(diagnostic_results),
            "failed_cases": [r for r in eval_results if not r.get("passed", True)][:20],
            "flagged_queries": self._get_flagged_queries(eval_results),
            "recommendations": self._generate_recommendations(summary, diagnostic_results),
        }

        return report

    def _calculate_summary(
        self,
        eval_results: List[Dict],
        diagnostic_results: List[Dict]
    ) -> EvalSummary:
        """Calculate summary statistics."""
        total = len(eval_results)
        passed = sum(1 for r in eval_results if r.get("passed", False))

        # Calculate metric averages
        metrics_lists = {
            "faithfulness": [],
            "answer_relevancy": [],
            "context_precision": [],
            "policy_citation": [],
        }

        for r in eval_results:
            metrics = r.get("metrics", {})
            for key in metrics_lists:
                if key in metrics and metrics[key] is not None:
                    metrics_lists[key].append(metrics[key])

        def safe_avg(lst):
            return sum(lst) / len(lst) if lst else 0.0

        # Count diagnostic issues
        hallucination_risk = sum(
            1 for r in eval_results
            if r.get("metrics", {}).get("faithfulness", 1) < 0.85
        )

        lost_in_middle = sum(
            d.get("lost_in_middle_count", 0)
            for d in diagnostic_results
        )

        retrieval_failures = sum(
            1 for d in diagnostic_results
            if d.get("failure_type") in ["retrieval", "both"]
        )

        generation_failures = sum(
            1 for d in diagnostic_results
            if d.get("failure_type") in ["generation", "both"]
        )

        return EvalSummary(
            total_queries=total,
            passed_queries=passed,
            failed_queries=total - passed,
            pass_rate=round(passed / total * 100, 1) if total > 0 else 0,
            avg_faithfulness=round(safe_avg(metrics_lists["faithfulness"]), 3),
            avg_answer_relevancy=round(safe_avg(metrics_lists["answer_relevancy"]), 3),
            avg_context_precision=round(safe_avg(metrics_lists["context_precision"]), 3),
            avg_policy_citation=round(safe_avg(metrics_lists["policy_citation"]), 3),
            hallucination_risk_count=hallucination_risk,
            lost_in_middle_count=lost_in_middle,
            retrieval_failures=retrieval_failures,
            generation_failures=generation_failures,
            evaluation_date=datetime.now().isoformat(),
            week_start=(datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"),
            week_end=datetime.now().strftime("%Y-%m-%d"),
        )

    def _calculate_metrics_breakdown(self, eval_results: List[Dict]) -> Dict[str, Any]:
        """Calculate detailed metrics breakdown."""
        # Thresholds calibrated for healthcare RAG
        # Context precision lowered to 0.60 (Jan 2026) - see metrics.py for rationale
        thresholds = {
            "faithfulness": 0.85,
            "answer_relevancy": 0.70,
            "context_precision": 0.60,  # Lowered from 0.75 for healthcare RAG
            "policy_citation": 0.80,
        }

        breakdown = {}
        for metric, threshold in thresholds.items():
            scores = [
                r.get("metrics", {}).get(metric)
                for r in eval_results
                if r.get("metrics", {}).get(metric) is not None
            ]

            if scores:
                breakdown[metric] = {
                    "average": round(sum(scores) / len(scores), 3),
                    "min": round(min(scores), 3),
                    "max": round(max(scores), 3),
                    "threshold": threshold,
                    "above_threshold": sum(1 for s in scores if s >= threshold),
                    "below_threshold": sum(1 for s in scores if s < threshold),
                }

        return breakdown

    def _calculate_diagnostics_breakdown(self, diagnostic_results: List[Dict]) -> Dict[str, Any]:
        """Calculate diagnostics breakdown."""
        failure_types = {}
        for d in diagnostic_results:
            ft = d.get("failure_type", "unknown")
            failure_types[ft] = failure_types.get(ft, 0) + 1

        position_breakdown = {"top": 0, "middle": 0, "bottom": 0, "not_found": 0}
        for d in diagnostic_results:
            pb = d.get("position_breakdown", {})
            for pos, count in pb.items():
                position_breakdown[pos] = position_breakdown.get(pos, 0) + count

        return {
            "failure_types": failure_types,
            "position_breakdown": position_breakdown,
            "total_lost_in_middle": sum(d.get("lost_in_middle_count", 0) for d in diagnostic_results),
        }

    def _get_flagged_queries(self, eval_results: List[Dict]) -> List[Dict]:
        """Get queries flagged for review."""
        flagged = []

        for r in eval_results:
            metrics = r.get("metrics", {})
            reasons = []

            # Low faithfulness
            if metrics.get("faithfulness", 1) < 0.7:
                reasons.append("Very low faithfulness - hallucination risk")
            elif metrics.get("faithfulness", 1) < 0.85:
                reasons.append("Low faithfulness")

            # Low citation
            if metrics.get("policy_citation", 1) < 0.6:
                reasons.append("Missing policy citations")

            if reasons:
                flagged.append({
                    "query": r.get("query", "")[:100],
                    "faithfulness": metrics.get("faithfulness"),
                    "reasons": reasons,
                })

        return flagged[:10]  # Top 10 flagged

    def _generate_recommendations(
        self,
        summary: EvalSummary,
        diagnostic_results: List[Dict]
    ) -> List[str]:
        """Generate actionable recommendations."""
        recommendations = []

        # Pass rate recommendations
        if summary.pass_rate < 80:
            recommendations.append(
                f"Overall pass rate ({summary.pass_rate}%) below 80% target. "
                "Review failing test cases and prioritize fixes."
            )

        # Faithfulness recommendations
        if summary.avg_faithfulness < 0.85:
            recommendations.append(
                f"Average faithfulness ({summary.avg_faithfulness}) below 0.85 threshold. "
                "Consider strengthening grounding constraints in system prompt."
            )

        # Hallucination recommendations
        if summary.hallucination_risk_count > summary.total_queries * 0.1:
            recommendations.append(
                f"{summary.hallucination_risk_count} queries at hallucination risk. "
                "Review Cohere rerank min_score threshold and retrieval top_k."
            )

        # Lost in middle recommendations
        if summary.lost_in_middle_count > 5:
            recommendations.append(
                f"Lost-in-the-Middle detected in {summary.lost_in_middle_count} claims. "
                "Consider context reordering or reducing context window."
            )

        # Retrieval failure recommendations
        if summary.retrieval_failures > summary.total_queries * 0.15:
            recommendations.append(
                f"{summary.retrieval_failures} retrieval failures detected. "
                "Review synonym expansion and search configuration."
            )

        # Generation failure recommendations
        if summary.generation_failures > summary.total_queries * 0.1:
            recommendations.append(
                f"{summary.generation_failures} generation failures detected. "
                "Review system prompt and temperature settings."
            )

        if not recommendations:
            recommendations.append(
                "RAG pipeline performing within acceptable thresholds. "
                "Continue monitoring weekly."
            )

        return recommendations

    def generate_html_report(self, report: Dict[str, Any]) -> str:
        """Generate HTML report for email."""
        summary = report.get("summary", {})
        metrics = report.get("metrics_breakdown", {})
        recommendations = report.get("recommendations", [])
        flagged = report.get("flagged_queries", [])

        html = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; color: #333; }}
        h1 {{ color: #006332; }}
        h2 {{ color: #30AE6E; border-bottom: 2px solid #30AE6E; padding-bottom: 5px; }}
        .summary-box {{ background: #DFF9EB; padding: 15px; border-radius: 8px; margin: 15px 0; }}
        .metric {{ display: inline-block; margin: 10px; padding: 15px; background: white; border-radius: 5px; text-align: center; min-width: 120px; }}
        .metric-value {{ font-size: 24px; font-weight: bold; color: #006332; }}
        .metric-label {{ font-size: 12px; color: #666; }}
        .pass {{ color: #30AE6E; }}
        .fail {{ color: #E74C3C; }}
        .warning {{ color: #F39C12; }}
        table {{ border-collapse: collapse; width: 100%; margin: 15px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 10px; text-align: left; }}
        th {{ background: #006332; color: white; }}
        tr:nth-child(even) {{ background: #f9f9f9; }}
        .recommendation {{ background: #FFF3CD; padding: 10px; margin: 5px 0; border-left: 4px solid #F39C12; }}
        .flagged {{ background: #FADBD8; padding: 10px; margin: 5px 0; border-left: 4px solid #E74C3C; }}
    </style>
</head>
<body>
    <h1>RUSH Policy RAG - Weekly Evaluation Report</h1>

    <p><strong>Period:</strong> {summary.get('week_start', 'N/A')} to {summary.get('week_end', 'N/A')}</p>
    <p><strong>Generated:</strong> {summary.get('evaluation_date', 'N/A')}</p>

    <h2>Summary</h2>
    <div class="summary-box">
        <div class="metric">
            <div class="metric-value {'pass' if summary.get('pass_rate', 0) >= 80 else 'fail'}">{summary.get('pass_rate', 0)}%</div>
            <div class="metric-label">Pass Rate</div>
        </div>
        <div class="metric">
            <div class="metric-value">{summary.get('total_queries', 0)}</div>
            <div class="metric-label">Total Queries</div>
        </div>
        <div class="metric">
            <div class="metric-value pass">{summary.get('passed_queries', 0)}</div>
            <div class="metric-label">Passed</div>
        </div>
        <div class="metric">
            <div class="metric-value fail">{summary.get('failed_queries', 0)}</div>
            <div class="metric-label">Failed</div>
        </div>
    </div>

    <h2>RAG Triad Metrics</h2>
    <table>
        <tr>
            <th>Metric</th>
            <th>Average</th>
            <th>Threshold</th>
            <th>Status</th>
        </tr>
        <tr>
            <td>Faithfulness</td>
            <td>{summary.get('avg_faithfulness', 'N/A')}</td>
            <td>0.85</td>
            <td class="{'pass' if summary.get('avg_faithfulness', 0) >= 0.85 else 'fail'}">
                {'PASS' if summary.get('avg_faithfulness', 0) >= 0.85 else 'FAIL'}
            </td>
        </tr>
        <tr>
            <td>Answer Relevancy</td>
            <td>{summary.get('avg_answer_relevancy', 'N/A')}</td>
            <td>0.70</td>
            <td class="{'pass' if summary.get('avg_answer_relevancy', 0) >= 0.70 else 'fail'}">
                {'PASS' if summary.get('avg_answer_relevancy', 0) >= 0.70 else 'FAIL'}
            </td>
        </tr>
        <tr>
            <td>Context Precision</td>
            <td>{summary.get('avg_context_precision', 'N/A')}</td>
            <td>0.60</td>
            <td class="{'pass' if summary.get('avg_context_precision', 0) >= 0.60 else 'fail'}">
                {'PASS' if summary.get('avg_context_precision', 0) >= 0.60 else 'FAIL'}
            </td>
        </tr>
        <tr>
            <td>Policy Citation</td>
            <td>{summary.get('avg_policy_citation', 'N/A')}</td>
            <td>0.80</td>
            <td class="{'pass' if summary.get('avg_policy_citation', 0) >= 0.80 else 'fail'}">
                {'PASS' if summary.get('avg_policy_citation', 0) >= 0.80 else 'FAIL'}
            </td>
        </tr>
    </table>

    <h2>Diagnostic Breakdown</h2>
    <table>
        <tr>
            <th>Issue Type</th>
            <th>Count</th>
        </tr>
        <tr>
            <td>Hallucination Risk (Faithfulness &lt; 0.85)</td>
            <td class="{'fail' if summary.get('hallucination_risk_count', 0) > 5 else ''}">{summary.get('hallucination_risk_count', 0)}</td>
        </tr>
        <tr>
            <td>Lost in the Middle</td>
            <td class="{'warning' if summary.get('lost_in_middle_count', 0) > 3 else ''}">{summary.get('lost_in_middle_count', 0)}</td>
        </tr>
        <tr>
            <td>Retrieval Failures</td>
            <td>{summary.get('retrieval_failures', 0)}</td>
        </tr>
        <tr>
            <td>Generation Failures</td>
            <td>{summary.get('generation_failures', 0)}</td>
        </tr>
    </table>

    <h2>Recommendations</h2>
    {"".join(f'<div class="recommendation">{rec}</div>' for rec in recommendations)}

    <h2>Flagged Queries for Review</h2>
    {"".join(f'''<div class="flagged">
        <strong>Query:</strong> {f['query'][:80]}...<br>
        <strong>Faithfulness:</strong> {f.get('faithfulness', 'N/A')}<br>
        <strong>Reasons:</strong> {', '.join(f.get('reasons', []))}
    </div>''' for f in flagged[:5])}

    <hr>
    <p style="color: #666; font-size: 12px;">
        This report was automatically generated by the RUSH Policy RAG Evaluation System.<br>
        For questions, contact the AI Innovation team.
    </p>
</body>
</html>
"""
        return html

    def send_email_report(self, report: Dict[str, Any], html_content: str):
        """Send evaluation report via email.

        Uses Azure Communication Services if configured, falls back to SMTP.
        """
        if self.config.dry_run:
            logger.info("Dry run - skipping email send")
            return

        # Try Azure Communication Services first (recommended)
        azure_conn_str = os.getenv("AZURE_COMM_CONNECTION_STRING")
        azure_sender = os.getenv("AZURE_COMM_SENDER_ADDRESS")

        if azure_conn_str and azure_sender and AZURE_COMM_AVAILABLE:
            self._send_email_azure_comm(report, html_content, azure_conn_str, azure_sender)
        else:
            # Fall back to SMTP
            self._send_email_smtp(report, html_content)

    def _send_email_azure_comm(
        self,
        report: Dict[str, Any],
        html_content: str,
        connection_string: str,
        sender_address: str
    ):
        """Send email via Azure Communication Services."""
        try:
            email_client = EmailClient.from_connection_string(connection_string)

            summary = report.get("summary", {})
            subject = f"RUSH Policy RAG - Weekly Evaluation Report ({summary.get('week_end', 'N/A')})"

            # Plain text version
            text_content = f"""
RUSH Policy RAG - Weekly Evaluation Report

Period: {summary.get('week_start')} to {summary.get('week_end')}

Summary:
- Pass Rate: {summary.get('pass_rate')}%
- Total Queries: {summary.get('total_queries')}
- Passed: {summary.get('passed_queries')}
- Failed: {summary.get('failed_queries')}

Average Metrics:
- Faithfulness: {summary.get('avg_faithfulness')}
- Answer Relevancy: {summary.get('avg_answer_relevancy')}
- Context Precision: {summary.get('avg_context_precision')}
- Policy Citation: {summary.get('avg_policy_citation')}

See the HTML version of this email for full details.
"""

            # Build message
            message = {
                "senderAddress": sender_address,
                "recipients": {
                    "to": [{"address": email} for email in self.config.email_recipients]
                },
                "content": {
                    "subject": subject,
                    "plainText": text_content,
                    "html": html_content,
                },
            }

            # Send email (synchronous)
            poller = email_client.begin_send(message)
            result = poller.result()

            logger.info(f"Email sent via Azure Communication Services to {self.config.email_recipients}")
            logger.info(f"Message ID: {result.get('id', 'N/A')}")

        except Exception as e:
            logger.error(f"Failed to send email via Azure Communication Services: {e}")
            logger.info("Falling back to SMTP...")
            self._send_email_smtp(report, html_content)

    def _send_email_smtp(self, report: Dict[str, Any], html_content: str):
        """Send email via SMTP (legacy fallback)."""
        smtp_server = os.getenv("SMTP_SERVER", "smtp.office365.com")
        smtp_user = os.getenv("SMTP_USER")
        smtp_pass = os.getenv("SMTP_PASS")
        smtp_from = os.getenv("SMTP_FROM", smtp_user)

        if not smtp_user or not smtp_pass:
            logger.warning("No email credentials configured (Azure Comm or SMTP), skipping email")
            return

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"RUSH Policy RAG - Weekly Evaluation Report ({report['summary']['week_end']})"
            msg["From"] = smtp_from
            msg["To"] = ", ".join(self.config.email_recipients)

            # Plain text version
            summary = report.get("summary", {})
            text_content = f"""
RUSH Policy RAG - Weekly Evaluation Report

Period: {summary.get('week_start')} to {summary.get('week_end')}

Summary:
- Pass Rate: {summary.get('pass_rate')}%
- Total Queries: {summary.get('total_queries')}
- Passed: {summary.get('passed_queries')}
- Failed: {summary.get('failed_queries')}

Average Metrics:
- Faithfulness: {summary.get('avg_faithfulness')}
- Answer Relevancy: {summary.get('avg_answer_relevancy')}
- Context Precision: {summary.get('avg_context_precision')}
- Policy Citation: {summary.get('avg_policy_citation')}

See attached HTML report for details.
"""

            msg.attach(MIMEText(text_content, "plain"))
            msg.attach(MIMEText(html_content, "html"))

            with smtplib.SMTP(smtp_server, 587) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.sendmail(
                    smtp_from,
                    self.config.email_recipients,
                    msg.as_string()
                )

            logger.info(f"Email report sent via SMTP to {self.config.email_recipients}")

        except Exception as e:
            logger.error(f"Failed to send email via SMTP: {e}")

    def save_report(self, report: Dict[str, Any]):
        """Save report to files."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Save JSON report
        json_path = self.output_dir / f"eval_report_{timestamp}.json"
        with open(json_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
        logger.info(f"JSON report saved to {json_path}")

        # Save HTML report
        html_content = self.generate_html_report(report)
        html_path = self.output_dir / f"eval_report_{timestamp}.html"
        with open(html_path, "w") as f:
            f.write(html_content)
        logger.info(f"HTML report saved to {html_path}")

        return json_path, html_path

    def run(self) -> Dict[str, Any]:
        """Execute the full weekly evaluation pipeline."""
        logger.info("=" * 60)
        logger.info("RUSH Policy RAG - Weekly Evaluation")
        logger.info("=" * 60)

        # Run evaluation
        report = self.run_evaluation()

        if "error" in report:
            logger.error(f"Evaluation failed: {report['error']}")
            return report

        # Save reports
        json_path, html_path = self.save_report(report)

        # Generate and send email
        html_content = self.generate_html_report(report)
        self.send_email_report(report, html_content)

        # Print summary
        summary = report.get("summary", {})
        logger.info("=" * 60)
        logger.info("Evaluation Complete")
        logger.info(f"Pass Rate: {summary.get('pass_rate')}%")
        logger.info(f"Avg Faithfulness: {summary.get('avg_faithfulness')}")
        logger.info(f"Reports saved to: {self.output_dir}")
        logger.info("=" * 60)

        return report


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Run weekly RAG evaluation")
    parser.add_argument("--sample", type=int, default=100, help="Number of queries to evaluate")
    parser.add_argument("--diagnostic-sample", type=int, default=20, help="Number of queries for diagnostics")
    parser.add_argument("--dry-run", action="store_true", help="Skip email sending")
    parser.add_argument("--local-queries", type=str, help="Path to local queries JSON file")
    parser.add_argument("--output-dir", type=str, default="eval_reports", help="Output directory for reports")
    parser.add_argument("--email", type=str, action="append", help="Email recipient (can be specified multiple times)")

    args = parser.parse_args()

    config = WeeklyEvalConfig(
        sample_size=args.sample,
        diagnostic_sample_size=args.diagnostic_sample,
        dry_run=args.dry_run,
        local_queries_file=args.local_queries,
        output_dir=args.output_dir,
        email_recipients=args.email if args.email else None,
    )

    evaluator = WeeklyEvaluator(config)
    report = evaluator.run()

    # Exit with error code if pass rate is too low
    pass_rate = report.get("summary", {}).get("pass_rate", 0)
    if pass_rate < 70:
        logger.warning(f"Pass rate {pass_rate}% below 70% minimum threshold")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
