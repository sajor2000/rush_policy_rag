#!/usr/bin/env python3
"""
Post-Indexing RAG Accuracy Test

This script runs after full index ingestion to validate RAG accuracy.
It uses a pre-generated test dataset to evaluate retrieval quality.

Called automatically by full_pipeline_ingest.py with --run-tests flag,
or can be run standalone.

Usage:
    python tests/rag_accuracy/run_post_index_test.py
    python tests/rag_accuracy/run_post_index_test.py --quick  # Quick 10-case test
    python tests/rag_accuracy/run_post_index_test.py --full   # Full 150+ case test
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "apps" / "backend"))

import ssl_fix  # Corporate proxy SSL fix

import argparse
import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

# Load environment
load_dotenv(project_root / ".env")

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Configuration
API_URL = os.getenv("BACKEND_URL", "http://localhost:8000") + "/api/chat"
TEST_DATA_DIR = Path(__file__).parent / "data"
DEFAULT_DATASET = TEST_DATA_DIR / "test_dataset_v3.json"

# Quick test dataset (subset for fast validation)
QUICK_TEST_CASES = [
    {
        "id": "quick-001",
        "question": "Who can accept verbal orders at RUSH?",
        "expected_ref": "486",
        "category": "general"
    },
    {
        "id": "quick-002", 
        "question": "What is the policy for latex allergy?",
        "expected_ref": "228",
        "category": "general"
    },
    {
        "id": "quick-003",
        "question": "How do I call a Rapid Response Team?",
        "expected_ref": "346",
        "category": "general"
    },
    {
        "id": "quick-004",
        "question": "What is SBAR communication?",
        "expected_ref": "1206",
        "category": "general"
    },
    {
        "id": "quick-005",
        "question": "Can a medical assistant accept verbal orders?",
        "expected_ref": "486",
        "category": "edge_case"
    },
    {
        "id": "quick-006",
        "question": "What is the cafeteria menu?",
        "expected_ref": None,
        "category": "not_found"
    },
    {
        "id": "quick-007",
        "question": "Tell me a joke about doctors",
        "expected_ref": None,
        "category": "adversarial"
    },
    {
        "id": "quick-008",
        "question": "What are the requirements for AI at Rush?",
        "expected_ref": "12106",
        "category": "general"
    },
    {
        "id": "quick-009",
        "question": "What is the authentication timeframe for verbal orders?",
        "expected_ref": "486",
        "category": "general"
    },
    {
        "id": "quick-010",
        "question": "Are latex balloons allowed at RUSH?",
        "expected_ref": "228",
        "category": "general"
    },
]

# ANSI colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"
BOLD = "\033[1m"


def call_rag_api(question: str, timeout: int = 90) -> Dict[str, Any]:
    """Call the PolicyTech RAG API."""
    try:
        response = requests.post(
            API_URL,
            json={"message": question},
            headers={"Content-Type": "application/json"},
            timeout=timeout
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"error": str(e), "response": "", "found": False}


def extract_ref_from_response(response_text: str) -> Optional[str]:
    """Extract reference number from response."""
    import re
    patterns = [
        r'Ref\s*[#:]?\s*(\d+)',
        r'Reference\s*(?:Number)?[:#]?\s*(\d+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, response_text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def run_quick_test() -> Dict[str, Any]:
    """Run quick 10-case validation test."""
    logger.info(f"\n{BOLD}Running Quick Post-Index Test (10 cases){RESET}")
    logger.info("=" * 60)
    
    results = []
    passed = 0
    failed = 0
    
    for tc in QUICK_TEST_CASES:
        logger.info(f"\n[{tc['id']}] {tc['question'][:50]}...")
        
        start = time.time()
        response = call_rag_api(tc["question"])
        elapsed = time.time() - start
        
        response_text = response.get("summary", "") or response.get("response", "")
        actual_ref = extract_ref_from_response(response_text)
        expected_ref = tc.get("expected_ref")
        
        # Evaluate
        if tc["category"] in ["not_found", "adversarial"]:
            # Should decline or refuse
            decline_patterns = ["could not find", "cannot", "i only answer", "outside"]
            is_declined = any(p in response_text.lower() for p in decline_patterns)
            test_passed = is_declined or expected_ref is None
        else:
            # Should find correct reference
            test_passed = actual_ref == expected_ref
        
        if test_passed:
            passed += 1
            status = f"{GREEN}PASS{RESET}"
        else:
            failed += 1
            status = f"{RED}FAIL{RESET}"
        
        logger.info(f"  {status} ({elapsed:.1f}s) - Expected: {expected_ref}, Got: {actual_ref}")
        
        results.append({
            "id": tc["id"],
            "question": tc["question"],
            "expected_ref": expected_ref,
            "actual_ref": actual_ref,
            "passed": test_passed,
            "elapsed_seconds": round(elapsed, 2)
        })
        
        time.sleep(0.5)  # Rate limiting
    
    total = len(QUICK_TEST_CASES)
    pass_rate = (passed / total) * 100
    
    logger.info(f"\n{BOLD}Quick Test Results:{RESET}")
    logger.info(f"  Passed: {GREEN}{passed}/{total}{RESET}")
    logger.info(f"  Failed: {RED}{failed}/{total}{RESET}")
    logger.info(f"  Pass Rate: {pass_rate:.1f}%")
    
    return {
        "type": "quick",
        "timestamp": datetime.now().isoformat(),
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": f"{pass_rate:.1f}%",
        "results": results,
        "status": "PASS" if pass_rate >= 80 else "FAIL"
    }


def run_full_test(dataset_path: Optional[str] = None) -> Dict[str, Any]:
    """Run full test dataset evaluation."""
    dataset_file = Path(dataset_path) if dataset_path else DEFAULT_DATASET
    
    if not dataset_file.exists():
        logger.error(f"Dataset not found: {dataset_file}")
        logger.error("Generate dataset first: python scripts/generate_test_dataset_from_pdfs.py")
        return {"error": "Dataset not found", "status": "ERROR"}
    
    logger.info(f"\n{BOLD}Running Full Post-Index Test{RESET}")
    logger.info(f"Dataset: {dataset_file}")
    logger.info("=" * 60)
    
    with open(dataset_file, "r") as f:
        dataset = json.load(f)
    
    test_cases = dataset.get("test_cases", [])
    logger.info(f"Loaded {len(test_cases)} test cases")
    
    results = []
    passed = 0
    failed = 0
    
    for i, tc in enumerate(test_cases):
        question = tc.get("question", "")
        expected_ref = tc.get("reference_number", "")
        category = tc.get("category", "general")
        
        logger.info(f"\n[{i+1}/{len(test_cases)}] {question[:50]}...")
        
        start = time.time()
        response = call_rag_api(question)
        elapsed = time.time() - start
        
        response_text = response.get("summary", "") or response.get("response", "")
        actual_ref = extract_ref_from_response(response_text)
        
        # Evaluate based on category
        if category in ["not_found", "adversarial"]:
            decline_patterns = ["could not find", "cannot", "i only answer", "outside"]
            is_declined = any(p in response_text.lower() for p in decline_patterns)
            test_passed = is_declined or expected_ref in ["", "N/A", None]
        else:
            test_passed = actual_ref == expected_ref
        
        if test_passed:
            passed += 1
            status = f"{GREEN}PASS{RESET}"
        else:
            failed += 1
            status = f"{RED}FAIL{RESET}"
        
        logger.info(f"  {status} ({elapsed:.1f}s)")
        
        results.append({
            "id": tc.get("id", f"case-{i}"),
            "question": question[:100],
            "expected_ref": expected_ref,
            "actual_ref": actual_ref,
            "category": category,
            "passed": test_passed,
            "elapsed_seconds": round(elapsed, 2)
        })
        
        time.sleep(0.5)
    
    total = len(test_cases)
    pass_rate = (passed / total) * 100 if total > 0 else 0
    
    # Category breakdown
    categories = {}
    for r in results:
        cat = r.get("category", "general")
        if cat not in categories:
            categories[cat] = {"passed": 0, "total": 0}
        categories[cat]["total"] += 1
        if r["passed"]:
            categories[cat]["passed"] += 1
    
    logger.info(f"\n{BOLD}Full Test Results:{RESET}")
    logger.info(f"  Total: {total}")
    logger.info(f"  Passed: {GREEN}{passed}{RESET}")
    logger.info(f"  Failed: {RED}{failed}{RESET}")
    logger.info(f"  Pass Rate: {pass_rate:.1f}%")
    
    logger.info(f"\n{BOLD}By Category:{RESET}")
    for cat, stats in sorted(categories.items()):
        cat_rate = (stats["passed"] / stats["total"]) * 100 if stats["total"] > 0 else 0
        logger.info(f"  {cat}: {stats['passed']}/{stats['total']} ({cat_rate:.1f}%)")
    
    return {
        "type": "full",
        "timestamp": datetime.now().isoformat(),
        "dataset": str(dataset_file),
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": f"{pass_rate:.1f}%",
        "categories": categories,
        "results": results[:50],  # Limit stored results
        "status": "PASS" if pass_rate >= 80 else "FAIL"
    }


def main():
    parser = argparse.ArgumentParser(description="Post-indexing RAG accuracy test")
    parser.add_argument("--quick", action="store_true", help="Run quick 10-case test (default)")
    parser.add_argument("--full", action="store_true", help="Run full dataset test")
    parser.add_argument("--dataset", type=str, help="Path to test dataset JSON")
    parser.add_argument("--output", "-o", type=str, help="Save results to JSON file")
    
    args = parser.parse_args()
    
    # Default to quick test
    if args.full:
        results = run_full_test(args.dataset)
    else:
        results = run_quick_test()
    
    # Save results
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(results, f, indent=2)
        logger.info(f"\nResults saved to: {output_path}")
    
    # Exit code based on pass rate
    status = results.get("status", "FAIL")
    if status == "PASS":
        logger.info(f"\n{GREEN}{BOLD}TEST PASSED{RESET}")
        sys.exit(0)
    elif status == "ERROR":
        logger.error(f"\n{RED}{BOLD}TEST ERROR{RESET}")
        sys.exit(2)
    else:
        logger.warning(f"\n{RED}{BOLD}TEST FAILED{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
