#!/usr/bin/env python3
"""
A/B Test: Checkbox Extraction Methods

Compares two approaches for extracting "Applies To" checkboxes:
A) pypdf form field extraction
B) Docling with do_cell_matching=False

Run: python scripts/test_checkbox_extraction.py
"""

import os
import sys
import tempfile
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "backend"))

from dotenv import load_dotenv
load_dotenv()

from azure.storage.blob import BlobServiceClient

# Target PDF for testing (NPO policy with known checkbox issue)
TEST_PDF = "food-and-nutrition-complete--fns-organizational-policies-and-procedure--3.-hpr-a.-npo-orders-npo-for-surgery.pdf"

# Expected entities (from visual inspection of PDF)
EXPECTED_ENTITIES = ["RUMC", "RUMG", "ROPH", "RCH"]  # All should be checked

# All RUSH entity codes
RUSH_ENTITIES = ['RUMC', 'RUMG', 'RMG', 'ROPH', 'RCMC', 'RCH', 'ROPPG', 'RCMG', 'RU']


def download_test_pdf(temp_dir: str) -> str:
    """Download NPO PDF from blob storage."""
    conn_str = os.getenv("STORAGE_CONNECTION_STRING")
    container = os.getenv("CONTAINER_NAME", "policies-active")

    blob_service = BlobServiceClient.from_connection_string(conn_str)
    container_client = blob_service.get_container_client(container)

    local_path = os.path.join(temp_dir, TEST_PDF)
    blob_client = container_client.get_blob_client(TEST_PDF)

    with open(local_path, "wb") as f:
        download_stream = blob_client.download_blob()
        f.write(download_stream.readall())

    print(f"Downloaded: {TEST_PDF}")
    return local_path


def test_pypdf_extraction(pdf_path: str) -> dict:
    """
    Method A: Extract checkboxes using pypdf form field API.
    """
    print("\n" + "="*60)
    print("METHOD A: pypdf Form Field Extraction")
    print("="*60)

    try:
        from pypdf import PdfReader
    except ImportError:
        print("ERROR: pypdf not installed. Run: pip install pypdf")
        return {"entities": [], "error": "pypdf not installed"}

    reader = PdfReader(pdf_path)
    fields = reader.get_fields() or {}

    print(f"\nTotal form fields found: {len(fields)}")

    checked_entities = []
    all_checkbox_info = []

    for field_name, field_obj in fields.items():
        # Get field type and value
        field_type = field_obj.get('/FT', 'Unknown')
        value = None

        if hasattr(field_obj, 'value'):
            value = str(field_obj.value) if field_obj.value else ""
        elif '/V' in field_obj:
            value = str(field_obj['/V'])

        # Check if this looks like a checkbox
        is_checkbox = field_type == '/Btn' or 'check' in field_name.lower()
        is_checked = value in ['/Yes', '/1', 'Yes', '1', '/On', 'On', '/True', 'True']

        if is_checkbox or any(e in field_name.upper() for e in RUSH_ENTITIES):
            all_checkbox_info.append({
                "name": field_name,
                "type": str(field_type),
                "value": value,
                "is_checked": is_checked
            })

            # Check for entity match
            if is_checked:
                for entity in RUSH_ENTITIES:
                    if entity in field_name.upper():
                        if entity not in checked_entities:
                            checked_entities.append(entity)
                            print(f"  FOUND: {entity} (field: {field_name}, value: {value})")

    print(f"\nAll checkbox-like fields ({len(all_checkbox_info)}):")
    for info in all_checkbox_info:
        status = "CHECKED" if info["is_checked"] else "unchecked"
        print(f"  [{status}] {info['name']} = {info['value']} (type: {info['type']})")

    print(f"\nExtracted entities: {checked_entities}")

    return {
        "entities": checked_entities,
        "all_fields": all_checkbox_info,
        "total_fields": len(fields)
    }


def test_docling_current(pdf_path: str) -> dict:
    """
    Method B-1: Current Docling implementation (baseline).
    """
    print("\n" + "="*60)
    print("METHOD B-1: Docling Current (TableFormer ACCURATE)")
    print("="*60)

    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
        from docling.datamodel.base_models import InputFormat
    except ImportError:
        print("ERROR: Docling not installed")
        return {"entities": [], "error": "Docling not installed"}

    # Current configuration (from chunker.py)
    pipeline_options = PdfPipelineOptions(
        do_table_structure=True,
        do_ocr=False,
    )
    pipeline_options.table_structure_options.mode = TableFormerMode.ACCURATE

    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )

    result = converter.convert(pdf_path)
    doc = result.document

    # Export to markdown to see what Docling extracts
    md_text = doc.export_to_markdown()

    # Find Applies To section
    import re
    applies_match = re.search(
        r'Applies\s*To[:\s]*(.*?)(?:\n\n|$|Review\s+Due|Date\s+Approved)',
        md_text,
        re.IGNORECASE | re.DOTALL
    )

    applies_section = applies_match.group(1) if applies_match else md_text[:500]
    print(f"\nApplies To section extracted:")
    print(f"  {applies_section[:200]}...")

    # Extract entities using current regex logic
    CHECKED_CHARS = r'[\u2612\u2611\u2713\u2714\u25A0\u2718Xx☒☑✓✔■]'
    checked_entities = []

    for entity in RUSH_ENTITIES:
        pattern = rf'\b{entity}\s*{CHECKED_CHARS}'
        if re.search(pattern, applies_section, re.IGNORECASE):
            checked_entities.append(entity)
            print(f"  FOUND: {entity}")

    print(f"\nExtracted entities: {checked_entities}")

    return {
        "entities": checked_entities,
        "applies_section": applies_section[:300]
    }


def test_docling_cell_matching(pdf_path: str) -> dict:
    """
    Method B-2: Docling with do_cell_matching=False.
    """
    print("\n" + "="*60)
    print("METHOD B-2: Docling with do_cell_matching=False")
    print("="*60)

    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
        from docling.datamodel.base_models import InputFormat
    except ImportError:
        print("ERROR: Docling not installed")
        return {"entities": [], "error": "Docling not installed"}

    # Modified configuration with do_cell_matching=False
    pipeline_options = PdfPipelineOptions(
        do_table_structure=True,
        do_ocr=False,
    )
    pipeline_options.table_structure_options.mode = TableFormerMode.ACCURATE
    pipeline_options.table_structure_options.do_cell_matching = False  # KEY CHANGE

    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )

    result = converter.convert(pdf_path)
    doc = result.document

    # Export to markdown
    md_text = doc.export_to_markdown()

    # Find Applies To section
    import re
    applies_match = re.search(
        r'Applies\s*To[:\s]*(.*?)(?:\n\n|$|Review\s+Due|Date\s+Approved)',
        md_text,
        re.IGNORECASE | re.DOTALL
    )

    applies_section = applies_match.group(1) if applies_match else md_text[:500]
    print(f"\nApplies To section extracted:")
    print(f"  {applies_section[:200]}...")

    # Extract entities
    CHECKED_CHARS = r'[\u2612\u2611\u2713\u2714\u25A0\u2718Xx☒☑✓✔■]'
    checked_entities = []

    for entity in RUSH_ENTITIES:
        pattern = rf'\b{entity}\s*{CHECKED_CHARS}'
        if re.search(pattern, applies_section, re.IGNORECASE):
            checked_entities.append(entity)
            print(f"  FOUND: {entity}")

    print(f"\nExtracted entities: {checked_entities}")

    return {
        "entities": checked_entities,
        "applies_section": applies_section[:300]
    }


def compare_results(expected: list, results: dict) -> None:
    """Compare extraction results against expected values."""
    print("\n" + "="*60)
    print("COMPARISON RESULTS")
    print("="*60)

    print(f"\nExpected entities: {expected}")

    for method, data in results.items():
        entities = data.get("entities", [])
        missing = set(expected) - set(entities)
        extra = set(entities) - set(expected)

        if set(entities) == set(expected):
            status = "PERFECT MATCH"
        elif entities:
            status = f"PARTIAL ({len(entities)}/{len(expected)})"
        else:
            status = "FAILED"

        print(f"\n{method}:")
        print(f"  Status: {status}")
        print(f"  Found: {entities}")
        if missing:
            print(f"  Missing: {list(missing)}")
        if extra:
            print(f"  Extra: {list(extra)}")


def main():
    print("Checkbox Extraction A/B Test")
    print("Target PDF:", TEST_PDF)
    print("Expected entities:", EXPECTED_ENTITIES)

    with tempfile.TemporaryDirectory() as temp_dir:
        # Download test PDF
        pdf_path = download_test_pdf(temp_dir)

        results = {}

        # Test Method A: pypdf
        results["pypdf"] = test_pypdf_extraction(pdf_path)

        # Test Method B-1: Current Docling
        results["docling_current"] = test_docling_current(pdf_path)

        # Test Method B-2: Docling with do_cell_matching=False
        results["docling_cell_matching"] = test_docling_cell_matching(pdf_path)

        # Compare all results
        compare_results(EXPECTED_ENTITIES, results)

        # Recommendation
        print("\n" + "="*60)
        print("RECOMMENDATION")
        print("="*60)

        pypdf_score = len(set(results["pypdf"]["entities"]) & set(EXPECTED_ENTITIES))
        docling_score = len(set(results["docling_current"]["entities"]) & set(EXPECTED_ENTITIES))
        cell_match_score = len(set(results["docling_cell_matching"]["entities"]) & set(EXPECTED_ENTITIES))

        scores = {
            "pypdf": pypdf_score,
            "docling_current": docling_score,
            "docling_cell_matching": cell_match_score
        }

        best_method = max(scores, key=scores.get)
        print(f"\nBest method: {best_method} (score: {scores[best_method]}/{len(EXPECTED_ENTITIES)})")

        if pypdf_score == len(EXPECTED_ENTITIES):
            print("\nRECOMMENDATION: Use pypdf for checkbox extraction (form fields)")
            print("  - pypdf reads actual PDF form field data")
            print("  - Keep Docling for all other document processing")
        elif cell_match_score > docling_score:
            print("\nRECOMMENDATION: Enable do_cell_matching=False in Docling")
            print("  - This improves table column separation")
        else:
            print("\nRECOMMENDATION: Consider hybrid approach or manual inspection")


if __name__ == "__main__":
    main()
