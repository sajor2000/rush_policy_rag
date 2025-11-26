"""
Archive - Legacy preprocessing implementations.

This folder contains archived implementations preserved for reference.
For new development, use the main modules in the parent directory.

Contents:
- pymupdf_chunker.py: Legacy PyMuPDF-based chunker (replaced by Docling)
"""

from .pymupdf_chunker import PyMuPDFChunker, PolicyChunk as LegacyPolicyChunk

__all__ = ['PyMuPDFChunker', 'LegacyPolicyChunk']
