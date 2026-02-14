#!/usr/bin/env python3
"""
Audit Drift Report — Detect operational drift between monthly audit periods.

Compares current month's chat-audit statistics against a baseline month to
flag regressions in found rate, latency, safety flags, confidence, and more.

Usage:
    # Current vs previous month (auto-detected)
    python scripts/audit_drift_report.py

    # Explicit months
    python scripts/audit_drift_report.py --current 2026-02 --baseline 2026-01

    # Dry run — show stats without comparison
    python scripts/audit_drift_report.py --dry-run

    # Custom output path
    python scripts/audit_drift_report.py --output reports/drift/feb.json

    # Custom thresholds
    python scripts/audit_drift_report.py --found-rate-threshold 0.03 --latency-threshold 0.15

Environment:
    STORAGE_CONNECTION_STRING — Azure Blob connection string (from .env)
"""

import argparse
import json
import logging
import sys
from calendar import monthrange
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv

# Add backend to path for model imports
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "apps" / "backend"))

load_dotenv(REPO_ROOT / ".env")

from app.core.config import settings
from app.models.audit_schemas import ChatAuditRecord

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

CONTAINER_NAME = settings.CHAT_AUDIT_CONTAINER  # "chat-audit"

# Default drift thresholds
DEFAULT_FOUND_RATE_THRESHOLD = 0.05      # > 5pp drop = regression
DEFAULT_LATENCY_THRESHOLD = 0.20         # > 20% increase = regression
DEFAULT_SAFETY_FLAG_THRESHOLD = 0.02     # > 2pp increase = regression
DEFAULT_NEEDS_REVIEW_THRESHOLD = 0.05    # > 5pp increase = regression
DEFAULT_HIGH_CONFIDENCE_THRESHOLD = 0.10  # > 10pp drop = regression


def load_month_records(
    container_client,
    year: int,
    month: int,
) -> list[ChatAuditRecord]:
    """Download and parse all JSONL records for a given month."""
    records: list[ChatAuditRecord] = []
    _, days_in_month = monthrange(year, month)

    for day in range(1, days_in_month + 1):
        blob_name = f"{year:04d}/{month:02d}/{day:02d}.jsonl"
        blob_client = container_client.get_blob_client(blob_name)

        try:
            download = blob_client.download_blob()
            content = download.readall().decode("utf-8")
        except ResourceNotFoundError:
            continue

        for line in content.strip().split("\n"):
            if not line.strip():
                continue
            try:
                record = ChatAuditRecord.model_validate_json(line)
                records.append(record)
            except Exception as e:
                logger.warning(f"Failed to parse record in {blob_name}: {e}")

    return records


def compute_stats(records: list[ChatAuditRecord]) -> dict[str, Any]:
    """Compute aggregate statistics from a list of audit records."""
    total = len(records)
    if total == 0:
        return {
            "total_queries": 0,
            "found_rate": 0.0,
            "not_found_rate": 0.0,
            "confidence_distribution": {"high": 0.0, "medium": 0.0, "low": 0.0},
            "needs_review_rate": 0.0,
            "safety_flag_rate": 0.0,
            "unique_safety_flags": [],
            "mean_latency_ms": 0.0,
            "p50_latency_ms": 0.0,
            "p95_latency_ms": 0.0,
            "pipeline_distribution": {},
        }

    found_count = sum(1 for r in records if r.found)
    confidence_counts = {"high": 0, "medium": 0, "low": 0}
    for r in records:
        confidence_counts[r.confidence] += 1

    needs_review = sum(1 for r in records if r.needs_human_review)
    safety_flagged = sum(1 for r in records if r.safety_flags)

    all_flags: list[str] = []
    for r in records:
        all_flags.extend(r.safety_flags)

    latencies = sorted(r.latency_ms for r in records)

    pipeline_counts: dict[str, int] = {}
    for r in records:
        pipeline_counts[r.pipeline_used] = pipeline_counts.get(r.pipeline_used, 0) + 1

    return {
        "total_queries": total,
        "found_rate": found_count / total,
        "not_found_rate": (total - found_count) / total,
        "confidence_distribution": {
            k: v / total for k, v in confidence_counts.items()
        },
        "needs_review_rate": needs_review / total,
        "safety_flag_rate": safety_flagged / total,
        "unique_safety_flags": sorted(set(all_flags)),
        "mean_latency_ms": sum(latencies) / total,
        "p50_latency_ms": latencies[len(latencies) // 2],
        "p95_latency_ms": latencies[int(len(latencies) * 0.95)],
        "pipeline_distribution": {
            k: v / total for k, v in pipeline_counts.items()
        },
    }


def compare_periods(
    current: dict[str, Any],
    baseline: dict[str, Any],
    thresholds: dict[str, float],
) -> dict[str, Any]:
    """Compare two stat periods and flag regressions."""
    checks: list[dict[str, Any]] = []

    # Found rate drop
    found_drop = baseline["found_rate"] - current["found_rate"]
    checks.append({
        "metric": "found_rate",
        "baseline": baseline["found_rate"],
        "current": current["found_rate"],
        "delta": -found_drop,
        "threshold": thresholds["found_rate"],
        "status": "FAIL" if found_drop > thresholds["found_rate"] else "PASS",
        "description": f"Found rate {'dropped' if found_drop > 0 else 'improved'} by {abs(found_drop)*100:.1f}pp",
    })

    # Mean latency increase (relative)
    if baseline["mean_latency_ms"] > 0:
        latency_increase = (
            (current["mean_latency_ms"] - baseline["mean_latency_ms"])
            / baseline["mean_latency_ms"]
        )
    else:
        latency_increase = 0.0
    checks.append({
        "metric": "mean_latency_ms",
        "baseline": baseline["mean_latency_ms"],
        "current": current["mean_latency_ms"],
        "delta_pct": latency_increase,
        "threshold": thresholds["latency"],
        "status": "FAIL" if latency_increase > thresholds["latency"] else "PASS",
        "description": f"Mean latency {'increased' if latency_increase > 0 else 'decreased'} by {abs(latency_increase)*100:.1f}%",
    })

    # Safety flag rate increase
    safety_increase = current["safety_flag_rate"] - baseline["safety_flag_rate"]
    checks.append({
        "metric": "safety_flag_rate",
        "baseline": baseline["safety_flag_rate"],
        "current": current["safety_flag_rate"],
        "delta": safety_increase,
        "threshold": thresholds["safety_flag"],
        "status": "FAIL" if safety_increase > thresholds["safety_flag"] else "PASS",
        "description": f"Safety flag rate {'increased' if safety_increase > 0 else 'decreased'} by {abs(safety_increase)*100:.1f}pp",
    })

    # Needs-review rate increase
    review_increase = current["needs_review_rate"] - baseline["needs_review_rate"]
    checks.append({
        "metric": "needs_review_rate",
        "baseline": baseline["needs_review_rate"],
        "current": current["needs_review_rate"],
        "delta": review_increase,
        "threshold": thresholds["needs_review"],
        "status": "FAIL" if review_increase > thresholds["needs_review"] else "PASS",
        "description": f"Needs-review rate {'increased' if review_increase > 0 else 'decreased'} by {abs(review_increase)*100:.1f}pp",
    })

    # High-confidence drop
    high_conf_drop = (
        baseline["confidence_distribution"].get("high", 0)
        - current["confidence_distribution"].get("high", 0)
    )
    checks.append({
        "metric": "high_confidence_rate",
        "baseline": baseline["confidence_distribution"].get("high", 0),
        "current": current["confidence_distribution"].get("high", 0),
        "delta": -high_conf_drop,
        "threshold": thresholds["high_confidence"],
        "status": "FAIL" if high_conf_drop > thresholds["high_confidence"] else "PASS",
        "description": f"High-confidence rate {'dropped' if high_conf_drop > 0 else 'improved'} by {abs(high_conf_drop)*100:.1f}pp",
    })

    failed = [c for c in checks if c["status"] == "FAIL"]
    overall = "FAIL" if failed else "PASS"

    return {
        "overall_status": overall,
        "checks": checks,
        "failures": len(failed),
    }


def _parse_month_arg(value: str, name: str) -> tuple[int, int]:
    """Parse and validate a YYYY-MM argument, returning (year, month)."""
    parts = value.split("-")
    if len(parts) != 2:
        raise SystemExit(f"Invalid {name}: expected YYYY-MM, got '{value}'")
    try:
        year, month = int(parts[0]), int(parts[1])
    except ValueError:
        raise SystemExit(f"Invalid {name}: expected YYYY-MM, got '{value}'")
    if not (2020 <= year <= 2100) or not (1 <= month <= 12):
        raise SystemExit(f"Invalid {name}: year={year}, month={month}")
    return year, month


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect operational drift between monthly audit periods"
    )
    parser.add_argument(
        "--current",
        default=None,
        help="Current period in YYYY-MM format (default: this month)",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="Baseline period in YYYY-MM format (default: previous month)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show stats for current period without comparison",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON path (default: reports/drift/drift_YYYYMMDD.json)",
    )
    parser.add_argument(
        "--connection-string",
        default=settings.STORAGE_CONNECTION_STRING,
        help="Azure Storage connection string",
    )
    # Threshold overrides
    parser.add_argument("--found-rate-threshold", type=float, default=DEFAULT_FOUND_RATE_THRESHOLD)
    parser.add_argument("--latency-threshold", type=float, default=DEFAULT_LATENCY_THRESHOLD)
    parser.add_argument("--safety-flag-threshold", type=float, default=DEFAULT_SAFETY_FLAG_THRESHOLD)
    parser.add_argument("--needs-review-threshold", type=float, default=DEFAULT_NEEDS_REVIEW_THRESHOLD)
    parser.add_argument("--high-confidence-threshold", type=float, default=DEFAULT_HIGH_CONFIDENCE_THRESHOLD)

    args = parser.parse_args()

    if not args.connection_string:
        logger.error("STORAGE_CONNECTION_STRING not set.")
        return 1

    now = datetime.now(timezone.utc)

    # Resolve current/baseline months
    if args.current:
        current_year, current_month = _parse_month_arg(args.current, "--current")
    else:
        current_year, current_month = now.year, now.month

    if args.baseline:
        baseline_year, baseline_month = _parse_month_arg(args.baseline, "--baseline")
    else:
        # Previous month
        if current_month == 1:
            baseline_year, baseline_month = current_year - 1, 12
        else:
            baseline_year, baseline_month = current_year, current_month - 1

    thresholds = {
        "found_rate": args.found_rate_threshold,
        "latency": args.latency_threshold,
        "safety_flag": args.safety_flag_threshold,
        "needs_review": args.needs_review_threshold,
        "high_confidence": args.high_confidence_threshold,
    }

    blob_service = BlobServiceClient.from_connection_string(args.connection_string)
    container_client = blob_service.get_container_client(CONTAINER_NAME)

    if not container_client.exists():
        logger.error(f"Container '{CONTAINER_NAME}' does not exist.")
        return 1

    # Load current period
    current_label = f"{current_year:04d}-{current_month:02d}"
    logger.info(f"Loading current period: {current_label}")
    current_records = load_month_records(container_client, current_year, current_month)
    current_stats = compute_stats(current_records)
    logger.info(f"  {current_stats['total_queries']} queries loaded")

    report: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "current_period": current_label,
        "current_stats": current_stats,
    }

    if args.dry_run:
        report["mode"] = "dry_run"
        print()
        print("=" * 60)
        print(f"AUDIT STATS — {current_label} (dry run)")
        print("=" * 60)
        _print_stats(current_stats)
    else:
        # Load baseline period
        baseline_label = f"{baseline_year:04d}-{baseline_month:02d}"
        logger.info(f"Loading baseline period: {baseline_label}")
        baseline_records = load_month_records(container_client, baseline_year, baseline_month)
        baseline_stats = compute_stats(baseline_records)
        logger.info(f"  {baseline_stats['total_queries']} queries loaded")

        if baseline_stats["total_queries"] == 0:
            logger.warning(f"No audit data for baseline period {baseline_label}. Showing current stats only.")
            report["mode"] = "no_baseline"
            print()
            print("=" * 60)
            print(f"AUDIT STATS — {current_label} (no baseline data for {baseline_label})")
            print("=" * 60)
            _print_stats(current_stats)
        else:
            comparison = compare_periods(current_stats, baseline_stats, thresholds)
            report["baseline_period"] = baseline_label
            report["baseline_stats"] = baseline_stats
            report["comparison"] = comparison
            report["thresholds"] = thresholds

            print()
            print("=" * 60)
            print(f"DRIFT REPORT — {current_label} vs {baseline_label}")
            print("=" * 60)
            print(f"  Current queries:  {current_stats['total_queries']}")
            print(f"  Baseline queries: {baseline_stats['total_queries']}")
            print()
            for check in comparison["checks"]:
                icon = "PASS" if check["status"] == "PASS" else "FAIL"
                print(f"  [{icon}] {check['metric']}: {check['description']}")
            print()
            print(f"  Overall: {comparison['overall_status']} ({comparison['failures']} failures)")
            print("=" * 60)

    # Write output
    if args.output:
        output_path = Path(args.output)
    else:
        output_dir = REPO_ROOT / "reports" / "drift"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"drift_{now.strftime('%Y%m%d')}.json"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    logger.info(f"Report saved to: {output_path}")

    # Return non-zero if any drift check failed
    if not args.dry_run and "comparison" in report:
        if report["comparison"]["overall_status"] == "FAIL":
            return 1
    return 0


def _print_stats(stats: dict[str, Any]) -> None:
    """Pretty-print stats to console."""
    print(f"  Total queries:       {stats['total_queries']}")
    print(f"  Found rate:          {stats['found_rate']*100:.1f}%")
    print(f"  Not-found rate:      {stats['not_found_rate']*100:.1f}%")
    conf = stats["confidence_distribution"]
    print(f"  Confidence:          high={conf.get('high',0)*100:.1f}% "
          f"med={conf.get('medium',0)*100:.1f}% "
          f"low={conf.get('low',0)*100:.1f}%")
    print(f"  Needs-review rate:   {stats['needs_review_rate']*100:.1f}%")
    print(f"  Safety flag rate:    {stats['safety_flag_rate']*100:.1f}%")
    if stats["unique_safety_flags"]:
        print(f"  Safety flag types:   {', '.join(stats['unique_safety_flags'])}")
    print(f"  Mean latency:        {stats['mean_latency_ms']:.0f} ms")
    print(f"  P50 latency:         {stats['p50_latency_ms']:.0f} ms")
    print(f"  P95 latency:         {stats['p95_latency_ms']:.0f} ms")
    if stats["pipeline_distribution"]:
        pipelines = ", ".join(
            f"{k}={v*100:.0f}%" for k, v in stats["pipeline_distribution"].items()
        )
        print(f"  Pipelines:           {pipelines}")


if __name__ == "__main__":
    raise SystemExit(main())
