#!/usr/bin/env python3
"""
Blue/green Azure AI Search index + alias management helper.

Implements operational steps for:
- creating a new index generation (v2)
- creating/updating active alias
- swapping alias for cutover/rollback
- validating required schema fields for HR retrieval fixes

Usage examples:
  python apps/backend/scripts/blue_green_index_alias.py create-index --index rush-policies-v2-20260212
  python apps/backend/scripts/blue_green_index_alias.py ensure-alias --alias rush-policies-active --index <current-prod-index>
  python apps/backend/scripts/blue_green_index_alias.py swap-alias --alias rush-policies-active --index rush-policies-v2-20260212
  python apps/backend/scripts/blue_green_index_alias.py show-alias --alias rush-policies-active
  python apps/backend/scripts/blue_green_index_alias.py validate-schema --index rush-policies-v2-20260212
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Optional

from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import SearchAlias

# Backend module imports
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from azure_policy_index import PolicySearchIndex  # noqa: E402

DEFAULT_ALIAS = os.environ.get("SEARCH_INDEX_NAME", "rush-policies-active")
DEFAULT_ENDPOINT = os.environ.get("SEARCH_ENDPOINT")
DEFAULT_API_KEY = os.environ.get("SEARCH_API_KEY")


@dataclass
class Context:
    endpoint: str
    index_client: SearchIndexClient


def build_context(endpoint: Optional[str], api_key: Optional[str]) -> Context:
    resolved_endpoint = endpoint or DEFAULT_ENDPOINT
    if not resolved_endpoint:
        raise RuntimeError("SEARCH_ENDPOINT is required (env or --endpoint)")

    resolved_api_key = api_key or DEFAULT_API_KEY
    if resolved_api_key:
        credential = AzureKeyCredential(resolved_api_key)
        credential_desc = "api-key"
    else:
        credential = DefaultAzureCredential()
        credential_desc = "default-azure-credential"

    index_client = SearchIndexClient(endpoint=resolved_endpoint, credential=credential)
    print(f"[INFO] Using endpoint={resolved_endpoint} credential={credential_desc}")
    return Context(endpoint=resolved_endpoint, index_client=index_client)


def create_index(
    index_name: str, endpoint: Optional[str], api_key: Optional[str]
) -> None:
    resolved_endpoint = endpoint or DEFAULT_ENDPOINT
    if not resolved_endpoint:
        raise RuntimeError("SEARCH_ENDPOINT is required (env or --endpoint)")

    resolved_api_key = api_key or DEFAULT_API_KEY
    credential_desc = "api-key" if resolved_api_key else "default-azure-credential"
    print(f"[INFO] Creating/updating index: {index_name}")
    print(f"[INFO] Using endpoint={resolved_endpoint} credential={credential_desc}")

    index = PolicySearchIndex(
        index_name=index_name,
        search_endpoint=resolved_endpoint,
        search_api_key=resolved_api_key,
    )

    try:
        index.create_synonym_map()
        print("[OK] Synonym map ensured")
    except Exception as exc:
        raise RuntimeError(
            "Failed to create/update synonym map. Ensure credentials have synonym-map permissions."
        ) from exc

    index.create_index()
    print(f"[OK] Index ready: {index_name}")


def ensure_alias(ctx: Context, alias_name: str, target_index: str) -> None:
    alias = SearchAlias(name=alias_name, indexes=[target_index])
    ctx.index_client.create_or_update_alias(alias)
    print(f"[OK] Alias '{alias_name}' -> '{target_index}'")


def show_alias(ctx: Context, alias_name: str) -> None:
    alias = ctx.index_client.get_alias(alias_name)
    print(f"Alias: {alias.name}")
    print(f"Targets: {', '.join(alias.indexes)}")


def list_aliases(ctx: Context) -> None:
    aliases = list(ctx.index_client.list_aliases())
    if not aliases:
        print("No aliases found.")
        return
    for alias in aliases:
        print(f"- {alias.name}: {', '.join(alias.indexes)}")


def validate_schema(ctx: Context, index_name: str) -> int:
    index = ctx.index_client.get_index(index_name)
    field_map = {field.name: field for field in index.fields}

    failures = []

    policy_field = field_map.get("policy_number")
    if not policy_field:
        failures.append("missing field: policy_number")
    elif not getattr(policy_field, "filterable", False):
        failures.append("policy_number is not filterable")

    chunk_index_field = field_map.get("chunk_index")
    if not chunk_index_field:
        failures.append("missing field: chunk_index")
    elif not getattr(chunk_index_field, "filterable", False):
        failures.append("chunk_index is not filterable")

    if failures:
        print(f"[FAIL] Schema validation failed for {index_name}")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print(
        f"[OK] Schema validated for {index_name}: policy_number + chunk_index are filterable"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Blue/green index alias manager")
    parser.add_argument(
        "--endpoint",
        default=None,
        help="Azure Search endpoint (defaults to SEARCH_ENDPOINT)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Azure Search API key (defaults to SEARCH_API_KEY)",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser(
        "create-index", help="Create or update a named index schema"
    )
    p_create.add_argument("--index", required=True, help="Index name to create")

    p_alias = sub.add_parser("ensure-alias", help="Create or update alias target")
    p_alias.add_argument("--alias", default=DEFAULT_ALIAS, help="Alias name")
    p_alias.add_argument("--index", required=True, help="Target index name")

    p_swap = sub.add_parser(
        "swap-alias", help="Swap alias to another index (cutover/rollback)"
    )
    p_swap.add_argument("--alias", default=DEFAULT_ALIAS, help="Alias name")
    p_swap.add_argument("--index", required=True, help="Target index name")

    p_show = sub.add_parser("show-alias", help="Show alias target")
    p_show.add_argument("--alias", default=DEFAULT_ALIAS, help="Alias name")

    sub.add_parser("list-aliases", help="List all aliases")

    p_validate = sub.add_parser(
        "validate-schema", help="Validate required index fields"
    )
    p_validate.add_argument("--index", required=True, help="Index name to validate")

    args = parser.parse_args()

    if args.cmd == "create-index":
        create_index(args.index, args.endpoint, args.api_key)
        return 0

    ctx = build_context(args.endpoint, args.api_key)

    if args.cmd == "ensure-alias":
        ensure_alias(ctx, args.alias, args.index)
        return 0
    if args.cmd == "swap-alias":
        ensure_alias(ctx, args.alias, args.index)
        return 0
    if args.cmd == "show-alias":
        show_alias(ctx, args.alias)
        return 0
    if args.cmd == "list-aliases":
        list_aliases(ctx)
        return 0
    if args.cmd == "validate-schema":
        return validate_schema(ctx, args.index)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
