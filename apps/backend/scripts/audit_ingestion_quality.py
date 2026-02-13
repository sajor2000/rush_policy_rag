#!/usr/bin/env python3
"""
Comprehensive Ingestion Quality Audit Script

Audits the quality and completeness of the Docling parsing + Azure AI Search
ingestion pipeline by checking:

1. METADATA EXTRACTION: title, reference_number, applies_to, date_updated, document_owner
2. ENTITY BOOLEANS: applies_to_rumc, applies_to_rumg, etc.
3. PAGE NUMBERS: Are page numbers being extracted?
4. CHUNKING: Are chunks at appropriate levels (semantic, section, document)?
5. CONTENT QUALITY: Is content being preserved with reasonable lengths?
6. AZURE SEARCH INDEX: Total chunks, unique files, searchability
7. SEARCH RETRIEVAL: Can we find content via search?

Usage:
    python scripts/audit_ingestion_quality.py
    python scripts/audit_ingestion_quality.py --sample 20  # Audit 20 random files
    python scripts/audit_ingestion_quality.py --verbose    # Show detailed per-file audit
"""

import os
import sys
import json
import random
import argparse
from pathlib import Path
from typing import Dict, List, Any, Set, Optional
from dataclasses import dataclass, field

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(env_path)

from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from app.core.security import escape_odata_string
from app.core.index_safety import resolve_index_name, ensure_safe_index_target


@dataclass
class FileAudit:
    """Audit results for a single file."""
    filename: str
    chunk_count: int = 0
    has_title: bool = False
    has_reference_number: bool = False
    has_applies_to: bool = False
    has_date_updated: bool = False
    has_document_owner: bool = False
    entity_count: int = 0
    entities: List[str] = field(default_factory=list)
    page_numbers: List[int] = field(default_factory=list)
    chunk_levels: Dict[str, int] = field(default_factory=dict)
    content_lengths: List[int] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)


@dataclass
class AuditReport:
    """Complete audit report."""
    total_chunks: int = 0
    total_files: int = 0
    files_audited: int = 0

    # Metadata extraction rates
    files_with_title: int = 0
    files_with_reference: int = 0
    files_with_applies_to: int = 0
    files_with_date: int = 0
    files_with_owner: int = 0

    # Entity boolean rates
    files_with_entities: int = 0
    total_entity_associations: int = 0
    entity_distribution: Dict[str, int] = field(default_factory=dict)

    # Page numbers
    files_with_pages: int = 0

    # Chunking
    chunk_level_distribution: Dict[str, int] = field(default_factory=dict)

    # Content quality
    avg_content_length: float = 0
    min_content_length: int = 0
    max_content_length: int = 0

    # Issues
    files_with_issues: int = 0
    common_issues: Dict[str, int] = field(default_factory=dict)

    # File audits
    file_audits: List[FileAudit] = field(default_factory=list)


class IngestionAuditor:
    """Comprehensive auditor for Docling + Azure AI Search ingestion."""

    ENTITY_FIELDS = [
        'applies_to_rumc', 'applies_to_rumg', 'applies_to_rmg',
        'applies_to_roph', 'applies_to_rcmc', 'applies_to_rch',
        'applies_to_roppg', 'applies_to_rcmg', 'applies_to_ru'
    ]

    ENTITY_NAMES = {
        'applies_to_rumc': 'RUMC',
        'applies_to_rumg': 'RUMG',
        'applies_to_rmg': 'RMG',
        'applies_to_roph': 'ROPH',
        'applies_to_rcmc': 'RCMC',
        'applies_to_rch': 'RCH',
        'applies_to_roppg': 'ROPPG',
        'applies_to_rcmg': 'RCMG',
        'applies_to_ru': 'RU'
    }

    def __init__(
        self,
        *,
        endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        index_name: Optional[str] = None,
        allow_direct_index: bool = False,
    ):
        endpoint = endpoint or os.environ.get("SEARCH_ENDPOINT")
        api_key = api_key or os.environ.get("SEARCH_API_KEY")
        resolved_index_name = resolve_index_name(index_name)
        ensure_safe_index_target(
            resolved_index_name,
            allow_direct_index=allow_direct_index,
            operation="read",
        )

        if not endpoint or not api_key:
            raise ValueError("SEARCH_ENDPOINT and SEARCH_API_KEY are required")

        self.client = SearchClient(
            endpoint=endpoint,
            index_name=resolved_index_name,
            credential=AzureKeyCredential(api_key)
        )
        self.endpoint = endpoint
        self.index_name = resolved_index_name

    def get_all_source_files(self) -> Set[str]:
        """Get all unique source files in the index."""
        files = set()
        results = self.client.search('*', top=5000, select='source_file')
        for r in results:
            if r.get('source_file'):
                files.add(r['source_file'])
        return files

    def get_total_chunks(self) -> int:
        """Get total chunk count."""
        results = self.client.search('*', top=0, include_total_count=True)
        return results.get_count()

    def audit_file(self, filename: str) -> FileAudit:
        """Audit a single file's chunks."""
        audit = FileAudit(filename=filename)

        # Escape single quotes for OData filter
        safe_filename = escape_odata_string(filename)

        # Fetch all chunks for this file
        results = list(self.client.search(
            '*',
            filter=f"source_file eq '{safe_filename}'",
            top=500,
            select=','.join([
                'id', 'title', 'reference_number', 'section', 'applies_to',
                'date_updated', 'document_owner', 'page_number', 'chunk_index',
                'chunk_level', 'content'
            ] + self.ENTITY_FIELDS)
        ))

        audit.chunk_count = len(results)

        if not results:
            audit.issues.append("NO_CHUNKS_FOUND")
            return audit

        # Analyze first chunk for metadata (should be consistent across chunks)
        first = results[0]

        # Check metadata
        audit.has_title = bool(first.get('title'))
        audit.has_reference_number = bool(first.get('reference_number'))
        audit.has_applies_to = bool(first.get('applies_to'))
        audit.has_date_updated = bool(first.get('date_updated'))
        audit.has_document_owner = bool(first.get('document_owner'))

        # Check entity booleans
        for field in self.ENTITY_FIELDS:
            if first.get(field):
                audit.entities.append(self.ENTITY_NAMES[field])
        audit.entity_count = len(audit.entities)

        # Check page numbers across all chunks
        pages = set()
        for r in results:
            if r.get('page_number') is not None:
                pages.add(r['page_number'])
        audit.page_numbers = sorted(pages)

        # Check chunk levels
        for r in results:
            level = r.get('chunk_level', 'unknown')
            audit.chunk_levels[level] = audit.chunk_levels.get(level, 0) + 1

        # Check content lengths
        audit.content_lengths = [len(r.get('content', '')) for r in results]

        # Identify issues
        if not audit.has_title:
            audit.issues.append("MISSING_TITLE")
        if not audit.has_applies_to:
            audit.issues.append("MISSING_APPLIES_TO")
        if audit.entity_count == 0:
            audit.issues.append("NO_ENTITY_BOOLEANS")
        if not audit.page_numbers:
            audit.issues.append("NO_PAGE_NUMBERS")
        if audit.chunk_count < 2:
            audit.issues.append("VERY_FEW_CHUNKS")
        if audit.content_lengths and min(audit.content_lengths) < 50:
            audit.issues.append("VERY_SHORT_CONTENT")

        return audit

    def run_audit(self, sample_size: int = None, verbose: bool = False) -> AuditReport:
        """Run comprehensive audit."""
        report = AuditReport()

        # Get index stats
        report.total_chunks = self.get_total_chunks()
        all_files = self.get_all_source_files()
        report.total_files = len(all_files)

        # Sample files if requested
        if sample_size and sample_size < len(all_files):
            files_to_audit = random.sample(list(all_files), sample_size)
        else:
            files_to_audit = list(all_files)

        report.files_audited = len(files_to_audit)

        print(f"\n{'='*70}")
        print("INGESTION QUALITY AUDIT")
        print(f"{'='*70}")
        print(f"\n  Total chunks in index: {report.total_chunks:,}")
        print(f"  Total unique files: {report.total_files}")
        print(f"  Files to audit: {report.files_audited}")
        print(f"\n  Auditing files...")

        all_content_lengths = []

        for i, filename in enumerate(sorted(files_to_audit), 1):
            if i % 50 == 0:
                print(f"    Progress: {i}/{report.files_audited}")

            audit = self.audit_file(filename)
            report.file_audits.append(audit)

            if verbose and (audit.issues or i <= 5):
                self._print_file_audit(audit)

            # Aggregate stats
            if audit.has_title:
                report.files_with_title += 1
            if audit.has_reference_number:
                report.files_with_reference += 1
            if audit.has_applies_to:
                report.files_with_applies_to += 1
            if audit.has_date_updated:
                report.files_with_date += 1
            if audit.has_document_owner:
                report.files_with_owner += 1

            if audit.entity_count > 0:
                report.files_with_entities += 1
                report.total_entity_associations += audit.entity_count
                for entity in audit.entities:
                    report.entity_distribution[entity] = \
                        report.entity_distribution.get(entity, 0) + 1

            if audit.page_numbers:
                report.files_with_pages += 1

            for level, count in audit.chunk_levels.items():
                report.chunk_level_distribution[level] = \
                    report.chunk_level_distribution.get(level, 0) + count

            all_content_lengths.extend(audit.content_lengths)

            if audit.issues:
                report.files_with_issues += 1
                for issue in audit.issues:
                    report.common_issues[issue] = \
                        report.common_issues.get(issue, 0) + 1

        # Content length stats
        if all_content_lengths:
            report.avg_content_length = sum(all_content_lengths) / len(all_content_lengths)
            report.min_content_length = min(all_content_lengths)
            report.max_content_length = max(all_content_lengths)

        return report

    def _print_file_audit(self, audit: FileAudit):
        """Print detailed audit for one file."""
        status = "✅" if not audit.issues else "⚠️"
        print(f"\n  {status} {audit.filename}")
        print(f"      Chunks: {audit.chunk_count}")
        print(f"      Title: {'✅' if audit.has_title else '❌'}")
        print(f"      Reference #: {'✅' if audit.has_reference_number else '⚠️'}")
        print(f"      Applies To: {'✅' if audit.has_applies_to else '❌'}")
        print(f"      Date: {'✅' if audit.has_date_updated else '⚠️'}")
        print(f"      Owner: {'✅' if audit.has_document_owner else '⚠️'}")
        print(f"      Entities: {audit.entities if audit.entities else '❌ None'}")
        print(f"      Pages: {audit.page_numbers if audit.page_numbers else '❌ None'}")
        if audit.issues:
            print(f"      Issues: {', '.join(audit.issues)}")


def print_report(report: AuditReport):
    """Print comprehensive audit report."""
    n = report.files_audited
    pct = lambda count: (count / n * 100) if n else 0.0

    print(f"\n{'='*70}")
    print("AUDIT RESULTS")
    print(f"{'='*70}")

    # Metadata extraction rates
    print(f"\n📋 METADATA EXTRACTION QUALITY ({n} files):")
    print(f"  {'Field':<20} {'Found':<10} {'Rate':<10} {'Status'}")
    print(f"  {'-'*50}")

    fields = [
        ("Title", report.files_with_title),
        ("Reference #", report.files_with_reference),
        ("Applies To", report.files_with_applies_to),
        ("Date Updated", report.files_with_date),
        ("Document Owner", report.files_with_owner),
    ]

    for name, count in fields:
        rate = count / n * 100 if n else 0
        status = "✅" if rate >= 80 else "⚠️" if rate >= 50 else "❌"
        print(f"  {name:<20} {count:<10} {rate:>6.1f}%    {status}")

    # Entity booleans
    print(f"\n🏥 ENTITY BOOLEAN EXTRACTION:")
    print(f"  Files with entities: {report.files_with_entities}/{n} ({pct(report.files_with_entities):.1f}%)")
    print(f"  Total associations: {report.total_entity_associations}")
    if n:
        print(f"  Avg entities/file: {report.total_entity_associations/n:.1f}")

    if report.entity_distribution:
        print(f"\n  Entity Distribution:")
        for entity, count in sorted(report.entity_distribution.items(), key=lambda x: -x[1]):
            print(f"    {entity}: {count} files")

    # Page numbers
    print(f"\n📄 PAGE NUMBER EXTRACTION:")
    rate = report.files_with_pages / n * 100 if n else 0
    status = "✅" if rate >= 80 else "⚠️" if rate >= 50 else "❌"
    print(f"  Files with pages: {report.files_with_pages}/{n} ({rate:.1f}%) {status}")

    # Chunking
    print(f"\n📦 CHUNKING QUALITY:")
    print(f"  Chunk level distribution:")
    for level, count in sorted(report.chunk_level_distribution.items()):
        print(f"    {level}: {count:,} chunks")

    print(f"\n  Content length stats:")
    print(f"    Min: {report.min_content_length} chars")
    print(f"    Max: {report.max_content_length} chars")
    print(f"    Avg: {report.avg_content_length:.0f} chars")

    # Issues summary
    print(f"\n⚠️  ISSUES SUMMARY:")
    print(f"  Files with issues: {report.files_with_issues}/{n} ({pct(report.files_with_issues):.1f}%)")

    if report.common_issues:
        print(f"\n  Common issues:")
        for issue, count in sorted(report.common_issues.items(), key=lambda x: -x[1]):
            print(f"    {issue}: {count} files")

    # Overall assessment
    print(f"\n{'='*70}")
    print("OVERALL ASSESSMENT")
    print(f"{'='*70}")

    title_rate = report.files_with_title / n * 100 if n else 0
    applies_rate = report.files_with_applies_to / n * 100 if n else 0
    entity_rate = report.files_with_entities / n * 100 if n else 0
    page_rate = report.files_with_pages / n * 100 if n else 0

    issues = []
    if title_rate < 80:
        issues.append(f"❌ Title extraction rate low ({title_rate:.0f}%)")
    if applies_rate < 80:
        issues.append(f"❌ Applies To extraction rate low ({applies_rate:.0f}%)")
    if entity_rate < 50:
        issues.append(f"❌ Entity boolean extraction rate low ({entity_rate:.0f}%)")
    if page_rate < 50:
        issues.append(f"❌ Page number extraction rate low ({page_rate:.0f}%)")

    if not issues:
        print("\n  ✅ PIPELINE WORKING CORRECTLY")
        print("     All quality metrics within acceptable ranges.")
    else:
        print("\n  ⚠️  PIPELINE ISSUES DETECTED")
        for issue in issues:
            print(f"     {issue}")

    print()


def main():
    parser = argparse.ArgumentParser(
        description="Audit Docling + Azure AI Search ingestion quality"
    )
    parser.add_argument(
        "--sample",
        type=int,
        help="Audit random sample of N files (default: all files)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed per-file audit results"
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Save report to JSON file"
    )
    parser.add_argument(
        "--index-name",
        type=str,
        default=None,
        help="Target search index (defaults to SEARCH_INDEX_NAME or active alias)"
    )
    parser.add_argument(
        "--endpoint",
        type=str,
        default=None,
        help="Azure Search endpoint override (defaults to SEARCH_ENDPOINT)"
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="Azure Search API key override (defaults to SEARCH_API_KEY)"
    )
    parser.add_argument(
        "--allow-direct-index",
        action="store_true",
        help="Compatibility flag for direct index operations (read-only audit)",
    )

    args = parser.parse_args()

    auditor = IngestionAuditor(
        endpoint=args.endpoint,
        api_key=args.api_key,
        index_name=args.index_name,
        allow_direct_index=args.allow_direct_index,
    )
    print(
        f"[INFO] Auditing index='{auditor.index_name}' "
        f"on endpoint='{auditor.endpoint}'"
    )
    report = auditor.run_audit(sample_size=args.sample, verbose=args.verbose)
    print_report(report)

    if args.output:
        # Save to JSON
        output_data = {
            "total_chunks": report.total_chunks,
            "total_files": report.total_files,
            "files_audited": report.files_audited,
            "metadata_rates": {
                "title": report.files_with_title / report.files_audited if report.files_audited else 0,
                "reference": report.files_with_reference / report.files_audited if report.files_audited else 0,
                "applies_to": report.files_with_applies_to / report.files_audited if report.files_audited else 0,
                "date": report.files_with_date / report.files_audited if report.files_audited else 0,
                "owner": report.files_with_owner / report.files_audited if report.files_audited else 0,
            },
            "entity_rates": {
                "files_with_entities": report.files_with_entities / report.files_audited if report.files_audited else 0,
                "distribution": report.entity_distribution,
            },
            "page_number_rate": report.files_with_pages / report.files_audited if report.files_audited else 0,
            "chunk_levels": report.chunk_level_distribution,
            "content_length": {
                "min": report.min_content_length,
                "max": report.max_content_length,
                "avg": report.avg_content_length,
            },
            "issues": report.common_issues,
        }

        with open(args.output, 'w') as f:
            json.dump(output_data, f, indent=2)
        print(f"  Report saved to: {args.output}")


if __name__ == "__main__":
    main()
