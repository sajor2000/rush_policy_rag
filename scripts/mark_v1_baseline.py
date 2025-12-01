#!/usr/bin/env python3
"""
Mark V1 Baseline - Retroactively set version 1.0 for all existing documents and chunks.

This script:
1. Updates all blob metadata in policies-active with version 1.0 fields
2. Updates all chunks in Azure AI Search with version 1.0 and ACTIVE status

Run this ONCE after initial ingestion to establish the version baseline.

Usage:
    python scripts/mark_v1_baseline.py          # Dry run (no changes)
    python scripts/mark_v1_baseline.py --apply  # Apply changes
"""

import ssl_fix  # Corporate proxy SSL fix - must be first import!

import os
import sys
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "backend"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from azure.storage.blob import BlobServiceClient
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
STORAGE_CONNECTION_STRING = os.environ.get("STORAGE_CONNECTION_STRING")
CONTAINER_NAME = os.environ.get("CONTAINER_NAME", "policies-active")
SEARCH_ENDPOINT = os.environ.get("SEARCH_ENDPOINT")
SEARCH_API_KEY = os.environ.get("SEARCH_API_KEY")
INDEX_NAME = "rush-policies"

# Version 1.0 baseline values
V1_VERSION_NUMBER = "1.0"
V1_VERSION_SEQUENCE = 1
V1_POLICY_STATUS = "ACTIVE"


def update_blob_metadata(apply: bool = False) -> Dict[str, int]:
    """Update all blob metadata with version 1.0 fields."""
    if not STORAGE_CONNECTION_STRING:
        logger.error("STORAGE_CONNECTION_STRING not set")
        return {"error": 1}

    blob_service = BlobServiceClient.from_connection_string(STORAGE_CONNECTION_STRING)
    container_client = blob_service.get_container_client(CONTAINER_NAME)

    stats = {
        "total_blobs": 0,
        "updated": 0,
        "already_versioned": 0,
        "errors": 0
    }

    current_time = datetime.now().isoformat()

    logger.info(f"\n{'='*60}")
    logger.info(f"Updating blob metadata in container: {CONTAINER_NAME}")
    logger.info(f"{'='*60}")

    for blob in container_client.list_blobs(include=['metadata']):
        if not blob.name.endswith('.pdf'):
            continue

        stats["total_blobs"] += 1
        metadata = blob.metadata or {}

        # Check if already versioned
        if metadata.get("version_number"):
            logger.debug(f"  Already versioned: {blob.name} (v{metadata['version_number']})")
            stats["already_versioned"] += 1
            continue

        # Prepare v1.0 metadata
        new_metadata = {
            **metadata,
            "version_number": V1_VERSION_NUMBER,
            "version_sequence": str(V1_VERSION_SEQUENCE),
            "effective_date": metadata.get("processed_date", current_time),
            "policy_status": V1_POLICY_STATUS,
        }

        if apply:
            try:
                blob_client = container_client.get_blob_client(blob.name)
                blob_client.set_blob_metadata(new_metadata)
                logger.info(f"  ✓ Updated: {blob.name}")
                stats["updated"] += 1
            except Exception as e:
                logger.error(f"  ✗ Failed: {blob.name} - {e}")
                stats["errors"] += 1
        else:
            logger.info(f"  [DRY RUN] Would update: {blob.name}")
            stats["updated"] += 1

    return stats


def update_search_chunks(apply: bool = False) -> Dict[str, int]:
    """Update all chunks in Azure AI Search with version 1.0 fields."""
    if not SEARCH_ENDPOINT or not SEARCH_API_KEY:
        logger.error("SEARCH_ENDPOINT or SEARCH_API_KEY not set")
        return {"error": 1}

    search_client = SearchClient(
        endpoint=SEARCH_ENDPOINT,
        index_name=INDEX_NAME,
        credential=AzureKeyCredential(SEARCH_API_KEY)
    )

    stats = {
        "total_chunks": 0,
        "updated": 0,
        "already_versioned": 0,
        "errors": 0,
        "batches": 0
    }

    # Use proper Azure DateTimeOffset format (ISO 8601 with Z suffix)
    current_time = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    batch_size = 100

    logger.info(f"\n{'='*60}")
    logger.info(f"Updating chunks in search index: {INDEX_NAME}")
    logger.info(f"{'='*60}")

    # Search for all chunks - use pagination to get beyond 1000 limit
    # Azure AI Search returns max 1000 per request
    try:
        chunks_to_update: List[Dict] = []
        skip = 0
        page_size = 1000
        has_more = True

        while has_more:
            results = search_client.search(
                search_text="*",
                select=["id", "source_file", "version_number", "policy_status"],
                top=page_size,
                skip=skip,
                include_total_count=True
            )

            page_count = 0
            for result in results:
                page_count += 1
                stats["total_chunks"] += 1

                # Check if already versioned
                if result.get("version_number") and result.get("policy_status") == "ACTIVE":
                    stats["already_versioned"] += 1
                    continue

                # Prepare update document
                update_doc = {
                    "id": result["id"],
                    "version_number": V1_VERSION_NUMBER,
                    "version_sequence": V1_VERSION_SEQUENCE,
                    "version_date": current_time,
                    "effective_date": current_time,
                    "policy_status": V1_POLICY_STATUS,
                }
                chunks_to_update.append(update_doc)

                # Process in batches
                if len(chunks_to_update) >= batch_size:
                    if apply:
                        try:
                            search_client.merge_documents(documents=chunks_to_update)
                            stats["updated"] += len(chunks_to_update)
                            stats["batches"] += 1
                            logger.info(f"  ✓ Batch {stats['batches']}: Updated {len(chunks_to_update)} chunks")
                        except Exception as e:
                            logger.error(f"  ✗ Batch failed: {e}")
                            stats["errors"] += len(chunks_to_update)
                    else:
                        logger.info(f"  [DRY RUN] Batch {stats['batches'] + 1}: Would update {len(chunks_to_update)} chunks")
                        stats["updated"] += len(chunks_to_update)
                        stats["batches"] += 1

                    chunks_to_update = []

            # Check if we got a full page (more might exist)
            if page_count < page_size:
                has_more = False
            else:
                skip += page_size
                logger.info(f"  Fetching next page (skip={skip})...")

        # Process remaining chunks
        if chunks_to_update:
            if apply:
                try:
                    search_client.merge_documents(documents=chunks_to_update)
                    stats["updated"] += len(chunks_to_update)
                    stats["batches"] += 1
                    logger.info(f"  ✓ Final batch: Updated {len(chunks_to_update)} chunks")
                except Exception as e:
                    logger.error(f"  ✗ Final batch failed: {e}")
                    stats["errors"] += len(chunks_to_update)
            else:
                logger.info(f"  [DRY RUN] Final batch: Would update {len(chunks_to_update)} chunks")
                stats["updated"] += len(chunks_to_update)
                stats["batches"] += 1

    except Exception as e:
        logger.error(f"Search query failed: {e}")
        stats["errors"] = 1

    return stats


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Mark all existing documents and chunks as version 1.0"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes (default is dry run)"
    )
    args = parser.parse_args()

    apply = args.apply

    print(f"""
{'='*60}
MARK V1 BASELINE
{'='*60}
Mode: {'APPLY CHANGES' if apply else 'DRY RUN (no changes)'}
Container: {CONTAINER_NAME}
Index: {INDEX_NAME}
{'='*60}
""")

    if not apply:
        print("NOTE: This is a dry run. Use --apply to make changes.\n")

    # Update blob metadata
    blob_stats = update_blob_metadata(apply)

    # Update search chunks
    chunk_stats = update_search_chunks(apply)

    # Print summary
    print(f"""
{'='*60}
SUMMARY
{'='*60}

Blob Storage ({CONTAINER_NAME}):
  Total PDFs: {blob_stats.get('total_blobs', 0)}
  Updated to v1.0: {blob_stats.get('updated', 0)}
  Already versioned: {blob_stats.get('already_versioned', 0)}
  Errors: {blob_stats.get('errors', 0)}

Search Index ({INDEX_NAME}):
  Total chunks: {chunk_stats.get('total_chunks', 0)}
  Updated to v1.0: {chunk_stats.get('updated', 0)}
  Already versioned: {chunk_stats.get('already_versioned', 0)}
  Batches processed: {chunk_stats.get('batches', 0)}
  Errors: {chunk_stats.get('errors', 0)}

{'='*60}
""")

    if not apply:
        print("To apply these changes, run: python scripts/mark_v1_baseline.py --apply\n")

    return 0 if (blob_stats.get('errors', 0) == 0 and chunk_stats.get('errors', 0) == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
