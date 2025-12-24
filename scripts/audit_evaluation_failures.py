#!/usr/bin/env python3
"""
Audit Evaluation Failures - Classify failures as EVALUATOR vs RAG issues.

This script analyzes evaluation results and classifies each failure as:
- EVALUATOR_ISSUE: Response is semantically correct but fails rigid string matching
- RAG_ISSUE: Actual retrieval, ranking, or response generation problem
- TEST_CASE_ISSUE: Test expectations are unrealistic
- RATE_LIMIT_ERROR: HTTP 429 - not a real failure

Usage:
    python scripts/audit_evaluation_failures.py
    python scripts/audit_evaluation_failures.py --results enhanced_evaluation_results.json
"""

import json
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Any, Optional, Tuple
from enum import Enum


class FailureClassification(Enum):
    EVALUATOR_ISSUE = "evaluator_issue"
    RAG_ISSUE = "rag_issue"
    TEST_CASE_ISSUE = "test_case_issue"
    RATE_LIMIT_ERROR = "rate_limit_error"
    API_ERROR = "api_error"


# Synonym groups - if response contains ANY of these, it's semantically equivalent
NEGATION_SYNONYMS = {
    "not allowed": ["not permitted", "prohibited", "forbidden", "cannot", "may not", "must not"],
    "not permitted": ["not allowed", "prohibited", "forbidden", "cannot", "may not"],
    "cannot": ["can not", "can't", "unable to", "not able to", "not authorized", "may not"],
    "does not": ["do not", "doesn't", "don't", "will not", "won't"],
    "does not respond": ["do not respond", "doesn't respond", "does not go", "will not respond"],
    "does NOT": ["does not", "do not", "will not", "doesn't"],
    "are not": ["is not", "aren't", "isn't", "not authorized", "cannot"],
    "not authorized": ["not permitted", "cannot", "may not", "are not allowed"],
    "required": ["must", "mandatory", "necessary", "need to", "shall"],
    "limited": ["restricted", "only", "specific", "designated"],
    "emergent": ["emergency", "urgent", "critical", "immediate"],
    "emergencies": ["emergency situations", "emergency", "emergent", "urgent situations"],
    "does not state": ["does not say", "policy does not", "not in the policy", "not true"],
    "fasting": ["NPO", "nothing by mouth", "no food", "nil per os"],
    "renal": ["kidney", "dialysis", "ESRD", "nephrology"],
    "indwelling": ["foley", "catheter", "urinary", "bladder"],
    "resonance": ["MR", "MRI", "magnetic"],
    "imaging": ["MRI", "scan", "radiology"],
}

# Phone number format equivalences
PHONE_EQUIVALENCES = {
    "312-942-5111": ["2-5111", "x5111", "5111", "942-5111"],
}

# Entity name expansions
ENTITY_EXPANSIONS = {
    "Rush University Medical Center": ["RUMC", "Rush", "medical center"],
    "Rush Copley": ["RCMC", "Rush Copley Medical Center", "Copley"],
    "Rush Medical Group": ["RMG", "medical group"],
    "Oak Park": ["ROPH", "Rush Oak Park", "Rush Oak Park Hospital"],
}


@dataclass
class FailureAnalysis:
    """Analysis of a single test failure."""
    test_id: str
    category: str
    query: str
    classification: FailureClassification
    reason: str
    response_snippet: str
    failures: List[str]
    suggested_fix: str


def normalize_phone(phone: str) -> str:
    """Extract digits only from phone number."""
    return re.sub(r'\D', '', phone)


def has_semantic_match(response: str, expected: str) -> bool:
    """Check if response contains a semantic equivalent of expected."""
    response_lower = response.lower()
    expected_lower = expected.lower()

    # Direct match
    if expected_lower in response_lower:
        return True

    # Check synonyms
    for base_term, synonyms in NEGATION_SYNONYMS.items():
        if expected_lower == base_term or expected_lower in synonyms:
            # Check if ANY synonym is in the response
            all_terms = [base_term] + synonyms
            if any(term.lower() in response_lower for term in all_terms):
                return True

    return False


def has_phone_match(response: str, expected_phone: str) -> bool:
    """Check if response contains equivalent phone number."""
    expected_digits = normalize_phone(expected_phone)
    response_digits = normalize_phone(response)

    # Check for exact match
    if expected_digits in response_digits:
        return True

    # Check for extension format (last 4-5 digits)
    if len(expected_digits) >= 4:
        if expected_digits[-4:] in response_digits or expected_digits[-5:] in response_digits:
            return True

    # Check known equivalences
    for full_number, short_forms in PHONE_EQUIVALENCES.items():
        if expected_phone in [full_number] + short_forms:
            for form in [full_number] + short_forms:
                if normalize_phone(form)[-4:] in response_digits:
                    return True

    return False


def has_entity_match(response: str, expected_entity: str) -> bool:
    """Check if response contains equivalent entity name."""
    response_lower = response.lower()
    expected_lower = expected_entity.lower()

    if expected_lower in response_lower:
        return True

    for full_name, abbreviations in ENTITY_EXPANSIONS.items():
        if expected_lower in [full_name.lower()] + [a.lower() for a in abbreviations]:
            all_forms = [full_name.lower()] + [a.lower() for a in abbreviations]
            if any(form in response_lower for form in all_forms):
                return True

    return False


def classify_failure(result: Dict[str, Any], test_case: Dict[str, Any] = None) -> FailureAnalysis:
    """Classify a single test failure."""
    test_id = result["test_id"]
    category = result["category"]
    query = result["query"]
    response = result["response"]
    failures = result.get("failures", [])

    # Check for rate limit / API errors first
    if "Error: HTTP 429" in response:
        return FailureAnalysis(
            test_id=test_id,
            category=category,
            query=query,
            classification=FailureClassification.RATE_LIMIT_ERROR,
            reason="Rate limited - not a real failure",
            response_snippet=response[:100],
            failures=failures,
            suggested_fix="Re-run test with rate limiting delay"
        )

    if "Error:" in response and result.get("result") == "error":
        return FailureAnalysis(
            test_id=test_id,
            category=category,
            query=query,
            classification=FailureClassification.API_ERROR,
            reason=f"API error: {response[:100]}",
            response_snippet=response[:100],
            failures=failures,
            suggested_fix="Check backend connectivity and retry"
        )

    # Check for retrieval failure (RAG issue)
    if "could not find" in response.lower() and result.get("expected_ref"):
        return FailureAnalysis(
            test_id=test_id,
            category=category,
            query=query,
            classification=FailureClassification.RAG_ISSUE,
            reason=f"Retrieval failure - expected Ref #{result['expected_ref']} but got nothing",
            response_snippet=response[:200],
            failures=failures,
            suggested_fix="Check synonym expansion and search query construction"
        )

    # Analyze each failure reason
    evaluator_issues = []
    rag_issues = []
    test_issues = []

    for failure in failures:
        # Missing content failures
        if "Missing expected content" in failure:
            # Extract the expected terms
            match = re.search(r"\[([^\]]+)\]", failure)
            if match:
                expected_terms = [t.strip().strip("'\"") for t in match.group(1).split(",")]

                # Check if response has semantic equivalents
                semantic_matches = []
                for term in expected_terms:
                    if has_semantic_match(response, term):
                        semantic_matches.append(term)
                    elif has_phone_match(response, term):
                        semantic_matches.append(term)
                    elif has_entity_match(response, term):
                        semantic_matches.append(term)

                if semantic_matches:
                    evaluator_issues.append(f"Synonym match found: {semantic_matches}")
                else:
                    # Check if it's a content accuracy issue vs test expectation issue
                    if any(term in ["cannot", "not", "NOT"] for term in expected_terms):
                        # Response might be correct but phrased differently
                        if any(neg in response.lower() for neg in ["not", "no", "cannot", "don't", "doesn't", "prohibited"]):
                            evaluator_issues.append(f"Response contains negation but different phrasing")
                        else:
                            rag_issues.append(f"Missing negation in response")
                    else:
                        rag_issues.append(f"Missing content: {expected_terms}")

        # Forbidden content failures
        elif "Contains forbidden content" in failure:
            match = re.search(r"\[([^\]]+)\]", failure)
            if match:
                forbidden_terms = [t.strip().strip("'\"") for t in match.group(1).split(",")]
                # Check if the forbidden content is actually problematic
                if "Applies To:" in forbidden_terms:
                    test_issues.append("Overly strict: 'Applies To:' in citation is normal")
                elif "authorized to take" in forbidden_terms:
                    # Check if response is actually saying "NOT authorized"
                    if "not authorized" in response.lower():
                        evaluator_issues.append("False positive: 'not authorized' contains 'authorized'")
                    else:
                        rag_issues.append(f"Forbidden content: {forbidden_terms}")
                else:
                    rag_issues.append(f"Forbidden content: {forbidden_terms}")

        # Reference mismatch
        elif "Expected Ref #" in failure:
            expected_ref = result.get("expected_ref")
            actual_ref = result.get("actual_ref")
            if actual_ref and expected_ref:
                # Check if related policy was found
                rag_issues.append(f"Wrong policy: expected #{expected_ref}, got #{actual_ref}")
            else:
                rag_issues.append(f"Retrieval failure: expected #{expected_ref}")

        # Response type failures
        elif "Expected response type" in failure:
            expected_type = failure.split("'")[1] if "'" in failure else "unknown"
            if expected_type == "safety_refusal":
                # Check if response is refusing in a different way
                refusal_phrases = ["only answer", "cannot", "will not", "policy questions", "outside my scope"]
                if any(phrase in response.lower() for phrase in refusal_phrases):
                    evaluator_issues.append("Response is refusing but detection pattern didn't match")
                else:
                    rag_issues.append("Should have refused but provided answer")
            else:
                rag_issues.append(f"Wrong response type: expected {expected_type}")

        # Missing citation
        elif "Missing required citation" in failure:
            rag_issues.append("No citation when expected")

        else:
            # Unknown failure type
            rag_issues.append(f"Unknown: {failure}")

    # Determine overall classification
    if evaluator_issues and not rag_issues:
        return FailureAnalysis(
            test_id=test_id,
            category=category,
            query=query,
            classification=FailureClassification.EVALUATOR_ISSUE,
            reason="; ".join(evaluator_issues),
            response_snippet=response[:300],
            failures=failures,
            suggested_fix="Add synonym matching to evaluator"
        )
    elif test_issues and not rag_issues:
        return FailureAnalysis(
            test_id=test_id,
            category=category,
            query=query,
            classification=FailureClassification.TEST_CASE_ISSUE,
            reason="; ".join(test_issues),
            response_snippet=response[:300],
            failures=failures,
            suggested_fix="Update test case expectations"
        )
    elif evaluator_issues and rag_issues:
        # Mixed - could be either, favor evaluator if semantic match found
        if any("Synonym match" in issue for issue in evaluator_issues):
            return FailureAnalysis(
                test_id=test_id,
                category=category,
                query=query,
                classification=FailureClassification.EVALUATOR_ISSUE,
                reason="; ".join(evaluator_issues + rag_issues),
                response_snippet=response[:300],
                failures=failures,
                suggested_fix="Add synonym matching; also review RAG for: " + "; ".join(rag_issues)
            )

    # Default to RAG issue
    return FailureAnalysis(
        test_id=test_id,
        category=category,
        query=query,
        classification=FailureClassification.RAG_ISSUE,
        reason="; ".join(rag_issues) if rag_issues else "Unknown issue",
        response_snippet=response[:300],
        failures=failures,
        suggested_fix="Review RAG pipeline: " + "; ".join(rag_issues) if rag_issues else "Manual review needed"
    )


def load_results(path: str) -> Dict[str, Any]:
    """Load evaluation results from JSON file."""
    with open(path, "r") as f:
        return json.load(f)


def generate_audit_report(analyses: List[FailureAnalysis]) -> Dict[str, Any]:
    """Generate comprehensive audit report."""
    total = len(analyses)

    by_classification = {}
    for analysis in analyses:
        class_name = analysis.classification.value
        if class_name not in by_classification:
            by_classification[class_name] = []
        by_classification[class_name].append(analysis)

    report = {
        "summary": {
            "total_failures_analyzed": total,
            "evaluator_issues": len(by_classification.get("evaluator_issue", [])),
            "rag_issues": len(by_classification.get("rag_issue", [])),
            "test_case_issues": len(by_classification.get("test_case_issue", [])),
            "rate_limit_errors": len(by_classification.get("rate_limit_error", [])),
            "api_errors": len(by_classification.get("api_error", [])),
        },
        "evaluator_issues": [
            {
                "test_id": a.test_id,
                "category": a.category,
                "query": a.query[:80],
                "reason": a.reason,
                "suggested_fix": a.suggested_fix,
            }
            for a in by_classification.get("evaluator_issue", [])
        ],
        "rag_issues": [
            {
                "test_id": a.test_id,
                "category": a.category,
                "query": a.query[:80],
                "reason": a.reason,
                "response_snippet": a.response_snippet[:150],
                "suggested_fix": a.suggested_fix,
            }
            for a in by_classification.get("rag_issue", [])
        ],
        "test_case_issues": [
            {
                "test_id": a.test_id,
                "category": a.category,
                "reason": a.reason,
                "suggested_fix": a.suggested_fix,
            }
            for a in by_classification.get("test_case_issue", [])
        ],
        "rate_limit_errors": [a.test_id for a in by_classification.get("rate_limit_error", [])],
    }

    # Calculate true failure rates
    actual_failures = len(by_classification.get("rag_issue", []))
    false_failures = len(by_classification.get("evaluator_issue", [])) + len(by_classification.get("test_case_issue", []))
    not_run = len(by_classification.get("rate_limit_error", [])) + len(by_classification.get("api_error", []))

    report["analysis"] = {
        "actual_rag_failures": actual_failures,
        "false_failures_due_to_evaluator": false_failures,
        "tests_not_run_due_to_errors": not_run,
        "true_failure_rate_estimate": f"{actual_failures / (total - not_run) * 100:.1f}%" if (total - not_run) > 0 else "N/A",
        "false_positive_rate": f"{false_failures / (total - not_run) * 100:.1f}%" if (total - not_run) > 0 else "N/A",
    }

    return report


def print_audit_report(report: Dict[str, Any]) -> None:
    """Print audit report to console."""
    print("\n" + "="*80)
    print("EVALUATION FAILURE AUDIT REPORT")
    print("="*80)

    summary = report["summary"]
    print(f"\n{'FAILURE CLASSIFICATION SUMMARY':^80}")
    print("-"*80)
    print(f"  Total Failures Analyzed:    {summary['total_failures_analyzed']}")
    print(f"  Evaluator Issues:           {summary['evaluator_issues']} (false positives - eval too strict)")
    print(f"  RAG Issues:                 {summary['rag_issues']} (actual pipeline problems)")
    print(f"  Test Case Issues:           {summary['test_case_issues']} (unrealistic expectations)")
    print(f"  Rate Limit Errors:          {summary['rate_limit_errors']} (need re-run)")
    print(f"  API Errors:                 {summary['api_errors']} (connectivity issues)")

    analysis = report["analysis"]
    print(f"\n{'TRUE PERFORMANCE ANALYSIS':^80}")
    print("-"*80)
    print(f"  Actual RAG Failures:        {analysis['actual_rag_failures']}")
    print(f"  False Failures (Evaluator): {analysis['false_failures_due_to_evaluator']}")
    print(f"  Tests Not Run (Errors):     {analysis['tests_not_run_due_to_errors']}")
    print(f"  True Failure Rate:          {analysis['true_failure_rate_estimate']}")
    print(f"  False Positive Rate:        {analysis['false_positive_rate']}")

    # Evaluator issues
    if report["evaluator_issues"]:
        print(f"\n{'EVALUATOR ISSUES (False Positives)':^80}")
        print("-"*80)
        for issue in report["evaluator_issues"][:10]:  # Show top 10
            print(f"\n  [{issue['test_id']}] {issue['query'][:60]}...")
            print(f"    Reason: {issue['reason'][:70]}")
            print(f"    Fix: {issue['suggested_fix']}")

    # RAG issues
    if report["rag_issues"]:
        print(f"\n{'RAG PIPELINE ISSUES (Actual Problems)':^80}")
        print("-"*80)
        for issue in report["rag_issues"][:10]:  # Show top 10
            print(f"\n  [{issue['test_id']}] {issue['query'][:60]}...")
            print(f"    Reason: {issue['reason'][:70]}")
            print(f"    Response: {issue['response_snippet'][:80]}...")
            print(f"    Fix: {issue['suggested_fix'][:70]}")

    # Test case issues
    if report["test_case_issues"]:
        print(f"\n{'TEST CASE ISSUES (Bad Expectations)':^80}")
        print("-"*80)
        for issue in report["test_case_issues"]:
            print(f"  [{issue['test_id']}] {issue['reason']}")

    # Rate limit errors
    if report["rate_limit_errors"]:
        print(f"\n{'RATE LIMIT ERRORS (Need Re-run)':^80}")
        print("-"*80)
        print(f"  Tests: {', '.join(report['rate_limit_errors'][:20])}")
        if len(report["rate_limit_errors"]) > 20:
            print(f"  ... and {len(report['rate_limit_errors']) - 20} more")

    print("\n" + "="*80)

    # Recommendations
    print(f"\n{'RECOMMENDATIONS':^80}")
    print("-"*80)

    if summary["evaluator_issues"] > 0:
        print(f"\n  1. FIX EVALUATOR ({summary['evaluator_issues']} false positives):")
        print("     - Add synonym matching to check_contains()")
        print("     - Add phone number normalization")
        print("     - Add entity name expansion")

    if summary["rag_issues"] > 0:
        print(f"\n  2. FIX RAG PIPELINE ({summary['rag_issues']} actual issues):")
        print("     - Review retrieval for failed queries")
        print("     - Check Cohere rerank min_score threshold")
        print("     - Verify adversarial detection patterns")

    if summary["rate_limit_errors"] > 0:
        print(f"\n  3. RE-RUN TESTS ({summary['rate_limit_errors']} rate limited):")
        print("     - Add delay between test batches")
        print("     - Or run in smaller batches")

    print("\n" + "="*80)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Audit evaluation failures")
    parser.add_argument(
        "--results",
        default="enhanced_evaluation_results.json",
        help="Path to evaluation results JSON file"
    )
    parser.add_argument(
        "--output",
        default="audit_report.json",
        help="Output path for audit report"
    )
    args = parser.parse_args()

    # Load results
    print(f"Loading results from {args.results}...")
    data = load_results(args.results)

    # Get failed/error results
    all_results = data.get("results", [])
    failed_results = [r for r in all_results if r.get("result") in ["fail", "error"]]

    print(f"Found {len(failed_results)} failures/errors out of {len(all_results)} total tests")

    # Classify each failure
    analyses = [classify_failure(r) for r in failed_results]

    # Generate report
    report = generate_audit_report(analyses)

    # Save report
    with open(args.output, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Audit report saved to {args.output}")

    # Print to console
    print_audit_report(report)


if __name__ == "__main__":
    main()
