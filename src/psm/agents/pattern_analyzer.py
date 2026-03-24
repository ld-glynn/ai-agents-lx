from __future__ import annotations

"""Pattern Analyzer agent — clusters related problems and identifies themes."""

import json

from anthropic import Anthropic

from psm.schemas.problem import CatalogEntry
from psm.schemas.pattern import Pattern, ThemeSummary
from psm.tools.context_loader import load_job_description


def build_system_prompt() -> str:
    job_desc = load_job_description("pattern_analyzer")
    return f"""{job_desc}

You will receive the full problem catalog as JSON. Analyze it and return a JSON object with
two keys: "patterns" (array of Pattern objects) and "themes" (array of ThemeSummary objects).
Nothing else — no markdown, no explanation. Just the JSON object."""


def run_pattern_analyzer(
    catalog: list[CatalogEntry],
    model: str = "claude-sonnet-4-20250514",
) -> tuple[list[Pattern], list[ThemeSummary]]:
    """Analyze the catalog for patterns and themes.

    Returns (patterns, themes) — both validated through Pydantic.
    """
    client = Anthropic()

    user_content = json.dumps(
        [e.model_dump(mode="json") for e in catalog],
        indent=2,
    )

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=build_system_prompt(),
        messages=[{"role": "user", "content": user_content}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    raw = json.loads(text)

    patterns = [Pattern.model_validate(p) for p in raw["patterns"]]
    themes = [ThemeSummary.model_validate(t) for t in raw["themes"]]

    # Validate referential integrity: all problem_ids must exist in catalog
    catalog_ids = {e.problem_id for e in catalog}
    for pat in patterns:
        invalid = set(pat.problem_ids) - catalog_ids
        if invalid:
            raise ValueError(
                f"Pattern {pat.pattern_id} references non-existent problems: {invalid}"
            )

    return patterns, themes
