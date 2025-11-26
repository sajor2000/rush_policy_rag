#!/usr/bin/env python3
"""
Human Review CLI Tool for RUSH PolicyTech RAG Evaluation.

Provides an interactive interface for human reviewers to:
- View evaluation results
- Mark responses as Correct/Incorrect/Partial
- Add notes for failures
- Export reviewed results

Usage:
    python scripts/review_responses.py                    # Review latest results
    python scripts/review_responses.py --file results.json  # Review specific file
    python scripts/review_responses.py --export reviewed.csv # Export to CSV
    python scripts/review_responses.py --stats            # Show statistics only
"""

import argparse
import json
import csv
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional


class Colors:
    """ANSI color codes for terminal output."""
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'


def print_colored(text: str, color: str = Colors.ENDC) -> None:
    """Print colored text to terminal."""
    print(f"{color}{text}{Colors.ENDC}")


def clear_screen() -> None:
    """Clear terminal screen."""
    print("\033[H\033[J", end="")


def load_evaluation_results(filepath: str) -> Dict[str, Any]:
    """Load evaluation results from JSON file."""
    with open(filepath, "r") as f:
        return json.load(f)


def save_review_results(results: Dict[str, Any], filepath: str) -> None:
    """Save reviewed results to JSON file."""
    with open(filepath, "w") as f:
        json.dump(results, f, indent=2, default=str)


def display_case(case: Dict[str, Any], index: int, total: int) -> None:
    """Display a single evaluation case for review."""
    clear_screen()
    
    print_colored("=" * 70, Colors.HEADER)
    print_colored(f"  CASE {index + 1} of {total}", Colors.BOLD)
    print_colored("=" * 70, Colors.HEADER)
    print()
    
    # Query
    print_colored("📋 QUERY:", Colors.CYAN)
    print(f"   {case.get('query', 'N/A')}")
    print()
    
    # Response
    print_colored("💬 RESPONSE:", Colors.CYAN)
    response = case.get('response', 'N/A')
    # Wrap long responses
    wrapped = [response[i:i+65] for i in range(0, len(response), 65)]
    for line in wrapped[:10]:  # Show first 10 lines
        print(f"   {line}")
    if len(wrapped) > 10:
        print(f"   ... ({len(wrapped) - 10} more lines)")
    print()
    
    # Scores
    print_colored("📊 SCORES:", Colors.CYAN)
    
    # Azure scores
    if any(k in case for k in ['groundedness_score', 'relevance_score']):
        groundedness = case.get('groundedness_score', 'N/A')
        relevance = case.get('relevance_score', 'N/A')
        coherence = case.get('coherence_score', 'N/A')
        retrieval = case.get('retrieval_score', 'N/A')
        
        g_color = Colors.GREEN if float(groundedness or 0) >= 4.0 else Colors.RED
        r_color = Colors.GREEN if float(relevance or 0) >= 3.5 else Colors.RED
        c_color = Colors.GREEN if float(coherence or 0) >= 3.5 else Colors.RED
        ret_color = Colors.GREEN if float(retrieval or 0) >= 0.7 else Colors.RED
        
        print(f"   Azure:  Groundedness={g_color}{groundedness}{Colors.ENDC}  "
              f"Relevance={r_color}{relevance}{Colors.ENDC}  "
              f"Coherence={c_color}{coherence}{Colors.ENDC}  "
              f"Retrieval={ret_color}{retrieval}{Colors.ENDC}")
    
    # RAGAS scores
    if any(k in case for k in ['faithfulness', 'answer_relevancy']):
        faithfulness = case.get('faithfulness', 'N/A')
        relevancy = case.get('answer_relevancy', 'N/A')
        precision = case.get('context_precision', 'N/A')
        recall = case.get('context_recall', 'N/A')
        
        f_color = Colors.GREEN if float(faithfulness or 0) >= 0.8 else Colors.RED
        r_color = Colors.GREEN if float(relevancy or 0) >= 0.7 else Colors.RED
        p_color = Colors.GREEN if float(precision or 0) >= 0.7 else Colors.RED
        rec_color = Colors.GREEN if float(recall or 0) >= 0.7 else Colors.RED
        
        print(f"   RAGAS:  Faithfulness={f_color}{faithfulness}{Colors.ENDC}  "
              f"Relevancy={r_color}{relevancy}{Colors.ENDC}  "
              f"Precision={p_color}{precision}{Colors.ENDC}  "
              f"Recall={rec_color}{recall}{Colors.ENDC}")
    
    # Pass/Fail
    passed = case.get('passed', None)
    if passed is not None:
        status_color = Colors.GREEN if passed else Colors.RED
        status_text = "PASSED" if passed else "FAILED"
        print(f"\n   AI Verdict: {status_color}{status_text}{Colors.ENDC}")
    
    # Existing human review
    if 'human_verdict' in case:
        h_color = Colors.GREEN if case['human_verdict'] == 'correct' else (
            Colors.YELLOW if case['human_verdict'] == 'partial' else Colors.RED
        )
        print(f"   Human Verdict: {h_color}{case['human_verdict'].upper()}{Colors.ENDC}")
        if case.get('human_notes'):
            print(f"   Notes: {case['human_notes']}")
    
    print()


def get_user_verdict() -> tuple[str, str]:
    """Get verdict and notes from user."""
    print_colored("─" * 70, Colors.HEADER)
    print_colored("  REVIEW OPTIONS", Colors.BOLD)
    print_colored("─" * 70, Colors.HEADER)
    print()
    print("   [C] Correct    - Response is accurate and well-sourced")
    print("   [P] Partial    - Mostly correct but has minor issues")
    print("   [I] Incorrect  - Contains errors, hallucinations, or wrong info")
    print("   [S] Skip       - Skip this case for now")
    print("   [Q] Quit       - Save and exit")
    print()
    
    while True:
        choice = input("   Your verdict (C/P/I/S/Q): ").strip().upper()
        
        if choice in ['C', 'P', 'I', 'S', 'Q']:
            break
        print_colored("   Invalid choice. Please enter C, P, I, S, or Q.", Colors.RED)
    
    if choice == 'Q':
        return 'quit', ''
    if choice == 'S':
        return 'skip', ''
    
    verdict_map = {'C': 'correct', 'P': 'partial', 'I': 'incorrect'}
    verdict = verdict_map[choice]
    
    notes = ""
    if choice in ['P', 'I']:
        notes = input("   Add notes (optional): ").strip()
    
    return verdict, notes


def review_cases(results: Dict[str, Any]) -> Dict[str, Any]:
    """Interactive review loop for evaluation cases."""
    # Combine cases from both evaluators
    azure_results = results.get('azure', {}).get('results', [])
    ragas_results = results.get('ragas', {}).get('results', [])
    
    # Merge by query
    cases_by_query = {}
    for case in azure_results:
        q = case.get('query', '')
        if q not in cases_by_query:
            cases_by_query[q] = {}
        cases_by_query[q].update(case)
    
    for case in ragas_results:
        q = case.get('query', '')
        if q not in cases_by_query:
            cases_by_query[q] = {}
        cases_by_query[q].update(case)
    
    cases = list(cases_by_query.values())
    
    if not cases:
        print_colored("No cases to review.", Colors.YELLOW)
        return results
    
    # Track reviews
    if 'human_reviews' not in results:
        results['human_reviews'] = {}
    
    reviewed_count = 0
    i = 0
    
    while i < len(cases):
        case = cases[i]
        display_case(case, i, len(cases))
        
        verdict, notes = get_user_verdict()
        
        if verdict == 'quit':
            break
        elif verdict == 'skip':
            i += 1
            continue
        else:
            # Save review
            query = case.get('query', f'case_{i}')
            results['human_reviews'][query] = {
                'verdict': verdict,
                'notes': notes,
                'reviewed_at': datetime.now().isoformat(),
                'case_index': i
            }
            # Also add to case for display
            case['human_verdict'] = verdict
            case['human_notes'] = notes
            reviewed_count += 1
            i += 1
    
    print()
    print_colored(f"Reviewed {reviewed_count} cases.", Colors.GREEN)
    
    return results


def show_statistics(results: Dict[str, Any]) -> None:
    """Display evaluation and review statistics."""
    clear_screen()
    
    print_colored("=" * 70, Colors.HEADER)
    print_colored("  EVALUATION STATISTICS", Colors.BOLD)
    print_colored("=" * 70, Colors.HEADER)
    print()
    
    # Azure stats
    if 'azure' in results and 'report' in results['azure']:
        report = results['azure']['report']
        print_colored("📊 AZURE AI FOUNDRY", Colors.CYAN)
        print(f"   Total Cases: {report['summary']['total_cases']}")
        print(f"   Pass Rate: {report['summary']['pass_rate']}")
        print(f"   Avg Groundedness: {report['average_scores']['groundedness']}/5")
        print(f"   Avg Relevance: {report['average_scores']['relevance']}/5")
        print(f"   Failed Cases: {report['summary']['failed']}")
        print()
    
    # RAGAS stats
    if 'ragas' in results and 'report' in results['ragas']:
        report = results['ragas']['report']
        print_colored("📊 RAGAS", Colors.CYAN)
        print(f"   Total Cases: {report['summary']['total_cases']}")
        print(f"   Pass Rate: {report['summary']['pass_rate']}")
        print(f"   Overall Score: {report['summary']['overall_score']}")
        print(f"   Avg Faithfulness: {report['average_scores']['faithfulness']}")
        print(f"   Hallucination Risk Cases: {report.get('problem_areas', {}).get('hallucination_risk', 0)}")
        print()
    
    # Human review stats
    if 'human_reviews' in results:
        reviews = results['human_reviews']
        correct = sum(1 for r in reviews.values() if r['verdict'] == 'correct')
        partial = sum(1 for r in reviews.values() if r['verdict'] == 'partial')
        incorrect = sum(1 for r in reviews.values() if r['verdict'] == 'incorrect')
        
        print_colored("👤 HUMAN REVIEW", Colors.CYAN)
        print(f"   Total Reviewed: {len(reviews)}")
        print(f"   Correct: {correct} ({correct/len(reviews)*100:.1f}%)" if reviews else "")
        print(f"   Partial: {partial} ({partial/len(reviews)*100:.1f}%)" if reviews else "")
        print(f"   Incorrect: {incorrect} ({incorrect/len(reviews)*100:.1f}%)" if reviews else "")
        
        # Agreement with AI
        if reviews:
            azure_results = {r.get('query'): r for r in results.get('azure', {}).get('results', [])}
            agreements = 0
            for query, review in reviews.items():
                ai_passed = azure_results.get(query, {}).get('passed', None)
                human_correct = review['verdict'] == 'correct'
                if ai_passed is not None and ai_passed == human_correct:
                    agreements += 1
            
            print(f"\n   AI-Human Agreement: {agreements}/{len(reviews)} ({agreements/len(reviews)*100:.1f}%)")
    
    print()


def export_to_csv(results: Dict[str, Any], output_path: str) -> None:
    """Export all results including human reviews to CSV."""
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        
        headers = [
            "query", "response", "ai_passed",
            "groundedness", "relevance", "faithfulness",
            "human_verdict", "human_notes", "reviewed_at"
        ]
        writer.writerow(headers)
        
        azure_results = {r.get('query'): r for r in results.get('azure', {}).get('results', [])}
        ragas_results = {r.get('query'): r for r in results.get('ragas', {}).get('results', [])}
        human_reviews = results.get('human_reviews', {})
        
        all_queries = set(azure_results.keys()) | set(ragas_results.keys())
        
        for query in all_queries:
            azure = azure_results.get(query, {})
            ragas = ragas_results.get(query, {})
            human = human_reviews.get(query, {})
            
            row = [
                query,
                azure.get('response', ragas.get('response', ''))[:500],
                azure.get('passed', ragas.get('passed', '')),
                azure.get('groundedness_score', ''),
                azure.get('relevance_score', ''),
                ragas.get('faithfulness', ''),
                human.get('verdict', ''),
                human.get('notes', ''),
                human.get('reviewed_at', '')
            ]
            writer.writerow(row)
    
    print_colored(f"Exported to {output_path}", Colors.GREEN)


def main():
    parser = argparse.ArgumentParser(description="Human review of RAG evaluation results")
    parser.add_argument(
        "--file",
        default="evaluation_results.json",
        help="Evaluation results JSON file"
    )
    parser.add_argument(
        "--export",
        help="Export results to CSV file"
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show statistics only"
    )
    parser.add_argument(
        "--failed-only",
        action="store_true",
        help="Review only failed cases"
    )
    
    args = parser.parse_args()
    
    # Load results
    filepath = Path(args.file)
    if not filepath.exists():
        print_colored(f"File not found: {filepath}", Colors.RED)
        print("Run evaluation first: python scripts/run_evaluation.py")
        sys.exit(1)
    
    results = load_evaluation_results(filepath)
    
    # Export mode
    if args.export:
        export_to_csv(results, args.export)
        return
    
    # Stats mode
    if args.stats:
        show_statistics(results)
        return
    
    # Interactive review mode
    print_colored("\n🔍 Starting Human Review Session", Colors.BOLD)
    print_colored("   Review each case and provide your verdict.", Colors.CYAN)
    print("   Press Enter to continue...")
    input()
    
    results = review_cases(results)
    
    # Save reviewed results
    output_path = filepath.with_suffix('.reviewed.json')
    save_review_results(results, output_path)
    print_colored(f"\nReviewed results saved to {output_path}", Colors.GREEN)
    
    # Show final stats
    show_statistics(results)


if __name__ == "__main__":
    main()

