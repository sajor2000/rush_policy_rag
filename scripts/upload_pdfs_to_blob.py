#!/usr/bin/env python3
"""
Upload PDFs to Azure Blob Storage for the RUSH Policy RAG system.

This script uploads PDF files to the policies-active container in Azure Blob Storage,
enabling the PDF viewing feature in the frontend.

Usage:
    # Upload all PDFs from default location (apps/backend/data/test_pdfs/)
    python scripts/upload_pdfs_to_blob.py

    # Upload from a specific directory
    python scripts/upload_pdfs_to_blob.py /path/to/pdfs

    # Upload a single file
    python scripts/upload_pdfs_to_blob.py /path/to/policy.pdf

    # List existing blobs (dry run)
    python scripts/upload_pdfs_to_blob.py --list

Requirements:
    - STORAGE_CONNECTION_STRING environment variable must be set
    - azure-storage-blob package installed
"""

import ssl_fix  # Corporate proxy SSL fix - must be first import!

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from project root
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

from azure.storage.blob import BlobServiceClient

# Configuration
STORAGE_CONNECTION_STRING = os.environ.get("STORAGE_CONNECTION_STRING")
CONTAINER_NAME = os.environ.get("CONTAINER_NAME", "policies-active")
DEFAULT_PDF_DIR = Path(__file__).resolve().parent.parent / "apps/backend/data/test_pdfs"


def get_blob_service_client() -> BlobServiceClient:
    """Get Azure Blob Service client."""
    if not STORAGE_CONNECTION_STRING:
        print("ERROR: STORAGE_CONNECTION_STRING environment variable not set")
        print("Add it to your .env file or export it in your shell")
        sys.exit(1)
    return BlobServiceClient.from_connection_string(STORAGE_CONNECTION_STRING)


def list_blobs(container_name: str = CONTAINER_NAME) -> list:
    """List all blobs in the container."""
    client = get_blob_service_client()
    container = client.get_container_client(container_name)
    return [blob.name for blob in container.list_blobs()]


def upload_pdf(pdf_path: Path, container_name: str = CONTAINER_NAME, overwrite: bool = True) -> bool:
    """Upload a single PDF to blob storage."""
    if not pdf_path.exists():
        print(f"  ✗ File not found: {pdf_path}")
        return False

    if not pdf_path.suffix.lower() == '.pdf':
        print(f"  ✗ Not a PDF: {pdf_path}")
        return False

    client = get_blob_service_client()
    container = client.get_container_client(container_name)
    blob_client = container.get_blob_client(pdf_path.name)

    try:
        with open(pdf_path, 'rb') as f:
            blob_client.upload_blob(f, overwrite=overwrite)
        print(f"  ✓ {pdf_path.name}")
        return True
    except Exception as e:
        print(f"  ✗ {pdf_path.name}: {e}")
        return False


def upload_directory(dir_path: Path, container_name: str = CONTAINER_NAME) -> dict:
    """Upload all PDFs from a directory."""
    if not dir_path.exists():
        print(f"ERROR: Directory not found: {dir_path}")
        sys.exit(1)

    pdf_files = list(dir_path.glob("*.pdf"))
    if not pdf_files:
        print(f"No PDF files found in {dir_path}")
        return {"uploaded": 0, "failed": 0, "total": 0}

    print(f"Uploading {len(pdf_files)} PDFs to {container_name}...")

    uploaded = 0
    failed = 0

    for pdf in sorted(pdf_files):
        if upload_pdf(pdf, container_name):
            uploaded += 1
        else:
            failed += 1

    return {"uploaded": uploaded, "failed": failed, "total": len(pdf_files)}


def main():
    """Main entry point."""
    args = sys.argv[1:]

    # Handle --list flag
    if "--list" in args:
        print(f"Blobs in {CONTAINER_NAME}:")
        blobs = list_blobs()
        if not blobs:
            print("  (empty)")
        else:
            for blob in sorted(blobs):
                print(f"  {blob}")
        print(f"\nTotal: {len(blobs)} files")
        return

    # Handle --help flag
    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    # Determine source path
    if args and not args[0].startswith("--"):
        source = Path(args[0])
    else:
        source = DEFAULT_PDF_DIR

    # Upload single file or directory
    if source.is_file():
        print(f"Uploading single file to {CONTAINER_NAME}...")
        success = upload_pdf(source)
        sys.exit(0 if success else 1)
    elif source.is_dir():
        result = upload_directory(source)
        print(f"\nDone! Uploaded {result['uploaded']}/{result['total']} files")
        if result['failed'] > 0:
            print(f"Failed: {result['failed']}")
            sys.exit(1)
    else:
        print(f"ERROR: Path not found: {source}")
        sys.exit(1)


if __name__ == "__main__":
    main()
