#!/usr/bin/env python3
"""
Integrate realistic staff questions into the testing infrastructure.

This script:
1. Merges realistic questions into DeepEval test dataset
2. Creates weekly evaluation query format
3. Generates pre-prod test batches by category

Usage:
    # Merge into DeepEval dataset
    python scripts/integrate_realistic_questions.py --merge-deepeval

    # Create weekly eval queries
    python scripts/integrate_realistic_questions.py --weekly-eval --sample 50

    # Generate category-specific test batches
    python scripts/integrate_realistic_questions.py --category emergency_codes

    # Export all formats
    python scripts/integrate_realistic_questions.py --export-all
"""

import json
import argparse
import random
from pathlib import Path
from datetime import datetime
from typing import Optional

# Paths
BASE_DIR = Path(__file__).parent.parent
BACKEND_DATA = BASE_DIR / "apps" / "backend" / "data"
REALISTIC_QUESTIONS_FILE = BACKEND_DATA / "realistic_staff_questions.json"
DEEPEVAL_DATASET_FILE = BACKEND_DATA / "deepeval_test_dataset.json"
WEEKLY_EVAL_QUERIES_FILE = BACKEND_DATA / "weekly_eval_queries.json"
PREPROD_TEST_FILE = BACKEND_DATA / "preprod_realistic_tests.json"


def load_realistic_questions() -> dict:
    """Load the realistic staff questions dataset."""
    with open(REALISTIC_QUESTIONS_FILE) as f:
        return json.load(f)


def load_deepeval_dataset() -> dict:
    """Load the existing DeepEval dataset."""
    with open(DEEPEVAL_DATASET_FILE) as f:
        return json.load(f)


def merge_into_deepeval(output_file: Optional[Path] = None) -> dict:
    """
    Merge realistic questions into DeepEval format.

    Maps realistic question categories to DeepEval categories:
    - retrieval_accuracy: General retrieval tests
    - negation_handling: Questions with "NOT", "cannot", "should not"
    - safety_critical: Verbatim requirements (phone numbers, dosing, timeframes)
    - scope_boundary: Scope of practice questions
    """
    realistic = load_realistic_questions()
    deepeval = load_deepeval_dataset()

    # Map test_type to DeepEval categories
    category_mapping = {
        "retrieval_accuracy": "retrieval_accuracy",
        "negation_handling": "negation_handling",
        "safety_critical": "safety_critical",
        "scope_boundary": "entity_filtering",  # Map to entity filtering
        "procedural_steps": "retrieval_accuracy"
    }

    # Convert realistic questions to DeepEval format
    new_test_cases = []
    for tc in realistic["test_cases"]:
        deepeval_category = category_mapping.get(tc["test_type"], "retrieval_accuracy")

        deepeval_case = {
            "id": tc["id"],
            "category": deepeval_category,
            "criticality": tc["criticality"],
            "query": tc["query"],
            "notes": tc["notes"]
        }

        # Add expected_source if present
        if "expected_source" in tc:
            deepeval_case["expected_source"] = tc["expected_source"]

        # Add expected_keywords if present
        if "expected_keywords" in tc:
            deepeval_case["expected_keywords"] = tc["expected_keywords"]

        # Add negation focus for negation tests
        if tc["test_type"] == "negation_handling":
            deepeval_case["expected_behavior"] = f"Must correctly handle negation: {tc.get('negation_focus', 'NOT')}"
            deepeval_case["negation_focus"] = tc.get("negation_focus", "NOT")

        # Add expected format for safety critical
        if tc.get("expected_format"):
            deepeval_case["expected_format"] = tc["expected_format"]

        new_test_cases.append(deepeval_case)

    # Create merged dataset
    merged = {
        "metadata": {
            "version": "2.0",
            "created": datetime.now().strftime("%Y-%m-%d"),
            "description": "Combined DeepEval test dataset with realistic staff questions",
            "original_cases": len(deepeval["test_cases"]),
            "realistic_cases": len(new_test_cases),
            "total_cases": len(deepeval["test_cases"]) + len(new_test_cases),
            "categories": {
                "retrieval_accuracy": sum(1 for tc in new_test_cases if tc["category"] == "retrieval_accuracy"),
                "negation_handling": sum(1 for tc in new_test_cases if tc["category"] == "negation_handling"),
                "safety_critical": sum(1 for tc in new_test_cases if tc["category"] == "safety_critical"),
                "entity_filtering": sum(1 for tc in new_test_cases if tc["category"] == "entity_filtering"),
                "hallucination_prevention": len([tc for tc in deepeval["test_cases"] if tc["category"] == "hallucination_prevention"]),
                "risen_compliance": len([tc for tc in deepeval["test_cases"] if tc["category"] == "risen_compliance"])
            }
        },
        "test_cases": deepeval["test_cases"] + new_test_cases
    }

    # Write output
    output = output_file or BACKEND_DATA / "deepeval_test_dataset_v2.json"
    with open(output, "w") as f:
        json.dump(merged, f, indent=2)

    print(f"✅ Merged dataset written to: {output}")
    print(f"   Original cases: {len(deepeval['test_cases'])}")
    print(f"   Realistic cases: {len(new_test_cases)}")
    print(f"   Total cases: {len(merged['test_cases'])}")

    return merged


def create_weekly_eval_queries(sample_size: int = 50, seed: Optional[int] = None) -> dict:
    """
    Create weekly evaluation queries from realistic questions.

    Format matches what weekly_eval.py expects:
    [{"query": "...", "timestamp": "..."}]

    Stratified sampling ensures coverage across all categories.
    """
    realistic = load_realistic_questions()
    test_cases = realistic["test_cases"]

    if seed:
        random.seed(seed)

    # Group by category for stratified sampling
    categories = {}
    for tc in test_cases:
        cat = tc["category"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(tc)

    # Calculate samples per category (proportional)
    total = len(test_cases)
    samples_per_cat = {}
    remaining = sample_size

    for cat, cases in categories.items():
        proportion = len(cases) / total
        n = max(1, int(sample_size * proportion))
        samples_per_cat[cat] = min(n, len(cases))
        remaining -= samples_per_cat[cat]

    # Distribute remaining slots to largest categories
    if remaining > 0:
        sorted_cats = sorted(categories.keys(), key=lambda c: len(categories[c]), reverse=True)
        for cat in sorted_cats:
            if remaining <= 0:
                break
            if samples_per_cat[cat] < len(categories[cat]):
                samples_per_cat[cat] += 1
                remaining -= 1

    # Sample from each category
    selected = []
    timestamp = datetime.now().isoformat()

    for cat, n in samples_per_cat.items():
        sampled = random.sample(categories[cat], n)
        for tc in sampled:
            selected.append({
                "query": tc["query"],
                "category": tc["category"],
                "criticality": tc["criticality"],
                "test_id": tc["id"],
                "timestamp": timestamp
            })

    # Shuffle final list
    random.shuffle(selected)

    # Create output
    output = {
        "metadata": {
            "created": timestamp,
            "sample_size": len(selected),
            "seed": seed,
            "source": "realistic_staff_questions.json",
            "category_distribution": samples_per_cat
        },
        "queries": selected
    }

    with open(WEEKLY_EVAL_QUERIES_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"✅ Weekly eval queries written to: {WEEKLY_EVAL_QUERIES_FILE}")
    print(f"   Sample size: {len(selected)}")
    print(f"   Category distribution:")
    for cat, n in sorted(samples_per_cat.items()):
        print(f"     - {cat}: {n}")

    return output


def create_category_batch(category: str) -> dict:
    """Create a test batch for a specific category."""
    realistic = load_realistic_questions()

    # Filter by category
    category_tests = [
        tc for tc in realistic["test_cases"]
        if tc["category"] == category
    ]

    if not category_tests:
        print(f"❌ No tests found for category: {category}")
        print(f"   Available categories: {list(realistic['metadata']['categories'].keys())}")
        return {}

    output = {
        "metadata": {
            "created": datetime.now().strftime("%Y-%m-%d"),
            "category": category,
            "total_cases": len(category_tests)
        },
        "test_cases": category_tests
    }

    output_file = BACKEND_DATA / f"test_batch_{category}.json"
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2)

    print(f"✅ Category batch written to: {output_file}")
    print(f"   Tests: {len(category_tests)}")

    return output


def create_preprod_tests() -> dict:
    """
    Create comprehensive pre-prod test dataset.

    Includes:
    - All 100 realistic questions
    - Criticality-based test ordering (critical first)
    - Expected results structure for automated validation
    """
    realistic = load_realistic_questions()

    # Sort by criticality: critical > high > medium > low
    criticality_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    sorted_tests = sorted(
        realistic["test_cases"],
        key=lambda tc: criticality_order.get(tc["criticality"], 4)
    )

    # Create validation-ready format
    preprod_tests = {
        "metadata": {
            "version": "1.0",
            "created": datetime.now().strftime("%Y-%m-%d"),
            "description": "Pre-production test dataset from realistic staff questions",
            "total_cases": len(sorted_tests),
            "criticality_distribution": {
                "critical": sum(1 for tc in sorted_tests if tc["criticality"] == "critical"),
                "high": sum(1 for tc in sorted_tests if tc["criticality"] == "high"),
                "medium": sum(1 for tc in sorted_tests if tc["criticality"] == "medium"),
                "low": sum(1 for tc in sorted_tests if tc["criticality"] == "low")
            },
            "test_type_distribution": realistic["metadata"]["test_type_distribution"]
        },
        "test_cases": []
    }

    for tc in sorted_tests:
        test_case = {
            "id": tc["id"],
            "query": tc["query"],
            "category": tc["category"],
            "criticality": tc["criticality"],
            "test_type": tc["test_type"],
            "validation": {
                "expected_source": tc.get("expected_source"),
                "expected_keywords": tc.get("expected_keywords", []),
                "expected_format": tc.get("expected_format"),
                "negation_focus": tc.get("negation_focus")
            },
            "notes": tc.get("notes", "")
        }
        preprod_tests["test_cases"].append(test_case)

    with open(PREPROD_TEST_FILE, "w") as f:
        json.dump(preprod_tests, f, indent=2)

    print(f"✅ Pre-prod tests written to: {PREPROD_TEST_FILE}")
    print(f"   Total: {len(sorted_tests)}")
    print(f"   Critical: {preprod_tests['metadata']['criticality_distribution']['critical']}")
    print(f"   High: {preprod_tests['metadata']['criticality_distribution']['high']}")

    return preprod_tests


def export_all():
    """Export all test formats."""
    print("=" * 60)
    print("Exporting all test formats from realistic staff questions")
    print("=" * 60)
    print()

    print("1. Merging into DeepEval format...")
    merge_into_deepeval()
    print()

    print("2. Creating weekly eval queries (50 samples)...")
    create_weekly_eval_queries(sample_size=50)
    print()

    print("3. Creating pre-prod test dataset...")
    create_preprod_tests()
    print()

    print("4. Creating category-specific batches...")
    realistic = load_realistic_questions()
    for category in realistic["metadata"]["categories"].keys():
        create_category_batch(category)
    print()

    print("=" * 60)
    print("All exports complete!")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Integrate realistic staff questions into testing infrastructure"
    )
    parser.add_argument(
        "--merge-deepeval",
        action="store_true",
        help="Merge into DeepEval test dataset"
    )
    parser.add_argument(
        "--weekly-eval",
        action="store_true",
        help="Create weekly evaluation queries"
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=50,
        help="Sample size for weekly eval (default: 50)"
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="Random seed for reproducible sampling"
    )
    parser.add_argument(
        "--category",
        type=str,
        help="Create test batch for specific category"
    )
    parser.add_argument(
        "--preprod",
        action="store_true",
        help="Create pre-prod test dataset"
    )
    parser.add_argument(
        "--export-all",
        action="store_true",
        help="Export all formats"
    )
    parser.add_argument(
        "--list-categories",
        action="store_true",
        help="List available categories"
    )

    args = parser.parse_args()

    if args.list_categories:
        realistic = load_realistic_questions()
        print("Available categories:")
        for cat, count in realistic["metadata"]["categories"].items():
            print(f"  - {cat}: {count} tests")
        return

    if args.export_all:
        export_all()
        return

    if args.merge_deepeval:
        merge_into_deepeval()

    if args.weekly_eval:
        create_weekly_eval_queries(sample_size=args.sample, seed=args.seed)

    if args.category:
        create_category_batch(args.category)

    if args.preprod:
        create_preprod_tests()

    if not any([args.merge_deepeval, args.weekly_eval, args.category, args.preprod, args.export_all, args.list_categories]):
        parser.print_help()


if __name__ == "__main__":
    main()
