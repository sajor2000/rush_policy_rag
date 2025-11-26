"""
Application configuration using Pydantic BaseSettings.

Features:
- Type validation and coercion at startup
- Environment variable loading with .env support
- Startup warnings for missing critical settings
- Backward-compatible property names
"""

import os
import logging
from pathlib import Path
from typing import Optional, List
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Load environment variables from .env file at project root
# Assuming this file is in apps/backend/app/core/
env_path = Path(__file__).resolve().parent.parent.parent.parent.parent / ".env"
load_dotenv(env_path, override=True)

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings with startup validation."""

    # Azure AI Search (required for core functionality)
    SEARCH_ENDPOINT: str = ""
    SEARCH_API_KEY: Optional[str] = None

    # Azure OpenAI (fallback when Foundry not configured)
    AOAI_ENDPOINT: Optional[str] = None
    AOAI_API_KEY: Optional[str] = None
    AOAI_CHAT_DEPLOYMENT: str = "gpt-4.1"

    # Admin
    ADMIN_API_KEY: Optional[str] = None

    # CORS - stored as comma-separated string, parsed to list via property
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5000,http://127.0.0.1:3000,http://127.0.0.1:5000"

    # Server
    BACKEND_PORT: int = 8000

    # Features
    USE_AGENTIC_RETRIEVAL: bool = False

    @property
    def ALLOWED_ORIGINS(self) -> List[str]:
        """Parse CORS_ORIGINS string into list (backward compatibility)."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @field_validator('USE_AGENTIC_RETRIEVAL', mode='before')
    @classmethod
    def parse_bool(cls, v):
        """Parse boolean from string (handles 'true', 'false', '1', '0')."""
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
        """Log warnings for missing critical configuration at startup."""
        warnings = []

        if not self.SEARCH_ENDPOINT:
            warnings.append("SEARCH_ENDPOINT not set - search functionality will fail")

        if not self.SEARCH_API_KEY:
            warnings.append("SEARCH_API_KEY not set - will attempt DefaultAzureCredential")

        if not self.AOAI_ENDPOINT:
            warnings.append("AOAI_ENDPOINT not set - Azure OpenAI fallback unavailable")

        for w in warnings:
            logger.warning(f"[CONFIG] {w}")

        return self

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # Ignore extra env vars


settings = Settings()
