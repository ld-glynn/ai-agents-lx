from __future__ import annotations

"""Orchestrator — pipeline coordinator.

Coordinates the discovery pipeline: CSV → Catalog → Patterns → Solvability →
Hypotheses → Hire + Screen → Generate Deployment Specs.

The pipeline discovers problems and designs agents. It does NOT execute agent work.
Deployment and invocation happen separately after human approval.
"""

import sys
from datetime import datetime

from psm.tools.csv_reader import load_problems
from psm.tools.data_store import store
from psm.agents.structurer import structure_records
from psm.agents.cataloger import run_cataloger
from psm.agents.pattern_analyzer import run_pattern_analyzer
from psm.agents.solvability_evaluator import run_solvability_evaluator
from psm.agents.hypothesis_gen import run_hypothesis_generator
from psm.agents.hiring_manager import run_hiring_manager
from psm.agents.spec_generator import generate_deployment_specs
from psm.agents.solvers.base import run_skill
from psm.eval.gate import screen_candidate
from psm.schemas.run_history import RunRecord
from psm.schemas.pipeline_config import PipelineConfig


def _log(msg: str) -> None:
    print(f"[orchestrator] {msg}", file=sys.stderr)


def run_pipeline(
    stage: str | None = None,
    model: str = "claude-sonnet-4-20250514",
    solver_model: str = "claude-sonnet-4-20250514",
    with_integrations: bool = False,
    skip_solvability: bool = False,
    config: PipelineConfig | None = None,
) -> dict:
    """Run the full PSM pipeline (or up to a specific stage).

    Args:
        stage: Stop after this stage. None = run all.
        model: Model for Tier 1 engine agents.
        solver_model: Model override for Tier 2 new hires executing skills.
        with_integrations: Run Stage 0 (sync + structure) before pipeline.
        skip_solvability: Skip the solvability evaluation stage.
        config: Per-stage configuration. If None, loads from disk or uses defaults.

    Returns:
        Summary dict with counts, run_id, status, and error info if applicable.
    """
    # Load config
    if config is None:
        config = store.read_pipeline_config()

    # Create run record and snapshot
    run_id = f"run-{datetime.now().strftime('%Y-%m-%dT%H-%M-%S')}"
    snapshot_path = store.create_snapshot(run_id)
    _log(f"Run {run_id} — snapshot saved to {snapshot_path}")

    run_record = RunRecord(
        run_id=run_id,
        snapshot_path=str(snapshot_path),
        config_used=config.model_dump(),
    )
    store.append_run_record(run_record)

    summary: dict = {"stages_completed": [], "run_id": run_id, "status": "running"}

    def _fail(stage_name: str, error: Exception) -> dict:
        """Record a stage failure and return the summary."""
        _log(f"Stage {stage_name} FAILED: {error}")
        summary["status"] = "partial" if summary["stages_completed"] else "failed"
        summary["error_stage"] = stage_name
        summary["error_message"] = str(error)
        store.update_run_record(run_id, {
            "status": summary["status"],
            "error_stage": stage_name,
            "error_message": str(error),
            "stage_reached": stage_name,
            "stages_completed": summary["stages_completed"],
            "summary": summary,
        })
        return summary

    # --- Stage 0: Sync & Structure (optional) ---
    if with_integrations:
        try:
            _log("Stage 0: Syncing integration sources...")
            from psm.integrations import get_all_adapters

            adapters = get_all_adapters(mock=True)
            all_records = []
            for adapter in adapters:
                records = adapter.fetch_records()
                _log(f"  {adapter.source_type.value}: fetched {len(records)} records")
                all_records.extend(records)
                status = adapter.get_status()
                status.last_sync_at = datetime.now()
                status.record_count = len(records)
                store.write_sync_status([status])

            integration_problems = []
            if all_records:
                new_count = store.append_ingestion(all_records)
                _log(f"  Persisted {new_count} new ingestion records")
                integration_problems = structure_records(all_records, model=model)
                _log(f"  Extracted {len(integration_problems)} problems")

            summary["ingestion_count"] = len(all_records)
            summary["integration_problems"] = len(integration_problems)
            summary["stages_completed"].append("sync")
        except Exception as e:
            return _fail("sync", e)
    else:
        integration_problems = []

    # --- Stage 1: Catalog ---
    try:
        _log("Stage 1: Loading problems from CSV...")
        raw_problems = load_problems()
        if integration_problems:
            raw_problems.extend(integration_problems)
        _log(f"  Loaded {len(raw_problems)} raw problems")

        existing = store.read_catalog()
        catalog = run_cataloger(raw_problems, existing_catalog=existing, model=model, config=config.cataloger.model_dump())
        count = store.write_catalog(catalog)
        _log(f"  Cataloged {count} problems")
        summary["catalog_count"] = count
        summary["stages_completed"].append("catalog")
    except Exception as e:
        return _fail("catalog", e)

    if stage == "catalog":
        summary["status"] = "success"
        store.update_run_record(run_id, {"status": "success", "stages_completed": summary["stages_completed"], "stage_reached": "catalog", "summary": summary})
        return summary

    # --- Stage 2: Patterns ---
    try:
        _log("Stage 2: Analyzing patterns...")
        catalog = store.read_catalog()
        patterns, themes = run_pattern_analyzer(catalog, model=model, config=config.pattern_analyzer.model_dump())
        result = store.write_patterns(patterns, themes)
        _log(f"  Found {result['patterns']} patterns in {result['themes']} themes")
        summary["pattern_count"] = result["patterns"]
        summary["theme_count"] = result["themes"]
        summary["stages_completed"].append("patterns")
    except Exception as e:
        return _fail("patterns", e)

    if stage == "patterns":
        summary["status"] = "success"
        store.update_run_record(run_id, {"status": "success", "stages_completed": summary["stages_completed"], "stage_reached": "patterns", "summary": summary})
        return summary

    # --- Stage 2.5: Solvability Evaluation (optional) ---
    if not skip_solvability:
        try:
            _log("Stage 2.5: Evaluating pattern solvability...")
            patterns = store.read_patterns()
            report = run_solvability_evaluator(patterns, model=model, config=config.solvability.model_dump())
            store.write_solvability(report)
            _log(f"  Results: {report.passed} pass, {report.flagged} flagged, {report.dropped} dropped")
            summary["solvability_passed"] = report.passed
            summary["solvability_flagged"] = report.flagged
            summary["solvability_dropped"] = report.dropped
            summary["stages_completed"].append("solvability")

            passed_ids = {r.pattern_id for r in report.results if r.status.value == "pass"}
            patterns = [p for p in patterns if p.pattern_id in passed_ids]
            _log(f"  {len(patterns)} patterns proceeding to hypothesis generation")
        except Exception as e:
            return _fail("solvability", e)

        if stage == "solvability":
            summary["status"] = "success"
            store.update_run_record(run_id, {"status": "success", "stages_completed": summary["stages_completed"], "stage_reached": "solvability", "summary": summary})
            return summary
    else:
        _log("Stage 2.5: Solvability evaluation skipped (--skip-solvability)")

    # --- Stage 3: Hypotheses ---
    try:
        _log("Stage 3: Generating hypotheses...")
        if skip_solvability:
            patterns = store.read_patterns()
        themes = store.read_themes()
        hypotheses = run_hypothesis_generator(patterns, themes, model=model, config=config.hypothesis_gen.model_dump())
        count = store.write_hypotheses(hypotheses)
        _log(f"  Generated {count} hypotheses")
        summary["hypothesis_count"] = count
        summary["stages_completed"].append("hypotheses")
    except Exception as e:
        return _fail("hypotheses", e)

    if stage == "hypotheses":
        summary["status"] = "success"
        store.update_run_record(run_id, {"status": "success", "stages_completed": summary["stages_completed"], "stage_reached": "hypotheses", "summary": summary})
        return summary

    # --- Stage 4: Hire + Screen ---
    try:
        _log("Stage 4: Hiring Agent New Hires...")
        patterns = store.read_patterns()
        hypotheses = store.read_hypotheses()
        candidates = run_hiring_manager(patterns, hypotheses, model=model, config=config.hiring_manager.model_dump())
        _log(f"  Hiring Manager proposed {len(candidates)} candidates")

        pat_by_id = {p.pattern_id: p for p in patterns}
        qualified, rejected = [], []
        for candidate in candidates:
            pat = pat_by_id.get(candidate.pattern_id)
            if not pat:
                rejected.append(candidate)
                continue
            gate_result = screen_candidate(candidate, hypotheses, pat, model=solver_model)
            if gate_result.passed:
                qualified.append(candidate)
            else:
                rejected.append(candidate)
                _log(f"  REJECTED {candidate.name}: {gate_result.reason}")

        count = store.write_new_hires(qualified)
        _log(f"  Hired {len(qualified)} agents ({len(rejected)} rejected)")
        summary["new_hire_count"] = count
        summary["candidates_proposed"] = len(candidates)
        summary["candidates_rejected"] = len(rejected)
        summary["stages_completed"].append("hire")
    except Exception as e:
        return _fail("hire", e)

    if stage == "hire":
        summary["status"] = "success"
        store.update_run_record(run_id, {"status": "success", "stages_completed": summary["stages_completed"], "stage_reached": "hire", "summary": summary})
        return summary

    # --- Stage 5: Generate Deployment Specs ---
    try:
        _log("Stage 5: Generating deployment specifications...")
        new_hires = store.read_new_hires()
        hypotheses = store.read_hypotheses()
        patterns = store.read_patterns()

        # Update agent lifecycle states
        for agent in new_hires:
            agent.lifecycle_state = "screened"
        store.write_new_hires(new_hires)

        specs = generate_deployment_specs(new_hires, patterns, hypotheses)
        spec_count = store.write_deployment_specs(specs)

        # Update agents with spec references
        spec_by_agent = {s.agent_id: s.spec_id for s in specs}
        for agent in new_hires:
            if agent.agent_id in spec_by_agent:
                agent.lifecycle_state = "proposed"
                agent.deployment_spec_id = spec_by_agent[agent.agent_id]
        store.write_new_hires(new_hires)

        _log(f"  Generated {spec_count} deployment specs (awaiting human approval)")
        summary["deployment_spec_count"] = spec_count
        summary["stages_completed"].append("spec")
    except Exception as e:
        return _fail("spec", e)

    # Success
    summary["status"] = "success"
    store.update_run_record(run_id, {
        "status": "success",
        "stages_completed": summary["stages_completed"],
        "stage_reached": summary["stages_completed"][-1] if summary["stages_completed"] else "",
        "summary": summary,
    })
    return summary
