import logging
from typing import Optional, AsyncGenerator
from contextlib import asynccontextmanager
from fastapi import FastAPI

from azure_policy_index import PolicySearchIndex
from foundry_client import FoundryRAGClient, get_foundry_client
from app.services.foundry_agent import FoundryAgentService
from app.core.config import settings

logger = logging.getLogger(__name__)

# Global clients
_search_index: Optional[PolicySearchIndex] = None
_foundry_client: Optional[FoundryRAGClient] = None
_foundry_agent_service: Optional[FoundryAgentService] = None

def get_search_index() -> PolicySearchIndex:
    global _search_index
    if _search_index is None:
        raise RuntimeError("Search index not initialized")
    return _search_index

def get_foundry_client_dep() -> Optional[FoundryRAGClient]:
    global _foundry_client
    return _foundry_client

def get_foundry_agent_service_dep() -> Optional[FoundryAgentService]:
    global _foundry_agent_service
    return _foundry_agent_service

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _search_index, _foundry_client, _foundry_agent_service
    
    # Initialize to None
    _search_index = None
    _foundry_client = None
    _foundry_agent_service = None

    # Initialize Foundry Client first to get configuration
    try:
        _foundry_client = get_foundry_client()
        if _foundry_client.is_configured:
            logger.info(f"Foundry client initialized (mode: {'Foundry' if _foundry_client.use_foundry else 'Fallback'})")
        else:
            logger.warning("Neither Foundry nor Azure OpenAI configured")
    except Exception as e:
        logger.error(f"Failed to initialize Foundry client: {e}")
        _foundry_client = None

    # Initialize Search Index with Foundry client context
    try:
        _search_index = PolicySearchIndex(foundry_client=_foundry_client)
        logger.info(f"Search index client initialized: {_search_index.index_name}")
    except Exception as e:
        logger.error(f"Failed to initialize search index: {e}")
        raise

    try:
        if settings.USE_AGENTIC_RETRIEVAL:
            _foundry_agent_service = FoundryAgentService()
            if _foundry_agent_service.agent_id:
                logger.info(f"Agentic retrieval enabled - Agent: {_foundry_agent_service.agent_name} (ID: {_foundry_agent_service.agent_id})")
            else:
                logger.warning("Agentic retrieval enabled but no agent configured - run: python scripts/create_foundry_agent.py")
                _foundry_agent_service = None
        else:
            logger.info("Agentic retrieval disabled")
    except Exception as e:
        logger.error(f"Failed to initialize Foundry Agent Service: {e}")
        _foundry_agent_service = None

    yield

    # Shutdown
    logger.info("Application shutting down - cleaning up resources")
    try:
        # FoundryAgentService doesn't have a close method itself, but relies on project_client
        if _foundry_client:
            await _foundry_client.close()
    except Exception as e:
        logger.error(f"Error during shutdown cleanup: {e}")
