from __future__ import annotations

"""Orchestrator — the New Hire.

Coordinates the full pipeline: CSV → Catalog → Patterns → Hypotheses → Hire → Execute Skills.
Each stage runs sequentially. Outputs are persisted to JSON between stages.

Three-tier agent model:
  Tier 1: Engine agents (Cataloger, Pattern Analyzer, Hypothesis Gen, Hiring Manager)
  Tier 2: Agent New Hires (created by Hiring Manager, one per pattern)
  Tier 3: Skills (executed by New Hires to produce deliverables)
"""

import sys

from psm.tools.csv_reader import load_problems
from psm.tools.data_store import store
from psm.agents.structurer import structure_records
from psm.agents.cataloger import run_cataloger
from psm.agents.pattern_analyzer import run_pattern_analyzer
from psm.agents.solvability_evaluator import run_solvability_evaluator
from psm.agents.hypothesis_gen import run_hypothesis_generator
from psm.agents.hiring_manager import run_hiring_manager
from psm.agents.solvers.base import run_skill
from psm.eval.gate import screen_candidate


def _log(msg: str) -> None:
    print(f"[orchestrator] {msg}", file=sys.stderr)


def run_pipeline(
    stage: str | None = None,
    model: str = "claude-sonnet-4-20250514",
    solver_model: str = "claude-sonnet-4-20250514",
    with_integrations: bool = False,
    skip_solvability: bool = False,
) -> dict:
    """Run the full PSM pipeline (or up to a specific stage).

    Args:
        stage: Stop after this stage. None = run all.
               Options: "catalog", "patterns", "hypotheses", "hire", "execute"
        model: Model for Tier 1 engine agents.
        solver_model: Model override for Tier 2 new hires executing skills.

    Returns:
        Summary dict with counts and key findings.
    """
    summary: dict = {"stages_completed": []}

    # --- Stage 0: Sync & Structure (optional) ---
    integration_problems = []
    if with_integrations:
        _log("Stage 0: Syncing integration sources...")
        from psm.integrations import get_all_adapters
        from datetime import datetime

        adapters = get_all_adapters(mock=True)
        all_records = []
        for adapter in adapters:
            records = adapter.fetch_records()
            _log(f"  {adapter.source_type.value}: fetched {len(records)} records")
            all_records.extend(records)

            # Update sync status
            status = adapter.get_status()
            status.last_sync_at = datetime.now()
            status.record_count = len(records)
            store.write_sync_status([status])

        if all_records:
            new_count = store.append_ingestion(all_records)
            _log(f"  Persisted {new_count} new ingestion records")

            _log("  Running Structurer agent...")
            integration_problems = structure_records(all_records, model=model)
            _log(f"  Extracted {len(integration_problems)} problems from integrations")

        summary["ingestion_count"] = len(all_records)
        summary["integration_problems"] = len(integration_problems)
        summary["stages_completed"].append("sync")

    # --- Stage 1: Catalog ---
    _log("Stage 1: Loading problems from CSV...")
    raw_problems = load_problems()
    # Merge integration-sourced problems
    if integration_problems:
        raw_problems.extend(integration_problems)
    _log(f"  Loaded {len(raw_problems)} raw problems")

    _log("  Running Cataloger agent...")
    existing = store.read_catalog()
    catalog = run_cataloger(raw_problems, existing_catalog=existing, model=model)
    count = store.write_catalog(catalog)
    _log(f"  Cataloged {count} problems")
    summary["catalog_count"] = count
    summary["stages_completed"].append("catalog")

    if stage == "catalog":
        return summary

    # --- Stage 2: Patterns ---
    _log("Stage 2: Analyzing patterns...")
    catalog = store.read_catalog()
    patterns, themes = run_pattern_analyzer(catalog, model=model)
    result = store.write_patterns(patterns, themes)
    _log(f"  Found {result['patterns']} patterns in {result['themes']} themes")
    summary["pattern_count"] = result["patterns"]
    summary["theme_count"] = result["themes"]
    summary["stages_completed"].append("patterns")

    if stage == "patterns":
        return summary

    # --- Stage 2.5: Solvability Evaluation (optional) ---
    if not skip_solvability:
        _log("Stage 2.5: Evaluating pattern solvability...")
        patterns = store.read_patterns()
        report = run_solvability_evaluator(patterns, model=model)
        store.write_solvability(report)
        _log(f"  Results: {report.passed} pass, {report.flagged} flagged, {report.dropped} dropped")
        summary["solvability_passed"] = report.passed
        summary["solvability_flagged"] = report.flagged
        summary["solvability_dropped"] = report.dropped
        summary["stages_completed"].append("solvability")

        # Filter: only pass patterns proceed. Flagged are held, dropped are excluded.
        passed_ids = {r.pattern_id for r in report.results if r.status.value == "pass"}
        patterns = [p for p in patterns if p.pattern_id in passed_ids]
        _log(f"  {len(patterns)} patterns proceeding to hypothesis generation")

        if stage == "solvability":
            return summary
    else:
        _log("Stage 2.5: Solvability evaluation skipped (--skip-solvability)")

    # --- Stage 3: Hypotheses ---
    _log("Stage 3: Generating hypotheses...")
    if skip_solvability:
        patterns = store.read_patterns()
    themes = store.read_themes()
    hypotheses = run_hypothesis_generator(patterns, themes, model=model)
    count = store.write_hypotheses(hypotheses)
    _log(f"  Generated {count} hypotheses")
    summary["hypothesis_count"] = count
    summary["stages_completed"].append("hypotheses")

    if stage == "hypotheses":
        return summary

    # --- Stage 4: Hire + Screen ---
    _log("Stage 4: Hiring Agent New Hires...")
    patterns = store.read_patterns()
    hypotheses = store.read_hypotheses()
    candidates = run_hiring_manager(patterns, hypotheses, model=model)
    _log(f"  Hiring Manager proposed {len(candidates)} candidates")

    pat_by_id = {p.pattern_id: p for p in patterns}

    # Screen each candidate before adding to roster
    qualified = []
    rejected = []
    for candidate in candidates:
        pat = pat_by_id.get(candidate.pattern_id)
        if not pat:
            _log(f"  WARNING: Pattern {candidate.pattern_id} not found for {candidate.name}, skipping")
            rejected.append(candidate)
            continue

        gate_result = screen_candidate(
            candidate, hypotheses, pat, model=solver_model
        )
        if gate_result.passed:
            qualified.append(candidate)
        else:
            rejected.append(candidate)
            _log(f"  REJECTED {candidate.name}: {gate_result.reason}")

    count = store.write_new_hires(qualified)
    _log(f"  Hired {len(qualified)} agents ({len(rejected)} rejected):")
    for agent in qualified:
        _log(f"    {agent.agent_id}: {agent.name} ({len(agent.skills)} skills)")
    summary["new_hire_count"] = count
    summary["candidates_proposed"] = len(candidates)
    summary["candidates_rejected"] = len(rejected)
    summary["stages_completed"].append("hire")

    if stage == "hire":
        return summary

    # --- Stage 5: Execute Skills (qualified agents only) ---
    _log("Stage 5: Qualified New Hires executing skills...")
    new_hires = store.read_new_hires()
    hypotheses = store.read_hypotheses()
    patterns = store.read_patterns()

    hyp_by_id = {h.hypothesis_id: h for h in hypotheses}
    pat_by_id = {p.pattern_id: p for p in patterns}

    outputs = []
    for agent in new_hires:
        pat = pat_by_id.get(agent.pattern_id)
        if not pat:
            _log(f"  WARNING: Pattern {agent.pattern_id} not found for {agent.name}, skipping")
            continue

        _log(f"  {agent.name} executing {len(agent.skills)} skills...")
        for skill in sorted(agent.skills, key=lambda s: s.priority):
            hyp = hyp_by_id.get(skill.hypothesis_id)
            if not hyp:
                _log(f"    WARNING: Hypothesis {skill.hypothesis_id} not found, skipping")
                continue

            _log(f"    Using skill: {skill.skill_type.value} for {skill.hypothesis_id}")
            output = run_skill(agent, skill, hyp, pat, model=solver_model)
            outputs.append(output)

    store.write_skill_outputs(outputs)
    _log(f"  Produced {len(outputs)} deliverables")
    summary["skill_output_count"] = len(outputs)
    summary["stages_completed"].append("execute")

    return summary
