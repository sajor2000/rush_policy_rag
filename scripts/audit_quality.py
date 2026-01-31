#!/usr/bin/env python3
"""
Quality Audit Script for PDF Batch Processing

Validates metadata extraction quality on a batch of PDFs before full indexing.

Criteria:
- Page number extraction > 90%
- Reference number present
- Title extracted correctly
- Entity checkboxes detected
- No critical errors

Usage:
    # Audit test batch
    python scripts/audit_quality.py --input pdf_staging/02_test_batch

    # Generate detailed report
    python scripts/audit_quality.py --input pdf_staging/02_test_batch --report pdf_staging/04_audit_reports/test_batch_report.json
"""

import sys
from pathlib import Path

# Add backend to path
backend_path = Path(__file__).parent.parent / "apps" / "backend"
sys.path.insert(0, str(backend_path))

import argparse
import json
import logging
from datetime import datetime
from typing import List, Dict, Any
from dotenv import load_dotenv

load_dotenv()

from preprocessing.chunker import PolicyChunker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def validate_input_folder(path_str: str) -> Path:
    """
    Validate input folder path with security checks.

    Prevents path traversal attacks and validates directory exists.
    """
    try:
        input_path = Path(path_str).resolve()  # Resolve symlinks and ".."
    except Exception as e:
        print(f"❌ Invalid path: {e}")
        sys.exit(1)

    # Must exist
    if not input_path.exists():
        print(f"❌ Path not found: {input_path}")
        sys.exit(1)

    # Must be a directory
    if not input_path.is_dir():
        print(f"❌ Path is not a directory: {input_path}")
        sys.exit(1)

    # Must be within repository (prevent path traversal)
    repo_root = Path(__file__).parent.parent.resolve()
    try:
        input_path.relative_to(repo_root)
    except ValueError:
        print(f"❌ Security: Path must be within repository: {repo_root}")
        print(f"   Attempted path: {input_path}")
        sys.exit(1)

    return input_path


class QualityAuditor:
    """Audits PDF processing quality."""

    def __init__(self):
        self.chunker = PolicyChunker(max_chunk_size=1500)
        self.results = []

    def audit_pdf(self, pdf_path: Path) -> Dict[str, Any]:
        """Process and audit a single PDF."""
        try:
            result = self.chunker.process_pdf_with_status(str(pdf_path))

            if not result.chunks:
                return {
                    "filename": pdf_path.name,
                    "success": False,
                    "error": "No chunks extracted",
                    "chunks": 0
                }

            # Calculate quality metrics
            chunk_count = len(result.chunks)
            chunks_with_pages = sum(1 for c in result.chunks if c.page_number)
            page_rate = (chunks_with_pages / chunk_count * 100) if chunk_count > 0 else 0

            chunk = result.chunks[0]  # First chunk for document-level metadata

            return {
                "filename": pdf_path.name,
                "success": True,
                "chunks": chunk_count,
                "page_extraction_rate": round(page_rate, 1),
                "has_reference_number": bool(chunk.reference_number),
                "has_title": bool(chunk.policy_title),
                "has_applies_to": bool(chunk.applies_to),
                "reference_number": chunk.reference_number or "NOT FOUND",
                "title": chunk.policy_title or "NOT FOUND",
                "applies_to": chunk.applies_to or "NOT FOUND",
                "entities": {
                    "RUMC": chunk.applies_to_rumc,
                    "RUMG": chunk.applies_to_rumg,
                    "RMG": chunk.applies_to_rmg,
                }
            }
        except Exception as e:
            logger.error(f"Failed to process {pdf_path.name}: {e}")
            return {
                "filename": pdf_path.name,
                "success": False,
                "error": str(e),
                "chunks": 0
            }

    def audit_batch(self, folder: Path) -> Dict[str, Any]:
        """Audit all PDFs in a folder."""
        pdf_files = list(folder.glob("*.pdf"))

        if not pdf_files:
            print(f"❌ No PDF files found in {folder}")
            sys.exit(1)

        print(f"\n{'='*70}")
        print(f"QUALITY AUDIT - {len(pdf_files)} PDFs")
        print(f"{'='*70}\n")

        for i, pdf_path in enumerate(pdf_files, 1):
            print(f"[{i}/{len(pdf_files)}] {pdf_path.name}...", end=" ")
            result = self.audit_pdf(pdf_path)
            self.results.append(result)

            if result["success"]:
                print(f"✅ {result['chunks']} chunks, {result['page_extraction_rate']}% pages")
            else:
                print(f"❌ {result.get('error', 'Unknown error')}")

        # Calculate summary statistics
        successful = [r for r in self.results if r["success"]]
        failed = [r for r in self.results if not r["success"]]

        if successful:
            avg_page_rate = sum(r["page_extraction_rate"] for r in successful) / len(successful)
            has_ref = sum(1 for r in successful if r["has_reference_number"])
            has_title = sum(1 for r in successful if r["has_title"])
            avg_chunks = sum(r["chunks"] for r in successful) / len(successful)
        else:
            avg_page_rate = 0
            has_ref = 0
            has_title = 0
            avg_chunks = 0

        summary = {
            "total_pdfs": len(pdf_files),
            "successful": len(successful),
            "failed": len(failed),
            "avg_page_extraction_rate": round(avg_page_rate, 1),
            "reference_numbers_found": has_ref,
            "titles_found": has_title,
            "avg_chunks_per_pdf": round(avg_chunks, 1),
            "timestamp": datetime.now().isoformat()
        }

        # Quality gate checks
        passed_quality = (
            summary["avg_page_extraction_rate"] >= 90 and
            summary["reference_numbers_found"] >= summary["successful"] * 0.9 and
            summary["titles_found"] >= summary["successful"] * 0.9 and
            summary["failed"] == 0
        )

        summary["quality_gate_passed"] = passed_quality

        return {
            "summary": summary,
            "details": self.results
        }

    def print_summary(self, report: Dict[str, Any]):
        """Print audit summary."""
        summary = report["summary"]

        print(f"\n{'='*70}")
        print("AUDIT SUMMARY")
        print(f"{'='*70}")
        print(f"\nTotal PDFs:          {summary['total_pdfs']}")
        print(f"Successful:          {summary['successful']}")
        print(f"Failed:              {summary['failed']}")
        print(f"\nQuality Metrics:")
        print(f"  Page Extraction:   {summary['avg_page_extraction_rate']}% (target: ≥90%)")
        print(f"  Reference Numbers: {summary['reference_numbers_found']}/{summary['successful']} (target: 90%+)")
        print(f"  Titles:            {summary['titles_found']}/{summary['successful']} (target: 90%+)")
        print(f"  Avg Chunks/PDF:    {summary['avg_chunks_per_pdf']}")

        print(f"\n{'='*70}")
        if summary["quality_gate_passed"]:
            print("✅ QUALITY GATE: PASSED")
            print("   Proceed with full batch indexing")
        else:
            print("❌ QUALITY GATE: FAILED")
            print("   Review errors before proceeding")
        print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description="Audit PDF processing quality")
    parser.add_argument("--input", required=True, help="Input folder with PDFs")
    parser.add_argument("--report", help="Save detailed report to JSON file")
    args = parser.parse_args()

    # Validate input folder with security checks
    input_folder = validate_input_folder(args.input)

    auditor = QualityAuditor()
    report = auditor.audit_batch(input_folder)
    auditor.print_summary(report)

    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2)
        print(f"💾 Detailed report saved: {report_path}\n")

    # Exit with code 1 if quality gate failed
    if not report["summary"]["quality_gate_passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
