"""
SSL certificate fix for corporate proxy environments (e.g., Netskope).

Import this module FIRST in any standalone script that makes HTTPS calls:

    import ssl_fix  # Must be first import!
    
    # Then your other imports...
    from azure.storage.blob import BlobServiceClient
    import requests
"""

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass  # truststore not installed, SSL uses default cert handling
