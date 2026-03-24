from __future__ import annotations
"""Structurer agent — Stage 0: Convert raw ingestion data into RawProblem objects.

Takes IngestionRecord objects from external sources and uses Claude to extract
structured problem statements suitable for the PSM pipeline.
"""

import json
import sys
from datetime import datetime

from psm.schemas.ingestion import IngestionRecord
from psm.schemas.problem import RawProblem
from psm.tools.context_loader import load_job_description


def structure_records(
    records: list[IngestionRecord],
    *,
    model: str = "claude-sonnet-4-20250514",
) -> list[RawProblem]:
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


def _structure_heuristic(records: list[IngestionRecord]) -> list[RawProblem]:
    """Simple heuristic extraction — used when no API key is available."""
    problems = []
    for i, record in enumerate(records):
        # Extract a title from the first line or first 80 chars
        lines = record.raw_text.strip().split("\n")
        title_line = lines[0] if lines else record.raw_text[:80]
        # Clean up common prefixes
        for prefix in ["[#", "Call: ", "INCIDENT: "]:
            if title_line.startswith(prefix):
                title_line = title_line.split("]")[-1].strip() if "]" in title_line else title_line[len(prefix):]

        if len(title_line) > 100:
            title_line = title_line[:97] + "..."

        source_label = record.source.value
        problem_id = f"INT-{source_label}-{str(i + 1).zfill(3)}"

        reported_by = record.metadata.get("user") or record.metadata.get("account_name") or "Unknown"
        date_str = record.ingested_at.isoformat() if record.ingested_at else datetime.now().isoformat()

        # Simple domain guessing from keywords
        text_lower = record.raw_text.lower()
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

        problems.append(RawProblem(
            id=problem_id,
            title=title_line,
            description=record.raw_text[:500],
            reported_by=reported_by,
            date_reported=date_str,
            domain=domain,
            tags=source_label,
        ))

    return problems


def _structure_with_llm(
    records: list[IngestionRecord],
    *,
    model: str,
) -> list[RawProblem]:
    """Use Claude to intelligently extract problems from raw records."""
    import anthropic

    client = anthropic.Anthropic()
    job_desc = load_job_description("structurer")

    records_json = json.dumps(
        [r.model_dump(mode="json") for r in records],
        indent=2,
        default=str,
    )

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=job_desc,
        messages=[
            {
                "role": "user",
                "content": f"Extract problems from these ingestion records:\n\n{records_json}",
            }
        ],
    )

    text = response.content[0].text
    # Parse JSON from response (handle markdown code blocks)
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]

    raw_problems_data = json.loads(text.strip())
    return [RawProblem.model_validate(p) for p in raw_problems_data]
