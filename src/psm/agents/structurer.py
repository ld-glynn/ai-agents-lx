"""Structurer agent — Stage 0: Convert raw ingestion data into RawProblem objects.

Takes IngestionRecord objects from external sources and uses Claude to extract
structured problem statements suitable for the PSM pipeline.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime

from psm.schemas.ingestion import IngestionRecord
from psm.schemas.problem import RawProblem
from psm.tools.context_loader import load_job_description


@dataclass
class ExtractedProblem:
    """A RawProblem plus provenance metadata from the extraction."""

    problem: RawProblem
    source_record_ids: list[str] = field(default_factory=list)
    evidence: str = ""


def structure_records(
    records: list[IngestionRecord],
    *,
    model: str = "claude-sonnet-4-20250514",
) -> list[ExtractedProblem]:
    """Extract problem statements from raw ingestion records.

    In mock/demo mode (no API key), uses a simple heuristic extractor.
    With a valid API key, uses Claude for intelligent extraction.
    """
    if not records:
        return []

    try:
        return _structure_with_llm(records, model=model)
    except Exception as e:
        print(f"  [structurer] LLM extraction failed ({e}), using heuristic fallback", file=sys.stderr)
        return _structure_heuristic(records)


def _structure_heuristic(records: list[IngestionRecord]) -> list[ExtractedProblem]:
    """Simple heuristic extraction — used when no API key is available."""
    problems = []
    for i, record in enumerate(records):
        meta = record.metadata or {}

        # Extract title — strip "Title: " prefix from raw_text first line
        lines = record.raw_text.strip().split("\n")
        title_line = lines[0] if lines else record.raw_text[:80]
        for prefix in ["Title: ", "Title:", "[#", "Call: ", "INCIDENT: "]:
            if title_line.startswith(prefix):
                title_line = title_line[len(prefix):].strip()
                break
        if "]" in title_line and title_line.startswith("["):
            title_line = title_line.split("]")[-1].strip()
        if len(title_line) > 100:
            title_line = title_line[:97] + "..."

        source_label = record.source.value
        problem_id = f"INT-{source_label}-{str(i + 1).zfill(3)}"

        reported_by = meta.get("user") or meta.get("account_name") or "Unknown"
        date_str = record.ingested_at.isoformat() if record.ingested_at else datetime.now().isoformat()

        # Build description from metadata — NOT just raw_text
        # Priority: synthesis > agent_idea > description from raw_text > raw_text fallback
        desc_parts = []
        synthesis = meta.get("synthesis", "")
        agent_idea = meta.get("agent_idea", "")
        if synthesis:
            desc_parts.append(synthesis)
        if agent_idea:
            desc_parts.append(f"Agent opportunity: {agent_idea}")
        # Add source info
        upstream = meta.get("upstream_sources", [])
        source_counts = meta.get("source_counts", {})
        feedback_count = meta.get("feedback_sample_count", 0)
        if source_counts:
            counts_str = ", ".join(f"{k}: {v} mentions" for k, v in source_counts.items())
            desc_parts.append(f"Sources: {counts_str}")
        elif upstream:
            desc_parts.append(f"Sources: {', '.join(upstream)}")
        if feedback_count:
            desc_parts.append(f"Based on {feedback_count} customer feedback items.")
        # Fallback to raw_text if no metadata
        if not desc_parts:
            # Use description line from raw_text if available
            for line in lines:
                if line.startswith("Description:") or line.startswith("Content:"):
                    desc_parts.append(line.split(":", 1)[1].strip()[:500])
                    break
            if not desc_parts:
                desc_parts.append(record.raw_text[:500])

        description = " ".join(desc_parts)

        # Domain guessing from keywords
        text_lower = (record.raw_text + " " + description).lower()
        domain = None
        if any(w in text_lower for w in ["deploy", "ci/cd", "pipeline", "docker", "database"]):
            domain = "infrastructure"
        elif any(w in text_lower for w in ["onboarding", "documentation", "docs", "guide", "knowledge"]):
            domain = "knowledge"
        elif any(w in text_lower for w in ["customer", "churn", "support", "ticket"]):
            domain = "customer"
        elif any(w in text_lower for w in ["feedback", "communication", "disconnect"]):
            domain = "communication"
        elif any(w in text_lower for w in ["process", "workflow", "sprint", "planning"]):
            domain = "process"

        # Build evidence from source metadata
        evidence_parts = [f"Extracted from {source_label} record {record.record_id}."]
        if upstream:
            evidence_parts.append(f"Upstream data sources: {', '.join(upstream)}.")
        snippet = record.raw_text[:150].replace("\n", " ")
        evidence_parts.append(f'Source signal: "{snippet}"')

        problems.append(ExtractedProblem(
            problem=RawProblem(
                id=problem_id,
                title=title_line,
                description=description,
                reported_by=reported_by,
                date_reported=date_str,
                domain=domain,
                tags=source_label,
                # Provenance from Wisdom enrichment
                agent_idea=agent_idea or None,
                synthesis=synthesis or None,
                evidence=" ".join(evidence_parts),
                upstream_sources=upstream,
                source_counts=source_counts,
                source_record_ids=[record.record_id],
            ),
            source_record_ids=[record.record_id],
            evidence=" ".join(evidence_parts),
        ))

    return problems


_STRUCTURER_BATCH_SIZE = 8


def _structure_with_llm(
    records: list[IngestionRecord],
    *,
    model: str,
) -> list[ExtractedProblem]:
    """Use Claude to intelligently extract problems from raw records.

    Processes in batches to stay within token limits.
    """
    import anthropic

    client = anthropic.Anthropic()
    job_desc = load_job_description("structurer")
    all_results: list[ExtractedProblem] = []

    for batch_start in range(0, len(records), _STRUCTURER_BATCH_SIZE):
        batch = records[batch_start:batch_start + _STRUCTURER_BATCH_SIZE]
        batch_num = batch_start // _STRUCTURER_BATCH_SIZE + 1
        print(f"  [structurer] LLM batch {batch_num} ({len(batch)} records)...")

        records_json = json.dumps(
            [r.model_dump(mode="json") for r in batch],
            indent=2,
            default=str,
        )

        response = client.messages.create(
            model=model,
            max_tokens=8192,
            system=job_desc,
            messages=[
                {
                    "role": "user",
                    "content": f"Extract problems from these ingestion records:\n\n{records_json}",
                }
            ],
        )

        text = response.content[0].text
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        try:
            raw_problems_data = json.loads(text.strip())
        except json.JSONDecodeError as e:
            print(f"  [structurer] Batch {batch_num} JSON parse failed ({e}), skipping")
            continue

        for p in raw_problems_data:
            source_record_ids = p.pop("source_record_ids", [])
            evidence = p.pop("evidence", "")
            try:
                problem = RawProblem.model_validate(p)
                all_results.append(ExtractedProblem(
                    problem=problem,
                    source_record_ids=source_record_ids,
                    evidence=evidence,
                ))
            except Exception as e:
                print(f"  [structurer] Skipping invalid problem: {e}")

    return all_results
