"""
RUSH Policy Chunker - Docling-based Implementation

Primary chunker for RUSH policy PDF documents using IBM Docling for:
- Superior layout understanding
- TableFormer model for accurate table extraction
- Native checkbox detection for "Applies To" fields
- Hierarchical section-aware chunking

This chunker is designed for 100% accuracy policy retrieval:
- Preserves EXACT text (no synthesis)
- Chunks by section boundaries (never splits mid-sentence)
- Includes full citation metadata for every chunk
- Handles the standard RUSH policy PDF format

Usage:
    from preprocessing.chunker import PolicyChunker, PolicyChunk, ProcessingResult, ProcessingStatus

    chunker = PolicyChunker()

    # Simple usage (backward compatible)
    chunks = chunker.process_pdf("policy.pdf")

    # With detailed error handling
    result = chunker.process_pdf_with_status("policy.pdf")
    if result.is_error:
        print(f"Failed: {result.status.value} - {result.error_message}")
    elif result.is_empty:
        print("Document had no extractable content")
    else:
        documents = [chunk.to_azure_document() for chunk in result.chunks]

Legacy PyMuPDF implementation is available in preprocessing/archive/pymupdf_chunker.py
"""

import re
import os
import hashlib
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)


class ProcessingStatus(str, Enum):
    """Status codes for PDF processing results."""
    SUCCESS = "success"
    EMPTY_DOCUMENT = "empty_document"
    FILE_NOT_FOUND = "file_not_found"
    DOCLING_UNAVAILABLE = "docling_unavailable"
    PROCESSING_ERROR = "processing_error"


@dataclass
class ProcessingResult:
    """Result of PDF processing with detailed status information.

    Use this to distinguish between:
    - Success with chunks
    - Empty document (0 chunks but not an error)
    - Actual failures (Docling unavailable, file not found, processing error)
    """
    chunks: List["PolicyChunk"]
    status: ProcessingStatus
    error_message: Optional[str] = None
    source_file: str = ""

    @property
    def is_success(self) -> bool:
        """True if processing succeeded (may have 0 chunks for empty docs)."""
        return self.status == ProcessingStatus.SUCCESS

    @property
    def is_error(self) -> bool:
        """True if processing failed due to an actual error."""
        return self.status in (
            ProcessingStatus.FILE_NOT_FOUND,
            ProcessingStatus.DOCLING_UNAVAILABLE,
            ProcessingStatus.PROCESSING_ERROR
        )

    @property
    def is_empty(self) -> bool:
        """True if document was processed but had no extractable content."""
        return self.status == ProcessingStatus.EMPTY_DOCUMENT


@dataclass
class PolicyChunk:
    """
    A chunk optimized for LITERAL text retrieval with full citation.

    Key principle: The 'text' field contains EXACT text from the PDF,
    never modified, summarized, or paraphrased.
    """
    chunk_id: str
    policy_title: str
    reference_number: str
    section_number: str
    section_title: str
    text: str                    # EXACT text - never modified
    date_updated: str
    applies_to: str              # Comma-separated string (backward compatibility)
    source_file: str
    char_count: int
    content_hash: str = field(default="")
    document_owner: str = field(default="")
    date_approved: str = field(default="")

    # Entity-specific boolean filters (for efficient Azure Search filtering)
    applies_to_rumc: bool = field(default=False)   # Rush University Medical Center
    applies_to_rumg: bool = field(default=False)   # Rush University Medical Group
    applies_to_rmg: bool = field(default=False)    # Rush Medical Group
    applies_to_roph: bool = field(default=False)   # Rush Oak Park Hospital
    applies_to_rcmc: bool = field(default=False)   # Rush Copley Medical Center
    applies_to_rch: bool = field(default=False)    # Rush Children's Hospital
    applies_to_roppg: bool = field(default=False)  # Rush Oak Park Physicians Group
    applies_to_rcmg: bool = field(default=False)   # Rush Copley Medical Group
    applies_to_ru: bool = field(default=False)     # Rush University

    # Hierarchical chunking fields
    chunk_level: str = field(default="semantic")   # "document" | "section" | "semantic"
    parent_chunk_id: Optional[str] = field(default=None)
    chunk_index: int = field(default=0)

    # Enhanced metadata fields
    category: Optional[str] = field(default=None)
    subcategory: Optional[str] = field(default=None)
    regulatory_citations: Optional[str] = field(default=None)
    related_policies: Optional[str] = field(default=None)

    # Version control fields (for monthly update tracking)
    version_number: str = field(default="1.0")           # Policy version (e.g., "1.0", "2.0")
    version_date: Optional[str] = field(default=None)    # ISO datetime when version was created
    effective_date: Optional[str] = field(default=None)  # When policy takes effect
    expiration_date: Optional[str] = field(default=None) # When policy expires (if any)
    policy_status: str = field(default="ACTIVE")         # ACTIVE, SUPERSEDED, RETIRED, DRAFT
    superseded_by: Optional[str] = field(default=None)   # Version that replaced this (e.g., "2.0")
    version_sequence: int = field(default=1)             # Numeric sequence for sorting

    def __post_init__(self):
        if not self.content_hash:
            self.content_hash = hashlib.md5(self.text.encode()).hexdigest()[:12]

    def get_citation(self) -> str:
        """Generate citation for RAG response."""
        ref_part = f"Ref: {self.reference_number}" if self.reference_number else "No Ref #"
        if self.section_number and self.section_title:
            return f"{self.policy_title} ({ref_part}), Section {self.section_number}. {self.section_title}"
        return f"{self.policy_title} ({ref_part})"

    def to_azure_document(self) -> dict:
        """
        Format for Azure AI Search index.

        Recommended index schema:
        - id: Edm.String (key)
        - content: Edm.String (searchable)
        - title: Edm.String (searchable, filterable)
        - reference_number: Edm.String (filterable)
        - section: Edm.String (searchable, filterable)
        - citation: Edm.String (retrievable)
        - applies_to: Edm.String (filterable)
        - applies_to_*: Edm.Boolean (filterable, facetable) - 9 entity fields
        - date_updated: Edm.String (filterable)
        - content_vector: Collection(Edm.Single) (for vector search)
        - chunk_level: Edm.String (filterable)
        - parent_chunk_id: Edm.String (filterable)
        - chunk_index: Edm.Int32 (sortable)
        - category: Edm.String (filterable, facetable)
        - subcategory: Edm.String (filterable, facetable)
        - regulatory_citations: Edm.String (searchable)
        - related_policies: Edm.String (searchable)
        """
        # Azure requires alphanumeric IDs
        safe_id = re.sub(r'[^a-zA-Z0-9_-]', '_', self.chunk_id)
        return {
            "id": safe_id,
            "content": self.text,
            "title": self.policy_title,
            "reference_number": self.reference_number,
            "section": f"{self.section_number}. {self.section_title}" if self.section_number else "",
            "citation": self.get_citation(),
            "applies_to": self.applies_to,
            "date_updated": self.date_updated,
            "source_file": self.source_file,
            "content_hash": self.content_hash,
            "document_owner": self.document_owner,
            "date_approved": self.date_approved,
            # Entity-specific boolean filters
            "applies_to_rumc": self.applies_to_rumc,
            "applies_to_rumg": self.applies_to_rumg,
            "applies_to_rmg": self.applies_to_rmg,
            "applies_to_roph": self.applies_to_roph,
            "applies_to_rcmc": self.applies_to_rcmc,
            "applies_to_rch": self.applies_to_rch,
            "applies_to_roppg": self.applies_to_roppg,
            "applies_to_rcmg": self.applies_to_rcmg,
            "applies_to_ru": self.applies_to_ru,
            # Hierarchical chunking fields
            "chunk_level": self.chunk_level,
            "parent_chunk_id": self.parent_chunk_id,
            "chunk_index": self.chunk_index,
            # Enhanced metadata fields
            "category": self.category,
            "subcategory": self.subcategory,
            "regulatory_citations": self.regulatory_citations,
            "related_policies": self.related_policies,
            # Version control fields
            "version_number": self.version_number,
            "version_date": self.version_date,
            "effective_date": self.effective_date,
            "expiration_date": self.expiration_date,
            "policy_status": self.policy_status,
            "superseded_by": self.superseded_by,
            "version_sequence": self.version_sequence,
        }

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "chunk_id": self.chunk_id,
            "policy_title": self.policy_title,
            "reference_number": self.reference_number,
            "section_number": self.section_number,
            "section_title": self.section_title,
            "text": self.text,
            "date_updated": self.date_updated,
            "applies_to": self.applies_to,
            "source_file": self.source_file,
            "char_count": self.char_count,
            "content_hash": self.content_hash,
            "document_owner": self.document_owner,
            "date_approved": self.date_approved,
            "citation": self.get_citation(),
            # Entity-specific boolean filters
            "applies_to_rumc": self.applies_to_rumc,
            "applies_to_rumg": self.applies_to_rumg,
            "applies_to_rmg": self.applies_to_rmg,
            "applies_to_roph": self.applies_to_roph,
            "applies_to_rcmc": self.applies_to_rcmc,
            "applies_to_rch": self.applies_to_rch,
            "applies_to_roppg": self.applies_to_roppg,
            "applies_to_rcmg": self.applies_to_rcmg,
            "applies_to_ru": self.applies_to_ru,
            # Hierarchical chunking fields
            "chunk_level": self.chunk_level,
            "parent_chunk_id": self.parent_chunk_id,
            "chunk_index": self.chunk_index,
            # Enhanced metadata fields
            "category": self.category,
            "subcategory": self.subcategory,
            "regulatory_citations": self.regulatory_citations,
            "related_policies": self.related_policies,
            # Version control fields (for monthly update tracking)
            "version_number": self.version_number,
            "version_date": self.version_date,
            "effective_date": self.effective_date,
            "expiration_date": self.expiration_date,
            "policy_status": self.policy_status,
            "superseded_by": self.superseded_by,
            "version_sequence": self.version_sequence,
        }


# All RUSH entity codes that may appear in checkboxes
RUSH_ENTITIES = ['RUMC', 'RUMG', 'RMG', 'ROPH', 'RCMC', 'RCH', 'ROPPG', 'RCMG', 'RU']

# Unicode characters for checked/unchecked states
CHECKED_CHARS = r'[\u2612\u2611\u2713\u2714\u25A0\u2718Xx☒☑✓✔■]'
UNCHECKED_CHARS = r'[\u2610\u25A1\u25CB☐]'


@dataclass
class RUSHPolicyMetadata:
    """Structured metadata extracted from RUSH policy header table."""
    title: str = ""
    reference_number: str = ""
    document_owner: str = ""
    approvers: str = ""
    date_created: str = ""
    date_approved: str = ""
    date_updated: str = ""
    review_due: str = ""
    applies_to: List[str] = field(default_factory=list)

    # Entity-specific booleans (populated from applies_to list)
    applies_to_rumc: bool = field(default=False)
    applies_to_rumg: bool = field(default=False)
    applies_to_rmg: bool = field(default=False)
    applies_to_roph: bool = field(default=False)
    applies_to_rcmc: bool = field(default=False)
    applies_to_rch: bool = field(default=False)
    applies_to_roppg: bool = field(default=False)
    applies_to_rcmg: bool = field(default=False)
    applies_to_ru: bool = field(default=False)

    # Enhanced metadata
    category: Optional[str] = field(default=None)
    subcategory: Optional[str] = field(default=None)
    regulatory_citations: Optional[str] = field(default=None)
    related_policies: Optional[str] = field(default=None)

    @property
    def applies_to_str(self) -> str:
        """Format applies_to as comma-separated string for index."""
        return ", ".join(self.applies_to) if self.applies_to else "All"

    def set_entity_booleans_from_list(self) -> None:
        """Set entity boolean fields based on applies_to list."""
        entity_to_field = {
            'RUMC': 'applies_to_rumc',
            'RUMG': 'applies_to_rumg',
            'RMG': 'applies_to_rmg',
            'ROPH': 'applies_to_roph',
            'RCMC': 'applies_to_rcmc',
            'RCH': 'applies_to_rch',
            'ROPPG': 'applies_to_roppg',
            'RCMG': 'applies_to_rcmg',
            'RU': 'applies_to_ru',
        }
        for entity in self.applies_to:
            field_name = entity_to_field.get(entity.upper())
            if field_name:
                setattr(self, field_name, True)


class PolicyChunker:
    """
    Docling-based chunker for RUSH policy PDFs.

    Features:
    - Table-aware header extraction via TableFormer
    - Native checkbox state detection for "Applies To" field
    - Hierarchical section chunking
    - Compatible with Azure AI Search schema

    Design principles:
    1. EXACT TEXT: Never modify, summarize, or paraphrase content
    2. SECTION BOUNDARIES: Chunk at section breaks (I., II., III.)
    3. SAFE SPLITS: If section too large, split at numbered items (1.1, 2.1)
    4. FULL CITATION: Every chunk has complete metadata for attribution
    5. NO ORPHANS: Minimum chunk size prevents tiny/useless chunks
    """

    def __init__(
        self,
        max_chunk_size: int = 1500,
        min_chunk_size: int = 100,
        overlap_sentences: int = 0,  # For literal retrieval, we want 0 overlap
        backend: Optional[str] = None  # Kept for backward compatibility, ignored
    ):
        """
        Initialize the Docling-based policy chunker.

        Args:
            max_chunk_size: Maximum characters per chunk (default: 1500)
            min_chunk_size: Minimum characters per chunk (default: 100)
            overlap_sentences: Not used in current implementation (kept for compatibility)
            backend: Deprecated parameter, kept for backward compatibility
        """
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size
        self.overlap_sentences = overlap_sentences
        self.backend = 'docling'  # Always use Docling

        # Initialize Docling components
        try:
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
            from docling.datamodel.base_models import InputFormat
            from docling_core.transforms.chunker import HierarchicalChunker

            # Configure pipeline for optimal policy parsing
            pipeline_options = PdfPipelineOptions(
                do_table_structure=True,  # Enable table extraction
                do_ocr=False,  # RUSH PDFs are programmatic, not scanned
            )
            pipeline_options.table_structure_options.mode = TableFormerMode.ACCURATE

            self.converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
                }
            )
            self.chunker = HierarchicalChunker()
            self._docling_available = True

            logger.info("PolicyChunker initialized with Docling backend (TableFormer ACCURATE mode)")

        except ImportError as e:
            logger.error(f"Docling not available: {e}")
            logger.error("Install with: pip install docling docling-core")
            self._docling_available = False
            self.converter = None
            self.chunker = None

    def process_pdf(self, pdf_path: str) -> List[PolicyChunk]:
        """
        Process a policy PDF into chunks (backward-compatible wrapper).

        Args:
            pdf_path: Path to the PDF file

        Returns:
            List of PolicyChunk objects. Returns empty list on any error.

        Note:
            For detailed error information, use process_pdf_with_status() instead.
        """
        result = self.process_pdf_with_status(pdf_path)
        return result.chunks

    def process_pdf_with_status(self, pdf_path: str) -> ProcessingResult:
        """
        Process a policy PDF into chunks with detailed status information.

        Args:
            pdf_path: Path to the PDF file

        Returns:
            ProcessingResult containing:
            - chunks: List of PolicyChunk objects
            - status: ProcessingStatus enum indicating success/failure type
            - error_message: Human-readable error description if failed
            - source_file: Filename for logging/tracking

        Use this method when you need to distinguish between:
        - Empty documents (SUCCESS with 0 chunks vs EMPTY_DOCUMENT)
        - Missing dependencies (DOCLING_UNAVAILABLE)
        - File issues (FILE_NOT_FOUND)
        - Processing errors (PROCESSING_ERROR)
        """
        filename = os.path.basename(pdf_path)

        # Check Docling availability
        if not self._docling_available:
            logger.error("Docling is not available. Cannot process PDF.")
            return ProcessingResult(
                chunks=[],
                status=ProcessingStatus.DOCLING_UNAVAILABLE,
                error_message="Docling not installed. Run: pip install docling docling-core",
                source_file=filename
            )

        # Check file exists
        if not os.path.exists(pdf_path):
            logger.error(f"PDF not found: {pdf_path}")
            return ProcessingResult(
                chunks=[],
                status=ProcessingStatus.FILE_NOT_FOUND,
                error_message=f"File not found: {pdf_path}",
                source_file=filename
            )

        # Check file not empty
        file_size = os.path.getsize(pdf_path)
        if file_size == 0:
            logger.warning(f"PDF is empty (0 bytes): {pdf_path}")
            return ProcessingResult(
                chunks=[],
                status=ProcessingStatus.EMPTY_DOCUMENT,
                error_message="PDF file is empty (0 bytes)",
                source_file=filename
            )

        logger.info(f"Processing PDF with Docling: {filename}")

        try:
            # Parse PDF with Docling
            result = self.converter.convert(pdf_path)
            doc = result.document

            # Extract metadata from header table(s)
            metadata = self._extract_header_metadata(doc, pdf_path)

            # Chunk the document body
            chunks = self._chunk_document(doc, metadata, filename)

            logger.info(f"Docling processed {filename}: {len(chunks)} chunks, "
                       f"ref={metadata.reference_number}, applies_to={metadata.applies_to_str}")

            # Distinguish between success with chunks vs empty document
            if not chunks:
                return ProcessingResult(
                    chunks=[],
                    status=ProcessingStatus.EMPTY_DOCUMENT,
                    source_file=filename
                )

            return ProcessingResult(
                chunks=chunks,
                status=ProcessingStatus.SUCCESS,
                source_file=filename
            )

        except Exception as e:
            logger.error(f"Docling failed to process {filename}: {e}")
            return ProcessingResult(
                chunks=[],
                status=ProcessingStatus.PROCESSING_ERROR,
                error_message=str(e),
                source_file=filename
            )

    def _extract_header_metadata(self, doc, pdf_path: str) -> RUSHPolicyMetadata:
        """
        Extract metadata from the header table in RUSH policy PDFs.

        RUSH policies typically have a header table on page 1 with:
        - Policy Title
        - Reference Number / Policy Number
        - Document Owner
        - Approver(s)
        - Date Approved, Date Updated
        - Applies To: checkbox row with RUMC, RMG, ROPH, RCMC, etc.
        """
        metadata = RUSHPolicyMetadata()
        filename = os.path.basename(pdf_path)

        # Get all tables from the document
        tables = list(doc.tables) if hasattr(doc, 'tables') else []

        # Also get markdown for fallback text extraction
        try:
            full_text = doc.export_to_markdown()
        except Exception:
            full_text = ""

        if not tables and not full_text:
            logger.warning(f"No tables or text found in {filename}")
            metadata.title = self._clean_filename(filename)
            return metadata

        # FIRST: Extract Applies To using PyMuPDF (most accurate for RUSH PDFs)
        # Do this before Docling text extraction which can truncate the checkbox row
        metadata.applies_to = self._extract_applies_to_from_raw_pdf(pdf_path)
        if metadata.applies_to:
            logger.info(f"PyMuPDF extracted {len(metadata.applies_to)} entities: {metadata.applies_to}")

        # Try to extract from first table (header table)
        if tables:
            try:
                header_table = tables[0]
                # Export table to text for parsing
                if hasattr(header_table, 'export_to_dataframe'):
                    df = header_table.export_to_dataframe()
                    table_text = df.to_string()
                elif hasattr(header_table, 'export_to_markdown'):
                    table_text = header_table.export_to_markdown()
                else:
                    table_text = str(header_table)

                # Extract fields from table
                self._extract_fields_from_text(table_text, metadata, filename)

            except Exception as e:
                logger.warning(f"Table extraction failed for {filename}: {e}")

        # Fallback/supplement with full text extraction
        if not metadata.title or not metadata.reference_number:
            self._extract_fields_from_text(full_text, metadata, filename)

        # Fall back to Docling checkbox detection if PyMuPDF didn't extract anything
        if not metadata.applies_to:
            metadata.applies_to = self._extract_applies_to_from_checkboxes(doc)

        # Set entity boolean fields based on extracted applies_to list
        metadata.set_entity_booleans_from_list()

        # If still no title, use cleaned filename
        if not metadata.title:
            metadata.title = self._clean_filename(filename)

        return metadata

    def _extract_fields_from_text(
        self,
        text: str,
        metadata: RUSHPolicyMetadata,
        filename: str
    ) -> None:
        """Extract metadata fields from text using regex patterns."""

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
            metadata.applies_to = self._extract_applies_to(text)

    def _extract_applies_to_from_checkboxes(self, doc) -> List[str]:
        """
        Extract Applies To entities using Docling's native checkbox detection.

        Docling detects checkboxes as document items with labels:
        - 'checkbox_selected' for checked boxes
        - 'checkbox_unselected' for unchecked boxes

        The text of checkbox items typically contains the entity name.
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

    def _extract_applies_to_from_raw_pdf(self, pdf_path: str) -> List[str]:
        """
        Extract Applies To entities using PyMuPDF for raw text extraction.

        This method is more reliable than Docling for RUSH PDFs because:
        - Docling's table extraction sometimes truncates the "Applies To" row
        - PyMuPDF extracts the complete raw text including all checkbox characters

        The method reads the first page of the PDF, finds the "Applies To" line,
        and parses checkbox states to determine which entities are checked.

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

    def _extract_applies_to(self, text: str) -> List[str]:
        """
        Extract which entities have checked boxes from text (fallback method).

        Handles multiple formats:
        - RUSH format: ENTITY ☒ or ENTITY ☐ (checkbox AFTER entity name)
        - Alternative: ☒ENTITY or ☐ENTITY (checkbox BEFORE entity name)
        - Bracketed: [X] ENTITY or ENTITY [X]
        - Markdown checkboxes: - [x] ENTITY

        Uses word boundaries (\b) to prevent partial matches (e.g., 'RU' matching inside 'RUMC').

        IMPORTANT: In RUSH PDFs, format is typically "RUMC ☒ RUMG ☐" where the checkbox
        belongs to the entity BEFORE it, not after. So "RUMC ☒" means RUMC is checked,
        and "☒ RUMG" would be an incorrect parse.
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

    def _clean_filename(self, filename: str) -> str:
        """Clean filename to use as fallback title."""
        return (
            filename
            .replace('.pdf', '')
            .replace('-', ' ')
            .replace('_', ' ')
            .replace('  ', ' - ')
            .strip()
        )

    def _chunk_document(
        self,
        doc,
        metadata: RUSHPolicyMetadata,
        source_file: str
    ) -> List[PolicyChunk]:
        """
        Chunk the document body using Docling's HierarchicalChunker.

        Returns PolicyChunk objects compatible with existing index schema.
        """
        chunks = []
        chunk_counter = 0

        try:
            # Use Docling's hierarchical chunker
            doc_chunks = list(self.chunker.chunk(dl_doc=doc))
        except Exception as e:
            logger.warning(f"HierarchicalChunker failed, using fallback: {e}")
            return self._fallback_chunking(doc, metadata, source_file)

        # Track chunk indices for hierarchical relationships
        global_chunk_index = 0

        for doc_chunk in doc_chunks:
            text = doc_chunk.text.strip()

            if len(text) < self.min_chunk_size:
                continue

            # Get section info from chunk metadata
            section_number, section_title = self._extract_section_info(doc_chunk)

            # Determine chunk level based on content/context
            chunk_level = "section" if section_number else "semantic"

            # Split if too large
            if len(text) > self.max_chunk_size:
                sub_texts = self._split_oversized(text, self.max_chunk_size)
                parent_id = f"{metadata.reference_number or 'doc'}_{chunk_counter}"
                for i, sub_text in enumerate(sub_texts):
                    if len(sub_text) >= self.min_chunk_size:
                        chunk = self._create_policy_chunk(
                            chunk_id=f"{metadata.reference_number or 'doc'}_{chunk_counter}_{i}",
                            text=sub_text,
                            metadata=metadata,
                            section_number=section_number,
                            section_title=section_title,
                            source_file=source_file,
                            chunk_level="semantic",  # Split chunks are semantic level
                            parent_chunk_id=parent_id if i > 0 else None,
                            chunk_index=global_chunk_index
                        )
                        chunks.append(chunk)
                        global_chunk_index += 1
                chunk_counter += 1
            else:
                chunk = self._create_policy_chunk(
                    chunk_id=f"{metadata.reference_number or 'doc'}_{chunk_counter}",
                    text=text,
                    metadata=metadata,
                    section_number=section_number,
                    section_title=section_title,
                    source_file=source_file,
                    chunk_level=chunk_level,
                    parent_chunk_id=None,
                    chunk_index=global_chunk_index
                )
                chunks.append(chunk)
                chunk_counter += 1
                global_chunk_index += 1

        return chunks

    def _extract_section_info(self, doc_chunk) -> Tuple[str, str]:
        """Extract section number and title from Docling chunk metadata."""
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

    def _create_policy_chunk(
        self,
        chunk_id: str,
        text: str,
        metadata: RUSHPolicyMetadata,
        section_number: str,
        section_title: str,
        source_file: str,
        chunk_level: str = "semantic",
        parent_chunk_id: Optional[str] = None,
        chunk_index: int = 0
    ) -> PolicyChunk:
        """Create a PolicyChunk from parsed data."""
        return PolicyChunk(
            chunk_id=chunk_id,
            policy_title=metadata.title,
            reference_number=metadata.reference_number,
            section_number=section_number,
            section_title=section_title,
            text=text,
            date_updated=metadata.date_updated,
            applies_to=metadata.applies_to_str,
            source_file=source_file,
            char_count=len(text),
            document_owner=metadata.document_owner,
            date_approved=metadata.date_approved,
            # Entity-specific boolean filters
            applies_to_rumc=metadata.applies_to_rumc,
            applies_to_rumg=metadata.applies_to_rumg,
            applies_to_rmg=metadata.applies_to_rmg,
            applies_to_roph=metadata.applies_to_roph,
            applies_to_rcmc=metadata.applies_to_rcmc,
            applies_to_rch=metadata.applies_to_rch,
            applies_to_roppg=metadata.applies_to_roppg,
            applies_to_rcmg=metadata.applies_to_rcmg,
            applies_to_ru=metadata.applies_to_ru,
            # Hierarchical chunking fields
            chunk_level=chunk_level,
            parent_chunk_id=parent_chunk_id,
            chunk_index=chunk_index,
            # Enhanced metadata fields
            category=metadata.category,
            subcategory=metadata.subcategory,
            regulatory_citations=metadata.regulatory_citations,
            related_policies=metadata.related_policies,
        )

    def _split_oversized(self, text: str, max_size: int) -> List[str]:
        """Split oversized text at paragraph boundaries."""
        if len(text) <= max_size:
            return [text]

        # Try splitting by double newlines (paragraphs)
        paragraphs = re.split(r'\n\n+', text)

        if len(paragraphs) == 1:
            # No paragraph breaks, split by sentences
            sentences = re.split(r'(?<=[.!?])\s+', text)
            chunks = []
            current = ""

            for sentence in sentences:
                if len(current) + len(sentence) + 1 <= max_size:
                    current = f"{current} {sentence}" if current else sentence
                else:
                    if current:
                        chunks.append(current.strip())
                    current = sentence

            if current:
                chunks.append(current.strip())

            return chunks if chunks else [text[:max_size]]

        # Merge paragraphs into chunks
        chunks = []
        current = ""

        for para in paragraphs:
            if len(current) + len(para) + 2 <= max_size:
                current = f"{current}\n\n{para}" if current else para
            else:
                if current:
                    chunks.append(current.strip())
                # If single paragraph is too large, split it
                if len(para) > max_size:
                    sub_chunks = self._split_oversized(para, max_size)
                    chunks.extend(sub_chunks)
                    current = ""
                else:
                    current = para

        if current:
            chunks.append(current.strip())

        return chunks

    def _fallback_chunking(
        self,
        doc,
        metadata: RUSHPolicyMetadata,
        source_file: str
    ) -> List[PolicyChunk]:
        """Fallback chunking if HierarchicalChunker fails."""
        try:
            full_text = doc.export_to_markdown()
        except Exception:
            logger.error(f"Cannot export document to markdown: {source_file}")
            return []

        chunks = []
        chunk_counter = 0

        # Simple paragraph-based chunking
        sub_texts = self._split_oversized(full_text, self.max_chunk_size)

        for i, text in enumerate(sub_texts):
            if len(text) >= self.min_chunk_size:
                chunk = self._create_policy_chunk(
                    chunk_id=f"{metadata.reference_number or 'doc'}_{chunk_counter}",
                    text=text,
                    metadata=metadata,
                    section_number="",
                    section_title="",
                    source_file=source_file,
                    chunk_level="semantic",  # Fallback chunks are semantic level
                    parent_chunk_id=None,
                    chunk_index=i
                )
                chunks.append(chunk)
                chunk_counter += 1

        return chunks

    def process_folder(self, folder_path: str) -> Dict:
        """
        Process all PDFs in a folder.

        Returns:
            Dict with 'chunks', 'stats', and 'errors' keys
        """
        all_chunks = []
        stats = {
            'total_docs': 0,
            'total_chunks': 0,
            'avg_chunk_size': 0,
            'min_chunk_size': float('inf'),
            'max_chunk_size': 0,
        }
        errors = []

        folder = Path(folder_path)

        for pdf_file in sorted(folder.glob("*.pdf")):
            try:
                chunks = self.process_pdf(str(pdf_file))
                all_chunks.extend(chunks)
                stats['total_docs'] += 1
                stats['total_chunks'] += len(chunks)

                for chunk in chunks:
                    stats['min_chunk_size'] = min(stats['min_chunk_size'], chunk.char_count)
                    stats['max_chunk_size'] = max(stats['max_chunk_size'], chunk.char_count)

            except Exception as e:
                errors.append({'file': pdf_file.name, 'error': str(e)})

        if all_chunks:
            stats['avg_chunk_size'] = sum(c.char_count for c in all_chunks) // len(all_chunks)

        if stats['min_chunk_size'] == float('inf'):
            stats['min_chunk_size'] = 0

        return {
            'chunks': all_chunks,
            'stats': stats,
            'errors': errors
        }

    def get_backend_info(self) -> Dict[str, str]:
        """Return information about the current backend configuration."""
        info = {
            'backend': 'docling',
            'max_chunk_size': str(self.max_chunk_size),
            'min_chunk_size': str(self.min_chunk_size),
            'docling_available': str(self._docling_available),
            'table_mode': 'TableFormer (ACCURATE)',
        }
        return info


# CLI for testing
if __name__ == "__main__":
    import sys
    import json

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python chunker.py <pdf_path_or_folder> [--json]")
        print("\nThis is the Docling-based implementation.")
        print("Legacy PyMuPDF implementation is in preprocessing/archive/pymupdf_chunker.py")
        sys.exit(1)

    path = sys.argv[1]
    output_json = "--json" in sys.argv

    chunker = PolicyChunker(max_chunk_size=1500, min_chunk_size=100)
    print(f"Backend: {chunker.backend}")
    print(f"Docling available: {chunker._docling_available}")

    if os.path.isfile(path):
        # Single file
        chunks = chunker.process_pdf(path)

        if output_json:
            print(json.dumps([c.to_dict() for c in chunks], indent=2))
        else:
            print(f"\nProcessed: {path}")
            print(f"Chunks: {len(chunks)}")
            for i, chunk in enumerate(chunks):
                print(f"\n--- Chunk {i+1} ---")
                print(f"Citation: {chunk.get_citation()}")
                print(f"Applies To: {chunk.applies_to}")
                print(f"Characters: {chunk.char_count}")
                print(f"Text preview: {chunk.text[:200]}...")

    elif os.path.isdir(path):
        # Folder
        result = chunker.process_folder(path)

        if output_json:
            output = {
                'stats': result['stats'],
                'errors': result['errors'],
                'chunks': [c.to_dict() for c in result['chunks']]
            }
            print(json.dumps(output, indent=2))
        else:
            print(f"\n{'='*60}")
            print("PROCESSING COMPLETE")
            print(f"{'='*60}")
            print(f"Documents: {result['stats']['total_docs']}")
            print(f"Chunks: {result['stats']['total_chunks']}")
            print(f"Avg size: {result['stats']['avg_chunk_size']} chars")
            print(f"Min size: {result['stats']['min_chunk_size']} chars")
            print(f"Max size: {result['stats']['max_chunk_size']} chars")

            if result['errors']:
                print(f"\nErrors ({len(result['errors'])}):")
                for err in result['errors']:
                    print(f"  - {err['file']}: {err['error']}")
