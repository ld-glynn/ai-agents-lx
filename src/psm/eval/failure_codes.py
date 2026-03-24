from __future__ import annotations

"""Failure codes — hard failures block deployment, soft failures are warnings.

Borrowed from elliot-eval's hard/soft classification pattern.
Hard failures mean the agent fundamentally can't do the job.
Soft failures mean it can, but the output quality needs attention.
"""

from enum import Enum


class FailureCode(str, Enum):
    """Every evaluation failure gets exactly one code."""

    # --- Hard failures: agent is disqualified ---
    JSON_PARSE_ERROR = "JSON_PARSE_ERROR"          # Output wasn't valid JSON
    SCHEMA_INVALID = "SCHEMA_INVALID"              # Output failed Pydantic validation
    HALLUCINATED_REF = "HALLUCINATED_REF"          # Referenced IDs not in input context
    MISSING_CONTENT = "MISSING_CONTENT"            # Content field empty or trivial
    WRONG_SKILL_TYPE = "WRONG_SKILL_TYPE"          # Output doesn't match requested skill
    ADAPTER_ERROR = "ADAPTER_ERROR"                # LLM call failed
    TIMEOUT = "TIMEOUT"                            # Exceeded time limit

    # --- Soft failures: warnings, don't block deployment ---
    WEAK_NEXT_STEPS = "WEAK_NEXT_STEPS"            # next_steps missing or generic
    SHORT_CONTENT = "SHORT_CONTENT"                # Content exists but suspiciously short
    TITLE_MISMATCH = "TITLE_MISMATCH"              # Title doesn't relate to hypothesis
    CONFIDENCE_MISMATCH = "CONFIDENCE_MISMATCH"    # High confidence claim with weak evidence
    GENERIC_OUTPUT = "GENERIC_OUTPUT"              # Output doesn't reference specific problems


# Hard failures always disqualify
HARD_FAILURES = {
    FailureCode.JSON_PARSE_ERROR,
    FailureCode.SCHEMA_INVALID,
    FailureCode.HALLUCINATED_REF,
    FailureCode.MISSING_CONTENT,
    FailureCode.WRONG_SKILL_TYPE,
    FailureCode.ADAPTER_ERROR,
    FailureCode.TIMEOUT,
}

SOFT_FAILURES = {code for code in FailureCode if code not in HARD_FAILURES}


def is_hard(code: FailureCode) -> bool:
    return code in HARD_FAILURES
