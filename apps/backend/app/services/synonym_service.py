"""
Synonym Expansion Service for RUSH Policy RAG

Enhances search accuracy by expanding user queries with synonyms,
handling medical abbreviations, misspellings, and Rush-specific terms.

Uses semantic-search-synonyms.json which contains:
- 1,860 policy documents analyzed
- 20+ synonym categories (medical abbreviations, hospital codes, etc.)
- Rush-specific institutional terms (RUMC, RUMG, ROPH, etc.)
- Common misspellings

Integration points:
1. Query preprocessing before search
2. Agent prompt context
3. Misspelling correction
"""

import json
import re
import logging
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Path to synonym configuration
SYNONYMS_PATH = Path(__file__).resolve().parent.parent.parent.parent.parent / "semantic-search-synonyms.json"


@dataclass
class QueryExpansion:
    """Result of query expansion."""
    original_query: str
    expanded_query: str
    expansions_applied: List[Dict[str, str]] = field(default_factory=list)
    misspellings_corrected: List[Dict[str, str]] = field(default_factory=list)
    abbreviations_expanded: List[Dict[str, str]] = field(default_factory=list)


class SynonymService:
    """
    Service for expanding queries with synonyms to improve RAG accuracy.

    Expansion strategy:
    1. Correct common misspellings
    2. Expand medical abbreviations (e.g., "ED" → "emergency department")
    3. Add Rush-specific term alternatives
    4. Apply query expansion rules based on patterns

    The expanded query helps Azure AI Search find more relevant results
    even when users use different terminology than the indexed documents.
    """

    def __init__(self, synonyms_path: Optional[Path] = None):
        self.synonyms_path = synonyms_path or SYNONYMS_PATH
        self.synonym_groups: Dict[str, Dict] = {}
        self.category_keywords: Dict[str, List[str]] = {}
        self.query_expansion_rules: List[Dict] = []
        self.metadata: Dict = {}

        # Reverse lookup: term → canonical form
        self._term_to_canonical: Dict[str, str] = {}
        # Abbreviation lookup: abbrev → full form
        self._abbreviations: Dict[str, str] = {}
        # Misspelling lookup: misspelled → correct
        self._misspellings: Dict[str, str] = {}
        # Rush-specific terms
        self._rush_terms: Dict[str, List[str]] = {}

        self._load_synonyms()

    def _load_synonyms(self):
        """Load and index synonyms from JSON file."""
        if not self.synonyms_path.exists():
            logger.warning(f"Synonyms file not found: {self.synonyms_path}")
            return

        try:
            with open(self.synonyms_path, 'r') as f:
                data = json.load(f)

            self.metadata = data.get('metadata', {})
            self.synonym_groups = data.get('synonym_groups', {})
            self.category_keywords = data.get('category_keywords', {})
            self.query_expansion_rules = data.get('query_expansion_rules', {}).get('rules', [])

            # Build indexes
            self._build_indexes()

            logger.info(
                f"Loaded synonyms: {self.metadata.get('total_documents_analyzed', 0)} docs, "
                f"{len(self.synonym_groups)} groups, "
                f"{len(self._abbreviations)} abbreviations"
            )
        except Exception as e:
            logger.error(f"Failed to load synonyms: {e}")

    def _build_indexes(self):
        """Build reverse lookup indexes for fast query expansion."""
        # Medical abbreviations
        if 'medical_abbreviations' in self.synonym_groups:
            mappings = self.synonym_groups['medical_abbreviations'].get('mappings', {})
            for abbrev, synonyms in mappings.items():
                # Store abbreviation → first (primary) expansion
                self._abbreviations[abbrev.lower()] = synonyms[0] if synonyms else abbrev
                # Also map all synonyms back to the abbreviation
                for syn in synonyms:
                    self._term_to_canonical[syn.lower()] = abbrev

        # Common misspellings
        if 'common_misspellings' in self.synonym_groups:
            mappings = self.synonym_groups['common_misspellings'].get('mappings', {})
            for correct, misspellings in mappings.items():
                for misspelled in misspellings:
                    self._misspellings[misspelled.lower()] = correct

        # Rush-specific institutional terms
        if 'rush_institution_terms' in self.synonym_groups:
            self._rush_terms = self.synonym_groups['rush_institution_terms'].get('mappings', {})

        # Hospital codes (important for emergency-related queries)
        if 'hospital_codes' in self.synonym_groups:
            mappings = self.synonym_groups['hospital_codes'].get('mappings', {})
            for code, synonyms in mappings.items():
                self._abbreviations[code.lower()] = synonyms[0] if synonyms else code

        # Software systems (Epic, Pyxis, etc.)
        if 'software_systems' in self.synonym_groups:
            mappings = self.synonym_groups['software_systems'].get('mappings', {})
            for system, synonyms in mappings.items():
                self._abbreviations[system.lower()] = synonyms[0] if synonyms else system


    def expand_query(
        self,
        query: str,
        max_expansions: int = 3,
        max_expansion_ratio: float = 2.0
    ) -> QueryExpansion:
        """
        Expand a user query with synonyms and corrections.

        Strategy:
        1. Correct misspellings first
        2. Expand medical abbreviations
        3. Add Rush-specific alternatives
        4. Apply pattern-based expansion rules
        5. Add domain context for short acronym-only queries
        6. NEW: Apply 2x expansion limit to prevent embedding dilution

        Args:
            query: Original user query
            max_expansions: Maximum synonym expansions per term
            max_expansion_ratio: Maximum ratio of expanded to original word count (default 2.0)

        Returns:
            QueryExpansion with original and expanded query
        """
        result = QueryExpansion(
            original_query=query,
            expanded_query=query
        )

        # Calculate max words allowed (minimum 6 to handle short queries)
        original_word_count = len(query.split())
        max_words = max(6, int(original_word_count * max_expansion_ratio))

        words = query.split()
        expanded_words = []

        for word in words:
            word_lower = word.lower().strip('.,?!')
            expanded = word

            # 1. Correct misspellings
            if word_lower in self._misspellings:
                corrected = self._misspellings[word_lower]
                result.misspellings_corrected.append({
                    'original': word,
                    'corrected': corrected
                })
                expanded = corrected
                word_lower = corrected.lower()

            # 2. Expand abbreviations (keep original + add expansion)
            # Skip common English words that happen to match abbreviations
            # e.g., "it" should NOT become "information technology"
            ABBREVIATION_STOPWORDS = {
                'it', 'is', 'in', 'at', 'as', 'or', 'an', 'am', 'be', 'do', 'go',
                'he', 'me', 'my', 'no', 'of', 'on', 'so', 'to', 'up', 'us', 'we',
                'by', 'if', 'ms', 'mr', 'vs', 'pm', 'am'
            }
            if word_lower in self._abbreviations and word_lower not in ABBREVIATION_STOPWORDS:
                expansion = self._abbreviations[word_lower]
                result.abbreviations_expanded.append({
                    'abbreviation': word,
                    'expansion': expansion
                })
                # Keep both terms space-separated for better semantic/vector matching
                # (parentheses can confuse embeddings)
                expanded = f"{word} {expansion}"

            expanded_words.append(expanded)

        # Reconstruct query
        expanded_query = ' '.join(expanded_words)

        # 3. Apply pattern-based expansion rules
        expanded_query = self._apply_expansion_rules(query, expanded_query, result)

        # 4. Handle multi-word Rush terms (e.g., "code blue", "labor and delivery")
        expanded_query = self._expand_multiword_terms(query, expanded_query, result)

        # 5. NEW: Add domain context for short acronym-only queries
        # This helps queries like "SBAR" find the same results as "SBAR communication framework"
        expanded_query = self._add_context_for_short_queries(query, expanded_query, result)

        # 6. NEW: Truncate if over limit to prevent embedding dilution
        # Research shows over-expansion causes semantic drift in embeddings
        expanded_words_final = expanded_query.split()
        if len(expanded_words_final) > max_words:
            expanded_query = ' '.join(expanded_words_final[:max_words])
            logger.info(f"Truncated expansion: {len(expanded_words_final)} -> {max_words} words")

        result.expanded_query = expanded_query

        if result.expansions_applied or result.misspellings_corrected or result.abbreviations_expanded:
            logger.info(f"Query expansion: '{query}' → '{expanded_query}'")

        return result

    def _add_context_for_short_queries(
        self,
        original: str,
        current: str,
        result: QueryExpansion
    ) -> str:
        """
        Add domain context for short acronym-only queries.
        
        Short queries like "SBAR" or "RRT" often miss relevant documents because
        they lack context. This adds policy-related terms to improve retrieval.
        
        Examples:
        - "SBAR" -> "SBAR situation background assessment recommendation communication hand-off"
        - "RRT" -> "RRT rapid response team emergency"
        """
        words = original.split()
        
        # Only apply to very short queries (1-2 words)
        if len(words) > 2:
            return current
        
        # Check if query is primarily acronyms/uppercase terms
        acronym_words = [w for w in words if w.isupper() and len(w) >= 2]
        if not acronym_words:
            return current
        
        # Domain-specific context additions for common healthcare acronyms
        # CONSERVATIVE: Max 5 terms per entry to prevent embedding dilution
        # Research shows over-expansion causes semantic drift in embeddings
        context_map = {
            # Communication (fix gen-004, gen-006) - SBAR = Situation Background Assessment Recommendation
            'sbar': 'Situation Background Assessment Recommendation handoff',
            'shift': 'shift change handoff report',
            'handoff': 'hand-off communication report',
            'hand-off': 'handoff communication report',
            'report': 'shift handoff SBAR communication',

            # Rapid Response (fix multi-001, adv-003)
            'rrt': 'rapid response team family',
            'rapid': 'rapid response RRT',

            # Verbal Orders (fix edge-001)
            'verbal': 'verbal telephone orders',
            'orders': 'verbal telephone orders',

            # Latex/Safety (fix edge-008, multi-002)
            'latex': 'latex allergy product precautions',
            'product': 'product latex identification labeling',
            'allergy': 'allergy latex precautions',
            'patient': 'patient identification safety',
            'identification': 'identification patient safety',
            'safety': 'safety patient precautions',

            # Epic/Documentation (fix multi-003)
            'epic': 'epic EHR documentation charting',
            'documentation': 'documentation Epic charting',

            # Standard acronyms (conservative - 3-4 terms)
            'rn': 'registered nurse nursing',
            'icu': 'intensive care critical',
            'ed': 'emergency department ER',
            'cpr': 'resuscitation cardiac arrest',
            'dnr': 'do not resuscitate',
            'hipaa': 'privacy patient information',
            'pca': 'patient controlled analgesia',
            'picc': 'central catheter line',
            'npo': 'nothing by mouth fasting',
            'prn': 'as needed medication',
            'stat': 'immediately urgent',
            'vte': 'blood clot prevention',
            'fall': 'fall prevention risk',
        }
        
        expanded = current
        for word in words:
            word_lower = word.lower()
            if word_lower in context_map:
                context_terms = context_map[word_lower]
                expanded = f"{expanded} {context_terms}"
                result.expansions_applied.append({
                    'term': word,
                    'context_added': context_terms
                })
                logger.debug(f"Added context for '{word}': {context_terms}")
        
        return expanded

    def _apply_expansion_rules(
        self,
        original: str,
        current: str,
        result: QueryExpansion
    ) -> str:
        """Apply pattern-based query expansion rules."""
        expanded = current

        for rule in self.query_expansion_rules:
            pattern = rule.get('pattern', '')
            expand_with = rule.get('expand_with', [])

            if not pattern or not expand_with:
                continue

            try:
                if re.match(pattern, original, re.IGNORECASE):
                    # Add expansion keywords to query
                    additions = ' '.join(expand_with[:2])  # Limit to top 2
                    expanded = f"{expanded} {additions}"
                    result.expansions_applied.append({
                        'pattern': pattern,
                        'additions': expand_with[:2]
                    })
                    break  # Apply only first matching rule
            except re.error:
                continue

        return expanded

    def _expand_multiword_terms(
        self,
        original: str,
        current: str,
        result: QueryExpansion
    ) -> str:
        """Expand multi-word terms like 'code blue', 'labor and delivery'."""
        expanded = current
        original_lower = original.lower()

        # Hospital codes (multi-word)
        if 'hospital_codes' in self.synonym_groups:
            for code, synonyms in self.synonym_groups['hospital_codes'].get('mappings', {}).items():
                if code.lower() in original_lower:
                    # Add primary synonym
                    if synonyms:
                        expanded = f"{expanded} {synonyms[0]}"
                        result.expansions_applied.append({
                            'term': code,
                            'expansion': synonyms[0]
                        })
                    break

        # Department/unit names
        if 'departments_units' in self.synonym_groups:
            for dept, synonyms in self.synonym_groups['departments_units'].get('mappings', {}).items():
                if dept.lower() in original_lower:
                    # Add abbreviation if available
                    abbrev = next((s for s in synonyms if s.isupper() and len(s) <= 4), None)
                    if abbrev:
                        expanded = f"{expanded} {abbrev}"
                        result.expansions_applied.append({
                            'term': dept,
                            'expansion': abbrev
                        })
                    break

        # Communication terminology (fix gen-006: "shift report" → "hand-off")
        # The policy uses "Hand Off Communication" but users search for "shift report"
        communication_terms = {
            'shift report': 'hand-off handoff communication nursing',
            'change of shift': 'hand-off handoff patient status',
            'shift change': 'hand-off handoff communication',
            'bedside report': 'hand-off handoff nursing communication',
            'nursing report': 'hand-off handoff shift communication',
        }
        for term, expansion in communication_terms.items():
            if term in original_lower:
                expanded = f"{expanded} {expansion}"
                result.expansions_applied.append({
                    'term': term,
                    'expansion': expansion
                })
                logger.debug(f"Multi-word expansion: '{term}' → '{expansion}'")
                break  # Only apply first match

        return expanded

    def get_abbreviation_context(self, limit: int = 50) -> str:
        """
        Generate a context string of key abbreviations for the agent prompt.

        This helps the agent understand common abbreviations users might use.
        """
        if not self._abbreviations:
            return ""

        # Prioritize most common/important abbreviations
        priority_abbrevs = [
            'ED', 'ICU', 'NICU', 'PICU', 'OR', 'PACU', 'L&D', 'LD',
            'DNR', 'HIPAA', 'EMTALA', 'PPE', 'NPO', 'PRN', 'STAT',
            'IV', 'CVC', 'PICC', 'CPR', 'BLS', 'AED',
            'RUMC', 'RUMG', 'ROPH', 'RUSH', 'RCMC',
            'EPIC', 'PYXIS', 'WORKDAY'
        ]

        context_lines = ["Key abbreviations:"]
        added = 0

        # Add priority abbreviations first
        for abbrev in priority_abbrevs:
            if abbrev.lower() in self._abbreviations and added < limit:
                expansion = self._abbreviations[abbrev.lower()]
                context_lines.append(f"- {abbrev}: {expansion}")
                added += 1

        return '\n'.join(context_lines)

    def get_rush_terms_context(self) -> str:
        """Generate context about Rush-specific institutional terms."""
        if not self._rush_terms:
            return ""

        lines = ["Rush University System for Health locations and terms:"]
        for term, synonyms in list(self._rush_terms.items())[:10]:
            lines.append(f"- {term}: {', '.join(synonyms[:3])}")

        return '\n'.join(lines)

    def correct_misspelling(self, word: str) -> Optional[str]:
        """Check and correct a single word's spelling."""
        return self._misspellings.get(word.lower())

    def expand_abbreviation(self, abbrev: str) -> Optional[str]:
        """Get the expansion of an abbreviation."""
        return self._abbreviations.get(abbrev.lower())

    def get_synonyms_for_term(self, term: str, category: Optional[str] = None) -> List[str]:
        """Get all synonyms for a term, optionally filtered by category."""
        term_lower = term.lower()
        synonyms = []

        groups_to_search = [category] if category else self.synonym_groups.keys()

        for group_name in groups_to_search:
            if group_name not in self.synonym_groups:
                continue

            mappings = self.synonym_groups[group_name].get('mappings', {})

            # Check if term is a key
            if term_lower in {k.lower() for k in mappings.keys()}:
                for key, syns in mappings.items():
                    if key.lower() == term_lower:
                        synonyms.extend(syns)
                        break

            # Check if term is in any synonym list
            for key, syns in mappings.items():
                if term_lower in [s.lower() for s in syns]:
                    synonyms.append(key)
                    synonyms.extend(s for s in syns if s.lower() != term_lower)
                    break

        return list(set(synonyms))  # Deduplicate


# Global singleton instance
_synonym_service: Optional[SynonymService] = None


def get_synonym_service() -> SynonymService:
    """Get or create the global synonym service instance."""
    global _synonym_service
    if _synonym_service is None:
        _synonym_service = SynonymService()
    return _synonym_service
