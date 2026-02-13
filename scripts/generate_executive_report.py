#!/usr/bin/env python3
"""
Executive Weekly Usage Report Generator.

Generates executive-friendly reports on RAG system usage by analyzing
production audit logs and using AI to classify question types.

Features:
- Question type classification (AI-powered)
- Time of engagement analysis (hourly/daily patterns)
- Usage metrics (total queries, success rate, response time)
- Department/role inference from questions
- Trend analysis vs previous weeks

Output:
- HTML report for executives
- JSON data for dashboards
- Email distribution

Usage:
    # Full weekly report
    python scripts/generate_executive_report.py

    # Specific date range
    python scripts/generate_executive_report.py --start-date 2026-01-20 --end-date 2026-01-27

    # Dry run without email
    python scripts/generate_executive_report.py --dry-run

Environment Variables:
    STORAGE_CONNECTION_STRING: Azure Storage connection string
    AOAI_ENDPOINT: Azure OpenAI endpoint
    AOAI_API_KEY: Azure OpenAI API key
    AZURE_COMM_CONNECTION_STRING: Azure Communication Services (for email)
"""

import asyncio
import json
import os
import sys
import argparse
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field, asdict

# Add backend to path
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

# Azure Communication Services (optional)
try:
    from azure.communication.email import EmailClient
    AZURE_COMM_AVAILABLE = True
except ImportError:
    AZURE_COMM_AVAILABLE = False

# Azure OpenAI for classification
try:
    from openai import AzureOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


@dataclass
class QuestionClassification:
    """Classification result for a question."""
    category: str  # e.g., "clinical_procedure", "policy_lookup", "compliance"
    subcategory: str  # e.g., "medication_admin", "infection_control"
    inferred_role: str  # e.g., "nurse", "physician", "staff"
    urgency: str  # e.g., "routine", "urgent", "critical"
    confidence: float


@dataclass
class EngagementMetrics:
    """Engagement metrics for the report period."""
    total_queries: int = 0
    unique_sessions: int = 0  # Estimated from query patterns
    queries_found: int = 0
    queries_not_found: int = 0
    avg_latency_ms: float = 0
    p95_latency_ms: float = 0
    queries_needing_review: int = 0

    # Time patterns
    queries_by_hour: Dict[int, int] = field(default_factory=dict)
    queries_by_day: Dict[str, int] = field(default_factory=dict)

    # Question types
    questions_by_category: Dict[str, int] = field(default_factory=dict)
    questions_by_subcategory: Dict[str, int] = field(default_factory=dict)
    questions_by_role: Dict[str, int] = field(default_factory=dict)

    # Confidence breakdown
    confidence_breakdown: Dict[str, int] = field(default_factory=dict)

    # Top queries
    top_questions: List[Dict[str, Any]] = field(default_factory=list)

    # Trends
    week_over_week_change: float = 0


@dataclass
class ExecutiveReportConfig:
    """Configuration for executive report generation."""
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    email_recipients: List[str] = None
    dry_run: bool = False
    output_dir: str = "eval_reports"
    include_sample_questions: bool = True
    sample_question_count: int = 10

    def __post_init__(self):
        if self.email_recipients is None:
            default = os.getenv("WEEKLY_REPORT_RECIPIENTS", "juan_rojas@rush.edu")
            self.email_recipients = [r.strip() for r in default.split(",")]

        # Default to last 7 days
        if self.end_date is None:
            self.end_date = datetime.now().strftime("%Y-%m-%d")
        if self.start_date is None:
            end = datetime.strptime(self.end_date, "%Y-%m-%d")
            self.start_date = (end - timedelta(days=7)).strftime("%Y-%m-%d")


class QuestionClassifier:
    """AI-powered question classifier using Azure OpenAI."""

    # Question categories for healthcare RAG
    CATEGORIES = {
        "clinical_procedure": "Clinical procedures, treatments, patient care protocols",
        "medication": "Medication administration, dosing, drug policies",
        "infection_control": "Infection prevention, PPE, isolation protocols",
        "patient_safety": "Fall prevention, restraints, safety measures",
        "compliance": "HIPAA, regulatory, documentation requirements",
        "emergency": "Emergency codes, rapid response, crisis protocols",
        "equipment": "Medical devices, equipment operation, maintenance",
        "staffing": "Scheduling, roles, credentialing, competencies",
        "administrative": "HR policies, general administration, benefits",
        "other": "Questions that don't fit other categories"
    }

    def __init__(self):
        self._client = None
        self._deployment = os.getenv("AOAI_CHAT_DEPLOYMENT", "gpt-4.1")

    def _get_client(self):
        """Lazy-load Azure OpenAI client."""
        if self._client is None and OPENAI_AVAILABLE:
            self._client = AzureOpenAI(
                azure_endpoint=os.getenv("AOAI_ENDPOINT"),
                api_key=os.getenv("AOAI_API_KEY"),
                api_version="2024-02-15-preview"
            )
        return self._client

    def classify_questions_batch(
        self,
        questions: List[str],
        batch_size: int = 20
    ) -> List[QuestionClassification]:
        """
        Classify a batch of questions using AI.

        Batches requests to minimize API calls.
        """
        client = self._get_client()
        if not client:
            # Fallback to simple keyword-based classification
            return [self._keyword_classify(q) for q in questions]

        results = []
        for i in range(0, len(questions), batch_size):
            batch = questions[i:i + batch_size]
            batch_results = self._classify_batch(client, batch)
            results.extend(batch_results)

            # Rate limiting
            if i + batch_size < len(questions):
                import time
                time.sleep(1)

        return results

    def _classify_batch(
        self,
        client: "AzureOpenAI",
        questions: List[str]
    ) -> List[QuestionClassification]:
        """Classify a single batch of questions."""
        try:
            # Build prompt
            categories_desc = "\n".join(
                f"- {cat}: {desc}" for cat, desc in self.CATEGORIES.items()
            )

            questions_list = "\n".join(
                f"{i+1}. {q[:200]}" for i, q in enumerate(questions)
            )

            system_prompt = f"""You are a healthcare question classifier for Rush University System for Health.
Classify each question into ONE category and infer the likely role asking it.

CATEGORIES:
{categories_desc}

ROLES:
- nurse: RN, LPN, nursing staff
- physician: MD, DO, resident, attending
- allied_health: PT, OT, respiratory, pharmacy
- support_staff: admin, housekeeping, nutrition
- management: supervisors, directors, executives
- unknown: cannot determine

URGENCY:
- routine: Standard policy lookup
- urgent: Time-sensitive but not emergency
- critical: Emergency or patient safety related

For each question, respond with ONLY a JSON array with objects containing:
- question_num (1-indexed)
- category
- subcategory (more specific)
- role
- urgency
- confidence (0.0-1.0)"""

            response = client.chat.completions.create(
                model=self._deployment,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Classify these questions:\n\n{questions_list}"}
                ],
                temperature=0.1,
                max_tokens=2000,
                response_format={"type": "json_object"}
            )

            # Parse response
            content = response.choices[0].message.content
            data = json.loads(content)

            # Handle different response formats
            classifications = data if isinstance(data, list) else data.get("classifications", [])

            results = []
            for i, q in enumerate(questions):
                # Find classification for this question
                cls = next(
                    (c for c in classifications if c.get("question_num") == i + 1),
                    None
                )

                if cls:
                    results.append(QuestionClassification(
                        category=cls.get("category", "other"),
                        subcategory=cls.get("subcategory", "general"),
                        inferred_role=cls.get("role", "unknown"),
                        urgency=cls.get("urgency", "routine"),
                        confidence=cls.get("confidence", 0.5)
                    ))
                else:
                    results.append(self._keyword_classify(q))

            return results

        except Exception as e:
            logger.error(f"AI classification failed: {e}")
            return [self._keyword_classify(q) for q in questions]

    def _keyword_classify(self, question: str) -> QuestionClassification:
        """Fallback keyword-based classification."""
        q_lower = question.lower()

        # Category detection
        if any(w in q_lower for w in ["medication", "drug", "dose", "pharmacy", "insulin"]):
            category, subcat = "medication", "medication_admin"
        elif any(w in q_lower for w in ["isolation", "ppe", "infection", "covid", "mrsa"]):
            category, subcat = "infection_control", "isolation"
        elif any(w in q_lower for w in ["fall", "restraint", "safety", "pressure ulcer"]):
            category, subcat = "patient_safety", "fall_prevention"
        elif any(w in q_lower for w in ["code", "rapid response", "emergency", "cardiac"]):
            category, subcat = "emergency", "emergency_response"
        elif any(w in q_lower for w in ["hipaa", "privacy", "compliance", "documentation"]):
            category, subcat = "compliance", "documentation"
        elif any(w in q_lower for w in ["procedure", "iv", "catheter", "wound"]):
            category, subcat = "clinical_procedure", "treatment"
        else:
            category, subcat = "other", "general"

        # Role detection
        if any(w in q_lower for w in ["nurse", "rn", "nursing"]):
            role = "nurse"
        elif any(w in q_lower for w in ["doctor", "physician", "resident"]):
            role = "physician"
        else:
            role = "unknown"

        return QuestionClassification(
            category=category,
            subcategory=subcat,
            inferred_role=role,
            urgency="routine",
            confidence=0.6
        )


class ExecutiveReportGenerator:
    """Generate executive-friendly usage reports from audit logs."""

    def __init__(self, config: ExecutiveReportConfig):
        self.config = config
        self.output_dir = Path(config.output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.classifier = QuestionClassifier()

        # Lazy-load audit service
        self._audit_service = None

    async def _get_audit_service(self):
        """Get ChatAuditService instance."""
        if self._audit_service is None:
            from app.services.chat_audit_service import (
                ChatAuditService,
                get_chat_audit_service
            )
            self._audit_service = get_chat_audit_service()
            await self._audit_service._ensure_container()
        return self._audit_service

    async def load_audit_records(self) -> List[Dict[str, Any]]:
        """Load audit records for the report period."""
        audit_service = await self._get_audit_service()

        # Generate date range
        start = datetime.strptime(self.config.start_date, "%Y-%m-%d")
        end = datetime.strptime(self.config.end_date, "%Y-%m-%d")

        all_records = []
        current = start

        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            try:
                records, total = await audit_service.get_records_for_date(
                    date_str, limit=10000
                )
                for r in records:
                    all_records.append({
                        "timestamp": r.timestamp.isoformat() if hasattr(r.timestamp, 'isoformat') else str(r.timestamp),
                        "question": r.question,
                        "response": r.response[:500] if r.response else "",
                        "found": r.found,
                        "confidence": r.confidence,
                        "confidence_score": r.confidence_score,
                        "latency_ms": r.latency_ms,
                        "needs_human_review": r.needs_human_review,
                        "citations_count": len(r.citations) if r.citations else 0,
                    })
                logger.info(f"Loaded {len(records)} records for {date_str}")
            except Exception as e:
                logger.warning(f"Failed to load records for {date_str}: {e}")

            current += timedelta(days=1)

        logger.info(f"Total records loaded: {len(all_records)}")
        return all_records

    async def generate_report(self) -> Dict[str, Any]:
        """Generate the executive report."""
        logger.info("=" * 60)
        logger.info("Executive Usage Report Generator")
        logger.info(f"Period: {self.config.start_date} to {self.config.end_date}")
        logger.info("=" * 60)

        # Load audit records
        records = await self.load_audit_records()

        if not records:
            return {
                "error": "No audit records found for the specified period",
                "period": {
                    "start": self.config.start_date,
                    "end": self.config.end_date
                }
            }

        # Calculate engagement metrics
        metrics = self._calculate_metrics(records)

        # Classify questions
        questions = [r["question"] for r in records]
        logger.info(f"Classifying {len(questions)} questions...")
        classifications = self.classifier.classify_questions_batch(questions)

        # Update metrics with classifications
        self._add_classification_metrics(metrics, classifications)

        # Get sample questions
        if self.config.include_sample_questions:
            metrics.top_questions = self._get_sample_questions(records, classifications)

        # Build report
        report = {
            "metadata": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "period_start": self.config.start_date,
                "period_end": self.config.end_date,
                "total_records_analyzed": len(records),
            },
            "summary": {
                "headline": self._generate_headline(metrics),
                "total_queries": metrics.total_queries,
                "success_rate": round(metrics.queries_found / metrics.total_queries * 100, 1) if metrics.total_queries else 0,
                "avg_response_time_ms": round(metrics.avg_latency_ms),
                "queries_needing_attention": metrics.queries_needing_review,
            },
            "engagement": asdict(metrics),
            "insights": self._generate_insights(metrics),
            "recommendations": self._generate_recommendations(metrics),
        }

        return report

    def _calculate_metrics(self, records: List[Dict[str, Any]]) -> EngagementMetrics:
        """Calculate engagement metrics from records."""
        metrics = EngagementMetrics()
        metrics.total_queries = len(records)

        latencies = []

        for r in records:
            # Count found/not found
            if r.get("found", False):
                metrics.queries_found += 1
            else:
                metrics.queries_not_found += 1

            # Count needs review
            if r.get("needs_human_review", False):
                metrics.queries_needing_review += 1

            # Latency
            if r.get("latency_ms"):
                latencies.append(r["latency_ms"])

            # Confidence breakdown
            conf = r.get("confidence", "medium")
            metrics.confidence_breakdown[conf] = metrics.confidence_breakdown.get(conf, 0) + 1

            # Time analysis
            try:
                ts = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
                hour = ts.hour
                day = ts.strftime("%A")

                metrics.queries_by_hour[hour] = metrics.queries_by_hour.get(hour, 0) + 1
                metrics.queries_by_day[day] = metrics.queries_by_day.get(day, 0) + 1
            except Exception:
                pass

        # Calculate latency stats
        if latencies:
            latencies.sort()
            metrics.avg_latency_ms = sum(latencies) / len(latencies)
            metrics.p95_latency_ms = latencies[int(len(latencies) * 0.95)] if len(latencies) >= 20 else max(latencies)

        return metrics

    def _add_classification_metrics(
        self,
        metrics: EngagementMetrics,
        classifications: List[QuestionClassification]
    ):
        """Add classification-based metrics."""
        for cls in classifications:
            # Category breakdown
            metrics.questions_by_category[cls.category] = \
                metrics.questions_by_category.get(cls.category, 0) + 1

            # Subcategory breakdown
            key = f"{cls.category}:{cls.subcategory}"
            metrics.questions_by_subcategory[key] = \
                metrics.questions_by_subcategory.get(key, 0) + 1

            # Role breakdown
            metrics.questions_by_role[cls.inferred_role] = \
                metrics.questions_by_role.get(cls.inferred_role, 0) + 1

    def _get_sample_questions(
        self,
        records: List[Dict[str, Any]],
        classifications: List[QuestionClassification]
    ) -> List[Dict[str, Any]]:
        """Get sample questions for the report."""
        samples = []

        # Get diverse samples from each category
        by_category = defaultdict(list)
        for i, (r, cls) in enumerate(zip(records, classifications)):
            by_category[cls.category].append({
                "question": r["question"][:150] + "..." if len(r["question"]) > 150 else r["question"],
                "category": cls.category,
                "subcategory": cls.subcategory,
                "inferred_role": cls.inferred_role,
                "found": r.get("found", False),
            })

        # Take 2 from each category
        for category, questions in sorted(by_category.items()):
            samples.extend(questions[:2])
            if len(samples) >= self.config.sample_question_count:
                break

        return samples[:self.config.sample_question_count]

    def _generate_headline(self, metrics: EngagementMetrics) -> str:
        """Generate a headline for executives."""
        success_rate = round(metrics.queries_found / metrics.total_queries * 100, 1) if metrics.total_queries else 0

        if success_rate >= 90:
            status = "Excellent"
        elif success_rate >= 80:
            status = "Good"
        elif success_rate >= 70:
            status = "Satisfactory"
        else:
            status = "Needs Attention"

        return f"{status} Performance: {metrics.total_queries} queries with {success_rate}% success rate"

    def _generate_insights(self, metrics: EngagementMetrics) -> List[str]:
        """Generate key insights for executives."""
        insights = []

        # Peak usage insight
        if metrics.queries_by_hour:
            peak_hour = max(metrics.queries_by_hour.items(), key=lambda x: x[1])
            insights.append(
                f"Peak usage at {peak_hour[0]}:00 with {peak_hour[1]} queries"
            )

        # Top category insight
        if metrics.questions_by_category:
            top_cat = max(metrics.questions_by_category.items(), key=lambda x: x[1])
            pct = round(top_cat[1] / metrics.total_queries * 100, 1)
            insights.append(
                f"Most common topic: {top_cat[0].replace('_', ' ').title()} ({pct}% of queries)"
            )

        # Role insight
        if metrics.questions_by_role:
            top_role = max(metrics.questions_by_role.items(), key=lambda x: x[1])
            if top_role[0] != "unknown":
                pct = round(top_role[1] / metrics.total_queries * 100, 1)
                insights.append(
                    f"Primary users: {top_role[0].title()}s ({pct}% of queries)"
                )

        # Busiest day
        if metrics.queries_by_day:
            busy_day = max(metrics.queries_by_day.items(), key=lambda x: x[1])
            insights.append(
                f"Busiest day: {busy_day[0]} with {busy_day[1]} queries"
            )

        # Response time insight
        if metrics.avg_latency_ms:
            if metrics.avg_latency_ms < 2000:
                insights.append(
                    f"Fast response times: {round(metrics.avg_latency_ms)}ms average"
                )
            elif metrics.avg_latency_ms > 5000:
                insights.append(
                    f"Response times need improvement: {round(metrics.avg_latency_ms)}ms average"
                )

        return insights

    def _generate_recommendations(self, metrics: EngagementMetrics) -> List[str]:
        """Generate recommendations based on metrics."""
        recommendations = []

        # Success rate recommendations
        success_rate = metrics.queries_found / metrics.total_queries * 100 if metrics.total_queries else 0
        if success_rate < 85:
            recommendations.append(
                "Consider expanding the policy knowledge base - "
                f"{metrics.queries_not_found} queries could not find matching policies"
            )

        # Review queue
        if metrics.queries_needing_review > 10:
            recommendations.append(
                f"{metrics.queries_needing_review} queries flagged for human review - "
                "recommend weekly audit of these responses"
            )

        # Category-based recommendations
        if metrics.questions_by_category.get("emergency", 0) > metrics.total_queries * 0.1:
            recommendations.append(
                "High volume of emergency-related queries - "
                "ensure emergency protocols are prominently featured"
            )

        # Low confidence recommendations
        low_conf = metrics.confidence_breakdown.get("low", 0)
        if low_conf > metrics.total_queries * 0.15:
            recommendations.append(
                f"{low_conf} queries returned low-confidence answers - "
                "review retrieval configuration and policy coverage"
            )

        if not recommendations:
            recommendations.append(
                "System performing well - continue monitoring weekly"
            )

        return recommendations

    def generate_html_report(self, report: Dict[str, Any]) -> str:
        """Generate HTML report for executives."""
        summary = report.get("summary", {})
        engagement = report.get("engagement", {})
        insights = report.get("insights", [])
        recommendations = report.get("recommendations", [])
        sample_questions = engagement.get("top_questions", [])

        # Format category breakdown for chart
        categories = engagement.get("questions_by_category", {})
        category_rows = ""
        for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
            pct = round(count / summary.get("total_queries", 1) * 100, 1)
            category_rows += f"""
            <tr>
                <td>{cat.replace('_', ' ').title()}</td>
                <td>{count}</td>
                <td>{pct}%</td>
            </tr>"""

        # Format time breakdown
        hour_data = engagement.get("queries_by_hour", {})
        day_data = engagement.get("queries_by_day", {})

        hour_rows = ""
        for hour in range(24):
            count = hour_data.get(hour, 0)
            bar_width = min(count / max(hour_data.values()) * 100, 100) if hour_data else 0
            hour_rows += f"""
            <tr>
                <td>{hour:02d}:00</td>
                <td>{count}</td>
                <td><div style="background:#30AE6E;width:{bar_width}%;height:15px;"></div></td>
            </tr>"""

        # Format sample questions
        sample_rows = ""
        for q in sample_questions[:5]:
            status = "found" if q.get("found") else "not_found"
            sample_rows += f"""
            <tr>
                <td>{q.get('question', 'N/A')[:80]}...</td>
                <td>{q.get('category', 'N/A').replace('_', ' ').title()}</td>
                <td>{q.get('inferred_role', 'N/A').title()}</td>
                <td class="{status}">{'Found' if q.get('found') else 'Not Found'}</td>
            </tr>"""

        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>RUSH Policy RAG - Executive Usage Report</title>
    <style>
        body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 20px; color: #333; max-width: 1200px; margin: 0 auto; padding: 20px; }}
        h1 {{ color: #006332; font-size: 28px; border-bottom: 3px solid #006332; padding-bottom: 10px; }}
        h2 {{ color: #30AE6E; font-size: 20px; margin-top: 30px; }}
        .header {{ background: linear-gradient(135deg, #006332, #30AE6E); color: white; padding: 30px; border-radius: 10px; margin-bottom: 30px; }}
        .header h1 {{ color: white; border: none; margin: 0; }}
        .header p {{ margin: 10px 0 0 0; opacity: 0.9; }}
        .headline {{ font-size: 22px; background: #DFF9EB; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 5px solid #30AE6E; }}
        .metrics-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin: 20px 0; }}
        .metric-card {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); text-align: center; }}
        .metric-value {{ font-size: 36px; font-weight: bold; color: #006332; }}
        .metric-label {{ font-size: 14px; color: #666; margin-top: 5px; }}
        .success {{ color: #30AE6E; }}
        .warning {{ color: #F39C12; }}
        .danger {{ color: #E74C3C; }}
        table {{ border-collapse: collapse; width: 100%; margin: 15px 0; background: white; }}
        th, td {{ border: 1px solid #ddd; padding: 12px; text-align: left; }}
        th {{ background: #006332; color: white; font-weight: 500; }}
        tr:nth-child(even) {{ background: #f9f9f9; }}
        tr:hover {{ background: #DFF9EB; }}
        .insight {{ background: #E8F6FF; padding: 15px; margin: 10px 0; border-left: 4px solid #3498DB; border-radius: 0 8px 8px 0; }}
        .recommendation {{ background: #FFF3CD; padding: 15px; margin: 10px 0; border-left: 4px solid #F39C12; border-radius: 0 8px 8px 0; }}
        .found {{ color: #30AE6E; font-weight: bold; }}
        .not_found {{ color: #E74C3C; }}
        .footer {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid #ddd; color: #666; font-size: 12px; text-align: center; }}
        @media (max-width: 768px) {{
            .metrics-grid {{ grid-template-columns: repeat(2, 1fr); }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>RUSH Policy RAG - Weekly Usage Report</h1>
        <p>Period: {report['metadata']['period_start']} to {report['metadata']['period_end']}</p>
        <p>Generated: {report['metadata']['generated_at'][:10]}</p>
    </div>

    <div class="headline">
        {summary.get('headline', 'Usage Report')}
    </div>

    <h2>Key Metrics</h2>
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
            <div class="metric-label">Needs Attention</div>
        </div>
    </div>

    <h2>Key Insights</h2>
    {"".join(f'<div class="insight">{insight}</div>' for insight in insights)}

    <h2>Question Categories</h2>
    <table>
        <tr>
            <th>Category</th>
            <th>Count</th>
            <th>Percentage</th>
        </tr>
        {category_rows}
    </table>

    <h2>Usage by Hour</h2>
    <table>
        <tr>
            <th>Hour</th>
            <th>Queries</th>
            <th>Distribution</th>
        </tr>
        {hour_rows}
    </table>

    <h2>Sample Questions</h2>
    <table>
        <tr>
            <th>Question</th>
            <th>Category</th>
            <th>Likely Role</th>
            <th>Status</th>
        </tr>
        {sample_rows}
    </table>

    <h2>Recommendations</h2>
    {"".join(f'<div class="recommendation">{rec}</div>' for rec in recommendations)}

    <div class="footer">
        <p>This report was automatically generated by the RUSH Policy RAG System.</p>
        <p>For questions or feedback, contact the AI Innovation Team.</p>
    </div>
</body>
</html>
"""
        return html

    async def send_email_report(self, report: Dict[str, Any], html_content: str):
        """Send executive report via email."""
        if self.config.dry_run:
            logger.info("Dry run - skipping email send")
            return

        conn_str = os.getenv("AZURE_COMM_CONNECTION_STRING")
        sender = os.getenv("AZURE_COMM_SENDER_ADDRESS")

        if not conn_str or not sender or not AZURE_COMM_AVAILABLE:
            logger.warning("Azure Communication Services not configured, skipping email")
            return

        try:
            email_client = EmailClient.from_connection_string(conn_str)

            subject = f"RUSH Policy RAG - Executive Usage Report ({self.config.end_date})"

            # Plain text version
            summary = report.get("summary", {})
            text_content = f"""
RUSH Policy RAG - Executive Usage Report

Period: {self.config.start_date} to {self.config.end_date}

Summary:
- Total Queries: {summary.get('total_queries', 0)}
- Success Rate: {summary.get('success_rate', 0)}%
- Avg Response Time: {summary.get('avg_response_time_ms', 0)}ms
- Queries Needing Attention: {summary.get('queries_needing_attention', 0)}

See the HTML version for full details and insights.
"""

            message = {
                "senderAddress": sender,
                "recipients": {
                    "to": [{"address": email} for email in self.config.email_recipients]
                },
                "content": {
                    "subject": subject,
                    "plainText": text_content,
                    "html": html_content,
                },
            }

            poller = email_client.begin_send(message)
            result = poller.result()

            logger.info(f"Executive report sent to {self.config.email_recipients}")
            logger.info(f"Message ID: {result.get('id', 'N/A')}")

        except Exception as e:
            logger.error(f"Failed to send executive report email: {e}")

    def save_report(self, report: Dict[str, Any]) -> tuple:
        """Save report to files."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Save JSON
        json_path = self.output_dir / f"executive_report_{timestamp}.json"
        with open(json_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
        logger.info(f"JSON report saved to {json_path}")

        # Save HTML
        html_content = self.generate_html_report(report)
        html_path = self.output_dir / f"executive_report_{timestamp}.html"
        with open(html_path, "w") as f:
            f.write(html_content)
        logger.info(f"HTML report saved to {html_path}")

        return json_path, html_path, html_content

    async def run(self) -> Dict[str, Any]:
        """Execute the full executive report generation."""
        # Generate report
        report = await self.generate_report()

        if "error" in report:
            logger.error(f"Report generation failed: {report['error']}")
            return report

        # Save reports
        json_path, html_path, html_content = self.save_report(report)

        # Send email
        await self.send_email_report(report, html_content)

        # Print summary
        summary = report.get("summary", {})
        logger.info("=" * 60)
        logger.info("Executive Report Complete")
        logger.info(f"Total Queries: {summary.get('total_queries', 0)}")
        logger.info(f"Success Rate: {summary.get('success_rate', 0)}%")
        logger.info(f"Reports saved to: {self.output_dir}")
        logger.info("=" * 60)

        return report


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Generate executive usage report")
    parser.add_argument("--start-date", type=str, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, help="End date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Skip email sending")
    parser.add_argument("--output-dir", type=str, default="eval_reports", help="Output directory")
    parser.add_argument("--email", type=str, action="append", help="Email recipient")

    args = parser.parse_args()

    config = ExecutiveReportConfig(
        start_date=args.start_date,
        end_date=args.end_date,
        dry_run=args.dry_run,
        output_dir=args.output_dir,
        email_recipients=args.email if args.email else None,
    )

    generator = ExecutiveReportGenerator(config)
    report = await generator.run()

    # Exit code based on success
    if "error" in report:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
