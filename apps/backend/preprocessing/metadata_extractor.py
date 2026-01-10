"""
RUSH Policy metadata extraction utilities.

This module provides utilities for extracting metadata from RUSH policy PDFs:
- Field extraction from text (title, reference number, dates, etc.)
- Page number extraction from Docling chunks
- Section info extraction from chunk metadata
- Filename cleaning utility

Extracted from chunker.py as part of tech debt refactoring.
"""

import re
import logging
from typing import Optional, Tuple

from preprocessing.rush_metadata import RUSHPolicyMetadata
from preprocessing.checkbox_extractor import extract_applies_to_from_text

logger = logging.getLogger(__name__)


def clean_filename(filename: str) -> str:
    """
    Clean filename to use as fallback title.

    Args:
        filename: PDF filename

    Returns:
        Cleaned title string
    """
    return (
        filename
        .replace('.pdf', '')
        .replace('-', ' ')
        .replace('_', ' ')
        .replace('  ', ' - ')
        .strip()
    )


def extract_page_number(doc_chunk) -> Optional[int]:
    """
    Extract page number from Docling chunk metadata.

    Docling chunks contain provenance info with page references.

    Args:
        doc_chunk: Docling chunk object

    Returns:
        1-indexed page number for PDF navigation, or None if not found
    """
    try:
        # Try to get page number from chunk meta/provenance
        if hasattr(doc_chunk, 'meta') and doc_chunk.meta:
            meta = doc_chunk.meta
            # Check for doc_items which contain provenance info
            if hasattr(meta, 'doc_items') and meta.doc_items:
                for item in meta.doc_items:
                    if hasattr(item, 'prov') and item.prov:
                        for prov in item.prov:
                            if hasattr(prov, 'page_no') and prov.page_no is not None:
                                # Docling page_no is already 1-indexed
                                return prov.page_no
            # Alternative: check for origin in some Docling versions
            if hasattr(meta, 'origin') and meta.origin:
                if hasattr(meta.origin, 'page_no') and meta.origin.page_no is not None:
                    return meta.origin.page_no
    except Exception as e:
        logger.debug(f"Could not extract page number: {e}")
    return None


def extract_section_info(doc_chunk) -> Tuple[str, str]:
    """
    Extract section number and title from Docling chunk metadata.

    Args:
        doc_chunk: Docling chunk object

    Returns:
        Tuple of (section_number, section_title)
    """
    section_number = ""
    section_title = ""

    # Access chunk metadata for headings
    if hasattr(doc_chunk, 'meta') and hasattr(doc_chunk.meta, 'headings'):
        headings = doc_chunk.meta.headings
        if headings:
            last_heading = headings[-1] if isinstance(headings, list) else str(headings)

            # Try to parse Roman numeral section (I., II., III., etc.)
            roman_match = re.match(
                r'^(I{1,3}V?I{0,3}|IV|V|VI{0,3}|IX|X{1,3})\.\s*(.+)',
                last_heading
            )
            if roman_match:
                section_number = roman_match.group(1)
                section_title = roman_match.group(2).strip()
            else:
                # Try numbered section (1.0, 2.0, etc.)
                num_match = re.match(r'^(\d+(?:\.\d+)?)\s*[\.:\s]\s*(.+)', last_heading)
                if num_match:
                    section_number = num_match.group(1)
                    section_title = num_match.group(2).strip()
                else:
                    section_title = last_heading

    return section_number, section_title


def extract_fields_from_text(
    text: str,
    metadata: RUSHPolicyMetadata,
    filename: str
) -> None:
    """
    Extract metadata fields from text using regex patterns.

    Extracts:
    - Policy title (with deduplication and cleanup)
    - Reference number (validated to contain digits)
    - Document owner
    - Approvers
    - Date fields (approved, updated, created, review due)
    - Applies To entities (fallback to text-based extraction)

    Args:
        text: Text content to extract from
        metadata: RUSHPolicyMetadata object to populate (modified in place)
        filename: Source filename for logging
    """
    # Title extraction (multiple patterns)
    # Enhanced patterns to capture full titles, including multi-line titles
    if not metadata.title:
        for pattern in [
            # Pattern 1: Capture until next field label (most specific)
            r'Policy\s+Title[:\s]+(.+?)(?=\s*(?:Policy\s*Number|Reference\s*Number|Effective\s*Date|Document\s*Owner|Applies\s*To))',
            # Pattern 2: Capture full cell content (for table-extracted text)
            r'Policy\s+Title[:\s]+([^\n|]+)',
            # Pattern 3: Generic title field
            r'POLICY\s+TITLE[:\s]+([^\n]+)',
            r'Title[:\s]+([^\n]+)',
        ]:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                # Normalize whitespace (collapse multiple spaces/newlines)
                title_text = ' '.join(match.group(1).strip().split())
                # Clean repeated "Policy Title:" labels from malformed table extraction
                if 'Policy Title' in title_text:
                    title_text = re.sub(r'\s*Policy\s+Title[:\s]*', ' ', title_text, flags=re.IGNORECASE).strip()
                    title_text = ' '.join(title_text.split())  # Re-normalize whitespace
                # Remove "Former" anywhere in the title (artifact from "Former Policy Number" field)
                title_text = re.sub(r'\s*Former\s*', ' ', title_text, flags=re.IGNORECASE).strip()
                title_text = ' '.join(title_text.split())  # Re-normalize whitespace
                # Remove checkbox characters that may leak from table extraction
                title_text = re.sub(r'[☐☒✓✔■□\[\]x]', '', title_text).strip()
                # Truncate at common table field labels that shouldn't be in title
                title_text = re.split(r'\s*(?:Approver|Date\s*Approved|Effective|Owner|Department|Version|Status)[:\(]', title_text, flags=re.IGNORECASE)[0].strip()
                # Deduplicate repeated title text (e.g., "AI Policy AI Policy AI Policy" -> "AI Policy")
                words = title_text.split()
                if len(words) >= 4:
                    # Try to find repeating pattern
                    for pattern_len in range(2, len(words) // 2 + 1):
                        pattern_words = words[:pattern_len]
                        pattern_str = ' '.join(pattern_words)
                        # Check if the pattern repeats
                        full_pattern = ' '.join(pattern_words * (len(words) // pattern_len))
                        if title_text.startswith(full_pattern) and len(pattern_str) >= 5:
                            title_text = pattern_str
                            break
                # Final cleanup: normalize whitespace again
                title_text = ' '.join(title_text.split())
                # Skip if it looks like we captured the next field
                if title_text and not re.match(r'^(Policy\s*Number|Reference|Document|Applies)', title_text, re.IGNORECASE):
                    metadata.title = title_text
                    break

    # Reference number extraction (multiple patterns)
    # Valid ref numbers must contain at least one digit (e.g., "892", "IT-09.02", "POL-2023")
    # Reject pure words like "Document", "Number", "Policy" that regex may incorrectly capture
    INVALID_REF_WORDS = {'document', 'number', 'policy', 'reference', 'ref', 'title', 'none', 'n/a'}

    if not metadata.reference_number:
        for pattern in [
            r'Reference\s*Number[:\s]+([A-Za-z0-9\-\.]+)',
            r'Policy\s*Number[:\s]+([A-Za-z0-9\-\.]+)',
            r'Ref[:\s#]+([A-Za-z0-9\-\.]+)',
            r'Reference[:\s]+(\d{3,6})',
        ]:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                candidate = match.group(1).strip()
                # Validate: must contain at least one digit AND not be an invalid word
                if (re.search(r'\d', candidate) and
                    candidate.lower() not in INVALID_REF_WORDS and
                    len(candidate) >= 2):
                    metadata.reference_number = candidate
                    break

    # Document owner
    if not metadata.document_owner:
        match = re.search(
            r'Document\s+Owner[:\s]+([^\n|]+?)(?=\s*(?:Approver|Date|\n))',
            text, re.IGNORECASE | re.DOTALL
        )
        if match:
            metadata.document_owner = match.group(1).strip()

    # Approvers
    if not metadata.approvers:
        match = re.search(
            r'Approver[s]?[:\s]+([^\n|]+?)(?=\s*(?:Date|Effective|\n))',
            text, re.IGNORECASE | re.DOTALL
        )
        if match:
            metadata.approvers = match.group(1).strip()

    # Date fields
    date_patterns = {
        'date_approved': [
            r'Date\s+Approved[:\s]+([\d/\-]+)',
            r'Approved[:\s]+([\d/\-]+)',
        ],
        'date_updated': [
            r'Date\s+Updated[:\s]+([\d/\-]+)',
            r'Last\s+(?:Updated|Modified|Revised)[:\s]+([\d/\-]+)',
            r'Revised[:\s]+([\d/\-]+)',
        ],
        'date_created': [
            r'Date\s+Created[:\s]+([\d/\-]+)',
            r'Created[:\s]+([\d/\-]+)',
        ],
        'review_due': [
            r'Review\s+Due[:\s]+([\d/\-]+)',
            r'Next\s+Review[:\s]+([\d/\-]+)',
        ],
    }

    for field_name, patterns in date_patterns.items():
        if not getattr(metadata, field_name):
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    setattr(metadata, field_name, match.group(1).strip())
                    break

    # Applies To - text-based checkbox extraction (fallback)
    if not metadata.applies_to:
        metadata.applies_to = extract_applies_to_from_text(text)
