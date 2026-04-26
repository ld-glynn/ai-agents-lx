"""Enterpret Wisdom (Knowledge Graph) as a PSM ingestion source.

Mock mode reads ``data/mock/wisdom_search_hits.json``. Live mode calls the Wisdom MCP
``search_knowledge_graph`` tool (same HTTP+SSE gateway as Cursor / the enterpret app).

Environment (live):
  WISDOM_API_BASE_URL — full MCP URL (e.g. https://.../server/mcp)
  WISDOM_API_TOKEN — Bearer token
  WISDOM_SEARCH_QUERY — natural language query. May be a single string,
      a newline-separated list, or a JSON array for multi-query sweeps.
  WISDOM_SEARCH_LIMIT — max hits per query (default: 15)
  WISDOM_ENTITY_TYPES — optional comma-separated list passed to the tool
  WISDOM_CYPHER_QUERY — optional Cypher query. When set, it replaces the
      search_knowledge_graph flow and calls execute_cypher_query instead.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any

from psm.config import settings
from psm.integrations.base import IntegrationAdapter
from psm.integrations.wisdom_client import call_wisdom_tool, wisdom_configured
from psm.schemas.ingestion import IngestionRecord, SourceType

logger = logging.getLogger(__name__)

_DEFAULT_QUERY = (
    "Operational and product problems, customer-impacting issues, "
    "and internal friction themes from feedback and support intelligence"
)

# Keys Enterpret/Wisdom commonly uses to identify the upstream ingestion source
# for a hit (Slack/Gong/Salesforce/Zendesk/etc.). Checked in order.
_UPSTREAM_SOURCE_KEYS = (
    "source",
    "sources",
    "source_type",
    "source_types",
    "feedback_source",
    "feedback_sources",
    "integration",
    "integrations",
    "channel",
    "channels",
    "origin",
    "origins",
    "provider",
)

_URL_KEYS = ("url", "permalink", "link", "source_url", "href")

_EXPERIMENTATION_KEYWORDS = (
    "experiment", "a/b", "a/a", "metric", "holdout", "rollout",
    "variation", "hypothesis", "funnel", "conversion", "baseline",
    "statistical", "sample size", "exposure", "bandit", "guarded",
    "percentage rollout", "traffic allocation", "feature flag",
    "feature change", "winner", "warehouse native",
)

# Signals that a record describes a problem, not praise/demo/neutral inquiry
_PROBLEM_SIGNALS = (
    "issue", "problem", "error", "fail", "broken", "confus",
    "struggle", "unable", "doesn't work", "can't ", "cannot ",
    "bug", "frustrat", "block", "missing", "incorrect", "wrong",
    "difficult", "challenge", "complain", "concern", "limitation",
    "not working", "not support", "unexpected", "inconsistent",
    "unclear", "no way to", "doesn't support", "pain", "gap",
    "workaround", "regression", "broke", "crash", "timeout",
    "slow", "unreliable", "inaccurate", "asking about", "inquired",
    "help with", "trouble", "stuck", "confused about",
)


def _filter_experimentation_themes(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only themes whose name matches experimentation-related keywords."""
    filtered = []
    for hit in hits:
        name = (hit.get("title") or hit.get("name") or "").lower()
        if any(kw in name for kw in _EXPERIMENTATION_KEYWORDS):
            filtered.append(hit)
    return filtered


def _env_queries(override: str | list[str] | None) -> list[str]:
    """Resolve the list of queries to run. Falls back to env var, then default.

    Accepts a single string or a list from the caller. The env var may be a
    single line, newline-separated list, or JSON array.
    """
    if isinstance(override, list):
        qs = [q.strip() for q in override if isinstance(q, str) and q.strip()]
        if qs:
            return qs
    elif isinstance(override, str) and override.strip():
        return [override.strip()]

    raw = os.environ.get("WISDOM_SEARCH_QUERY", "").strip()
    if raw:
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    qs = [str(q).strip() for q in parsed if str(q).strip()]
                    if qs:
                        return qs
            except json.JSONDecodeError:
                pass
        if "\n" in raw:
            qs = [line.strip() for line in raw.splitlines() if line.strip()]
            if qs:
                return qs
        return [raw]
    return [_DEFAULT_QUERY]


def _env_limit(override: int | None) -> int:
    if override is not None and override > 0:
        return min(override, 100)
    raw = os.environ.get("WISDOM_SEARCH_LIMIT", "").strip()
    if raw.isdigit():
        return min(int(raw), 100)
    return 15


def _env_cypher(override: str | None) -> str | None:
    if isinstance(override, str) and override.strip():
        return override.strip()
    raw = os.environ.get("WISDOM_CYPHER_QUERY", "").strip()
    return raw or None


def _env_entity_types() -> list[str] | None:
    raw = os.environ.get("WISDOM_ENTITY_TYPES", "").strip()
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts or None


def _flatten_search_payload(data: Any) -> list[dict[str, Any]]:
    """Normalize Wisdom search structured payload into a list of dict-like hits."""
    if data is None:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("results", "entities", "hits", "items", "data", "records", "rows"):
        v = data.get(key)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
    if any(k in data for k in ("id", "title", "name", "summary", "text", "description")):
        return [data]
    return []


def _extract_upstream_sources(hit: dict[str, Any]) -> list[str]:
    """Pull upstream source names (slack, gong, salesforce, ...) from a hit.

    Wisdom payloads vary — sources may appear as a scalar string, a list of
    strings, or a list of objects with name/type/source keys. We collect from
    any of the known keys, lowercase, and de-duplicate while preserving order.
    """
    found: list[str] = []
    for key in _UPSTREAM_SOURCE_KEYS:
        v = hit.get(key)
        if isinstance(v, str) and v.strip():
            found.append(v.strip().lower())
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, str) and item.strip():
                    found.append(item.strip().lower())
                elif isinstance(item, dict):
                    name = item.get("name") or item.get("type") or item.get("source")
                    if isinstance(name, str) and name.strip():
                        found.append(name.strip().lower())
    seen: set[str] = set()
    ordered: list[str] = []
    for s in found:
        if s not in seen:
            seen.add(s)
            ordered.append(s)
    return ordered


def _extract_url(hit: dict[str, Any]) -> str | None:
    for key in _URL_KEYS:
        v = hit.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _extract_source_counts(hit: dict[str, Any]) -> dict[str, int] | None:
    """Aggregate mention counts per upstream source, if the hit exposes them."""
    for key in ("source_counts", "mention_counts", "counts_by_source"):
        v = hit.get(key)
        if isinstance(v, dict):
            cleaned: dict[str, int] = {}
            for k, n in v.items():
                if isinstance(k, str) and isinstance(n, (int, float)):
                    cleaned[k.lower()] = int(n)
            if cleaned:
                return cleaned
    return None


def _hit_to_raw_text(hit: dict[str, Any]) -> str:
    title = hit.get("title") or hit.get("name") or hit.get("label") or "Knowledge graph entity"
    parts = [f"Title: {title}"]
    for key in ("summary", "description", "text", "snippet", "content"):
        val = hit.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(f"{key.replace('_', ' ').title()}: {val.strip()}")
            break
    et = hit.get("entity_type") or hit.get("type") or hit.get("kind")
    if isinstance(et, str) and et.strip():
        parts.append(f"Entity type: {et.strip()}")
    score = hit.get("relevance_score") or hit.get("score")
    if score is not None:
        parts.append(f"Relevance: {score}")

    srcs = _extract_upstream_sources(hit)
    if srcs:
        parts.append(f"Upstream sources: {', '.join(srcs)}")
    counts = _extract_source_counts(hit)
    if counts:
        rendered = ", ".join(f"{k}={v}" for k, v in counts.items())
        parts.append(f"Mentions by source: {rendered}")
    url = _extract_url(hit)
    if url:
        parts.append(f"URL: {url}")
    return "\n".join(parts)


def _hit_id(hit: dict[str, Any], index: int) -> str:
    for key in ("id", "entity_id", "uuid", "key"):
        v = hit.get(key)
        if isinstance(v, str) and v.strip():
            safe = re.sub(r"[^\w.-]+", "-", v.strip())[:80]
            return f"WISDOM-{safe}"
    return f"WISDOM-{index + 1:04d}"


def _build_metadata(hit: dict[str, Any], wisdom_source: str) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "entity_type": hit.get("entity_type") or hit.get("type"),
        "wisdom_source": wisdom_source,
        "relevance_score": hit.get("relevance_score") or hit.get("score"),
    }
    srcs = _extract_upstream_sources(hit)
    if srcs:
        meta["upstream_sources"] = srcs
        if len(srcs) == 1:
            meta["upstream_source"] = srcs[0]
    counts = _extract_source_counts(hit)
    if counts:
        meta["source_counts"] = counts
    url = _extract_url(hit)
    if url:
        meta["url"] = url
    if wisdom_source == "live":
        meta["kg_payload_keys"] = list(hit.keys())[:20]
    # Pass through enrichment fields from _enrich_themes
    for key in ("summary", "synthesis", "agent_idea", "feedback_items", "feedback_sample_count"):
        val = hit.get(key)
        if val is not None:
            meta[key] = val
    return meta


class WisdomAdapter(IntegrationAdapter):
    source_type = SourceType.WISDOM

    def __init__(
        self,
        mock: bool = True,
        *,
        search_query: str | list[str] | None = None,
        search_limit: int | None = None,
        cypher_query: str | None = None,
        days: int | None = None,
        record_limit: int | None = None,
    ):
        super().__init__(mock=mock)
        self._search_query = search_query
        self._search_limit = search_limit
        self._cypher_query = cypher_query
        self._days = days
        self._record_limit = record_limit

    def test_connection(self) -> bool:
        if self.mock:
            return True
        if not wisdom_configured():
            return False
        ok, _ = call_wisdom_tool("get_organization_details", {})
        return ok

    def fetch_records(self) -> list[IngestionRecord]:
        if self.mock:
            return self._fetch_mock()
        cypher = _env_cypher(self._cypher_query)
        if cypher:
            return self._fetch_live_cypher(cypher)
        # Default: record-first approach — pull FeedbackInsights directly
        return self._fetch_live_records()

    def _fetch_mock(self) -> list[IngestionRecord]:
        path = settings.mock_data_dir / "wisdom_search_hits.json"
        if not path.exists():
            return []
        hits = json.loads(path.read_text())
        if not isinstance(hits, list):
            return []
        records: list[IngestionRecord] = []
        for i, hit in enumerate(hits):
            if not isinstance(hit, dict):
                continue
            rid = _hit_id(hit, i)
            records.append(
                IngestionRecord(
                    record_id=rid,
                    source=SourceType.WISDOM,
                    source_record_id=str(hit.get("id", rid)),
                    raw_text=_hit_to_raw_text(hit),
                    metadata=_build_metadata(hit, "mock"),
                    ingested_at=datetime.now(),
                )
            )
        return records

    def _fetch_live(self) -> list[IngestionRecord]:
        if not wisdom_configured():
            return []

        queries = _env_queries(self._search_query)
        limit = _env_limit(self._search_limit)
        entity_types = _env_entity_types()

        # record_id -> (IngestionRecord, list of matched queries)
        by_id: dict[str, tuple[IngestionRecord, list[str]]] = {}
        empty_payloads: list[tuple[str, dict[str, Any]]] = []

        for query in queries:
            args: dict[str, Any] = {"query": query, "limit": limit}
            if entity_types is not None:
                args["entity_types"] = entity_types

            ok, data = call_wisdom_tool("search_knowledge_graph", args)
            if not ok:
                raise RuntimeError(f"Wisdom search failed for query {query!r}: {data}")

            hits = _flatten_search_payload(data)
            if not hits and isinstance(data, dict):
                empty_payloads.append((query, data))
                continue

            for i, hit in enumerate(hits):
                rid = _hit_id(hit, i)
                existing = by_id.get(rid)
                if existing is not None:
                    existing[1].append(query)
                    continue
                meta = _build_metadata(hit, "live")
                meta["matched_queries"] = [query]
                record = IngestionRecord(
                    record_id=rid,
                    source=SourceType.WISDOM,
                    source_record_id=str(hit.get("id") or hit.get("entity_id") or rid),
                    raw_text=_hit_to_raw_text(hit),
                    metadata=meta,
                    ingested_at=datetime.now(),
                )
                by_id[rid] = (record, meta["matched_queries"])

        records = [rec for rec, _ in by_id.values()]

        # If every query produced an empty flattened payload, fall back to a
        # single blob record so the Structurer still has something to work with.
        if not records and empty_payloads:
            query, data = empty_payloads[0]
            logger.warning(
                "Wisdom search returned unparseable payload for %d queries %s; "
                "falling back to full_payload blob. Top-level keys: %s",
                len(empty_payloads),
                [q for q, _ in empty_payloads],
                list(data.keys())[:20] if isinstance(data, dict) else type(data).__name__,
            )
            records.append(
                IngestionRecord(
                    record_id="WISDOM-SEARCH-BLOB",
                    source=SourceType.WISDOM,
                    source_record_id=None,
                    raw_text=json.dumps(data, indent=2, default=str)[:12000],
                    metadata={
                        "wisdom_source": "live",
                        "fallback": "full_payload",
                        "matched_queries": [q for q, _ in empty_payloads],
                    },
                    ingested_at=datetime.now(),
                )
            )
        return records

    def _fetch_live_records(self) -> list[IngestionRecord]:
        """Record-first approach: query FeedbackInsights directly with source links.

        Pulls individual customer feedback records (Gong calls, Zendesk tickets, etc.)
        time-constrained and keyword-filtered. Each record has a direct link back to
        its source via origin_id.
        """
        if not wisdom_configured():
            return []

        days = self._days or 14
        limit = self._record_limit or 500

        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")

        cypher = (
            f"MATCH (n:NaturalLanguageInteraction) "
            f"WHERE n.record_timestamp > '{cutoff}' "
            f"MATCH (i:FeedbackInsight) "
            f"WHERE i.feedback_record_id = n.record_id "
            f"AND i.summary_type = 'BASIC' AND i.content <> '' "
            f"RETURN i.content AS content, i.record_id AS id, "
            f"n.source AS source, n.origin_record_id AS origin_id, "
            f"n.record_timestamp AS timestamp "
            f"LIMIT {limit}"
        )

        logger.info("Fetching records: last %d days, limit %d", days, limit)
        ok, data = call_wisdom_tool(
            "execute_cypher_query",
            {"cypher_query": cypher, "description": f"psm record-first fetch (last {days}d)"},
        )
        if not ok:
            raise RuntimeError(f"Wisdom record fetch failed: {data}")

        hits = _flatten_search_payload(data)
        logger.info("Fetched %d raw records, filtering for experimentation relevance...", len(hits))

        # Two-pass filter: must be experimentation-related AND indicate a problem
        exp_filtered = []
        for hit in hits:
            content = (hit.get("content") or "").lower()
            if any(kw in content for kw in _EXPERIMENTATION_KEYWORDS):
                exp_filtered.append(hit)

        filtered = []
        for hit in exp_filtered:
            content = (hit.get("content") or "").lower()
            if any(signal in content for signal in _PROBLEM_SIGNALS):
                filtered.append(hit)

        logger.info(
            "Filtered: %d raw → %d experimentation-related → %d with problem signals",
            len(hits), len(exp_filtered), len(filtered),
        )

        # Convert to IngestionRecords with full source provenance
        records: list[IngestionRecord] = []
        for i, hit in enumerate(filtered):
            rid = f"WISDOM-{hit.get('id', f'{i:04d}')}"
            source_name = hit.get("source", "unknown")
            origin_id = hit.get("origin_id", "")
            content = hit.get("content", "")
            timestamp = hit.get("timestamp", "")

            # Build source URL from origin_id
            source_url = None
            if origin_id:
                if source_name == "ZendeskSupport":
                    source_url = f"https://launchdarkly.zendesk.com/agent/tickets/{origin_id}"
                elif source_name == "Gong":
                    source_url = f"https://app.gong.io/call?id={origin_id}"

            meta: dict[str, Any] = {
                "wisdom_source": "live",
                "source_name": source_name,
                "origin_id": origin_id,
                "source_url": source_url,
                "record_timestamp": timestamp,
                "upstream_sources": [source_name] if source_name else [],
                # Store the content as a single feedback item for downstream rendering
                "feedback_items": [{
                    "source": source_name,
                    "text": content,
                    "origin_id": origin_id,
                }],
                "feedback_sample_count": 1,
            }

            raw_text = f"[{source_name}] {content}"

            records.append(
                IngestionRecord(
                    record_id=rid,
                    source=SourceType.WISDOM,
                    source_record_id=origin_id or None,
                    raw_text=raw_text,
                    metadata=meta,
                    ingested_at=datetime.now(),
                )
            )

        return records

    def _fetch_live_cypher(self, cypher: str) -> list[IngestionRecord]:
        """Run a Cypher query against the Wisdom knowledge graph.

        Cypher bypasses ``search_knowledge_graph`` entirely — you get back whatever
        the graph returns for the query shape. The adapter flattens results using
        the same envelope heuristics as search; any node-like rows become hits.

        If the results look like Theme rows (have a ``title`` or ``name`` and a
        ``category`` field), each theme is automatically enriched with sample
        feedback and upstream source information via a second query.
        """
        if not wisdom_configured():
            return []

        ok, data = call_wisdom_tool(
            "execute_cypher_query",
            {"cypher_query": cypher, "description": "psm WisdomAdapter cypher fetch"},
        )
        if not ok:
            raise RuntimeError(f"Wisdom cypher failed: {data}")

        hits = _flatten_search_payload(data)

        # Detect if these are Theme rows — filter for relevance, then enrich
        if hits and all(h.get("category") and (h.get("title") or h.get("name")) for h in hits):
            hits = _filter_experimentation_themes(hits)
            logger.info("Filtered to %d experimentation-relevant themes", len(hits))
            hits = self._enrich_themes(hits)

        records: list[IngestionRecord] = []
        for i, hit in enumerate(hits):
            rid = _hit_id(hit, i)
            meta = _build_metadata(hit, "live")
            meta["cypher_query"] = cypher
            records.append(
                IngestionRecord(
                    record_id=rid,
                    source=SourceType.WISDOM,
                    source_record_id=str(hit.get("id") or hit.get("entity_id") or rid),
                    raw_text=_hit_to_raw_text(hit),
                    metadata=meta,
                    ingested_at=datetime.now(),
                )
            )

        if not records and isinstance(data, dict):
            logger.warning(
                "Wisdom cypher returned unparseable payload; falling back to "
                "full_payload blob. Top-level keys: %s",
                list(data.keys())[:20],
            )
            records.append(
                IngestionRecord(
                    record_id="WISDOM-CYPHER-BLOB",
                    source=SourceType.WISDOM,
                    source_record_id=None,
                    raw_text=json.dumps(data, indent=2, default=str)[:12000],
                    metadata={
                        "wisdom_source": "live",
                        "fallback": "full_payload",
                        "cypher_query": cypher,
                    },
                    ingested_at=datetime.now(),
                )
            )
        return records

    def _enrich_themes(self, themes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """For each theme, fetch sample feedback with upstream sources.

        Queries the Wisdom graph to join Theme → CustomerFeedbackTags →
        FeedbackInsight → NaturalLanguageInteraction, returning a few
        representative feedback excerpts and their sources per theme.
        """
        theme_ids = [h.get("id") or h.get("theme_id") or h.get("record_id") for h in themes]
        theme_ids = [tid for tid in theme_ids if tid]
        if not theme_ids:
            return themes

        # Build a single query that fetches feedback for all themes at once
        id_list = ", ".join(f"'{tid}'" for tid in theme_ids)
        enrich_cypher = (
            f"MATCH (tag:CustomerFeedbackTags) WHERE tag.theme_id IN [{id_list}] "
            f"MATCH (i:FeedbackInsight) WHERE i.feedback_record_id = tag.feedback_id "
            f"AND i.summary_type = 'BASIC' AND i.content <> '' "
            f"MATCH (n:NaturalLanguageInteraction) WHERE n.record_id = i.feedback_record_id "
            f"RETURN tag.theme_id AS theme_id, i.content AS feedback, n.source AS source, "
            f"n.origin_record_id AS origin_id, n.record_id AS interaction_id "
            f"LIMIT 1000"
        )

        ok, data = call_wisdom_tool(
            "execute_cypher_query",
            {"cypher_query": enrich_cypher, "description": "psm enrich themes with feedback"},
        )

        if not ok:
            logger.warning("Theme enrichment query failed: %s", data)
            return themes

        rows = _flatten_search_payload(data)

        # Group all feedback by theme_id, deduplicate by origin_id
        feedback_by_theme: dict[str, list[dict[str, Any]]] = {}
        seen_by_theme: dict[str, set[str]] = {}
        for row in rows:
            tid = row.get("theme_id")
            if not tid:
                continue
            origin = row.get("origin_id", "")
            if origin:
                seen = seen_by_theme.setdefault(tid, set())
                if origin in seen:
                    continue
                seen.add(origin)
            samples = feedback_by_theme.setdefault(tid, [])
            samples.append({
                    "feedback": row.get("feedback", ""),
                    "origin_id": row.get("origin_id", ""),
                    "source": row.get("source", ""),
                })

        # Merge enrichment back into themes
        enriched = []
        for theme in themes:
            tid = theme.get("id") or theme.get("theme_id") or theme.get("record_id")
            samples = feedback_by_theme.get(tid, [])
            theme = dict(theme)  # copy
            if samples:
                source_set = sorted({s["source"] for s in samples if s["source"]})
                theme["upstream_sources"] = source_set
                theme["source_counts"] = {s: sum(1 for x in samples if x["source"] == s) for s in source_set}
                # Store full feedback items for the dashboard to render
                theme["feedback_items"] = [
                    {
                        "source": s["source"],
                        "text": s["feedback"],
                        "origin_id": s.get("origin_id", ""),
                    }
                    for s in samples
                ]
                # Also build a text description for the structurer
                sample_texts = []
                for s in samples:
                    label = f"[{s['source']}]" if s["source"] else ""
                    sample_texts.append(f"{label} {s['feedback']}")
                theme["description"] = "\n\n".join(sample_texts)
                theme["feedback_sample_count"] = len(samples)
            enriched.append(theme)

        return _generate_theme_summaries(enriched)


def _generate_theme_summaries(themes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Use Claude to summarize themes and classify each by agent opportunity."""
    themes_with_feedback = [t for t in themes if t.get("feedback_items")]
    if not themes_with_feedback:
        return themes

    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic SDK not available; skipping theme summaries")
        return themes

    # Build the prompt — include up to 5 samples per theme
    theme_blocks = []
    for t in themes_with_feedback:
        name = t.get("title") or t.get("name", "Unknown")
        category = t.get("category", "")
        items = t.get("feedback_items", [])
        sources = t.get("upstream_sources", [])
        count = t.get("feedback_sample_count", 0)

        samples = items[:5]
        sample_lines = "\n".join(
            f"  - [{s['source']}] {s['text'][:300]}" for s in samples
        )
        theme_blocks.append(
            f"Theme: {name}\n"
            f"Category: {category}\n"
            f"Data sources: {', '.join(sources)} ({count} total mentions)\n"
            f"Sample feedback:\n{sample_lines}"
        )

    prompt = (
        "You are analyzing feedback themes for a Product Specialist in Experimentation at LaunchDarkly. "
        "Their role involves pre/post-sales expertise, identifying product gaps, creating enablement content, "
        "and finding opportunities where experimentation support could drive customer success and expansion.\n\n"
        "For each theme below, generate:\n"
        "1. summary: One-line plain-English description of the problem or opportunity (max 120 chars)\n"
        "2. synthesis: 2-3 sentences on why this matters — what's the impact and who's affected\n"
        "3. agent_idea: A specific, actionable AI agent that could be built to address this. "
        "Think beyond bug fixes — consider agents that monitor for patterns, generate reports, "
        "flag expansion opportunities, create enablement content, alert teams, or automate workflows. "
        "Be creative and specific about what the agent would DO and who it would help.\n\n"
        "Return ONLY a JSON array. Each element:\n"
        '{"theme": "exact theme name", "summary": "...", "synthesis": "...", "agent_idea": "..."}\n\n'
        + "\n\n---\n\n".join(theme_blocks)
    )

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        summaries_list = json.loads(text.strip())
        summaries_by_name: dict[str, dict[str, str]] = {}
        for item in summaries_list:
            key = item.get("theme", "").strip()
            if key:
                summaries_by_name[key] = item

        for theme in themes:
            name = (theme.get("title") or theme.get("name", "")).strip()
            match = summaries_by_name.get(name)
            if match:
                theme["summary"] = match.get("summary", "")
                theme["synthesis"] = match.get("synthesis", "")
                theme["agent_idea"] = match.get("agent_idea", "")

    except Exception as e:
        logger.warning("Theme summary generation failed: %s", e)

    # Second pass: extract relevant excerpts from each feedback item
    try:
        _extract_relevant_excerpts(themes, client)
    except Exception as e:
        logger.warning("Feedback excerpt extraction failed: %s", e)

    return themes


def _extract_relevant_excerpts(
    themes: list[dict[str, Any]],
    client: Any,
) -> None:
    """For each theme, ask the LLM to extract the relevant excerpt from each feedback item.

    Modifies feedback_items in place, adding a ``relevant_excerpt`` field.
    Batches all themes into a single call to minimize latency.
    """
    # Build a compact payload: for each theme, number the feedback items
    theme_payloads = []
    theme_item_counts = []
    for theme in themes:
        items = theme.get("feedback_items", [])
        if not items:
            theme_item_counts.append(0)
            continue
        name = theme.get("title") or theme.get("name", "Unknown")
        numbered = []
        for i, item in enumerate(items):
            # Truncate to keep prompt reasonable
            text = item["text"][:400]
            numbered.append(f"  [{i}] [{item.get('source', '')}] {text}")
        theme_payloads.append(
            f"Problem: {name}\nFeedback items:\n" + "\n".join(numbered)
        )
        theme_item_counts.append(len(items))

    if not theme_payloads:
        return

    prompt = (
        "For each problem below, extract a 1-2 sentence excerpt from each feedback item "
        "that specifically explains why this feedback is evidence of the problem. "
        "Focus on the concrete issue, impact, or user pain — not general context about the call/ticket.\n\n"
        "Return ONLY a JSON array of arrays. Outer array = problems (same order). "
        "Inner array = excerpts (same order as feedback items). Each excerpt is a string.\n\n"
        + "\n\n".join(theme_payloads)
    )

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]

    excerpts_by_theme = json.loads(text.strip())
    if not isinstance(excerpts_by_theme, list):
        return

    # Map back to themes — only themes with feedback items got sent
    excerpt_idx = 0
    for theme in themes:
        items = theme.get("feedback_items", [])
        if not items:
            continue
        if excerpt_idx >= len(excerpts_by_theme):
            break
        theme_excerpts = excerpts_by_theme[excerpt_idx]
        excerpt_idx += 1
        if not isinstance(theme_excerpts, list):
            continue
        for i, item in enumerate(items):
            if i < len(theme_excerpts) and isinstance(theme_excerpts[i], str):
                item["relevant_excerpt"] = theme_excerpts[i]
