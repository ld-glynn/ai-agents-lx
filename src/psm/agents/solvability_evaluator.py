from __future__ import annotations

"""Solvability Evaluator — Stage 2.5: Filter patterns before hypothesis generation.

Sits between Pattern Analyzer (S2) and Hypothesis Generator (S3).
Evaluates each pattern against the capability inventory and historical outcomes
to decide: pass (proceed), flag (human review), or drop (exclude).
"""

import json
import sys
from datetime import datetime

from psm.schemas.pattern import Pattern
from psm.schemas.solvability import (
    SolvabilityResult, SolvabilityReport, SolvabilityStatus,
    CapabilityInventory, OutcomeEntry,
)
from psm.tools.context_loader import load_job_description
from psm.config import settings


def load_capability_inventory() -> CapabilityInventory:
    """Load the capability inventory from disk. Never cached — always fresh."""
    path = settings.data_dir / "capability_inventory.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Capability inventory not found: {path}\n"
            "Create data/capability_inventory.json with agent types and skill types."
        )
    raw = json.loads(path.read_text())
    return CapabilityInventory.model_validate(raw)


def load_outcomes_log() -> list[OutcomeEntry]:
    """Load historical outcomes if available."""
    path = settings.data_dir / "outcomes_log.jsonl"
    if not path.exists():
        return []
    entries = []
    for line in path.read_text().strip().split("\n"):
        if line.strip():
            entries.append(OutcomeEntry.model_validate(json.loads(line)))
    return entries


def run_solvability_evaluator(
    patterns: list[Pattern],
    *,
    model: str = "claude-sonnet-4-20250514",
) -> SolvabilityReport:
    """Evaluate patterns for solvability.

    Uses Claude when API key is available, falls back to heuristic evaluation.
    Returns a SolvabilityReport with a result for each pattern.
    """
    if not patterns:
        return SolvabilityReport(
            results=[], total_patterns=0, passed=0, flagged=0, dropped=0,
        )

    inventory = load_capability_inventory()
    outcomes = load_outcomes_log()

    try:
        results = _evaluate_with_llm(patterns, inventory, outcomes, model=model)
    except Exception as e:
        print(f"  [solvability] LLM evaluation failed ({e}), using heuristic", file=sys.stderr)
        results = _evaluate_heuristic(patterns, inventory, outcomes)

    # Validate: every pattern must have a result
    result_ids = {r.pattern_id for r in results}
    for pat in patterns:
        if pat.pattern_id not in result_ids:
            results.append(SolvabilityResult(
                pattern_id=pat.pattern_id,
                status=SolvabilityStatus.FLAG,
                confidence=0.3,
                reason="No evaluation produced — flagged for manual review.",
                capability_match=True,
                signal_strength=0.5,
                actionability=0.5,
            ))

    passed = sum(1 for r in results if r.status == SolvabilityStatus.PASS)
    flagged = sum(1 for r in results if r.status == SolvabilityStatus.FLAG)
    dropped = sum(1 for r in results if r.status == SolvabilityStatus.DROP)

    return SolvabilityReport(
        results=results,
        total_patterns=len(patterns),
        passed=passed,
        flagged=flagged,
        dropped=dropped,
    )


def _evaluate_heuristic(
    patterns: list[Pattern],
    inventory: CapabilityInventory,
    outcomes: list[OutcomeEntry],
) -> list[SolvabilityResult]:
    """Deterministic heuristic evaluation — no LLM needed."""
    # Build domain coverage set from inventory
    covered_domains = set()
    for agent_type in inventory.agent_types:
        for domain in agent_type.domains:
            covered_domains.add(domain.lower())

    # Build outcome history by domain
    domain_outcomes: dict[str, list[str]] = {}
    for o in outcomes:
        if o.domain:
            domain_outcomes.setdefault(o.domain, []).append(o.outcome)

    results = []
    for pat in patterns:
        # Check capability match
        pat_domains = {d.value.lower() for d in pat.domains_affected}
        capability_match = bool(pat_domains & covered_domains)

        # Signal strength: based on problem count vs minimum
        min_problems = inventory.min_problems_per_pattern
        signal = min(pat.frequency / (min_problems * 3), 1.0)

        # Actionability: heuristic based on domain
        non_actionable_signals = {"strategy", "people"}
        actionability = 0.4 if pat_domains & non_actionable_signals else 0.8

        # Historical context
        confidence_boost = 0.0
        for domain in pat_domains:
            hist = domain_outcomes.get(domain, [])
            if hist:
                validated = sum(1 for o in hist if o == "validated")
                total = len(hist)
                if total > 0:
                    confidence_boost = (validated / total - 0.5) * 0.2

        # Decide status
        score = (
            (0.3 if capability_match else 0.0) +
            signal * 0.3 +
            actionability * 0.3 +
            pat.confidence * 0.1 +
            confidence_boost
        )

        if not capability_match:
            status = SolvabilityStatus.DROP
            reason = f"Pattern domains {pat_domains} not covered by capability inventory."
        elif pat.frequency < min_problems:
            status = SolvabilityStatus.DROP
            reason = f"Insufficient signal: {pat.frequency} problems (minimum: {min_problems})."
        elif score >= 0.6:
            status = SolvabilityStatus.PASS
            reason = f"Within capability space, sufficient signal ({pat.frequency} problems), actionable."
        elif score >= 0.4:
            status = SolvabilityStatus.FLAG
            reason = f"Borderline solvability (score {score:.2f}) — needs human review."
        else:
            status = SolvabilityStatus.DROP
            reason = f"Low solvability score ({score:.2f}) — likely structural or political."

        results.append(SolvabilityResult(
            pattern_id=pat.pattern_id,
            status=status,
            confidence=min(max(score, 0.1), 0.95),
            reason=reason,
            capability_match=capability_match,
            signal_strength=signal,
            actionability=actionability,
        ))

    return results


def _evaluate_with_llm(
    patterns: list[Pattern],
    inventory: CapabilityInventory,
    outcomes: list[OutcomeEntry],
    *,
    model: str,
) -> list[SolvabilityResult]:
    """Use Claude for intelligent solvability evaluation."""
    from anthropic import Anthropic

    client = Anthropic()
    job_desc = load_job_description("solvability_evaluator")

    user_content = json.dumps({
        "patterns": [p.model_dump(mode="json") for p in patterns],
        "capability_inventory": inventory.model_dump(mode="json"),
        "outcomes_log": [o.model_dump(mode="json") for o in outcomes[-20:]],  # last 20 entries
    }, indent=2, default=str)

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=f"{job_desc}\n\nReturn a JSON array of result objects. Nothing else — no markdown, no explanation.",
        messages=[{"role": "user", "content": user_content}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    raw = json.loads(text)
    return [SolvabilityResult.model_validate(r) for r in raw]
