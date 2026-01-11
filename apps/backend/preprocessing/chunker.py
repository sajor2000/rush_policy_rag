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
from typing import List, Optional, Dict, Tuple
from pathlib import Path

# Import extracted dataclasses and enums
from preprocessing.rush_metadata import (
    ProcessingStatus,
    ProcessingResult,
    RUSHPolicyMetadata,
    RUSH_ENTITIES,
    CHECKED_CHARS,
    UNCHECKED_CHARS,
    ENTITY_TO_FIELD,
)
from preprocessing.policy_chunk import PolicyChunk
from preprocessing.checkbox_extractor import (
    extract_applies_to_from_raw_pdf,
    extract_applies_to_from_checkboxes,
    extract_applies_to_from_text,
)
from preprocessing.metadata_extractor import (
    clean_filename,
    extract_page_number,
    extract_section_info,
    extract_fields_from_text,
)

logger = logging.getLogger(__name__)


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
        except Exception as e:
            logger.warning(f"Failed to export markdown from {filename}: {e}")
            full_text = ""

        if not tables and not full_text:
            logger.warning(f"No tables or text found in {filename}")
            metadata.title = clean_filename(filename)
            return metadata

        # FIRST: Extract Applies To using PyMuPDF (most accurate for RUSH PDFs)
        # Do this before Docling text extraction which can truncate the checkbox row
        metadata.applies_to = extract_applies_to_from_raw_pdf(pdf_path)
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
                extract_fields_from_text(table_text, metadata, filename)

            except Exception as e:
                logger.warning(f"Table extraction failed for {filename}: {e}")

        # Fallback/supplement with full text extraction
        if not metadata.title or not metadata.reference_number:
            extract_fields_from_text(full_text, metadata, filename)

        # Fall back to Docling checkbox detection if PyMuPDF didn't extract anything
        if not metadata.applies_to:
            metadata.applies_to = extract_applies_to_from_checkboxes(doc)

        # Set entity boolean fields based on extracted applies_to list
        metadata.set_entity_booleans_from_list()

        # If still no title, use cleaned filename
        if not metadata.title:
            metadata.title = clean_filename(filename)

        return metadata

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
            section_number, section_title = extract_section_info(doc_chunk)

            # Extract page number for PDF navigation
            page_number = extract_page_number(doc_chunk)

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
                            chunk_index=global_chunk_index,
                            page_number=page_number
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
                    chunk_index=global_chunk_index,
                    page_number=page_number
                )
                chunks.append(chunk)
                chunk_counter += 1
                global_chunk_index += 1

        return chunks

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
        chunk_index: int = 0,
        page_number: Optional[int] = None
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
            # Page number for PDF navigation
            page_number=page_number,
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
        except Exception as e:
            logger.error(f"Cannot export document to markdown: {source_file}: {e}")
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
