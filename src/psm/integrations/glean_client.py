"""REST client for the Glean Client API (search + chat).

Replaces the Enterpret/Wisdom MCP transport (wisdom_client.py). Glean exposes a
standard REST API rather than an MCP/Cypher gateway, so this is plain JSON over
HTTPS with a bearer token — no SSE/JSON-RPC envelope to unwrap.

Environment (live):
  GLEAN_INSTANCE   — your Glean instance/subdomain, e.g. "launchdarkly".
                     Used to build https://{instance}-be.glean.com/rest/api/v1
  GLEAN_BASE_URL   — (optional) full REST base URL override. Takes precedence
                     over GLEAN_INSTANCE. e.g. https://launchdarkly-be.glean.com/rest/api/v1
  GLEAN_API_TOKEN  — Client API bearer token (scope: SEARCH / CHAT)
  GLEAN_ACT_AS     — (optional) email to act-as for server-to-server tokens

Docs: https://developers.glean.com/api/client-api/  (search, chat endpoints)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


def glean_configured() -> bool:
    token = os.environ.get("GLEAN_API_TOKEN", "").strip()
    base = _base_url()
    return bool(token and base)


def _base_url() -> str | None:
    explicit = os.environ.get("GLEAN_BASE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    instance = os.environ.get("GLEAN_INSTANCE", "").strip()
    if instance:
        return f"https://{instance}-be.glean.com/rest/api/v1"
    return None


def _token() -> str | None:
    return os.environ.get("GLEAN_API_TOKEN", "").strip() or None


def _post(path: str, payload: dict[str, Any], timeout: int = 60) -> tuple[bool, Any]:
    """POST JSON to a Glean REST endpoint. Returns (ok, parsed_json_or_error)."""
    base = _base_url()
    token = _token()
    if not base or not token:
        return False, "Set GLEAN_API_TOKEN and GLEAN_INSTANCE (or GLEAN_BASE_URL)"

    url = f"{base}/{path.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    act_as = os.environ.get("GLEAN_ACT_AS", "").strip()
    if act_as:
        headers["X-Glean-ActAs"] = act_as

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        return False, f"HTTP {e.code}: {detail or e.reason}"
    except urllib.error.URLError as e:
        return False, str(getattr(e, "reason", e))

    try:
        return True, json.loads(text)
    except json.JSONDecodeError:
        return False, f"Non-JSON response: {text[:500]}"


def glean_search(
    query: str,
    *,
    page_size: int = 15,
    datasources: list[str] | None = None,
    cursor: str | None = None,
) -> tuple[bool, Any]:
    """POST /search. Returns (ok, response) where response has `results` + `cursor`.

    datasources maps to requestOptions.facetFilters on the `datasource` field —
    the Glean equivalent of Wisdom's entity_types narrowing.
    """
    payload: dict[str, Any] = {"query": query, "pageSize": page_size}
    if cursor:
        payload["cursor"] = cursor
    if datasources:
        payload["requestOptions"] = {
            "facetFilters": [
                {
                    "fieldName": "datasource",
                    "values": [{"value": ds, "relationType": "EQUALS"} for ds in datasources],
                }
            ]
        }
    return _post("search", payload)


def glean_chat(message: str, context: list[str] | None = None) -> tuple[bool, Any]:
    """POST /chat. Glean's RAG synthesis — the nearest analog to Wisdom theme
    summaries. Returns (ok, response). The answer text lives under
    messages[].fragments[].text in the response."""
    messages: list[dict[str, Any]] = []
    for prior in context or []:
        messages.append({"author": "USER", "fragments": [{"text": prior}]})
    messages.append({"author": "USER", "fragments": [{"text": message}]})
    return _post("chat", {"messages": messages})
