from __future__ import annotations

"""Test case generator — creates screening and gold test cases from pipeline data.

For each skill type, generates cases with known inputs (hypothesis + pattern)
and expected output characteristics. These become the rubric the candidate
agent is evaluated against.
"""

from dataclasses import dataclass, field

from psm.schemas.agent import AgentNewHire, AgentSkill, SkillType
from psm.schemas.hypothesis import Hypothesis
from psm.schemas.pattern import Pattern


@dataclass
class ExpectedOutput:
    """What a passing skill output should look like."""

    min_content_length: int = 200           # Meaningful content, not a stub
    must_reference_ids: list[str] = field(default_factory=list)  # IDs that must appear in output
    must_contain_keywords: list[str] = field(default_factory=list)  # Domain terms expected
    requires_next_steps: bool = True        # next_steps should be non-empty
    min_next_steps: int = 1


@dataclass
class TestCase:
    """A single evaluation case for a candidate agent."""

    case_id: str
    stage: str                              # "screening" or "gold"
    agent: AgentNewHire
    skill: AgentSkill
    hypothesis: Hypothesis
    pattern: Pattern
    expected: ExpectedOutput

    @property
    def description(self) -> str:
        return (
            f"[{self.stage}] {self.agent.name} using {self.skill.skill_type.value} "
            f"for {self.hypothesis.hypothesis_id}"
        )


def _keywords_for_skill(skill_type: SkillType, hypothesis: Hypothesis, pattern: Pattern) -> list[str]:
    """Extract domain keywords the output should reference."""
    keywords: list[str] = []

    # Pattern name parts (split multi-word names)
    for word in pattern.name.lower().split():
        if len(word) > 3:
            keywords.append(word)

    # Hypothesis action verbs from the statement
    statement_lower = hypothesis.statement.lower()
    if "if we " in statement_lower:
        action_part = statement_lower.split("if we ", 1)[1].split(",")[0]
        for word in action_part.split():
            if len(word) > 3:
                keywords.append(word)

    # Keep unique, limit to most relevant
    seen: set[str] = set()
    unique: list[str] = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)
    return unique[:5]


def _min_content_for_skill(skill_type: SkillType) -> int:
    """Different skill types have different expected output lengths."""
    return {
        SkillType.RECOMMEND: 150,
        SkillType.ACTION_PLAN: 300,
        SkillType.PROCESS_DOC: 400,
        SkillType.INVESTIGATE: 250,
    }.get(skill_type, 200)


def generate_test_cases(
    agent: AgentNewHire,
    hypotheses: list[Hypothesis],
    pattern: Pattern,
    stage: str = "screening",
) -> list[TestCase]:
    """Generate test cases for a candidate agent.

    Screening cases test basic contract compliance — can the agent produce
    valid, non-trivial output for each of its skills?

    Gold cases (future) will test output quality against curated rubrics.
    """
    hyp_by_id = {h.hypothesis_id: h for h in hypotheses}
    cases: list[TestCase] = []

    for i, skill in enumerate(agent.skills):
        hypothesis = hyp_by_id.get(skill.hypothesis_id)
        if not hypothesis:
            continue

        # IDs the output should reference (proves it read the input)
        must_ref = [
            hypothesis.hypothesis_id,
            pattern.pattern_id,
        ]
        # Add at least one problem ID from the pattern
        if pattern.problem_ids:
            must_ref.append(pattern.problem_ids[0])

        keywords = _keywords_for_skill(skill.skill_type, hypothesis, pattern)

        expected = ExpectedOutput(
            min_content_length=_min_content_for_skill(skill.skill_type),
            must_reference_ids=must_ref,
            must_contain_keywords=keywords,
            requires_next_steps=True,
            min_next_steps=2 if skill.skill_type in (SkillType.ACTION_PLAN, SkillType.INVESTIGATE) else 1,
        )

        cases.append(TestCase(
            case_id=f"{stage}-{agent.agent_id}-{skill.skill_type.value}-{i+1}",
            stage=stage,
            agent=agent,
            skill=skill,
            hypothesis=hypothesis,
            pattern=pattern,
            expected=expected,
        ))

    return cases
