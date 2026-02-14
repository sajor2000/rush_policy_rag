#!/usr/bin/env python3
"""
Combined Weekly Report: Technical Quality + Executive Usage.

Merges DeepEval quality metrics with executive usage analytics into
a single weekly email sent to stakeholders.

This script orchestrates both technical evaluation and usage analytics,
combining them into a unified report that provides comprehensive visibility
into both system performance and user engagement.

Features:
- Technical Quality Metrics (DeepEval: faithfulness, answer_relevancy, etc.)
- Executive Usage Analytics (query volume, success rates, categories)
- Merged recommendations and flagged queries
- Single consolidated email to stakeholders
- Historical artifact storage (JSON + HTML)

Usage:
    # Full weekly report (default)
    python scripts/run_combined_weekly_report.py

    # Dry run (no email)
    python scripts/run_combined_weekly_report.py --dry-run

    # Custom recipient
    python scripts/run_combined_weekly_report.py --email user@rush.edu

    # Use local queries for evaluation
    python scripts/run_combined_weekly_report.py --local-queries apps/backend/data/weekly_eval_queries.json

    # Customize lookback period for usage data
    python scripts/run_combined_weekly_report.py --days 14

    # Limit evaluation sample size
    python scripts/run_combined_weekly_report.py --sample 30

Environment Variables:
    AOAI_ENDPOINT: Azure OpenAI endpoint
    AOAI_API_KEY: Azure OpenAI API key
    AOAI_CHAT_DEPLOYMENT: GPT-4.1 deployment name
    STORAGE_CONNECTION_STRING: Azure Storage connection string
    AZURE_COMM_CONNECTION_STRING: Azure Communication Services
    AZURE_COMM_SENDER_ADDRESS: Sender email address
    BACKEND_URL: Backend API URL (for evaluation)
    LOG_ANALYTICS_WORKSPACE_ID: App Insights workspace (optional)

Output:
    - Combined JSON report (eval_reports/combined_report_TIMESTAMP.json)
    - Combined HTML report (eval_reports/combined_report_TIMESTAMP.html)
    - Email to stakeholders (if not dry run)
"""

import os
import sys
import json
import argparse
import logging
import asyncio
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import asdict

# SSL fix for corporate proxies
try:
    import ssl_fix  # noqa: F401 — side-effect import for corporate proxy SSL
except ImportError:
    pass

# Add parent directory to path for imports
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "apps" / "backend"))

from dotenv import load_dotenv

# Load environment
load_dotenv(REPO_ROOT / ".env")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Azure Communication Services (optional)
try:
    from azure.communication.email import EmailClient
    AZURE_COMM_AVAILABLE = True
except ImportError:
    AZURE_COMM_AVAILABLE = False

# Import evaluation components
try:
    from weekly_eval import WeeklyEvaluator, WeeklyEvalConfig, EvalSummary
    WEEKLY_EVAL_AVAILABLE = True
except ImportError:
    logger.warning("weekly_eval module not available")
    WEEKLY_EVAL_AVAILABLE = False

# Import executive report components
try:
    from generate_executive_report import ExecutiveReportGenerator, ExecutiveReportConfig
    EXEC_REPORT_AVAILABLE = True
except ImportError:
    logger.warning("generate_executive_report module not available")
    EXEC_REPORT_AVAILABLE = False


class CombinedWeeklyReportConfig:
    """Configuration for combined weekly report."""

    def __init__(
        self,
        email_recipients: Optional[List[str]] = None,
        dry_run: bool = False,
        output_dir: str = "eval_reports",
        sample_size: int = 50,
        local_queries_file: Optional[str] = None,
        days: int = 7,
    ):
        default_recipients = os.getenv("WEEKLY_REPORT_RECIPIENTS", "juan_rojas@rush.edu").split(",")
        self.email_recipients = email_recipients or default_recipients
        self.dry_run = dry_run
        self.output_dir = output_dir
        self.sample_size = sample_size
        self.local_queries_file = local_queries_file
        self.days = days

        # Ensure output directory exists
        Path(output_dir).mkdir(exist_ok=True)


class CombinedWeeklyReporter:
    """Generates combined technical quality + executive usage weekly reports."""

    def __init__(self, config: CombinedWeeklyReportConfig):
        self.config = config
        self.output_dir = Path(config.output_dir)
        self.output_dir.mkdir(exist_ok=True)

    async def run_technical_evaluation(self) -> Optional[Dict[str, Any]]:
        """
        Run technical quality evaluation using WeeklyEvaluator.

        Returns:
            Evaluation report dict or None if evaluation fails
        """
        if not WEEKLY_EVAL_AVAILABLE:
            logger.error("WeeklyEvaluator not available - skipping technical evaluation")
            return None

        try:
            logger.info("=" * 60)
            logger.info("Running Technical Quality Evaluation...")
            logger.info("=" * 60)

            # Configure weekly evaluator
            eval_config = WeeklyEvalConfig(
                sample_size=self.config.sample_size,
                diagnostic_sample_size=min(20, self.config.sample_size // 5),
                email_recipients=self.config.email_recipients,
                dry_run=True,  # Don't send separate email
                local_queries_file=self.config.local_queries_file,
                output_dir=str(self.output_dir),
            )

            # Run evaluation
            evaluator = WeeklyEvaluator(eval_config)
            report = evaluator.run_evaluation()

            if "error" in report:
                logger.error(f"Technical evaluation failed: {report['error']}")
                return None

            logger.info("Technical evaluation completed successfully")
            return report

        except Exception as e:
            logger.error(f"Technical evaluation error: {e}", exc_info=True)
            return None

    async def run_usage_analytics(self) -> Optional[Dict[str, Any]]:
        """
        Run executive usage analytics using ExecutiveReportGenerator.

        Returns:
            Usage report dict or None if generation fails
        """
        if not EXEC_REPORT_AVAILABLE:
            logger.error("ExecutiveReportGenerator not available - skipping usage analytics")
            return None

        try:
            logger.info("=" * 60)
            logger.info("Running Executive Usage Analytics...")
            logger.info("=" * 60)

            # Calculate date range
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date = (datetime.now() - timedelta(days=self.config.days)).strftime("%Y-%m-%d")

            # Configure executive report generator
            exec_config = ExecutiveReportConfig(
                start_date=start_date,
                end_date=end_date,
                email_recipients=self.config.email_recipients,
                dry_run=True,  # Don't send separate email
                output_dir=str(self.output_dir),
                include_sample_questions=True,
                sample_question_count=10,
            )

            # Run usage analytics
            generator = ExecutiveReportGenerator(exec_config)
            report = await generator.generate_report()

            if "error" in report:
                logger.error(f"Usage analytics failed: {report['error']}")
                return None

            logger.info("Usage analytics completed successfully")
            return report

        except Exception as e:
            logger.error(f"Usage analytics error: {e}", exc_info=True)
            return None

    def merge_reports(
        self,
        technical_report: Optional[Dict[str, Any]],
        usage_report: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Merge technical and usage reports into unified report.

        Args:
            technical_report: Technical quality evaluation report
            usage_report: Executive usage analytics report

        Returns:
            Merged report with both components
        """
        # Determine report period
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=self.config.days)).strftime("%Y-%m-%d")

        # Build merged report structure
        merged = {
            "metadata": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "period_start": start_date,
                "period_end": end_date,
                "report_type": "combined_weekly",
                "components_included": [],
            },
            "executive_summary": {},
            "usage_analytics": {},
            "quality_metrics": {},
            "issues_and_flags": [],
            "recommendations": [],
            "status": "partial" if not (technical_report and usage_report) else "complete",
        }

        # Merge usage analytics
        if usage_report:
            merged["metadata"]["components_included"].append("usage_analytics")
            merged["usage_analytics"] = {
                "summary": usage_report.get("summary", {}),
                "engagement": usage_report.get("engagement", {}),
                "insights": usage_report.get("insights", []),
            }
            merged["executive_summary"].update({
                "total_queries": usage_report.get("summary", {}).get("total_queries", 0),
                "success_rate": usage_report.get("summary", {}).get("success_rate", 0),
                "avg_response_time_ms": usage_report.get("summary", {}).get("avg_response_time_ms", 0),
                "queries_needing_attention": usage_report.get("summary", {}).get("queries_needing_attention", 0),
            })
            merged["recommendations"].extend(usage_report.get("recommendations", []))

        # Merge technical quality
        if technical_report:
            merged["metadata"]["components_included"].append("quality_metrics")
            merged["quality_metrics"] = {
                "summary": technical_report.get("summary", {}),
                "metrics_breakdown": technical_report.get("metrics_breakdown", {}),
                "diagnostics_breakdown": technical_report.get("diagnostics_breakdown", {}),
            }
            merged["executive_summary"].update({
                "eval_pass_rate": technical_report.get("summary", {}).get("pass_rate", 0),
                "avg_faithfulness": technical_report.get("summary", {}).get("avg_faithfulness", 0),
                "avg_answer_relevancy": technical_report.get("summary", {}).get("avg_answer_relevancy", 0),
                "avg_policy_citation": technical_report.get("summary", {}).get("avg_policy_citation", 0),
            })

            # Add flagged queries to issues
            flagged = technical_report.get("flagged_queries", [])
            for f in flagged[:5]:  # Top 5
                merged["issues_and_flags"].append({
                    "source": "technical_evaluation",
                    "type": "low_quality",
                    "query": f.get("query", ""),
                    "details": f.get("reasons", []),
                })

            merged["recommendations"].extend(technical_report.get("recommendations", []))

        # Add trend analysis if both reports available
        if technical_report and usage_report:
            merged["trend_analysis"] = self._calculate_trends(technical_report, usage_report)

        return merged

    def _calculate_trends(
        self,
        technical_report: Dict[str, Any],
        usage_report: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Calculate trend analysis from both reports."""
        trends = {
            "queries_trend": "stable",  # Would require historical data
            "quality_trend": "stable",
            "key_changes": [],
        }

        # Check for significant changes (placeholder - would need historical comparison)
        tech_summary = technical_report.get("summary", {})
        usage_summary = usage_report.get("summary", {})

        if tech_summary.get("pass_rate", 0) >= 85:
            trends["quality_trend"] = "improving"
        elif tech_summary.get("pass_rate", 0) < 75:
            trends["quality_trend"] = "declining"

        if usage_summary.get("success_rate", 0) >= 90:
            trends["queries_trend"] = "improving"
        elif usage_summary.get("success_rate", 0) < 80:
            trends["queries_trend"] = "needs_attention"

        return trends

    def generate_combined_html_report(self, report: Dict[str, Any]) -> str:
        """
        Generate unified HTML email report.

        Args:
            report: Merged report dict

        Returns:
            HTML content for email
        """
        metadata = report.get("metadata", {})
        summary = report.get("executive_summary", {})
        usage = report.get("usage_analytics", {})
        quality = report.get("quality_metrics", {})
        issues = report.get("issues_and_flags", [])
        recommendations = report.get("recommendations", [])
        trends = report.get("trend_analysis", {})

        # Determine overall status
        status_class = "success"
        status_text = "Excellent"
        if summary.get("success_rate", 0) < 85 or summary.get("eval_pass_rate", 0) < 80:
            status_class = "warning"
            status_text = "Needs Attention"
        if summary.get("success_rate", 0) >= 90 and summary.get("eval_pass_rate", 0) >= 85:
            status_class = "success"
            status_text = "Excellent"

        # Build usage dashboard section
        usage_dashboard = ""
        if usage:
            usage_summary = usage.get("summary", {})
            engagement = usage.get("engagement", {})
            insights = usage.get("insights", [])

            # Top categories
            categories = engagement.get("questions_by_category", {})
            top_categories = sorted(categories.items(), key=lambda x: -x[1])[:3]
            category_text = ", ".join([
                f"{cat.replace('_', ' ').title()} ({count})"
                for cat, count in top_categories
            ])

            usage_dashboard = f"""
    <h2>Usage Dashboard</h2>
    <div class="metrics-grid">
        <div class="metric-card">
            <div class="metric-value">{summary.get('total_queries', 0):,}</div>
            <div class="metric-label">Total Queries</div>
        </div>
        <div class="metric-card">
            <div class="metric-value {'success' if summary.get('success_rate', 0) >= 85 else 'warning' if summary.get('success_rate', 0) >= 70 else 'danger'}">
                {summary.get('success_rate', 0)}%
            </div>
            <div class="metric-label">Success Rate</div>
        </div>
        <div class="metric-card">
            <div class="metric-value">{summary.get('avg_response_time_ms', 0):,}ms</div>
            <div class="metric-label">Avg Response Time</div>
        </div>
        <div class="metric-card">
            <div class="metric-value {'warning' if summary.get('queries_needing_attention', 0) > 10 else ''}">{summary.get('queries_needing_attention', 0)}</div>
            <div class="metric-label">Needs Review</div>
        </div>
    </div>

    <div class="summary-box">
        <strong>Top Categories:</strong> {category_text or 'N/A'}
    </div>

    <h3>Key Insights</h3>
    {"".join(f'<div class="insight">{insight}</div>' for insight in insights)}
"""

        # Build quality metrics section
        quality_metrics = ""
        if quality:
            qual_summary = quality.get("summary", {})
            metrics_breakdown = quality.get("metrics_breakdown", {})

            quality_metrics = f"""
    <h2>Quality Metrics</h2>
    <div class="summary-box">
        <div class="metric-inline">
            <span class="metric-label">Evaluation Pass Rate:</span>
            <span class="metric-value {'pass' if summary.get('eval_pass_rate', 0) >= 80 else 'fail'}">
                {summary.get('eval_pass_rate', 0)}%
            </span>
        </div>
        <div class="metric-inline">
            <span class="metric-label">Queries Evaluated:</span>
            <span>{qual_summary.get('total_queries', 0)}</span>
        </div>
    </div>

    <table>
        <tr>
            <th>Metric</th>
            <th>Score</th>
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
            <td>{metrics_breakdown.get('context_precision', {}).get('average', 'N/A')}</td>
            <td>0.60</td>
            <td class="{'pass' if metrics_breakdown.get('context_precision', {}).get('average', 0) >= 0.60 else 'fail'}">
                {'PASS' if metrics_breakdown.get('context_precision', {}).get('average', 0) >= 0.60 else 'FAIL'}
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
"""

        # Build issues section
        issues_section = ""
        if issues:
            issues_rows = ""
            for issue in issues[:5]:  # Top 5
                issues_rows += f"""
        <div class="flagged">
            <strong>Query:</strong> {issue.get('query', 'N/A')[:100]}...<br>
            <strong>Type:</strong> {issue.get('type', 'N/A')}<br>
            <strong>Details:</strong> {', '.join(issue.get('details', [])[:3])}
        </div>"""

            issues_section = f"""
    <h2>Issues & Flagged Queries</h2>
    <p>Total flagged: {len(issues)}</p>
    {issues_rows}
"""

        # Build trend analysis section
        trend_section = ""
        if trends:
            trend_section = f"""
    <h2>Trend Analysis</h2>
    <div class="summary-box">
        <div><strong>Query Volume Trend:</strong> {trends.get('queries_trend', 'N/A').replace('_', ' ').title()}</div>
        <div><strong>Quality Trend:</strong> {trends.get('quality_trend', 'N/A').replace('_', ' ').title()}</div>
    </div>
"""

        # Build recommendations section
        recommendations_section = ""
        if recommendations:
            # Deduplicate recommendations
            unique_recs = list(dict.fromkeys(recommendations))
            rec_items = "".join(
                f'<div class="recommendation">{rec}</div>'
                for rec in unique_recs[:8]  # Top 8
            )
            recommendations_section = f"""
    <h2>Recommendations</h2>
    {rec_items}
"""

        # Generate full HTML
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>RUSH Policy RAG - Combined Weekly Report</title>
    <style>
        body {{
            font-family: 'Segoe UI', Arial, sans-serif;
            margin: 0;
            padding: 0;
            background: #f5f5f5;
            color: #333;
        }}
        .container {{
            max-width: 1000px;
            margin: 0 auto;
            background: white;
            padding: 0;
        }}
        .header {{
            background: linear-gradient(135deg, #006332, #30AE6E);
            color: white;
            padding: 40px 30px;
            text-align: center;
        }}
        .header h1 {{
            margin: 0;
            font-size: 32px;
        }}
        .header p {{
            margin: 10px 0 0;
            opacity: 0.9;
            font-size: 16px;
        }}
        .content {{
            padding: 30px;
        }}
        .status-banner {{
            background: #{'DFF9EB' if status_class == 'success' else 'FFF3CD'};
            border-left: 5px solid #{'30AE6E' if status_class == 'success' else 'F39C12'};
            padding: 20px;
            margin: 20px 0;
            font-size: 20px;
            font-weight: 500;
        }}
        h2 {{
            color: #006332;
            font-size: 22px;
            margin-top: 35px;
            margin-bottom: 15px;
            border-bottom: 2px solid #30AE6E;
            padding-bottom: 8px;
        }}
        h3 {{
            color: #30AE6E;
            font-size: 18px;
            margin-top: 25px;
            margin-bottom: 10px;
        }}
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 15px;
            margin: 20px 0;
        }}
        .metric-card {{
            background: #f9f9f9;
            padding: 20px;
            border-radius: 8px;
            text-align: center;
            border: 1px solid #e0e0e0;
        }}
        .metric-value {{
            font-size: 28px;
            font-weight: bold;
            color: #006332;
            margin-bottom: 5px;
        }}
        .metric-label {{
            font-size: 13px;
            color: #666;
        }}
        .metric-inline {{
            display: inline-block;
            margin-right: 25px;
            margin-bottom: 10px;
        }}
        .summary-box {{
            background: #DFF9EB;
            padding: 15px;
            border-radius: 8px;
            margin: 15px 0;
        }}
        .success {{ color: #30AE6E; font-weight: bold; }}
        .warning {{ color: #F39C12; font-weight: bold; }}
        .danger {{ color: #E74C3C; font-weight: bold; }}
        .pass {{ color: #30AE6E; font-weight: bold; }}
        .fail {{ color: #E74C3C; font-weight: bold; }}
        table {{
            border-collapse: collapse;
            width: 100%;
            margin: 15px 0;
        }}
        th, td {{
            border: 1px solid #ddd;
            padding: 12px;
            text-align: left;
        }}
        th {{
            background: #006332;
            color: white;
            font-weight: 500;
        }}
        tr:nth-child(even) {{
            background: #f9f9f9;
        }}
        .insight {{
            background: #E8F6FF;
            padding: 12px;
            margin: 8px 0;
            border-left: 4px solid #3498DB;
            border-radius: 0 5px 5px 0;
        }}
        .recommendation {{
            background: #FFF3CD;
            padding: 12px;
            margin: 8px 0;
            border-left: 4px solid #F39C12;
            border-radius: 0 5px 5px 0;
        }}
        .flagged {{
            background: #FADBD8;
            padding: 12px;
            margin: 8px 0;
            border-left: 4px solid #E74C3C;
            border-radius: 0 5px 5px 0;
            font-size: 14px;
        }}
        .footer {{
            background: #f9f9f9;
            padding: 25px 30px;
            text-align: center;
            color: #666;
            font-size: 13px;
            border-top: 1px solid #e0e0e0;
        }}
        @media (max-width: 768px) {{
            .metrics-grid {{ grid-template-columns: repeat(2, 1fr); }}
            .content {{ padding: 20px; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>RUSH Policy RAG</h1>
            <h1>Weekly Report</h1>
            <p>Period: {metadata.get('period_start', 'N/A')} to {metadata.get('period_end', 'N/A')}</p>
            <p>Generated: {metadata.get('generated_at', 'N/A')[:10]}</p>
        </div>

        <div class="content">
            <div class="status-banner">
                {status_text} Performance — Status: {report.get('status', 'unknown').upper()}
            </div>

            {usage_dashboard}

            {quality_metrics}

            {issues_section}

            {trend_section}

            {recommendations_section}

            <h2>Next Steps</h2>
            <div class="summary-box">
                <ol>
                    <li>Review flagged queries requiring human attention</li>
                    <li>Address any failing quality metrics</li>
                    <li>Monitor trends for continued improvement</li>
                    <li>Update policies based on user question patterns</li>
                </ol>
            </div>
        </div>

        <div class="footer">
            <p><strong>RUSH Policy RAG System</strong></p>
            <p>This report was automatically generated by the RAG evaluation pipeline.</p>
            <p>For questions or feedback, contact the AI Innovation Team.</p>
        </div>
    </div>
</body>
</html>
"""
        return html

    async def send_email_report(self, report: Dict[str, Any], html_content: str):
        """
        Send combined weekly report via email.

        Uses Azure Communication Services (primary) or SMTP (fallback).

        Args:
            report: Merged report dict
            html_content: HTML email content
        """
        if self.config.dry_run:
            logger.info("Dry run - skipping email send")
            return

        # Try Azure Communication Services first
        azure_conn_str = os.getenv("AZURE_COMM_CONNECTION_STRING")
        azure_sender = os.getenv("AZURE_COMM_SENDER_ADDRESS")

        if azure_conn_str and azure_sender and AZURE_COMM_AVAILABLE:
            await self._send_email_azure_comm(report, html_content, azure_conn_str, azure_sender)
        else:
            # Fall back to SMTP
            self._send_email_smtp(report, html_content)

    async def _send_email_azure_comm(
        self,
        report: Dict[str, Any],
        html_content: str,
        connection_string: str,
        sender_address: str
    ):
        """Send email via Azure Communication Services."""
        try:
            email_client = EmailClient.from_connection_string(connection_string)

            metadata = report.get("metadata", {})
            period_end = metadata.get("period_end", "N/A")
            subject = f"RUSH Policy RAG — Weekly Report ({period_end})"

            # Plain text version
            summary = report.get("executive_summary", {})
            text_content = f"""
RUSH Policy RAG — Weekly Report

Period: {metadata.get('period_start', 'N/A')} to {period_end}

=== USAGE DASHBOARD ===
Total Queries: {summary.get('total_queries', 0):,}
Success Rate: {summary.get('success_rate', 0)}%
Avg Response Time: {summary.get('avg_response_time_ms', 0):,}ms
Queries Needing Attention: {summary.get('queries_needing_attention', 0)}

=== QUALITY METRICS ===
Evaluation Pass Rate: {summary.get('eval_pass_rate', 0)}%
Faithfulness: {summary.get('avg_faithfulness', 'N/A')}
Answer Relevancy: {summary.get('avg_answer_relevancy', 'N/A')}
Policy Citation: {summary.get('avg_policy_citation', 'N/A')}

See the HTML version of this email for complete details, insights, and recommendations.
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

            logger.info(f"Combined weekly report sent via Azure Communication Services to {self.config.email_recipients}")
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
            metadata = report.get("metadata", {})
            period_end = metadata.get("period_end", "N/A")

            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"RUSH Policy RAG — Weekly Report ({period_end})"
            msg["From"] = smtp_from
            msg["To"] = ", ".join(self.config.email_recipients)

            # Plain text version
            summary = report.get("executive_summary", {})
            text_content = f"""
RUSH Policy RAG — Weekly Report

Period: {metadata.get('period_start', 'N/A')} to {period_end}

=== USAGE DASHBOARD ===
Total Queries: {summary.get('total_queries', 0):,}
Success Rate: {summary.get('success_rate', 0)}%
Avg Response Time: {summary.get('avg_response_time_ms', 0):,}ms
Queries Needing Attention: {summary.get('queries_needing_attention', 0)}

=== QUALITY METRICS ===
Evaluation Pass Rate: {summary.get('eval_pass_rate', 0)}%
Faithfulness: {summary.get('avg_faithfulness', 'N/A')}
Answer Relevancy: {summary.get('avg_answer_relevancy', 'N/A')}
Policy Citation: {summary.get('avg_policy_citation', 'N/A')}

See attached HTML report for complete details.
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

            logger.info(f"Combined weekly report sent via SMTP to {self.config.email_recipients}")

        except Exception as e:
            logger.error(f"Failed to send email via SMTP: {e}")

    def save_report(self, report: Dict[str, Any], html_content: str) -> tuple:
        """
        Save combined report to files.

        Args:
            report: Merged report dict
            html_content: HTML report content

        Returns:
            Tuple of (json_path, html_path)
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Save JSON report
        json_path = self.output_dir / f"combined_report_{timestamp}.json"
        with open(json_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
        logger.info(f"JSON report saved to {json_path}")

        # Save HTML report
        html_path = self.output_dir / f"combined_report_{timestamp}.html"
        with open(html_path, "w") as f:
            f.write(html_content)
        logger.info(f"HTML report saved to {html_path}")

        return json_path, html_path

    async def run(self) -> Dict[str, Any]:
        """
        Execute the full combined weekly report pipeline.

        Returns:
            Combined report dict
        """
        logger.info("=" * 60)
        logger.info("RUSH Policy RAG - Combined Weekly Report")
        logger.info("=" * 60)

        # Run both components in parallel
        technical_task = self.run_technical_evaluation()
        usage_task = self.run_usage_analytics()

        technical_report, usage_report = await asyncio.gather(
            technical_task,
            usage_task,
            return_exceptions=True
        )

        # Handle exceptions from parallel execution
        if isinstance(technical_report, Exception):
            logger.error(f"Technical evaluation failed: {technical_report}")
            technical_report = None

        if isinstance(usage_report, Exception):
            logger.error(f"Usage analytics failed: {usage_report}")
            usage_report = None

        # Check if we have at least one component
        if not technical_report and not usage_report:
            logger.error("Both report components failed - cannot generate combined report")
            return {
                "error": "Both technical evaluation and usage analytics failed",
                "status": "failed"
            }

        # Merge reports
        logger.info("Merging reports...")
        merged_report = self.merge_reports(technical_report, usage_report)

        # Generate HTML
        logger.info("Generating HTML report...")
        html_content = self.generate_combined_html_report(merged_report)

        # Save reports
        json_path, html_path = self.save_report(merged_report, html_content)

        # Send email
        await self.send_email_report(merged_report, html_content)

        # Print summary
        summary = merged_report.get("executive_summary", {})
        logger.info("=" * 60)
        logger.info("Combined Weekly Report Complete")
        logger.info(f"Status: {merged_report.get('status', 'unknown').upper()}")
        logger.info(f"Total Queries: {summary.get('total_queries', 0):,}")
        logger.info(f"Success Rate: {summary.get('success_rate', 0)}%")
        logger.info(f"Eval Pass Rate: {summary.get('eval_pass_rate', 0)}%")
        logger.info(f"Reports saved to: {self.output_dir}")
        logger.info("=" * 60)

        return merged_report


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate combined technical quality + executive usage weekly report",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full weekly report (default)
  python scripts/run_combined_weekly_report.py

  # Dry run without email
  python scripts/run_combined_weekly_report.py --dry-run

  # Custom recipient
  python scripts/run_combined_weekly_report.py --email user@rush.edu

  # Use local queries for evaluation
  python scripts/run_combined_weekly_report.py --local-queries queries.json

  # 14-day lookback for usage data
  python scripts/run_combined_weekly_report.py --days 14
"""
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip email sending, save report to file only"
    )
    parser.add_argument(
        "--email",
        type=str,
        action="append",
        help="Email recipient (can be specified multiple times, default: juan_rojas@rush.edu)"
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=50,
        help="Number of queries to sample for technical evaluation (default: 50)"
    )
    parser.add_argument(
        "--local-queries",
        type=str,
        help="Path to local queries JSON file (instead of fetching from App Insights)"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="eval_reports",
        dest="output_dir",
        help="Output directory for reports (default: eval_reports/)"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Lookback days for usage analytics (default: 7)"
    )

    args = parser.parse_args()

    # Build config
    config = CombinedWeeklyReportConfig(
        email_recipients=args.email if args.email else None,
        dry_run=args.dry_run,
        output_dir=args.output_dir,
        sample_size=args.sample,
        local_queries_file=args.local_queries,
        days=args.days,
    )

    # Run combined report
    reporter = CombinedWeeklyReporter(config)
    report = await reporter.run()

    # Exit code based on status
    if report.get("status") == "failed":
        sys.exit(1)
    elif report.get("status") == "partial":
        logger.warning("Partial report generated - some components failed")
        sys.exit(0)  # Don't fail CI/CD on partial success
    else:
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
