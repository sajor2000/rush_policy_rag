# Plan: Fix "Applies To" Checkbox Extraction

> **STATUS: IMPLEMENTED** (2024-11-26)
>
> This plan has been successfully implemented using PyMuPDF (Option C). See:
> - `apps/backend/preprocessing/chunker.py::_extract_applies_to_from_raw_pdf()` (lines 687-750)
> - Fallback chain: PyMuPDF → Docling → Regex
> - NPO Policy now correctly extracts `Applies To: RUMC, RUMG, ROPH, RCH`

## Problem Statement

The NPO policy (Ref #2867) shows `applies_to: "RUMC"` but the PDF has checkboxes for **RUMC, RUMG, ROPH, RCH** all checked. Docling extracts only partial text: `"Applies To: RUMC ☒"` instead of the full checkbox row.

**Root Cause**: RUSH policy PDFs use actual PDF form fields (AcroForm checkboxes), not just visual Unicode checkbox characters. Docling's TableFormer extracts the visual text but doesn't read the underlying form field data.

---

## Solution Options

### Option A: pypdf Form Field Extraction (Recommended)
**Approach**: Use `pypdf` library to directly read PDF AcroForm checkbox states before/alongside Docling processing.

**Pros**:
- Directly reads PDF form field data (the authoritative source)
- Works with any PDF that has actual form checkboxes
- Simple API: `reader.get_fields()` returns all form fields with values
- Already a Python standard library for PDF forms

**Cons**:
- Additional dependency (though lightweight)
- May not work if checkboxes are visual-only (not form fields)

**Implementation**:
```python
from pypdf import PdfReader

def extract_checkbox_fields(pdf_path: str) -> dict[str, bool]:
    """Extract checkbox states from PDF form fields."""
    reader = PdfReader(pdf_path)
    fields = reader.get_fields() or {}

    checkbox_states = {}
    for field_name, field_obj in fields.items():
        # Checkbox values are typically "/Yes" or "/Off"
        if hasattr(field_obj, 'value'):
            value = str(field_obj.value)
            checkbox_states[field_name] = value in ['/Yes', '/1', 'Yes', '1', '/On']

    return checkbox_states
```

### Option B: Docling `do_cell_matching` Option
**Approach**: Enable `do_cell_matching=False` in Docling's table structure options to prevent column merging.

**Pros**:
- Pure Docling solution (no additional library)
- May improve table column separation

**Cons**:
- This is for table cell matching, not form field extraction
- Unlikely to solve the root cause (form fields vs visual elements)

**Implementation** (in chunker.py line 353-357):
```python
pipeline_options = PdfPipelineOptions(
    do_table_structure=True,
    do_ocr=False,
)
pipeline_options.table_structure_options.mode = TableFormerMode.ACCURATE
pipeline_options.table_structure_options.do_cell_matching = False  # Try this
```

### Option C: PyMuPDF (fitz) Form Field Extraction
**Approach**: Use PyMuPDF to extract form widget data.

**Pros**:
- Very powerful PDF manipulation library
- Can read form fields, annotations, and widgets

**Cons**:
- Heavier dependency
- More complex API

**Implementation**:
```python
import fitz  # PyMuPDF

def extract_form_checkboxes(pdf_path: str) -> dict[str, bool]:
    """Extract checkbox states using PyMuPDF."""
    doc = fitz.open(pdf_path)
    checkboxes = {}

    for page in doc:
        for widget in page.widgets():
            if widget.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
                checkboxes[widget.field_name] = widget.field_value == "Yes"

    return checkboxes
```

---

## Recommended Implementation Plan

### Phase 1: Investigate (30 min)
1. Download a test PDF (NPO policy) locally
2. Use pypdf to check if form fields exist
3. Print field names and values to understand structure

### Phase 2: Implement Hybrid Approach (1-2 hrs)
1. Add pypdf as dependency: `pip install pypdf`
2. Create new method `_extract_applies_to_from_form_fields()` in chunker.py
3. Call this method BEFORE Docling checkbox detection as primary source
4. Fall back to existing methods if no form fields found

### Phase 3: Verify (30 min)
1. Test on NPO policy PDF
2. Re-run full ingestion pipeline
3. Query Azure Search to verify `applies_to` field is correct

---

## Code Changes Required

### File: `apps/backend/preprocessing/chunker.py`

```python
# Add import at top
from pypdf import PdfReader

# Add new method to PolicyChunker class (around line 650)
def _extract_applies_to_from_form_fields(self, pdf_path: str) -> List[str]:
    """
    Extract Applies To entities from PDF form fields.

    RUSH PDFs use AcroForm checkboxes for entity selection.
    Field names typically contain entity codes like 'RUMC', 'RUMG', etc.
    """
    checked_entities = []

    try:
        reader = PdfReader(pdf_path)
        fields = reader.get_fields() or {}

        for field_name, field_obj in fields.items():
            # Check if this is an Applies To checkbox
            field_name_upper = field_name.upper()

            # Get checkbox value (varies by PDF creator)
            value = None
            if hasattr(field_obj, 'value'):
                value = str(field_obj.value) if field_obj.value else ""
            elif '/V' in field_obj:
                value = str(field_obj['/V'])

            # Check if checkbox is checked
            is_checked = value in ['/Yes', '/1', 'Yes', '1', '/On', 'On', '/True', 'True']

            if is_checked:
                # Match entity code in field name
                for entity in RUSH_ENTITIES:
                    if entity in field_name_upper:
                        if entity not in checked_entities:
                            checked_entities.append(entity)
                            logger.debug(f"Found checked entity via form field: {entity} ({field_name})")
                        break

    except Exception as e:
        logger.warning(f"PDF form field extraction failed: {e}")

    return checked_entities

# Modify _extract_header_metadata method (around line 537)
# Change from:
#     if not metadata.applies_to:
#         metadata.applies_to = self._extract_applies_to_from_checkboxes(doc)
# To:
    if not metadata.applies_to:
        # Try form fields first (most reliable for RUSH PDFs)
        metadata.applies_to = self._extract_applies_to_from_form_fields(pdf_path)

    if not metadata.applies_to:
        # Fall back to Docling checkbox detection
        metadata.applies_to = self._extract_applies_to_from_checkboxes(doc)
```

### File: `apps/backend/requirements.txt`
```
pypdf>=4.0.0
```

---

## Testing Plan

1. **Unit Test**: Extract form fields from NPO PDF, verify all 4 entities detected
2. **Integration Test**: Run `chunker.py` CLI on NPO PDF, verify `applies_to` output
3. **E2E Test**: Re-run full pipeline, query search index for NPO policy

---

## Rollback Plan

If pypdf approach doesn't work:
1. Keep existing Docling + regex fallback methods
2. Consider manual metadata override for known problematic PDFs
3. Investigate if PDFs can be regenerated with proper form structure

---

## Success Criteria

- [ ] NPO policy (Ref #2867) shows `applies_to: "RUMC, RUMG, ROPH, RCH"`
- [ ] All 50 PDFs in pipeline show correct entity detection
- [ ] No regression in existing functionality
- [ ] Test suite passes

---

## References

- pypdf forms documentation: https://github.com/py-pdf/pypdf/blob/main/docs/user/forms.md
- Docling v2 options: https://github.com/docling-project/docling/blob/main/docs/v2.md
- Current chunker implementation: `apps/backend/preprocessing/chunker.py`
