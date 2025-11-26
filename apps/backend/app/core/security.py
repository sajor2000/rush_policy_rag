import re
from typing import Optional

def validate_query(query: str, max_length: int = 1000) -> str:
    """
    Validate and normalize a search/chat query.
    
    Args:
        query: The query string to validate
        max_length: Maximum allowed length (default: 1000)
        
    Returns:
        Normalized query string
        
    Raises:
        ValueError: If query is empty or too long
    """
    if not query or not query.strip():
        raise ValueError("Query cannot be empty")
    
    if len(query) > max_length:
        raise ValueError(f"Query exceeds maximum length of {max_length} characters")
        
    return query.strip()

def build_applies_to_filter(filter_value: Optional[str]) -> Optional[str]:
    """
    Build a safe OData filter expression for 'applies_to'.
    
    Prevents injection by validating against allowed pattern.
    
    Args:
        filter_value: The value to filter by (e.g., "RMG")
        
    Returns:
        OData filter string or None
        
    Raises:
        ValueError: If filter_value contains invalid characters
    """
    if not filter_value:
        return None
        
    # Allow only alphanumeric characters, spaces, and hyphens
    # This prevents OData injection attacks
    if not re.match(r'^[a-zA-Z0-9\s\-]+$', filter_value):
        raise ValueError("Invalid filter value: contains illegal characters")
        
    # Escape single quotes just in case (though regex above prevents them)
    safe_value = filter_value.replace("'", "''")
    return f"applies_to eq '{safe_value}'"
