from __future__ import annotations

"""Cataloger agent — normalizes raw problems into structured catalog entries.

Uses the Anthropic API directly with structured output to ensure
schema compliance. This is the first stage of the pipeline.
"""

import json

from anthropic import Anthropic

from psm.schemas.problem import RawProblem, CatalogEntry
from psm.tools.context_loader import load_job_description


def build_system_prompt() -> str:
    job_desc = load_job_description("cataloger")
    return f"""{job_desc}

You will receive a batch of raw problems as JSON. For each one, produce a CatalogEntry.
Return a JSON array of CatalogEntry objects. Nothing else — no markdown, no explanation.
Just the JSON array."""


def run_cataloger(
    problems: list[RawProblem],
    existing_catalog: list[CatalogEntry] | None = None,
    model: str = "claude-sonnet-4-20250514",
) -> list[CatalogEntry]:
    """Run the Cataloger agent on a batch of raw problems.

    Returns validated CatalogEntry objects.
    """
    client = Anthropic()

    # Build context about existing tags for consistency
    existing_tags: set[str] = set()
    if existing_catalog:
        for entry in existing_catalog:
            existing_tags.update(entry.tags)

    user_content = json.dumps(
        [p.model_dump() for p in problems],
        indent=2,
    )

    if existing_tags:
        user_content += f"\n\nExisting tags in catalog (reuse where appropriate): {sorted(existing_tags)}"

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=build_system_prompt(),
        messages=[{"role": "user", "content": user_content}],
    )

    # Extract JSON from response
    text = response.content[0].text.strip()
    # Handle potential markdown fencing
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    raw_entries = json.loads(text)

    # Validate each entry through Pydantic
    entries = []
    for raw in raw_entries:
        entry = CatalogEntry.model_validate(raw)
        entries.append(entry)

    return entries
