#!/usr/bin/env python3
"""
Create or Update the RUSH PolicyTech Foundry Agent

This script creates a persistent agent in Azure AI Foundry that can be
reused by the web application. Run once during initial setup or when
updating agent configuration.

Usage:
    python scripts/create_foundry_agent.py              # Create agent
    python scripts/create_foundry_agent.py --update     # Update existing
    python scripts/create_foundry_agent.py --delete     # Delete agent
    python scripts/create_foundry_agent.py --dry-run    # Show what would be done
    python scripts/create_foundry_agent.py --list       # List existing agents

Prerequisites:
    1. Azure CLI login: az login
    2. AZURE_AI_PROJECT_ENDPOINT set in .env
    3. Azure AI Search connection configured in Foundry project
"""

import sys
import os
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone

# Add backend to path for imports
backend_path = Path(__file__).resolve().parent.parent / "apps" / "backend"
sys.path.insert(0, str(backend_path))

# Load environment
from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

# Configuration
AGENT_NAME = "rush-policy-agent"
AGENT_MODEL = "gpt-4.1"
INDEX_NAME = "rush-policies"
AGENT_CONFIG_FILE = backend_path / "agent_config.json"
SYSTEM_PROMPT_FILE = backend_path / "policytech_prompt.txt"

# World-class RAG configuration for 1,800+ policy documents
DEFAULT_TOP_K = 50  # Optimal for semantic ranker to process candidates
DEFAULT_QUERY_TYPE = "VECTOR_SEMANTIC_HYBRID"  # Vector + BM25 + RRF + L2 reranking


def load_system_prompt() -> str:
    """Load the RISEN prompt from file."""
    if not SYSTEM_PROMPT_FILE.exists():
        raise FileNotFoundError(f"System prompt not found: {SYSTEM_PROMPT_FILE}")
    with open(SYSTEM_PROMPT_FILE, 'r') as f:
        return f.read()


def get_clients():
    """Initialize both AI Project Client (for connections) and Agents Client (for agents)."""
    from azure.ai.projects import AIProjectClient
    from azure.ai.agents import AgentsClient
    from azure.identity import DefaultAzureCredential

    endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT")
    if not endpoint:
        print("\nERROR: AZURE_AI_PROJECT_ENDPOINT not set")
        print("\nTo get this value:")
        print("  1. Go to Azure AI Foundry portal")
        print("  2. Open your project")
        print("  3. Go to Settings > Project properties")
        print("  4. Copy the 'Project endpoint' URL")
        print("\nAdd to .env:")
        print("  AZURE_AI_PROJECT_ENDPOINT=https://<ai-services>.services.ai.azure.com/api/projects/<project>")
        sys.exit(1)

    print(f"Connecting to: {endpoint}")
    credential = DefaultAzureCredential()

    # Project client for connections management
    project_client = AIProjectClient(endpoint=endpoint, credential=credential)

    # Agents client for agent CRUD operations
    agents_client = AgentsClient(endpoint=endpoint, credential=credential)

    return project_client, agents_client


def find_existing_agent(agents_client, name: str):
    """Find an existing agent by name."""
    try:
        # list_agents() returns an iterable
        agents = agents_client.list_agents()
        for agent in agents:
            if agent.name == name:
                return agent
    except Exception as e:
        print(f"Warning: Could not list agents: {e}")
    return None


def get_agent_details(agent) -> dict:
    """Extract details from agent object (handles both SDK versions)."""
    # azure-ai-agents Agent object has direct attributes
    if hasattr(agent, 'model'):
        return {
            'name': agent.name,
            'id': agent.id,
            'version': 'N/A',
            'model': agent.model or 'N/A',
            'instructions_len': len(agent.instructions or ''),
            'tools_count': len(agent.tools or []),
            'created_at': getattr(agent, 'created_at', 'N/A'),
        }

    # azure-ai-projects AgentObject uses _data structure
    data = agent._data if hasattr(agent, '_data') else {}
    versions = data.get('versions', {})
    latest = versions.get('latest', {})
    definition = latest.get('definition', {})

    return {
        'name': agent.name,
        'id': agent.id,
        'version': latest.get('version', 'N/A'),
        'model': definition.get('model', 'N/A'),
        'instructions_len': len(definition.get('instructions', '')),
        'tools_count': len(definition.get('tools', [])),
        'created_at': latest.get('created_at', 'N/A'),
    }


def list_all_agents(agents_client):
    """List all agents in the project."""
    try:
        # list_agents() returns an iterable
        agents = agents_client.list_agents()
        agent_list = list(agents)  # Convert to list for counting

        if not agent_list:
            print("No agents found in this project.")
            return

        print(f"\nFound {len(agent_list)} agent(s):\n")
        for agent in agent_list:
            details = get_agent_details(agent)
            print(f"  Name: {details['name']}")
            print(f"  ID: {details['id']}")
            print(f"  Version: {details['version']}")
            print(f"  Model: {details['model']}")
            print(f"  Instructions: {details['instructions_len']} chars")
            print(f"  Tools: {details['tools_count']}")
            print(f"  Created: {details['created_at']}")
            print()
    except Exception as e:
        print(f"Error listing agents: {e}")


def get_search_connection_id(project_client) -> str:
    """Get the Azure AI Search connection ID from the project."""
    from azure.ai.projects.models import ConnectionType

    # Try default connection first
    try:
        conn = project_client.connections.get_default(ConnectionType.AZURE_AI_SEARCH)
        print(f"Found default search connection: {conn.name}")
        return conn.id
    except Exception as e:
        print(f"No default search connection, searching...")

    # Fall back to listing connections
    try:
        connections = project_client.connections.list()
        for conn in connections:
            conn_type = getattr(conn, 'type', '').lower() if hasattr(conn, 'type') else ''
            conn_category = getattr(conn, 'category', '').lower() if hasattr(conn, 'category') else ''

            if 'search' in conn_type or 'search' in conn_category:
                print(f"Found search connection: {conn.name} (ID: {conn.id})")
                return conn.id
    except Exception as e:
        print(f"Error listing connections: {e}")

    print("\nERROR: No Azure AI Search connection found in Foundry project")
    print("\nTo add a search connection:")
    print("  1. Go to Azure AI Foundry portal")
    print("  2. Open your project > Settings > Connections")
    print("  3. Click 'New connection' > Azure AI Search")
    print("  4. Select your search service and save")
    sys.exit(1)


def create_agent(
    project_client,
    agents_client,
    model: str,
    instructions: str,
    index_name: str,
    query_type: str = DEFAULT_QUERY_TYPE,
    top_k: int = DEFAULT_TOP_K,
    update_existing: bool = False
) -> dict:
    """
    Create or update the PolicyTech agent with world-class RAG configuration.

    Uses VECTOR_SEMANTIC_HYBRID query type (Vector + BM25 + RRF + L2 reranking)
    with top_k=50 for optimal retrieval across 1,800+ policy documents.

    Uses azure-ai-agents SDK for proper tool configuration:
    - AzureAISearchTool: Provides .definitions and .resources for agent creation
    - AzureAISearchQueryType: Enum for query types (VECTOR_SEMANTIC_HYBRID, etc.)
    """
    from azure.ai.agents.models import AzureAISearchTool, AzureAISearchQueryType

    # Get search connection from project client
    conn_id = get_search_connection_id(project_client)

    # Map query type string to enum
    query_type_map = {
        "SIMPLE": AzureAISearchQueryType.SIMPLE,
        "SEMANTIC": AzureAISearchQueryType.SEMANTIC,
        "VECTOR": AzureAISearchQueryType.VECTOR,
        "VECTOR_SIMPLE_HYBRID": AzureAISearchQueryType.VECTOR_SIMPLE_HYBRID,
        "VECTOR_SEMANTIC_HYBRID": AzureAISearchQueryType.VECTOR_SEMANTIC_HYBRID,
    }
    search_query_type = query_type_map.get(query_type.upper(), AzureAISearchQueryType.SEMANTIC)

    print(f"\nConfiguring Azure AI Search Tool:")
    print(f"  Query Type: {query_type} (optimal for semantic understanding)")
    print(f"  Top K: {top_k} (focused retrieval)")
    print(f"  Index: {index_name}")
    print(f"  Connection: {conn_id}")

    # Configure Azure AI Search tool with world-class settings
    ai_search = AzureAISearchTool(
        index_connection_id=conn_id,
        index_name=index_name,
        query_type=search_query_type,
        top_k=top_k,
        filter="",
    )

    print(f"  Tool definitions: {ai_search.definitions}")
    print(f"  Tool resources: {ai_search.resources}")

    # Check for existing agent using agents_client
    existing = find_existing_agent(agents_client, AGENT_NAME)

    if existing and update_existing:
        # Delete and recreate (update not directly supported)
        agents_client.delete_agent(existing.id)
        print(f"\nDeleted existing agent for update: {existing.name}")

        agent = agents_client.create_agent(
            model=model,
            name=AGENT_NAME,
            instructions=instructions,
            tools=ai_search.definitions,
            tool_resources=ai_search.resources,
        )
        print(f"\nRecreated agent: {agent.name}")
        print(f"  ID: {agent.id}")
        status = "updated"
    elif existing and not update_existing:
        # Get details from existing agent
        details = get_agent_details(existing)
        print(f"\nAgent already exists: {existing.name}")
        print(f"  ID: {existing.id}")
        print(f"  Model: {details['model']}")
        print(f"  Instructions: {details['instructions_len']} chars")
        print(f"  Tools: {details['tools_count']}")
        print("\nUse --update to update the existing agent")
        return {
            "agent_id": existing.id,
            "agent_name": existing.name,
            "model": details['model'],
            "created_at": str(details['created_at']),
            "status": "existing"
        }
    else:
        # Create new agent
        agent = agents_client.create_agent(
            model=model,
            name=AGENT_NAME,
            instructions=instructions,
            tools=ai_search.definitions,
            tool_resources=ai_search.resources,
        )
        print(f"\nCreated agent: {agent.name}")
        print(f"  ID: {agent.id}")
        status = "created"

    # Extract model from agent or use configured model
    agent_model = getattr(agent, 'model', model)
    created_at = getattr(agent, 'created_at', datetime.now(timezone.utc).isoformat())

    return {
        "agent_id": agent.id,
        "agent_name": agent.name,
        "model": agent_model,
        "created_at": str(created_at),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "index_name": index_name,
        "search_connection_id": conn_id,
        "query_type": query_type,
        "top_k": top_k,
        "status": status
    }


def delete_agent(agents_client) -> bool:
    """Delete the existing agent."""
    existing = find_existing_agent(agents_client, AGENT_NAME)
    if existing:
        agents_client.delete_agent(existing.id)
        print(f"\nDeleted agent: {existing.name}")
        print(f"  ID: {existing.id}")
        return True
    else:
        print(f"\nNo agent found with name: {AGENT_NAME}")
        return False


def save_agent_config(config: dict):
    """Save agent configuration for the web app."""
    AGENT_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(AGENT_CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)
    print(f"\nSaved config to: {AGENT_CONFIG_FILE}")


def main():
    parser = argparse.ArgumentParser(
        description="Manage RUSH PolicyTech Foundry Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/create_foundry_agent.py              # Create new agent
  python scripts/create_foundry_agent.py --update     # Update existing agent
  python scripts/create_foundry_agent.py --list       # List all agents
  python scripts/create_foundry_agent.py --delete     # Delete agent
  python scripts/create_foundry_agent.py --dry-run    # Preview without changes
        """
    )
    parser.add_argument("--update", action="store_true", help="Update existing agent")
    parser.add_argument("--delete", action="store_true", help="Delete existing agent")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    parser.add_argument("--list", action="store_true", help="List all agents")
    parser.add_argument("--model", default=AGENT_MODEL, help=f"Model deployment name (default: {AGENT_MODEL})")
    parser.add_argument("--index", default=INDEX_NAME, help=f"Search index name (default: {INDEX_NAME})")
    parser.add_argument("--query-type", default=DEFAULT_QUERY_TYPE,
                        choices=["SIMPLE", "SEMANTIC", "VECTOR", "VECTOR_SIMPLE_HYBRID", "VECTOR_SEMANTIC_HYBRID"],
                        help=f"Search query type (default: {DEFAULT_QUERY_TYPE})")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K,
                        help=f"Number of documents to retrieve (default: {DEFAULT_TOP_K})")
    args = parser.parse_args()

    print("=" * 60)
    print("RUSH PolicyTech Agent Manager")
    print("=" * 60)

    # Initialize both clients
    project_client, agents_client = get_clients()
    print("Connected successfully!")

    if args.list:
        list_all_agents(agents_client)
        return 0

    if args.dry_run:
        print("\nDRY RUN - No changes will be made\n")
        existing = find_existing_agent(agents_client, AGENT_NAME)
        if existing:
            details = get_agent_details(existing)
            print(f"Found existing agent: {existing.name}")
            print(f"  ID: {existing.id}")
            print(f"  Model: {details['model']}")
        else:
            print("No existing agent found - would create new")

        print(f"\nConfiguration:")
        print(f"  Agent name: {AGENT_NAME}")
        print(f"  Model: {args.model}")
        print(f"  Index: {args.index}")
        print(f"  Query Type: {args.query_type}")
        print(f"  Top K: {args.top_k}")
        print(f"  Prompt file: {SYSTEM_PROMPT_FILE}")
        return 0

    if args.delete:
        deleted = delete_agent(agents_client)
        # Remove config file
        if AGENT_CONFIG_FILE.exists():
            AGENT_CONFIG_FILE.unlink()
            print(f"Removed config file: {AGENT_CONFIG_FILE}")
        return 0 if deleted else 1

    # Load system prompt
    instructions = load_system_prompt()
    print(f"\nLoaded system prompt ({len(instructions)} chars)")

    # Create/update agent with world-class configuration
    config = create_agent(
        project_client=project_client,
        agents_client=agents_client,
        model=args.model,
        instructions=instructions,
        index_name=args.index,
        query_type=args.query_type,
        top_k=args.top_k,
        update_existing=args.update
    )

    # Save config for web app
    if config["status"] != "existing":
        save_agent_config(config)

    print("\n" + "=" * 60)
    print("AGENT CONFIGURATION")
    print("=" * 60)
    for key, value in config.items():
        print(f"  {key}: {value}")

    print("\n" + "=" * 60)
    print("NEXT STEPS")
    print("=" * 60)
    print(f"\n1. Add to .env:")
    print(f"   FOUNDRY_AGENT_ID={config['agent_id']}")
    print(f"\n2. Restart the backend:")
    print(f"   ./start_backend.sh")
    print(f"\n3. Test the chat:")
    print(f"   curl -X POST http://localhost:8000/api/chat \\")
    print(f'     -H "Content-Type: application/json" \\')
    print(f'     -d \'{{"message": "What is the visitor policy?"}}\'')

    return 0


if __name__ == "__main__":
    sys.exit(main())
