#!/usr/bin/env python3
"""
Targeted re-indexing script for specific files.

Deletes existing chunks and re-processes selected PDFs with the current chunker.
By default, write operations are restricted to the active alias unless
--allow-direct-index is provided.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import List

from dotenv import load_dotenv

# Load environment variables from repo root .env
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

# Add backend root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.storage.blob import BlobServiceClient

from app.core.index_safety import (
    DEFAULT_ACTIVE_ALIAS,
    ensure_safe_index_target,
    resolve_index_name,
)
from app.core.security import escape_odata_string
from azure_policy_index import PolicySearchIndex
from preprocessing.chunker import PolicyChunker


DEFAULT_TARGET_FILES = [
    "information-systems-general-organizational-policies-artificial-intelligence-policy.pdf",
    "supply-chain-procurement-organizational-policies-product-request-process.pdf",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Targeted re-index for selected policy files")
    parser.add_argument(
        "--files",
        nargs="+",
        default=DEFAULT_TARGET_FILES,
        help="List of source filenames to re-index",
    )
    parser.add_argument(
        "--container",
        default=os.environ.get("CONTAINER_NAME", "policies-active"),
        help="Blob container containing source PDFs",
    )
    parser.add_argument(
        "--index-name",
        default=None,
        help="Search index target (defaults to SEARCH_INDEX_NAME or active alias)",
    )
    parser.add_argument(
        "--endpoint",
        default=None,
        help="Azure Search endpoint override (defaults to SEARCH_ENDPOINT)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Azure Search API key override (defaults to SEARCH_API_KEY)",
    )
    parser.add_argument(
        "--allow-direct-index",
        action="store_true",
        help="Allow direct writes to non-alias index targets",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    storage_connection_string = os.environ.get("STORAGE_CONNECTION_STRING")
    search_endpoint = args.endpoint or os.environ.get("SEARCH_ENDPOINT")
    search_api_key = args.api_key or os.environ.get("SEARCH_API_KEY")
    index_name = resolve_index_name(args.index_name)

    ensure_safe_index_target(
        index_name,
        allow_direct_index=args.allow_direct_index,
        active_alias=DEFAULT_ACTIVE_ALIAS,
        operation="write",
    )

    if not storage_connection_string:
        raise RuntimeError("STORAGE_CONNECTION_STRING not set")
    if not search_endpoint or not search_api_key:
        raise RuntimeError("SEARCH_ENDPOINT and SEARCH_API_KEY are required")

    print("=" * 60)
    print("TARGETED RE-INDEX")
    print("=" * 60)
    print(f"[INFO] endpoint={search_endpoint}")
    print(f"[INFO] index={index_name}")
    print(f"[INFO] container={args.container}")
    print(f"[INFO] files={len(args.files)}")

    # Initialize services
    chunker = PolicyChunker()
    search_index = PolicySearchIndex(
        index_name=index_name,
        search_endpoint=search_endpoint,
        search_api_key=search_api_key,
    )
    search_client = SearchClient(
        endpoint=search_endpoint,
        index_name=index_name,
        credential=AzureKeyCredential(search_api_key),
    )

    # Connect to blob storage
    blob_service = BlobServiceClient.from_connection_string(storage_connection_string)
    container_client = blob_service.get_container_client(args.container)

    failures: List[str] = []

    for filename in args.files:
        print(f"\n[Processing] {filename}")
        try:
            # 1. Download PDF from blob
            blob_client = container_client.get_blob_client(filename)
            pdf_data = blob_client.download_blob().readall()
            print(f"  Downloaded: {len(pdf_data):,} bytes")

            # 2. Delete existing chunks
            safe_filename = escape_odata_string(filename)
            results = search_client.search(
                search_text="*",
                filter=f"source_file eq '{safe_filename}'",
                select=["id"],
            )
            chunk_ids = [r["id"] for r in results]

            if chunk_ids:
                print(f"  Deleting {len(chunk_ids)} existing chunks...")
                search_client.delete_documents(documents=[{"id": cid} for cid in chunk_ids])
                print(f"  Deleted {len(chunk_ids)} chunks")
            else:
                print("  No existing chunks found")

            # 3. Re-process with updated chunker
            print("  Processing with updated chunker...")
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(pdf_data)
                tmp_path = tmp.name

            try:
                chunks = chunker.process_pdf(tmp_path)
                print(f"  Generated {len(chunks)} new chunks")
                for chunk in chunks:
                    chunk.source_file = filename
            finally:
                Path(tmp_path).unlink(missing_ok=True)

            if chunks:
                title = chunks[0].policy_title or "NO TITLE"
                print(f"  Title: {title}")

            # 4. Upload new chunks with embeddings
            print("  Uploading with embeddings...")
            stats = search_index.upload_chunks(chunks)
            print(f"  Uploaded {stats.get('uploaded', len(chunks))} chunks")
        except Exception as exc:
            failures.append(f"{filename}: {exc}")
            print(f"  FAILED: {exc}")

    print("\n" + "=" * 60)
    if failures:
        print(f"COMPLETE WITH FAILURES ({len(failures)})")
        for failure in failures:
            print(f"  - {failure}")
        print("=" * 60)
        raise SystemExit(1)

    print("COMPLETE: Re-indexed files successfully")
    print("=" * 60)


if __name__ == "__main__":
    main()

