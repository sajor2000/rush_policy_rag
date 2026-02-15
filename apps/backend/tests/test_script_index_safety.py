import re
from pathlib import Path

from app.core.index_safety import (
    DEFAULT_ACTIVE_ALIAS,
    ensure_safe_index_target,
    resolve_index_name,
)


def test_resolve_index_name_prefers_cli_value(monkeypatch):
    monkeypatch.setenv("SEARCH_INDEX_NAME", "from-env")
    assert resolve_index_name("from-cli") == "from-cli"


def test_resolve_index_name_uses_env_when_cli_missing(monkeypatch):
    monkeypatch.setenv("SEARCH_INDEX_NAME", "from-env")
    assert resolve_index_name(None) == "from-env"


def test_resolve_index_name_defaults_to_alias(monkeypatch):
    monkeypatch.delenv("SEARCH_INDEX_NAME", raising=False)
    assert resolve_index_name(None) == DEFAULT_ACTIVE_ALIAS


def test_safe_index_target_allows_alias_by_default():
    ensure_safe_index_target(
        DEFAULT_ACTIVE_ALIAS,
        allow_direct_index=False,
        active_alias=DEFAULT_ACTIVE_ALIAS,
        operation="write",
    )


def test_safe_index_target_blocks_direct_write_without_override():
    try:
        ensure_safe_index_target(
            "rush-policies-v2-20260212",
            allow_direct_index=False,
            active_alias=DEFAULT_ACTIVE_ALIAS,
            operation="write",
        )
        assert False, "Expected direct write guard to raise"
    except ValueError as exc:
        assert "--allow-direct-index" in str(exc)


def test_safe_index_target_allows_direct_write_with_override():
    ensure_safe_index_target(
        "rush-policies-v2-20260212",
        allow_direct_index=True,
        active_alias=DEFAULT_ACTIVE_ALIAS,
        operation="write",
    )


def test_active_scripts_expose_index_override_and_direct_override_flags():
    backend_root = Path(__file__).resolve().parents[1]
    repo_root = Path(__file__).resolve().parents[3]
    script_paths = [
        backend_root / "scripts" / "audit_ingestion_quality.py",
        backend_root / "scripts" / "reindex_specific_files.py",
        repo_root / "scripts" / "full_pipeline_ingest.py",
        repo_root / "scripts" / "local_folder_ingest.py",
    ]

    for script_path in script_paths:
        text = script_path.read_text(encoding="utf-8")
        assert "--index-name" in text, f"Missing --index-name in {script_path}"
        assert (
            "--allow-direct-index" in text
        ), f"Missing --allow-direct-index in {script_path}"


def test_active_scripts_do_not_hardcode_direct_rush_policies_index():
    backend_root = Path(__file__).resolve().parents[1]
    repo_root = Path(__file__).resolve().parents[3]
    script_paths = [
        backend_root / "scripts" / "audit_ingestion_quality.py",
        backend_root / "scripts" / "reindex_specific_files.py",
        repo_root / "scripts" / "full_pipeline_ingest.py",
        repo_root / "scripts" / "local_folder_ingest.py",
    ]
    direct_index_pattern = re.compile(r"\brush-policies\b(?!-active)")

    for script_path in script_paths:
        text = script_path.read_text(encoding="utf-8")
        assert not direct_index_pattern.search(
            text
        ), f"Found direct rush-policies reference in {script_path}"
