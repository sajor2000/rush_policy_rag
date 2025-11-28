import logging
from typing import Optional, AsyncGenerator, Dict, Any
from contextlib import asynccontextmanager
from fastapi import FastAPI, Header, HTTPException, status

from azure_policy_index import PolicySearchIndex
from app.services.on_your_data_service import OnYourDataService
from app.core.config import settings
from app.core.auth import AzureADTokenValidator, TokenValidationError

logger = logging.getLogger(__name__)

# Global clients
_search_index: Optional[PolicySearchIndex] = None
_on_your_data_service: Optional[OnYourDataService] = None
_auth_validator: Optional[AzureADTokenValidator] = None


def get_search_index() -> PolicySearchIndex:
    global _search_index
    if _search_index is None:
        raise RuntimeError("Search index not initialized")
    return _search_index


def get_on_your_data_service_dep() -> Optional[OnYourDataService]:
    global _on_your_data_service
    return _on_your_data_service


def get_current_user_claims(authorization: Optional[str] = Header(default=None)) -> Optional[Dict[str, Any]]:
    """Validate Authorization header when Azure AD auth is required."""

    if not settings.REQUIRE_AAD_AUTH:
        return None

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.split(" ", 1)[1]

    validator = _get_auth_validator()
    if not validator:
        raise HTTPException(status_code=500, detail="Authentication not configured")

    try:
        return validator.validate(token)
    except TokenValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
        )


def _get_auth_validator() -> Optional[AzureADTokenValidator]:
    global _auth_validator
    return _auth_validator


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _search_index, _on_your_data_service, _auth_validator

    # Initialize to None
    _search_index = None
    _on_your_data_service = None
    _auth_validator = None

    # Initialize Search Index
    try:
        _search_index = PolicySearchIndex()
        logger.info(f"Search index client initialized: {_search_index.index_name}")
    except Exception as e:
        logger.error(f"Failed to initialize search index: {e}")
        raise

    # Initialize On Your Data service for vectorSemanticHybrid search
    try:
        if settings.USE_ON_YOUR_DATA:
            _on_your_data_service = OnYourDataService()
            if _on_your_data_service.is_configured:
                logger.info(
                    f"On Your Data service enabled - "
                    f"vectorSemanticHybrid with semantic config: {_on_your_data_service.semantic_config}"
                )
            else:
                logger.warning(
                    "USE_ON_YOUR_DATA enabled but service not configured - "
                    "check AOAI_ENDPOINT, AOAI_API_KEY, SEARCH_ENDPOINT, SEARCH_API_KEY"
                )
                _on_your_data_service = None
        else:
            logger.info("On Your Data (vectorSemanticHybrid) disabled")
    except Exception as e:
        logger.error(f"Failed to initialize On Your Data Service: {e}")
        _on_your_data_service = None

    # Initialize Azure AD validator if required
    try:
        if settings.REQUIRE_AAD_AUTH:
            audience = settings.AZURE_AD_TOKEN_AUDIENCE or settings.AZURE_AD_CLIENT_ID
            if not (settings.AZURE_AD_TENANT_ID and audience):
                raise RuntimeError("REQUIRE_AAD_AUTH enabled but Azure AD settings incomplete")

            _auth_validator = AzureADTokenValidator(
                tenant_id=settings.AZURE_AD_TENANT_ID,
                audience=audience,
                allowed_client_ids=settings.ALLOWED_AAD_CLIENT_IDS,
            )
            logger.info("Azure AD authentication enabled")
        else:
            _auth_validator = None
    except Exception as e:
        logger.error(f"Failed to initialize Azure AD auth validator: {e}")
        raise

    yield

    # Shutdown - clean up all service connections
    logger.info("Application shutting down - cleaning up resources")

    # Close On Your Data service
    if _on_your_data_service:
        try:
            _on_your_data_service.close()
            logger.info("On Your Data service closed")
        except Exception as e:
            logger.warning(f"Error closing On Your Data service: {e}")

    # Close search index
    if _search_index:
        try:
            _search_index.close()
            logger.info("Search index closed")
        except Exception as e:
            logger.warning(f"Error closing search index: {e}")

    logger.info("All resources cleaned up")
