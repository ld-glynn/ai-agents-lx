"""Evaluation gate — decides whether a candidate agent gets deployed.

Two-stage gate (from elliot-eval):
  1. Screening: Must pass ALL cases with zero hard failures.
     Tests basic contract compliance — can the agent produce valid output?
  2. Gold: Must pass >= threshold with zero hard failures.
     Tests output quality — is the agent good enough to deploy?

The Hiring Manager calls screen_candidate() before adding an agent to the roster.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from psm.schemas.agent import AgentNewHire
from psm.schemas.hypothesis import Hypothesis
from psm.schemas.pattern import Pattern
from psm.eval.runner import EvalRun, run_eval


def _log(msg: str) -> None:
    print(f"[gate] {msg}", file=sys.stderr)


@dataclass
class GateResult:
    """Whether a candidate agent passed the gate."""

    agent_id: str
    agent_name: str
    passed: bool
    stage: str                  # "screening" or "gold"
    pass_rate: float
    hard_failures: int
    avg_score: float
    reason: str                 # Why it passed or failed
    eval_run: EvalRun


# --- Thresholds ---
SCREENING_PASS_RATE = 1.0      # Must pass 100% of screening cases
GOLD_PASS_RATE = 0.85           # Must pass >= 85% of gold cases
GOLD_MIN_SCORE = 0.7            # Average score must be >= 0.7


def screen_candidate(
    agent: AgentNewHire,
    hypotheses: list[Hypothesis],
    pattern: Pattern,
    model: str | None = None,
) -> GateResult:
    """Run screening evaluation. Must pass all cases with zero hard failures.

    This is the minimum bar — can the agent produce valid, non-trivial output
    for each of its skills?
    """
    _log(f"Screening candidate: {agent.name} ({agent.agent_id})")

    eval_run = run_eval(
        agent=agent,
        hypotheses=hypotheses,
        pattern=pattern,
        stage="screening",
        model=model,
    )

    passed = (
        eval_run.total > 0
        and eval_run.pass_rate >= SCREENING_PASS_RATE
        and eval_run.hard_failure_count == 0
    )

    if passed:
        reason = f"Passed all {eval_run.total} screening cases"
    elif eval_run.total == 0:
        reason = "No test cases generated"
    elif eval_run.hard_failure_count > 0:
        reason = f"Hard failures: {eval_run.hard_failure_count} (must be 0)"
    else:
        reason = f"Pass rate {eval_run.pass_rate:.0%} < {SCREENING_PASS_RATE:.0%} required"

    result = GateResult(
        agent_id=agent.agent_id,
        agent_name=agent.name,
        passed=passed,
        stage="screening",
        pass_rate=eval_run.pass_rate,
        hard_failures=eval_run.hard_failure_count,
        avg_score=eval_run.avg_score,
        reason=reason,
        eval_run=eval_run,
    )

    status = "HIRED" if passed else "REJECTED"
    _log(f"  {status}: {reason}")
    for line in eval_run.summary_lines():
        _log(f"    {line}")

    return result


def evaluate_gold(
    agent: AgentNewHire,
    hypotheses: list[Hypothesis],
    pattern: Pattern,
    model: str | None = None,
) -> GateResult:
    """Run gold evaluation. Must pass >= threshold with zero hard failures.

    Gold is a higher bar than screening — tests whether the agent produces
    genuinely useful output, not just valid output.
    """
    _log(f"Gold evaluation: {agent.name} ({agent.agent_id})")

    # Gate: must pass screening first
    screening = screen_candidate(agent, hypotheses, pattern, model=model)
    if not screening.passed:
        _log(f"  Skipping gold — failed screening: {screening.reason}")
        return GateResult(
            agent_id=agent.agent_id,
            agent_name=agent.name,
            passed=False,
            stage="gold",
            pass_rate=0.0,
            hard_failures=screening.hard_failures,
            avg_score=0.0,
            reason=f"Failed screening gate: {screening.reason}",
            eval_run=screening.eval_run,
        )

    eval_run = run_eval(
        agent=agent,
        hypotheses=hypotheses,
        pattern=pattern,
        stage="gold",
        model=model,
    )

    passed = (
        eval_run.total > 0
        and eval_run.pass_rate >= GOLD_PASS_RATE
        and eval_run.hard_failure_count == 0
        and eval_run.avg_score >= GOLD_MIN_SCORE
    )

    if passed:
        reason = (
            f"Passed {eval_run.passed}/{eval_run.total} gold cases "
            f"(avg score: {eval_run.avg_score:.2f})"
        )
    elif eval_run.hard_failure_count > 0:
        reason = f"Hard failures in gold: {eval_run.hard_failure_count}"
    elif eval_run.avg_score < GOLD_MIN_SCORE:
        reason = f"Avg score {eval_run.avg_score:.2f} < {GOLD_MIN_SCORE} required"
    else:
        reason = f"Pass rate {eval_run.pass_rate:.0%} < {GOLD_PASS_RATE:.0%} required"

    result = GateResult(
        agent_id=agent.agent_id,
        agent_name=agent.name,
        passed=passed,
        stage="gold",
        pass_rate=eval_run.pass_rate,
        hard_failures=eval_run.hard_failure_count,
        avg_score=eval_run.avg_score,
        reason=reason,
        eval_run=eval_run,
    )

    status = "QUALIFIED" if passed else "REJECTED"
    _log(f"  {status}: {reason}")

    return result
