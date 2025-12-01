#!/usr/bin/env python3
"""
Debug PDF structure to understand checkbox layout.
Uses PyMuPDF (fitz) for low-level text extraction.
"""

import ssl_fix  # Corporate proxy SSL fix - must be first import!

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "backend"))

from dotenv import load_dotenv
load_dotenv()

from azure.storage.blob import BlobServiceClient

TEST_PDF = "food-and-nutrition-complete--fns-organizational-policies-and-procedure--3.-hpr-a.-npo-orders-npo-for-surgery.pdf"


def download_pdf(temp_dir: str) -> str:
    conn_str = os.getenv("STORAGE_CONNECTION_STRING")
    container = os.getenv("CONTAINER_NAME", "policies-active")
    blob_service = BlobServiceClient.from_connection_string(conn_str)
    container_client = blob_service.get_container_client(container)
    local_path = os.path.join(temp_dir, TEST_PDF)
    blob_client = container_client.get_blob_client(TEST_PDF)
    with open(local_path, "wb") as f:
        f.write(blob_client.download_blob().readall())
    return local_path


def analyze_with_pymupdf(pdf_path: str):
    """Use PyMuPDF for detailed text extraction."""
    print("\n" + "="*60)
    print("PyMuPDF (fitz) Analysis")
    print("="*60)

    try:
        import fitz
    except ImportError:
        print("ERROR: pymupdf not installed. Run: pip install pymupdf")
        return

    doc = fitz.open(pdf_path)
    page = doc[0]  # First page

    # Get all text with positions
    text_dict = page.get_text("dict")

    print(f"\nPage 1 dimensions: {page.rect.width} x {page.rect.height}")
    print(f"\nSearching for 'Applies To' section and checkboxes...")

    # Find text containing entity names
    entities_found = []
    checkbox_chars = ['☒', '☐', '✓', '✔', '■', '□']

    for block in text_dict["blocks"]:
        if block["type"] == 0:  # Text block
            for line in block["lines"]:
                line_text = ""
                for span in line["spans"]:
                    line_text += span["text"]

                # Check if line contains entity names or checkboxes
                if any(e in line_text.upper() for e in ['RUMC', 'RUMG', 'RMG', 'ROPH', 'RCMC', 'RCH']) or \
                   any(c in line_text for c in checkbox_chars) or \
                   'applies' in line_text.lower():
                    bbox = line["bbox"]
                    print(f"\n  Line @ y={bbox[1]:.0f}:")
                    print(f"    Text: '{line_text}'")
                    print(f"    BBox: {bbox}")

                    # Extract individual spans
                    for span in line["spans"]:
                        span_text = span["text"]
                        if any(e in span_text.upper() for e in ['RUMC', 'RUMG', 'RMG', 'ROPH', 'RCMC', 'RCH']) or \
                           any(c in span_text for c in checkbox_chars):
                            entities_found.append({
                                "text": span_text,
                                "bbox": span["bbox"],
                                "font": span["font"],
                                "size": span["size"]
                            })

    # Also try raw text extraction
    print("\n" + "-"*40)
    print("Raw text from first page (first 2000 chars):")
    print("-"*40)
    raw_text = page.get_text()
    print(raw_text[:2000])

    # Search for checkbox patterns in raw text
    print("\n" + "-"*40)
    print("Checkbox pattern search:")
    print("-"*40)
    import re
    patterns = [
        (r'Applies\s*To.*?(?=Review|Date|$)', 'Full Applies To section'),
        (r'[☒☐]\s*[A-Z]{2,5}', 'Checkbox before entity'),
        (r'[A-Z]{2,5}\s*[☒☐]', 'Entity before checkbox'),
        (r'RUMC.*?RCH', 'RUMC to RCH span'),
    ]

    for pattern, desc in patterns:
        matches = re.findall(pattern, raw_text, re.DOTALL | re.IGNORECASE)
        if matches:
            print(f"\n  {desc}:")
            for m in matches[:3]:
                print(f"    '{m[:100]}...'")

    doc.close()
    return entities_found


def analyze_with_pdfplumber(pdf_path: str):
    """Use pdfplumber for table extraction."""
    print("\n" + "="*60)
    print("pdfplumber Table Analysis")
    print("="*60)

    try:
        import pdfplumber
    except ImportError:
        print("ERROR: pdfplumber not installed. Run: pip install pdfplumber")
        return

    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]

        # Extract tables
        tables = page.extract_tables()
        print(f"\nTables found: {len(tables)}")

        for i, table in enumerate(tables):
            print(f"\n  Table {i+1} ({len(table)} rows):")
            for j, row in enumerate(table[:5]):  # First 5 rows
                print(f"    Row {j}: {row}")

        # Also look for characters by type
        chars = page.chars
        checkbox_chars = [c for c in chars if c['text'] in '☒☐✓✔■□']
        print(f"\n  Checkbox characters found: {len(checkbox_chars)}")
        for c in checkbox_chars[:10]:
            print(f"    '{c['text']}' @ ({c['x0']:.0f}, {c['top']:.0f})")


def main():
    print("PDF Structure Debug")
    print("Target:", TEST_PDF)

    with tempfile.TemporaryDirectory() as temp_dir:
        pdf_path = download_pdf(temp_dir)
        print(f"Downloaded to: {pdf_path}")

        # Try multiple extraction methods
        analyze_with_pymupdf(pdf_path)
        analyze_with_pdfplumber(pdf_path)


if __name__ == "__main__":
    main()
