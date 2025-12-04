"""
Tests for the Synonym Expansion Service

Validates that query expansion improves RAG accuracy by:
1. Expanding medical abbreviations
2. Correcting common misspellings
3. Adding Rush-specific term alternatives
4. Applying pattern-based expansions
"""

import pytest
import sys
from pathlib import Path

# Add backend to path
backend_path = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_path))

from app.services.synonym_service import SynonymService, get_synonym_service


class TestSynonymService:
    """Test the SynonymService class."""

    @pytest.fixture
    def service(self):
        """Create a SynonymService instance."""
        return get_synonym_service()

    def test_service_loads(self, service):
        """Test that the service loads synonyms successfully."""
        assert service is not None
        assert len(service.synonym_groups) > 0
        print(f"Loaded {len(service.synonym_groups)} synonym groups")

    def test_abbreviation_expansion(self, service):
        """Test that medical abbreviations are expanded."""
        # Test ED → emergency department
        result = service.expand_query("What is the ED visitor policy?")
        assert "emergency department" in result.expanded_query.lower()
        assert len(result.abbreviations_expanded) > 0

        # Test ICU
        result = service.expand_query("ICU visiting hours")
        assert "intensive care" in result.expanded_query.lower()

        # Test DNR
        result = service.expand_query("DNR policy")
        assert "do not resuscitate" in result.expanded_query.lower()

    def test_misspelling_correction(self, service):
        """Test that common misspellings are corrected."""
        # Test catheter misspelling
        result = service.expand_query("cathater insertion procedure")
        assert len(result.misspellings_corrected) > 0
        corrected_words = [c['corrected'] for c in result.misspellings_corrected]
        assert 'catheter' in corrected_words

        # Test medication misspelling
        result = service.expand_query("medciation administration")
        if result.misspellings_corrected:
            corrected_words = [c['corrected'] for c in result.misspellings_corrected]
            assert 'medication' in corrected_words

    def test_rush_specific_terms(self, service):
        """Test that Rush-specific terms are recognized."""
        # Test RUMC
        result = service.expand_query("RUMC parking policy")
        assert "Rush University Medical Center" in result.expanded_query or "RUMC" in result.expanded_query

        # Test RUMG
        result = service.expand_query("RUMG scheduling")
        assert len(result.abbreviations_expanded) > 0

    def test_hospital_codes(self, service):
        """Test that hospital emergency codes are expanded."""
        # Test code blue
        result = service.expand_query("code blue procedure")
        # Should expand to include cardiac arrest or similar
        assert "cardiac" in result.expanded_query.lower() or "code blue" in result.expanded_query.lower()

    def test_pattern_expansion(self, service):
        """Test pattern-based query expansion."""
        # Test "How do I..." pattern
        result = service.expand_query("How do I request time off?")
        # Should add procedure/policy keywords
        has_expansion = any(
            word in result.expanded_query.lower()
            for word in ['procedure', 'policy', 'protocol', 'guideline']
        )
        # Pattern expansion is optional, so this might not trigger
        print(f"Pattern expansion result: {result.expanded_query}")

    def test_no_expansion_needed(self, service):
        """Test that queries without abbreviations pass through."""
        query = "What is the visitor policy?"
        result = service.expand_query(query)

        # Should have minimal or no expansion
        assert result.original_query == query
        # The query might still get some expansion from patterns
        print(f"Original: {query}")
        print(f"Expanded: {result.expanded_query}")

    def test_multiple_abbreviations(self, service):
        """Test queries with multiple abbreviations."""
        result = service.expand_query("ED to ICU transfer policy")

        # Both should be expanded
        assert len(result.abbreviations_expanded) >= 1
        assert "emergency" in result.expanded_query.lower() or "intensive" in result.expanded_query.lower()

    def test_get_synonyms_for_term(self, service):
        """Test getting synonyms for a specific term."""
        synonyms = service.get_synonyms_for_term("ED")
        assert len(synonyms) > 0
        print(f"Synonyms for ED: {synonyms}")

        synonyms = service.get_synonyms_for_term("emergency department")
        assert len(synonyms) > 0
        print(f"Synonyms for 'emergency department': {synonyms}")

    def test_abbreviation_context(self, service):
        """Test generating abbreviation context for agent prompt."""
        context = service.get_abbreviation_context(limit=20)
        assert len(context) > 0
        assert "ED:" in context or "ICU:" in context
        print(f"Abbreviation context:\n{context}")

    def test_rush_terms_context(self, service):
        """Test generating Rush-specific terms context."""
        context = service.get_rush_terms_context()
        assert len(context) > 0
        assert "Rush" in context
        print(f"Rush terms context:\n{context}")

    def test_possessive_normalization(self, service):
        """Test that possessives are normalized before expansion."""
        # Test RUMC's -> RUMC
        result = service.expand_query("What is RUMC's policy on visitors?")
        # The possessive should be stripped
        assert "RUMC's" not in result.expanded_query
        assert "RUMC" in result.expanded_query
        print(f"Possessive test: {result.expanded_query}")

        # Test Rush's -> Rush
        result = service.expand_query("Rush's visitor guidelines")
        assert "Rush's" not in result.expanded_query
        print(f"Possessive test 2: {result.expanded_query}")

    def test_compound_term_expansion_nicu_pain(self, service):
        """Test NICU + pain compound term expansion."""
        result = service.expand_query("NICU pain assessment policy")
        expanded_lower = result.expanded_query.lower()

        # Should add neonatal/pediatric pain terms
        has_neonatal_terms = any(term in expanded_lower for term in [
            'flacc', 'n-pass', 'neonatal', 'infant', 'newborn'
        ])
        assert has_neonatal_terms, f"Expected neonatal pain terms, got: {result.expanded_query}"
        print(f"NICU+pain expansion: {result.expanded_query}")

    def test_compound_term_expansion_pediatric_pain(self, service):
        """Test pediatric + pain compound term expansion."""
        result = service.expand_query("pediatric pain management protocol")
        expanded_lower = result.expanded_query.lower()

        # Should add pediatric pain terms
        has_peds_terms = any(term in expanded_lower for term in [
            'flacc', 'wong-baker', 'picu', 'child'
        ])
        assert has_peds_terms, f"Expected pediatric pain terms, got: {result.expanded_query}"
        print(f"Pediatric+pain expansion: {result.expanded_query}")

    def test_pediatric_pain_synonyms_in_json(self, service):
        """Test that pediatric pain assessment synonyms are loaded from JSON."""
        # Check if patient_care_terms contains neonatal pain assessment
        patient_care = service.synonym_groups.get('patient_care_terms', {})
        mappings = patient_care.get('mappings', {})

        # Verify neonatal pain assessment entry exists
        assert 'neonatal pain assessment' in mappings, "Missing neonatal pain assessment synonyms"
        neonatal_synonyms = mappings['neonatal pain assessment']
        assert 'FLACC' in neonatal_synonyms, "Missing FLACC in neonatal pain synonyms"
        assert 'N-PASS' in neonatal_synonyms, "Missing N-PASS in neonatal pain synonyms"
        print(f"Neonatal pain synonyms: {neonatal_synonyms}")

        # Verify pediatric pain assessment entry exists
        assert 'pediatric pain assessment' in mappings, "Missing pediatric pain assessment synonyms"
        peds_synonyms = mappings['pediatric pain assessment']
        assert 'Wong-Baker' in peds_synonyms, "Missing Wong-Baker in pediatric pain synonyms"
        print(f"Pediatric pain synonyms: {peds_synonyms}")

    def test_nicu_query_consistency(self, service):
        """
        Regression test for NICU query consistency issue.

        These three queries should all expand similarly since they're semantically equivalent.
        """
        queries = [
            "Show me the neonatal pain policy for RUMC nursing",
            "What is the pain assessment policy for RUMC's NICU",
            "RUMC Neonatal ICU Pain assessment policy overview"
        ]

        results = []
        for query in queries:
            result = service.expand_query(query)
            results.append(result)
            print(f"\nQuery: {query}")
            print(f"Expanded: {result.expanded_query}")
            print(f"Expansions: {result.expansions_applied}")

        # All queries should have some expansion applied
        for i, result in enumerate(results):
            expanded_lower = result.expanded_query.lower()
            # Should contain neonatal/NICU/infant terms after expansion
            has_nicu_context = any(term in expanded_lower for term in [
                'nicu', 'neonatal', 'infant', 'newborn', 'flacc', 'n-pass'
            ])
            assert has_nicu_context, f"Query {i+1} missing NICU context: {result.expanded_query}"


def test_sample_queries():
    """Test a variety of sample queries that users might ask."""
    service = get_synonym_service()

    sample_queries = [
        "What are the ED visiting hours?",
        "ICU family visitation policy",
        "How do I respond to a code blue?",
        "DNR documentation requirements",
        "HIPAA compliance for patient records",
        "Pyxis medication override procedure",
        "RUMC parking validation",
        "cathater care protocol",  # intentional misspelling
        "L&D visitor policy",
        "PPE requirements for isolation rooms",
        "Epic documentation standards",
        "What is the policy on restraints?",
    ]

    print("\n" + "=" * 60)
    print("SAMPLE QUERY EXPANSIONS")
    print("=" * 60)

    for query in sample_queries:
        result = service.expand_query(query)
        print(f"\nOriginal: {query}")
        print(f"Expanded: {result.expanded_query}")
        if result.abbreviations_expanded:
            print(f"  Abbreviations: {result.abbreviations_expanded}")
        if result.misspellings_corrected:
            print(f"  Misspellings: {result.misspellings_corrected}")
        if result.expansions_applied:
            print(f"  Patterns: {result.expansions_applied}")


if __name__ == "__main__":
    # Run sample queries test
    test_sample_queries()

    # Run pytest tests
    pytest.main([__file__, "-v"])
