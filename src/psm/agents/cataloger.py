"""Cataloger agent — normalizes raw problems into structured catalog entries.

Uses the Anthropic API directly with structured output to ensure
schema compliance. This is the first stage of the pipeline.
"""
from __future__ import annotations

import json

from anthropic import Anthropic

from psm.schemas.problem import RawProblem, CatalogEntry, Domain
from psm.tools.context_loader import load_job_description, load_onboarding

_VALID_DOMAINS = {d.value for d in Domain}


def build_system_prompt() -> str:
    job_desc = load_job_description("cataloger")
    onboarding = load_onboarding()
    return f"""{job_desc}

## User Context
{onboarding}

You will receive a batch of raw problems as JSON. For each one, produce a CatalogEntry.
Return a JSON array of CatalogEntry objects. Nothing else — no markdown, no explanation.
Just the JSON array."""


_BATCH_SIZE = 20


def run_cataloger(
    problems: list[RawProblem],
    existing_catalog: list[CatalogEntry] | None = None,
    model: str = "claude-sonnet-4-20250514",
    config: dict | None = None,
) -> list[CatalogEntry]:
    """Run the Cataloger agent on a batch of raw problems.

    Processes in batches to stay within token limits.
    Returns validated CatalogEntry objects.
    """
    client = Anthropic()
    system = build_system_prompt()

    # Build context about existing tags for consistency
    existing_tags: set[str] = set()
    if existing_catalog:
        for entry in existing_catalog:
            existing_tags.update(entry.tags)

    all_entries: list[CatalogEntry] = []

    total_batches = (len(problems) + _BATCH_SIZE - 1) // _BATCH_SIZE
    for batch_start in range(0, len(problems), _BATCH_SIZE):
        batch = problems[batch_start:batch_start + _BATCH_SIZE]
        batch_num = batch_start // _BATCH_SIZE + 1
        print(f"  [cataloger] Processing batch {batch_num}/{total_batches} ({len(batch)} problems)...")
        try:
            from psm.agents.orchestrator import report_sub_progress
            report_sub_progress(f"Cataloger batch {batch_num}/{total_batches} ({len(batch)} problems)")
        except ImportError:
            pass

        user_content = json.dumps(
            [p.model_dump() for p in batch],
            indent=2,
        )

        if existing_tags:
            user_content += f"\n\nExisting tags in catalog (reuse where appropriate): {sorted(existing_tags)}"

        response = client.messages.create(
            model=model,
            max_tokens=8192,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )

        # Extract JSON from response
        text = response.content[0].text.strip()
        # Handle potential markdown fencing
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        raw_entries = json.loads(text)

        # Validate each entry through Pydantic
        for raw in raw_entries:
            # Coerce invalid domain values to "other"
            if isinstance(raw, dict) and raw.get("domain") not in _VALID_DOMAINS:
                raw["domain"] = "other"
            try:
                entry = CatalogEntry.model_validate(raw)
                all_entries.append(entry)
                existing_tags.update(entry.tags)
            except Exception as e:
                print(f"  [cataloger] Skipping invalid entry: {e}")

    return all_entries
