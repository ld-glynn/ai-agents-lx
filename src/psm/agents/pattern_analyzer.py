"""Pattern Analyzer agent — clusters related problems and identifies themes."""
from __future__ import annotations

import json

from anthropic import Anthropic

from psm.schemas.problem import CatalogEntry
from psm.schemas.pattern import Pattern, ThemeSummary
from psm.tools.context_loader import load_job_description, load_onboarding


def build_system_prompt() -> str:
    job_desc = load_job_description("pattern_analyzer")
    onboarding = load_onboarding()
    return f"""{job_desc}

## User Context
{onboarding}

You will receive the full problem catalog as JSON. Analyze it and return a JSON object with
two keys: "patterns" (array of Pattern objects) and "themes" (array of ThemeSummary objects).
Nothing else — no markdown, no explanation. Just the JSON object."""


def run_pattern_analyzer(
    catalog: list[CatalogEntry],
    model: str = "claude-sonnet-4-20250514",
    config: dict | None = None,
) -> tuple[list[Pattern], list[ThemeSummary]]:
    """Analyze the catalog for patterns and themes.

    Returns (patterns, themes) — both validated through Pydantic.
    """
    client = Anthropic()

    config_instructions = ""
    if config:
        parts = []
        if config.get("min_cluster_size"):
            parts.append(f"Each pattern must link at least {config['min_cluster_size']} problems.")
        if config.get("max_patterns"):
            parts.append(f"Produce at most {config['max_patterns']} patterns.")
        if config.get("clustering_strictness") is not None:
            strictness = config["clustering_strictness"]
            if strictness > 0.7:
                parts.append("Be strict: only cluster problems with strong causal connections.")
            elif strictness < 0.3:
                parts.append("Be broad: cluster problems with loose thematic similarity.")
        if parts:
            config_instructions = "\n\nAdditional constraints:\n" + "\n".join(f"- {p}" for p in parts)

    user_content = json.dumps(
        [e.model_dump(mode="json") for e in catalog],
        indent=2,
    )

    response = client.messages.create(
        model=model,
        max_tokens=16384,
        system=build_system_prompt() + config_instructions,
        messages=[{"role": "user", "content": user_content}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    raw = json.loads(text)

    patterns = [Pattern.model_validate(p) for p in raw["patterns"]]
    themes = [ThemeSummary.model_validate(t) for t in raw["themes"]]

    # Clean referential integrity: drop invalid problem_ids instead of crashing
    catalog_ids = {e.problem_id for e in catalog}
    for pat in patterns:
        invalid = set(pat.problem_ids) - catalog_ids
        if invalid:
            print(f"  [pattern_analyzer] Warning: {pat.pattern_id} references unknown problems {invalid} — removing them")
            pat.problem_ids = [pid for pid in pat.problem_ids if pid in catalog_ids]
    # Drop patterns with no valid problems left
    patterns = [p for p in patterns if p.problem_ids]

    return patterns, themes
