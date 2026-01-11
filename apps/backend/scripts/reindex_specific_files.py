#!/usr/bin/env python3
"""
Targeted re-indexing script for specific files.
Deletes existing chunks and re-processes with updated chunker.
"""
import sys
import os
import tempfile
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '..', '.env'))

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from azure.storage.blob import BlobServiceClient
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from preprocessing.chunker import PolicyChunker
from azure_policy_index import PolicySearchIndex
from app.core.security import escape_odata_string

# Get settings from environment
STORAGE_CONNECTION_STRING = os.environ.get("STORAGE_CONNECTION_STRING")
SEARCH_ENDPOINT = os.environ.get("SEARCH_ENDPOINT")
SEARCH_API_KEY = os.environ.get("SEARCH_API_KEY")

# Files to re-index
TARGET_FILES = [
    "information-systems-general-organizational-policies-artificial-intelligence-policy.pdf",
    "supply-chain-procurement-organizational-policies-product-request-process.pdf",
]

def main():
    print("=" * 60)
    print("TARGETED RE-INDEX: 2 files with title issues")
    print("=" * 60)

    if not STORAGE_CONNECTION_STRING:
        print("ERROR: STORAGE_CONNECTION_STRING not set")
        return

    # Initialize services
    chunker = PolicyChunker()
    search_index = PolicySearchIndex()

    # Connect to blob storage
    blob_service = BlobServiceClient.from_connection_string(STORAGE_CONNECTION_STRING)
    container_client = blob_service.get_container_client("policies-active")

    for filename in TARGET_FILES:
        print(f"\n[Processing] {filename}")

        # 1. Download PDF from blob
        blob_client = container_client.get_blob_client(filename)
        pdf_data = blob_client.download_blob().readall()
        print(f"  Downloaded: {len(pdf_data):,} bytes")

        # 2. Delete existing chunks
        search_client = SearchClient(
            endpoint=SEARCH_ENDPOINT,
            index_name="rush-policies",
            credential=AzureKeyCredential(SEARCH_API_KEY)
        )

        # Find all chunks for this source file
        safe_filename = escape_odata_string(filename)
        results = search_client.search(
            search_text="*",
            filter=f"source_file eq '{safe_filename}'",
            select=["id"]
        )
        chunk_ids = [r["id"] for r in results]

        if chunk_ids:
            print(f"  Deleting {len(chunk_ids)} existing chunks...")
            search_client.delete_documents(documents=[{"id": cid} for cid in chunk_ids])
            print(f"  Deleted {len(chunk_ids)} chunks")
        else:
            print("  No existing chunks found")

        # 3. Re-process with updated chunker (save to temp file first)
        print("  Processing with updated chunker...")
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp.write(pdf_data)
            tmp_path = tmp.name

        try:
            chunks = chunker.process_pdf(tmp_path)
            print(f"  Generated {len(chunks)} new chunks")

            # Fix source_file in all chunks (replace temp path with actual filename)
            for chunk in chunks:
                chunk.source_file = filename
        finally:
            os.unlink(tmp_path)  # Clean up temp file

        # Show extracted title
        if chunks:
            title = chunks[0].policy_title or "NO TITLE"
            print(f"  Title: {title}")

        # 4. Upload new chunks with embeddings
        print("  Uploading with embeddings...")
        stats = search_index.upload_chunks(chunks)
        print(f"  ✓ Uploaded {stats.get('uploaded', len(chunks))} chunks")

    print("\n" + "=" * 60)
    print("COMPLETE: Re-indexed 2 files with fixed titles")
    print("=" * 60)

if __name__ == "__main__":
    main()
