"""
PDF Service - Generate SAS URLs for secure PDF access from Azure Blob Storage.

This service creates time-limited, read-only URLs for PDF documents stored
in the policies-active container.
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from dotenv import load_dotenv

# Load environment variables
env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(env_path)

from azure.storage.blob import (
    BlobServiceClient,
    generate_blob_sas,
    BlobSasPermissions,
)

# Configuration
STORAGE_CONNECTION_STRING = os.environ.get("STORAGE_CONNECTION_STRING")
STORAGE_ACCOUNT_URL = os.environ.get("STORAGE_ACCOUNT_URL")
CONTAINER_NAME = os.environ.get("CONTAINER_NAME", "policies-active")


def get_blob_service_client() -> BlobServiceClient:
    """Get Azure Blob Service client."""
    if STORAGE_CONNECTION_STRING:
        return BlobServiceClient.from_connection_string(STORAGE_CONNECTION_STRING)
    elif STORAGE_ACCOUNT_URL:
        from azure.identity import DefaultAzureCredential
        return BlobServiceClient(STORAGE_ACCOUNT_URL, credential=DefaultAzureCredential())
    else:
        raise ValueError("STORAGE_CONNECTION_STRING or STORAGE_ACCOUNT_URL required")


def _parse_connection_string() -> tuple[str, str]:
    """
    Parse account name and key from connection string.
    
    Returns:
        Tuple of (account_name, account_key)
        
    Raises:
        ValueError: If connection string is missing or cannot be parsed
    """
    if not STORAGE_CONNECTION_STRING:
        raise ValueError(
            "STORAGE_CONNECTION_STRING is required for SAS URL generation. "
            "DefaultAzureCredential cannot be used for SAS tokens - an account key is needed."
        )
    
    try:
        parts = dict(
            item.split("=", 1) 
            for item in STORAGE_CONNECTION_STRING.split(";") 
            if "=" in item
        )
        account_name = parts.get("AccountName")
        account_key = parts.get("AccountKey")
        
        if not account_name or not account_key:
            raise ValueError(
                "Connection string must contain AccountName and AccountKey. "
                "Received keys: " + ", ".join(parts.keys())
            )
        
        return account_name, account_key
    except Exception as e:
        raise ValueError(f"Failed to parse STORAGE_CONNECTION_STRING: {e}")


def generate_pdf_sas_url(
    filename: str,
    expiry_hours: int = 1,
    container_name: str = CONTAINER_NAME
) -> dict:
    """
    Generate a time-limited SAS URL for PDF access.

    Args:
        filename: Name of the PDF file in blob storage
        expiry_hours: Hours until URL expires (default: 1)
        container_name: Blob container name

    Returns:
        dict with url, expires_at, and filename
        
    Raises:
        ValueError: If storage credentials are not configured or invalid
    """
    # Parse credentials (will raise clear error if not available)
    account_name, account_key = _parse_connection_string()
    
    # Calculate expiry
    expiry_time = datetime.now(timezone.utc) + timedelta(hours=expiry_hours)
    
    # Generate SAS token
    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=container_name,
        blob_name=filename,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=expiry_time,
    )
    
    # Build full URL - encode filename for URL safety (spaces, special chars)
    # Use quote with safe='' to encode all special characters except /
    encoded_filename = quote(filename, safe='')
    blob_url = f"https://{account_name}.blob.core.windows.net/{container_name}/{encoded_filename}?{sas_token}"
    
    return {
        "url": blob_url,
        "filename": filename,
        "expires_at": expiry_time.isoformat(),
        "expiry_hours": expiry_hours
    }


def check_pdf_exists(filename: str, container_name: str = CONTAINER_NAME) -> bool:
    """Check if a PDF exists in blob storage."""
    try:
        blob_service = get_blob_service_client()
        container_client = blob_service.get_container_client(container_name)
        blob_client = container_client.get_blob_client(filename)
        return blob_client.exists()
    except Exception:
        return False


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python pdf_service.py <filename>")
        sys.exit(1)
    
    filename = sys.argv[1]
    
    if not STORAGE_CONNECTION_STRING:
        print("ERROR: STORAGE_CONNECTION_STRING not set")
        sys.exit(1)
    
    if check_pdf_exists(filename):
        result = generate_pdf_sas_url(filename)
        print(f"URL: {result['url']}")
        print(f"Expires: {result['expires_at']}")
    else:
        print(f"PDF not found: {filename}")
