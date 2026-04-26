"""Scorer — validates agent output against test case expectations.

Three phases (borrowed from elliot-eval):
  1. Parse: Can we extract valid JSON from the raw text?
  2. Schema: Does it pass Pydantic validation?
  3. Quality: Does it meet the rubric (references, content depth, keywords)?
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from pydantic import ValidationError

from psm.schemas.agent import SkillOutput
from psm.eval.failure_codes import FailureCode, is_hard
from psm.eval.test_gen import TestCase


@dataclass
class ScoreResult:
    """Result of scoring a single test case."""

    case_id: str
    passed: bool
    score: float                                   # 0.0 to 1.0
    failures: list[FailureCode] = field(default_factory=list)
    failure_details: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    output: SkillOutput | None = None

    @property
    def has_hard_failure(self) -> bool:
        return any(is_hard(f) for f in self.failures)


def _extract_json(raw_text: str) -> dict | None:
    """Try to extract a JSON object from raw text. Mirrors elliot-eval's 3-strategy approach."""
    text = raw_text.strip()

    # Strategy 1: entire text is JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: fenced code block
    if "```" in text:
        parts = text.split("```")
        for part in parts[1::2]:  # odd indices are inside fences
            cleaned = part.strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                continue

    # Strategy 3: outermost { ... }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    return None


def _check_hallucinated_refs(
    output: SkillOutput,
    case: TestCase,
) -> list[str]:
    """Check that the output doesn't reference IDs that don't exist in input context."""
    issues: list[str] = []

    # Build the set of valid IDs from input context
    valid_ids: set[str] = set()
    valid_ids.add(case.hypothesis.hypothesis_id.lower())
    valid_ids.add(case.pattern.pattern_id.lower())
    for pid in case.pattern.problem_ids:
        valid_ids.add(pid.lower())
    for hid in case.agent.hypothesis_ids:
        valid_ids.add(hid.lower())

    # Look for ID-like references in the output (e.g. P-001, PAT-01, HYP-01)
    import re
    id_pattern = re.compile(r'\b([A-Z]+-\d+)\b')
    found_ids = id_pattern.findall(output.content)

    for found_id in found_ids:
        if found_id.lower() not in valid_ids:
            issues.append(f"Referenced '{found_id}' which is not in input context")

    return issues


def score_case(case: TestCase, raw_text: str) -> ScoreResult:
    """Score a single test case output.

    Runs three phases: parse → schema → quality.
    Returns as soon as a hard failure is detected.
    """
    result = ScoreResult(case_id=case.case_id, passed=False, score=0.0)

    # --- Phase 1: Parse JSON ---
    raw_json = _extract_json(raw_text)
    if raw_json is None:
        result.failures.append(FailureCode.JSON_PARSE_ERROR)
        result.failure_details.append("Could not extract valid JSON from agent output")
        return result

    # --- Phase 2: Schema validation ---
    try:
        output = SkillOutput(
            agent_id=case.agent.agent_id,
            skill_type=case.skill.skill_type,
            hypothesis_id=case.skill.hypothesis_id,
            title=raw_json.get("title", ""),
            content=raw_json.get("content", ""),
            next_steps=raw_json.get("next_steps", []),
        )
    except (ValidationError, Exception) as e:
        result.failures.append(FailureCode.SCHEMA_INVALID)
        result.failure_details.append(f"Pydantic validation failed: {e}")
        return result

    result.output = output

    # --- Phase 3: Quality checks ---
    check_count = 0
    pass_count = 0

    # 3a: Content length
    check_count += 1
    if len(output.content) < case.expected.min_content_length:
        if len(output.content) < 50:
            result.failures.append(FailureCode.MISSING_CONTENT)
            result.failure_details.append(
                f"Content is only {len(output.content)} chars (minimum: {case.expected.min_content_length})"
            )
        else:
            result.warnings.append(
                f"SHORT_CONTENT: {len(output.content)} chars (expected >= {case.expected.min_content_length})"
            )
            result.failures.append(FailureCode.SHORT_CONTENT)
            pass_count += 0.5  # Partial credit
    else:
        pass_count += 1

    # 3b: References input IDs (proves the agent read the context)
    check_count += 1
    content_lower = output.content.lower() + " " + output.title.lower()
    refs_found = sum(
        1 for ref_id in case.expected.must_reference_ids
        if ref_id.lower() in content_lower
    )
    if refs_found == 0:
        result.failures.append(FailureCode.GENERIC_OUTPUT)
        result.failure_details.append(
            f"Output doesn't reference any input IDs: {case.expected.must_reference_ids}"
        )
    elif refs_found < len(case.expected.must_reference_ids):
        result.warnings.append(
            f"Only referenced {refs_found}/{len(case.expected.must_reference_ids)} input IDs"
        )
        pass_count += refs_found / len(case.expected.must_reference_ids)
    else:
        pass_count += 1

    # 3c: Hallucination check
    check_count += 1
    hallucinations = _check_hallucinated_refs(output, case)
    if hallucinations:
        result.failures.append(FailureCode.HALLUCINATED_REF)
        result.failure_details.extend(hallucinations)
    else:
        pass_count += 1

    # 3d: Next steps
    check_count += 1
    if case.expected.requires_next_steps:
        if len(output.next_steps) < case.expected.min_next_steps:
            result.failures.append(FailureCode.WEAK_NEXT_STEPS)
            result.warnings.append(
                f"Expected >= {case.expected.min_next_steps} next_steps, got {len(output.next_steps)}"
            )
        else:
            pass_count += 1
    else:
        pass_count += 1

    # 3e: Keywords (soft check only)
    check_count += 1
    if case.expected.must_contain_keywords:
        kw_found = sum(
            1 for kw in case.expected.must_contain_keywords
            if kw.lower() in content_lower
        )
        kw_ratio = kw_found / len(case.expected.must_contain_keywords)
        if kw_ratio < 0.3:
            result.warnings.append(
                f"Low keyword coverage: {kw_found}/{len(case.expected.must_contain_keywords)} domain terms found"
            )
            pass_count += kw_ratio
        else:
            pass_count += 1
    else:
        pass_count += 1

    # Calculate score and pass/fail
    result.score = pass_count / check_count if check_count > 0 else 0.0
    result.passed = not result.has_hard_failure and result.score >= 0.7

    return result
