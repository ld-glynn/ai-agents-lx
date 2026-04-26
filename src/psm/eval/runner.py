"""Eval runner — invokes a candidate agent against test cases and collects results.

The runner calls the same skill executor (solvers/base.py) that production uses,
so we're testing the actual agent behavior, not a mock.
"""
from __future__ import annotations

import sys
import json
import time
from dataclasses import dataclass, field

from psm.schemas.agent import AgentNewHire
from psm.schemas.hypothesis import Hypothesis
from psm.schemas.pattern import Pattern
from psm.eval.test_gen import generate_test_cases
from psm.eval.scorer import ScoreResult, score_case
from psm.eval.failure_codes import FailureCode


def _log(msg: str) -> None:
    print(f"[eval] {msg}", file=sys.stderr)


@dataclass
class EvalRun:
    """Results of running all test cases for one candidate agent."""

    agent_id: str
    agent_name: str
    stage: str
    results: list[ScoreResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total > 0 else 0.0

    @property
    def hard_failure_count(self) -> int:
        return sum(1 for r in self.results if r.has_hard_failure)

    @property
    def avg_score(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.score for r in self.results) / len(self.results)

    @property
    def failure_summary(self) -> dict[str, int]:
        """Count of each failure code across all results."""
        counts: dict[str, int] = {}
        for r in self.results:
            for f in r.failures:
                counts[f.value] = counts.get(f.value, 0) + 1
        return counts

    def summary_lines(self) -> list[str]:
        """Human-readable summary."""
        lines = [
            f"Agent: {self.agent_name} ({self.agent_id})",
            f"Stage: {self.stage}",
            f"Cases: {self.total} | Passed: {self.passed} | Failed: {self.failed}",
            f"Pass rate: {self.pass_rate:.0%} | Avg score: {self.avg_score:.2f}",
            f"Hard failures: {self.hard_failure_count}",
        ]
        if self.failure_summary:
            lines.append("Failure codes:")
            for code, count in sorted(self.failure_summary.items()):
                lines.append(f"  {code}: {count}")
        return lines


def run_eval(
    agent: AgentNewHire,
    hypotheses: list[Hypothesis],
    pattern: Pattern,
    stage: str = "screening",
    model: str | None = None,
    timeout_seconds: float = 60.0,
) -> EvalRun:
    """Run evaluation for a single candidate agent.

    Generates test cases, invokes the agent's skill executor for each,
    and scores the results.
    """
    from psm.agents.solvers.base import run_skill

    cases = generate_test_cases(agent, hypotheses, pattern, stage=stage)
    eval_run = EvalRun(
        agent_id=agent.agent_id,
        agent_name=agent.name,
        stage=stage,
    )

    if not cases:
        _log(f"  No test cases generated for {agent.name}")
        return eval_run

    _log(f"  Running {len(cases)} {stage} cases for {agent.name}...")

    for case_idx, case in enumerate(cases):
        _log(f"    Case {case.case_id}: {case.skill.skill_type.value}...")
        try:
            from psm.agents.orchestrator import report_sub_progress
            report_sub_progress(f"Screening {agent.name}: test {case_idx+1}/{len(cases)} ({case.skill.skill_type.value})")
        except ImportError:
            pass

        start = time.time()
        try:
            output = run_skill(
                agent=case.agent,
                skill=case.skill,
                hypothesis=case.hypothesis,
                pattern=case.pattern,
                model=model,
            )
            elapsed = time.time() - start

            # Reconstruct the raw JSON text to score it
            raw_text = json.dumps({
                "title": output.title,
                "content": output.content,
                "next_steps": output.next_steps,
            })

            result = score_case(case, raw_text)
            _log(
                f"    {'PASS' if result.passed else 'FAIL'} "
                f"(score={result.score:.2f}, {elapsed:.1f}s)"
            )

        except json.JSONDecodeError as e:
            result = ScoreResult(
                case_id=case.case_id,
                passed=False,
                score=0.0,
                failures=[FailureCode.JSON_PARSE_ERROR],
                failure_details=[f"Agent returned unparseable output: {e}"],
            )
            _log("    FAIL (JSON parse error)")

        except TimeoutError:
            result = ScoreResult(
                case_id=case.case_id,
                passed=False,
                score=0.0,
                failures=[FailureCode.TIMEOUT],
                failure_details=[f"Exceeded {timeout_seconds}s timeout"],
            )
            _log("    FAIL (timeout)")

        except Exception as e:
            result = ScoreResult(
                case_id=case.case_id,
                passed=False,
                score=0.0,
                failures=[FailureCode.ADAPTER_ERROR],
                failure_details=[f"Agent invocation failed: {type(e).__name__}: {e}"],
            )
            _log(f"    FAIL (adapter error: {e})")

        eval_run.results.append(result)

        # Screening: fail fast on hard failure
        if stage == "screening" and result.has_hard_failure:
            _log("    Screening fail-fast: hard failure detected, stopping")
            break

    return eval_run
