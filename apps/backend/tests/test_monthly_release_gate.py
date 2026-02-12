import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "monthly_hr_release_gate.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "monthly_hr_release_gate", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_change_counts_extracts_new_changed_deleted():
    module = _load_module()
    output = """
New: 2
Changed: 5
Deleted: 1
"""
    counts = module.parse_change_counts(output)
    assert counts == {"new": 2, "changed": 5, "deleted": 1}


def test_validate_sync_matches_detect_raises_on_mismatch():
    module = _load_module()
    with pytest.raises(ValueError):
        module.validate_sync_matches_detect(
            {"new": 1, "changed": 2, "deleted": 0},
            {"new": 1, "changed": 3, "deleted": 0},
        )


def test_load_release_profile_rejects_unresolved_placeholders(tmp_path: Path):
    module = _load_module()
    profile_file = tmp_path / "profiles.json"
    profile_file.write_text(
        json.dumps(
            {
                "prod": {
                    "subscription": "__REQUIRED__",
                    "resource_group": "rg",
                    "acr_name": "acr",
                    "backend_app": "be",
                    "frontend_app": "fe",
                    "search_alias": "rush-policies-active",
                    "search_endpoint": "https://example.search.windows.net",
                    "source_container": "policies-source",
                    "target_container": "policies-active",
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        module.load_release_profile(profile_file, "prod")


def test_load_release_profile_returns_env_profile(tmp_path: Path):
    module = _load_module()
    profile_file = tmp_path / "profiles.json"
    profile_file.write_text(
        json.dumps(
            {
                "nonprod": {
                    "subscription": "sub",
                    "resource_group": "rg",
                    "acr_name": "acr",
                    "backend_app": "be",
                    "frontend_app": "fe",
                    "search_alias": "rush-policies-active",
                    "search_endpoint": "https://example.search.windows.net",
                    "source_container": "policies-source",
                    "target_container": "policies-active",
                }
            }
        ),
        encoding="utf-8",
    )

    profile = module.load_release_profile(profile_file, "nonprod")
    assert profile["subscription"] == "sub"
    assert profile["search_alias"] == "rush-policies-active"


def test_compute_manifest_delta_prefers_policy_number_identity():
    module = _load_module()
    previous = {
        "entries": [
            {
                "filename": "old-name.pdf",
                "source_blob": "2026-01/old-name.pdf",
                "policy_number": "HR-C 05.00",
                "content_hash": "abc",
            }
        ]
    }
    current = {
        "entries": [
            {
                "filename": "renamed.pdf",
                "source_blob": "2026-02/renamed.pdf",
                "policy_number": "HR-C 05.00",
                "content_hash": "def",
            }
        ]
    }

    delta = module.compute_manifest_delta(current, previous)
    assert delta["counts"]["new"] == 0
    assert delta["counts"]["changed"] == 1
    assert delta["changed"][0]["filename"] == "renamed.pdf"


def test_compute_manifest_delta_falls_back_to_filename_when_no_policy_number():
    module = _load_module()
    previous = {
        "entries": [
            {
                "filename": "General Policy.pdf",
                "source_blob": "2026-01/General Policy.pdf",
                "policy_number": "",
                "content_hash": "abc",
            }
        ]
    }
    current = {
        "entries": [
            {
                "filename": "General Policy.pdf",
                "source_blob": "2026-02/General Policy.pdf",
                "policy_number": "",
                "content_hash": "abc",
            }
        ]
    }

    delta = module.compute_manifest_delta(current, previous)
    assert delta["counts"]["new"] == 0
    assert delta["counts"]["changed"] == 0
    assert delta["counts"]["unchanged"] == 1


def test_compute_manifest_delta_handles_duplicate_policy_number_collisions():
    module = _load_module()
    previous = {
        "entries": [
            {
                "filename": "A.pdf",
                "policy_number": "HR-C 05.00",
                "content_hash": "hash-a",
                "size": 10,
                "etag": "e1",
            },
            {
                "filename": "B.pdf",
                "policy_number": "HR-C 05.00",
                "content_hash": "hash-b",
                "size": 20,
                "etag": "e2",
            },
        ]
    }
    current = {
        "entries": [
            {
                "filename": "A.pdf",
                "policy_number": "HR-C 05.00",
                "content_hash": "hash-a",
                "size": 10,
                "etag": "e1",
            },
            {
                "filename": "C.pdf",
                "policy_number": "HR-C 05.00",
                "content_hash": "hash-c",
                "size": 30,
                "etag": "e3",
            },
        ]
    }

    delta = module.compute_manifest_delta(current, previous)
    assert delta["counts"]["collisions"] == 1
    assert delta["counts"]["unchanged"] == 1
    assert delta["counts"]["new"] == 1
    assert delta["counts"]["missing"] == 1
    assert delta["collisions"][0]["strategy"] == "filename_fallback"


def test_should_clone_active_index_respects_run_mode():
    module = _load_module()
    assert module.should_clone_active_index("monthly") is True
    assert module.should_clone_active_index("baseline") is False


def test_build_delta_for_run_mode_baseline_marks_all_entries_new():
    module = _load_module()
    current = {
        "entries": [
            {"filename": "A.pdf", "policy_number": "HR-C 05.00"},
            {"filename": "B.pdf", "policy_number": ""},
        ]
    }
    delta = module.build_delta_for_run_mode(
        run_mode="baseline", current_manifest=current, previous_manifest=None
    )
    assert delta["run_mode"] == "baseline"
    assert delta["counts"]["new"] == 2
    assert delta["counts"]["changed"] == 0
    assert delta["counts"]["missing"] == 0


def test_resolve_previous_manifest_ignores_failed_and_incompatible_runs(tmp_path: Path):
    module = _load_module()
    report_root = tmp_path
    month_dir = report_root / "2026-03"
    month_dir.mkdir(parents=True)

    failed_run = month_dir / "run_20260310_010101_nonprod_monthly_bad"
    failed_run.mkdir()
    (failed_run / "manifest_current.json").write_text('{"entries":[]}', encoding="utf-8")
    (failed_run / "summary.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "source_container": "policies-source",
                "source_prefix": "2026-03",
            }
        ),
        encoding="utf-8",
    )

    wrong_prefix_run = month_dir / "run_20260311_010101_nonprod_monthly_wrongprefix"
    wrong_prefix_run.mkdir()
    (wrong_prefix_run / "manifest_current.json").write_text(
        '{"entries":[]}', encoding="utf-8"
    )
    (wrong_prefix_run / "summary.json").write_text(
        json.dumps(
            {
                "status": "success",
                "source_container": "policies-source",
                "source_prefix": "2026-02",
            }
        ),
        encoding="utf-8",
    )

    good_run = month_dir / "run_20260312_010101_nonprod_monthly_good"
    good_run.mkdir()
    good_manifest = good_run / "manifest_current.json"
    good_manifest.write_text('{"entries":[{"filename":"A.pdf"}]}', encoding="utf-8")
    (good_run / "summary.json").write_text(
        json.dumps(
            {
                "status": "success",
                "source_container": "policies-source",
                "source_prefix": "2026-03",
            }
        ),
        encoding="utf-8",
    )

    selected = module.resolve_previous_manifest(
        previous_manifest_path=None,
        report_root=report_root,
        environment="nonprod",
        source_container="policies-source",
        source_prefix="2026-03",
    )
    assert selected == good_manifest


def test_resolve_previous_manifest_explicit_missing_path_raises(tmp_path: Path):
    module = _load_module()
    with pytest.raises(FileNotFoundError):
        module.resolve_previous_manifest(
            previous_manifest_path=str(tmp_path / "missing.json"),
            report_root=tmp_path,
            environment="nonprod",
            source_container="policies-source",
            source_prefix="",
        )
