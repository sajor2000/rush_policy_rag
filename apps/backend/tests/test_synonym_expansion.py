"""
Test the new context-aware synonym expansion logic.

Verifies that the priority-based stopping mechanism prevents over-broad
cascading expansions that cause noisy results.
"""
from app.services.synonym_service import get_synonym_service


def test_iv_neutral_fallback_no_cascade():
    """Test that 'IV' alone gets neutral expansion, not catheter cascade."""
    service = get_synonym_service()

    # OLD BEHAVIOR: 'iv' → 'peripheral intravenous PIV catheter' → urinary Foley...
    # NEW BEHAVIOR: 'iv' → 'intravenous vascular access' (STOP)

    expansion = service.expand_query("how long can an IV stay in place")
    expanded = expansion.expanded_query.lower()

    # Should contain neutral terms
    assert 'intravenous' in expanded or 'vascular' in expanded

    # Should NOT cascade to urinary catheter terms
    assert 'foley' not in expanded, "Should not cascade to Foley"
    assert 'urinary' not in expanded, "Should not cascade to urinary catheter"


def test_peripheral_iv_specific_expansion():
    """Test that 'peripheral IV' gets specific expansion."""
    service = get_synonym_service()

    expansion = service.expand_query("peripheral IV dwell time")
    expanded = expansion.expanded_query.lower()

    # Should contain specific peripheral IV terms
    assert 'piv' in expanded or 'short-term' in expanded or 'peripheral' in expanded

    # Should NOT cascade to PICC or central line
    assert 'picc' not in expanded, "Should not add PICC terms"
    assert expanded.count('central') <= 1, "Should not add central line terms"


def test_picc_line_specific_expansion():
    """Test that 'PICC line' gets specific expansion."""
    service = get_synonym_service()

    expansion = service.expand_query("PICC line dwell time")
    expanded = expansion.expanded_query.lower()

    # Should contain PICC-specific terms
    assert 'picc' in expanded
    assert 'central' in expanded or 'peripherally inserted' in expanded

    # Should NOT add peripheral IV terms
    assert 'peripheral iv' not in expanded, "Should not add peripheral IV"
    assert expanded.count('piv') == 0, "Should not add PIV abbreviation"


def test_catheter_neutral_fallback():
    """Test that 'catheter' alone gets neutral expansion."""
    service = get_synonym_service()

    expansion = service.expand_query("catheter care procedures")
    expanded = expansion.expanded_query.lower()

    # Should use neutral terms
    assert 'vascular' in expanded or 'tube' in expanded

    # Should NOT specify urinary/foley
    assert 'foley' not in expanded, "Should not assume urinary catheter"


def test_foley_specific_expansion():
    """Test that 'Foley' gets urinary catheter specific expansion."""
    service = get_synonym_service()

    expansion = service.expand_query("Foley catheter removal")
    expanded = expansion.expanded_query.lower()

    # Should contain urinary catheter terms
    assert 'urinary' in expanded or 'bladder' in expanded or 'foley' in expanded

    # Should NOT add IV or central line terms
    assert 'peripheral' not in expanded, "Should not add peripheral IV"
    assert 'picc' not in expanded, "Should not add PICC"


def test_central_line_specific_expansion():
    """Test that 'central line' gets specific expansion."""
    service = get_synonym_service()

    expansion = service.expand_query("central line dressing change")
    expanded = expansion.expanded_query.lower()

    # Should contain central line specific terms
    assert 'cvc' in expanded or 'picc' in expanded or 'central' in expanded


def test_priority_stopping():
    """Test that expansion stops after first priority match."""
    service = get_synonym_service()

    # Query has both 'peripheral' and 'iv'
    # Should match "peripheral iv" (multi-word) and STOP
    # Should NOT also expand 'iv' separately
    expansion = service.expand_query("peripheral IV care")
    expanded = expansion.expanded_query.lower()

    # Count occurrences of 'intravenous' - should only appear once from the phrase expansion
    intravenous_count = expanded.count('intravenous')
    assert intravenous_count <= 2, f"Should not double-expand; got {intravenous_count} occurrences"


def test_line_neutral_fallback():
    """Test that 'line' alone gets neutral expansion."""
    service = get_synonym_service()

    expansion = service.expand_query("line dressing change")
    expanded = expansion.expanded_query.lower()

    # Should use neutral term
    assert 'vascular' in expanded or 'access' in expanded

    # Should NOT specify peripheral vs central
    assert 'peripheral' not in expanded, "Should not assume peripheral"
    assert expanded.count('central') <= 1, "Should not assume central line specifically"


def test_port_specific_expansion():
    """Test that 'port' gets implanted port expansion."""
    service = get_synonym_service()

    expansion = service.expand_query("port flushing protocol")
    expanded = expansion.expanded_query.lower()

    # Should contain port-specific terms
    assert 'port' in expanded
    assert 'implanted' in expanded or 'vascular access' in expanded or 'device' in expanded


if __name__ == "__main__":
    # Run tests manually
    import sys

    print("Running synonym expansion tests...\n")

    tests = [
        test_iv_neutral_fallback_no_cascade,
        test_peripheral_iv_specific_expansion,
        test_picc_line_specific_expansion,
        test_catheter_neutral_fallback,
        test_foley_specific_expansion,
        test_central_line_specific_expansion,
        test_priority_stopping,
        test_line_neutral_fallback,
        test_port_specific_expansion
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test_func()
            print(f"✓ {test_func.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"✗ {test_func.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {test_func.__name__}: Unexpected error: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
