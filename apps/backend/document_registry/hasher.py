"""
SHA256 file hashing utilities.
"""

import hashlib
from pathlib import Path
from typing import Tuple


class DocumentHasher:
    """Compute SHA256 hashes for document files."""

    CHUNK_SIZE = 8192  # 8KB chunks for memory efficiency

    @staticmethod
    def compute_hash(file_path: Path) -> Tuple[str, int]:
        """
        Compute SHA256 hash and file size for a file.

        Args:
            file_path: Path to the file

        Returns:
            Tuple of (sha256_hex_digest, file_size_bytes)
        """
        sha256_hash = hashlib.sha256()
        file_size = 0

        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(DocumentHasher.CHUNK_SIZE), b""):
                sha256_hash.update(chunk)
                file_size += len(chunk)

        return sha256_hash.hexdigest(), file_size

    @staticmethod
    def compute_hash_from_bytes(data: bytes) -> str:
        """
        Compute SHA256 hash from bytes.

        Args:
            data: Bytes to hash

        Returns:
            SHA256 hex digest
        """
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def verify_hash(file_path: Path, expected_hash: str) -> bool:
        """
        Verify a file's hash matches the expected value.

        Args:
            file_path: Path to the file
            expected_hash: Expected SHA256 hex digest

        Returns:
            True if hash matches, False otherwise
        """
        actual_hash, _ = DocumentHasher.compute_hash(file_path)
        return actual_hash == expected_hash
