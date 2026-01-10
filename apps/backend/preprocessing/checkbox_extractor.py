"""
RUSH Policy checkbox extraction utilities.

This module provides 3-tier checkbox extraction for RUSH policy PDFs:
1. PyMuPDF (primary) - Most reliable for RUSH PDF format
2. Docling (fallback) - Native checkbox detection
3. Text regex (final fallback) - Pattern-based extraction

The "Applies To" field in RUSH policies contains checkboxes indicating
which entities (RUMC, RUMG, RMG, etc.) the policy applies to.

Extracted from chunker.py as part of tech debt refactoring.
"""

import re
import logging
from typing import List

from preprocessing.rush_metadata import (
    RUSH_ENTITIES,
    CHECKED_CHARS,
    UNCHECKED_CHARS,
)

logger = logging.getLogger(__name__)


def extract_applies_to_from_raw_pdf(pdf_path: str) -> List[str]:
    """
    Extract Applies To entities using PyMuPDF for raw text extraction.

    This method is more reliable than Docling for RUSH PDFs because:
    - Docling's table extraction sometimes truncates the "Applies To" row
    - PyMuPDF extracts the complete raw text including all checkbox characters

    The method reads the first page of the PDF, finds the "Applies To" line,
    and parses checkbox states to determine which entities are checked.

    Args:
        pdf_path: Path to the PDF file

    Returns:
        List of entity codes that have checked boxes (e.g., ['RUMC', 'RUMG', 'ROPH', 'RCH'])
    """
    checked_entities = []

    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("PyMuPDF not available for raw checkbox extraction. "
                      "Install with: pip install pymupdf")
        return checked_entities

    try:
        doc = fitz.open(pdf_path)
        if len(doc) == 0:
            return checked_entities

        # Get text from first page (where header/metadata is located)
        page = doc[0]
        raw_text = page.get_text()
        doc.close()

        # Find the Applies To line
        applies_match = re.search(
            r'Applies\s*To[:\s]*(.*?)(?:\n|Printed|Reference|Purpose|$)',
            raw_text,
            re.IGNORECASE | re.DOTALL
        )

        if not applies_match:
            logger.debug("No 'Applies To' section found in raw PDF text")
            return checked_entities

        applies_line = applies_match.group(1)
        logger.debug(f"Raw 'Applies To' line: {applies_line[:100]}...")

        # Parse checkbox states - format is "ENTITY ☒" or "ENTITY ☐"
        # Check each entity for checked status
        for entity in RUSH_ENTITIES:
            # Pattern: Entity name followed by checked checkbox character
            pattern = rf'\b{entity}\s*[☒✓✔■Xx]'
            if re.search(pattern, applies_line, re.IGNORECASE):
                if entity not in checked_entities:
                    checked_entities.append(entity)
                    logger.debug(f"Found checked entity via PyMuPDF: {entity}")

        if checked_entities:
            logger.info(f"PyMuPDF extracted entities: {checked_entities}")

    except Exception as e:
        logger.warning(f"PyMuPDF checkbox extraction failed: {e}")

    return checked_entities


def extract_applies_to_from_checkboxes(doc) -> List[str]:
    """
    Extract Applies To entities using Docling's native checkbox detection.

    Docling detects checkboxes as document items with labels:
    - 'checkbox_selected' for checked boxes
    - 'checkbox_unselected' for unchecked boxes

    The text of checkbox items typically contains the entity name.

    Args:
        doc: Docling document object

    Returns:
        List of entity codes that have checked boxes
    """
    checked_entities = []

    try:
        for item in doc.iterate_items():
            obj = item[0]
            label = str(obj.label) if hasattr(obj, 'label') else ''

            # Only process selected checkboxes
            if label == 'checkbox_selected':
                text = obj.text if hasattr(obj, 'text') else ''

                # Check if text contains any RUSH entity using word boundary matching
                # This prevents 'RU' from matching inside 'RUMC'
                for entity in RUSH_ENTITIES:
                    # Use regex word boundary to match whole entity only
                    pattern = rf'\b{entity}\b'
                    if re.search(pattern, text.upper()):
                        if entity not in checked_entities:
                            checked_entities.append(entity)
                            logger.debug(f"Found checked entity via Docling checkbox: {entity}")

    except Exception as e:
        logger.warning(f"Docling checkbox extraction failed: {e}")

    return checked_entities


def extract_applies_to_from_text(text: str) -> List[str]:
    """
    Extract which entities have checked boxes from text (fallback method).

    Handles multiple formats:
    - RUSH format: ENTITY ☒ or ENTITY ☐ (checkbox AFTER entity name)
    - Alternative: ☒ENTITY or ☐ENTITY (checkbox BEFORE entity name)
    - Bracketed: [X] ENTITY or ENTITY [X]
    - Markdown checkboxes: - [x] ENTITY

    Uses word boundaries (\\b) to prevent partial matches (e.g., 'RU' matching inside 'RUMC').

    IMPORTANT: In RUSH PDFs, format is typically "RUMC ☒ RUMG ☐" where the checkbox
    belongs to the entity BEFORE it, not after. So "RUMC ☒" means RUMC is checked,
    and "☒ RUMG" would be an incorrect parse.

    Args:
        text: Text containing checkbox patterns

    Returns:
        List of entity codes that have checked boxes
    """
    checked_entities = []

    # Find the Applies To section
    applies_match = re.search(
        r'Applies\s*To[:\s]*(.*?)(?:\n\n|$|Review\s+Due|Date\s+Approved)',
        text,
        re.IGNORECASE | re.DOTALL
    )

    search_text = applies_match.group(1) if applies_match else text[:3000]

    # Check each entity for checked status
    # Use word boundary \b to prevent 'RU' from matching inside 'RUMC'
    for entity in RUSH_ENTITIES:
        # Primary pattern (RUSH format): Entity followed DIRECTLY by checked mark
        # This is the standard RUSH policy format: "RUMC ☒ RUMG ☐"
        pattern_entity_then_check = rf'\b{entity}\s*{CHECKED_CHARS}'

        # Alternative pattern: Checked mark followed by entity (for different PDF formats)
        # Only match if the check mark is at start of line or after whitespace/punctuation
        # AND the entity is followed by unchecked mark or whitespace (to avoid "☒ RUMG" matching)
        pattern_check_then_entity = rf'(?:^|[:\s]){CHECKED_CHARS}\s*{entity}\b(?=\s*(?:{UNCHECKED_CHARS}|$|\s))'

        # Bracketed selection [X] ENTITY or ENTITY [X]
        pattern_bracketed = rf'\[X\]\s*{entity}\b|\b{entity}\s*\[X\]'

        # Markdown checkbox format - [x] ENTITY
        pattern_markdown = rf'\[x\]\s*{entity}\b'

        # Try primary pattern first (most common in RUSH PDFs)
        if re.search(pattern_entity_then_check, search_text, re.IGNORECASE):
            if entity not in checked_entities:
                checked_entities.append(entity)
        # Then try alternative patterns
        elif re.search(pattern_check_then_entity, search_text, re.IGNORECASE | re.MULTILINE):
            if entity not in checked_entities:
                checked_entities.append(entity)
        elif re.search(pattern_bracketed, search_text, re.IGNORECASE):
            if entity not in checked_entities:
                checked_entities.append(entity)
        elif re.search(pattern_markdown, search_text, re.IGNORECASE):
            if entity not in checked_entities:
                checked_entities.append(entity)

    return checked_entities
