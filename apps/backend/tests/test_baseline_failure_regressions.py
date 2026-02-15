import importlib.util
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _load_module(module_name: str, relative_path: str):
    path = BACKEND_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_metadata_contract_uses_policy_chunk_text_field():
    policy_sync = _load_module("policy_sync", "policy_sync.py")

    chunk = policy_sync.PolicyChunk(
        chunk_id="c1",
        policy_title="HR-C 05.00 Shift Differentials",
        policy_number="HR-C 05.00",
        reference_number="",
        section_number="1.0",
        section_title="Overview",
        text="Exact chunk text",
        date_updated="2026-02-12",
        applies_to="RUMC",
        source_file="HR-C 05.00.pdf",
        char_count=16,
        page_number=1,
    )

    report = policy_sync.PolicySyncManager._validate_document_metadata_contract(
        object(),
        filename="HR-C 05.00.pdf",
        chunks=[chunk],
        content_hash="abc123",
        processed_date="2026-02-12T00:00:00",
    )

    assert report["valid"] is True
    assert "content" not in report["issues"]


def test_audit_report_handles_zero_files_without_division_error():
    audit = _load_module(
        "audit_ingestion_quality", "scripts/audit_ingestion_quality.py"
    )

    report = audit.AuditReport(files_audited=0)
    # Should not raise ZeroDivisionError when no files are audited.
    audit.print_report(report)
