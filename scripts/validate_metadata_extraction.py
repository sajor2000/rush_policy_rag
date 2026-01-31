#!/usr/bin/env python3
"""
Validate metadata extraction from sample PDFs.

Tests what fields can be reliably extracted:
- page_number (Docling + PyMuPDF fallback)
- reference_number (regex patterns)
- source_file (filename)
- category, subcategory (if available in PDF)
- regulatory_citations (if available)
- All other metadata fields

Usage:
    python scripts/validate_metadata_extraction.py
"""

import sys
from pathlib import Path

# Add backend to path
script_dir = Path(__file__).parent
backend_path = script_dir.parent / "apps" / "backend"
sys.path.insert(0, str(backend_path))

from preprocessing.chunker import PolicyChunker
from dotenv import load_dotenv

# Load .env from root
root_dir = script_dir.parent
load_dotenv(root_dir / ".env")

def analyze_pdf(pdf_path: str):
    """Process a PDF and report all extracted metadata."""
    chunker = PolicyChunker(max_chunk_size=1500)
    result = chunker.process_pdf_with_status(pdf_path)

    if result.chunks:
        print(f"\n{'='*70}")
        print(f"FILE: {result.source_file}")
        print(f"{'='*70}")

        # Analyze first chunk for document-level metadata
        chunk = result.chunks[0]
        print(f"Title: {chunk.policy_title or 'NOT FOUND'}")
        print(f"Reference Number: {chunk.reference_number or 'NOT FOUND'}")
        print(f"Source File: {chunk.source_file or 'NOT FOUND'}")
        print(f"Document Owner: {chunk.document_owner or 'NOT FOUND'}")
        print(f"Date Approved: {chunk.date_approved or 'NOT FOUND'}")
        print(f"Date Updated: {chunk.date_updated or 'NOT FOUND'}")
        print(f"Applies To: {chunk.applies_to or 'NOT FOUND'}")
        print(f"Category: {chunk.category or 'NOT FOUND'}")
        print(f"Subcategory: {chunk.subcategory or 'NOT FOUND'}")
        print(f"Regulatory Citations: {chunk.regulatory_citations or 'NOT FOUND'}")

        # Analyze page number extraction success
        chunks_with_pages = sum(1 for c in result.chunks if c.page_number)
        total_chunks = len(result.chunks)
        page_success_rate = (chunks_with_pages / total_chunks * 100) if total_chunks > 0 else 0

        print(f"\n{'='*70}")
        print(f"CHUNK ANALYSIS ({total_chunks} chunks)")
        print(f"{'='*70}")
        print(f"Page Numbers: {chunks_with_pages}/{total_chunks} ({page_success_rate:.1f}%)")

        # Show page distribution
        print(f"\nSample chunks with page numbers:")
        for i, chunk in enumerate(result.chunks[:5], 1):
            print(f"  Chunk {i}: Page {chunk.page_number or 'UNKNOWN'} | Section: {chunk.section_title or 'N/A'}")

        print(f"\nEntity Breakdown:")
        print(f"  RUMC: {chunk.applies_to_rumc}")
        print(f"  RUMG: {chunk.applies_to_rumg}")
        print(f"  RMG: {chunk.applies_to_rmg}")

        return {
            "success": True,
            "chunks": total_chunks,
            "page_success_rate": page_success_rate,
            "has_reference": bool(chunk.reference_number),
            "has_title": bool(chunk.policy_title),
            "has_category": bool(chunk.category)
        }
    else:
        print(f"\n❌ No chunks extracted from {pdf_path}")
        return {"success": False}

def main():
    # Test with sample PDFs
    test_pdfs_dir = Path("apps/backend/data/test_pdfs")

    if not test_pdfs_dir.exists():
        print(f"ERROR: Test PDFs directory not found: {test_pdfs_dir}")
        sys.exit(1)

    pdf_files = list(test_pdfs_dir.glob("*.pdf"))[:5]  # Test first 5

    if not pdf_files:
        print("ERROR: No PDF files found")
        sys.exit(1)

    print(f"Testing metadata extraction on {len(pdf_files)} PDFs...")

    results = []
    for pdf_path in pdf_files:
        result = analyze_pdf(str(pdf_path))
        results.append(result)

    # Summary
    print(f"\n{'='*70}")
    print(f"VALIDATION SUMMARY")
    print(f"{'='*70}")
    successful = sum(1 for r in results if r["success"])
    print(f"Successfully processed: {successful}/{len(pdf_files)}")

    if successful > 0:
        avg_page_rate = sum(r.get("page_success_rate", 0) for r in results if r["success"]) / successful
        has_ref = sum(1 for r in results if r.get("has_reference"))
        has_title = sum(1 for r in results if r.get("has_title"))
        has_category = sum(1 for r in results if r.get("has_category"))

        print(f"\nMetadata Extraction Rates:")
        print(f"  Page Numbers: {avg_page_rate:.1f}% average")
        print(f"  Reference Number: {has_ref}/{successful} files")
        print(f"  Title: {has_title}/{successful} files")
        print(f"  Category: {has_category}/{successful} files")

        print(f"\n✅ Metadata extraction validation complete!")
        print(f"\nRecommendation: Proceed with API implementation if:")
        print(f"  - Page extraction > 80%")
        print(f"  - Reference numbers found in most files")
        print(f"  - Titles extracted correctly")

if __name__ == "__main__":
    main()
