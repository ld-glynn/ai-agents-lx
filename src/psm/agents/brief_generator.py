"""Brief Generator — produces executive-quality narrative documents for pipeline entities.

Each entity type (problem, pattern, hypothesis) gets a tailored prompt that
synthesizes the entity's data, evidence, and context into a presentable brief.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from psm.schemas.brief import Brief

logger = logging.getLogger(__name__)

_PROBLEM_SYSTEM = """\
You are writing an executive brief about a specific problem identified in the PSM system.
Your audience is a Product Specialist in Experimentation at LaunchDarkly who needs to \
present this to their manager or VP.

Write a compelling, evidence-grounded narrative in markdown format with these sections:

## Summary
One paragraph: what this problem is, who it affects, and why it matters.

## Evidence
Cite specific customer interactions. Use direct quotes or paraphrases from the source \
data provided. Name the source (Gong call, Zendesk ticket, etc.) for each piece of evidence. \
If no source evidence is available, state that clearly.

## Impact
Who is affected (roles, teams, customers)? How severely? How frequently? \
What's the business consequence if this goes unaddressed?

## Connection to Larger Themes
How does this problem relate to other identified patterns? Is it part of a cluster? \
What does addressing this problem contribute to?

Be specific and evidence-grounded. Never fabricate quotes or data. If information is missing, \
say so rather than inventing it. Write in a professional but direct tone."""

_PATTERN_SYSTEM = """\
You are writing an executive brief about a pattern — a cluster of related problems — \
identified in the PSM system. Your audience is a Product Specialist in Experimentation \
at LaunchDarkly who needs to present this to their manager or VP.

This is the most important document in the PSM process. It needs to make a compelling, \
evidence-based case for why this pattern deserves attention and resources.

Write in markdown format with these sections:

## Executive Summary
2-3 sentences: what this pattern is, how significant it is, and what should be done about it.

## Evidence Base
How many customer interactions support this pattern? From which sources (Gong, Zendesk, \
Slack, G2)? Cite specific examples — direct quotes or paraphrases from the source data. \
The reader should come away thinking "this is real, customers are actually saying this."

## Business Impact
Who is affected? How severely? What's at stake — deals, retention, adoption, efficiency? \
Quantify where possible (mention counts, number of affected accounts, severity distribution).

## Root Cause Analysis
Why does this pattern keep recurring? What's the underlying driver? This should go beyond \
symptoms to structural causes.

## Recommendation
What should be done? Connect to the proposed hypothesis and agent if available. Frame it \
as a specific, actionable next step — not a vague aspiration.

Be specific and evidence-grounded. Never fabricate quotes or data. Write in a professional \
but direct tone that would be appropriate for an executive presentation."""

_HYPOTHESIS_SYSTEM = """\
You are writing a proposal document for a hypothesis — a testable solution — in the PSM \
system. Your audience is a Product Specialist in Experimentation at LaunchDarkly who needs \
to convince their manager that this approach is worth pursuing.

Write in markdown format with these sections:

## Proposal
What are we proposing? Restate the hypothesis in plain English. What would the agent \
actually do day-to-day?

## Why This Approach
What evidence supports this specific solution? Connect to the pattern's root cause and \
the customer feedback that motivated it. Why is this approach better than alternatives?

## Expected Outcome
What would success look like? Be specific and measurable. Include the test criteria \
and how we'd know within the trial period whether this is working.

## Assumptions & Risks
What must be true for this to work? What could go wrong? Be honest — identifying risks \
upfront builds credibility.

## Success Criteria
How will we measure this? What metrics, over what timeframe? What's the minimum bar \
for "validated"?

## Recommended Action
What's the concrete next step? Deploy the agent, start a trial, configure monitoring — \
make it actionable.

Be specific and evidence-grounded. Write to persuade, but honestly."""


def generate_brief(
    entity_type: str,
    entity_id: str,
    context: dict[str, Any],
    model: str = "claude-sonnet-4-20250514",
) -> Brief:
    """Generate an executive brief for a pipeline entity.

    Args:
        entity_type: "problem", "pattern", or "hypothesis"
        entity_id: The entity's ID
        context: Dict with the entity data plus related entities and evidence.
            Expected keys vary by entity_type but typically include:
            - entity: the main entity's data
            - related_problems, related_patterns, related_hypotheses, related_agents
            - feedback_items: source evidence from ingestion records
        model: Claude model to use
    """
    import anthropic

    system_prompts = {
        "problem": _PROBLEM_SYSTEM,
        "pattern": _PATTERN_SYSTEM,
        "hypothesis": _HYPOTHESIS_SYSTEM,
    }

    system = system_prompts.get(entity_type)
    if not system:
        raise ValueError(f"Unknown entity type for brief generation: {entity_type}")

    client = anthropic.Anthropic()

    user_content = json.dumps(context, indent=2, default=str)

    logger.info("Generating %s brief for %s", entity_type, entity_id)

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )

    content = response.content[0].text.strip()

    return Brief(
        entity_type=entity_type,
        entity_id=entity_id,
        content=content,
        model_used=model,
    )
