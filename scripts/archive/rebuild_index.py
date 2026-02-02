#!/usr/bin/env python3
"""
Rebuild Azure AI Search Index with Synonym Map Support

This script performs a one-time migration to add synonym maps and update the index schema.
Run this after modifying azure_policy_index.py to enable synonym/speller support.

Usage:
    python scripts/rebuild_index.py

Steps performed:
1. Create/update the synonym map with domain-specific terminology
2. Update the index schema to reference the synonym map
3. Optionally re-upload documents (if needed)

Note: The speller feature is enabled at search time and doesn't require index changes.
"""

import sys
import os
from pathlib import Path

# Add backend to path
backend_path = Path(__file__).resolve().parent.parent / "apps" / "backend"
sys.path.insert(0, str(backend_path))

# Load environment
from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

from azure_policy_index import PolicySearchIndex, SYNONYM_MAP_NAME


def main():
    print("=" * 60)
    print("AZURE AI SEARCH - Index Rebuild with Synonyms")
    print("=" * 60)
    
    # Initialize index
    index = PolicySearchIndex()
    
    print(f"\n1. Creating/updating synonym map '{SYNONYM_MAP_NAME}'...")
    try:
        index.create_synonym_map()
        print("   ✓ Synonym map created successfully")
    except Exception as e:
        print(f"   ✗ Error creating synonym map: {e}")
        return 1
    
    print(f"\n2. Updating index schema to reference synonym map...")
    try:
        index.create_index()
        print("   ✓ Index schema updated successfully")
    except Exception as e:
        print(f"   ✗ Error updating index: {e}")
        return 1
    
    print(f"\n3. Getting index stats...")
    try:
        stats = index.get_stats()
        print(f"   Index: {stats.get('index_name', 'N/A')}")
        print(f"   Documents: {stats.get('document_count', 'N/A')}")
        print(f"   Fields: {stats.get('fields', 'N/A')}")
    except Exception as e:
        print(f"   Warning: Could not get stats: {e}")
    
    print("\n" + "=" * 60)
    print("REBUILD COMPLETE")
    print("=" * 60)
    print("\nThe following features are now enabled:")
    print("  • Synonym expansion for domain terminology")
    print("    (e.g., 'radiology' will find 'diagnostic services')")
    print("  • Spell correction for typos")
    print("    (e.g., 'expierence' will find 'experience')")
    print("\nTest with:")
    print("  curl -X POST http://localhost:8000/api/chat \\")
    print('    -H "Content-Type: application/json" \\')
    print('    -d \'{"message": "radiology patient expierence policy"}\'')
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

