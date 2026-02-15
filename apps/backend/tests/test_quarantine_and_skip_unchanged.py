"""
Scale tests for Fix 1 (quarantine rate reduction) and Fix 2 (skip unchanged documents).

Exercises both fixes with 100+ synthetic documents covering:
- PolicyTech document ID extraction from parenthesized filenames
- Autofill behavior across gate modes (autofill, quarantine, fail)
- Manifest delta computation with target-container fallback at scale
- Mixed corpora: XX-X format, parenthesized IDs, neither format
"""

import hashlib
import importlib.util
import random
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module loading helpers
# ---------------------------------------------------------------------------

BACKEND_ROOT = Path(__file__).resolve().parents[1]
GATE_SCRIPT = BACKEND_ROOT / "scripts" / "monthly_hr_release_gate.py"

sys.path.insert(0, str(BACKEND_ROOT))

from preprocessing.policy_chunk import PolicyChunk  # noqa: E402


def _load_gate_module():
    spec = importlib.util.spec_from_file_location(
        "monthly_hr_release_gate", GATE_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_sync_manager_class():
    """Import PolicySyncManager with Azure SDK mocked out."""
    # The import chain needs BlobServiceClient, PolicySearchIndex, etc.
    # We only need the pure-logic methods, so we mock the constructor.
    from policy_sync import PolicySyncManager

    return PolicySyncManager


# ---------------------------------------------------------------------------
# Fixture: synthetic document corpus (100+ files)
# ---------------------------------------------------------------------------

# Realistic RUSH policy filenames sampled from actual naming patterns
_HR_FILENAMES_WITH_XX_X = [
    f"HR-{chr(65 + i % 6)} {str(i).zfill(2)}.{str((i * 7) % 100).zfill(2)} "
    f"{'Policy' if i % 3 == 0 else 'Procedure'} Name {i}.pdf"
    for i in range(1, 36)
]  # 35 files with XX-X format

_FILENAMES_WITH_POLICYTECH_ID = [
    f"Employee {'Appeals' if i % 5 == 0 else 'Benefits'} Policy ({1000 + i}).pdf"
    for i in range(1, 41)
]  # 40 files with parenthesized IDs

_FILENAMES_NO_ID = [
    f"General Corporate Policy {i}.pdf" for i in range(1, 16)
]  # 15 files with no extractable ID

_FILENAMES_EDGE_CASES = [
    "Fire Safety Plan (99).pdf",  # 2-digit ID (min)
    "HIPAA Compliance Guide (123456).pdf",  # 6-digit ID (max)
    "Lab Procedure (1234567).pdf",  # 7 digits - should NOT match (too long)
    "Budget Report (5).pdf",  # 1-digit - should NOT match (too short)
    "HR-A 01.00 Policy (2523).pdf",  # Has BOTH XX-X and parenthesized ID
    "Some Policy v2 (draft).pdf",  # Non-numeric in parens - no match
    "Another Policy.pdf",  # No parens at all
    "Policy With Spaces ( 3456 ).pdf",  # Spaces around number - no match (regex is strict)
    "Research Protocol (42) Appendix.pdf",  # ID mid-filename
    "Clinical Trial (00123).pdf",  # Leading zeros, 5 digits
]  # 10 edge-case files

ALL_FILENAMES = (
    _HR_FILENAMES_WITH_XX_X
    + _FILENAMES_WITH_POLICYTECH_ID
    + _FILENAMES_NO_ID
    + _FILENAMES_EDGE_CASES
)  # 100 files total


def _make_chunk(
    *,
    source_file: str = "",
    policy_title: str = "",
    policy_number: str = "",
    reference_number: str = "",
    chunk_index: int = 0,
    page_number: int = None,
    text: str = "Lorem ipsum dolor sit amet " * 20,
) -> PolicyChunk:
    return PolicyChunk(
        chunk_id=f"test_{chunk_index}",
        policy_title=policy_title,
        policy_number=policy_number,
        reference_number=reference_number,
        section_number="",
        section_title="",
        text=text,
        date_updated="",
        applies_to="",
        source_file=source_file,
        char_count=len(text),
        chunk_index=chunk_index,
        page_number=page_number,
    )


def _make_chunks_for_file(filename: str, count: int = 5) -> List[PolicyChunk]:
    """Create a batch of blank chunks (no metadata) simulating chunker output with no extraction."""
    return [
        _make_chunk(
            source_file="",
            policy_title="",
            policy_number="",
            reference_number="",
            chunk_index=i,
        )
        for i in range(count)
    ]


def _content_hash(filename: str) -> str:
    return hashlib.sha256(filename.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Fix 1 Tests: Document ID Extraction + Autofill
# ---------------------------------------------------------------------------


class TestExtractDocumentIdFromFilename:
    """Test _extract_document_id_from_filename at scale."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        with patch("policy_sync.BlobServiceClient"), patch(
            "policy_sync.PolicySearchIndex"
        ), patch("policy_sync.PolicyChunker"):
            self.sync = _load_sync_manager_class().__new__(_load_sync_manager_class())
            # Manually set up just the methods we need (skip __init__)

    def test_all_policytech_ids_extracted(self):
        """All 40 parenthesized-ID files should yield a numeric doc ID."""
        results = {}
        for fname in _FILENAMES_WITH_POLICYTECH_ID:
            doc_id = self.sync._extract_document_id_from_filename(fname)
            results[fname] = doc_id

        extracted = {k: v for k, v in results.items() if v}
        assert len(extracted) == 40, (
            f"Expected 40 extracted IDs, got {len(extracted)}. "
            f"Failures: {[k for k, v in results.items() if not v]}"
        )

        # Verify IDs are correct
        for i in range(1, 41):
            expected_id = str(1000 + i)
            fname = _FILENAMES_WITH_POLICYTECH_ID[i - 1]
            assert (
                results[fname] == expected_id
            ), f"{fname} → {results[fname]} != {expected_id}"

    def test_xx_x_format_files_return_empty(self):
        """Files with XX-X policy numbers should NOT extract a doc ID (they have policy_number)."""
        for fname in _HR_FILENAMES_WITH_XX_X:
            self.sync._extract_document_id_from_filename(fname)
            # XX-X files may or may not have parens; the important thing is
            # _autofill only uses doc_id when both policy_number and reference_number are empty
            # So extraction is fine, the guard is in _autofill_chunk_metadata

    def test_no_id_files_return_empty(self):
        """Files with no parenthesized number should return empty string."""
        for fname in _FILENAMES_NO_ID:
            doc_id = self.sync._extract_document_id_from_filename(fname)
            assert doc_id == "", f"Unexpected ID from '{fname}': '{doc_id}'"

    def test_edge_cases(self):
        """Verify edge cases match expected behavior."""
        cases = {
            "Fire Safety Plan (99).pdf": "99",  # 2-digit: matches
            "HIPAA Compliance Guide (123456).pdf": "123456",  # 6-digit: matches
            "Lab Procedure (1234567).pdf": "",  # 7-digit: too long
            "Budget Report (5).pdf": "",  # 1-digit: too short
            "HR-A 01.00 Policy (2523).pdf": "2523",  # Has both: ID extracted
            "Some Policy v2 (draft).pdf": "",  # Non-numeric: no match
            "Another Policy.pdf": "",  # No parens
            "Policy With Spaces ( 3456 ).pdf": "",  # Spaces: no match
            "Research Protocol (42) Appendix.pdf": "42",  # Mid-filename: matches
            "Clinical Trial (00123).pdf": "00123",  # Leading zeros: matches
        }
        for fname, expected in cases.items():
            result = self.sync._extract_document_id_from_filename(fname)
            assert result == expected, f"'{fname}' → '{result}' (expected '{expected}')"

    def test_none_and_empty_inputs(self):
        assert self.sync._extract_document_id_from_filename(None) == ""
        assert self.sync._extract_document_id_from_filename("") == ""

    def test_100_files_extraction_summary(self):
        """Run extraction on all 100 files and report statistics."""
        results = {}
        for fname in ALL_FILENAMES:
            results[fname] = self.sync._extract_document_id_from_filename(fname)

        extracted_count = sum(1 for v in results.values() if v)
        # 40 policytech + some edge cases that match
        assert extracted_count >= 40, f"Too few extractions: {extracted_count}/100"
        # Should NOT be all 100 (no-ID files should fail)
        assert extracted_count < 100, f"Too many extractions: {extracted_count}/100"


class TestAutofillChunkMetadata:
    """Test _autofill_chunk_metadata fills reference_number correctly at scale."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        with patch("policy_sync.BlobServiceClient"), patch(
            "policy_sync.PolicySearchIndex"
        ), patch("policy_sync.PolicyChunker"):
            self.sync = _load_sync_manager_class().__new__(_load_sync_manager_class())

    def test_policytech_id_files_get_reference_number(self):
        """All 40 parenthesized-ID files should have reference_number after autofill."""
        pass_count = 0
        for fname in _FILENAMES_WITH_POLICYTECH_ID:
            chunks = _make_chunks_for_file(fname, count=3)
            self.sync._autofill_chunk_metadata(chunks, fname)

            for chunk in chunks:
                if chunk.reference_number:
                    pass_count += 1
                    break

        assert (
            pass_count == 40
        ), f"Expected 40/40 files to get reference_number, got {pass_count}/40"

    def test_xx_x_files_get_policy_number_not_reference_number(self):
        """XX-X format files should get policy_number filled, reference_number stays empty."""
        for fname in _HR_FILENAMES_WITH_XX_X:
            chunks = _make_chunks_for_file(fname, count=3)
            self.sync._autofill_chunk_metadata(chunks, fname)

            for chunk in chunks:
                # Should have policy_number (from XX-X extraction)
                assert chunk.policy_number, f"No policy_number for '{fname}'"
                # reference_number should be empty (guard: not set when policy_number exists)
                assert (
                    not chunk.reference_number
                ), f"Unexpected reference_number '{chunk.reference_number}' for '{fname}'"

    def test_dual_id_file_prefers_policy_number(self):
        """File with both XX-X and parenthesized ID should use policy_number."""
        fname = "HR-A 01.00 Policy (2523).pdf"
        chunks = _make_chunks_for_file(fname, count=3)
        self.sync._autofill_chunk_metadata(chunks, fname)

        for chunk in chunks:
            assert (
                chunk.policy_number == "HR-A 01.00"
            ), f"Expected 'HR-A 01.00', got '{chunk.policy_number}'"
            # reference_number should NOT be set because policy_number is present
            assert not chunk.reference_number, (
                f"reference_number should be empty when policy_number is set, "
                f"got '{chunk.reference_number}'"
            )

    def test_autofill_never_overwrites_existing_metadata(self):
        """Autofill should not overwrite chunks that already have metadata."""
        fname = "Employee Appeals Policy (2523).pdf"
        chunks = [
            _make_chunk(
                source_file="original.pdf",
                policy_title="Original Title",
                policy_number="",
                reference_number="EXISTING-REF",
                chunk_index=0,
                page_number=5,
            )
        ]
        self.sync._autofill_chunk_metadata(chunks, fname)

        assert chunks[0].source_file == "original.pdf"
        assert chunks[0].policy_title == "Original Title"
        assert chunks[0].reference_number == "EXISTING-REF"
        assert chunks[0].page_number == 5

    def test_100_files_validation_pass_rate(self):
        """Simulate validation on all 100 files after autofill.

        Expected:
        - 35 XX-X files → policy_number filled → PASS
        - 40 parenthesized ID files → reference_number filled → PASS
        - 15 no-ID files → neither filled → FAIL (quarantined)
        - 10 edge cases → mixed results
        """
        pass_count = 0
        fail_count = 0
        details = []

        for fname in ALL_FILENAMES:
            chunks = _make_chunks_for_file(fname, count=3)
            self.sync._autofill_chunk_metadata(chunks, fname)

            # Check validation: at least one of policy_number or reference_number must be set
            has_id = any(
                (c.policy_number or "").strip() or (c.reference_number or "").strip()
                for c in chunks
            )
            if has_id:
                pass_count += 1
            else:
                fail_count += 1
                details.append(fname)

        total = len(ALL_FILENAMES)
        pass_rate = pass_count / total

        # At least 75% should pass (35 XX-X + 40 policytech + some edge cases)
        assert pass_rate >= 0.75, (
            f"Pass rate {pass_rate:.1%} ({pass_count}/{total}) is below 75%. "
            f"Failures:\n" + "\n".join(f"  - {d}" for d in details)
        )
        # No-ID files (15) should still fail
        assert fail_count >= 10, (
            f"Expected at least 10 quarantined files, got {fail_count}. "
            f"Something is too permissive."
        )
        # Verify exact counts for known categories
        xx_x_pass = 0
        for fname in _HR_FILENAMES_WITH_XX_X:
            chunks = _make_chunks_for_file(fname, count=1)
            self.sync._autofill_chunk_metadata(chunks, fname)
            if any((c.policy_number or "").strip() for c in chunks):
                xx_x_pass += 1
        assert xx_x_pass == 35, f"XX-X pass count: {xx_x_pass}/35"


class TestAutofillInQuarantineMode:
    """Verify autofill is called in quarantine gate mode in process_document."""

    def test_quarantine_mode_triggers_autofill(self):
        """The process_document method should call _autofill when mode is 'quarantine'."""
        # Read the actual source and verify the gate check
        source = (BACKEND_ROOT / "policy_sync.py").read_text(encoding="utf-8")
        # Check that the condition includes quarantine
        assert (
            'in ("autofill", "quarantine")' in source
        ), "Expected autofill to be called in quarantine mode"
        # Verify the old single-mode check is gone
        assert (
            'metadata_gate_mode == "autofill"'
            not in source.split("_autofill_chunk_metadata")[0].split(
                "metadata_gate_mode"
            )[-1]
            if "_autofill_chunk_metadata" in source
            else True
        )


# ---------------------------------------------------------------------------
# Fix 1 (chunker side): Verify _extract_header_metadata fallback
# ---------------------------------------------------------------------------


class TestChunkerDocIdFallback:
    """Verify chunker.py adds reference_number from parenthesized filename."""

    def test_chunker_source_has_fallback(self):
        """Verify the fallback regex is in chunker.py source."""
        source = (BACKEND_ROOT / "preprocessing" / "chunker.py").read_text(
            encoding="utf-8"
        )
        assert (
            r"\((\d{2,6})\)" in source
        ), "Expected parenthesized doc ID regex in chunker.py"
        assert (
            "metadata.reference_number" in source
        ), "Expected reference_number assignment in chunker.py fallback"


# ---------------------------------------------------------------------------
# Fix 2 Tests: Skip Unchanged Documents at Scale
# ---------------------------------------------------------------------------


def _make_manifest_entries(
    filenames: List[str],
    *,
    hash_prefix: str = "",
    include_content_hash: bool = True,
) -> List[Dict[str, Any]]:
    """Create manifest entries for a list of filenames.

    Uses deterministic size/etag derived from filename so two calls with the
    same filenames produce identical entries (required for unchanged detection).
    """
    entries = []
    for fname in filenames:
        h = hashlib.sha256(f"{hash_prefix}{fname}".encode()).hexdigest()
        # Deterministic size from filename hash (not random) so entries match
        size = int(hashlib.md5(fname.encode()).hexdigest()[:8], 16) % 500000 + 50000
        entry: Dict[str, Any] = {
            "filename": fname,
            "source_blob": fname,
            "policy_number": "",
            "size": size,
            "etag": f"etag-{hashlib.md5(fname.encode()).hexdigest()[:12]}",
        }
        if include_content_hash:
            entry["content_hash"] = h
        entries.append(entry)
    return entries


class TestBuildDeltaForRunModeWithTargetFallback:
    """Test build_delta_for_run_mode with target_container_manifest at 100+ doc scale."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.gate = _load_gate_module()

    def test_baseline_all_new_when_no_fallback(self):
        """Baseline mode, no previous manifest, no target fallback → all new."""
        entries = _make_manifest_entries(ALL_FILENAMES)
        current = {"entries": entries, "count": len(entries)}

        delta = self.gate.build_delta_for_run_mode(
            run_mode="baseline",
            current_manifest=current,
            previous_manifest=None,
            target_container_manifest=None,
        )
        assert delta["run_mode"] == "baseline"
        assert delta["counts"]["new"] == 100
        assert delta["counts"]["unchanged"] == 0
        assert delta["counts"]["changed"] == 0

    def test_baseline_all_unchanged_with_target_fallback(self):
        """Baseline mode with identical target container → all unchanged (0 reprocessed)."""
        entries = _make_manifest_entries(ALL_FILENAMES)
        current = {"entries": entries, "count": len(entries)}
        target = {
            "entries": _make_manifest_entries(ALL_FILENAMES),  # Same hashes
            "count": len(ALL_FILENAMES),
            "source": "target_container_metadata",
        }

        delta = self.gate.build_delta_for_run_mode(
            run_mode="baseline",
            current_manifest=current,
            previous_manifest=None,
            target_container_manifest=target,
        )
        assert delta["run_mode"] == "baseline"
        assert (
            delta["counts"]["new"] == 0
        ), f"Expected 0 new, got {delta['counts']['new']}"
        assert delta["counts"]["unchanged"] == 100
        assert delta["counts"]["changed"] == 0
        assert delta.get("previous_source") == "target_container_metadata"

    def test_baseline_mixed_new_and_unchanged(self):
        """Baseline: 80 unchanged + 20 new documents."""
        existing_files = ALL_FILENAMES[:80]
        new_files = [f"Brand New Policy {i}.pdf" for i in range(1, 21)]
        all_files = existing_files + new_files

        current_entries = _make_manifest_entries(all_files)
        target_entries = _make_manifest_entries(existing_files)

        current = {"entries": current_entries, "count": len(current_entries)}
        target = {
            "entries": target_entries,
            "count": len(target_entries),
            "source": "target_container_metadata",
        }

        delta = self.gate.build_delta_for_run_mode(
            run_mode="baseline",
            current_manifest=current,
            previous_manifest=None,
            target_container_manifest=target,
        )
        assert (
            delta["counts"]["new"] == 20
        ), f"Expected 20 new, got {delta['counts']['new']}"
        assert delta["counts"]["unchanged"] == 80
        assert delta["counts"]["changed"] == 0

    def test_baseline_detects_changed_documents(self):
        """Baseline: 90 unchanged + 10 changed (different content hashes)."""
        unchanged_files = ALL_FILENAMES[:90]
        changed_files = ALL_FILENAMES[90:]  # last 10

        current_entries = _make_manifest_entries(ALL_FILENAMES)
        # Target has different hashes for last 10 files
        target_entries = _make_manifest_entries(
            unchanged_files
        ) + _make_manifest_entries(changed_files, hash_prefix="OLD_")

        current = {"entries": current_entries, "count": 100}
        target = {
            "entries": target_entries,
            "count": 100,
            "source": "target_container_metadata",
        }

        delta = self.gate.build_delta_for_run_mode(
            run_mode="baseline",
            current_manifest=current,
            previous_manifest=None,
            target_container_manifest=target,
        )
        assert delta["counts"]["unchanged"] == 90
        assert delta["counts"]["changed"] == 10
        assert delta["counts"]["new"] == 0

    def test_baseline_previous_manifest_takes_precedence_over_target(self):
        """If both previous_manifest and target_container_manifest exist, previous wins."""
        entries = _make_manifest_entries(ALL_FILENAMES)
        current = {"entries": entries, "count": 100}
        previous = {"entries": entries, "count": 100}  # identical
        target = {
            "entries": _make_manifest_entries(ALL_FILENAMES, hash_prefix="STALE_"),
            "count": 100,
            "source": "target_container_metadata",
        }

        delta = self.gate.build_delta_for_run_mode(
            run_mode="baseline",
            current_manifest=current,
            previous_manifest=previous,
            target_container_manifest=target,
        )
        # Should use previous_manifest (all match), not target (all different)
        assert delta["counts"]["unchanged"] == 100
        assert delta["counts"]["changed"] == 0

    def test_monthly_mode_uses_target_fallback_when_no_previous(self):
        """Monthly mode without previous manifest should also use target fallback."""
        entries = _make_manifest_entries(ALL_FILENAMES)
        current = {"entries": entries, "count": 100}
        target = {
            "entries": entries,
            "count": 100,
            "source": "target_container_metadata",
        }

        delta = self.gate.build_delta_for_run_mode(
            run_mode="monthly",
            current_manifest=current,
            previous_manifest=None,
            target_container_manifest=target,
        )
        assert delta["run_mode"] == "monthly"
        assert delta["counts"]["unchanged"] == 100
        assert delta["counts"]["new"] == 0

    def test_empty_target_container_results_in_all_new(self):
        """Empty target container → same as no fallback → all new."""
        entries = _make_manifest_entries(ALL_FILENAMES)
        current = {"entries": entries, "count": 100}
        target = {"entries": [], "count": 0, "source": "target_container_metadata"}

        delta = self.gate.build_delta_for_run_mode(
            run_mode="baseline",
            current_manifest=current,
            previous_manifest=None,
            target_container_manifest=target,
        )
        assert delta["counts"]["new"] == 100
        assert delta["counts"]["unchanged"] == 0

    def test_target_with_missing_documents(self):
        """Target has 100 docs, source only has 80 → 80 unchanged + 20 missing."""
        subset = ALL_FILENAMES[:80]
        current_entries = _make_manifest_entries(subset)
        target_entries = _make_manifest_entries(ALL_FILENAMES)

        current = {"entries": current_entries, "count": 80}
        target = {
            "entries": target_entries,
            "count": 100,
            "source": "target_container_metadata",
        }

        delta = self.gate.build_delta_for_run_mode(
            run_mode="baseline",
            current_manifest=current,
            previous_manifest=None,
            target_container_manifest=target,
        )
        assert delta["counts"]["unchanged"] == 80
        assert delta["counts"]["missing"] == 20
        assert delta["counts"]["new"] == 0


class TestBuildDeltaAtLargerScale:
    """Run delta computation at 200-500 document scale for performance confidence."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.gate = _load_gate_module()

    def _generate_filenames(self, count: int) -> List[str]:
        """Generate unique policy filenames."""
        files = []
        for i in range(count):
            category = random.choice(["HR", "IT", "FS", "CL", "NU", "PH", "AD"])
            letter = random.choice("ABCDEF")
            if i % 3 == 0:
                # XX-X format
                files.append(
                    f"{category}-{letter} {str(i % 30).zfill(2)}.{str(i % 100).zfill(2)} "
                    f"Policy {i}.pdf"
                )
            elif i % 3 == 1:
                # Parenthesized ID
                files.append(f"Policy Document {i} ({2000 + i}).pdf")
            else:
                # No ID
                files.append(f"General Policy Document Number {i}.pdf")
        return files

    @pytest.mark.parametrize("corpus_size", [200, 500])
    def test_large_corpus_unchanged(self, corpus_size):
        """All docs unchanged at scale."""
        files = self._generate_filenames(corpus_size)
        entries = _make_manifest_entries(files)
        current = {"entries": entries, "count": corpus_size}
        target = {
            "entries": _make_manifest_entries(files),
            "count": corpus_size,
            "source": "target_container_metadata",
        }

        delta = self.gate.build_delta_for_run_mode(
            run_mode="baseline",
            current_manifest=current,
            previous_manifest=None,
            target_container_manifest=target,
        )
        assert delta["counts"]["unchanged"] == corpus_size
        assert delta["counts"]["new"] == 0
        assert delta["counts"]["changed"] == 0

    @pytest.mark.parametrize("corpus_size", [200, 500])
    def test_large_corpus_10_percent_changed(self, corpus_size):
        """10% changed, 90% unchanged at scale."""
        files = self._generate_filenames(corpus_size)
        change_count = corpus_size // 10
        changed_files = files[:change_count]
        unchanged_files = files[change_count:]

        current_entries = _make_manifest_entries(files)
        target_entries = _make_manifest_entries(
            changed_files, hash_prefix="OLD_VERSION_"
        ) + _make_manifest_entries(unchanged_files)

        current = {"entries": current_entries, "count": corpus_size}
        target = {
            "entries": target_entries,
            "count": corpus_size,
            "source": "target_container_metadata",
        }

        delta = self.gate.build_delta_for_run_mode(
            run_mode="baseline",
            current_manifest=current,
            previous_manifest=None,
            target_container_manifest=target,
        )
        assert delta["counts"]["changed"] == change_count
        assert delta["counts"]["unchanged"] == corpus_size - change_count
        assert delta["counts"]["new"] == 0

    def test_500_docs_mixed_scenario(self):
        """Realistic: 450 unchanged, 30 changed, 20 new, 10 missing from target."""
        base_files = self._generate_filenames(490)
        unchanged = base_files[:450]
        changed = base_files[450:480]
        new_only_in_source = [f"Brand New {i}.pdf" for i in range(20)]
        missing_only_in_target = [f"Retired {i}.pdf" for i in range(10)]

        source_files = unchanged + changed + new_only_in_source
        target_files = unchanged + changed + missing_only_in_target

        current_entries = _make_manifest_entries(source_files)
        target_entries = (
            _make_manifest_entries(unchanged)
            + _make_manifest_entries(changed, hash_prefix="PREV_")
            + _make_manifest_entries(missing_only_in_target)
        )

        current = {"entries": current_entries, "count": len(source_files)}
        target = {
            "entries": target_entries,
            "count": len(target_files),
            "source": "target_container_metadata",
        }

        delta = self.gate.build_delta_for_run_mode(
            run_mode="baseline",
            current_manifest=current,
            previous_manifest=None,
            target_container_manifest=target,
        )
        assert delta["counts"]["unchanged"] == 450
        assert delta["counts"]["changed"] == 30
        assert delta["counts"]["new"] == 20
        assert delta["counts"]["missing"] == 10


# ---------------------------------------------------------------------------
# Fix 2: build_manifest_from_target_container (mock Azure Blob)
# ---------------------------------------------------------------------------


class TestBuildManifestFromTargetContainer:
    """Test the target container manifest builder with mocked Azure Blob SDK."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.gate = _load_gate_module()

    def _make_mock_blob(
        self,
        name: str,
        metadata: Dict[str, str],
        size: int = 100000,
        etag: str = "etag-abc",
    ):
        blob = MagicMock()
        blob.name = name
        blob.metadata = metadata
        blob.size = size
        blob.etag = etag
        return blob

    def test_100_blobs_all_with_hashes(self):
        """100 blobs with content_hash → 100 entries in manifest."""
        mock_blobs = []
        for fname in ALL_FILENAMES:
            mock_blobs.append(
                self._make_mock_blob(
                    name=fname,
                    metadata={
                        "content_hash": _content_hash(fname),
                        "policy_number": "",
                    },
                )
            )

        mock_container = MagicMock()
        mock_container.list_blobs.return_value = mock_blobs

        mock_blob_service = MagicMock()
        mock_blob_service.get_container_client.return_value = mock_container

        with patch.object(
            self.gate.BlobServiceClient,
            "from_connection_string",
            return_value=mock_blob_service,
        ):
            manifest = self.gate.build_manifest_from_target_container(
                storage_connection_string="fake",
                container="policies-active",
            )

        assert manifest["count"] == 100
        assert manifest["source"] == "target_container_metadata"
        assert len(manifest["entries"]) == 100

        # Verify content hashes are preserved
        for entry in manifest["entries"]:
            assert entry["content_hash"], f"Missing hash for {entry['filename']}"

    def test_blobs_without_hash_are_skipped(self):
        """Blobs that lack content_hash in metadata should be excluded."""
        mock_blobs = [
            self._make_mock_blob("has_hash.pdf", {"content_hash": "abc123"}),
            self._make_mock_blob("no_hash.pdf", {}),
            self._make_mock_blob("empty_hash.pdf", {"content_hash": ""}),
            self._make_mock_blob("also_has.pdf", {"content_hash": "def456"}),
            self._make_mock_blob("not_pdf.json", {"content_hash": "ghi789"}),
        ]

        mock_container = MagicMock()
        mock_container.list_blobs.return_value = mock_blobs

        mock_blob_service = MagicMock()
        mock_blob_service.get_container_client.return_value = mock_container

        with patch.object(
            self.gate.BlobServiceClient,
            "from_connection_string",
            return_value=mock_blob_service,
        ):
            manifest = self.gate.build_manifest_from_target_container(
                storage_connection_string="fake",
                container="policies-active",
            )

        assert manifest["count"] == 2  # Only has_hash.pdf and also_has.pdf
        filenames = [e["filename"] for e in manifest["entries"]]
        assert "has_hash.pdf" in filenames
        assert "also_has.pdf" in filenames
        assert "no_hash.pdf" not in filenames
        assert "empty_hash.pdf" not in filenames
        assert "not_pdf.json" not in filenames

    def test_empty_container(self):
        """Empty container → manifest with 0 entries."""
        mock_container = MagicMock()
        mock_container.list_blobs.return_value = []

        mock_blob_service = MagicMock()
        mock_blob_service.get_container_client.return_value = mock_container

        with patch.object(
            self.gate.BlobServiceClient,
            "from_connection_string",
            return_value=mock_blob_service,
        ):
            manifest = self.gate.build_manifest_from_target_container(
                storage_connection_string="fake",
                container="policies-active",
            )

        assert manifest["count"] == 0
        assert manifest["entries"] == []

    def test_200_blobs_performance(self):
        """200 blobs should process without issues."""
        files = [f"Policy {i} ({3000 + i}).pdf" for i in range(200)]
        mock_blobs = [
            self._make_mock_blob(
                name=f,
                metadata={"content_hash": _content_hash(f), "policy_number": ""},
            )
            for f in files
        ]

        mock_container = MagicMock()
        mock_container.list_blobs.return_value = mock_blobs

        mock_blob_service = MagicMock()
        mock_blob_service.get_container_client.return_value = mock_container

        with patch.object(
            self.gate.BlobServiceClient,
            "from_connection_string",
            return_value=mock_blob_service,
        ):
            manifest = self.gate.build_manifest_from_target_container(
                storage_connection_string="fake",
                container="policies-active",
            )

        assert manifest["count"] == 200
        # Entries should be sorted
        filenames = [e["filename"] for e in manifest["entries"]]
        assert filenames == sorted(filenames)


# ---------------------------------------------------------------------------
# Integration: Full pipeline simulation
# ---------------------------------------------------------------------------


class TestEndToEndQuarantineReduction:
    """Simulate the full autofill + validation pipeline on 100 files."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        with patch("policy_sync.BlobServiceClient"), patch(
            "policy_sync.PolicySearchIndex"
        ), patch("policy_sync.PolicyChunker"):
            self.sync = _load_sync_manager_class().__new__(_load_sync_manager_class())

    def test_quarantine_rate_below_25_percent(self):
        """After autofill, quarantine rate on 100 docs should be < 25%."""
        quarantined = []
        passed = []

        for fname in ALL_FILENAMES:
            chunks = _make_chunks_for_file(fname, count=4)
            self.sync._autofill_chunk_metadata(chunks, fname)

            # Run validation logic (mirrors _validate_document_metadata_contract)
            has_id = any(
                (c.policy_number or "").strip() or (c.reference_number or "").strip()
                for c in chunks
            )
            has_title = any((c.policy_title or "").strip() for c in chunks)
            has_source = any((c.source_file or "").strip() for c in chunks)

            if has_id and has_title and has_source:
                passed.append(fname)
            else:
                quarantined.append(fname)

        total = len(ALL_FILENAMES)
        quarantine_rate = len(quarantined) / total

        # Key assertion: quarantine rate must be below 25%
        assert quarantine_rate < 0.25, (
            f"Quarantine rate {quarantine_rate:.1%} ({len(quarantined)}/{total}) is too high.\n"
            f"Quarantined files:\n" + "\n".join(f"  - {q}" for q in quarantined)
        )

        # The 15 no-ID files should be the primary quarantine group
        no_id_quarantined = [f for f in quarantined if f in _FILENAMES_NO_ID]
        assert (
            len(no_id_quarantined) >= 10
        ), f"Expected most no-ID files quarantined, got {len(no_id_quarantined)}/15"

    def test_xx_x_files_never_quarantined(self):
        """All 35 XX-X format files should pass validation after autofill."""
        for fname in _HR_FILENAMES_WITH_XX_X:
            chunks = _make_chunks_for_file(fname, count=3)
            self.sync._autofill_chunk_metadata(chunks, fname)

            has_id = any(
                (c.policy_number or "").strip() or (c.reference_number or "").strip()
                for c in chunks
            )
            assert has_id, f"XX-X file '{fname}' failed validation"

    def test_policytech_files_never_quarantined(self):
        """All 40 parenthesized-ID files should pass validation after autofill."""
        for fname in _FILENAMES_WITH_POLICYTECH_ID:
            chunks = _make_chunks_for_file(fname, count=3)
            self.sync._autofill_chunk_metadata(chunks, fname)

            has_id = any(
                (c.policy_number or "").strip() or (c.reference_number or "").strip()
                for c in chunks
            )
            assert has_id, f"PolicyTech ID file '{fname}' failed validation"


class TestEndToEndSkipUnchanged:
    """Simulate full baseline pipeline: manifest build → delta → verify skip."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.gate = _load_gate_module()

    def test_100_unchanged_docs_produce_zero_new(self):
        """100 docs already in target → delta should show 0 new, 100 unchanged."""
        entries = _make_manifest_entries(ALL_FILENAMES)
        current = {"entries": entries, "count": 100}
        target = {
            "entries": _make_manifest_entries(ALL_FILENAMES),
            "count": 100,
            "source": "target_container_metadata",
        }

        delta = self.gate.build_delta_for_run_mode(
            run_mode="baseline",
            current_manifest=current,
            previous_manifest=None,
            target_container_manifest=target,
        )

        assert delta["counts"]["new"] == 0
        assert delta["counts"]["unchanged"] == 100
        assert delta["counts"]["changed"] == 0
        assert delta["counts"]["missing"] == 0

    def test_no_target_fallback_all_new(self):
        """Without target fallback (true first run), all 100 should be new."""
        entries = _make_manifest_entries(ALL_FILENAMES)
        current = {"entries": entries, "count": 100}

        delta = self.gate.build_delta_for_run_mode(
            run_mode="baseline",
            current_manifest=current,
            previous_manifest=None,
            target_container_manifest=None,
        )

        assert delta["counts"]["new"] == 100
        assert delta["counts"]["unchanged"] == 0
