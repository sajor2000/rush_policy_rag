"""Azure AD token validation utilities for FastAPI endpoints."""

from __future__ import annotations

import json
import logging
from threading import Lock
from typing import Any, Dict, List, Optional

import requests
import jwt
from cachetools import TTLCache


logger = logging.getLogger(__name__)


class TokenValidationError(Exception):
    """Raised when a bearer token cannot be validated."""


class AzureADTokenValidator:
    """Validate Azure AD bearer tokens using the tenant JWKS endpoint."""

    def __init__(
        self,
        tenant_id: str,
        audience: str,
        allowed_client_ids: Optional[List[str]] = None,
        cache_ttl: int = 3600,
    ) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if not audience:
            raise ValueError("audience/client_id is required")

        self.tenant_id = tenant_id
        self.audience = audience
        self.allowed_client_ids = set(cid for cid in (allowed_client_ids or []) if cid)
        self.issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self.jwks_uri = f"{self.issuer}/discovery/v2.0/keys"
        self._cache: TTLCache[str, Dict[str, List[dict]]] = TTLCache(maxsize=1, ttl=cache_ttl)
        self._lock = Lock()

    def validate(self, token: str) -> Dict[str, Any]:
        """Validate and decode a JWT, returning its claims."""

        if not token:
            raise TokenValidationError("Empty bearer token")

        try:
            header = jwt.get_unverified_header(token)
        except jwt.JWTError as exc:  # pragma: no cover - PyJWT specific
            raise TokenValidationError("Invalid token header") from exc

        kid = header.get("kid")
        if not kid:
            raise TokenValidationError("Token missing key identifier")

        public_key = self._get_public_key(kid)
        alg = header.get("alg", "RS256")

        try:
            claims = jwt.decode(
                token,
                public_key,
                algorithms=[alg],
                audience=self.audience,
                issuer=self.issuer,
            )
        except jwt.ExpiredSignatureError as exc:  # pragma: no cover - expiration specific
            raise TokenValidationError("Token has expired") from exc
        except jwt.InvalidTokenError as exc:  # pragma: no cover - generic JWT error
            raise TokenValidationError(str(exc)) from exc

        if self.allowed_client_ids:
            app_id = claims.get("azp") or claims.get("appid")
            if app_id not in self.allowed_client_ids:
                raise TokenValidationError("Client application not allowed")

        return claims

    def _get_public_key(self, kid: str):
        jwks = self._get_jwks()
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                return jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key))

        # Refresh keys once if the kid was not found (rotation scenario)
        logger.info("JWKS cache miss for kid=%s, refreshing", kid)
        self._cache.pop("jwks", None)
        jwks = self._get_jwks()
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                return jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key))

        raise TokenValidationError("Signing key not found for token")

    def _get_jwks(self) -> Dict[str, List[dict]]:
        with self._lock:
            cached = self._cache.get("jwks")
            if cached:
                return cached

            try:
                response = requests.get(self.jwks_uri, timeout=5)
                response.raise_for_status()
            except requests.RequestException as exc:  # pragma: no cover - network errors
                raise TokenValidationError("Failed to download JWKS") from exc

            payload = response.json()
            self._cache["jwks"] = payload
            return payload
