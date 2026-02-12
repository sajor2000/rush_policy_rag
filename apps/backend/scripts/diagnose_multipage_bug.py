#!/usr/bin/env python3
"""
Bug 1 Diagnostic: Multi-Page Retrieval Failure

Queries the live Azure AI Search index to determine if page 2+ chunks
exist for known multi-page policies (HR-C 06.00, HR-C 05.00).

Results determine whether a re-index is needed:
  - Page 2+ chunks MISSING       → re-index required
  - Page 2+ chunks have page=None → re-index required (fallback chunking bug)
  - Page 2+ chunks exist + pages  → search ranking issue (Bug 2 filter may resolve)

Usage:
    cd apps/backend
    python scripts/diagnose_multipage_bug.py
    python scripts/diagnose_multipage_bug.py --ref "HR-C 06.00"
"""

import os
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(env_path)

from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from app.core.security import escape_odata_string


def diagnose(ref_number: str):
    endpoint = os.environ.get("SEARCH_ENDPOINT")
    api_key = os.environ.get("SEARCH_API_KEY")
    index_name = os.environ.get("SEARCH_INDEX_NAME", "rush-policies-active")

    if not endpoint or not api_key:
        print("ERROR: SEARCH_ENDPOINT and SEARCH_API_KEY must be set in .env")
        sys.exit(1)

    client = SearchClient(
        endpoint=endpoint,
        index_name=index_name,
        credential=AzureKeyCredential(api_key)
    )

    safe_ref = escape_odata_string(ref_number)

    print(f"\n{'='*70}")
    print(f"DIAGNOSTIC: Chunks for reference_number = '{ref_number}'")
    print(f"{'='*70}\n")

    results = list(client.search(
        search_text="*",
        filter=f"reference_number eq '{safe_ref}'",
        select=["id", "page_number", "chunk_index", "section",
                "chunk_level", "content"],
        top=50,
        order_by=["chunk_index asc"]
    ))

    if not results:
        print(f"  NO CHUNKS FOUND for '{ref_number}'")
        print(f"  → This policy may not be indexed at all.")
        print(f"  → Action: Run full_pipeline_ingest.py\n")
        return False

    print(f"  Total chunks: {len(results)}\n")

    pages_seen = set()
    null_page_count = 0
    max_chunk_index = 0

    for r in results:
        page = r.get("page_number")
        idx = r.get("chunk_index", 0)
        section = r.get("section", "")
        level = r.get("chunk_level", "")
        content_preview = (r.get("content") or "")[:80].replace("\n", " ")

        max_chunk_index = max(max_chunk_index, idx)

        page_str = str(page) if page is not None else "NULL"
        if page is None:
            null_page_count += 1
        else:
            pages_seen.add(page)

        print(f"  chunk[{idx:2d}]  page={page_str:>4s}  level={level:<10s} "
              f"sec={section or '-':<8s} | {content_preview}...")

    print(f"\n{'='*70}")
    print(f"SUMMARY")
    print(f"{'='*70}")
    print(f"  Total chunks:       {len(results)}")
    print(f"  Pages seen:         {sorted(pages_seen) if pages_seen else 'NONE'}")
    print(f"  Null page_number:   {null_page_count}/{len(results)}")
    print(f"  Max chunk_index:    {max_chunk_index}")

    needs_reindex = False

    if not pages_seen or max(pages_seen, default=0) <= 1:
        print(f"\n  PROBLEM: Only page 1 (or no pages) found.")
        if null_page_count > 0:
            print(f"  → Fallback chunking produced chunks WITHOUT page numbers.")
            print(f"  → The code fix (chunker.py) is applied. RE-INDEX REQUIRED.")
        else:
            print(f"  → Page 2+ chunks may be missing from ingestion.")
            print(f"  → RE-INDEX REQUIRED.")
        needs_reindex = True
    elif null_page_count > 0:
        print(f"\n  PARTIAL: {null_page_count} chunks have null page_number.")
        print(f"  → RE-INDEX RECOMMENDED to populate page numbers.")
        needs_reindex = True
    else:
        print(f"\n  OK: Multi-page chunks exist with page numbers.")
        print(f"  → Bug 2 filter fix should resolve retrieval ranking.")

    if needs_reindex:
        print(f"\n  TO FIX: cd apps/backend && python scripts/full_pipeline_ingest.py --run-tests")

    print()
    return not needs_reindex


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Diagnose multi-page retrieval bug")
    parser.add_argument("--ref", default="HR-C 06.00",
                        help="Reference number to check (default: HR-C 06.00)")
    args = parser.parse_args()

    ok = diagnose(args.ref)

    # Also check HR-C 05.00 (the Shift Differentials policy from Bug 3)
    if args.ref == "HR-C 06.00":
        diagnose("HR-C 05.00")

    sys.exit(0 if ok else 1)
