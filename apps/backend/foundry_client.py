"""
Azure AI Foundry Client for RUSH Policy RAG Agent

Provides unified access to Azure AI services via the AIProjectClient:
- Chat completions via project.inference
- Telemetry/tracing via project.telemetry
- Knowledge Agent retrieval (when configured)

Falls back to direct AsyncAzureOpenAI if Foundry is not configured.
"""

import os
import logging
from typing import Optional, List, Dict, Any, Union
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class FoundryConfig:
    """Configuration for Azure AI Foundry connection."""
    project_endpoint: Optional[str] = None
    # Fallback to direct Azure OpenAI
    aoai_endpoint: Optional[str] = None
    aoai_api_key: Optional[str] = None
    aoai_chat_deployment: str = "gpt-4.1"
    aoai_api_version: str = "2024-10-21"


class FoundryRAGClient:
    """
    Unified client for Azure AI Foundry SDK.

    Supports two modes:
    1. Foundry Mode: Uses AIProjectClient for unified resource management
    2. Fallback Mode: Uses direct AsyncAzureOpenAI SDK
    """

    def __init__(self, config: Optional[FoundryConfig] = None):
        """
        Initialize the Foundry client.

        Args:
            config: Optional configuration. If None, reads from environment.
        """
        self.config = config or self._load_config_from_env()
        self.use_foundry = bool(self.config.project_endpoint)

        # Clients (lazy initialized)
        self._project_client = None
        self._chat_client = None
        self._fallback_client = None
        self._app_insights_connection_string = None

        logger.info(f"FoundryRAGClient initialized (Foundry mode: {self.use_foundry})")

    @staticmethod
    def _load_config_from_env() -> FoundryConfig:
        """Load configuration from environment variables."""
        return FoundryConfig(
            project_endpoint=os.environ.get("AZURE_AI_PROJECT_ENDPOINT"),
            aoai_endpoint=os.environ.get("AOAI_ENDPOINT"),
            aoai_api_key=os.environ.get("AOAI_API"),
            aoai_chat_deployment=os.environ.get("AOAI_CHAT_DEPLOYMENT", "gpt-4.1"),
            aoai_api_version=os.environ.get("AOAI_API_VERSION", "2024-10-21"),
        )

    @property
    def project_client(self) -> Optional[Any]:
        """Lazy-load the AIProjectClient."""
        if self._project_client is None and self.use_foundry:
            try:
                from azure.ai.projects import AIProjectClient
                from azure.identity import DefaultAzureCredential

                self._project_client = AIProjectClient(
                    endpoint=self.config.project_endpoint,
                    credential=DefaultAzureCredential()
                )
                logger.info("AIProjectClient initialized successfully")
            except ImportError:
                logger.error("azure-ai-projects not installed. Run: pip install azure-ai-projects")
                raise
            except Exception as e:
                logger.error(f"Failed to initialize AIProjectClient: {e}")
                raise
        return self._project_client

    @property
    def chat_client(self) -> Optional[Any]:
        """
        Get the chat completions client.

        In Foundry mode: Uses project.inference.get_chat_completions_client()
        In Fallback mode: Uses AsyncAzureOpenAI directly
        """
        if self._chat_client is None:
            if self.use_foundry and self.project_client:
                try:
                    self._chat_client = self.project_client.inference.get_chat_completions_client()
                    logger.info("Foundry chat client initialized")
                except Exception as e:
                    logger.warning(f"Failed to get Foundry chat client, falling back to direct SDK: {e}")
                    self._chat_client = self._get_fallback_client()
            else:
                self._chat_client = self._get_fallback_client()
        return self._chat_client

    def _get_fallback_client(self) -> Optional[Any]:
        """Get the fallback AsyncAzureOpenAI client."""
        if self._fallback_client is None:
            if not self.config.aoai_endpoint or not self.config.aoai_api_key:
                missing = []
                if not self.config.aoai_endpoint:
                    missing.append("AOAI_ENDPOINT")
                if not self.config.aoai_api_key:
                    missing.append("AOAI_API")
                logger.error(f"Azure OpenAI not configured. Missing: {', '.join(missing)}")
                return None

            from openai import AsyncAzureOpenAI

            self._fallback_client = AsyncAzureOpenAI(
                azure_endpoint=self.config.aoai_endpoint,
                api_key=self.config.aoai_api_key,
                api_version=self.config.aoai_api_version
            )
            logger.info("Fallback AsyncAzureOpenAI client initialized")
        return self._fallback_client

    def get_application_insights_connection_string(self) -> Optional[str]:
        """
        Get Application Insights connection string for tracing.

        In Foundry mode: Retrieves from project.telemetry
        In Fallback mode: Uses environment variable
        """
        if self._app_insights_connection_string:
            return self._app_insights_connection_string

        if self.use_foundry and self.project_client:
            try:
                self._app_insights_connection_string = (
                    self.project_client.telemetry.get_application_insights_connection_string()
                )
                logger.info("Retrieved App Insights connection string from Foundry")
                return self._app_insights_connection_string
            except Exception as e:
                logger.warning(f"Failed to get App Insights from Foundry: {e}")

        # Fallback to environment variable
        self._app_insights_connection_string = os.environ.get(
            "APPLICATIONINSIGHTS_CONNECTION_STRING"
        )
        return self._app_insights_connection_string

    def get_search_connection(self) -> Optional[Any]:
        """
        Retrieve Azure AI Search connection details from Foundry Project.
        
        Returns:
            Connection object or None if not found/configured
        """
        if self.use_foundry and self.project_client:
            try:
                # List connections and find the one typed 'Azure AI Search'
                # Note: The exact type string might vary, usually 'Azure.Search' or similar
                connections = self.project_client.connections.list()
                for conn in connections:
                    # Check for search connection type
                    # Common types: Azure.Search, CognitiveSearch
                    conn_type = getattr(conn, 'type', '').lower()
                    if 'search' in conn_type:
                        return conn
            except Exception as e:
                logger.warning(f"Failed to list connections from Foundry: {e}")
        return None

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 800,
    ) -> Dict[str, Any]:
        """
        Generate a chat completion.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model/deployment name (defaults to config)
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens in response

        Returns:
            Dict with 'content', 'model', 'usage' keys

        Raises:
            ValueError: If inputs are invalid
            RuntimeError: If no chat client is available or no choices returned
        """
        # Input validation
        if not messages:
            raise ValueError("messages cannot be empty")
        for msg in messages:
            if "role" not in msg or "content" not in msg:
                raise ValueError("Each message must have 'role' and 'content' keys")
            if msg["role"] not in ("system", "user", "assistant"):
                raise ValueError(f"Invalid role: {msg['role']}")
        if not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")

        deployment = model or self.config.aoai_chat_deployment

        if not self.chat_client:
            raise RuntimeError("No chat client available")

        try:
            if self.use_foundry and hasattr(self.chat_client, 'complete'):
                # Foundry SDK uses .complete()
                response = await self.chat_client.complete(
                    model=deployment,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                if not response.choices:
                    raise RuntimeError("No choices returned from chat completion")
                return {
                    "content": response.choices[0].message.content,
                    "model": response.model,
                    "usage": {
                        "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                        "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                    }
                }
            else:
                # Fallback SDK uses .chat.completions.create()
                response = await self.chat_client.chat.completions.create(
                    model=deployment,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                if not response.choices:
                    raise RuntimeError("No choices returned from chat completion")
                return {
                    "content": response.choices[0].message.content,
                    "model": response.model,
                    "usage": {
                        "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                        "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                    }
                }
        except Exception as e:
            logger.error(f"Chat completion failed: {e}")
            raise

    async def close(self) -> None:
        """
        Close all client connections to prevent resource leaks.

        Should be called during application shutdown.
        """
        if self._fallback_client:
            try:
                await self._fallback_client.close()
                logger.debug("Fallback AsyncAzureOpenAI client closed")
            except Exception as e:
                logger.warning(f"Error closing fallback client: {e}")
            self._fallback_client = None

        if self._project_client and hasattr(self._project_client, 'close'):
            try:
                self._project_client.close()
                logger.debug("AIProjectClient closed")
            except Exception as e:
                logger.warning(f"Error closing project client: {e}")
            self._project_client = None

        self._chat_client = None

    @property
    def is_configured(self) -> bool:
        """Check if any AI service is configured."""
        return self.use_foundry or (
            bool(self.config.aoai_endpoint) and bool(self.config.aoai_api_key)
        )

    def get_status(self) -> Dict[str, Any]:
        """Get client status for health checks."""
        return {
            "foundry_mode": self.use_foundry,
            "project_endpoint": bool(self.config.project_endpoint),
            "aoai_configured": bool(self.config.aoai_endpoint and self.config.aoai_api_key),
            "chat_deployment": self.config.aoai_chat_deployment,
            "is_configured": self.is_configured,
        }


# Singleton instance (initialized on import)
_foundry_client: Optional[FoundryRAGClient] = None


def get_foundry_client() -> FoundryRAGClient:
    """Get or create the singleton FoundryRAGClient instance."""
    global _foundry_client
    if _foundry_client is None:
        _foundry_client = FoundryRAGClient()
    return _foundry_client
