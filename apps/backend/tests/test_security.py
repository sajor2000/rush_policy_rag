"""
Security tests for input validation and injection prevention.
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi import HTTPException

from app.core.security import validate_query, build_applies_to_filter


class TestValidateQuery:
    """Tests for validate_query function."""

    def test_valid_query_returns_stripped(self):
        """Valid query should be stripped and returned."""
        result = validate_query("  hello world  ")
        assert result == "hello world"

    def test_empty_query_raises_error(self):
        """Empty query should raise ValueError."""
        with pytest.raises(ValueError, match="Query cannot be empty"):
            validate_query("")

    def test_whitespace_only_raises_error(self):
        """Whitespace-only query should raise ValueError."""
        with pytest.raises(ValueError, match="Query cannot be empty"):
            validate_query("   \t\n  ")

    def test_none_query_raises_error(self):
        """None query should raise ValueError."""
        with pytest.raises(ValueError, match="Query cannot be empty"):
            validate_query(None)

    def test_query_at_max_length_passes(self):
        """Query at exactly max length should pass."""
        query = "a" * 1000
        result = validate_query(query, max_length=1000)
        assert result == query

    def test_query_exceeds_max_length_raises_error(self):
        """Query exceeding max length should raise ValueError."""
        query = "a" * 1001
        with pytest.raises(ValueError, match="Query exceeds maximum length"):
            validate_query(query, max_length=1000)

    def test_custom_max_length(self):
        """Custom max_length should be respected."""
        query = "a" * 500
        result = validate_query(query, max_length=500)
        assert result == query

        with pytest.raises(ValueError):
            validate_query("a" * 501, max_length=500)

    def test_unicode_query_allowed(self):
        """Unicode characters should be allowed."""
        result = validate_query("查询 поиск البحث")
        assert "查询" in result


class TestBuildAppliesToFilter:
    """Tests for build_applies_to_filter function (OData injection prevention)."""

    def test_none_returns_none(self):
        """None filter value should return None."""
        assert build_applies_to_filter(None) is None

    def test_empty_string_returns_none(self):
        """Empty string should return None."""
        assert build_applies_to_filter("") is None

    def test_valid_filter_returns_odata(self):
        """Valid filter value should return OData expression."""
        result = build_applies_to_filter("RUMC")
        assert result == "applies_to eq 'RUMC'"

    def test_alphanumeric_allowed(self):
        """Alphanumeric characters should be allowed."""
        result = build_applies_to_filter("RUMC123")
        assert result == "applies_to eq 'RUMC123'"

    def test_spaces_allowed(self):
        """Spaces should be allowed."""
        result = build_applies_to_filter("Rush Medical")
        assert result == "applies_to eq 'Rush Medical'"

    def test_hyphens_allowed(self):
        """Hyphens should be allowed."""
        result = build_applies_to_filter("Rush-Medical")
        assert result == "applies_to eq 'Rush-Medical'"

    # OData Injection Prevention Tests
    def test_single_quote_injection_blocked(self):
        """Single quote injection should be blocked."""
        with pytest.raises(ValueError, match="Invalid filter value"):
            build_applies_to_filter("RUMC' or 1 eq 1 --")

    def test_odata_operator_injection_blocked(self):
        """OData operators should be blocked."""
        with pytest.raises(ValueError, match="Invalid filter value"):
            build_applies_to_filter("RUMC) or (1 eq 1")

    def test_parentheses_blocked(self):
        """Parentheses should be blocked."""
        with pytest.raises(ValueError, match="Invalid filter value"):
            build_applies_to_filter("(RUMC)")

    def test_semicolon_blocked(self):
        """Semicolons should be blocked."""
        with pytest.raises(ValueError, match="Invalid filter value"):
            build_applies_to_filter("RUMC; delete")

    def test_special_chars_blocked(self):
        """Special characters should be blocked."""
        special_chars = ["$", "&", "|", "!", "@", "#", "%", "^", "*", "=", "<", ">", "[", "]", "{", "}", "/", "\\"]
        for char in special_chars:
            with pytest.raises(ValueError, match="Invalid filter value"):
                build_applies_to_filter(f"RUMC{char}test")

    def test_sql_injection_patterns_blocked(self):
        """SQL injection patterns should be blocked."""
        patterns = [
            "'; DROP TABLE users; --",
            "RUMC' UNION SELECT * FROM users --",
            "1=1",
            "RUMC'; exec xp_cmdshell",
        ]
        for pattern in patterns:
            with pytest.raises(ValueError, match="Invalid filter value"):
                build_applies_to_filter(pattern)


class TestAdminKeyVerification:
    """Tests for admin API key verification."""

    @pytest.mark.asyncio
    async def test_missing_admin_key_config_returns_500(self):
        """Missing ADMIN_API_KEY config should return 500."""
        from app.api.routes.admin import verify_admin_key

        with patch("app.api.routes.admin.settings") as mock_settings:
            mock_settings.ADMIN_API_KEY = None

            with pytest.raises(HTTPException) as exc_info:
                await verify_admin_key("any-key")

            assert exc_info.value.status_code == 500
            assert "not configured" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_missing_api_key_returns_403(self):
        """Missing API key in request should return 403."""
        from app.api.routes.admin import verify_admin_key

        with patch("app.api.routes.admin.settings") as mock_settings:
            mock_settings.ADMIN_API_KEY = "valid-key"

            with pytest.raises(HTTPException) as exc_info:
                await verify_admin_key(None)

            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_invalid_api_key_returns_403(self):
        """Invalid API key should return 403."""
        from app.api.routes.admin import verify_admin_key

        with patch("app.api.routes.admin.settings") as mock_settings:
            mock_settings.ADMIN_API_KEY = "valid-key"

            with pytest.raises(HTTPException) as exc_info:
                await verify_admin_key("wrong-key")

            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_valid_api_key_returns_key(self):
        """Valid API key should return the key."""
        from app.api.routes.admin import verify_admin_key

        with patch("app.api.routes.admin.settings") as mock_settings:
            mock_settings.ADMIN_API_KEY = "valid-key"

            result = await verify_admin_key("valid-key")

            assert result == "valid-key"


class TestPathTraversalPrevention:
    """Tests for path traversal prevention in admin routes."""

    def test_path_outside_allowed_dirs_blocked(self):
        """Paths outside allowed directories should be blocked."""
        from app.api.routes.admin import validate_folder_path

        # These paths should all be blocked
        dangerous_paths = [
            "/etc/passwd",
            "../../../etc/shadow",
            "/root/.ssh/id_rsa",
        ]

        for path in dangerous_paths:
            with pytest.raises(HTTPException) as exc_info:
                validate_folder_path(path)
            assert exc_info.value.status_code == 400
            assert "outside allowed directories" in exc_info.value.detail

    def test_path_with_traversal_blocked(self):
        """Path traversal attempts should be blocked."""
        from app.api.routes.admin import validate_folder_path

        with pytest.raises(HTTPException):
            validate_folder_path("../../../../../../etc/passwd")
