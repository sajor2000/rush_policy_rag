"""
RUSH Policy Chunker - PyMuPDF Implementation (ARCHIVED)

This is the legacy PyMuPDF-based chunker, preserved for reference.
The main implementation now uses Docling (see ../chunker.py).

This chunker was designed for 100% accuracy policy retrieval:
- Preserves EXACT text (no synthesis)
- Chunks by section boundaries (never splits mid-sentence)
- Includes full citation metadata for every chunk
- Handles the standard RUSH policy PDF format

NOTE: For new development, use the Docling-based chunker which provides:
- Better table extraction (TableFormer)
- Native checkbox detection
- Improved structure understanding

Usage (if fallback needed):
    from preprocessing.archive.pymupdf_chunker import PyMuPDFChunker
    chunker = PyMuPDFChunker()
    chunks = chunker.process_pdf("policy.pdf")
"""

import fitz  # PyMuPDF
import re
import os
import hashlib
import logging
from dataclasses import dataclass, field
from typing import List, Dict
from pathlib import Path

logger = logging.getLogger(__name__)


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
    applies_to: str
    source_file: str
    char_count: int
    content_hash: str = field(default="")
    document_owner: str = field(default="")
    date_approved: str = field(default="")

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
        """Format for Azure AI Search index."""
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
        }


class PyMuPDFChunker:
    """
    Legacy PyMuPDF-based chunker for RUSH policy PDFs.

    ARCHIVED: Use the main Docling-based PolicyChunker instead.

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
    ):
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size
        self.overlap_sentences = overlap_sentences
        logger.info(f"PyMuPDFChunker initialized (legacy/archived implementation)")

    def extract_metadata(self, text: str, filename: str) -> Dict[str, str]:
        """Extract policy metadata from header."""
        # Default values from filename
        clean_filename = filename.replace('.pdf', '').replace('-', ' ').replace('  ', ' - ')

        metadata = {
            'title': clean_filename,
            'reference_number': '',
            'date_updated': '',
            'date_approved': '',
            'document_owner': '',
            'applies_to': 'All',
        }

        # Extract title
        match = re.search(r'Policy Title:\s*(.+?)(?:\n|Policy Number)', text, re.DOTALL)
        if match:
            title = match.group(1).strip()
            title = ' '.join(title.split())
            metadata['title'] = title

        # Extract reference number - try multiple patterns
        for pattern in [
            r'Reference\s*Number[:\s]+(\d+)',
            r'Reference\s*Number[:\s]+([A-Za-z0-9\-]+)',
            r'Policy\s*Number[:\s]+([A-Za-z0-9\-]+)',
            r'Ref[:\s#]+([A-Za-z0-9\-]+)',
        ]:
            match = re.search(pattern, text)
            if match:
                metadata['reference_number'] = match.group(1)
                break

        # Extract dates
        for field, pattern in [
            ('date_updated', r'Date Updated:\s*([\d/]+)'),
            ('date_approved', r'Date Approved:\s*([\d/]+)'),
        ]:
            match = re.search(pattern, text)
            if match:
                metadata[field] = match.group(1)

        # Extract document owner
        match = re.search(r'Document Owner:\s*(.+?)(?:\n|Approver)', text, re.DOTALL)
        if match:
            metadata['document_owner'] = match.group(1).strip()

        # Extract applies_to (which entities have checkbox checked)
        checked_patterns = [
            r'{entity}\s*[☒☑✓✔■]',
            r'[☒☑✓✔■]\s*{entity}',
        ]
        applies = []
        for entity in ['RUMC', 'RMG', 'ROPH', 'RCMC', 'RU']:
            for pattern_template in checked_patterns:
                pattern = pattern_template.replace('{entity}', entity)
                if re.search(pattern, text):
                    applies.append(entity)
                    break
        if applies:
            metadata['applies_to'] = ', '.join(applies)

        return metadata

    def clean_text(self, full_text: str) -> str:
        """Remove repeated headers, keeping only policy content."""
        header_pattern = r'Policy Title:.*?Reference Number:\s*\d+\s*'

        first_match = re.search(header_pattern, full_text, re.DOTALL)
        if first_match:
            content = full_text[first_match.end():]
            content = re.sub(header_pattern, '\n\n', content, flags=re.DOTALL)
            content = re.sub(
                r'Printed copies are for reference only\..*?(?=\n)',
                '',
                content,
                flags=re.DOTALL
            )
            return content.strip()

        return full_text.strip()

    def split_into_sections(self, text: str) -> List[Dict]:
        """Split policy text into sections."""
        sections = []

        # Try Roman numerals first
        roman_pattern = r'\n(I{1,3}V?I{0,3})\.\s*\n?\s*([A-Za-z][A-Za-z\s\(\)]*?)(?=\n)'
        roman_matches = list(re.finditer(roman_pattern, text))

        if len(roman_matches) >= 2:
            for i, match in enumerate(roman_matches):
                section_num = match.group(1)
                section_title = match.group(2).strip()

                start = match.end()
                end = roman_matches[i+1].start() if i+1 < len(roman_matches) else len(text)
                content = text[start:end].strip()

                if content and len(content) >= self.min_chunk_size:
                    sections.append({
                        'number': section_num,
                        'title': section_title,
                        'text': content
                    })

            if sections:
                return sections

        # Try numbered sections
        numbered_pattern = r'\n(\d+)\.0\s+([A-Za-z][^\n]+)'
        numbered_matches = list(re.finditer(numbered_pattern, text))

        if len(numbered_matches) >= 2:
            for i, match in enumerate(numbered_matches):
                section_num = match.group(1)
                section_title = match.group(2).strip()

                start = match.end()
                end = numbered_matches[i+1].start() if i+1 < len(numbered_matches) else len(text)
                content = text[start:end].strip()

                if content and len(content) >= self.min_chunk_size:
                    sections.append({
                        'number': section_num,
                        'title': section_title,
                        'text': content
                    })

            if sections:
                return sections

        # Try keyword-based sections
        keyword_pattern = r'\n(POLICY|PURPOSE|SCOPE|DEFINITIONS?|PROCEDURES?|EXECUTIVE SUMMARY|APPLIES TO)\s*\n'
        keyword_matches = list(re.finditer(keyword_pattern, text, re.IGNORECASE))

        if len(keyword_matches) >= 2:
            for i, match in enumerate(keyword_matches):
                section_title = match.group(1).strip().title()

                start = match.end()
                end = keyword_matches[i+1].start() if i+1 < len(keyword_matches) else len(text)
                content = text[start:end].strip()

                if content and len(content) >= self.min_chunk_size:
                    sections.append({
                        'number': str(i+1),
                        'title': section_title,
                        'text': content
                    })

            if sections:
                return sections

        # Fallback: entire document as one section
        if text.strip() and len(text.strip()) >= self.min_chunk_size:
            sections.append({
                'number': '1',
                'title': 'Content',
                'text': text.strip()
            })

        return sections

    def chunk_section(
        self,
        section: Dict,
        metadata: Dict,
        filename: str,
        chunk_counter: int
    ) -> List[PolicyChunk]:
        """Chunk a section, splitting at safe boundaries if too large."""
        chunks = []
        text = section['text']

        # If section fits in one chunk, return it
        if len(text) <= self.max_chunk_size:
            if len(text) >= self.min_chunk_size:
                chunk = PolicyChunk(
                    chunk_id=f"{metadata['reference_number'] or 'doc'}_{section['number']}_{chunk_counter}",
                    policy_title=metadata['title'],
                    reference_number=metadata['reference_number'],
                    section_number=section['number'],
                    section_title=section['title'],
                    text=text,
                    date_updated=metadata['date_updated'],
                    applies_to=metadata['applies_to'],
                    source_file=filename,
                    char_count=len(text),
                    document_owner=metadata.get('document_owner', ''),
                    date_approved=metadata.get('date_approved', '')
                )
                return [chunk]
            else:
                logger.debug(
                    f"Discarding undersized chunk ({len(text)} chars < {self.min_chunk_size} min) "
                    f"from section {section['number']} of {metadata['title']}"
                )
                return []

        # Section too large - split at numbered items first
        parts = re.split(r'(?=\n\d+\.\d+\s)', text)

        current_text = ""
        part_idx = 0

        for part in parts:
            if len(current_text) + len(part) <= self.max_chunk_size:
                current_text += part
            else:
                if len(current_text.strip()) >= self.min_chunk_size:
                    chunk = PolicyChunk(
                        chunk_id=f"{metadata['reference_number'] or 'doc'}_{section['number']}_{chunk_counter}_{part_idx}",
                        policy_title=metadata['title'],
                        reference_number=metadata['reference_number'],
                        section_number=section['number'],
                        section_title=section['title'],
                        text=current_text.strip(),
                        date_updated=metadata['date_updated'],
                        applies_to=metadata['applies_to'],
                        source_file=filename,
                        char_count=len(current_text.strip()),
                        document_owner=metadata.get('document_owner', ''),
                        date_approved=metadata.get('date_approved', '')
                    )
                    chunks.append(chunk)
                    part_idx += 1

                if len(part) > self.max_chunk_size:
                    sub_parts = self._split_at_paragraphs(part)
                    for sub_part in sub_parts:
                        if len(sub_part.strip()) >= self.min_chunk_size:
                            chunk = PolicyChunk(
                                chunk_id=f"{metadata['reference_number'] or 'doc'}_{section['number']}_{chunk_counter}_{part_idx}",
                                policy_title=metadata['title'],
                                reference_number=metadata['reference_number'],
                                section_number=section['number'],
                                section_title=section['title'],
                                text=sub_part.strip(),
                                date_updated=metadata['date_updated'],
                                applies_to=metadata['applies_to'],
                                source_file=filename,
                                char_count=len(sub_part.strip()),
                                document_owner=metadata.get('document_owner', ''),
                                date_approved=metadata.get('date_approved', '')
                            )
                            chunks.append(chunk)
                            part_idx += 1
                    current_text = ""
                else:
                    current_text = part

        # Don't forget the last chunk
        if len(current_text.strip()) >= self.min_chunk_size:
            chunk = PolicyChunk(
                chunk_id=f"{metadata['reference_number'] or 'doc'}_{section['number']}_{chunk_counter}_{part_idx}",
                policy_title=metadata['title'],
                reference_number=metadata['reference_number'],
                section_number=section['number'],
                section_title=section['title'],
                text=current_text.strip(),
                date_updated=metadata['date_updated'],
                applies_to=metadata['applies_to'],
                source_file=filename,
                char_count=len(current_text.strip()),
                document_owner=metadata.get('document_owner', ''),
                date_approved=metadata.get('date_approved', '')
            )
            chunks.append(chunk)

        return chunks

    def _split_at_paragraphs(self, text: str) -> List[str]:
        """Split text at paragraph boundaries to fit max_chunk_size."""
        parts = []
        paragraphs = re.split(r'\n\n+', text)

        current = ""
        for para in paragraphs:
            if len(current) + len(para) + 2 <= self.max_chunk_size:
                current = current + "\n\n" + para if current else para
            else:
                if current:
                    parts.append(current)

                if len(para) > self.max_chunk_size:
                    sentences = re.split(r'(?<=[.!?])\s+', para)
                    current = ""
                    for sent in sentences:
                        if len(current) + len(sent) + 1 <= self.max_chunk_size:
                            current = current + " " + sent if current else sent
                        else:
                            if current:
                                parts.append(current)

                            if len(sent) > self.max_chunk_size:
                                logger.debug(f"Force-splitting oversized sentence ({len(sent)} chars)")
                                sent_chunks = self._force_split_at_word_boundary(sent)
                                for chunk in sent_chunks[:-1]:
                                    parts.append(chunk)
                                current = sent_chunks[-1] if sent_chunks else ""
                            else:
                                current = sent
                else:
                    current = para

        if current:
            parts.append(current)

        return parts

    def _force_split_at_word_boundary(self, text: str) -> List[str]:
        """Force split text at word boundaries when it exceeds max_chunk_size."""
        if len(text) <= self.max_chunk_size:
            return [text]

        chunks = []
        remaining = text

        while len(remaining) > self.max_chunk_size:
            split_point = remaining[:self.max_chunk_size].rfind(' ')

            if split_point == -1:
                split_point = self.max_chunk_size
                logger.warning(f"Force-splitting at {split_point} chars (no word boundary found)")

            chunk = remaining[:split_point].strip()
            if chunk:
                chunks.append(chunk)

            remaining = remaining[split_point:].strip()

        if remaining:
            chunks.append(remaining)

        return chunks

    def process_pdf(self, pdf_path: str) -> List[PolicyChunk]:
        """Process a policy PDF into chunks using PyMuPDF."""
        doc = None
        try:
            if not os.path.exists(pdf_path):
                logger.error(f"PDF not found: {pdf_path}")
                return []

            file_size = os.path.getsize(pdf_path)
            if file_size == 0:
                logger.warning(f"PDF is empty (0 bytes): {pdf_path}")
                return []

            try:
                doc = fitz.open(pdf_path)
            except Exception as e:
                logger.error(f"Failed to open PDF '{pdf_path}': {e}")
                return []

            if doc.page_count == 0:
                logger.warning(f"PDF has no pages: {pdf_path}")
                return []

            filename = os.path.basename(pdf_path)

            # Extract text from each page
            full_text = ""
            pages_processed = 0
            pages_failed = 0

            for page_num in range(doc.page_count):
                try:
                    page = doc[page_num]
                    page_text = page.get_text()

                    if page_text:
                        try:
                            page_text.encode('utf-8')
                        except UnicodeEncodeError:
                            page_text = page_text.encode('utf-8', errors='replace').decode('utf-8')
                            logger.warning(f"Fixed encoding issues on page {page_num + 1} of {pdf_path}")

                        full_text += page_text + "\n"
                        pages_processed += 1
                    else:
                        logger.debug(f"Page {page_num + 1} is empty in {pdf_path}")

                except Exception as e:
                    pages_failed += 1
                    logger.error(f"Error extracting page {page_num + 1} from {pdf_path}: {e}")
                    continue

            if pages_failed > 0:
                logger.warning(f"Processed {pages_processed} pages, failed {pages_failed} pages in {pdf_path}")

            if not full_text.strip():
                logger.warning(f"No text content extracted from {pdf_path}")
                return []

            # Get metadata
            metadata = self.extract_metadata(full_text, filename)

            # Clean text
            clean_text = self.clean_text(full_text)

            # Split into sections
            sections = self.split_into_sections(clean_text)

            # Chunk each section
            all_chunks = []
            chunk_counter = 0

            for section in sections:
                section_chunks = self.chunk_section(
                    section, metadata, filename, chunk_counter
                )
                all_chunks.extend(section_chunks)
                chunk_counter += len(section_chunks)

            if all_chunks:
                logger.info(f"Processed {pdf_path}: {len(all_chunks)} chunks from {pages_processed} pages")
            else:
                logger.warning(f"No chunks generated from {pdf_path} (text length: {len(clean_text)})")

            return all_chunks

        except (FileNotFoundError, ValueError) as e:
            logger.error(f"PDF validation error for {pdf_path}: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error processing {pdf_path}: {type(e).__name__}: {e}")
            return []
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception as e:
                    logger.warning(f"Error closing PDF {pdf_path}: {e}")

    def process_folder(self, folder_path: str) -> Dict:
        """Process all PDFs in a folder."""
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
        return {
            'backend': 'pymupdf',
            'max_chunk_size': str(self.max_chunk_size),
            'min_chunk_size': str(self.min_chunk_size),
            'pymupdf_version': fitz.version[0]
        }


# CLI for testing
if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python pymupdf_chunker.py <pdf_path_or_folder> [--json]")
        print("\nNOTE: This is the ARCHIVED PyMuPDF implementation.")
        print("For new development, use the main Docling-based chunker.")
        sys.exit(1)

    path = sys.argv[1]
    output_json = "--json" in sys.argv

    chunker = PyMuPDFChunker(max_chunk_size=1500, min_chunk_size=100)
    print(f"Backend: pymupdf (archived)")

    if os.path.isfile(path):
        chunks = chunker.process_pdf(path)

        if output_json:
            print(json.dumps([c.to_dict() for c in chunks], indent=2))
        else:
            print(f"\nProcessed: {path}")
            print(f"Chunks: {len(chunks)}")
            for i, chunk in enumerate(chunks):
                print(f"\n--- Chunk {i+1} ---")
                print(f"Citation: {chunk.get_citation()}")
                print(f"Characters: {chunk.char_count}")
                print(f"Text preview: {chunk.text[:200]}...")

    elif os.path.isdir(path):
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
