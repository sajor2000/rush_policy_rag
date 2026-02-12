from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "tests" / "promptfoo" / "data" / "rag_basic.jsonl"
KNOWN_SERVER_NAMES = {
    "rag-eval",
    "rag-evaluation",
    "promptfoo",
    "promptfoo-dataset",
}


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        print(f"Invalid JSON in {path}")
        return None


def load_mcp_servers() -> Dict[str, dict]:
    servers: Dict[str, dict] = {}
    project_config = PROJECT_ROOT / ".factory" / "mcp.json"
    user_config = Path.home() / ".factory" / "mcp.json"

    project_data = _read_json(project_config)
    if project_data:
        servers.update(project_data.get("mcpServers", {}))

    user_data = _read_json(user_config)
    if user_data:
        servers.update(user_data.get("mcpServers", {}))

    return servers


def select_server(servers: Dict[str, dict], preferred: Optional[str]) -> Tuple[Optional[str], Optional[dict]]:
    if preferred:
        return preferred, servers.get(preferred)

    for name in KNOWN_SERVER_NAMES:
        if name in servers:
            return name, servers[name]

    return None, None


def _mcp_request(url: str, headers: Dict[str, str], payload: dict) -> dict:
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def _list_tools(url: str, headers: Dict[str, str]) -> List[dict]:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    data = _mcp_request(url, headers, payload)
    result = data.get("result", {})
    tools = result.get("tools") or result.get("items") or []
    if isinstance(tools, list):
        return tools
    return []


def _call_tool(url: str, headers: Dict[str, str], tool_name: str, args: dict) -> Any:
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": args},
    }
    data = _mcp_request(url, headers, payload)
    if "error" in data:
        raise RuntimeError(data["error"])
    result = data.get("result", {})
    if isinstance(result, dict) and "content" in result:
        content = result.get("content") or []
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and first.get("type") == "text":
                text = first.get("text", "")
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
    return result


def _build_regex(expected_keyword: str) -> str:
    return f"(?i){re.escape(expected_keyword)}"


def normalize_cases(payload: Any) -> List[dict]:
    if isinstance(payload, dict):
        cases = payload.get("tests") or payload.get("test_cases") or payload.get("cases")
    else:
        cases = payload

    if not isinstance(cases, list):
        return []

    normalized: List[dict] = []
    for idx, case in enumerate(cases, start=1):
        if isinstance(case, dict) and "vars" in case and "assert" in case:
            normalized.append(case)
            continue

        if not isinstance(case, dict):
            continue

        question = case.get("question") or case.get("query")
        if not question:
            continue

        vars_payload = {"question": question}
        if case.get("expected_keyword"):
            vars_payload["expected_keyword"] = case["expected_keyword"]
        if case.get("expected_regex"):
            vars_payload["expected_regex"] = case["expected_regex"]

        asserts: List[dict] = []
        if case.get("expected_regex"):
            asserts.append({"type": "regex", "value": case["expected_regex"]})
        elif case.get("expected_keyword"):
            asserts.append({"type": "regex", "value": _build_regex(case["expected_keyword"])})

        expect_found = case.get("expect_found")
        expect_evidence = case.get("expect_evidence")

        if expect_found is True:
            asserts.append({"type": "javascript", "value": "context.providerResponse?.metadata?.found === true"})
        elif expect_found is False:
            asserts.append({"type": "javascript", "value": "context.providerResponse?.metadata?.found === false"})

        if expect_evidence is True:
            asserts.append({"type": "javascript", "value": "(context.providerResponse?.metadata?.evidenceCount ?? 0) > 0"})
        elif expect_evidence is False:
            asserts.append({"type": "javascript", "value": "(context.providerResponse?.metadata?.evidenceCount ?? 0) === 0"})

        if not asserts:
            asserts.append({"type": "javascript", "value": "output && output.length > 0"})

        normalized.append({
            "description": case.get("description") or case.get("id") or f"MCP case {idx}",
            "vars": vars_payload,
            "assert": asserts,
        })

    return normalized


def write_jsonl(cases: List[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Promptfoo dataset from MCP (if available)")
    parser.add_argument("--server", default=os.getenv("PROMPTFOO_MCP_SERVER"))
    parser.add_argument("--tool", default=os.getenv("PROMPTFOO_MCP_TOOL", "get_rag_test_cases"))
    parser.add_argument("--args", default=os.getenv("PROMPTFOO_MCP_ARGS", "{}"))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    servers = load_mcp_servers()
    server_name, server = select_server(servers, args.server)
    if not server:
        print("No MCP server configured for dataset sync.")
        return 0

    if server.get("disabled"):
        print(f"MCP server '{server_name}' is disabled.")
        return 1

    if server.get("type") != "http":
        print(f"MCP server '{server_name}' is not an HTTP server.")
        return 1

    url = server.get("url")
    if not url:
        print(f"MCP server '{server_name}' missing URL.")
        return 1

    headers = server.get("headers", {}) or {}
    try:
        tools = _list_tools(url, headers)
        tool_names = {tool.get("name") for tool in tools if isinstance(tool, dict)}
        if args.tool not in tool_names:
            print(f"Tool '{args.tool}' not found on MCP server '{server_name}'.")
            return 1

        try:
            tool_args = json.loads(args.args)
        except json.JSONDecodeError:
            print("Invalid JSON in --args")
            return 1

        payload = _call_tool(url, headers, args.tool, tool_args)
    except Exception as exc:
        print(f"MCP sync failed: {exc}")
        return 1

    cases = normalize_cases(payload)
    if not cases:
        print("No test cases returned from MCP tool.")
        return 1

    output_path = Path(args.output)
    write_jsonl(cases, output_path)
    print(f"Wrote {len(cases)} test cases to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
