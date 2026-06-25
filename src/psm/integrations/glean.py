"""Glean enterprise search as a PSM ingestion source.

The Glean replacement for the Enterpret/Wisdom adapter. Where Wisdom queried a
knowledge graph (semantic search + Cypher) and returned pre-aggregated Themes,
Glean is federated enterprise search: it returns individual documents (Zendesk
tickets, Gong calls, Confluence pages, Slack messages, …) across LaunchDarkly's
indexed apps. Recurrence/clustering that Enterpret pre-baked is intentionally
left to the downstream Cataloger → Pattern Analyzer stages.

Mock mode reads ``data/mock/glean_search_hits.json`` — a fixture in the same
shape as the live REST ``/search`` response, so a single parser serves both.
Live mode calls the Glean Client API via glean_client.

Environment (live): see glean_client. GLEAN_INSTANCE + GLEAN_API_TOKEN.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from psm.config import settings
from psm.integrations.base import IntegrationAdapter
from psm.integrations.glean_client import glean_configured, glean_search
from psm.schemas.ingestion import IngestionRecord, SourceType

logger = logging.getLogger(__name__)

# Relevance heuristics applied to raw text — transport-agnostic. These keep the
# ingestion focused on experimentation-related problems (vs. praise/demos/neutral
# inquiries). Tuned originally for the Enterpret adapter; retained here.
_EXPERIMENTATION_KEYWORDS = (
    "experiment", "a/b", "a/a", "metric", "holdout", "rollout",
    "variation", "hypothesis", "funnel", "conversion", "baseline",
    "statistical", "sample size", "exposure", "bandit", "guarded",
    "percentage rollout", "traffic allocation", "feature flag",
    "feature change", "winner", "warehouse native",
)

# Signals that a record describes a problem, not praise/demo/neutral inquiry.
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

_DEFAULT_QUERY = "experiment metrics feature flag problem"
# Default datasources to scan — the customer-signal apps Enterpret used to
# aggregate. None = search across everything Glean indexes.
_DEFAULT_DATASOURCES = ["zendesk", "gong", "slack", "salescloud", "jira"]

# First-party support/feedback channels: content here is inherently a customer
# problem, inquiry, or escalation, so it bypasses the problem-signal keyword
# gate (which is brittle and was dropping genuine tickets whose phrasing — e.g.
# "0 exposures" — contained no hardcoded signal word). Reference sources (docs
# site, blog, marketing) still require a problem signal to filter out how-tos.
_FEEDBACK_DATASOURCES = {
    "zendesk", "gong", "slack", "jira", "salescloud", "servicecloud",
    "intercom", "g2", "gainsight",
}


# --- Field extraction (maps the real Glean /search result shape) ------------


def _flatten_results(data: Any) -> list[dict[str, Any]]:
    """Pull the list of result objects out of a Glean /search response."""
    if isinstance(data, dict):
        results = data.get("results")
        if isinstance(results, list):
            return [r for r in results if isinstance(r, dict)]
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return []


def _doc(result: dict[str, Any]) -> dict[str, Any]:
    d = result.get("document")
    return d if isinstance(d, dict) else {}


def _field(result: dict[str, Any], key: str) -> Any:
    """Glean puts some fields on the result and some on result.document.
    Prefer the document, fall back to the result top-level."""
    doc = _doc(result)
    return doc.get(key) if doc.get(key) is not None else result.get(key)


def _snippet_text(result: dict[str, Any]) -> str:
    """Join the snippet fragments into one block of text."""
    snippets = result.get("snippets")
    parts: list[str] = []
    if isinstance(snippets, list):
        for s in snippets:
            if isinstance(s, dict):
                txt = s.get("text") or s.get("snippet")
                if isinstance(txt, str) and txt.strip():
                    parts.append(txt.strip())
            elif isinstance(s, str) and s.strip():
                parts.append(s.strip())
    return "\n".join(parts)


def _metadata_block(result: dict[str, Any]) -> dict[str, Any]:
    md = _doc(result).get("metadata")
    return md if isinstance(md, dict) else {}


def _author_name(result: dict[str, Any]) -> str | None:
    md = _metadata_block(result)
    for key in ("author", "owner", "ownedAndUpdatedBy"):
        v = md.get(key) if md else None
        if v is None:
            v = result.get(key)
        if isinstance(v, dict) and isinstance(v.get("name"), str):
            return v["name"]
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _timestamp(result: dict[str, Any]) -> str | None:
    md = _metadata_block(result)
    for key in ("updateTime", "createTime"):
        v = md.get(key) if md else None
        if v is None:
            v = result.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _datasource(result: dict[str, Any]) -> str:
    ds = _field(result, "datasource")
    if isinstance(ds, str) and ds.strip():
        return ds.strip().lower()
    md = _metadata_block(result)
    ds = md.get("datasource") if md else None
    return ds.strip().lower() if isinstance(ds, str) and ds.strip() else "glean"


def _facets(result: dict[str, Any]) -> dict[str, str]:
    """Lift a few high-value facets (account, opportunity, brand) out of
    matchingFilters for provenance — the Glean analog of Wisdom's source spread."""
    mf = result.get("matchingFilters")
    out: dict[str, str] = {}
    if isinstance(mf, dict):
        for key in ("account", "opportunity", "brand", "department", "space", "channel"):
            v = mf.get(key)
            if isinstance(v, list) and v:
                out[key] = ", ".join(str(x) for x in v[:3])
            elif isinstance(v, str) and v.strip():
                out[key] = v.strip()
    return out


def _result_id(result: dict[str, Any], index: int) -> str:
    raw = _field(result, "id") or _field(result, "url") or f"{index + 1:04d}"
    safe = re.sub(r"[^\w.-]+", "-", str(raw).strip())[:80]
    return f"GLEAN-{safe}"


def _result_to_raw_text(result: dict[str, Any]) -> str:
    title = _field(result, "title") or "Glean document"
    parts = [f"Title: {title}"]
    snippet = _snippet_text(result)
    if snippet:
        parts.append(f"Content: {snippet}")
    ds = _datasource(result)
    parts.append(f"Datasource: {ds}")
    facets = _facets(result)
    if facets.get("account"):
        parts.append(f"Account: {facets['account']}")
    url = _field(result, "url")
    if isinstance(url, str) and url.strip():
        parts.append(f"URL: {url.strip()}")
    return "\n".join(parts)


def _build_metadata(result: dict[str, Any], glean_source: str) -> dict[str, Any]:
    ds = _datasource(result)
    url = _field(result, "url")
    snippet = _snippet_text(result)
    meta: dict[str, Any] = {
        "glean_source": glean_source,
        "datasource": ds,
        # Mirror the Wisdom provenance contract the dashboard already understands.
        "upstream_sources": [ds],
        "upstream_source": ds,
        "source_name": ds,
    }
    if isinstance(url, str) and url.strip():
        meta["url"] = url.strip()
        meta["source_url"] = url.strip()
    ts = _timestamp(result)
    if ts:
        meta["record_timestamp"] = ts
    author = _author_name(result)
    if author:
        meta["author"] = author
    facets = _facets(result)
    if facets:
        meta["facets"] = facets
    # Single feedback item so the dashboard's SourceEvidence panel renders it,
    # matching the Wisdom record-first shape.
    if snippet:
        meta["feedback_items"] = [{
            "source": ds,
            "text": snippet,
            "origin_id": _field(result, "id") or "",
            "url": meta.get("url"),
        }]
        meta["feedback_sample_count"] = 1
    return meta


# --- Adapter ----------------------------------------------------------------


class GleanAdapter(IntegrationAdapter):
    source_type = SourceType.GLEAN

    def __init__(
        self,
        mock: bool = True,
        *,
        search_query: str | list[str] | None = None,
        search_limit: int | None = None,
        datasources: list[str] | None = None,
        experimentation_only: bool = True,
        problems_only: bool = True,
    ):
        super().__init__(mock=mock)
        self._search_query = search_query
        self._search_limit = search_limit
        self._datasources = datasources
        self._experimentation_only = experimentation_only
        self._problems_only = problems_only

    def test_connection(self) -> bool:
        if self.mock:
            return True
        if not glean_configured():
            return False
        ok, _ = glean_search("test", page_size=1)
        return ok

    # --- queries / limit resolution (parallels the Wisdom env helpers) ---

    def _queries(self) -> list[str]:
        q = self._search_query
        if isinstance(q, list):
            qs = [s.strip() for s in q if isinstance(s, str) and s.strip()]
            if qs:
                return qs
        elif isinstance(q, str) and q.strip():
            return [q.strip()]
        return [_DEFAULT_QUERY]

    def _limit(self) -> int:
        if self._search_limit and self._search_limit > 0:
            return min(self._search_limit, 100)
        return 15

    # --- fetch ---

    def fetch_records(self) -> list[IngestionRecord]:
        if self.mock:
            return self._fetch_mock()
        return self._fetch_live()

    def _filter_hits(self, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Relevance filter: keep experimentation-related hits, then keep the
        problem-shaped ones. Hits from first-party support/feedback channels
        (Zendesk, Gong, …) are inherently problems/inquiries and bypass the
        keyword problem-signal gate — that gate is only applied to reference
        sources (docs, blog) to filter out how-to/marketing content."""
        out = hits
        if self._experimentation_only:
            out = [
                h for h in out
                if any(kw in (_result_to_raw_text(h)).lower() for kw in _EXPERIMENTATION_KEYWORDS)
            ]
        if self._problems_only:
            out = [
                h for h in out
                if _datasource(h) in _FEEDBACK_DATASOURCES
                or any(sig in (_snippet_text(h) or _result_to_raw_text(h)).lower() for sig in _PROBLEM_SIGNALS)
            ]
        return out

    def _results_to_records(self, hits: list[dict[str, Any]], glean_source: str) -> list[IngestionRecord]:
        records: list[IngestionRecord] = []
        seen: set[str] = set()
        for i, hit in enumerate(hits):
            rid = _result_id(hit, i)
            if rid in seen:
                continue
            seen.add(rid)
            records.append(
                IngestionRecord(
                    record_id=rid,
                    source=SourceType.GLEAN,
                    source_record_id=str(_field(hit, "id") or rid),
                    raw_text=_result_to_raw_text(hit),
                    metadata=_build_metadata(hit, glean_source),
                    ingested_at=datetime.now(),
                )
            )
        return records

    def _fetch_mock(self) -> list[IngestionRecord]:
        path = settings.mock_data_dir / "glean_search_hits.json"
        if not path.exists():
            return []
        raw = json.loads(path.read_text())
        # Accept either a bare list of results or a full /search response.
        hits = _flatten_results(raw) if isinstance(raw, dict) else (
            [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []
        )
        hits = self._filter_hits(hits)
        return self._results_to_records(hits, "mock")

    def _fetch_live(self) -> list[IngestionRecord]:
        if not glean_configured():
            return []
        limit = self._limit()
        datasources = self._datasources or _DEFAULT_DATASOURCES
        by_id: dict[str, dict[str, Any]] = {}
        for query in self._queries():
            ok, data = glean_search(query, page_size=limit, datasources=datasources)
            if not ok:
                raise RuntimeError(f"Glean search failed for query {query!r}: {data}")
            for hit in _flatten_results(data):
                rid = _result_id(hit, len(by_id))
                by_id.setdefault(rid, hit)
        hits = self._filter_hits(list(by_id.values()))
        return self._results_to_records(hits, "live")
