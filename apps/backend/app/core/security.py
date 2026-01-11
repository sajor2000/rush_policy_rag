import re
from typing import Optional

def escape_odata_string(value: str) -> str:
    """
    Escape a string value for safe use in OData filter expressions.

    Prevents OData injection by escaping single quotes.

    Args:
        value: The string value to escape

    Returns:
        Escaped string safe for OData filter use

    Example:
        >>> escape_odata_string("file's name.pdf")
        "file''s name.pdf"
    """
    if not value:
        return value
    # OData escapes single quotes by doubling them
    return value.replace("'", "''")


def build_source_file_filter(source_file: str) -> str:
    """
    Build a safe OData filter expression for 'source_file'.

    Prevents injection by escaping quotes in the filename.

    Args:
        source_file: The source file name to filter by

    Returns:
        OData filter string

    Raises:
        ValueError: If source_file is empty
    """
    if not source_file or not source_file.strip():
        raise ValueError("source_file cannot be empty")

    safe_value = escape_odata_string(source_file.strip())
    return f"source_file eq '{safe_value}'"


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
