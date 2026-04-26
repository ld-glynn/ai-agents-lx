"""HTTP+SSE client for Enterpret Wisdom MCP (same wire format as Cursor MCP / enterpret Next app).

Set WISDOM_API_BASE_URL to the full MCP URL and WISDOM_API_TOKEN as the Bearer token.
"""

from __future__ import annotations

import json
import os
import random
import urllib.error
import urllib.request
from typing import Any

MCP_ACCEPT = "application/json, text/event-stream"


def wisdom_configured() -> bool:
    base = os.environ.get("WISDOM_API_BASE_URL", "").strip()
    token = os.environ.get("WISDOM_API_TOKEN", "").strip()
    return bool(base and token)


def _endpoint() -> str | None:
    b = os.environ.get("WISDOM_API_BASE_URL", "").strip()
    return b.rstrip("/") if b else None


def _bearer() -> str | None:
    t = os.environ.get("WISDOM_API_TOKEN", "").strip()
    return t or None


def _parse_sse_result(sse_text: str, expected_id: int) -> tuple[bool, Any]:
    for line in sse_text.splitlines():
        line = line.replace("\r", "")
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if msg.get("id") != expected_id:
            continue
        if msg.get("error"):
            err = msg["error"]
            return False, (err.get("message") if isinstance(err, dict) else str(err))
        return True, msg.get("result")
    return False, f"No JSON-RPC result for id {expected_id} in SSE response"


def _unwrap_tool_result(result: Any) -> Any:
    if not result or not isinstance(result, dict):
        return result
    if result.get("isError"):
        return {"error": True, "detail": result}
    sc = result.get("structuredContent")
    if isinstance(sc, dict) and "structuredContent" in sc and sc.get("structuredContent") is not None:
        return sc["structuredContent"]
    if sc is not None:
        return sc
    content = result.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and first.get("type") == "text":
            text = first.get("text")
            if isinstance(text, str):
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
    return result


def call_wisdom_tool(name: str, arguments: dict[str, Any]) -> tuple[bool, Any]:
    """Call a Wisdom MCP tool by name. Returns (ok, data_or_error_message)."""
    url = _endpoint()
    token = _bearer()
    if not url or not token:
        return False, "Set WISDOM_API_BASE_URL and WISDOM_API_TOKEN"

    req_id = random.randint(1, 2_147_483_647)
    payload = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": MCP_ACCEPT,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return False, e.read().decode("utf-8", errors="replace") or str(e)
    except urllib.error.URLError as e:
        return False, str(e.reason if hasattr(e, "reason") else e)

    ok, parsed = _parse_sse_result(text, req_id)
    if not ok:
        return False, parsed
    unwrapped = _unwrap_tool_result(parsed)
    if isinstance(unwrapped, dict) and unwrapped.get("error"):
        return False, json.dumps(unwrapped)
    return True, unwrapped
