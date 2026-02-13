#!/usr/bin/env python3
"""
Persist Evaluation Baseline — Save structured evaluation snapshot to Azure Blob.

After each monthly release, persists a JSON snapshot of all evaluation scores
to the 'evaluation-baselines' Azure Blob container for cross-release comparison.

Storage layout:
    evaluation-baselines/
    ├── 2026-01/
    │   └── baseline_20260115_143000.json
    ├── 2026-02/
    │   └── baseline_20260212_200000.json
    └── latest_baseline.json        (overwritten each run)

Usage:
    # Persist baseline from a monthly release run directory
    python scripts/persist_evaluation_baseline.py --run-dir reports/monthly/2026-02/run_...

    # Skip pulling the 30-day audit snapshot
    python scripts/persist_evaluation_baseline.py --run-dir ... --skip-audit-snapshot

    # Dry run — assemble and print baseline without uploading
    python scripts/persist_evaluation_baseline.py --run-dir ... --dry-run
"""

import argparse
import json
import logging
import subprocess
import sys
import uuid
from calendar import monthrange
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv

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

BASELINE_CONTAINER = "evaluation-baselines"
AUDIT_CONTAINER = settings.CHAT_AUDIT_CONTAINER  # "chat-audit"


def _get_git_commit() -> str:
    """Get current short git commit hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def load_summary(run_dir: Path) -> dict[str, Any]:
    """Load summary.json from the run directory."""
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"summary.json not found in {run_dir}")
    return json.loads(summary_path.read_text(encoding="utf-8"))


def load_promptfoo_audit(run_dir: Path) -> dict[str, Any] | None:
    """Load PromptFoo audit JSON if available."""
    audit_path = run_dir / "13b_promptfoo_audit.json"
    if not audit_path.exists():
        logger.info("No PromptFoo audit found in run directory")
        return None
    return json.loads(audit_path.read_text(encoding="utf-8"))


def load_hr_regression_results(run_dir: Path) -> dict[str, Any]:
    """Parse HR regression results from artifact."""
    # Check pre-cutover regressions
    for name in ("12_hr_regressions_pre_cutover.txt", "06_hr_regressions_no_changes.txt"):
        path = run_dir / name
        if path.exists():
            content = path.read_text(encoding="utf-8")
            # Count pass/fail from output lines
            passed = content.lower().count("pass")
            failed = content.lower().count("fail")
            total = passed + failed if (passed + failed) > 0 else 10
            return {"passed": passed, "failed": failed, "total": total}

    return {"passed": 0, "failed": 0, "total": 0, "note": "no artifact found"}


def collect_audit_snapshot(
    connection_string: str,
    period_days: int = 30,
) -> dict[str, Any]:
    """Collect aggregate stats from the last N days of audit data."""
    blob_service = BlobServiceClient.from_connection_string(connection_string)
    container_client = blob_service.get_container_client(AUDIT_CONTAINER)

    if not container_client.exists():
        return {"period_days": period_days, "total_queries": 0, "note": "container not found"}

    now = datetime.now(timezone.utc)
    records: list[ChatAuditRecord] = []

    for day_offset in range(period_days):
        dt = now - __import__("datetime").timedelta(days=day_offset)
        blob_name = f"{dt.year:04d}/{dt.month:02d}/{dt.day:02d}.jsonl"
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
                records.append(ChatAuditRecord.model_validate_json(line))
            except Exception:
                pass

    total = len(records)
    if total == 0:
        return {"period_days": period_days, "total_queries": 0}

    found_count = sum(1 for r in records if r.found)
    latencies = sorted(r.latency_ms for r in records)
    safety_flagged = sum(1 for r in records if r.safety_flags)
    confidence_counts = {"high": 0, "medium": 0, "low": 0}
    for r in records:
        confidence_counts[r.confidence] += 1

    return {
        "period_days": period_days,
        "total_queries": total,
        "found_rate": round(found_count / total, 4),
        "avg_latency_ms": round(sum(latencies) / total, 1),
        "p95_latency_ms": latencies[int(total * 0.95)],
        "safety_flag_rate": round(safety_flagged / total, 4),
        "confidence_distribution": {
            k: round(v / total, 4) for k, v in confidence_counts.items()
        },
    }


def build_baseline(
    run_dir: Path,
    skip_audit: bool,
    connection_string: str | None,
) -> dict[str, Any]:
    """Assemble a complete evaluation baseline from run artifacts."""
    summary = load_summary(run_dir)
    promptfoo = load_promptfoo_audit(run_dir)
    hr = load_hr_regression_results(run_dir)

    baseline: dict[str, Any] = {
        "baseline_id": str(uuid.uuid4()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release_info": {
            "environment": summary.get("environment", "unknown"),
            "candidate_index": summary.get("candidate_index", "unknown"),
            "previous_index": summary.get("previous_index", "unknown"),
            "run_mode": summary.get("run_mode", "unknown"),
            "git_commit": _get_git_commit(),
            "run_dir": str(run_dir),
        },
        "hr_regression": hr,
    }

    if promptfoo:
        baseline["promptfoo_audit"] = {
            "pass_rate": promptfoo.get("pass_rate", 0),
            "safety_flag_rate": promptfoo.get("safety_flag_rate", 0),
            "citation_coverage_rate": promptfoo.get("citation_coverage_rate", 0),
            "total": promptfoo.get("total", 0),
        }

    if not skip_audit and connection_string:
        logger.info("Collecting 30-day audit snapshot...")
        baseline["audit_snapshot"] = collect_audit_snapshot(connection_string)
    elif skip_audit:
        baseline["audit_snapshot"] = {"skipped": True}

    return baseline


def upload_baseline(
    baseline: dict[str, Any],
    connection_string: str,
) -> str:
    """Upload baseline to Azure Blob and return blob name."""
    blob_service = BlobServiceClient.from_connection_string(connection_string)
    container_client = blob_service.get_container_client(BASELINE_CONTAINER)

    # Auto-create container
    if not container_client.exists():
        container_client.create_container()
        logger.info(f"Created container: {BASELINE_CONTAINER}")

    now = datetime.now(timezone.utc)
    month_folder = now.strftime("%Y-%m")
    blob_name = f"{month_folder}/baseline_{now.strftime('%Y%m%d_%H%M%S')}.json"
    content = json.dumps(baseline, indent=2, default=str).encode("utf-8")

    # Upload dated baseline
    container_client.get_blob_client(blob_name).upload_blob(content, overwrite=True)
    logger.info(f"Uploaded: {blob_name}")

    # Update latest pointer
    container_client.get_blob_client("latest_baseline.json").upload_blob(
        content, overwrite=True
    )
    logger.info("Updated: latest_baseline.json")

    return blob_name


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Persist evaluation baseline snapshot to Azure Blob Storage"
    )
    parser.add_argument(
        "--run-dir",
        required=True,
        help="Path to the monthly release run directory containing summary.json",
    )
    parser.add_argument(
        "--skip-audit-snapshot",
        action="store_true",
        help="Skip pulling the 30-day audit snapshot from chat-audit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Assemble and print baseline without uploading",
    )
    parser.add_argument(
        "--connection-string",
        default=settings.STORAGE_CONNECTION_STRING,
        help="Azure Storage connection string (default: from .env)",
    )
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    if not run_dir.exists():
        logger.error(f"Run directory not found: {run_dir}")
        return 1

    if not args.connection_string and not args.dry_run:
        logger.error("STORAGE_CONNECTION_STRING not set.")
        return 1

    baseline = build_baseline(
        run_dir=run_dir,
        skip_audit=args.skip_audit_snapshot,
        connection_string=args.connection_string,
    )

    # Save locally in run directory
    local_path = run_dir / "evaluation_baseline.json"
    local_path.write_text(json.dumps(baseline, indent=2, default=str), encoding="utf-8")
    logger.info(f"Saved locally: {local_path}")

    if args.dry_run:
        print()
        print("=" * 60)
        print("EVALUATION BASELINE (dry run)")
        print("=" * 60)
        print(json.dumps(baseline, indent=2, default=str))
        return 0

    blob_name = upload_baseline(baseline, args.connection_string)

    print()
    print("=" * 60)
    print("EVALUATION BASELINE PERSISTED")
    print("=" * 60)
    print(f"  Baseline ID:  {baseline['baseline_id']}")
    print(f"  Environment:  {baseline['release_info']['environment']}")
    print(f"  Index:        {baseline['release_info']['candidate_index']}")
    print(f"  Blob:         {BASELINE_CONTAINER}/{blob_name}")
    print(f"  Local:        {local_path}")
    if "promptfoo_audit" in baseline:
        pf = baseline["promptfoo_audit"]
        print(f"  PromptFoo:    {pf['pass_rate']*100:.1f}% pass, {pf['safety_flag_rate']*100:.1f}% safety")
    hr = baseline["hr_regression"]
    print(f"  HR Regression: {hr.get('passed', 0)}/{hr.get('total', 0)} passed")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
