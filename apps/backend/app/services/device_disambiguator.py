"""
Medical device disambiguation for the RUSH Policy RAG system.

This module handles ambiguous medical device terminology in user queries.
Clinical shorthand like "IV", "catheter", "line", or "port" can refer to
multiple different devices with distinct policies.

Key Features:
- Detects ambiguous device terms in queries
- Provides user-friendly clarification options
- Returns device-specific query expansions for better search

Extracted from chat_service.py as part of tech debt refactoring.
"""

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# Ambiguous Medical Device Configuration
# ============================================================================
# Maps ambiguous terms to device types and clarification options.
# Each option includes:
# - label: User-friendly description
# - expansion: Terms added to query for better search matching
# - type: Internal identifier for the device type

AMBIGUOUS_DEVICE_TERMS: Dict[str, Dict] = {
    'iv': {
        'device_types': ['peripheral_iv', 'picc', 'cvc', 'port'],
        'message': 'Your query mentions "IV" which could refer to different devices. Which type are you asking about?',
        'options': [
            {
                'label': 'Peripheral IV (short-term, 72-96 hours)',
                'expansion': 'peripheral intravenous PIV short-term',
                'type': 'peripheral_iv'
            },
            {
                'label': 'PICC line (long-term central line)',
                'expansion': 'PICC peripherally inserted central catheter long-term',
                'type': 'picc'
            },
            {
                'label': 'Central venous catheter (CVC, triple lumen)',
                'expansion': 'central venous catheter CVC TLC long-term',
                'type': 'cvc'
            },
            {
                'label': 'Any IV or catheter (show all results)',
                'expansion': 'intravenous vascular access',
                'type': 'all'
            }
        ]
    },
    'catheter': {
        'device_types': ['urinary', 'peripheral_iv', 'central_line', 'epidural'],
        'message': 'Your query mentions "catheter" which could refer to different types. Which are you asking about?',
        'options': [
            {'label': 'Urinary catheter (Foley)', 'expansion': 'urinary catheter Foley bladder', 'type': 'urinary'},
            {'label': 'IV catheter (peripheral or central)', 'expansion': 'intravenous catheter vascular', 'type': 'iv'},
            {'label': 'Epidural catheter', 'expansion': 'epidural catheter spinal', 'type': 'epidural'},
            {'label': 'Any catheter (show all results)', 'expansion': 'catheter tube', 'type': 'all'}
        ]
    },
    'line': {
        'device_types': ['peripheral_iv', 'central_line', 'arterial'],
        'message': 'Your query mentions "line" which could refer to different vascular access types. Which are you asking about?',
        'options': [
            {'label': 'Peripheral IV line', 'expansion': 'peripheral intravenous PIV', 'type': 'peripheral'},
            {'label': 'Central line (PICC, CVC)', 'expansion': 'central venous catheter PICC CVC', 'type': 'central'},
            {'label': 'Arterial line', 'expansion': 'arterial line A-line', 'type': 'arterial'},
            {'label': 'Any line (show all results)', 'expansion': 'vascular access line', 'type': 'all'}
        ]
    },
    'port': {
        'device_types': ['implanted_port', 'dialysis_port'],
        'message': 'Your query mentions "port" which could refer to different access devices. Which are you asking about?',
        'options': [
            {'label': 'Implanted port (chemotherapy port)', 'expansion': 'implanted port chemotherapy vascular access', 'type': 'implanted'},
            {'label': 'Dialysis port (apheresis catheter)', 'expansion': 'dialysis port apheresis catheter', 'type': 'dialysis'},
            {'label': 'Any port (show all results)', 'expansion': 'port vascular access', 'type': 'all'}
        ]
    }
}

# Keywords indicating the query is about device policies/procedures
DEVICE_CONTEXT_KEYWORDS = [
    'dwell', 'stay', 'place', 'long', 'care', 'change', 'remove',
    'insertion', 'maintain', 'flush', 'dressing', 'duration', 'access',
    'policy', 'guideline', 'protocol', 'procedure', 'rule'
]

# Terms that disambiguate device types (if present, no clarification needed)
DISAMBIGUATING_TERMS = [
    'peripheral', 'central', 'urinary', 'foley', 'epidural',
    'picc', 'cvc', 'tlc', 'arterial', 'implanted', 'dialysis',
    'apheresis', 'port-a-cath', 'chemo'
]


def detect_device_ambiguity(query: str) -> Optional[Dict]:
    """
    Detect if query contains ambiguous medical device shorthand without context.

    Returns clarification config if:
    1. Query contains ambiguous term (iv, catheter, line, port)
    2. Query lacks disambiguating modifiers (peripheral, central, urinary, etc.)
    3. Query is device-focused (contains: dwell, stay, place, care, remove, change)

    Args:
        query: User's query text

    Returns:
        Dict with 'message', 'options', 'ambiguous_term' if ambiguous
        None if query is clear enough

    Examples:
        >>> detect_device_ambiguity("iv dwell time policy")
        {'ambiguous_term': 'iv', 'message': '...', 'options': [...], 'requires_clarification': True}

        >>> detect_device_ambiguity("peripheral iv dwell time")
        None  # Has disambiguating term "peripheral"

        >>> detect_device_ambiguity("what is the weather")
        None  # Not a device-focused query
    """
    query_lower = query.lower()

    # Check for device-related context (dwell time, care, insertion, policy, etc.)
    has_device_context = any(kw in query_lower for kw in DEVICE_CONTEXT_KEYWORDS)

    if not has_device_context:
        return None  # Not a device-focused query

    # Check each ambiguous term
    for term, config in AMBIGUOUS_DEVICE_TERMS.items():
        if term in query_lower:
            # Check for disambiguating modifiers
            has_disambiguator = any(d in query_lower for d in DISAMBIGUATING_TERMS)

            if not has_disambiguator:
                # AMBIGUOUS - return clarification config
                logger.info(f"Ambiguous device term detected: '{term}' in query: {query[:50]}...")
                return {
                    'ambiguous_term': term,
                    'message': config['message'],
                    'options': config['options'],
                    'requires_clarification': True
                }

    return None  # Query is clear enough


def get_device_expansion(term: str, device_type: str) -> Optional[str]:
    """
    Get query expansion for a specific device type.

    Args:
        term: The ambiguous term (iv, catheter, line, port)
        device_type: The specific device type chosen by user

    Returns:
        Query expansion string or None if not found
    """
    if term not in AMBIGUOUS_DEVICE_TERMS:
        return None

    config = AMBIGUOUS_DEVICE_TERMS[term]
    for option in config['options']:
        if option['type'] == device_type:
            return option['expansion']

    return None
