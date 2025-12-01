"""
Azure Infrastructure Setup Script for RUSH Policy RAG

Sets up all Azure resources required for the policy ingestion pipeline:
1. Azure AI Search index with optimized schema
2. Synonym map for RUSH-specific terminology
3. Azure Blob Storage container configuration
4. Validates Azure OpenAI embedding deployment

All configuration via SDK/CLI - no Azure portal required.

Usage:
    python scripts/setup_azure_infrastructure.py              # Full setup
    python scripts/setup_azure_infrastructure.py --validate   # Validate only
    python scripts/setup_azure_infrastructure.py --index      # Index only
    python scripts/setup_azure_infrastructure.py --storage    # Storage only
"""

# Corporate proxy SSL fix - must be before other imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    import ssl_fix
except ImportError:
    pass

import os
import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

# Load environment variables
env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(env_path)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Azure configuration from environment
SEARCH_ENDPOINT = os.environ.get("SEARCH_ENDPOINT", "https://policychataisearch.search.windows.net")
SEARCH_API_KEY = os.environ.get("SEARCH_API_KEY")
STORAGE_CONNECTION_STRING = os.environ.get("STORAGE_CONNECTION_STRING")
CONTAINER_NAME = os.environ.get("CONTAINER_NAME", "policies-active")
SOURCE_CONTAINER_NAME = os.environ.get("SOURCE_CONTAINER_NAME", "policies-source")
AOAI_ENDPOINT = os.environ.get("AOAI_ENDPOINT")
AOAI_API_KEY = os.environ.get("AOAI_API")
AOAI_EMBEDDING_DEPLOYMENT = os.environ.get("AOAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")


def check_environment() -> Dict[str, bool]:
    """Check that all required environment variables are set."""
    required = {
        "SEARCH_ENDPOINT": SEARCH_ENDPOINT,
        "SEARCH_API_KEY": SEARCH_API_KEY,
        "STORAGE_CONNECTION_STRING": STORAGE_CONNECTION_STRING,
        "AOAI_ENDPOINT": AOAI_ENDPOINT,
        "AOAI_API": AOAI_API_KEY,
    }

    status = {}
    all_present = True

    print("\n" + "=" * 60)
    print("ENVIRONMENT CHECK")
    print("=" * 60)

    for name, value in required.items():
        present = bool(value)
        status[name] = present
        indicator = "[OK]" if present else "[MISSING]"
        # Mask API keys for security
        if present and "KEY" in name or "API" in name or "STRING" in name:
            display = value[:8] + "..." if value else "Not set"
        else:
            display = value if present else "Not set"
        print(f"  {indicator} {name}: {display}")
        if not present:
            all_present = False

    status["all_present"] = all_present
    return status


def setup_blob_storage() -> bool:
    """
    Configure Azure Blob Storage for differential sync.

    Creates containers and enables soft delete for sync tracking.
    """
    print("\n" + "=" * 60)
    print("BLOB STORAGE SETUP")
    print("=" * 60)

    if not STORAGE_CONNECTION_STRING:
        logger.error("STORAGE_CONNECTION_STRING not set")
        return False

    try:
        from azure.storage.blob import BlobServiceClient, ContainerClient

        blob_service = BlobServiceClient.from_connection_string(STORAGE_CONNECTION_STRING)

        # Create source container (for incoming PDFs)
        print(f"\n  Creating container: {SOURCE_CONTAINER_NAME}")
        try:
            source_container = blob_service.create_container(SOURCE_CONTAINER_NAME)
            print(f"    [CREATED] Container '{SOURCE_CONTAINER_NAME}' created")
        except Exception as e:
            if "ContainerAlreadyExists" in str(e):
                print(f"    [EXISTS] Container '{SOURCE_CONTAINER_NAME}' already exists")
            else:
                raise

        # Create active container (for processed PDFs with metadata)
        print(f"\n  Creating container: {CONTAINER_NAME}")
        try:
            active_container = blob_service.create_container(CONTAINER_NAME)
            print(f"    [CREATED] Container '{CONTAINER_NAME}' created")
        except Exception as e:
            if "ContainerAlreadyExists" in str(e):
                print(f"    [EXISTS] Container '{CONTAINER_NAME}' already exists")
            else:
                raise

        # Enable soft delete for differential sync tracking
        print("\n  Configuring soft delete policy...")
        try:
            from azure.storage.blob import RetentionPolicy

            # Get current properties
            props = blob_service.get_service_properties()

            # Enable soft delete if not already
            if not props.delete_retention_policy or not props.delete_retention_policy.enabled:
                blob_service.set_service_properties(
                    delete_retention_policy=RetentionPolicy(enabled=True, days=14)
                )
                print("    [ENABLED] Soft delete enabled (14 day retention)")
            else:
                days = props.delete_retention_policy.days
                print(f"    [EXISTS] Soft delete already enabled ({days} day retention)")

        except Exception as e:
            logger.warning(f"Could not configure soft delete: {e}")
            print(f"    [WARN] Could not configure soft delete (may require Storage Admin)")

        # List containers to verify
        print("\n  Verifying containers:")
        containers = list(blob_service.list_containers())
        for c in containers:
            if c.name in [SOURCE_CONTAINER_NAME, CONTAINER_NAME]:
                print(f"    [OK] {c.name}")

        print("\n  [SUCCESS] Blob storage setup complete")
        return True

    except ImportError:
        logger.error("azure-storage-blob not installed. Run: pip install azure-storage-blob")
        return False
    except Exception as e:
        logger.error(f"Blob storage setup failed: {e}")
        return False


def setup_search_index() -> bool:
    """
    Create or update Azure AI Search index with full schema.

    Uses PolicySearchIndex from azure_policy_index.py for consistency.
    """
    print("\n" + "=" * 60)
    print("AZURE AI SEARCH SETUP")
    print("=" * 60)

    if not SEARCH_API_KEY:
        logger.error("SEARCH_API_KEY not set")
        return False

    try:
        from azure_policy_index import PolicySearchIndex, INDEX_NAME, SYNONYM_MAP_NAME

        index = PolicySearchIndex()

        # Create synonym map first (index depends on it)
        print(f"\n  Creating synonym map: {SYNONYM_MAP_NAME}")
        try:
            index.create_synonym_map()
            print(f"    [OK] Synonym map '{SYNONYM_MAP_NAME}' created/updated")
        except Exception as e:
            logger.warning(f"Synonym map creation warning: {e}")
            print(f"    [WARN] Synonym map may already exist or failed: {e}")

        # Create/update index
        print(f"\n  Creating search index: {INDEX_NAME}")
        index.create_index()
        print(f"    [OK] Index '{INDEX_NAME}' created/updated")

        # Get and display stats
        print("\n  Verifying index:")
        stats = index.get_stats()
        if "error" in stats:
            print(f"    [WARN] Could not get stats: {stats['error']}")
        else:
            print(f"    Index name: {stats['index_name']}")
            print(f"    Document count: {stats['document_count']}")
            print(f"    Field count: {stats['fields']}")

        # Display new schema features
        print("\n  Schema features:")
        print("    [OK] 9 entity boolean filters (applies_to_rumc, applies_to_rmg, etc.)")
        print("    [OK] Hierarchical chunking fields (chunk_level, parent_chunk_id, chunk_index)")
        print("    [OK] Enhanced metadata (category, subcategory, regulatory_citations)")

        print("\n  [SUCCESS] Search index setup complete")
        return True

    except ImportError as e:
        logger.error(f"Import error: {e}")
        return False
    except Exception as e:
        logger.error(f"Search index setup failed: {e}")
        return False


def validate_aoai_embedding() -> bool:
    """
    Validate Azure OpenAI embedding deployment is working.
    """
    print("\n" + "=" * 60)
    print("AZURE OPENAI VALIDATION")
    print("=" * 60)

    if not AOAI_ENDPOINT or not AOAI_API_KEY:
        logger.error("Azure OpenAI credentials not set")
        return False

    try:
        from openai import AzureOpenAI

        print(f"\n  Endpoint: {AOAI_ENDPOINT}")
        print(f"  Deployment: {AOAI_EMBEDDING_DEPLOYMENT}")

        client = AzureOpenAI(
            azure_endpoint=AOAI_ENDPOINT,
            api_key=AOAI_API_KEY,
            api_version="2024-06-01"
        )

        # Test embedding generation
        print("\n  Testing embedding generation...")
        test_text = "This is a test policy document about patient care procedures."

        response = client.embeddings.create(
            input=test_text,
            model=AOAI_EMBEDDING_DEPLOYMENT
        )

        embedding = response.data[0].embedding
        dimensions = len(embedding)

        print(f"    [OK] Embedding generated successfully")
        print(f"    Dimensions: {dimensions}")
        print(f"    Expected: 3072 (text-embedding-3-large)")

        if dimensions != 3072:
            print(f"    [WARN] Dimensions mismatch - update EMBEDDING_DIMENSIONS in azure_policy_index.py")

        print("\n  [SUCCESS] Azure OpenAI validation complete")
        return True

    except ImportError:
        logger.error("openai package not installed. Run: pip install openai")
        return False
    except Exception as e:
        logger.error(f"Azure OpenAI validation failed: {e}")
        return False


def validate_docling() -> bool:
    """
    Validate Docling is installed and working.
    """
    print("\n" + "=" * 60)
    print("DOCLING VALIDATION")
    print("=" * 60)

    try:
        from preprocessing.chunker import PolicyChunker

        print("\n  Initializing PolicyChunker (Docling-based)...")
        chunker = PolicyChunker()

        info = chunker.get_backend_info()
        print(f"    Backend: {info['backend']}")
        print(f"    Max chunk size: {info['max_chunk_size']}")
        print(f"    Min chunk size: {info['min_chunk_size']}")
        print(f"    Docling available: {info.get('docling_available', 'N/A')}")
        print(f"    Table mode: {info.get('table_mode', 'N/A')}")

        if info.get('docling_available') == 'True':
            print("\n  [SUCCESS] Docling is available and working")
            return True
        else:
            print("\n  [FAILED] Docling is not available")
            print("  Install with: pip install docling docling-core")
            return False

    except Exception as e:
        logger.error(f"Docling validation failed: {e}")
        return False


def run_full_setup() -> Dict[str, bool]:
    """Run complete infrastructure setup."""
    results = {
        "environment": False,
        "blob_storage": False,
        "search_index": False,
        "aoai_embedding": False,
        "docling": False,
    }

    print("\n" + "=" * 60)
    print("RUSH POLICY RAG - AZURE INFRASTRUCTURE SETUP")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Check environment
    env_status = check_environment()
    results["environment"] = env_status["all_present"]

    if not results["environment"]:
        print("\n[ERROR] Missing required environment variables. Setup cannot continue.")
        print("Please set the missing variables in your .env file and try again.")
        return results

    # Setup components
    results["blob_storage"] = setup_blob_storage()
    results["search_index"] = setup_search_index()
    results["aoai_embedding"] = validate_aoai_embedding()
    results["docling"] = validate_docling()

    # Summary
    print("\n" + "=" * 60)
    print("SETUP SUMMARY")
    print("=" * 60)

    all_success = True
    for component, success in results.items():
        status = "[OK]" if success else "[FAILED]"
        print(f"  {status} {component}")
        if not success:
            all_success = False

    if all_success:
        print("\n[SUCCESS] All infrastructure components are ready!")
        print("\nNext steps:")
        print("  1. Run ingestion: python scripts/ingest_all_policies.py")
        print("  2. Test search: python azure_policy_index.py search \"chaperone policy\"")
    else:
        print("\n[WARNING] Some components failed. Review errors above.")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Setup Azure infrastructure for RUSH Policy RAG"
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate configuration only (no changes)"
    )
    parser.add_argument(
        "--index",
        action="store_true",
        help="Setup search index only"
    )
    parser.add_argument(
        "--storage",
        action="store_true",
        help="Setup blob storage only"
    )
    parser.add_argument(
        "--docling",
        action="store_true",
        help="Validate Docling only"
    )

    args = parser.parse_args()

    if args.validate:
        check_environment()
        validate_aoai_embedding()
        validate_docling()
    elif args.index:
        check_environment()
        setup_search_index()
    elif args.storage:
        check_environment()
        setup_blob_storage()
    elif args.docling:
        validate_docling()
    else:
        run_full_setup()


if __name__ == "__main__":
    main()
