import logging
import asyncio
import os
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

from azure.ai.agents import AgentsClient
from azure.ai.agents.models import (
    ListSortOrder,
    MessageRole,
    AgentThreadCreationOptions,
    ThreadMessageOptions,
)
from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)

# Path to agent config file (created by scripts/create_foundry_agent.py)
AGENT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "agent_config.json"


def load_agent_config() -> dict:
    """Load persistent agent configuration from file."""
    if AGENT_CONFIG_PATH.exists():
        try:
            with open(AGENT_CONFIG_PATH) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load agent config: {e}")
    return {}

@dataclass
class AgentActivity:
    """Activity log from the knowledge agent."""
    query_decomposition: List[str] = field(default_factory=list)
    subqueries_executed: int = 0
    sources_retrieved: int = 0
    reranking_applied: bool = False

@dataclass
class AgentReference:
    """A reference from the agentic retrieval."""
    content: str
    citation: str
    title: str
    reference_number: str = ""
    section: str = ""
    applies_to: str = ""
    source_file: str = ""
    score: float = 0.0
    reranker_score: Optional[float] = None

@dataclass
class AgentRetrievalResult:
    """Result from agentic retrieval."""
    synthesized_answer: str
    references: List[AgentReference]
    activity: AgentActivity
    raw_response: Optional[str] = None

class FoundryAgentService:
    """
    Service for agentic retrieval using Azure AI Foundry Agents API.

    Uses a persistent agent created by scripts/create_foundry_agent.py.
    The agent_id is loaded from environment variable or agent_config.json.

    Uses AgentsClient from azure-ai-agents for thread/run operations.
    Pattern:
    - create_thread_and_process_run() for combined thread+message+run
    - messages.get_last_message_text_by_role() for response retrieval
    """

    def __init__(self):
        # Load persistent agent configuration
        config = load_agent_config()

        # Agent ID: prefer env var, fallback to config file
        self.agent_id = os.environ.get("FOUNDRY_AGENT_ID") or config.get("agent_id")
        self.agent_name = config.get("agent_name", "rush-policy-agent")
        self.index_name = config.get("index_name", "rush-policies")
        self.agent_model = config.get("model", "gpt-4.1")

        # Initialize AgentsClient for thread/run operations
        endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT")
        if endpoint:
            self.agents_client = AgentsClient(
                endpoint=endpoint,
                credential=DefaultAzureCredential()
            )
            logger.info(f"Initialized AgentsClient for endpoint: {endpoint}")
        else:
            self.agents_client = None
            logger.warning("AZURE_AI_PROJECT_ENDPOINT not set")

        if self.agent_id:
            logger.info(f"Using persistent agent: {self.agent_name} (ID: {self.agent_id})")
        else:
            logger.warning(
                "No persistent agent configured. Run: python scripts/create_foundry_agent.py"
            )

    async def retrieve(
        self,
        query: str,
        max_results: int = 5
    ) -> AgentRetrievalResult:
        """
        Perform agentic retrieval using a persistent Foundry Agent.

        The agent is created once via scripts/create_foundry_agent.py and reused
        for all requests. Only threads are created per conversation.

        Steps:
        1. Verify persistent agent is configured
        2. Create thread with user message and run agent in one call
        3. Get assistant response from completed thread
        4. Extract citations and return structured result

        Uses AgentsClient SDK pattern:
        - create_thread_and_process_run() for combined thread+message+run
        - messages.get_last_message_text_by_role() for response retrieval
        - messages.list() for full message history with annotations
        """
        if not self.agents_client:
            raise RuntimeError("AgentsClient not initialized - check AZURE_AI_PROJECT_ENDPOINT")

        if not self.agent_id:
            raise RuntimeError(
                "No persistent agent configured. "
                "Run: python scripts/create_foundry_agent.py"
            )

        try:
            # Create thread with message and run agent in one call
            # This is the most efficient pattern for the azure-ai-agents SDK
            thread_options = AgentThreadCreationOptions(
                messages=[
                    ThreadMessageOptions(
                        role=MessageRole.USER,
                        content=query
                    )
                ]
            )

            run = self.agents_client.create_thread_and_process_run(
                agent_id=self.agent_id,
                thread=thread_options
            )
            logger.info(f"Run finished with status: {run.status}, thread: {run.thread_id}")

            if run.status == "failed":
                error_msg = getattr(run, 'last_error', str(run))
                logger.error(f"Agent run failed: {error_msg}")
                raise RuntimeError(f"Agent run failed: {error_msg}")

            # Get full messages to extract response text and annotations
            messages = list(self.agents_client.messages.list(
                thread_id=run.thread_id,
                order=ListSortOrder.DESCENDING
            ))

            # Find the last agent message using SDK helper properties
            # Per SDK docs: msg.text_messages provides quick access to text content
            last_agent_msg = None
            response_text = ""
            for msg in messages:
                # Check role (SDK uses 'agent' role for assistant responses)
                msg_role = str(msg.role).lower() if hasattr(msg.role, '__str__') else str(msg.role)
                if 'agent' in msg_role or 'assistant' in msg_role:
                    last_agent_msg = msg
                    # Use SDK's text_messages helper property (recommended per docs)
                    if hasattr(msg, 'text_messages') and msg.text_messages:
                        last_text = msg.text_messages[-1]
                        if hasattr(last_text, 'text') and hasattr(last_text.text, 'value'):
                            response_text = last_text.text.value
                    # Fallback to manual content extraction if helper not available
                    elif hasattr(msg, 'content') and msg.content:
                        for content_block in msg.content:
                            if hasattr(content_block, 'text') and content_block.text:
                                text_value = getattr(content_block.text, 'value', '')
                                if text_value:
                                    response_text = text_value
                                    break
                    break

            if not response_text:
                logger.warning("No response text found in agent messages")
                return AgentRetrievalResult(
                    synthesized_answer="No response generated.",
                    references=[],
                    activity=AgentActivity(),
                    raw_response=str(messages)
                )

            logger.info(f"Agent response extracted: {len(response_text)} chars")

            # Extract references from annotations if available
            references = self._parse_annotations(last_agent_msg) if last_agent_msg else []

            # Note: Agent is NOT deleted - it persists for future requests
            # Thread is discarded after this request (stateless per query)

            return AgentRetrievalResult(
                synthesized_answer=response_text,
                references=references,
                activity=AgentActivity(
                    sources_retrieved=len(references)
                ),
                raw_response=str(messages)
            )

        except Exception as e:
            logger.error(f"Agent retrieval failed: {e}")
            raise

    def _parse_annotations(self, message) -> List[AgentReference]:
        """
        Parse citation annotations from the agent's response.

        Per SDK docs, Azure AI Search citations are accessed via:
        - message.url_citation_annotations (direct property on message)
        - Each annotation has: text (placeholder), url_citation.title, url_citation.url

        This extracts them into AgentReference objects.
        """
        references = []

        try:
            # SDK pattern: message.url_citation_annotations for Azure AI Search results
            # See: https://github.com/azure/azure-sdk-for-python/blob/main/sdk/ai/azure-ai-agents/README.md
            if hasattr(message, 'url_citation_annotations') and message.url_citation_annotations:
                logger.info(f"Found {len(message.url_citation_annotations)} URL citations")
                for annotation in message.url_citation_annotations:
                    if hasattr(annotation, 'url_citation'):
                        citation = annotation.url_citation
                        title = getattr(citation, 'title', '')
                        url = getattr(citation, 'url', '')

                        # Extract source_file from URL (last part of path)
                        source_file = url.split('/')[-1] if url else ''

                        references.append(AgentReference(
                            content=title,
                            citation=f"{title} - {url}" if url else title,
                            title=title,
                            source_file=source_file,
                            score=1.0  # URL citations don't have scores
                        ))

            # Also check file_citation_annotations for uploaded files
            if hasattr(message, 'file_citation_annotations') and message.file_citation_annotations:
                logger.info(f"Found {len(message.file_citation_annotations)} file citations")
                for annotation in message.file_citation_annotations:
                    if hasattr(annotation, 'file_citation'):
                        citation = annotation.file_citation
                        references.append(AgentReference(
                            content=getattr(citation, 'quote', ''),
                            citation=getattr(annotation, 'text', ''),
                            title=getattr(citation, 'file_id', ''),
                            source_file=getattr(citation, 'file_id', '')
                        ))

        except Exception as e:
            logger.warning(f"Failed to parse annotations: {e}")

        return references
