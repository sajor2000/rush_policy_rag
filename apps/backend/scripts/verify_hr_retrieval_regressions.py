#!/usr/bin/env python3
"""
Run scripted HR regression checks against /api/chat.

Validates the key failure set for:
- Multi-page retrieval (Page 2+ evidence)
- Policy-number lookup (including malformed variants)
- Partial title lookup without punctuation
- Safety non-regression (no BLOCKED_UNVERIFIED_FACTS on policy lookups)

Usage:
  python apps/backend/scripts/verify_hr_retrieval_regressions.py
  python apps/backend/scripts/verify_hr_retrieval_regressions.py --base-url http://localhost:8000
  python apps/backend/scripts/verify_hr_retrieval_regressions.py --strict-policy-number
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx


@dataclass
class VerificationCase:
    case_id: str
    query: str
    expected_policy_number: str
    expected_title_hint: str
    min_page: Optional[int] = None
    forbid_unverified_block: bool = False


CASES: List[VerificationCase] = [
    VerificationCase(
        case_id="BUG-001-A",
        query="What is 6.02 in HR-C 06.00?",
        expected_policy_number="HR-C 06.00",
        expected_title_hint="Time and Attendance",
        min_page=2,
    ),
    VerificationCase(
        case_id="BUG-001-B",
        query="What is 6.03 Supervisor Entry/Editing?",
        expected_policy_number="HR-C 06.00",
        expected_title_hint="Time and Attendance",
        min_page=2,
    ),
    VerificationCase(
        case_id="BUG-001-C",
        query="What are sections 3.04 and 3.05?",
        expected_policy_number="HR-E 03.00",
        expected_title_hint="Disciplinary Procedures",
        min_page=2,
    ),
    VerificationCase(
        case_id="BUG-001-D",
        query="What are the levels of warning with or without suspension?",
        expected_policy_number="HR-E 03.00",
        expected_title_hint="Disciplinary Procedures",
        min_page=2,
    ),
    VerificationCase(
        case_id="BUG-002-A",
        query="Can you summarize HR-C 05.00?",
        expected_policy_number="HR-C 05.00",
        expected_title_hint="Shift Differentials",
        forbid_unverified_block=True,
    ),
    VerificationCase(
        case_id="BUG-002-B",
        query="What is HR-C 06.00?",
        expected_policy_number="HR-C 06.00",
        expected_title_hint="Time and Attendance",
        forbid_unverified_block=True,
    ),
    VerificationCase(
        case_id="BUG-002-C",
        query="Summarize HR-E 03.00",
        expected_policy_number="HR-E 03.00",
        expected_title_hint="Disciplinary Procedures",
        forbid_unverified_block=True,
    ),
    VerificationCase(
        case_id="BUG-002-D",
        query="What is HR-C 0.600 Time and Attendance Recording?",
        expected_policy_number="HR-C 06.00",
        expected_title_hint="Time and Attendance",
        forbid_unverified_block=True,
    ),
    VerificationCase(
        case_id="BUG-003-A",
        query="Can you summarize Shift Differentials?",
        expected_policy_number="HR-C 05.00",
        expected_title_hint="Shift Differentials",
    ),
    VerificationCase(
        case_id="BUG-003-B",
        query="What's the policy on Shift Differentials?",
        expected_policy_number="HR-C 05.00",
        expected_title_hint="Shift Differentials",
    ),
]


def normalize_policy_number(value: str) -> str:
    """Normalize policy code variants into canonical XX-X NN.NN where possible."""
    if not value:
        return ""
    match = re.search(r"\b([A-Za-z]{2})\s*-\s*([A-Za-z])\s*(\d{1,2})(?:\.(\d{1,4}))?\b", value)
    if not match:
        return value.strip().upper()

    prefix = match.group(1).upper()
    letter = match.group(2).upper()
    major = match.group(3)
    minor = match.group(4)

    if minor is None:
        return f"{prefix}-{letter} {major.zfill(2)}.00"
    if len(minor) == 3 and major == "0":
        digits = f"{major}{minor}".zfill(4)[:4]
        return f"{prefix}-{letter} {digits[:2]}.{digits[2:]}"
    minor2 = (minor + "0")[:2] if len(minor) == 1 else minor[:2]
    return f"{prefix}-{letter} {major.zfill(2)}.{minor2}"


def _normalize_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "title": str(item.get("title") or ""),
        "reference_number": str(item.get("reference_number") or ""),
        "policy_number": normalize_policy_number(str(item.get("policy_number") or "")),
        "page_number": item.get("page_number"),
    }


def _collect_candidates(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    evidence = payload.get("evidence") or []
    sources = payload.get("sources") or []
    return [_normalize_item(item) for item in evidence + sources if isinstance(item, dict)]


def _matches_case(item: Dict[str, Any], case: VerificationCase, strict_policy_number: bool) -> bool:
    expected_policy = normalize_policy_number(case.expected_policy_number)
    title = item.get("title", "").lower()

    if item.get("policy_number") == expected_policy:
        return True

    if not strict_policy_number:
        if normalize_policy_number(item.get("reference_number", "")) == expected_policy:
            return True
        if case.expected_title_hint.lower() in title:
            return True

    return False


def run_case(
    client: httpx.Client,
    base_url: str,
    case: VerificationCase,
    strict_policy_number: bool,
) -> tuple[bool, str]:
    try:
        response = client.post(f"{base_url}/api/chat", json={"message": case.query})
    except Exception as exc:
        return False, f"request failed: {exc}"

    if response.status_code != 200:
        return False, f"HTTP {response.status_code}: {response.text[:200]}"

    payload = response.json()
    candidates = _collect_candidates(payload)
    matching = [item for item in candidates if _matches_case(item, case, strict_policy_number)]

    if not matching:
        return False, "no matching policy evidence/sources"

    if case.min_page is not None:
        has_page = any(
            isinstance(item.get("page_number"), int) and item["page_number"] >= case.min_page
            for item in matching
        )
        if not has_page:
            return False, f"no matching evidence with page >= {case.min_page}"

    if case.forbid_unverified_block:
        safety_flags = payload.get("safety_flags") or []
        summary = str(payload.get("summary") or "")
        if "BLOCKED_UNVERIFIED_FACTS" in safety_flags or "Unable to verify factual accuracy" in summary:
            return False, "response blocked by unverified-facts safety gate"

    found = payload.get("found")
    if found is False:
        return False, "response marked not found"

    return True, "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run HR retrieval regression checks")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Backend URL")
    parser.add_argument("--timeout", type=float, default=90.0, help="Request timeout seconds")
    parser.add_argument(
        "--strict-policy-number",
        action="store_true",
        help="Require policy_number metadata exact-match (no title/reference fallback)",
    )
    args = parser.parse_args()

    print("=" * 88)
    print(f"HR RETRIEVAL REGRESSION CHECKS: {args.base_url}")
    print("=" * 88)

    passed = 0
    failed = 0

    with httpx.Client(timeout=args.timeout) as client:
        for case in CASES:
            ok, detail = run_case(
                client=client,
                base_url=args.base_url,
                case=case,
                strict_policy_number=args.strict_policy_number,
            )
            status = "PASS" if ok else "FAIL"
            print(f"[{status}] {case.case_id} | {case.query}")
            if detail != "ok":
                print(f"       {detail}")
            if ok:
                passed += 1
            else:
                failed += 1

    print("-" * 88)
    print(f"RESULT: {passed} passed / {failed} failed / {len(CASES)} total")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
