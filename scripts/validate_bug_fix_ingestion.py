#!/usr/bin/env python3
"""
Pre-reindex validation: process 3 bug-tracker PDFs and verify all metadata
and content prefix generation before committing to a full 2000+ doc reindex.

Tests:
  BUG-001: Page numbers present, content prefix includes policy/section metadata
  BUG-002: policy_number extracted and normalized from filename + text
  BUG-003: Content field includes prefix for BM25 keyword matching

Usage:
    python scripts/validate_bug_fix_ingestion.py
    python scripts/validate_bug_fix_ingestion.py --limit 5
    python scripts/validate_bug_fix_ingestion.py --pdf /path/to/specific.pdf

Requires: STORAGE_CONNECTION_STRING, CONTAINER_NAME in .env (or --pdf for local)
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "backend"))

from dotenv import load_dotenv
load_dotenv()

# ---------------------------------------------------------------------------
# Target PDFs — the ones referenced in the bug tracker
# ---------------------------------------------------------------------------
BUG_TRACKER_PDFS = [
    # BUG-001 / BUG-002: HR-C 06.00 (Time and Attendance, multi-page)
    "HR-C 06.00",
    # BUG-002 / BUG-003: HR-C 05.00 (Shift Differentials, punctuation in title)
    "HR-C 05.00",
    # BUG-001: HR-E 03.00 (Disciplinary Procedures, multi-page)
    "HR-E 03.00",
]


def find_matching_blobs(container_client, targets: list[str]) -> list[str]:
    """Find blobs whose names contain any of the target strings."""
    matched = []
    for blob in container_client.list_blobs():
        name = blob.name
        if not name.lower().endswith(".pdf"):
            continue
        for target in targets:
            # Match "HR-C 06.00" or "HR-C_06.00" in filename
            if target in name or target.replace(" ", "_") in name:
                matched.append(name)
                break
    return sorted(matched)


def process_single_pdf(pdf_path: str):
    """Process one PDF with PolicyChunker and return chunks."""
    from preprocessing.chunker import PolicyChunker
    chunker = PolicyChunker()
    result = chunker.process_pdf_with_status(pdf_path)
    return result


def validate_chunks(chunks, filename: str) -> dict:
    """
    Validate all bug-fix requirements on a set of chunks.
    Returns a report dict with pass/fail for each check.
    """
    report = {
        "filename": filename,
        "total_chunks": len(chunks),
        "checks": {},
        "sample_content_prefix": "",
        "sample_azure_doc_keys": [],
        "errors": [],
    }

    if not chunks:
        report["errors"].append("NO CHUNKS PRODUCED")
        return report

    # --- Convert to Azure documents (tests to_azure_document + _build_content_prefix) ---
    azure_docs = []
    for chunk in chunks:
        try:
            azure_docs.append(chunk.to_azure_document())
        except Exception as e:
            report["errors"].append(f"to_azure_document() failed: {e}")

    if not azure_docs:
        report["errors"].append("ALL to_azure_document() calls failed")
        return report

    report["sample_azure_doc_keys"] = sorted(azure_docs[0].keys())

    # === BUG-001 CHECKS: Multi-page retrieval ===

    # Check 1: page_number field present
    pages = [d.get("page_number") for d in azure_docs]
    pages_with_values = [p for p in pages if p is not None]
    report["checks"]["page_numbers_present"] = {
        "pass": len(pages_with_values) > 0,
        "detail": f"{len(pages_with_values)}/{len(azure_docs)} chunks have page_number",
        "values": sorted(set(pages_with_values)),
    }

    # Check 2: Multiple pages represented (multi-page doc)
    unique_pages = set(pages_with_values)
    report["checks"]["multi_page_coverage"] = {
        "pass": len(unique_pages) > 1,
        "detail": f"{len(unique_pages)} unique pages found: {sorted(unique_pages)}",
    }

    # Check 3: Content prefix present (REC-007)
    prefixed = [d for d in azure_docs if d["content"].startswith("[")]
    report["checks"]["content_prefix_present"] = {
        "pass": len(prefixed) > 0,
        "detail": f"{len(prefixed)}/{len(azure_docs)} chunks have [prefix]",
    }

    # Sample the first prefixed chunk
    if prefixed:
        first_line = prefixed[0]["content"].split("\n")[0]
        report["sample_content_prefix"] = first_line

    # Check 4: Prefix contains policy_number
    policy_num = azure_docs[0].get("policy_number", "")
    if policy_num and prefixed:
        prefix_has_policy = any(policy_num in d["content"].split("\n")[0] for d in prefixed)
        report["checks"]["prefix_has_policy_number"] = {
            "pass": prefix_has_policy,
            "detail": f"Looking for '{policy_num}' in prefix",
        }

    # Check 5: Prefix contains title
    title = azure_docs[0].get("title", "")
    if title and prefixed:
        # Check first few words of title (may be truncated)
        title_hint = title.split()[0] if title else ""
        prefix_has_title = any(title_hint in d["content"].split("\n")[0] for d in prefixed)
        report["checks"]["prefix_has_title"] = {
            "pass": prefix_has_title,
            "detail": f"Looking for '{title_hint}' in prefix",
        }

    # === BUG-002 CHECKS: Policy number extraction ===

    # Check 6: policy_number extracted
    report["checks"]["policy_number_extracted"] = {
        "pass": bool(policy_num),
        "detail": f"policy_number = '{policy_num}'",
    }

    # Check 7: policy_number format is canonical XX-X NN.NN
    import re
    canonical_pattern = re.compile(r'^[A-Z]{2}-[A-Z] \d{2}\.\d{2}$')
    report["checks"]["policy_number_canonical_format"] = {
        "pass": bool(canonical_pattern.match(policy_num)) if policy_num else False,
        "detail": f"'{policy_num}' matches XX-X NN.NN" if policy_num else "no policy_number",
    }

    # Check 8: policy_number consistent across all chunks
    all_policy_nums = set(d.get("policy_number", "") for d in azure_docs)
    report["checks"]["policy_number_consistent"] = {
        "pass": len(all_policy_nums) == 1 and "" not in all_policy_nums,
        "detail": f"unique values: {all_policy_nums}",
    }

    # === BUG-003 CHECKS: Metadata completeness for matching ===

    # Check 9: title extracted
    report["checks"]["title_extracted"] = {
        "pass": bool(title),
        "detail": f"title = '{title[:80]}'" if title else "no title",
    }

    # Check 10: applies_to has at least one entity
    applies_to = azure_docs[0].get("applies_to", "")
    entity_booleans = {
        k: azure_docs[0].get(k, False)
        for k in [
            "applies_to_rumc", "applies_to_rumg", "applies_to_rmg",
            "applies_to_roph", "applies_to_rcmc", "applies_to_rch",
            "applies_to_roppg", "applies_to_rcmg", "applies_to_ru",
        ]
    }
    true_entities = [k for k, v in entity_booleans.items() if v]
    report["checks"]["applies_to_populated"] = {
        "pass": bool(applies_to) or len(true_entities) > 0,
        "detail": f"applies_to='{applies_to}', booleans={true_entities}",
    }

    # Check 11: section metadata on at least some chunks
    sections = [d.get("section", "") for d in azure_docs if d.get("section")]
    report["checks"]["section_metadata_present"] = {
        "pass": len(sections) > 0,
        "detail": f"{len(sections)}/{len(azure_docs)} chunks have section metadata",
        "samples": sections[:5],
    }

    # Check 12: content field is prefix + text (not just text)
    raw_texts = [c.text for c in chunks]
    contents = [d["content"] for d in azure_docs]
    content_longer = sum(1 for t, c in zip(raw_texts, contents) if len(c) > len(t))
    report["checks"]["content_includes_prefix_text"] = {
        "pass": content_longer > 0,
        "detail": f"{content_longer}/{len(chunks)} content fields are longer than raw text (prefix added)",
    }

    # Check 13: raw text field preserved (not modified)
    report["checks"]["raw_text_preserved"] = {
        "pass": all(c.text in d["content"] for c, d in zip(chunks, azure_docs)),
        "detail": "All chunk.text appears verbatim in content field",
    }

    # --- Summary ---
    all_passed = all(c.get("pass", False) for c in report["checks"].values())
    report["all_passed"] = all_passed

    return report


def main():
    parser = argparse.ArgumentParser(description="Validate bug-fix ingestion on sample PDFs")
    parser.add_argument("--pdf", help="Path to a specific local PDF to test")
    parser.add_argument("--limit", type=int, default=None, help="Max blobs to process from storage")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    print("=" * 72)
    print("BUG-FIX INGESTION VALIDATION")
    print("Validates BUG-001/002/003 fixes before full reindex")
    print("=" * 72)

    pdf_paths = []
    temp_dir = None

    if args.pdf:
        # Local PDF mode
        if not os.path.exists(args.pdf):
            print(f"ERROR: File not found: {args.pdf}")
            return 1
        pdf_paths.append(args.pdf)
    else:
        # Azure Blob Storage mode
        from azure.storage.blob import BlobServiceClient

        conn_str = os.getenv("STORAGE_CONNECTION_STRING")
        container = os.getenv("CONTAINER_NAME", "policies-active")

        if not conn_str:
            print("ERROR: STORAGE_CONNECTION_STRING not set. Use --pdf for local testing.")
            return 1

        blob_service = BlobServiceClient.from_connection_string(conn_str)
        container_client = blob_service.get_container_client(container)

        print(f"\nSearching '{container}' for bug-tracker PDFs...")
        matched = find_matching_blobs(container_client, BUG_TRACKER_PDFS)

        if not matched:
            print("WARNING: No bug-tracker PDFs found. Falling back to first 3 PDFs...")
            all_blobs = sorted(
                b.name for b in container_client.list_blobs()
                if b.name.lower().endswith(".pdf")
            )
            matched = all_blobs[:3]

        if args.limit:
            matched = matched[:args.limit]

        print(f"Found {len(matched)} PDFs to validate:")
        for name in matched:
            print(f"  - {name}")

        # Download to temp
        temp_dir = tempfile.mkdtemp(prefix="bugfix_validation_")
        for blob_name in matched:
            local_path = os.path.join(temp_dir, os.path.basename(blob_name))
            blob_client = container_client.get_blob_client(blob_name)
            with open(local_path, "wb") as f:
                f.write(blob_client.download_blob().readall())
            pdf_paths.append(local_path)
            print(f"  Downloaded: {blob_name}")

    try:
        return _run_validation(pdf_paths, args)
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _run_validation(pdf_paths: list, args) -> int:
    """Run PDF validation and return exit code."""
    # Process each PDF
    print("\n" + "-" * 72)
    print("PROCESSING PDFs with PolicyChunker...")
    print("-" * 72)

    # Lazy-load chunker once
    print("\nLoading Docling chunker (this may take a moment)...")
    from preprocessing.chunker import PolicyChunker
    chunker = PolicyChunker()
    print("Chunker loaded.\n")

    all_reports = []
    total_pass = 0
    total_fail = 0

    for pdf_path in pdf_paths:
        filename = os.path.basename(pdf_path)
        print(f"\n{'='*60}")
        print(f"Processing: {filename}")
        print(f"{'='*60}")

        result = chunker.process_pdf_with_status(pdf_path)

        if result.is_error:
            print(f"  PROCESSING ERROR: {result.status.value} - {result.error_message}")
            all_reports.append({
                "filename": filename,
                "error": f"{result.status.value}: {result.error_message}",
                "all_passed": False,
            })
            total_fail += 1
            continue

        chunks = result.chunks
        print(f"  Chunks produced: {len(chunks)}")

        report = validate_chunks(chunks, filename)
        all_reports.append(report)

        # Print results
        for name, check in report["checks"].items():
            status = "PASS" if check["pass"] else "FAIL"
            icon = "+" if check["pass"] else "X"
            print(f"  [{icon}] {status}: {name}")
            print(f"       {check['detail']}")

        if report.get("sample_content_prefix"):
            print(f"\n  Sample prefix: {report['sample_content_prefix'][:120]}")

        if report.get("errors"):
            for err in report["errors"]:
                print(f"  ERROR: {err}")

        if report["all_passed"]:
            total_pass += 1
        else:
            total_fail += 1

    # Summary
    print(f"\n{'='*72}")
    print(f"VALIDATION SUMMARY")
    print(f"{'='*72}")
    print(f"Documents: {total_pass} PASSED / {total_fail} FAILED / {len(all_reports)} total")

    for report in all_reports:
        status = "PASS" if report.get("all_passed") else "FAIL"
        filename = report["filename"]
        if "checks" in report:
            check_count = len(report["checks"])
            failed_checks = [n for n, c in report["checks"].items() if not c["pass"]]
            if failed_checks:
                print(f"  [{status}] {filename} — failed: {', '.join(failed_checks)}")
            else:
                print(f"  [{status}] {filename} — all {check_count} checks passed")
        else:
            print(f"  [{status}] {filename} — {report.get('error', 'unknown error')}")

    if total_fail == 0:
        print(f"\nAll checks passed. Safe to proceed with full reindex:")
        print(f"  python scripts/full_pipeline_ingest.py --run-tests")
    else:
        print(f"\n{total_fail} document(s) have failures. Fix before full reindex.")

    if args.json:
        print(f"\n--- JSON Report ---")
        print(json.dumps(all_reports, indent=2, default=str))

    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
