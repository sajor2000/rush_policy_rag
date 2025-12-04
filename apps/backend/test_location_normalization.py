#!/usr/bin/env python3
"""
Test script to verify location context normalization behavior.
Identifies bugs and validates fixes.
"""
import re
from typing import List, Optional

# Copy the patterns from chat_service.py
LOCATION_CONTEXT_PATTERNS: List[str] = [
    r'\s*\bin\s+(?:a\s+)?patient\s+room(?:s)?\b',           # "in a patient room"
    r'\s*\bat\s+the\s+bedside\b',                           # "at the bedside"
    r'\s*\bduring\s+(?:a\s+)?(?:procedure|visit)\b',        # "during a procedure"
    r'\s*\bon\s+the\s+(?:floor|unit|ward)\b',               # "on the floor/unit"
    r'\s*\bin\s+(?:the\s+)?(?:clinical|hospital)\s+setting\b',  # "in the clinical setting"
    r'\s*\bwhen\s+caring\s+for\s+(?:a\s+)?patient\b',       # "when caring for a patient" (NOT bare "when a patient")
    r'\s*\bwhile\s+(?:treating|seeing)\s+(?:a\s+)?patient\b', # "while treating a patient"
]

def normalize_location_context_current(query: str) -> tuple[str, Optional[str]]:
    """Current implementation from chat_service.py"""
    original = query
    extracted = []

    for pattern in LOCATION_CONTEXT_PATTERNS:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            extracted.append(match.group().strip())
            query = re.sub(pattern, '', query, flags=re.IGNORECASE)

    # Clean up extra whitespace
    query = ' '.join(query.split())

    # Remove leading/trailing punctuation and spaces (handles leftover commas)
    query = query.strip(' ,;:')

    # Remove space before punctuation (e.g., "policy ?" -> "policy?")
    query = re.sub(r'\s+([?!.,;:])', r'\1', query)

    # Ensure space after punctuation (except at end)
    query = re.sub(r'([?!.,;:])(\S)', r'\1 \2', query)

    if extracted:
        context = ', '.join(extracted)
        return query, context

    return query, None

def normalize_location_context_fixed(query: str) -> tuple[str, Optional[str]]:
    """Fixed implementation with proper punctuation handling"""
    original = query
    extracted = []

    for pattern in LOCATION_CONTEXT_PATTERNS:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            extracted.append(match.group().strip())
            query = re.sub(pattern, '', query, flags=re.IGNORECASE)

    # Clean up extra whitespace
    query = ' '.join(query.split())

    # Remove leading/trailing punctuation and spaces
    query = query.strip(' ,;:')

    # Fix spacing: remove space before punctuation
    query = re.sub(r'\s+([?!.,;:])', r'\1', query)

    # Fix spacing: ensure space after punctuation (except at end)
    query = re.sub(r'([?!.,;:])(\S)', r'\1 \2', query)

    if extracted:
        context = ', '.join(extracted)
        return query, context

    return query, None

# Test cases covering edge cases
test_cases = [
    # Known issue: comma at start
    ("In a patient room, what is the hand hygiene policy?",
     "what is the hand hygiene policy?"),

    # Location at end
    ("What is the hand hygiene policy in a patient room?",
     "What is the hand hygiene policy?"),

    # Multiple locations
    ("What is the policy at the bedside during a procedure?",
     "What is the policy?"),

    # Location in middle
    ("Can I use my phone in a patient room for work?",
     "Can I use my phone for work?"),

    # Possessive form
    ("What is the patient room's cleaning policy?",
     "What is the patient room's cleaning policy?"),  # Should NOT strip "patient room's"

    # Plural with "for" - NOT stripped (could be specific scope, not just location)
    ("What is the policy for patient rooms?",
     "What is the policy for patient rooms?"),

    # With comma and no space
    ("At the bedside,what should I do?",
     "what should I do?"),

    # Multiple commas
    ("In a patient room, at the bedside, what is the protocol?",
     "what is the protocol?"),

    # No location context
    ("What is the HIPAA policy?",
     "What is the HIPAA policy?"),

    # Department codes should NOT be stripped
    ("What is the ED policy for triage?",
     "What is the ED policy for triage?"),

    # Entity names should NOT be stripped
    ("What is the policy at Oak Park?",
     "What is the policy at Oak Park?"),

    # "on the floor" = location context, but "when a patient" is grammatical subject (NOT stripped)
    ("What do I do when a patient falls on the floor?",
     "What do I do when a patient falls?"),

    # Edge case: only location context
    ("in a patient room",
     ""),  # Edge case: empty query after normalization

    # Unicode and special chars
    ("What is the policy in a patient room?",
     "What is the policy?"),

    # Case variations
    ("In A Patient Room, what is the policy?",
     "what is the policy?"),
]

def run_tests():
    """Run all test cases and report bugs"""
    print("=" * 80)
    print("LOCATION CONTEXT NORMALIZATION - BUG REPORT")
    print("=" * 80)

    bugs_found = []

    for i, (input_query, expected) in enumerate(test_cases, 1):
        current_result, current_context = normalize_location_context_current(input_query)
        fixed_result, fixed_context = normalize_location_context_fixed(input_query)

        # Check if current implementation has bug
        has_bug = current_result != expected

        print(f"\nTest {i}:")
        print(f"  Input:    '{input_query}'")
        print(f"  Expected: '{expected}'")
        print(f"  Current:  '{current_result}' {' ❌ BUG' if has_bug else ' ✓'}")
        if current_context:
            print(f"  Context:  '{current_context}'")

        if has_bug:
            print(f"  Fixed:    '{fixed_result}' {' ✓' if fixed_result == expected else ' ❌ STILL BROKEN'}")
            bugs_found.append({
                'test_num': i,
                'input': input_query,
                'expected': expected,
                'current': current_result,
                'fixed': fixed_result
            })

    print("\n" + "=" * 80)
    print(f"SUMMARY: {len(bugs_found)} bugs found out of {len(test_cases)} test cases")
    print("=" * 80)

    if bugs_found:
        print("\nBUGS IDENTIFIED:")
        for bug in bugs_found:
            print(f"\n  Bug #{bug['test_num']}:")
            print(f"    Input:    '{bug['input']}'")
            print(f"    Expected: '{bug['expected']}'")
            print(f"    Current:  '{bug['current']}'")
            print(f"    Fixed:    '{bug['fixed']}'")

    return bugs_found

if __name__ == "__main__":
    bugs = run_tests()
    exit(0 if not bugs else 1)
