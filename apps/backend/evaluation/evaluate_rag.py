import os
import json
import requests
from pathlib import Path
from dotenv import load_dotenv
from azure.ai.evaluation import evaluate, GroundednessEvaluator, RelevanceEvaluator
from azure.identity import DefaultAzureCredential

# Load environment variables
env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(env_path)

# Configuration
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
AOAI_ENDPOINT = os.environ.get("AOAI_ENDPOINT")
AOAI_API_KEY = os.environ.get("AOAI_API") or os.environ.get("AOAI_API_KEY")
AOAI_CHAT_DEPLOYMENT = os.environ.get("AOAI_CHAT_DEPLOYMENT", "gpt-4")

def target_fn(query):
    """
    Target function for evaluation.
    Calls the local RAG API.
    """
    try:
        response = requests.post(
            f"{BACKEND_URL}/api/chat",
            json={"message": query},
            headers={"Content-Type": "application/json"}
        )
        response.raise_for_status()
        data = response.json()
        
        # Return format expected by evaluators
        return {
            "response": data.get("response", ""),
            "context": "\n\n".join([e["snippet"] for e in data.get("evidence", [])])
        }
    except Exception as e:
        print(f"Error calling API: {e}")
        return {"response": "Error", "context": ""}

def main():
    if not AOAI_ENDPOINT or not AOAI_API_KEY:
        print("Error: Azure OpenAI credentials not found in environment.")
        return

    # Model configuration for evaluators
    model_config = {
        "azure_endpoint": AOAI_ENDPOINT,
        "api_key": AOAI_API_KEY,
        "azure_deployment": AOAI_CHAT_DEPLOYMENT,
        "api_version": "2024-06-01",
    }

    # Initialize evaluators
    groundedness_eval = GroundednessEvaluator(model_config)
    relevance_eval = RelevanceEvaluator(model_config)

    # Load dataset
    data_path = Path(__file__).parent / "dataset.jsonl"
    
    print(f"Starting evaluation against {BACKEND_URL}...")
    
    # Run evaluation
    results = evaluate(
        target=target_fn,
        data=str(data_path),
        evaluators={
            "groundedness": groundedness_eval,
            "relevance": relevance_eval
        },
        # Map dataset fields to evaluator inputs
        evaluator_config={
            "groundedness": {
                "response": "${target.response}",
                "context": "${target.context}"
            },
            "relevance": {
                "response": "${target.response}",
                "query": "${data.query}"
            }
        }
    )

    print("\nEvaluation Results:")
    print(json.dumps(results, indent=2))
    
    # Calculate aggregates
    if "rows" in results:
        avg_groundedness = sum(r.get("groundedness.score", 0) for r in results["rows"]) / len(results["rows"])
        avg_relevance = sum(r.get("relevance.score", 0) for r in results["rows"]) / len(results["rows"])
        print(f"\nAverage Groundedness: {avg_groundedness:.2f}")
        print(f"Average Relevance: {avg_relevance:.2f}")

if __name__ == "__main__":
    main()
