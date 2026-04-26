"""Reverse Analyzer — maps an existing agent backwards into PSM's framework.

Takes an agent description and generates the implicit hypothesis, pattern,
and candidate problems it addresses. Cross-references with existing PSM data
to identify matches.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from psm.schemas.problem import CatalogEntry
from psm.schemas.pattern import Pattern
from psm.schemas.hypothesis import Hypothesis

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a reverse-analysis agent for the PSM (Problem-Solution Mapping) system.

A user has an externally-built AI agent they want to register in PSM. Your job is to work \
backwards from what the agent does to identify:

1. **PATTERN** — What recurring problem cluster does this agent address?
   Think about what organizational pain point motivated someone to build this agent.

2. **HYPOTHESIS** — What testable claim is this agent implicitly making?
   Format: "If we [what the agent does], then [expected outcome], because [reasoning]"
   Include specific, measurable test_criteria for how you'd know if this agent delivers value.

3. **AGENT SUMMARY** — A refined title, persona description, and primary skill type for PSM.

4. **CROSS-REFERENCES** — Do any of the existing PSM patterns or hypotheses already cover \
this ground? List matches with explanation of similarity. Only include genuine matches, not \
vague thematic overlap.

5. **CANDIDATE PROBLEMS** — What specific problems would this agent appear in a problem \
catalog as? List 3-5 concrete problem statements.

6. **REASONING** — A paragraph explaining your analysis: why you chose this pattern, \
why the hypothesis is framed this way, and what evidence would validate or invalidate it.

Return a single JSON object with these keys:
- pattern: {name, description, domains_affected (array of: process/tooling/communication/knowledge/infrastructure/people/strategy/customer/other), root_cause_hypothesis, confidence (0-1)}
- hypothesis: {statement (min 20 chars, "If we..., then..., because..." format), assumptions (array), expected_outcome, effort_estimate ("low"|"medium"|"high"), confidence (0-1), test_criteria (array, min 1), risks (array)}
- agent_summary: {title, persona (min 10 chars), skill_type ("recommend"|"action_plan"|"process_doc"|"investigate")}
- matched_patterns: [{pattern_id, name, similarity_reason}] (empty array if no matches)
- matched_hypotheses: [{hypothesis_id, statement, similarity_reason}] (empty array if no matches)
- candidate_problems: [string descriptions]
- reasoning: string

No markdown fences. Just the JSON object."""


def reverse_analyze(
    agent_description: dict[str, Any],
    existing_patterns: list[Pattern],
    existing_hypotheses: list[Hypothesis],
    existing_catalog: list[CatalogEntry],
    model: str = "claude-sonnet-4-20250514",
) -> dict[str, Any]:
    """Reverse-analyze an externally-built agent to generate PSM entities.

    Args:
        agent_description: Dict with name, description, who_uses, data_sources, outputs
        existing_patterns: Current PSM patterns for cross-referencing
        existing_hypotheses: Current PSM hypotheses for cross-referencing
        existing_catalog: Current catalog entries for context
        model: Claude model to use

    Returns:
        Dict with pattern, hypothesis, agent_summary, matched_patterns,
        matched_hypotheses, candidate_problems, and reasoning.
    """
    import anthropic

    client = anthropic.Anthropic()

    # Build context about existing PSM data
    existing_context = {
        "patterns": [
            {"pattern_id": p.pattern_id, "name": p.name, "description": p.description, "domains": [d.value if hasattr(d, 'value') else d for d in p.domains_affected]}
            for p in existing_patterns
        ],
        "hypotheses": [
            {"hypothesis_id": h.hypothesis_id, "statement": h.statement, "pattern_id": h.pattern_id}
            for h in existing_hypotheses
        ],
        "problem_count": len(existing_catalog),
        "domains_present": list(set(
            (e.domain.value if hasattr(e.domain, 'value') else e.domain)
            for e in existing_catalog
        )),
    }

    user_content = json.dumps({
        "agent": agent_description,
        "existing_psm_data": existing_context,
    }, indent=2)

    logger.info("Running reverse analysis for agent: %s", agent_description.get("name", "?"))

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )

    text = response.content[0].text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]

    result = json.loads(text.strip())

    # Validate required keys
    for key in ("pattern", "hypothesis", "agent_summary"):
        if key not in result:
            raise ValueError(f"Reverse analysis missing required key: {key}")

    # Default optional keys
    result.setdefault("matched_patterns", [])
    result.setdefault("matched_hypotheses", [])
    result.setdefault("candidate_problems", [])
    result.setdefault("reasoning", "")

    return result
