#!/usr/bin/env python3
"""
RUSH PolicyTech RAG - CEO Quality Assurance Report Generator

Generates executive-level reports combining:
1. Performance accuracy (enhanced evaluation suite)
2. Jailbreak/adversarial accuracy (security testing)
3. RAGAS semantic metrics (optional)

Outputs Markdown and JSON formats suitable for C-suite presentation.

Usage:
    python scripts/generate_ceo_report.py
    python scripts/generate_ceo_report.py --ragas --adversarial
    python scripts/generate_ceo_report.py --output reports/ceo_report.md
    python scripts/generate_ceo_report.py --full --output reports/ceo_report.md
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
from dotenv import load_dotenv

# Add paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "backend"))
scripts_dir = Path(__file__).parent

# Load environment
load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class CEOReportGenerator:
    """Generates comprehensive CEO-level quality assurance reports."""

    def __init__(self, api_url: str = "http://localhost:8000"):
        self.api_url = api_url
        self.evaluation_results: Optional[Dict] = None
        self.adversarial_results: Optional[Dict] = None
        self.ragas_results: Optional[Dict] = None
        self.report_timestamp = datetime.now()

    async def run_performance_evaluation(self) -> Dict[str, Any]:
        """Run the enhanced evaluation suite."""
        logger.info("Running performance evaluation suite...")

        try:
            # Import and run the enhanced evaluation
            sys.path.insert(0, str(scripts_dir))
            from run_enhanced_evaluation import EnhancedRAGEvaluator

            # Load test cases
            test_file = Path(__file__).parent.parent / "apps" / "backend" / "data" / "enhanced_test_dataset.json"
            with open(test_file) as f:
                test_data = json.load(f)
            test_cases = test_data.get("test_cases", [])

            evaluator = EnhancedRAGEvaluator(backend_url=self.api_url)
            results = await evaluator.run_evaluation(test_cases)

            # Generate report structure using evaluator's method
            report = evaluator.generate_report(results)
            self.evaluation_results = {"report": report, "results": [r.to_dict() for r in results]}
            logger.info(f"Performance evaluation complete: {report.get('summary', {}).get('pass_rate', 0)}% pass rate")
            return self.evaluation_results
        except ImportError as e:
            logger.warning(f"Could not import EnhancedRAGEvaluator: {e}, running via subprocess...")
            result = subprocess.run(
                [sys.executable, str(scripts_dir / "run_enhanced_evaluation.py"), "--json"],
                capture_output=True,
                text=True,
                cwd=str(scripts_dir.parent)
            )
            if result.returncode == 0:
                # Try to parse JSON from output
                try:
                    self.evaluation_results = json.loads(result.stdout)
                    return self.evaluation_results
                except json.JSONDecodeError:
                    logger.error("Could not parse evaluation output as JSON")
            return {"error": "Evaluation failed", "stderr": result.stderr}
        except Exception as e:
            logger.error(f"Performance evaluation failed: {e}")
            return {"error": str(e)}

    async def run_adversarial_evaluation(self) -> Dict[str, Any]:
        """Run the adversarial/jailbreak test suite."""
        logger.info("Running adversarial security tests...")

        try:
            # Import and run the adversarial tests
            sys.path.insert(0, str(scripts_dir))
            from run_garak_adversarial import AdversarialTester

            tester = AdversarialTester(api_url=self.api_url)
            results = await tester.run_all_probes()
            self.adversarial_results = results
            logger.info(f"Adversarial evaluation complete: {results.get('summary', {}).get('security_score', 0)}% security score")
            return results
        except Exception as e:
            logger.error(f"Adversarial evaluation failed: {e}")
            return {"error": str(e)}

    async def run_ragas_evaluation(self, sample_size: int = 10) -> Dict[str, Any]:
        """Run RAGAS semantic evaluation on a sample of queries."""
        logger.info(f"Running RAGAS semantic evaluation (sample size: {sample_size})...")

        try:
            # Import RAGAS evaluator
            sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "backend" / "evaluation"))
            from ragas_evaluator import RAGASEvaluator

            evaluator = RAGASEvaluator()

            # Load test cases for RAGAS
            test_file = Path(__file__).parent.parent / "apps" / "backend" / "data" / "enhanced_test_dataset.json"
            with open(test_file) as f:
                test_data = json.load(f)

            # Select diverse sample for RAGAS
            test_cases = test_data.get("test_cases", [])[:sample_size]

            # Run RAGAS evaluation
            results = await evaluator.evaluate_batch(
                queries=[tc["query"] for tc in test_cases],
                api_url=self.api_url
            )

            self.ragas_results = results
            logger.info("RAGAS evaluation complete")
            return results
        except Exception as e:
            logger.error(f"RAGAS evaluation failed: {e}")
            return {"error": str(e), "message": "RAGAS evaluation requires OpenAI API key and dependencies"}

    def _calculate_overall_grade(self) -> tuple[str, str]:
        """Calculate overall system grade (A-F) and status."""
        scores = []

        # Performance score (weight: 40%)
        if self.evaluation_results and "report" in self.evaluation_results:
            perf_rate = self.evaluation_results["report"]["summary"].get("pass_rate", 0)
            # Ensure numeric - handle string percentages like "88.75%"
            if isinstance(perf_rate, str):
                perf_rate = float(perf_rate.replace("%", ""))
            scores.append(("performance", float(perf_rate), 0.4))

        # Security score (weight: 40%)
        if self.adversarial_results and "summary" in self.adversarial_results:
            sec_score = self.adversarial_results["summary"].get("security_score", 0)
            if isinstance(sec_score, str):
                sec_score = float(sec_score.replace("%", ""))
            scores.append(("security", float(sec_score), 0.4))

        # RAGAS score (weight: 20%)
        if self.ragas_results and "aggregate" in self.ragas_results:
            ragas_score = self.ragas_results["aggregate"].get("overall_score", 0) * 100
            scores.append(("semantic", ragas_score, 0.2))
        elif scores:
            # Redistribute weight if no RAGAS
            for i, (name, score, weight) in enumerate(scores):
                if name == "performance":
                    scores[i] = (name, score, 0.5)
                elif name == "security":
                    scores[i] = (name, score, 0.5)

        if not scores:
            return "N/A", "No evaluation data available"

        # Calculate weighted average
        total_weight = sum(w for _, _, w in scores)
        weighted_score = sum(s * w for _, s, w in scores) / total_weight if total_weight > 0 else 0

        # Determine grade
        if weighted_score >= 95:
            return "A+", "Excellent - Production Ready"
        elif weighted_score >= 90:
            return "A", "Very Good - Production Ready"
        elif weighted_score >= 85:
            return "B+", "Good - Minor Issues"
        elif weighted_score >= 80:
            return "B", "Acceptable - Review Recommended"
        elif weighted_score >= 70:
            return "C", "Needs Improvement"
        elif weighted_score >= 60:
            return "D", "Significant Issues"
        else:
            return "F", "Critical Issues - Not Production Ready"

    def generate_markdown_report(self) -> str:
        """Generate executive-level Markdown report."""
        grade, status = self._calculate_overall_grade()

        report = []
        report.append("# RUSH PolicyTech RAG - Quality Assurance Report")
        report.append("")
        report.append(f"**Generated:** {self.report_timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
        report.append(f"**System Version:** 4.0 (Enhanced Evaluation Framework)")
        report.append("")

        # Executive Summary
        report.append("## Executive Summary")
        report.append("")
        report.append(f"| Metric | Value |")
        report.append(f"|--------|-------|")
        report.append(f"| **Overall Grade** | **{grade}** |")
        report.append(f"| **Status** | {status} |")

        if self.evaluation_results and "report" in self.evaluation_results:
            perf = self.evaluation_results["report"]["summary"]
            report.append(f"| Performance Accuracy | {perf.get('pass_rate', 0):.1f}% |")

        if self.adversarial_results and "summary" in self.adversarial_results:
            adv = self.adversarial_results["summary"]
            report.append(f"| Security Score | {adv.get('security_score', 0):.1f}% |")
            report.append(f"| Jailbreak Resistance | {adv.get('pass_rate', 0):.1f}% |")

        if self.ragas_results and "aggregate" in self.ragas_results:
            ragas = self.ragas_results["aggregate"]
            report.append(f"| Semantic Quality (RAGAS) | {ragas.get('overall_score', 0)*100:.1f}% |")

        report.append("")

        # Performance Metrics Section
        if self.evaluation_results and "report" in self.evaluation_results:
            report.append("## Performance Metrics")
            report.append("")

            summary = self.evaluation_results["report"]["summary"]
            report.append(f"**Total Tests:** {summary.get('total', 0)}")
            report.append(f"**Passed:** {summary.get('passed', 0)}")
            report.append(f"**Failed:** {summary.get('failed', 0)}")
            report.append(f"**Pass Rate:** {summary.get('pass_rate', 0):.1f}%")
            report.append("")

            # Category breakdown
            report.append("### Category Breakdown")
            report.append("")
            report.append("| Category | Pass Rate | Tests | Status |")
            report.append("|----------|-----------|-------|--------|")

            categories = self.evaluation_results["report"].get("by_category", {})
            for cat, stats in sorted(categories.items()):
                total = stats.get("total", 0)
                passed = stats.get("passed", 0)
                rate = (passed / total * 100) if total > 0 else 0
                status_icon = "Pass" if rate >= 80 else "Review" if rate >= 60 else "Fail"
                report.append(f"| {cat.replace('_', ' ').title()} | {rate:.0f}% | {passed}/{total} | {status_icon} |")

            report.append("")

        # Adversarial/Security Section
        if self.adversarial_results and "summary" in self.adversarial_results:
            report.append("## Security & Adversarial Testing")
            report.append("")

            summary = self.adversarial_results["summary"]
            report.append(f"**Security Score:** {summary.get('security_score', 0):.1f}%")
            report.append(f"**Total Probes:** {summary.get('total_probes', 0)}")
            report.append(f"**Attacks Blocked:** {summary.get('blocked', 0)}")
            report.append(f"**Attacks Bypassed:** {summary.get('bypassed', 0)}")
            report.append("")

            verdict = self.adversarial_results.get("verdict", "UNKNOWN")
            if verdict == "PASS":
                report.append("> **PASS** - All adversarial attacks were successfully blocked.")
            else:
                report.append("> **FAIL** - Some adversarial attacks bypassed defenses. Immediate review required.")
            report.append("")

            # Attack category breakdown
            report.append("### Attack Category Results")
            report.append("")
            report.append("| Attack Type | Blocked | Total | Rate |")
            report.append("|-------------|---------|-------|------|")

            categories = self.adversarial_results.get("category_breakdown", {})
            for cat, stats in sorted(categories.items()):
                total = stats.get("total", 0)
                blocked = stats.get("blocked", 0)
                rate = (blocked / total * 100) if total > 0 else 0
                report.append(f"| {cat.replace('_', ' ').title()} | {blocked} | {total} | {rate:.0f}% |")

            report.append("")

            # Critical failures
            critical_failures = self.adversarial_results.get("critical_failures", [])
            if critical_failures:
                report.append("### Critical Security Failures")
                report.append("")
                report.append("> **ATTENTION:** The following critical attacks bypassed defenses:")
                report.append("")
                for failure in critical_failures:
                    report.append(f"- **{failure.get('name', 'Unknown')}** ({failure.get('probe_id', '')})")
                    report.append(f"  - Analysis: {failure.get('analysis', 'N/A')[:100]}")
                report.append("")

        # RAGAS Section
        if self.ragas_results and "aggregate" in self.ragas_results:
            report.append("## Semantic Quality Metrics (RAGAS)")
            report.append("")

            agg = self.ragas_results["aggregate"]
            report.append("| Metric | Score | Threshold | Status |")
            report.append("|--------|-------|-----------|--------|")

            metrics = [
                ("Faithfulness", agg.get("faithfulness", 0), 0.80),
                ("Answer Relevancy", agg.get("answer_relevancy", 0), 0.70),
                ("Context Precision", agg.get("context_precision", 0), 0.70),
                ("Context Recall", agg.get("context_recall", 0), 0.70),
            ]

            for name, score, threshold in metrics:
                status = "Pass" if score >= threshold else "Fail"
                report.append(f"| {name} | {score:.2f} | {threshold:.2f} | {status} |")

            report.append("")

        # Recommendations
        report.append("## Recommendations")
        report.append("")

        recommendations = self._generate_recommendations()
        if recommendations:
            for rec in recommendations:
                report.append(f"- {rec}")
        else:
            report.append("- No critical issues identified. Continue monitoring.")
        report.append("")

        # Footer
        report.append("---")
        report.append("")
        report.append("*This report was automatically generated by the RUSH PolicyTech RAG Quality Assurance System.*")
        report.append("")
        report.append(f"*Report ID: CEO-{self.report_timestamp.strftime('%Y%m%d-%H%M%S')}*")

        return "\n".join(report)

    def _generate_recommendations(self) -> List[str]:
        """Generate actionable recommendations based on results."""
        recommendations = []

        # Performance recommendations
        if self.evaluation_results and "report" in self.evaluation_results:
            summary = self.evaluation_results["report"]["summary"]
            if summary.get("pass_rate", 0) < 90:
                recommendations.append("Performance accuracy below 90% - review failing test categories")

            categories = self.evaluation_results["report"].get("by_category", {})
            for cat, stats in categories.items():
                total = stats.get("total", 0)
                passed = stats.get("passed", 0)
                rate = (passed / total * 100) if total > 0 else 100
                if rate < 70:
                    recommendations.append(f"Category '{cat}' has low pass rate ({rate:.0f}%) - investigate root cause")

        # Security recommendations
        if self.adversarial_results:
            if self.adversarial_results.get("verdict") == "FAIL":
                recommendations.append("CRITICAL: Security vulnerabilities detected - immediate remediation required")

            critical_failures = self.adversarial_results.get("critical_failures", [])
            if critical_failures:
                recommendations.append(f"Address {len(critical_failures)} critical security failures before production deployment")

            partial = self.adversarial_results.get("needs_review", [])
            if partial:
                recommendations.append(f"Review {len(partial)} partial block results for potential vulnerabilities")

        # RAGAS recommendations
        if self.ragas_results and "aggregate" in self.ragas_results:
            agg = self.ragas_results["aggregate"]
            if agg.get("faithfulness", 0) < 0.80:
                recommendations.append("Faithfulness score below threshold - review response grounding")
            if agg.get("context_precision", 0) < 0.70:
                recommendations.append("Context precision low - improve retrieval relevance ranking")

        return recommendations

    def generate_json_report(self) -> Dict[str, Any]:
        """Generate comprehensive JSON report."""
        grade, status = self._calculate_overall_grade()

        return {
            "report_metadata": {
                "type": "ceo_quality_assurance_report",
                "timestamp": self.report_timestamp.isoformat(),
                "version": "4.0"
            },
            "executive_summary": {
                "overall_grade": grade,
                "status": status,
                "recommendations": self._generate_recommendations()
            },
            "performance_evaluation": self.evaluation_results,
            "adversarial_evaluation": self.adversarial_results,
            "semantic_evaluation": self.ragas_results
        }


async def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Generate CEO Quality Assurance Report")
    parser.add_argument("--api-url", default="http://localhost:8000", help="Backend API URL")
    parser.add_argument("--output", help="Output file path (default: stdout)")
    parser.add_argument("--format", choices=["markdown", "json", "both"], default="markdown", help="Output format")
    parser.add_argument("--performance", action="store_true", help="Run performance evaluation")
    parser.add_argument("--adversarial", action="store_true", help="Run adversarial evaluation")
    parser.add_argument("--ragas", action="store_true", help="Run RAGAS semantic evaluation")
    parser.add_argument("--full", action="store_true", help="Run all evaluations")
    parser.add_argument("--ragas-sample", type=int, default=10, help="RAGAS sample size")
    args = parser.parse_args()

    # Default to full if no specific evaluation selected
    if not (args.performance or args.adversarial or args.ragas or args.full):
        args.performance = True
        args.adversarial = True

    if args.full:
        args.performance = True
        args.adversarial = True
        args.ragas = True

    generator = CEOReportGenerator(api_url=args.api_url)

    # Run selected evaluations
    if args.performance:
        await generator.run_performance_evaluation()

    if args.adversarial:
        await generator.run_adversarial_evaluation()

    if args.ragas:
        await generator.run_ragas_evaluation(sample_size=args.ragas_sample)

    # Generate reports
    if args.format in ["markdown", "both"]:
        md_report = generator.generate_markdown_report()
        if args.output:
            output_path = Path(args.output)
            if args.format == "both":
                md_path = output_path.with_suffix(".md")
            else:
                md_path = output_path
            md_path.parent.mkdir(parents=True, exist_ok=True)
            with open(md_path, "w") as f:
                f.write(md_report)
            print(f"Markdown report saved to: {md_path}")
        else:
            print(md_report)

    if args.format in ["json", "both"]:
        json_report = generator.generate_json_report()
        if args.output:
            output_path = Path(args.output)
            if args.format == "both":
                json_path = output_path.with_suffix(".json")
            else:
                json_path = output_path
            json_path.parent.mkdir(parents=True, exist_ok=True)
            with open(json_path, "w") as f:
                json.dump(json_report, f, indent=2, default=str)
            print(f"JSON report saved to: {json_path}")
        elif args.format == "json":
            print(json.dumps(json_report, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
