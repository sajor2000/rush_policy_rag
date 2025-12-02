"""
Application configuration using Pydantic BaseSettings.

Features:
- Type validation and coercion at startup
- Environment variable loading with .env support
- Startup warnings for missing critical settings
- Fail-fast mode for production (FAIL_ON_MISSING_CONFIG=true)
- Backward-compatible property names
"""

import os
import sys
import logging
from pathlib import Path
from typing import Optional, List
from pydantic import field_validator, model_validator, ConfigDict
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Load environment variables from .env file at project root
# Assuming this file is in apps/backend/app/core/
env_path = Path(__file__).resolve().parent.parent.parent.parent.parent / ".env"
load_dotenv(env_path, override=True)

logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """Raised when critical configuration is missing in production mode."""
    pass


class Settings(BaseSettings):
    """Application settings with startup validation."""

    # Azure AI Search (required for core functionality)
    SEARCH_ENDPOINT: str = ""
    SEARCH_API_KEY: Optional[str] = None

    # Azure OpenAI (for On Your Data vectorSemanticHybrid search)
    AOAI_ENDPOINT: Optional[str] = None
    AOAI_API_KEY: Optional[str] = None
    AOAI_CHAT_DEPLOYMENT: str = "gpt-4.1"

    # Admin
    ADMIN_API_KEY: Optional[str] = None

    # Azure AD / Authentication
    AZURE_AD_TENANT_ID: Optional[str] = None
    AZURE_AD_CLIENT_ID: Optional[str] = None
    AZURE_AD_TOKEN_AUDIENCE: Optional[str] = None
    AZURE_AD_ALLOWED_CLIENT_IDS: str = ""
    REQUIRE_AAD_AUTH: bool = False

    # CORS - stored as comma-separated string, parsed to list via property
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5000,http://127.0.0.1:3000,http://127.0.0.1:5000"

    # Server
    BACKEND_PORT: int = 8000

    # Production mode - fail fast on missing critical config
    # Set to true in production to catch misconfigurations at startup
    FAIL_ON_MISSING_CONFIG: bool = False

    # Features
    USE_ON_YOUR_DATA: bool = False  # Enable Azure OpenAI "On Your Data" for vectorSemanticHybrid

    # Cohere Rerank (cross-encoder for negation-aware search)
    # Deploy Cohere Rerank 3.5 on Azure AI Foundry as serverless API
    USE_COHERE_RERANK: bool = False  # Feature flag: use Cohere instead of On Your Data
    COHERE_RERANK_ENDPOINT: Optional[str] = None  # e.g., https://cohere-rerank-v3-5-xyz.eastus.models.ai.azure.com/
    COHERE_RERANK_API_KEY: Optional[str] = None
    # Per industry best practices: retrieve 100+ docs, rerank to top 5-10
    COHERE_RERANK_TOP_N: int = 10  # Increased for multi-policy queries (was 5)
    # Healthcare needs higher precision - calibrated for policy domain
    COHERE_RERANK_MIN_SCORE: float = 0.15  # Increased threshold (was 0.1)
    # Number of documents to retrieve before reranking (higher = better recall)
    COHERE_RETRIEVE_TOP_K: int = 100  # Industry standard: 100-150 candidates
    # Model name for Cohere rerank (configurable for version upgrades)
    COHERE_RERANK_MODEL: str = "cohere-rerank-v3-5"

    # PolicyTech URL - official RUSH policy administration portal
    POLICYTECH_URL: str = "https://rushumc.navexone.com/"

    @property
    def ALLOWED_ORIGINS(self) -> List[str]:
        """Parse CORS_ORIGINS string into list (backward compatibility)."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def ALLOWED_AAD_CLIENT_IDS(self) -> List[str]:
        """Parse AZURE_AD_ALLOWED_CLIENT_IDS into a list."""
        return [client.strip() for client in self.AZURE_AD_ALLOWED_CLIENT_IDS.split(",") if client.strip()]

    @field_validator('USE_ON_YOUR_DATA', 'USE_COHERE_RERANK', mode='before')
    @classmethod
    def parse_bool(cls, v):
        """Parse boolean from string (handles 'true', 'false', '1', '0')."""
        if isinstance(v, str):
            return v.lower() in ("true", "1", "yes")
        return bool(v)

    @field_validator('REQUIRE_AAD_AUTH', 'FAIL_ON_MISSING_CONFIG', mode='before')
    @classmethod
    def parse_require_auth(cls, v):
        if isinstance(v, str):
            return v.lower() in ("true", "1", "yes")
        return bool(v)

    @field_validator('AOAI_API_KEY', mode='before')
    @classmethod
    def map_aoai_api(cls, v):
        """Support both AOAI_API and AOAI_API_KEY env vars for backward compatibility."""
        if v:
            return v
        return os.environ.get("AOAI_API")

    @field_validator('BACKEND_PORT', mode='before')
    @classmethod
    def parse_port(cls, v):
        """Parse port from string with validation."""
        if isinstance(v, str):
            try:
                port = int(v)
                if not 1 <= port <= 65535:
                    raise ValueError(f"Port must be 1-65535, got {port}")
                return port
            except ValueError as e:
                raise ValueError(f"Invalid port value: {v}") from e
        return v

    @model_validator(mode='after')
    def validate_required_services(self):
        """
        Validate configuration at startup.

        Behavior depends on FAIL_ON_MISSING_CONFIG:
        - False (default): Log warnings for missing config
        - True: Raise ConfigurationError for missing critical config
        """
        critical_errors = []
        warnings = []

        # Critical: SEARCH_ENDPOINT is required for core functionality
        if not self.SEARCH_ENDPOINT:
            critical_errors.append("SEARCH_ENDPOINT not set - search functionality will fail")

        if not self.SEARCH_API_KEY:
            warnings.append("SEARCH_API_KEY not set - will attempt DefaultAzureCredential")

        if not self.AOAI_ENDPOINT:
            warnings.append("AOAI_ENDPOINT not set - Azure OpenAI fallback unavailable")

        # Critical: AOAI_API_KEY required when USE_ON_YOUR_DATA is enabled
        if self.USE_ON_YOUR_DATA and not self.AOAI_API_KEY:
            critical_errors.append(
                "USE_ON_YOUR_DATA=true but AOAI_API_KEY not set - vectorSemanticHybrid search will fail"
            )

        # Critical: Cohere config required when USE_COHERE_RERANK is enabled
        if self.USE_COHERE_RERANK:
            missing_cohere = []
            if not self.COHERE_RERANK_ENDPOINT:
                missing_cohere.append("COHERE_RERANK_ENDPOINT")
            if not self.COHERE_RERANK_API_KEY:
                missing_cohere.append("COHERE_RERANK_API_KEY")
            if not self.AOAI_ENDPOINT:
                missing_cohere.append("AOAI_ENDPOINT (needed for chat completions)")
            if not self.AOAI_API_KEY:
                missing_cohere.append("AOAI_API_KEY (needed for chat completions)")
            if missing_cohere:
                critical_errors.append(
                    "USE_COHERE_RERANK=true but missing: " + ", ".join(missing_cohere)
                )

        # Critical: AAD config required when auth is enabled
        if self.REQUIRE_AAD_AUTH:
            missing_auth = []
            if not self.AZURE_AD_TENANT_ID:
                missing_auth.append("AZURE_AD_TENANT_ID")
            if not self.AZURE_AD_CLIENT_ID:
                missing_auth.append("AZURE_AD_CLIENT_ID")
            if missing_auth:
                critical_errors.append(
                    "REQUIRE_AAD_AUTH enabled but missing: " + ", ".join(missing_auth)
                )

        # Critical: Admin API key should be set in production
        # Detect production environment (Azure Container Apps sets these)
        is_production = bool(
            os.environ.get("WEBSITE_SITE_NAME") or
            os.environ.get("CONTAINER_APP_NAME") or
            os.environ.get("AZURE_FUNCTIONS_ENVIRONMENT")
        )
        if (is_production or self.FAIL_ON_MISSING_CONFIG) and not self.ADMIN_API_KEY:
            critical_errors.append(
                "ADMIN_API_KEY not set - admin endpoints unprotected in production"
            )

        # Log all warnings
        for w in warnings:
            logger.warning(f"[CONFIG] {w}")

        # Handle critical errors based on mode
        if critical_errors:
            if self.FAIL_ON_MISSING_CONFIG:
                error_msg = "Critical configuration errors:\n  - " + "\n  - ".join(critical_errors)
                logger.error(f"[CONFIG] {error_msg}")
                raise ConfigurationError(error_msg)
            else:
                # Just warn in development mode
                for e in critical_errors:
                    logger.warning(f"[CONFIG] {e}")

        return self

    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # Ignore extra env vars
    )


settings = Settings()
