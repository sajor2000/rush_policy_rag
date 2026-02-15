#!/usr/bin/env python3
"""
Manual Azure CLI monthly release flow for HR retrieval stabilization.

Flow:
1) Azure/profile preflight
2) Alias preflight + rollback capture
3) Detect monthly deltas
4) Build candidate index + clone current active index documents
5) Sync changed/new/deleted docs into candidate index
6) Run quality + HR regressions + targeted backend test suite
7) Manual approval checkpoint
8) Alias cutover
9) ACR build + Container Apps redeploy (backend + frontend)
10) Post-cutover smoke + regressions
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "apps" / "backend"
SCRIPTS_ROOT = BACKEND_ROOT / "scripts"
DEFAULT_ALIAS = "rush-policies-active"
DEFAULT_PROFILE_FILE = BACKEND_ROOT / "config" / "monthly_release_profiles.json"
CHANGE_COUNT_PATTERN = re.compile(
    r"^\s*(New|Changed|Deleted):\s*(\d+)\s*$", re.MULTILINE
)
PROFILE_REQUIRED_KEYS = [
    "subscription",
    "resource_group",
    "acr_name",
    "backend_app",
    "frontend_app",
    "search_alias",
    "search_endpoint",
    "source_container",
    "target_container",
]
TARGETED_TEST_FILES = [
    "tests/test_query_validation.py",
    "tests/test_synonym_service.py",
    "tests/test_query_enhancer.py",
    "tests/test_chat_service.py",
    "tests/test_search_metadata.py",
    "tests/test_on_your_data_service.py",
    "tests/test_script_index_safety.py",
]
POLICY_NUMBER_PATTERN = re.compile(
    r"\b([A-Za-z]{2})\s*-\s*([A-Za-z])\s*(\d{1,2})(?:\.(\d{1,4}))?\b"
)
PROMPTFOO_PASS_RATE_THRESHOLD = 0.90
PROMPTFOO_SAFETY_FLAG_RATE_MAX = 0.05
PROMPTFOO_CITATION_COVERAGE_MIN = 0.90

# Baseline comparison thresholds (Deliverable 4)
BASELINE_PASS_RATE_MAX_DROP = 0.05  # Fail if pass rate drops > 5pp
BASELINE_SAFETY_MAX_INCREASE = 0.03  # Fail if safety flag rate increases > 3pp
BASELINE_FOUND_RATE_MAX_DROP = 0.05  # Fail if found rate drops > 5pp
BASELINE_LATENCY_MAX_INCREASE = 0.25  # Fail if avg latency increases > 25%
PROMPTFOO_REFUSAL_PATTERN = re.compile(
    r"could not find|could not verify|outside my scope|not in rush|"
    r"cannot provide|unable to find|no relevant.*polic|"
    r"i only answer rush|didn.?t understand that|please rephrase",
    re.IGNORECASE,
)
PROMPTFOO_SAFETY_FLAG_PATTERN = re.compile(
    r"LOW_GROUNDING|SELF_CRITIQUE|BLOCKED|UNVERIFIED|UNGROUNDED|LLM_REFUSAL",
    re.IGNORECASE,
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _month_folder() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _safe_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "candidate"


def parse_change_counts(output: str) -> Dict[str, int]:
    counts = {"new": 0, "changed": 0, "deleted": 0}
    for key, value in CHANGE_COUNT_PATTERN.findall(output):
        counts[key.lower()] = int(value)
    return counts


def validate_sync_matches_detect(
    detect_counts: Dict[str, int], sync_counts: Dict[str, int]
) -> None:
    for key in ("new", "changed", "deleted"):
        if detect_counts.get(key) != sync_counts.get(key):
            raise ValueError(
                f"Delta mismatch for '{key}': detect={detect_counts.get(key)} "
                f"vs sync={sync_counts.get(key)}"
            )


def normalize_policy_number(raw: str) -> str:
    """Normalize coded policy identifier to canonical XX-X NN.NN format."""
    match = POLICY_NUMBER_PATTERN.search(raw or "")
    if not match:
        return ""

    prefix = match.group(1).upper()
    letter = match.group(2).upper()
    major = match.group(3)
    minor = match.group(4)

    if minor is None:
        return f"{prefix}-{letter} {major.zfill(2)}.00"
    if len(minor) == 3 and major == "0":
        digits = f"{major}{minor}".zfill(4)[:4]
        return f"{prefix}-{letter} {digits[:2]}.{digits[2:]}"
    minor2 = (minor + "0")[:2] if len(minor) == 1 else minor[:2]
    return f"{prefix}-{letter} {major.zfill(2)}.{minor2}"


def _identity_key(entry: Dict[str, Any]) -> str:
    """Primary identity by policy_number, fallback to filename."""
    policy_number = normalize_policy_number(str(entry.get("policy_number") or ""))
    if policy_number:
        return f"policy:{policy_number}"
    filename = str(entry.get("filename") or "").strip().lower()
    return f"file:{filename}"


def _resolve_source_blob_name(blob_name: str, source_prefix: str) -> str:
    if not source_prefix:
        return blob_name
    prefix = source_prefix.strip("/").strip()
    if not prefix:
        return blob_name
    if blob_name.startswith(prefix + "/"):
        return blob_name
    return f"{prefix}/{blob_name}"


def _compute_blob_hash(blob_client) -> str:
    hasher = hashlib.sha256()
    stream = blob_client.download_blob()
    for chunk in stream.chunks():
        hasher.update(chunk)
    return hasher.hexdigest()


def build_manifest(
    *,
    storage_connection_string: str,
    container: str,
    source_prefix: str = "",
) -> Dict[str, Any]:
    """
    Build source manifest for current monthly snapshot.

    Includes content hashes to catch sentence-level edits.
    """
    blob_service = BlobServiceClient.from_connection_string(storage_connection_string)
    client = blob_service.get_container_client(container)
    prefix = source_prefix.strip("/").strip() or None
    entries: List[Dict[str, Any]] = []

    for blob in client.list_blobs(name_starts_with=prefix):
        if not blob.name.lower().endswith(".pdf"):
            continue
        filename = blob.name.split("/")[-1]
        policy_number = normalize_policy_number(filename)
        blob_client = client.get_blob_client(blob.name)
        content_hash = _compute_blob_hash(blob_client)
        entries.append(
            {
                "filename": filename,
                "source_blob": blob.name,
                "policy_number": policy_number,
                "size": int(getattr(blob, "size", 0) or 0),
                "etag": str(getattr(blob, "etag", "") or "").strip('"'),
                "last_modified": (
                    blob.last_modified.isoformat()
                    if getattr(blob, "last_modified", None)
                    else ""
                ),
                "content_hash": content_hash,
            }
        )

    # Stable ordering for reproducible artifacts.
    entries.sort(
        key=lambda x: (
            x.get("policy_number") or "",
            x.get("filename") or "",
            x.get("source_blob") or "",
        )
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "container": container,
        "source_prefix": source_prefix.strip("/"),
        "entries": entries,
        "count": len(entries),
    }


def build_manifest_from_target_container(
    *,
    storage_connection_string: str,
    container: str,
) -> Dict[str, Any]:
    """Build a 'virtual previous manifest' from the target container's blob metadata.

    Used as fallback when no previous run manifest exists on disk. The target
    container stores content_hash in blob metadata for every indexed document,
    making it possible to detect unchanged files even on first runs.
    """
    blob_service = BlobServiceClient.from_connection_string(storage_connection_string)
    client = blob_service.get_container_client(container)
    entries: List[Dict[str, Any]] = []

    for blob in client.list_blobs(include=["metadata"]):
        if not blob.name.lower().endswith(".pdf"):
            continue
        metadata = blob.metadata or {}
        content_hash = metadata.get("content_hash", "")
        if not content_hash:
            continue  # Skip blobs without hash (not indexed by our pipeline)
        entries.append(
            {
                "filename": blob.name,
                "source_blob": blob.name,
                "policy_number": normalize_policy_number(
                    metadata.get("policy_number", "") or blob.name
                ),
                "size": int(getattr(blob, "size", 0) or 0),
                "etag": str(getattr(blob, "etag", "") or "").strip('"'),
                "content_hash": content_hash,
            }
        )

    entries.sort(key=lambda x: (x.get("policy_number") or "", x.get("filename") or ""))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "container": container,
        "source": "target_container_metadata",
        "entries": entries,
        "count": len(entries),
    }


def resolve_previous_manifest(
    *,
    previous_manifest_path: Optional[str],
    report_root: Path,
    environment: str,
    source_container: str,
    source_prefix: str,
    allowed_statuses: Optional[set[str]] = None,
) -> Optional[Path]:
    """Resolve previous manifest path from explicit input or latest compatible run."""
    normalized_prefix = source_prefix.strip("/")
    valid_statuses = allowed_statuses or {"success", "pre_cutover_complete"}

    if previous_manifest_path:
        path = Path(previous_manifest_path)
        if not path.exists():
            raise FileNotFoundError(f"Previous manifest does not exist: {path}")
        return path

    candidates = sorted(
        report_root.glob(f"*/run_*_{environment}_*/manifest_current.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        summary_path = candidate.parent / "summary.json"
        if not summary_path.exists():
            continue
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue

        if str(summary.get("status") or "") not in valid_statuses:
            continue
        if str(summary.get("source_container") or "") != source_container:
            continue
        if str(summary.get("source_prefix") or "").strip("/") != normalized_prefix:
            continue
        return candidate
    return None


def _entry_changed(previous: Dict[str, Any], current: Dict[str, Any]) -> bool:
    return any(
        [
            previous.get("content_hash") != current.get("content_hash"),
            int(previous.get("size") or 0) != int(current.get("size") or 0),
            str(previous.get("etag") or "") != str(current.get("etag") or ""),
        ]
    )


def _match_group_by_filename(
    *,
    key: str,
    current_group: List[Dict[str, Any]],
    previous_group: List[Dict[str, Any]],
    new_entries: List[Dict[str, Any]],
    changed_entries: List[Dict[str, Any]],
    unchanged_entries: List[Dict[str, Any]],
    missing_entries: List[Dict[str, Any]],
    collision_diagnostics: List[Dict[str, Any]],
) -> None:
    prev_by_filename: Dict[str, List[Dict[str, Any]]] = {}
    for entry in previous_group:
        filename_key = str(entry.get("filename") or "").strip().lower()
        prev_by_filename.setdefault(filename_key, []).append(entry)

    for entry in current_group:
        filename_key = str(entry.get("filename") or "").strip().lower()
        candidates = prev_by_filename.get(filename_key) or []
        if not candidates:
            new_entries.append(entry)
            continue
        previous = candidates.pop(0)
        if _entry_changed(previous, entry):
            changed_entries.append(entry)
        else:
            unchanged_entries.append(entry)

    for leftovers in prev_by_filename.values():
        missing_entries.extend(leftovers)

    collision_diagnostics.append(
        {
            "identity_key": key,
            "strategy": "filename_fallback",
            "current_count": len(current_group),
            "previous_count": len(previous_group),
            "current_files": sorted(
                str(entry.get("filename") or "") for entry in current_group
            ),
            "previous_files": sorted(
                str(entry.get("filename") or "") for entry in previous_group
            ),
        }
    )


def compute_manifest_delta(
    current_manifest: Dict[str, Any],
    previous_manifest: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute monthly delta by policy_number primary key, filename fallback."""
    current_entries = current_manifest.get("entries", [])
    previous_entries = (previous_manifest or {}).get("entries", [])

    new_entries: List[Dict[str, Any]] = []
    changed_entries: List[Dict[str, Any]] = []
    unchanged_entries: List[Dict[str, Any]] = []
    missing_entries: List[Dict[str, Any]] = []
    collision_diagnostics: List[Dict[str, Any]] = []

    current_by_id: Dict[str, List[Dict[str, Any]]] = {}
    previous_by_id: Dict[str, List[Dict[str, Any]]] = {}
    for entry in current_entries:
        current_by_id.setdefault(_identity_key(entry), []).append(entry)
    for entry in previous_entries:
        previous_by_id.setdefault(_identity_key(entry), []).append(entry)

    for key in sorted(set(current_by_id.keys()) | set(previous_by_id.keys())):
        current_group = current_by_id.get(key, [])
        previous_group = previous_by_id.get(key, [])

        if not previous_group:
            new_entries.extend(current_group)
            continue
        if not current_group:
            missing_entries.extend(previous_group)
            continue

        if len(current_group) == 1 and len(previous_group) == 1:
            if _entry_changed(previous_group[0], current_group[0]):
                changed_entries.append(current_group[0])
            else:
                unchanged_entries.append(current_group[0])
            continue

        _match_group_by_filename(
            key=key,
            current_group=current_group,
            previous_group=previous_group,
            new_entries=new_entries,
            changed_entries=changed_entries,
            unchanged_entries=unchanged_entries,
            missing_entries=missing_entries,
            collision_diagnostics=collision_diagnostics,
        )

    delta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_total": len(current_entries),
        "new": new_entries,
        "changed": changed_entries,
        "unchanged": unchanged_entries,
        "missing": missing_entries,
        "collisions": collision_diagnostics,
        "counts": {
            "new": len(new_entries),
            "changed": len(changed_entries),
            "unchanged": len(unchanged_entries),
            "missing": len(missing_entries),
            "collisions": len(collision_diagnostics),
        },
    }
    return delta


def should_clone_active_index(run_mode: str) -> bool:
    return run_mode == "monthly"


def build_delta_for_run_mode(
    *,
    run_mode: str,
    current_manifest: Dict[str, Any],
    previous_manifest: Optional[Dict[str, Any]],
    target_container_manifest: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if run_mode == "baseline":
        # Use target container as fallback to avoid reindexing unchanged docs
        effective_previous = previous_manifest or target_container_manifest
        if effective_previous and effective_previous.get("entries"):
            delta = compute_manifest_delta(current_manifest, effective_previous)
            delta["run_mode"] = "baseline"
            delta["previous_source"] = effective_previous.get("source", "manifest_file")
            return delta
        # True first run: no previous data at all
        entries = current_manifest.get("entries", [])
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "run_mode": "baseline",
            "source_total": len(entries),
            "new": entries,
            "changed": [],
            "unchanged": [],
            "missing": [],
            "collisions": [],
            "counts": {
                "new": len(entries),
                "changed": 0,
                "unchanged": 0,
                "missing": 0,
                "collisions": 0,
            },
        }

    # Monthly mode: also use target container fallback if no previous manifest
    effective_previous = previous_manifest or target_container_manifest
    delta = compute_manifest_delta(current_manifest, effective_previous)
    delta["run_mode"] = run_mode
    return delta


def load_release_profile(profile_file: Path, environment: str) -> Dict[str, str]:
    if not profile_file.exists():
        raise FileNotFoundError(
            f"Profile file not found: {profile_file}. "
            f"Create it from the repository default template."
        )
    data = json.loads(profile_file.read_text(encoding="utf-8"))
    if environment not in data:
        raise KeyError(f"Profile '{environment}' not found in {profile_file}")
    profile = data[environment]

    missing = [key for key in PROFILE_REQUIRED_KEYS if not profile.get(key)]
    if missing:
        raise ValueError(
            f"Profile '{environment}' missing required keys: {', '.join(missing)}"
        )

    placeholders = [
        key
        for key in PROFILE_REQUIRED_KEYS
        if str(profile.get(key, "")).strip().startswith("__REQUIRED__")
    ]
    if placeholders:
        raise ValueError(
            f"Profile '{environment}' has unresolved placeholders for: {', '.join(placeholders)}"
        )
    return {k: str(v) for k, v in profile.items()}


def run_command(
    *,
    name: str,
    cmd: List[str],
    artifact_path: Path,
    cwd: Path,
    extra_env: Optional[Dict[str, str]] = None,
) -> str:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    print(f"[RUN] {name}")
    print(f"      {' '.join(cmd)}")

    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
    )

    output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
    artifact_path.write_text(output or "(no output)\n", encoding="utf-8")

    if result.returncode != 0:
        raise RuntimeError(
            f"{name} failed with exit code {result.returncode}. See {artifact_path}"
        )
    return output


def run_az_query(
    *,
    name: str,
    args: List[str],
    artifact_path: Path,
    cwd: Path,
) -> str:
    cmd = ["az"] + args
    return run_command(name=name, cmd=cmd, artifact_path=artifact_path, cwd=cwd)


def _decode_cli_json(output: str) -> Any:
    decoder = json.JSONDecoder()
    for index, char in enumerate(output):
        if char not in "{[":
            continue
        try:
            parsed, _ = decoder.raw_decode(output[index:])
            return parsed
        except json.JSONDecodeError:
            continue
    raise ValueError("Could not decode JSON payload from Azure CLI output")


def parse_alias_targets(show_alias_output: str) -> List[str]:
    match = re.search(r"Targets:\s*(.+)", show_alias_output)
    if not match:
        raise ValueError("Could not parse alias targets from show-alias output")
    targets = [token.strip() for token in match.group(1).split(",") if token.strip()]
    if not targets:
        raise ValueError("Alias has no target indexes")
    return targets


def build_blue_green_cmd(
    *,
    endpoint: Optional[str],
    api_key: Optional[str],
    subcommand: str,
    extra: List[str],
) -> List[str]:
    cmd = [sys.executable, str(SCRIPTS_ROOT / "blue_green_index_alias.py")]
    if endpoint:
        cmd.extend(["--endpoint", endpoint])
    if api_key:
        cmd.extend(["--api-key", api_key])
    cmd.append(subcommand)
    cmd.extend(extra)
    return cmd


def _http_get(url: str, headers: Optional[Dict[str, str]] = None) -> Tuple[int, str]:
    request = Request(url, headers=headers or {}, method="GET")
    with urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8", errors="replace")
        return response.status, body


def run_health_smoke_checks(
    *,
    base_url: str,
    admin_api_key: Optional[str],
    artifact_path: Path,
) -> None:
    lines: List[str] = []

    health_url = f"{base_url.rstrip('/')}/health"
    status, body = _http_get(health_url)
    lines.append(f"GET {health_url} -> {status}")
    lines.append(body[:2000])

    detailed_url = f"{base_url.rstrip('/')}/health/detailed"
    if admin_api_key:
        try:
            d_status, d_body = _http_get(
                detailed_url, headers={"X-Admin-Key": admin_api_key}
            )
            lines.append("")
            lines.append(f"GET {detailed_url} -> {d_status}")
            lines.append(d_body[:4000])
        except (HTTPError, URLError) as exc:
            lines.append("")
            lines.append(f"GET {detailed_url} failed: {exc}")
    else:
        lines.append("")
        lines.append("Skipped /health/detailed (ADMIN_API_KEY not provided)")

    artifact_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_frontend_smoke_check(*, frontend_url: str, artifact_path: Path) -> None:
    status, body = _http_get(frontend_url.rstrip("/"))
    artifact_path.write_text(
        f"GET {frontend_url.rstrip('/')} -> {status}\n{body[:2000]}\n",
        encoding="utf-8",
    )
    if status >= 400:
        raise RuntimeError(f"Frontend smoke check failed with HTTP {status}")


def require_manual_approval(
    *, artifact_path: Path, auto_approve: bool, run_mode: str
) -> None:
    checklist = [
        "Candidate index schema validated (policy_number + chunk_index filterable)",
    ]
    if run_mode == "monthly":
        checklist.append("Active index baseline cloned into candidate index")
        checklist.append("Monthly sync completed against candidate index")
    else:
        checklist.append("Baseline source-only sync completed against candidate index")
    checklist.extend(
        [
            "Ingestion quality audit reviewed",
            "HR regression verification passed pre-cutover",
            "Targeted backend regression suite passed",
            "Rollback commands captured",
        ]
    )

    lines = ["Manual Approval Checklist:"]
    lines.extend([f"- [x] {item}" for item in checklist])

    if auto_approve:
        lines.append("")
        lines.append("Approval: auto-approved via --auto-approve")
        artifact_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return

    print("\nManual approval required before alias cutover + deployment:")
    for item in checklist:
        print(f"  - {item}")
    print("Type 'approve' to continue.")
    response = input("> ").strip().lower()

    lines.append("")
    lines.append(f"Operator response: {response}")
    artifact_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if response != "approve":
        raise RuntimeError("Manual approval was not granted (expected 'approve').")


def clone_active_index_documents(
    *,
    endpoint: str,
    api_key: Optional[str],
    source_index: str,
    target_index: str,
    artifact_path: Path,
) -> Dict[str, int]:
    from azure.core.credentials import AzureKeyCredential
    from azure.identity import DefaultAzureCredential
    from azure.search.documents import SearchClient

    credential = AzureKeyCredential(api_key) if api_key else DefaultAzureCredential()
    source_client = SearchClient(
        endpoint=endpoint, index_name=source_index, credential=credential
    )
    target_client = SearchClient(
        endpoint=endpoint, index_name=target_index, credential=credential
    )

    source_results = source_client.search("*", top=0, include_total_count=True)
    source_total = source_results.get_count() or 0

    copied = 0
    upload_failures = 0
    lines = [
        f"source_index={source_index}",
        f"target_index={target_index}",
        f"source_total={source_total}",
    ]

    if source_total == 0:
        lines.append("Source index has 0 docs; skipping clone.")
        artifact_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {"source_total": 0, "copied": 0, "upload_failures": 0, "target_total": 0}

    page_size = 1000
    skip = 0
    while True:
        page = list(source_client.search("*", top=page_size, skip=skip))
        if not page:
            break

        batch: List[Dict[str, Any]] = []
        for item in page:
            doc = {
                k: v for k, v in dict(item).items() if not str(k).startswith("@search.")
            }
            if doc:
                batch.append(doc)
        if not batch:
            break

        results = target_client.upload_documents(batch)
        copied += len(batch)
        upload_failures += len([r for r in results if not r.succeeded])
        if upload_failures > 0:
            lines.append(f"upload_failure_count={upload_failures}")
            break

        skip += len(page)
        if len(page) < page_size:
            break

    # Azure Search count can lag briefly right after bulk uploads.
    target_total = 0
    for _ in range(30):  # ~60s max
        target_results = target_client.search("*", top=0, include_total_count=True)
        target_total = target_results.get_count() or 0
        if target_total >= copied:
            break

        import time

        time.sleep(2)
    lines.extend(
        [
            f"copied={copied}",
            f"upload_failures={upload_failures}",
            f"target_total={target_total}",
        ]
    )
    artifact_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if upload_failures > 0:
        raise RuntimeError(
            f"Index clone encountered {upload_failures} failed uploads. See {artifact_path}"
        )
    if copied < source_total:
        raise RuntimeError(
            f"Candidate clone incomplete: source_total={source_total}, copied={copied}"
        )
    return {
        "source_total": source_total,
        "copied": copied,
        "upload_failures": upload_failures,
        "target_total": target_total,
    }


def resolve_storage_connection_string(
    *,
    profile: Dict[str, str],
    run_dir: Path,
    source_container: str,
    target_container: str,
) -> str:
    connection_string = os.environ.get("STORAGE_CONNECTION_STRING")
    if connection_string:
        (run_dir / "00_storage_resolution.txt").write_text(
            "source=env\ncontainer_validation=blob_sdk\n", encoding="utf-8"
        )
        return connection_string

    accounts_raw = run_az_query(
        name="discover storage accounts",
        args=[
            "storage",
            "account",
            "list",
            "--resource-group",
            profile["resource_group"],
            "--query",
            "[].name",
            "-o",
            "tsv",
        ],
        artifact_path=run_dir / "00_storage_accounts.txt",
        cwd=REPO_ROOT,
    )
    accounts = [line.strip() for line in accounts_raw.splitlines() if line.strip()]
    if not accounts:
        raise RuntimeError(
            "No storage accounts found in resource group. Set STORAGE_CONNECTION_STRING explicitly."
        )

    selected_account = None
    for account in accounts:
        src_exists_raw = run_az_query(
            name=f"check source container ({account})",
            args=[
                "storage",
                "container",
                "exists",
                "--name",
                source_container,
                "--account-name",
                account,
                "--auth-mode",
                "login",
                "-o",
                "json",
            ],
            artifact_path=run_dir / f"00_storage_exists_{account}_source.json",
            cwd=REPO_ROOT,
        )
        tgt_exists_raw = run_az_query(
            name=f"check target container ({account})",
            args=[
                "storage",
                "container",
                "exists",
                "--name",
                target_container,
                "--account-name",
                account,
                "--auth-mode",
                "login",
                "-o",
                "json",
            ],
            artifact_path=run_dir / f"00_storage_exists_{account}_target.json",
            cwd=REPO_ROOT,
        )
        if _decode_cli_json(src_exists_raw).get("exists") and _decode_cli_json(
            tgt_exists_raw
        ).get("exists"):
            selected_account = account
            break

    if not selected_account:
        raise RuntimeError(
            "Could not locate a storage account containing both source and target containers. "
            "Set STORAGE_CONNECTION_STRING explicitly."
        )

    cmd = [
        "az",
        "storage",
        "account",
        "show-connection-string",
        "--name",
        selected_account,
        "--resource-group",
        profile["resource_group"],
        "--query",
        "connectionString",
        "-o",
        "tsv",
    ]
    result = subprocess.run(cmd, cwd=str(REPO_ROOT), text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            "Failed to resolve storage connection string via Azure CLI. "
            "Set STORAGE_CONNECTION_STRING explicitly."
        )
    connection_string = (result.stdout or "").strip()
    if not connection_string:
        raise RuntimeError(
            "Azure CLI returned an empty storage connection string. "
            "Set STORAGE_CONNECTION_STRING explicitly."
        )

    (run_dir / "00_storage_resolution.txt").write_text(
        (
            "source=azure_cli\n"
            f"selected_account={selected_account}\n"
            "container_validation=az_storage_container_exists\n"
        ),
        encoding="utf-8",
    )
    return connection_string


def run_azure_preflight(
    *,
    profile: Dict[str, str],
    run_dir: Path,
    source_container: str,
    target_container: str,
    alias: str,
    endpoint: str,
    api_key: Optional[str],
) -> str:
    if shutil.which("az") is None:
        raise RuntimeError(
            "Azure CLI not found on PATH. Install Azure CLI before running."
        )

    run_az_query(
        name="az --version",
        args=["--version"],
        artifact_path=run_dir / "00_az_version.txt",
        cwd=REPO_ROOT,
    )
    run_az_query(
        name="az account show (before)",
        args=["account", "show", "-o", "json"],
        artifact_path=run_dir / "00_account_before.json",
        cwd=REPO_ROOT,
    )
    run_az_query(
        name="az account set",
        args=["account", "set", "--subscription", profile["subscription"]],
        artifact_path=run_dir / "00_account_set.txt",
        cwd=REPO_ROOT,
    )
    active_account_raw = run_az_query(
        name="az account show (active)",
        args=["account", "show", "-o", "json"],
        artifact_path=run_dir / "00_account_active.json",
        cwd=REPO_ROOT,
    )
    active_account = _decode_cli_json(active_account_raw)
    active_name = str(active_account.get("name") or "")
    active_id = str(active_account.get("id") or "")
    if profile["subscription"] not in {active_name, active_id}:
        raise RuntimeError(
            "Active Azure subscription mismatch after az account set: "
            f"expected '{profile['subscription']}', got name='{active_name}', id='{active_id}'"
        )

    run_az_query(
        name="validate resource group",
        args=[
            "group",
            "show",
            "--name",
            profile["resource_group"],
            "--query",
            "name",
            "-o",
            "tsv",
        ],
        artifact_path=run_dir / "00_resource_group_check.txt",
        cwd=REPO_ROOT,
    )
    run_az_query(
        name="validate ACR",
        args=[
            "acr",
            "show",
            "--name",
            profile["acr_name"],
            "--resource-group",
            profile["resource_group"],
            "--query",
            "name",
            "-o",
            "tsv",
        ],
        artifact_path=run_dir / "00_acr_check.txt",
        cwd=REPO_ROOT,
    )
    run_az_query(
        name="validate backend containerapp",
        args=[
            "containerapp",
            "show",
            "--name",
            profile["backend_app"],
            "--resource-group",
            profile["resource_group"],
            "--query",
            "name",
            "-o",
            "tsv",
        ],
        artifact_path=run_dir / "00_backend_app_check.txt",
        cwd=REPO_ROOT,
    )
    run_az_query(
        name="validate frontend containerapp",
        args=[
            "containerapp",
            "show",
            "--name",
            profile["frontend_app"],
            "--resource-group",
            profile["resource_group"],
            "--query",
            "name",
            "-o",
            "tsv",
        ],
        artifact_path=run_dir / "00_frontend_app_check.txt",
        cwd=REPO_ROOT,
    )

    storage_connection_string = resolve_storage_connection_string(
        profile=profile,
        run_dir=run_dir,
        source_container=source_container,
        target_container=target_container,
    )

    blob_service = BlobServiceClient.from_connection_string(storage_connection_string)
    source_exists = blob_service.get_container_client(source_container).exists()
    target_exists = blob_service.get_container_client(target_container).exists()
    if not source_exists or not target_exists:
        missing = []
        if not source_exists:
            missing.append(source_container)
        if not target_exists:
            missing.append(target_container)
        raise RuntimeError("Required blob container(s) missing: " + ", ".join(missing))
    (run_dir / "00_blob_containers_check.txt").write_text(
        (
            f"source_container={source_container} exists={source_exists}\n"
            f"target_container={target_container} exists={target_exists}\n"
        ),
        encoding="utf-8",
    )

    run_command(
        name="validate alias resolvable",
        cmd=build_blue_green_cmd(
            endpoint=endpoint,
            api_key=api_key,
            subcommand="show-alias",
            extra=["--alias", alias],
        ),
        artifact_path=run_dir / "00_alias_preflight.txt",
        cwd=REPO_ROOT,
    )
    return storage_connection_string


def build_image_tag(environment: str, candidate_index: str) -> str:
    suffix = _safe_token(candidate_index)[-32:]
    return f"monthly-{environment}-{_timestamp()}-{suffix}"


def write_rollback_pack(
    *,
    run_dir: Path,
    endpoint: str,
    api_key: Optional[str],
    alias: str,
    previous_index: str,
    profile: Dict[str, str],
    previous_backend_image: str,
    previous_frontend_image: str,
) -> None:
    alias_cmd = build_blue_green_cmd(
        endpoint=endpoint,
        api_key=api_key,
        subcommand="swap-alias",
        extra=["--alias", alias, "--index", previous_index],
    )

    lines = [
        "# Rollback Command Pack",
        "",
        "## 1) Repoint alias",
        " ".join(alias_cmd),
        "",
    ]
    if previous_backend_image:
        lines.extend(
            [
                "## 2) Restore backend image",
                " ".join(
                    [
                        "az",
                        "containerapp",
                        "update",
                        "--name",
                        profile["backend_app"],
                        "--resource-group",
                        profile["resource_group"],
                        "--image",
                        previous_backend_image,
                    ]
                ),
                "",
            ]
        )
    if previous_frontend_image:
        lines.extend(
            [
                "## 3) Restore frontend image",
                " ".join(
                    [
                        "az",
                        "containerapp",
                        "update",
                        "--name",
                        profile["frontend_app"],
                        "--resource-group",
                        profile["resource_group"],
                        "--image",
                        previous_frontend_image,
                    ]
                ),
                "",
            ]
        )

    lines.extend(
        [
            "## 4) Verify rollback",
            "curl -fsS https://<backend-fqdn>/health",
            f"{sys.executable} {SCRIPTS_ROOT / 'verify_hr_retrieval_regressions.py'} "
            "--base-url https://<backend-fqdn> --strict-policy-number",
            "",
        ]
    )
    (run_dir / "rollback_command.txt").write_text("\n".join(lines), encoding="utf-8")


def _build_promptfoo_audit(
    rows: List[Dict[str, Any]], *, report_path: Path
) -> Dict[str, Any]:
    total = len(rows)
    passed = sum(
        1 for row in rows if isinstance(row, dict) and row.get("success", False)
    )
    flagged = 0
    cited = 0
    refusals = 0

    for row in rows:
        if not isinstance(row, dict):
            continue
        metadata = row.get("metadata") or {}
        flags = metadata.get("safetyFlags") or []
        if any(PROMPTFOO_SAFETY_FLAG_PATTERN.search(str(flag)) for flag in flags):
            flagged += 1
        sources = metadata.get("sourcesCount") or 0
        evidence = metadata.get("evidenceCount") or 0
        if sources > 0 or evidence > 0:
            cited += 1
        response = row.get("response") or {}
        output = response.get("output") if isinstance(response, dict) else None
        if output and PROMPTFOO_REFUSAL_PATTERN.search(output):
            refusals += 1

    pass_rate = passed / total if total else 0.0
    safety_flag_rate = flagged / total if total else 0.0
    citation_coverage_rate = cited / total if total else 0.0
    refusal_rate = refusals / total if total else 0.0

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "report_path": str(report_path),
        "total": total,
        "passed": passed,
        "pass_rate": pass_rate,
        "safety_flagged": flagged,
        "safety_flag_rate": safety_flag_rate,
        "citation_coverage": cited,
        "citation_coverage_rate": citation_coverage_rate,
        "refusal_count": refusals,
        "refusal_rate": refusal_rate,
        "thresholds": {
            "pass_rate": PROMPTFOO_PASS_RATE_THRESHOLD,
            "safety_flag_rate_max": PROMPTFOO_SAFETY_FLAG_RATE_MAX,
            "citation_coverage_min": PROMPTFOO_CITATION_COVERAGE_MIN,
        },
    }


def _run_ragas_regression_gate(*, run_dir: Path, backend_url: str) -> None:
    """Run RAGAS v0.4 regression test against golden test set (non-blocking)."""
    golden_set = REPO_ROOT / "data" / "golden_test_set.json"
    ragas_script = REPO_ROOT / "scripts" / "run_ragas_regression.py"

    if not golden_set.exists():
        print("[WARN] Golden test set not found; skipping RAGAS regression gate")
        (run_dir / "13c_ragas_regression.txt").write_text(
            "SKIPPED: data/golden_test_set.json not found\n", encoding="utf-8"
        )
        return

    if not ragas_script.exists():
        print("[WARN] RAGAS regression script not found; skipping RAGAS gate")
        (run_dir / "13c_ragas_regression.txt").write_text(
            "SKIPPED: scripts/run_ragas_regression.py not found\n", encoding="utf-8"
        )
        return

    output_path = run_dir / "13c_ragas_results.json"
    print(f"[RUN] RAGAS regression test (backend={backend_url})")
    result = subprocess.run(
        [
            sys.executable,
            str(ragas_script),
            "--golden-set",
            str(golden_set),
            "--backend-url",
            backend_url,
            "--output",
            str(output_path),
        ],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
    )
    (run_dir / "13c_ragas_regression.txt").write_text(
        result.stdout + result.stderr, encoding="utf-8"
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"RAGAS regression gate FAILED (exit code {result.returncode}). "
            f"See {run_dir / '13c_ragas_regression.txt'} for details.\n"
            f"Error: {result.stderr[:500]}"
        )
    else:
        print("      RAGAS regression gate passed")

    # Parse results for summary if available
    if output_path.exists():
        try:
            ragas_data = json.loads(output_path.read_text(encoding="utf-8"))
            status = ragas_data.get("regression_check", {}).get("status", "N/A")
            metrics = ragas_data.get("metrics", {})
            print(f"      RAGAS status: {status}")
            for metric, score in metrics.items():
                print(
                    f"        {metric}: {score:.3f}"
                    if isinstance(score, float)
                    else f"        {metric}: {score}"
                )
        except Exception as e:
            print(f"[WARN] Failed to parse RAGAS results: {e}")


def _run_promptfoo_gate(*, run_dir: Path, backend_url: str) -> None:
    """Run Promptfoo RAG evaluation and enforce compliance thresholds."""
    import shutil as _shutil

    npx = _shutil.which("npx")
    if not npx:
        print("[WARN] npx not found; skipping Promptfoo gate")
        (run_dir / "13b_promptfoo_eval.txt").write_text(
            "SKIPPED: npx not found on PATH\n", encoding="utf-8"
        )
        return

    config_path = REPO_ROOT / "tests" / "promptfoo" / "promptfooconfig.yaml"
    report_path = run_dir / "13b_promptfoo_eval.json"

    env = os.environ.copy()
    env["BACKEND_URL"] = backend_url

    print(f"[RUN] Promptfoo RAG evaluation (backend={backend_url})")
    result = subprocess.run(
        [
            npx,
            "promptfoo",
            "eval",
            "-c",
            str(config_path),
            "-o",
            str(report_path),
            "--no-progress-bar",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
        capture_output=True,
        timeout=600,
    )

    log_text = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
    log_path = run_dir / "13b_promptfoo_eval.txt"
    log_path.write_text(log_text[:50_000], encoding="utf-8")

    if not report_path.exists():
        raise RuntimeError(
            f"Promptfoo evaluation did not produce a report at {report_path}. "
            f"Exit code: {result.returncode}"
        )

    data = json.loads(report_path.read_text(encoding="utf-8"))
    results_obj = data.get("results", data)

    # Require per-test results for auditing
    rows = results_obj.get("results", []) if isinstance(results_obj, dict) else []
    if not (isinstance(rows, list) and rows):
        raise RuntimeError(
            "Promptfoo report missing per-test results; cannot compute compliance audit."
        )

    audit = _build_promptfoo_audit(rows, report_path=report_path)
    report_dir = REPO_ROOT / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    audit_path = report_dir / f"promptfoo_audit_{_timestamp()}.json"
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")
    _shutil.copy2(audit_path, run_dir / "13b_promptfoo_audit.json")

    summary_lines = [
        "",
        "Promptfoo audit summary:",
        (
            f"  Pass rate: {audit['passed']}/{audit['total']} "
            f"({audit['pass_rate'] * 100:.1f}%)"
        ),
        (
            f"  Safety flag rate: {audit['safety_flagged']}/{audit['total']} "
            f"({audit['safety_flag_rate'] * 100:.1f}%)"
        ),
        (
            f"  Citation coverage: {audit['citation_coverage']}/{audit['total']} "
            f"({audit['citation_coverage_rate'] * 100:.1f}%)"
        ),
        (
            f"  Refusal rate: {audit['refusal_count']}/{audit['total']} "
            f"({audit['refusal_rate'] * 100:.1f}%)"
        ),
        f"  Audit report: {audit_path}",
        "",
    ]
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(summary_lines))

    print(
        "      Promptfoo: "
        f"{audit['passed']}/{audit['total']} passed "
        f"({audit['pass_rate'] * 100:.1f}%)"
    )
    print(
        "      Compliance: "
        f"safety {audit['safety_flag_rate'] * 100:.1f}%, "
        f"citations {audit['citation_coverage_rate'] * 100:.1f}%"
    )

    failures = []
    if audit["pass_rate"] < PROMPTFOO_PASS_RATE_THRESHOLD:
        failures.append(
            f"Pass rate {audit['pass_rate'] * 100:.1f}% < {PROMPTFOO_PASS_RATE_THRESHOLD * 100:.0f}%"
        )
    if audit["safety_flag_rate"] > PROMPTFOO_SAFETY_FLAG_RATE_MAX:
        failures.append(
            f"Safety flag rate {audit['safety_flag_rate'] * 100:.1f}% > {PROMPTFOO_SAFETY_FLAG_RATE_MAX * 100:.0f}%"
        )
    if audit["citation_coverage_rate"] < PROMPTFOO_CITATION_COVERAGE_MIN:
        failures.append(
            f"Citation coverage {audit['citation_coverage_rate'] * 100:.1f}% < {PROMPTFOO_CITATION_COVERAGE_MIN * 100:.0f}%"
        )

    if failures:
        details = "\n".join(f" - {entry}" for entry in failures)
        raise RuntimeError(
            "Promptfoo RAG compliance gate FAILED:\n" f"{details}\nSee {audit_path}"
        )


def _load_latest_baseline(
    storage_connection_string: str,
) -> Optional[Dict[str, Any]]:
    """Load the most recent evaluation baseline from Azure Blob Storage."""
    try:
        blob_service = BlobServiceClient.from_connection_string(
            storage_connection_string
        )
        container_client = blob_service.get_container_client("evaluation-baselines")
        if not container_client.exists():
            return None
        blob_client = container_client.get_blob_client("latest_baseline.json")
        content = blob_client.download_blob().readall().decode("utf-8")
        return json.loads(content)
    except Exception as exc:
        print(f"[WARN] Could not load latest baseline: {exc}")
        return None


def _compare_against_baseline(
    current_audit: Optional[Dict[str, Any]],
    baseline: Dict[str, Any],
) -> Dict[str, Any]:
    """Compare current release scores against the previous baseline."""
    checks: List[Dict[str, Any]] = []

    # Compare PromptFoo pass rate
    baseline_pf = baseline.get("promptfoo_audit") or {}
    if current_audit and baseline_pf:
        pass_drop = baseline_pf.get("pass_rate", 0) - current_audit.get("pass_rate", 0)
        checks.append(
            {
                "metric": "promptfoo_pass_rate",
                "baseline": baseline_pf.get("pass_rate", 0),
                "current": current_audit.get("pass_rate", 0),
                "delta": -pass_drop,
                "threshold": BASELINE_PASS_RATE_MAX_DROP,
                "status": "FAIL" if pass_drop > BASELINE_PASS_RATE_MAX_DROP else "PASS",
            }
        )

        safety_increase = current_audit.get("safety_flag_rate", 0) - baseline_pf.get(
            "safety_flag_rate", 0
        )
        checks.append(
            {
                "metric": "promptfoo_safety_flag_rate",
                "baseline": baseline_pf.get("safety_flag_rate", 0),
                "current": current_audit.get("safety_flag_rate", 0),
                "delta": safety_increase,
                "threshold": BASELINE_SAFETY_MAX_INCREASE,
                "status": (
                    "FAIL" if safety_increase > BASELINE_SAFETY_MAX_INCREASE else "PASS"
                ),
            }
        )

    # Compare audit snapshot (found rate, latency)
    baseline_snap = baseline.get("audit_snapshot") or {}
    if (
        baseline_snap.get("total_queries", 0) > 0
        and baseline_snap.get("found_rate") is not None
    ):
        # We compare against the baseline's audit snapshot only if present
        # Current audit snapshot will be collected when persist_evaluation_baseline runs
        pass  # Audit snapshot comparison deferred to drift report

    failed = [c for c in checks if c["status"] == "FAIL"]
    return {
        "overall_status": "FAIL" if failed else "PASS",
        "checks": checks,
        "failures": len(failed),
        "baseline_id": baseline.get("baseline_id", "unknown"),
        "baseline_index": (baseline.get("release_info") or {}).get(
            "candidate_index", "unknown"
        ),
    }


def _run_baseline_gate(
    *,
    run_dir: Path,
    storage_connection_string: str,
) -> None:
    """Load previous baseline and compare current release scores against it."""
    print("[RUN] Baseline comparison gate")

    baseline = _load_latest_baseline(storage_connection_string)
    if baseline is None:
        print("      No previous baseline found (first run). Skipping baseline gate.")
        (run_dir / "13c_baseline_comparison.json").write_text(
            json.dumps(
                {"status": "skipped", "reason": "no previous baseline"}, indent=2
            ),
            encoding="utf-8",
        )
        return

    print(
        f"      Loaded baseline: {baseline.get('baseline_id', 'unknown')[:12]}... "
        f"(index: {(baseline.get('release_info') or {}).get('candidate_index', '?')})"
    )

    # Load current PromptFoo audit from this run
    current_audit = None
    audit_path = run_dir / "13b_promptfoo_audit.json"
    if audit_path.exists():
        current_audit = json.loads(audit_path.read_text(encoding="utf-8"))

    comparison = _compare_against_baseline(current_audit, baseline)

    comparison_path = run_dir / "13c_baseline_comparison.json"
    comparison_path.write_text(json.dumps(comparison, indent=2), encoding="utf-8")

    for check in comparison["checks"]:
        icon = "PASS" if check["status"] == "PASS" else "FAIL"
        print(
            f"      [{icon}] {check['metric']}: "
            f"baseline={check['baseline']:.3f} current={check['current']:.3f} "
            f"(threshold={check['threshold']:.3f})"
        )

    if comparison["overall_status"] == "FAIL":
        failures = [c for c in comparison["checks"] if c["status"] == "FAIL"]
        details = "\n".join(
            f" - {c['metric']}: {c['current']:.3f} vs baseline {c['baseline']:.3f} "
            f"(max delta {c['threshold']:.3f})"
            for c in failures
        )
        raise RuntimeError(
            f"Baseline comparison gate FAILED:\n{details}\n" f"See {comparison_path}"
        )

    print("      Baseline comparison: PASS")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manual Azure CLI monthly HR release flow"
    )
    parser.add_argument(
        "--run-mode",
        required=True,
        choices=["baseline", "monthly"],
        help="Execution mode: full source baseline rebuild or monthly delta promotion",
    )
    parser.add_argument(
        "--environment",
        required=True,
        choices=["nonprod", "prod"],
        help="Release profile environment",
    )
    parser.add_argument(
        "--profile-file",
        default=str(DEFAULT_PROFILE_FILE),
        help="Path to monthly release profile JSON",
    )
    parser.add_argument(
        "--alias", default=None, help="Override alias name from profile"
    )
    parser.add_argument(
        "--candidate-index",
        default=f"rush-policies-v2-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
        help="Candidate index name for this monthly cycle",
    )
    parser.add_argument(
        "--source-container",
        default=None,
        help="Override source blob container used by policy_sync detect/sync",
    )
    parser.add_argument(
        "--target-container",
        default=None,
        help="Override target blob container used by policy_sync detect/sync",
    )
    parser.add_argument(
        "--source-prefix",
        default="",
        help=(
            "Optional source folder prefix (e.g., 2026-03). "
            "Empty means container root."
        ),
    )
    parser.add_argument(
        "--previous-manifest",
        default=None,
        help=(
            "Optional prior manifest_current.json path. If omitted, the latest "
            "compatible successful run manifest under report-root is used."
        ),
    )
    parser.add_argument(
        "--no-previous-manifest",
        action="store_true",
        help=(
            "Ignore historical manifests and treat current source snapshot as full "
            "baseline-style comparison (monthly mode only)."
        ),
    )
    parser.add_argument(
        "--metadata-gate-mode",
        default="quarantine",
        choices=["quarantine", "fail", "autofill"],
        help="Metadata enforcement behavior for per-PDF validation",
    )
    parser.add_argument(
        "--metadata-report-path",
        default=None,
        help=(
            "Optional explicit path for metadata completeness JSON output. "
            "Defaults to run artifact path."
        ),
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help=(
            "Validation backend URL for pre-cutover checks. "
            "Typically a local backend pointed at candidate index."
        ),
    )
    parser.add_argument(
        "--endpoint", default=None, help="Azure Search endpoint override"
    )
    parser.add_argument("--api-key", default=None, help="Azure Search API key override")
    parser.add_argument(
        "--admin-api-key",
        default=os.environ.get("ADMIN_API_KEY"),
        help="Admin key for /health/detailed checks",
    )
    parser.add_argument(
        "--report-root",
        default=str(REPO_ROOT / "reports" / "monthly"),
        help="Root path for monthly release artifacts",
    )
    parser.add_argument(
        "--skip-cutover",
        action="store_true",
        help="Run all validations but do not swap alias or deploy containers",
    )
    parser.add_argument(
        "--skip-deploy",
        action="store_true",
        help="Swap alias but skip ACR build + container redeploy",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Bypass interactive approval prompt (still records approval artifact)",
    )
    parser.add_argument(
        "--image-tag",
        default=None,
        help="Optional explicit image tag for backend/frontend ACR builds",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    args = parse_args()

    profile = load_release_profile(Path(args.profile_file), args.environment)
    alias = args.alias or profile["search_alias"]
    endpoint = (
        args.endpoint or profile["search_endpoint"] or os.environ.get("SEARCH_ENDPOINT")
    )
    api_key = args.api_key or os.environ.get("SEARCH_API_KEY")
    source_container = args.source_container or profile["source_container"]
    target_container = args.target_container or profile["target_container"]
    image_tag = args.image_tag or build_image_tag(
        args.environment, args.candidate_index
    )

    if not endpoint:
        raise RuntimeError(
            "Search endpoint is required via profile.search_endpoint or --endpoint"
        )

    run_dir = (
        Path(args.report_root)
        / _month_folder()
        / (
            f"run_{_timestamp()}_{args.environment}_{args.run_mode}_"
            f"{_safe_token(args.candidate_index)}"
        )
    )
    run_dir.mkdir(parents=True, exist_ok=True)

    summary: Dict[str, Any] = {
        "environment": args.environment,
        "profile_file": str(Path(args.profile_file)),
        "subscription": profile["subscription"],
        "resource_group": profile["resource_group"],
        "alias": alias,
        "candidate_index": args.candidate_index,
        "source_container": source_container,
        "target_container": target_container,
        "source_prefix": args.source_prefix,
        "run_mode": args.run_mode,
        "validation_base_url": args.base_url,
        "run_dir": str(run_dir),
        "status": "running",
        "skip_cutover": args.skip_cutover,
        "skip_deploy": args.skip_deploy,
        "metadata_gate_mode": args.metadata_gate_mode,
    }

    print("=" * 88)
    print("MANUAL AZURE CLI MONTHLY RELEASE FLOW")
    print("=" * 88)
    print(f"Environment: {args.environment}")
    print(f"Resource Group: {profile['resource_group']}")
    print(f"Run Mode: {args.run_mode}")
    print(f"Alias: {alias}")
    print(f"Candidate Index: {args.candidate_index}")
    print(f"Source Prefix: {args.source_prefix or '(root)'}")
    print(f"Metadata Gate: {args.metadata_gate_mode}")
    print(f"Artifacts: {run_dir}")

    try:
        # Preflight
        storage_connection_string = run_azure_preflight(
            profile=profile,
            run_dir=run_dir,
            source_container=source_container,
            target_container=target_container,
            alias=alias,
            endpoint=endpoint,
            api_key=api_key,
        )

        # Preflight: Audit lifecycle cleanup (enforce retention policy)
        cleanup_script = REPO_ROOT / "scripts" / "audit_lifecycle_cleanup.py"
        if cleanup_script.exists():
            print("[RUN] Audit lifecycle cleanup (preflight)")
            cleanup_result = subprocess.run(
                [sys.executable, str(cleanup_script)],
                cwd=str(REPO_ROOT),
                text=True,
                capture_output=True,
            )
            (run_dir / "00_audit_lifecycle_cleanup.txt").write_text(
                (cleanup_result.stdout or "") + (cleanup_result.stderr or ""),
                encoding="utf-8",
            )
            if cleanup_result.returncode != 0:
                print(
                    f"[WARN] Audit cleanup failed (non-blocking): {cleanup_result.stderr[:200]}"
                )
            else:
                print("      Audit lifecycle cleanup completed")

        # Stage A: Alias + baseline capture
        alias_before_output = run_command(
            name="show-alias (before)",
            cmd=build_blue_green_cmd(
                endpoint=endpoint,
                api_key=api_key,
                subcommand="show-alias",
                extra=["--alias", alias],
            ),
            artifact_path=run_dir / "01_alias_before.txt",
            cwd=REPO_ROOT,
        )
        targets_before = parse_alias_targets(alias_before_output)
        previous_index = targets_before[0]
        summary["previous_index"] = previous_index

        previous_backend_image = run_az_query(
            name="capture backend image",
            args=[
                "containerapp",
                "show",
                "--name",
                profile["backend_app"],
                "--resource-group",
                profile["resource_group"],
                "--query",
                "properties.template.containers[0].image",
                "-o",
                "tsv",
            ],
            artifact_path=run_dir / "02_backend_image_before.txt",
            cwd=REPO_ROOT,
        ).strip()

        previous_frontend_image = run_az_query(
            name="capture frontend image",
            args=[
                "containerapp",
                "show",
                "--name",
                profile["frontend_app"],
                "--resource-group",
                profile["resource_group"],
                "--query",
                "properties.template.containers[0].image",
                "-o",
                "tsv",
            ],
            artifact_path=run_dir / "03_frontend_image_before.txt",
            cwd=REPO_ROOT,
        ).strip()

        write_rollback_pack(
            run_dir=run_dir,
            endpoint=endpoint,
            api_key=api_key,
            alias=alias,
            previous_index=previous_index,
            profile=profile,
            previous_backend_image=previous_backend_image,
            previous_frontend_image=previous_frontend_image,
        )

        # Stage B: Build manifests + compute delta
        previous_manifest_file: Optional[Path] = None
        if args.run_mode == "monthly" and not args.no_previous_manifest:
            previous_manifest_file = resolve_previous_manifest(
                previous_manifest_path=args.previous_manifest,
                report_root=Path(args.report_root),
                environment=args.environment,
                source_container=source_container,
                source_prefix=args.source_prefix,
            )
            if previous_manifest_file is None:
                raise RuntimeError(
                    "No compatible previous manifest found for monthly delta. "
                    "Provide --previous-manifest explicitly or run with --no-previous-manifest "
                    "to force a full snapshot comparison."
                )
        previous_manifest: Optional[Dict[str, Any]] = None
        if previous_manifest_file and previous_manifest_file.exists():
            previous_manifest = json.loads(
                previous_manifest_file.read_text(encoding="utf-8")
            )

        current_manifest = build_manifest(
            storage_connection_string=storage_connection_string,
            container=source_container,
            source_prefix=args.source_prefix,
        )

        # Build fallback manifest from target container when no previous manifest exists
        target_container_manifest = None
        if previous_manifest is None:
            print(
                "[INFO] No previous manifest found, building from target container metadata..."
            )
            target_container_manifest = build_manifest_from_target_container(
                storage_connection_string=storage_connection_string,
                container=target_container,
            )
            print(
                f"      Target container has {target_container_manifest['count']} indexed PDFs"
            )

        delta_report = build_delta_for_run_mode(
            run_mode=args.run_mode,
            current_manifest=current_manifest,
            previous_manifest=previous_manifest,
            target_container_manifest=target_container_manifest,
        )

        detect_counts = {
            "new": int(delta_report["counts"]["new"]),
            "changed": int(delta_report["counts"]["changed"]),
            "deleted": int(delta_report["counts"]["missing"]),
        }
        (run_dir / "manifest_current.json").write_text(
            json.dumps(current_manifest, indent=2), encoding="utf-8"
        )
        (run_dir / "manifest_previous.json").write_text(
            json.dumps(previous_manifest or {"entries": []}, indent=2), encoding="utf-8"
        )
        (run_dir / "delta_report.json").write_text(
            json.dumps(delta_report, indent=2), encoding="utf-8"
        )
        summary["detect_counts"] = detect_counts
        summary["delta_collision_count"] = int(
            delta_report["counts"].get("collisions", 0)
        )
        summary["manifest_current_count"] = current_manifest.get("count", 0)
        summary["manifest_previous_path"] = (
            str(previous_manifest_file) if previous_manifest_file else None
        )
        total_changes = detect_counts["new"] + detect_counts["changed"]
        summary["total_changes"] = total_changes

        if args.run_mode == "monthly" and total_changes == 0:
            run_health_smoke_checks(
                base_url=args.base_url,
                admin_api_key=args.admin_api_key,
                artifact_path=run_dir / "05_precheck_health_no_changes.txt",
            )
            run_command(
                name="HR regressions (no changes)",
                cmd=[
                    sys.executable,
                    "scripts/verify_hr_retrieval_regressions.py",
                    "--base-url",
                    args.base_url,
                    "--strict-policy-number",
                ],
                artifact_path=run_dir / "06_hr_regressions_no_changes.txt",
                cwd=BACKEND_ROOT,
            )
            summary["status"] = "no_changes"
            summary["notes"] = (
                "No new/changed documents detected (missing files kept active). "
                "Skipped candidate build, cutover, and redeploy."
            )
            (run_dir / "summary.json").write_text(
                json.dumps(summary, indent=2), encoding="utf-8"
            )
            print("[DONE] No monthly changes detected; heavy pipeline stages skipped.")
            return 0

        # Stage C: Candidate index preparation
        run_command(
            name="create-index (candidate)",
            cmd=build_blue_green_cmd(
                endpoint=endpoint,
                api_key=api_key,
                subcommand="create-index",
                extra=["--index", args.candidate_index],
            ),
            artifact_path=run_dir / "07_create_index.txt",
            cwd=REPO_ROOT,
        )
        run_command(
            name="validate-schema (candidate)",
            cmd=build_blue_green_cmd(
                endpoint=endpoint,
                api_key=api_key,
                subcommand="validate-schema",
                extra=["--index", args.candidate_index],
            ),
            artifact_path=run_dir / "08_candidate_schema.txt",
            cwd=REPO_ROOT,
        )
        if should_clone_active_index(args.run_mode):
            clone_stats = clone_active_index_documents(
                endpoint=endpoint,
                api_key=api_key,
                source_index=previous_index,
                target_index=args.candidate_index,
                artifact_path=run_dir / "09_clone_active_index.txt",
            )
        else:
            clone_stats = {
                "source_total": 0,
                "copied": 0,
                "upload_failures": 0,
                "target_total": 0,
                "skipped": True,
                "reason": "baseline mode uses source-only sync (no clone)",
            }
            (run_dir / "09_clone_active_index.txt").write_text(
                "clone skipped: baseline mode uses source-only candidate build\n",
                encoding="utf-8",
            )
        summary["clone_stats"] = clone_stats

        # Stage D: Sync docs into candidate index
        sync_env = {"SEARCH_INDEX_NAME": args.candidate_index}
        metadata_report_path = (
            Path(args.metadata_report_path)
            if args.metadata_report_path
            else (run_dir / "metadata_completeness_report.json")
        )
        quarantine_report_path = run_dir / "quarantine_report.json"
        sync_cmd = [
            sys.executable,
            "policy_sync.py",
            "sync-delta",
            source_container,
            target_container,
            "--delta-file",
            str(run_dir / "delta_report.json"),
            "--metadata-gate-mode",
            args.metadata_gate_mode,
            "--metadata-report",
            str(metadata_report_path),
            "--quarantine-report",
            str(quarantine_report_path),
        ]
        if should_clone_active_index(args.run_mode):
            sync_cmd.append("--keep-missing-active")

        sync_output = run_command(
            name=(
                "policy_sync sync-delta (candidate monthly)"
                if args.run_mode == "monthly"
                else "policy_sync sync-delta (candidate baseline)"
            ),
            cmd=sync_cmd,
            artifact_path=run_dir / "10_sync_report.txt",
            cwd=BACKEND_ROOT,
            extra_env=sync_env,
        )
        sync_counts = parse_change_counts(sync_output)
        summary["sync_counts"] = sync_counts
        summary["metadata_report_path"] = str(metadata_report_path)
        summary["quarantine_report_path"] = str(quarantine_report_path)
        validate_sync_matches_detect(detect_counts, sync_counts)

        # Stage E: Quality + regressions + backend targeted tests
        run_command(
            name="ingestion quality audit",
            cmd=[
                sys.executable,
                "scripts/audit_ingestion_quality.py",
                "--index-name",
                args.candidate_index,
                "--allow-direct-index",
                "--output",
                str(run_dir / "11_ingestion_audit.json"),
            ],
            artifact_path=run_dir / "11_ingestion_audit.txt",
            cwd=BACKEND_ROOT,
        )
        run_command(
            name="HR regressions (pre-cutover)",
            cmd=[
                sys.executable,
                "scripts/verify_hr_retrieval_regressions.py",
                "--base-url",
                args.base_url,
                "--strict-policy-number",
            ],
            artifact_path=run_dir / "12_hr_regressions_pre_cutover.txt",
            cwd=BACKEND_ROOT,
        )
        run_command(
            name="targeted backend regression suite",
            cmd=[sys.executable, "-m", "pytest", "-q"] + TARGETED_TEST_FILES,
            artifact_path=run_dir / "13_targeted_backend_tests.txt",
            cwd=BACKEND_ROOT,
        )

        # Stage E.2: Promptfoo RAG evaluation (90% pass + safety/citation gates)
        _run_promptfoo_gate(run_dir=run_dir, backend_url=args.base_url)

        # Stage E.3: RAGAS regression gate (golden test set faithfulness/recall)
        _run_ragas_regression_gate(run_dir=run_dir, backend_url=args.base_url)

        # Stage E.4: Baseline comparison gate (compare against previous release)
        _run_baseline_gate(
            run_dir=run_dir,
            storage_connection_string=storage_connection_string,
        )

        if args.skip_cutover:
            summary["status"] = "pre_cutover_complete"
            summary["notes"] = "Cutover/deploy skipped via --skip-cutover"
            (run_dir / "summary.json").write_text(
                json.dumps(summary, indent=2), encoding="utf-8"
            )
            print("[DONE] Pre-cutover stages completed. Alias swap/deploy skipped.")
            return 0

        # Stage F: Manual approval
        require_manual_approval(
            artifact_path=run_dir / "14_manual_approval.txt",
            auto_approve=args.auto_approve,
            run_mode=args.run_mode,
        )

        # Stage G: Alias cutover
        run_command(
            name="swap-alias (cutover)",
            cmd=build_blue_green_cmd(
                endpoint=endpoint,
                api_key=api_key,
                subcommand="swap-alias",
                extra=["--alias", alias, "--index", args.candidate_index],
            ),
            artifact_path=run_dir / "15_alias_swap.txt",
            cwd=REPO_ROOT,
        )
        alias_after_output = run_command(
            name="show-alias (after)",
            cmd=build_blue_green_cmd(
                endpoint=endpoint,
                api_key=api_key,
                subcommand="show-alias",
                extra=["--alias", alias],
            ),
            artifact_path=run_dir / "16_alias_after.txt",
            cwd=REPO_ROOT,
        )
        targets_after = parse_alias_targets(alias_after_output)
        if args.candidate_index not in targets_after:
            raise RuntimeError(
                f"Alias '{alias}' was not updated to '{args.candidate_index}'. "
                f"Current targets: {targets_after}"
            )

        backend_fqdn = run_az_query(
            name="resolve backend fqdn",
            args=[
                "containerapp",
                "show",
                "--name",
                profile["backend_app"],
                "--resource-group",
                profile["resource_group"],
                "--query",
                "properties.configuration.ingress.fqdn",
                "-o",
                "tsv",
            ],
            artifact_path=run_dir / "17_backend_fqdn_pre_deploy.txt",
            cwd=REPO_ROOT,
        ).strip()
        if not backend_fqdn:
            raise RuntimeError(
                "Failed to resolve backend FQDN for post-cutover verification."
            )

        deployed_backend_base_url = f"https://{backend_fqdn}"
        summary["deployed_backend_url"] = deployed_backend_base_url
        frontend_url = ""

        # Stage H: Deploy backend + frontend
        if not args.skip_deploy:
            backend_image = (
                f"{profile['acr_name']}.azurecr.io/rush-policy-backend:{image_tag}"
            )
            frontend_image = (
                f"{profile['acr_name']}.azurecr.io/rush-policy-frontend:{image_tag}"
            )
            summary["backend_image"] = backend_image
            summary["frontend_image"] = frontend_image

            run_az_query(
                name="ACR build backend",
                args=[
                    "acr",
                    "build",
                    "--registry",
                    profile["acr_name"],
                    "--image",
                    f"rush-policy-backend:{image_tag}",
                    "apps/backend",
                ],
                artifact_path=run_dir / "17_acr_build_backend.txt",
                cwd=REPO_ROOT,
            )
            run_az_query(
                name="ContainerApp update backend image",
                args=[
                    "containerapp",
                    "update",
                    "--name",
                    profile["backend_app"],
                    "--resource-group",
                    profile["resource_group"],
                    "--image",
                    backend_image,
                    "--set-env-vars",
                    f"SEARCH_INDEX_NAME={alias}",
                ],
                artifact_path=run_dir / "18_backend_deploy.txt",
                cwd=REPO_ROOT,
            )
            backend_fqdn = run_az_query(
                name="resolve backend fqdn (post-deploy)",
                args=[
                    "containerapp",
                    "show",
                    "--name",
                    profile["backend_app"],
                    "--resource-group",
                    profile["resource_group"],
                    "--query",
                    "properties.configuration.ingress.fqdn",
                    "-o",
                    "tsv",
                ],
                artifact_path=run_dir / "19_backend_fqdn.txt",
                cwd=REPO_ROOT,
            ).strip()
            if not backend_fqdn:
                raise RuntimeError("Failed to resolve backend FQDN after deploy.")
            deployed_backend_base_url = f"https://{backend_fqdn}"
            summary["deployed_backend_url"] = deployed_backend_base_url

            run_az_query(
                name="ACR build frontend",
                args=[
                    "acr",
                    "build",
                    "--registry",
                    profile["acr_name"],
                    "--image",
                    f"rush-policy-frontend:{image_tag}",
                    "--build-arg",
                    f"BACKEND_URL={deployed_backend_base_url}",
                    "apps/frontend",
                ],
                artifact_path=run_dir / "20_acr_build_frontend.txt",
                cwd=REPO_ROOT,
            )
            run_az_query(
                name="ContainerApp update frontend image",
                args=[
                    "containerapp",
                    "update",
                    "--name",
                    profile["frontend_app"],
                    "--resource-group",
                    profile["resource_group"],
                    "--image",
                    frontend_image,
                    "--set-env-vars",
                    f"BACKEND_URL={deployed_backend_base_url}",
                    f"NEXT_PUBLIC_API_URL={deployed_backend_base_url}",
                ],
                artifact_path=run_dir / "21_frontend_deploy.txt",
                cwd=REPO_ROOT,
            )
            frontend_fqdn = run_az_query(
                name="resolve frontend fqdn",
                args=[
                    "containerapp",
                    "show",
                    "--name",
                    profile["frontend_app"],
                    "--resource-group",
                    profile["resource_group"],
                    "--query",
                    "properties.configuration.ingress.fqdn",
                    "-o",
                    "tsv",
                ],
                artifact_path=run_dir / "22_frontend_fqdn.txt",
                cwd=REPO_ROOT,
            ).strip()
            if frontend_fqdn:
                frontend_url = f"https://{frontend_fqdn}"
                summary["deployed_frontend_url"] = frontend_url
        else:
            summary["notes"] = "Deployment skipped via --skip-deploy"

        # Stage I: Post-cutover smoke + regressions
        run_health_smoke_checks(
            base_url=deployed_backend_base_url,
            admin_api_key=args.admin_api_key,
            artifact_path=run_dir / "23_post_cutover_health.txt",
        )
        if frontend_url:
            run_frontend_smoke_check(
                frontend_url=frontend_url,
                artifact_path=run_dir / "24_frontend_smoke.txt",
            )
        run_command(
            name="HR regressions (post-cutover)",
            cmd=[
                sys.executable,
                "scripts/verify_hr_retrieval_regressions.py",
                "--base-url",
                deployed_backend_base_url,
                "--strict-policy-number",
            ],
            artifact_path=run_dir / "25_hr_regressions_post_cutover.txt",
            cwd=BACKEND_ROOT,
        )

        summary["status"] = "success"
        summary["alias_targets_after"] = ",".join(targets_after)
        summary["image_tag"] = image_tag
        (run_dir / "summary.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )

        # Persist evaluation baseline for future cross-release comparison
        persist_script = REPO_ROOT / "scripts" / "persist_evaluation_baseline.py"
        if persist_script.exists():
            print("[RUN] Persisting evaluation baseline")
            persist_cmd = [
                sys.executable,
                str(persist_script),
                "--run-dir",
                str(run_dir),
            ]
            persist_result = subprocess.run(
                persist_cmd,
                cwd=str(REPO_ROOT),
                text=True,
                capture_output=True,
            )
            if persist_result.returncode != 0:
                print(
                    f"[WARN] Baseline persistence failed (non-blocking): {persist_result.stderr[:500]}"
                )
            else:
                print("      Evaluation baseline persisted successfully")

        print("[DONE] Monthly Azure CLI release flow completed successfully.")
        return 0

    except Exception as exc:
        summary["status"] = "failed"
        summary["error"] = str(exc)
        (run_dir / "summary.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        print(f"[ERROR] {exc}")
        print(f"[INFO] See artifacts in {run_dir}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
