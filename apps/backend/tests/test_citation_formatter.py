from app.models.schemas import EvidenceItem
from app.services.citation_formatter import CitationFormatter


def build_evidence(ref: str, title: str, applies_to: str = "RUMC") -> EvidenceItem:
    return EvidenceItem(
        snippet="Example snippet",
        citation=f"{title} (Ref #{ref})",
        title=title,
        reference_number=ref,
        applies_to=applies_to,
    )


def test_formatter_appends_reference_lines():
    formatter = CitationFormatter()
    evidence = [build_evidence("486", "Verbal and Telephone Orders")]

    result = formatter.format(
        answer_text="Verbal orders must be authenticated within 72 hours.",
        evidence=evidence,
        max_refs=5,
        found=True,
    )

    assert "Ref #486" in result.summary
    assert result.summary.startswith("Verbal orders")
    assert result.references[0].startswith("1. Ref #486")


def test_formatter_respects_max_refs_and_deduplicates():
    formatter = CitationFormatter()
    evidence = [
        build_evidence("486", "Verbal and Telephone Orders"),
        build_evidence("486", "Verbal and Telephone Orders"),  # duplicate
        build_evidence("346", "Adult Rapid Response"),
        build_evidence("228", "Latex Management"),
    ]

    result = formatter.format(
        answer_text="Multiple policies govern this workflow.",
        evidence=evidence,
        max_refs=2,
        found=True,
    )

    # Only two unique references should appear despite duplicates (plus overflow note)
    assert result.references[0].startswith("1. Ref #486")
    assert result.references[1].startswith("2. Ref #346")
    assert "additional reference" in result.references[-1]
    assert "Ref #486" in result.summary
    assert "Ref #346" in result.summary
    assert "Ref #228" not in result.summary
