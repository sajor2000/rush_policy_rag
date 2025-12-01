#!/usr/bin/env python3
"""
Run test dataset against the PolicyTech API and evaluate results.

Usage:
    python scripts/run_test_dataset.py                    # Run all tests
    python scripts/run_test_dataset.py --category general # Run specific category
    python scripts/run_test_dataset.py --id gen-001       # Run single test
    python scripts/run_test_dataset.py --verbose          # Show full responses
"""

import ssl_fix  # Corporate proxy SSL fix - must be first import!

import json
import sys
import argparse
import requests
import time
from pathlib import Path
from typing import Optional
import re

# Configuration
API_URL = "http://localhost:8000/api/chat"
TEST_DATA_PATH = Path(__file__).parent.parent / "apps/backend/data/test_dataset.json"

# ANSI colors for terminal output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"
BOLD = "\033[1m"


def load_test_data() -> dict:
    """Load test dataset from JSON file."""
    with open(TEST_DATA_PATH) as f:
        return json.load(f)


def call_api(question: str) -> dict:
    """Call the PolicyTech API with a question."""
    try:
        response = requests.post(
            API_URL,
            json={"message": question},
            headers={"Content-Type": "application/json"},
            timeout=90
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": str(e), "response": "", "found": False}


def extract_ref_from_response(response_text: str) -> Optional[str]:
    """Extract reference number from response text."""
    # Pattern: Ref #XXX, Ref: XXX, Reference Number: XXX
    patterns = [
        r'Ref\s*[#:]?\s*(\d+)',
        r'Reference\s*(?:Number)?[:#]?\s*(\d+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, response_text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def evaluate_result(test_case: dict, api_response: dict, verbose: bool = False) -> dict:
    """Evaluate API response against expected results."""
    result = {
        "id": test_case["id"],
        "category": test_case["category"],
        "question": test_case["question"][:60] + "..." if len(test_case["question"]) > 60 else test_case["question"],
        "expected_ref": test_case.get("reference_number", "N/A"),
        "passed": False,
        "details": [],
    }

    response_text = api_response.get("summary", "") or api_response.get("response", "")
    found = api_response.get("found", False)
    error = api_response.get("error")

    if error:
        result["details"].append(f"API Error: {error}")
        return result

    # For "not_found" category - expect the system to decline
    if test_case["category"] == "not_found":
        # Check for decline patterns
        decline_patterns = [
            "could not find",
            "couldn't find",
            "not in rush policies",
            "not found",
            "please verify",
            "contact human resources",
            "contact policy administration",
        ]
        declined = any(p in response_text.lower() for p in decline_patterns)

        # Check for hallucination (citing a fake policy)
        actual_ref = extract_ref_from_response(response_text)
        hallucinated = actual_ref is not None and not found

        if declined and not hallucinated:
            result["passed"] = True
            result["details"].append("Correctly declined (no hallucination)")
        elif hallucinated:
            result["passed"] = False
            result["details"].append(f"HALLUCINATED: Cited Ref #{actual_ref} when no policy exists")
        else:
            result["passed"] = False
            result["details"].append(f"Should have declined but gave answer: {response_text[:100]}...")

        result["actual_response"] = response_text[:200] if verbose else ""
        return result

    # For adversarial category - check appropriate handling
    if test_case["category"] == "adversarial":
        # Some adversarial tests expect a policy answer, others expect refusal
        if test_case["reference_number"] == "N/A":
            # Expect refusal - either direct refusal or policy-based refusal
            refusal_patterns = [
                "i only answer",
                "cannot provide",
                "can't provide",
                "cannot help",
                "can't help",
                "cannot advise",
                "can't advise",
                "could not find",
                "couldn't find",  # Contraction variant
                "no exceptions",   # Policy-based refusal (no way to bypass)
                "strictly requires",  # Policy enforcement language
                "must be",         # Policy requirement language
                "not permitted",   # Policy restriction
                "i'm not able",    # Alternative refusal
                "i am not able",
                "outside my scope",
            ]
            refused = any(p in response_text.lower() for p in refusal_patterns)
            if refused:
                result["passed"] = True
                result["details"].append("Correctly refused inappropriate request")
            else:
                result["passed"] = False
                result["details"].append("Should have refused but didn't")
        else:
            # Expect answer with appropriate framing (won't help bypass but cites policy)
            expected_ref = test_case["reference_number"]
            actual_ref = extract_ref_from_response(response_text)
            if actual_ref == expected_ref:
                result["passed"] = True
                result["details"].append(f"Correctly cited Ref #{expected_ref}")
            else:
                result["passed"] = False
                result["details"].append(f"Expected Ref #{expected_ref}, got {actual_ref or 'none'}")

        result["actual_response"] = response_text[:200] if verbose else ""
        return result

    # For regular tests - check if correct policy was cited
    expected_ref = test_case.get("reference_number", "")
    acceptable_refs = test_case.get("acceptable_refs", [expected_ref] if expected_ref else [])
    actual_ref = extract_ref_from_response(response_text)

    # Also check evidence for reference numbers
    evidence = api_response.get("evidence", [])
    evidence_refs = [e.get("reference_number", "") for e in evidence if e.get("reference_number")]

    # Check if any acceptable ref is in response or evidence
    # For multi-policy tests, any of the acceptable refs is valid
    ref_found = False
    matched_ref = None
    
    for acceptable in acceptable_refs:
        if (actual_ref == acceptable or
            acceptable in evidence_refs or
            (acceptable and acceptable in response_text)):
            ref_found = True
            matched_ref = acceptable
            break

    if ref_found and found:
        result["passed"] = True
        if len(acceptable_refs) > 1:
            result["details"].append(f"Correctly cited Ref #{matched_ref} (one of {len(acceptable_refs)} acceptable)")
        else:
            result["details"].append(f"Correctly cited Ref #{expected_ref}")
    elif not found:
        result["passed"] = False
        result["details"].append(f"Expected to find Ref #{expected_ref} but found=False")
    else:
        result["passed"] = False
        if len(acceptable_refs) > 1:
            result["details"].append(f"Expected one of Refs {acceptable_refs}, got {actual_ref or 'none'}")
        else:
            result["details"].append(f"Expected Ref #{expected_ref}, got {actual_ref or 'none'}")

    result["actual_ref"] = actual_ref
    result["actual_response"] = response_text[:200] if verbose else ""

    return result


def run_tests(
    test_cases: list,
    category: Optional[str] = None,
    test_id: Optional[str] = None,
    verbose: bool = False
) -> list:
    """Run test cases and return results."""
    results = []

    # Filter test cases if needed
    if test_id:
        test_cases = [t for t in test_cases if t["id"] == test_id]
    elif category:
        test_cases = [t for t in test_cases if t["category"] == category]

    total = len(test_cases)
    print(f"\n{BOLD}Running {total} test(s)...{RESET}\n")
    print("-" * 80)

    for i, test_case in enumerate(test_cases, 1):
        print(f"\n[{i}/{total}] {BLUE}{test_case['id']}{RESET} ({test_case['category']})")
        print(f"  Q: {test_case['question'][:70]}{'...' if len(test_case['question']) > 70 else ''}")

        # Call API
        start_time = time.time()
        api_response = call_api(test_case["question"])
        elapsed = time.time() - start_time

        # Evaluate
        result = evaluate_result(test_case, api_response, verbose)
        result["elapsed_seconds"] = round(elapsed, 2)
        results.append(result)

        # Print result
        if result["passed"]:
            status = f"{GREEN}PASS{RESET}"
        else:
            status = f"{RED}FAIL{RESET}"

        print(f"  {status} ({elapsed:.1f}s) - {'; '.join(result['details'])}")

        if verbose and result.get("actual_response"):
            print(f"  Response: {result['actual_response']}")

        # Small delay to avoid rate limiting
        time.sleep(0.5)

    return results


def print_summary(results: list):
    """Print test summary."""
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed

    print("\n" + "=" * 80)
    print(f"{BOLD}TEST SUMMARY{RESET}")
    print("=" * 80)

    # By category
    categories = {}
    for r in results:
        cat = r["category"]
        if cat not in categories:
            categories[cat] = {"passed": 0, "total": 0}
        categories[cat]["total"] += 1
        if r["passed"]:
            categories[cat]["passed"] += 1

    print(f"\n{BOLD}By Category:{RESET}")
    for cat, stats in sorted(categories.items()):
        pct = (stats["passed"] / stats["total"]) * 100 if stats["total"] > 0 else 0
        color = GREEN if pct >= 80 else YELLOW if pct >= 50 else RED
        print(f"  {cat:15} {color}{stats['passed']}/{stats['total']} ({pct:.0f}%){RESET}")

    # Overall
    pct = (passed / total) * 100 if total > 0 else 0
    color = GREEN if pct >= 80 else YELLOW if pct >= 50 else RED
    print(f"\n{BOLD}Overall: {color}{passed}/{total} ({pct:.1f}%){RESET}")

    # List failures
    failures = [r for r in results if not r["passed"]]
    if failures:
        print(f"\n{BOLD}Failed Tests:{RESET}")
        for f in failures:
            print(f"  {RED}{f['id']}{RESET}: {f['question']}")
            for detail in f["details"]:
                print(f"    - {detail}")

    print()


def main():
    parser = argparse.ArgumentParser(description="Run PolicyTech test dataset")
    parser.add_argument("--category", "-c", help="Filter by category")
    parser.add_argument("--id", "-i", help="Run single test by ID")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show full responses")
    parser.add_argument("--output", "-o", help="Save results to JSON file")
    args = parser.parse_args()

    # Load test data
    try:
        test_data = load_test_data()
    except FileNotFoundError:
        print(f"{RED}Error: Test dataset not found at {TEST_DATA_PATH}{RESET}")
        sys.exit(1)

    print(f"{BOLD}PolicyTech Test Runner{RESET}")
    print(f"Dataset: {test_data['description']}")
    print(f"Total cases: {test_data['total_cases']}")

    # Run tests
    results = run_tests(
        test_data["test_cases"],
        category=args.category,
        test_id=args.id,
        verbose=args.verbose
    )

    # Print summary
    print_summary(results)

    # Save results if requested
    if args.output:
        output_path = Path(args.output)
        with open(output_path, 'w') as f:
            json.dump({
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "total_tests": len(results),
                "passed": sum(1 for r in results if r["passed"]),
                "results": results
            }, f, indent=2)
        print(f"Results saved to: {output_path}")

    # Exit with appropriate code
    failures = sum(1 for r in results if not r["passed"])
    sys.exit(1 if failures > 0 else 0)


if __name__ == "__main__":
    main()
