"""
Example script for querying the RUSH policy knowledge base.
"""

import os
from policy_rag_setup import query_knowledge_base, get_credential, load_risen_prompt
from azure.search.documents.agent import KnowledgeAgentRetrievalClient

# Configuration
SEARCH_ENDPOINT = os.environ.get("SEARCH_ENDPOINT", "https://policychataisearch.search.windows.net")
KNOWLEDGE_BASE_NAME = "rush-policies-kb"

# Load RISEN prompt (will use default if file not found)
SYSTEM_MESSAGE = load_risen_prompt() or (
    "You are PolicyTech, a RUSH policy retrieval agent. "
    "Only answer from retrieved documents. "
    "Be precise and cite sources when possible."
)


def main():
    """Run example queries."""
    print("="*60)
    print("RUSH Policy Knowledge Base Query Examples")
    print("="*60)
    
    # Initialize client
    credential = get_credential()
    agent_client = KnowledgeAgentRetrievalClient(
        endpoint=SEARCH_ENDPOINT,
        agent_name=KNOWLEDGE_BASE_NAME,
        credential=credential
    )
    
    # Example queries
    example_queries = [
        "Who can accept verbal orders?",
        "What is the policy on medication administration?",
        "What are the requirements for patient consent?",
    ]
    
    for i, query in enumerate(example_queries, 1):
        print(f"\n{'='*60}")
        print(f"Example Query {i}")
        print(f"{'='*60}")
        
        try:
            result = query_knowledge_base(
                agent_client,
                user_message=query,
                system_message=SYSTEM_MESSAGE
            )
            print(f"\n✓ Query {i} completed successfully")
        except Exception as e:
            print(f"\n✗ Query {i} failed: {e}")
    
    # Interactive mode
    print(f"\n{'='*60}")
    print("Interactive Mode")
    print("="*60)
    print("Enter queries (type 'exit' to quit):\n")
    
    while True:
        try:
            user_input = input("Query: ").strip()
            if user_input.lower() in ['exit', 'quit', 'q']:
                break
            
            if not user_input:
                continue
            
            result = query_knowledge_base(
                agent_client,
                user_message=user_input,
                system_message=SYSTEM_MESSAGE
            )
        except KeyboardInterrupt:
            print("\n\nExiting...")
            break
        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    main()

