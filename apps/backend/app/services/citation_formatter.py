from __future__ import annotations

from dataclasses import dataclass
from typing import List

from app.models.schemas import EvidenceItem


@dataclass
class CitationFormattingResult:
    """Represents the formatted response with reference lines appended."""

    summary: str
    response: str
    references: List[str]


class CitationFormatter:
    """Normalizes response text to include deterministic citation references."""

    def __init__(self) -> None:
        # Precompute strings to avoid recreating them on each call
        self._reference_prefix = "Ref #"

    def format(
        self,
        answer_text: str,
        evidence: List[EvidenceItem],
        *,
        max_refs: int = 5,
        found: bool = True,
    ) -> CitationFormattingResult:
        """Append standardized reference markers to the answer text."""

        clean_answer = (answer_text or "").strip()

        if not clean_answer:
            return CitationFormattingResult(summary="", response="", references=[])

        if not found or not evidence:
            return CitationFormattingResult(summary=clean_answer, response=clean_answer, references=[])

        references, total_refs = self._collect_references(evidence)
        if not references:
            return CitationFormattingResult(summary=clean_answer, response=clean_answer, references=[])

        limited = references[:max_refs] if max_refs > 0 else references
        reference_lines = self._build_reference_lines(limited)

        remaining = total_refs - len(limited)
        if remaining > 0:
            reference_lines.append(
                f"… plus {remaining} additional reference{'s' if remaining != 1 else ''} in evidence cards."
            )

        summary_with_refs = f"{clean_answer}\n\n" + "\n".join(reference_lines)

        return CitationFormattingResult(
            summary=summary_with_refs.strip(),
            response=summary_with_refs.strip(),
            references=reference_lines,
        )

    def _collect_references(self, evidence: List[EvidenceItem]) -> tuple[List[dict], int]:
        """Deduplicate evidence by reference number while preserving order."""

        unique_refs: List[dict] = []
        seen = set()

        for item in evidence:
            ref = self._extract_reference_number(item)
            if not ref or ref in seen:
                continue

            unique_refs.append(
                {
                    "ref": ref,
                    "title": (item.title or "Referenced Policy").strip(),
                    "applies_to": (item.applies_to or "").strip(),
                    "section": (item.section or "").strip(),
                }
            )
            seen.add(ref)

        return unique_refs, len(unique_refs)

    def _build_reference_lines(self, references: List[dict]) -> List[str]:
        lines: List[str] = []
        for idx, data in enumerate(references, start=1):
            title = data["title"] or "Referenced Policy"
            line = f"{idx}. {self._reference_prefix}{data['ref']} — {title}"

            details = []
            if data["section"]:
                details.append(f"Section: {data['section']}")
            if data["applies_to"]:
                details.append(f"Applies To: {data['applies_to']}")

            if details:
                line += f" ({'; '.join(details)})"

            lines.append(line)

        return lines

    def _extract_reference_number(self, item: EvidenceItem) -> str:
        """Return the best available reference identifier for an evidence item."""

        if item.reference_number:
            return item.reference_number.strip()

        if item.citation:
            import re

            match = re.search(r"Ref\s*[#:]*\s*([A-Za-z0-9.\-]+)", item.citation, re.IGNORECASE)
            if match:
                return match.group(1).strip()

        return ""


_formatter: CitationFormatter | None = None


def get_citation_formatter() -> CitationFormatter:
    global _formatter
    if _formatter is None:
        _formatter = CitationFormatter()
    return _formatter
