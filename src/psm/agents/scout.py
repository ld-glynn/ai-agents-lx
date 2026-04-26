"""Scout — Tier 1 Engine Agent for autonomous Wisdom knowledge graph exploration.

Sits upstream of the Structurer. Its sole job is to decide what to query,
surface entities that show concrete evidence of recurrence, and hand off plain
IngestionRecords to the existing pipeline. It does not normalize, cluster,
hypothesize, or assess solvability — all of that is owned by downstream agents.

Mock mode bypasses the LLM loop and reads the static Wisdom fixture file,
so it can be used offline without API credentials.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from anthropic import Anthropic

from psm.config import settings
from psm.integrations.wisdom_client import call_wisdom_tool, wisdom_configured
from psm.schemas.ingestion import IngestionRecord, SourceType
from psm.tools.context_loader import load_job_description, load_onboarding

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MAX_TOOL_CALLS = 25
DEFAULT_MAX_FINDINGS = 30


@dataclass
class ScoutConfig:
    """Runtime configuration for a Scout session."""

    mock: bool = False
    model: str = DEFAULT_MODEL
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    max_findings: int = DEFAULT_MAX_FINDINGS
    # Optional narrow focus passed into the system prompt. When empty, the Scout
    # falls back to onboarding.md for grounding and explores broadly.
    focus_hint: str | None = None


@dataclass
class ScoutTraceEntry:
    """One iteration of the Scout's tool-use loop."""

    tool: str
    arguments: dict[str, Any]
    result_summary: str
    succeeded: bool


@dataclass
class ScoutResult:
    """Everything a Scout run produced — records for the pipeline plus an audit trail."""

    records: list[IngestionRecord] = field(default_factory=list)
    trace: list[ScoutTraceEntry] = field(default_factory=list)
    finish_reason: str | None = None
    tool_calls_used: int = 0
    findings_emitted: int = 0
    stop_cause: str = "unknown"  # "finish" | "tool_budget" | "finding_budget" | "model_end_turn" | "error"


# --- Tool schemas exposed to Claude -----------------------------------------

_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_organization_details",
        "description": (
            "One-shot orientation call. Fetch high-level information about the organization the "
            "knowledge graph covers — industries, products, top customers. Call this early, once."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_schema",
        "description": (
            "One-shot orientation call. Fetch the schema of the Wisdom knowledge graph — entity "
            "types, relationships, and properties. Call this early, once. Use the results to "
            "inform subsequent search and Cypher queries."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "search_knowledge_graph",
        "description": (
            "Semantic search over the knowledge graph. Good for initial broad exploration. "
            "Returns hits ranked by relevance. For recurrence-focused queries (counts, time "
            "windows, multi-source filtering) prefer execute_cypher_query."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language search query"},
                "entity_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional filter on entity types returned by get_schema",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Max hits to return. Default 15.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "execute_cypher_query",
        "description": (
            "Run a Cypher query against the knowledge graph. Use this for structured queries "
            "semantic search can't express — counting mentions per entity, grouping by source, "
            "filtering by time window, finding entities with high recurrence."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cypher_query": {"type": "string", "description": "Cypher query text"},
                "description": {
                    "type": "string",
                    "description": "Short note on what this query is trying to find (for audit).",
                },
            },
            "required": ["cypher_query"],
        },
    },
    {
        "name": "record_finding",
        "description": (
            "Log a knowledge-graph entity as a finding worth passing downstream. Only call this "
            "for entities where you have concrete evidence of recurrence — the entity appears "
            "frequently, across multiple sources, or over a sustained time window. "
            "recurrence_evidence MUST be concrete (counts, sources, dates), not vague."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "Stable identifier for the entity from the Wisdom graph",
                },
                "title": {"type": "string", "description": "Short human-readable label"},
                "summary": {
                    "type": "string",
                    "description": "2-4 sentences describing what this problem/theme is about",
                },
                "recurrence_evidence": {
                    "type": "string",
                    "description": "Concrete evidence of recurrence — mention counts, time span, source spread, distinct reporter count.",
                },
                "upstream_sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Upstream sources where this entity surfaces (slack, gong, salesforce, zendesk, …).",
                },
                "entity_type": {
                    "type": "string",
                    "description": "Entity type from the Wisdom schema, if known.",
                },
            },
            "required": ["entity_id", "title", "summary", "recurrence_evidence"],
        },
    },
    {
        "name": "finish",
        "description": (
            "Signal that exploration is complete. Call this when further searches are unlikely "
            "to surface new recurrence signal, OR when the findings you've recorded give a "
            "reasonable picture of the recurring problems in the graph."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Short explanation of why you're stopping.",
                },
            },
            "required": ["reason"],
        },
    },
]

_TOOL_NAMES = {t["name"] for t in _TOOLS}


# --- System prompt ----------------------------------------------------------


def _build_system_prompt(config: ScoutConfig) -> str:
    job_desc = load_job_description("scout")
    try:
        onboarding = load_onboarding()
    except FileNotFoundError:
        onboarding = "(no onboarding.md found — explore broadly with no team-specific focus)"

    focus_block = ""
    if config.focus_hint and config.focus_hint.strip():
        focus_block = (
            "\n\n## This run's focus hint\n"
            f"{config.focus_hint.strip()}\n\n"
            "Treat this as a suggested starting point for your initial queries. It does NOT "
            "mean you should only surface findings in this area — continue to follow recurrence "
            "signal wherever it leads."
        )

    return (
        f"{job_desc}\n\n"
        "## Budget\n"
        f"- Max tool calls: {config.max_tool_calls}\n"
        f"- Max findings: {config.max_findings}\n\n"
        "## Team context (onboarding.md)\n"
        f"{onboarding}"
        f"{focus_block}"
    )


# --- Tool dispatcher --------------------------------------------------------


def _short(obj: Any, limit: int = 240) -> str:
    s = json.dumps(obj, default=str) if not isinstance(obj, str) else obj
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _sanitize_id(raw: str) -> str:
    return re.sub(r"[^\w.-]+", "-", raw.strip())[:80]


class _ScoutSession:
    """Mutable state for a single Scout run — budgets, findings, trace."""

    def __init__(self, config: ScoutConfig):
        self.config = config
        self.findings: list[dict[str, Any]] = []
        self.trace: list[ScoutTraceEntry] = []
        self.tool_calls_used = 0
        self.finish_reason: str | None = None

    def dispatch(self, name: str, args: dict[str, Any]) -> tuple[bool, Any]:
        """Run one tool call. Returns (succeeded, result_for_claude)."""
        self.tool_calls_used += 1
        succeeded = True
        result: Any

        if name == "get_organization_details":
            ok, data = call_wisdom_tool("get_organization_details", {})
            if ok:
                result = data
            else:
                succeeded = False
                result = {"error": str(data)}

        elif name == "get_schema":
            ok, data = call_wisdom_tool("get_schema", {})
            if ok:
                result = data
            else:
                succeeded = False
                result = {"error": str(data)}

        elif name == "search_knowledge_graph":
            query = args.get("query", "")
            if not isinstance(query, str) or not query.strip():
                succeeded = False
                result = {"error": "query is required"}
            else:
                payload: dict[str, Any] = {"query": query}
                if isinstance(args.get("entity_types"), list):
                    payload["entity_types"] = args["entity_types"]
                limit = args.get("limit", 15)
                if isinstance(limit, int) and limit > 0:
                    payload["limit"] = min(limit, 50)
                ok, data = call_wisdom_tool("search_knowledge_graph", payload)
                if ok:
                    result = data
                else:
                    succeeded = False
                    result = {"error": str(data)}

        elif name == "execute_cypher_query":
            cypher = args.get("cypher_query", "")
            if not isinstance(cypher, str) or not cypher.strip():
                succeeded = False
                result = {"error": "cypher_query is required"}
            else:
                payload = {"cypher_query": cypher}
                if isinstance(args.get("description"), str) and args["description"].strip():
                    payload["description"] = args["description"]
                ok, data = call_wisdom_tool("execute_cypher_query", payload)
                if ok:
                    result = data
                else:
                    succeeded = False
                    result = {"error": str(data)}

        elif name == "record_finding":
            if len(self.findings) >= self.config.max_findings:
                succeeded = False
                result = {
                    "error": "findings budget exhausted",
                    "max_findings": self.config.max_findings,
                }
            else:
                missing = [
                    k for k in ("entity_id", "title", "summary", "recurrence_evidence")
                    if not isinstance(args.get(k), str) or not args[k].strip()
                ]
                if missing:
                    succeeded = False
                    result = {"error": f"missing/invalid required fields: {missing}"}
                else:
                    self.findings.append(args)
                    result = {
                        "ok": True,
                        "total_findings": len(self.findings),
                        "remaining_budget": self.config.max_findings - len(self.findings),
                    }

        elif name == "finish":
            reason = args.get("reason", "")
            self.finish_reason = reason if isinstance(reason, str) else str(reason)
            result = {"ok": True}

        else:
            succeeded = False
            result = {"error": f"unknown tool: {name}"}

        self.trace.append(
            ScoutTraceEntry(
                tool=name,
                arguments=args,
                result_summary=_short(result),
                succeeded=succeeded,
            )
        )
        return succeeded, result


# --- IngestionRecord conversion --------------------------------------------


def _finding_to_record(finding: dict[str, Any], index: int) -> IngestionRecord:
    entity_id_raw = str(finding.get("entity_id") or f"scout-{index + 1:04d}")
    record_id = f"WISDOM-{_sanitize_id(entity_id_raw)}"

    title = finding.get("title") or "Knowledge graph finding"
    summary = finding.get("summary") or ""
    recurrence = finding.get("recurrence_evidence") or ""
    entity_type = finding.get("entity_type")
    upstream = finding.get("upstream_sources")
    if isinstance(upstream, list):
        sources_clean = [s.strip().lower() for s in upstream if isinstance(s, str) and s.strip()]
    else:
        sources_clean = []

    lines = [f"Title: {title}"]
    if summary:
        lines.append(f"Summary: {summary}")
    if recurrence:
        lines.append(f"Recurrence evidence: {recurrence}")
    if isinstance(entity_type, str) and entity_type.strip():
        lines.append(f"Entity type: {entity_type.strip()}")
    if sources_clean:
        lines.append(f"Upstream sources: {', '.join(sources_clean)}")
    raw_text = "\n".join(lines)

    metadata: dict[str, Any] = {
        "wisdom_source": "scout",
        "scout_entity_id": entity_id_raw,
        "recurrence_evidence": recurrence,
    }
    if isinstance(entity_type, str) and entity_type.strip():
        metadata["entity_type"] = entity_type.strip()
    if sources_clean:
        metadata["upstream_sources"] = sources_clean
        if len(sources_clean) == 1:
            metadata["upstream_source"] = sources_clean[0]

    return IngestionRecord(
        record_id=record_id,
        source=SourceType.WISDOM,
        source_record_id=entity_id_raw,
        raw_text=raw_text,
        metadata=metadata,
        ingested_at=datetime.now(),
    )


# --- Mock path --------------------------------------------------------------


def _run_mock(config: ScoutConfig) -> ScoutResult:
    """Mock mode bypasses the LLM loop and converts the static fixture file.

    This is NOT a test of the tool-use loop — it's a smoke path for running the
    downstream pipeline without API credentials. Live mode is where the agent
    behavior is exercised.
    """
    path = settings.mock_data_dir / "wisdom_search_hits.json"
    trace: list[ScoutTraceEntry] = []
    records: list[IngestionRecord] = []
    if not path.exists():
        return ScoutResult(
            records=records,
            trace=trace,
            finish_reason="mock fixture missing",
            stop_cause="error",
        )

    try:
        hits = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        return ScoutResult(
            records=records,
            trace=trace,
            finish_reason=f"mock fixture parse error: {e}",
            stop_cause="error",
        )

    if not isinstance(hits, list):
        return ScoutResult(
            records=records, trace=trace, finish_reason="mock fixture is not a list", stop_cause="error"
        )

    for i, hit in enumerate(hits[: config.max_findings]):
        if not isinstance(hit, dict):
            continue
        synthetic = {
            "entity_id": str(hit.get("id") or f"mock-{i + 1}"),
            "title": hit.get("title") or hit.get("name") or "Mock finding",
            "summary": hit.get("summary") or hit.get("description") or "",
            "recurrence_evidence": "mock fixture (no real recurrence data)",
            "upstream_sources": hit.get("sources") or [],
            "entity_type": hit.get("entity_type") or hit.get("type"),
        }
        records.append(_finding_to_record(synthetic, i))
        trace.append(
            ScoutTraceEntry(
                tool="record_finding",
                arguments=synthetic,
                result_summary=f"mock record {i + 1}",
                succeeded=True,
            )
        )

    return ScoutResult(
        records=records,
        trace=trace,
        finish_reason="mock run complete",
        tool_calls_used=len(trace),
        findings_emitted=len(records),
        stop_cause="finish",
    )


# --- Live tool-use loop -----------------------------------------------------


def _run_live(config: ScoutConfig) -> ScoutResult:
    if not wisdom_configured():
        raise RuntimeError(
            "Scout live mode requires WISDOM_API_BASE_URL and WISDOM_API_TOKEN to be set."
        )

    client = Anthropic()
    session = _ScoutSession(config)
    system_prompt = _build_system_prompt(config)

    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                "Begin exploration. Orient with get_organization_details and get_schema, then "
                "run searches and Cypher queries to surface entities that show concrete "
                "recurrence evidence. Record findings and call finish when done."
            ),
        }
    ]

    stop_cause = "model_end_turn"

    while True:
        if session.tool_calls_used >= config.max_tool_calls:
            stop_cause = "tool_budget"
            break
        if session.finish_reason is not None:
            stop_cause = "finish"
            break

        try:
            # Cache the system prompt + tool definitions. They don't change
            # across turns, so after the first call Anthropic reads them from
            # cache and they stop counting toward input-token rate limits.
            response = client.messages.create(
                model=config.model,
                max_tokens=4096,
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=_TOOLS,  # type: ignore[arg-type]
                messages=messages,  # type: ignore[arg-type]
            )
        except Exception as e:
            logger.exception("Scout: Anthropic API call failed")
            session.trace.append(
                ScoutTraceEntry(
                    tool="<anthropic>",
                    arguments={},
                    result_summary=f"API error: {e}",
                    succeeded=False,
                )
            )
            stop_cause = "error"
            break

        assistant_blocks = list(response.content)
        messages.append({"role": "assistant", "content": assistant_blocks})

        tool_use_blocks = [b for b in assistant_blocks if getattr(b, "type", None) == "tool_use"]
        if not tool_use_blocks:
            # Model emitted only text / ended its turn without a tool call.
            stop_cause = "model_end_turn"
            break

        tool_results: list[dict[str, Any]] = []
        for block in tool_use_blocks:
            name = block.name
            args = block.input if isinstance(block.input, dict) else {}

            if name not in _TOOL_NAMES:
                session.trace.append(
                    ScoutTraceEntry(
                        tool=name,
                        arguments=args,
                        result_summary="unknown tool",
                        succeeded=False,
                    )
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps({"error": f"unknown tool {name}"}),
                        "is_error": True,
                    }
                )
                continue

            succeeded, result = session.dispatch(name, args)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=str),
                    "is_error": not succeeded,
                }
            )

            if session.tool_calls_used >= config.max_tool_calls:
                break
            if session.finish_reason is not None:
                break

        messages.append({"role": "user", "content": tool_results})

    records = [_finding_to_record(f, i) for i, f in enumerate(session.findings)]
    return ScoutResult(
        records=records,
        trace=session.trace,
        finish_reason=session.finish_reason,
        tool_calls_used=session.tool_calls_used,
        findings_emitted=len(session.findings),
        stop_cause=stop_cause,
    )


def run_scout(config: ScoutConfig | None = None) -> ScoutResult:
    """Run a Scout session.

    Mock mode is a smoke path that bypasses the LLM loop — it reads the static
    Wisdom fixture and produces IngestionRecords deterministically. Use live
    mode to actually exercise the agent.
    """
    cfg = config or ScoutConfig()
    if cfg.mock:
        return _run_mock(cfg)
    return _run_live(cfg)
