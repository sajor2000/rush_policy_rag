import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import azure.functions as func
from azure.identity import OnBehalfOfCredential
from azure.ai.projects import AIProjectClient
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError

logger = logging.getLogger("agent_proxy")

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


def _allowed_origin(request_origin: Optional[str]) -> Optional[str]:
    raw_origins = os.getenv("AGENT_PROXY_ALLOWED_ORIGINS", "*").strip()
    if raw_origins == "*":
        return "*"

    allowed = {origin.strip() for origin in raw_origins.split(",") if origin.strip()}
    if not allowed:
        return None

    if request_origin and request_origin in allowed:
        return request_origin

    # If the origin does not match, reject the request explicitly
    return None


def _cors_headers(origin: Optional[str]) -> Dict[str, str]:
    headers = {
        "Access-Control-Allow-Methods": "POST,OPTIONS",
        "Access-Control-Allow-Headers": "Authorization,Content-Type",
        "Access-Control-Max-Age": "86400",
    }

    if origin:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Vary"] = "Origin"
    else:
        headers["Access-Control-Allow-Origin"] = "null"

    return headers


def _json_response(body: Dict[str, Any], status: int, origin: Optional[str]) -> func.HttpResponse:
    headers = _cors_headers(origin)
    return func.HttpResponse(
        body=json.dumps(body),
        status_code=status,
        mimetype="application/json",
        headers=headers,
    )


def _get_project_client(user_token: str) -> AIProjectClient:
    project_endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT")
    agent_client_id = os.getenv("AZURE_AD_CLIENT_ID")
    agent_client_secret = os.getenv("AZURE_AD_CLIENT_SECRET")
    tenant_id = os.getenv("AZURE_AD_TENANT_ID")

    missing = [
        key
        for key, value in {
            "AZURE_AI_PROJECT_ENDPOINT": project_endpoint,
            "AZURE_AD_CLIENT_ID": agent_client_id,
            "AZURE_AD_CLIENT_SECRET": agent_client_secret,
            "AZURE_AD_TENANT_ID": tenant_id,
        }.items()
        if not value
    ]

    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    credential = OnBehalfOfCredential(
        client_id=agent_client_id,
        client_secret=agent_client_secret,
        tenant_id=tenant_id,
        user_assertion=user_token,
    )

    return AIProjectClient(endpoint=project_endpoint, credential=credential)


def _invoke_agent(
    client: AIProjectClient, message: str, thread_id: Optional[str]
) -> Tuple[str, str, str, List[Dict[str, Any]], str]:
    agent_id = os.getenv("AZURE_AI_PROJECT_AGENT_ID")
    if not agent_id:
        raise ValueError("AZURE_AI_PROJECT_AGENT_ID is not configured")

    if not thread_id:
        thread = client.agents.create_thread()
        thread_id = thread.id

    client.agents.create_message(
        thread_id=thread_id,
        role="user",
        content=message,
    )

    run = client.agents.create_and_process_run(
        thread_id=thread_id,
        agent_id=agent_id,
    )

    if run.status == "failed":
        raise RuntimeError(f"Agent run failed: {run.last_error}")

    messages = client.agents.list_messages(thread_id=thread_id)
    assistant_message = messages.get_last_text_message_by_sender("assistant")

    response_text = ""
    references: List[Dict[str, Any]] = []

    if assistant_message and getattr(assistant_message, "text", None):
        response_text = assistant_message.text.value
        references = _extract_references(assistant_message)

    return thread_id, run.id, response_text, references, str(messages)


def _extract_references(message: Any) -> List[Dict[str, Any]]:
    annotations = getattr(message, "annotations", None)
    references: List[Dict[str, Any]] = []

    if not annotations:
        return references

    for annotation in annotations:
        citation = getattr(annotation, "text", "")
        metadata = getattr(annotation, "file_citation", None)
        references.append(
            {
                "citation": citation,
                "title": getattr(annotation, "title", ""),
                "source_file": getattr(metadata, "file_id", ""),
                "quote": getattr(metadata, "quote", ""),
            }
        )

    return references


@app.route(route="agent/messages", methods=["POST", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
def agent_messages(req: func.HttpRequest) -> func.HttpResponse:
    request_origin = req.headers.get("Origin")
    origin = _allowed_origin(request_origin)

    if req.method == "OPTIONS":
        return _json_response({}, status=204, origin=origin or request_origin)

    if origin is None:
        return _json_response({"error": "Origin not allowed"}, status=403, origin=None)

    auth_header = req.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return _json_response({"error": "Missing bearer token"}, status=401, origin=origin)

    user_token = auth_header.split(" ", 1)[1]

    try:
        payload = req.get_json()
    except ValueError:
        return _json_response({"error": "Invalid JSON payload"}, status=400, origin=origin)

    message = payload.get("message") if isinstance(payload, dict) else None
    thread_id = payload.get("thread_id") if isinstance(payload, dict) else None

    if not message or not isinstance(message, str):
        return _json_response({"error": "Message is required"}, status=400, origin=origin)

    try:
        project_client = _get_project_client(user_token)
        thread_id, run_id, response_text, references, raw_messages = _invoke_agent(
            project_client, message.strip(), thread_id if isinstance(thread_id, str) else None
        )

        response_body = {
            "response": response_text or "No response generated",
            "thread_id": thread_id,
            "run_id": run_id,
            "references": references,
            "raw_response": raw_messages,
        }

        return _json_response(response_body, status=200, origin=origin)

    except ValueError as err:
        logger.error("Configuration error: %s", err)
        return _json_response({"error": str(err)}, status=500, origin=origin)
    except ClientAuthenticationError as err:
        logger.warning("Authentication failed: %s", err)
        return _json_response({"error": "Authentication failed"}, status=401, origin=origin)
    except HttpResponseError as err:
        logger.error("Azure AI request failed: %s", err)
        return _json_response({"error": "Agent invocation failed"}, status=502, origin=origin)
    except RuntimeError as err:
        logger.error("Agent runtime error: %s", err)
        return _json_response({"error": str(err)}, status=500, origin=origin)
    except Exception as err:  # pylint: disable=broad-except
        logger.exception("Unexpected agent proxy error")
        return _json_response(
            {"error": "Unexpected error", "detail": str(err)},
            status=500,
            origin=origin,
        )
