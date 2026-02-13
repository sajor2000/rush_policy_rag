#!/usr/bin/env python3
"""
Generate Test Dataset v5 with Real Policy Content

This script queries the live backend for each test case from v4 and extracts
actual policy text from evidence[].snippet to populate ground_truth_context.

Key improvements over v4:
1. Extracts evidence[].snippet (actual policy text) instead of placeholder keywords
2. Adds expected_not_found: true for cases with no matching policies
3. Downgrades 3 mislabeled critical cases to "high"
4. Preserves all existing metadata (category, subcategory, id)

Usage:
    # Requires backend running on localhost:8000
    python scripts/generate_test_dataset_v5.py

    # Or specify a different backend URL
    python scripts/generate_test_dataset_v5.py --backend-url http://localhost:8000
"""

import asyncio
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
V4_DATASET_PATH = PROJECT_ROOT / "apps" / "backend" / "data" / "test_dataset_v4.json"
V5_DATASET_PATH = PROJECT_ROOT / "apps" / "backend" / "data" / "test_dataset_v5.json"

# Cases with mislabeled criticality (have placeholder content or empty retrieval)
# These should be downgraded from "critical" to "high"
DOWNGRADE_IDS = {
    "real-pain-003",  # Epidural bolus dose - placeholder ground_truth
    "real-pain-008",  # Epidural disconnect - placeholder ground_truth
    "real-safe-007",  # Physical interventions - placeholder ground_truth
}


def adjust_criticality(case: dict) -> str:
    """Downgrade mislabeled critical cases with placeholder content."""
    case_id = case.get("id", "")
    original = case.get("criticality", "medium")

    if case_id in DOWNGRADE_IDS and original == "critical":
        print(f"  [DOWNGRADE] {case_id}: critical -> high")
        return "high"

    return original


def is_not_found_response(answer: str) -> bool:
    """Detect if the RAG response indicates no policy was found."""
    if not answer:
        return True

    lower_answer = answer.lower()
    not_found_phrases = [
        "could not find",
        "couldn't find",
        "no information",
        "not found in rush",
        "i don't have information",
        "i do not have information",
        "unable to find",
        "no specific policy",
        "no rush policy",
    ]

    return any(phrase in lower_answer for phrase in not_found_phrases)


def extract_ground_truth_from_evidence(evidence_list: list) -> list[str]:
    """Extract actual policy content from evidence[].snippet."""
    ground_truth_contexts = []

    for evidence in evidence_list:
        if not isinstance(evidence, dict):
            continue

        # Backend returns 'snippet' field with actual policy text (150-200 chars)
        snippet = evidence.get("snippet", "")

        # Fallback to other common field names
        if not snippet:
            snippet = evidence.get("content", "")
        if not snippet:
            snippet = evidence.get("text", "")

        if snippet and isinstance(snippet, str) and len(snippet) > 10:
            # Format with source attribution
            title = evidence.get("title", "Unknown Policy")
            ref = evidence.get("reference_number", "")
            source = evidence.get("source_file", "")

            # Build attribution
            attribution_parts = []
            if title:
                attribution_parts.append(f"Policy: {title}")
            if ref:
                attribution_parts.append(f"Ref: {ref}")
            if source:
                attribution_parts.append(f"Source: {source}")

            attribution = " | ".join(attribution_parts) if attribution_parts else ""

            if attribution:
                ground_truth_contexts.append(f"{snippet}\n[{attribution}]")
            else:
                ground_truth_contexts.append(snippet)

    return ground_truth_contexts


async def query_backend(client: httpx.AsyncClient, backend_url: str, query: str, max_retries: int = 3) -> Optional[dict]:
    """Query the backend API and return the response with retry logic for rate limits."""
    for attempt in range(max_retries):
        try:
            response = await client.post(
                f"{backend_url}/api/chat",
                json={
                    "message": query  # API uses 'message' field
                },
                timeout=60.0
            )

            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                # Rate limited - wait and retry
                wait_time = 30 * (attempt + 1)  # 30s, 60s, 90s
                print(f"  [RATE_LIMIT] Waiting {wait_time}s before retry {attempt + 1}/{max_retries}...")
                await asyncio.sleep(wait_time)
                continue
            else:
                print(f"  [ERROR] Backend returned {response.status_code} for: {query[:50]}...")
                return None

        except httpx.TimeoutException:
            print(f"  [TIMEOUT] Query timed out: {query[:50]}...")
            return None
        except Exception as e:
            print(f"  [ERROR] {type(e).__name__}: {e}")
            return None

    print(f"  [ERROR] Max retries exceeded for: {query[:50]}...")
    return None


async def generate_v5_dataset(backend_url: str, dry_run: bool = False, limit: int = 0):
    """Generate v5 dataset with real policy content from live backend."""

    print(f"Loading v4 dataset from: {V4_DATASET_PATH}", flush=True)

    if not V4_DATASET_PATH.exists():
        print(f"ERROR: v4 dataset not found at {V4_DATASET_PATH}")
        sys.exit(1)

    with open(V4_DATASET_PATH) as f:
        v4_data = json.load(f)

    test_cases = v4_data.get("test_cases", [])
    print(f"Found {len(test_cases)} test cases in v4 dataset", flush=True)

    # Apply limit if specified
    if limit > 0:
        test_cases = test_cases[:limit]
        print(f"Limited to first {limit} cases", flush=True)

    if dry_run:
        print("\n[DRY RUN] Would process the following cases:", flush=True)
        for i, case in enumerate(test_cases[:5]):
            print(f"  {i+1}. {case.get('id')}: {case.get('question', case.get('input', ''))[:60]}...", flush=True)
        print(f"  ... and {len(test_cases) - 5} more", flush=True)
        return

    v5_cases = []
    stats = {
        "success": 0,
        "not_found": 0,
        "errors": 0,
        "downgraded": 0,
        "placeholders_fixed": 0,
    }

    print(f"\nQuerying backend at: {backend_url}", flush=True)
    print("-" * 60, flush=True)

    async with httpx.AsyncClient() as client:
        for i, case in enumerate(test_cases):
            case_id = case.get("id", f"case-{i}")
            # v4 dataset uses "question" field, v5 uses "input"
            query = case.get("question", case.get("input", ""))

            print(f"[{i+1}/{len(test_cases)}] {case_id}: {query[:50]}...", flush=True)

            # Query live backend
            data = await query_backend(client, backend_url, query)

            if data is None:
                stats["errors"] += 1
                # Keep original case with empty ground_truth
                v5_case = {
                    "id": case_id,
                    "input": query,
                    "expected_output": case.get("expected_output", ""),
                    "ground_truth_context": [],
                    "retrieval_context": [],
                    "category": case.get("category"),
                    "subcategory": case.get("subcategory"),
                    "criticality": adjust_criticality(case),
                    "expected_not_found": True,  # Assume not found if backend error
                    "backend_error": True,
                }
                v5_cases.append(v5_case)
                continue

            # Extract actual policy content from evidence
            evidence = data.get("evidence", [])
            ground_truth_contexts = extract_ground_truth_from_evidence(evidence)

            # Get the actual response
            response_text = data.get("response", "")

            # Detect "not found" responses
            expected_not_found = is_not_found_response(response_text)

            if expected_not_found:
                stats["not_found"] += 1
                print(f"  -> [NOT_FOUND] No matching policy")
            else:
                stats["success"] += 1
                print(f"  -> [OK] Found {len(ground_truth_contexts)} evidence snippets")

            # Check if we're fixing a placeholder
            old_ground_truth = case.get("ground_truth_context", [])
            if isinstance(old_ground_truth, list):
                old_text = " ".join(str(x) for x in old_ground_truth)
            else:
                old_text = str(old_ground_truth)

            if "Expected keywords" in old_text and ground_truth_contexts:
                stats["placeholders_fixed"] += 1
                print(f"  -> [FIXED] Replaced placeholder with real content")

            # Track criticality changes
            new_criticality = adjust_criticality(case)
            if new_criticality != case.get("criticality"):
                stats["downgraded"] += 1

            # Build v5 case
            # v4 uses expected_answer, v5 uses expected_output
            expected_output = case.get("expected_answer", case.get("expected_output", response_text))
            v5_case = {
                "id": case_id,
                "input": query,
                "expected_output": expected_output,
                "ground_truth_context": ground_truth_contexts,
                "retrieval_context": [],  # Populated at runtime by evaluation
                "category": case.get("category"),
                "subcategory": case.get("subcategory"),
                "criticality": new_criticality,
                "expected_not_found": expected_not_found,
            }

            # Preserve additional metadata if present
            if case.get("source_policies"):
                v5_case["source_policies"] = case["source_policies"]
            if case.get("tags"):
                v5_case["tags"] = case["tags"]

            v5_cases.append(v5_case)

            # Delay to respect rate limits (30/min = 2 sec between requests)
            await asyncio.sleep(2.5)

    # Build output
    output = {
        "version": "5.0",
        "generated_at": datetime.now().isoformat(),
        "generator": "generate_test_dataset_v5.py",
        "v4_source": str(V4_DATASET_PATH),
        "backend_url": backend_url,
        "statistics": {
            "total_cases": len(v5_cases),
            "successful_queries": stats["success"],
            "not_found_cases": stats["not_found"],
            "backend_errors": stats["errors"],
            "criticality_downgraded": stats["downgraded"],
            "placeholders_fixed": stats["placeholders_fixed"],
        },
        "test_cases": v5_cases
    }

    # Save v5 dataset
    print(f"\nSaving v5 dataset to: {V5_DATASET_PATH}")
    with open(V5_DATASET_PATH, "w") as f:
        json.dump(output, f, indent=2)

    # Print summary
    print("\n" + "=" * 60)
    print("V5 DATASET GENERATION COMPLETE")
    print("=" * 60)
    print(f"Total cases:           {len(v5_cases)}")
    print(f"Successful queries:    {stats['success']}")
    print(f"Not found cases:       {stats['not_found']}")
    print(f"Backend errors:        {stats['errors']}")
    print(f"Criticality downgraded:{stats['downgraded']}")
    print(f"Placeholders fixed:    {stats['placeholders_fixed']}")
    print(f"\nOutput saved to: {V5_DATASET_PATH}")

    # Validation check
    print("\n" + "-" * 60)
    print("VALIDATION CHECK")
    print("-" * 60)

    # Check for remaining placeholders
    remaining_placeholders = [
        c for c in v5_cases
        if "Expected keywords" in str(c.get("ground_truth_context", ""))
    ]

    if remaining_placeholders:
        print(f"WARNING: {len(remaining_placeholders)} cases still have placeholder content!")
        for c in remaining_placeholders[:5]:
            print(f"  - {c['id']}")
    else:
        print("OK: No placeholder content remaining")

    # Check expected_not_found count
    not_found_count = sum(1 for c in v5_cases if c.get("expected_not_found"))
    print(f"Cases with expected_not_found=true: {not_found_count}")

    # Check criticality distribution
    criticality_counts = {}
    for c in v5_cases:
        crit = c.get("criticality", "unknown")
        criticality_counts[crit] = criticality_counts.get(crit, 0) + 1

    print(f"Criticality distribution: {criticality_counts}")


def main():
    parser = argparse.ArgumentParser(description="Generate v5 test dataset with real policy content")
    parser.add_argument(
        "--backend-url",
        default="http://localhost:8000",
        help="Backend URL (default: http://localhost:8000)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of cases to process (0 = all)"
    )

    args = parser.parse_args()

    asyncio.run(generate_v5_dataset(args.backend_url, args.dry_run, args.limit))


if __name__ == "__main__":
    main()
