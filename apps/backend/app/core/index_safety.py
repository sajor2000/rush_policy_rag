"""
Index target resolution and safety guards for operational scripts.

These helpers are intentionally lightweight and dependency-free so they can
be reused by scripts that run outside the FastAPI runtime.
"""

from __future__ import annotations

import os
from typing import Optional


DEFAULT_ACTIVE_ALIAS = "rush-policies-active"


def resolve_index_name(
    explicit_index_name: Optional[str],
    *,
    env_var: str = "SEARCH_INDEX_NAME",
    default_alias: str = DEFAULT_ACTIVE_ALIAS,
) -> str:
    """
    Resolve target index name from CLI override, env, then default alias.
    """
    resolved = (explicit_index_name or os.environ.get(env_var) or default_alias).strip()
    if not resolved:
        raise ValueError("Resolved search index name is empty")
    return resolved


def ensure_safe_index_target(
    index_name: str,
    *,
    allow_direct_index: bool,
    active_alias: str = DEFAULT_ACTIVE_ALIAS,
    operation: str = "write",
) -> None:
    """
    Guard destructive/non-idempotent operations from bypassing the active alias.

    By default, write operations must target the active alias unless the operator
    passes an explicit override.
    """
    if operation != "write":
        return

    if index_name == active_alias:
        return

    if allow_direct_index:
        return

    raise ValueError(
        "Refusing direct index write against "
        f"'{index_name}'. Use alias '{active_alias}' or pass --allow-direct-index."
    )

