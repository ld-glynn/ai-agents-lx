"""Orchestrator — pipeline coordinator.

Coordinates the discovery pipeline: CSV → Catalog → Patterns → Solvability →
Hypotheses → Hire + Screen → Generate Deployment Specs.

The pipeline discovers problems and designs agents. It does NOT execute agent work.
Deployment and invocation happen separately after human approval.
"""
from __future__ import annotations

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
from psm.eval.gate import screen_candidate
from psm.schemas.run_history import RunRecord
from psm.schemas.pipeline_config import PipelineConfig


def _log(msg: str) -> None:
    print(f"[orchestrator] {msg}", file=sys.stderr)


# Module-level ref to current run_id for progress updates
_current_run_id: str | None = None
_current_stage: str | None = None


def _progress(stage: str, message: str) -> None:
    """Log and persist a progress update for the current run."""
    global _current_stage
    _current_stage = stage
    _log(message)
    if _current_run_id:
        try:
            store.update_run_record(_current_run_id, {
                "current_stage": stage,
                "progress_message": message,
            })
        except Exception:
            pass  # Don't crash pipeline on progress write failure


def report_sub_progress(message: str) -> None:
    """Called by agents (cataloger, hiring_manager) to report batch-level progress.

    Updates the run record with a sub-stage message while keeping the current stage.
    """
    if _current_run_id and _current_stage:
        full_msg = f"Stage {_current_stage}: {message}"
        _log(f"  {message}")
        try:
            store.update_run_record(_current_run_id, {
                "progress_message": full_msg,
            })
        except Exception:
            pass


_STAGE_ORDER = ["catalog", "patterns", "solvability", "hypotheses", "hire", "specs", "execute"]


def run_pipeline(
    stage: str | None = None,
    start_stage: str | None = None,
    model: str = "claude-sonnet-4-20250514",
    solver_model: str = "claude-sonnet-4-20250514",
    with_integrations: bool = False,
    skip_solvability: bool = False,
    config: PipelineConfig | None = None,
) -> dict:
    """Run the full PSM pipeline (or up to a specific stage).

    Args:
        stage: Stop after this stage. None = run all.
        start_stage: Skip stages before this one (load their output from disk).
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
    global _current_run_id
    run_id = f"run-{datetime.now().strftime('%Y-%m-%dT%H-%M-%S')}"
    _current_run_id = run_id
    snapshot_path = store.create_snapshot(run_id)
    _log(f"Run {run_id} — snapshot saved to {snapshot_path}")

    # Determine which stages to skip
    skip_before = set()
    if start_stage and start_stage in _STAGE_ORDER:
        start_idx = _STAGE_ORDER.index(start_stage)
        skip_before = set(_STAGE_ORDER[:start_idx])
        _log(f"Skipping stages before '{start_stage}': {skip_before or 'none'}")

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
                integration_extracted = structure_records(all_records, model=model)
                integration_problems = [ep.problem for ep in integration_extracted]
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
      if "catalog" in skip_before:
        _log("Stage 1: Catalog — skipped (loading from disk)")
        summary["catalog_count"] = len(store.read_catalog())
        summary["stages_completed"].append("catalog")
      else:
        _progress("catalog", "Stage 1: Loading problems...")
        # Load from discovered problems (Find Problems) + CSV fallback
        raw_problems = store.read_discovered_problems()
        if not raw_problems:
            _log("  No discovered problems found, falling back to CSV...")
            raw_problems = load_problems()
        else:
            _log(f"  Loaded {len(raw_problems)} discovered problems")
        if integration_problems:
            raw_problems.extend(integration_problems)
        _log(f"  Total: {len(raw_problems)} raw problems")

        total_batches = (len(raw_problems) + 19) // 20
        _progress("catalog", f"Stage 1: Cataloging {len(raw_problems)} problems in {total_batches} batches...")
        existing = store.read_catalog()
        catalog_model = config.cataloger.model or model
        catalog = run_cataloger(raw_problems, existing_catalog=existing, model=catalog_model, config=config.cataloger.model_dump())

        # Propagate provenance from raw problems to catalog entries.
        # Match by ID first, then fall back to title matching (the cataloger
        # may assign new IDs like P-001 while raw problems have INT-wisdom-001).
        raw_by_id = {p.id: p for p in raw_problems}
        raw_by_title = {}
        for p in raw_problems:
            key = p.title.lower().strip().replace("title: ", "")
            raw_by_title[key] = p

        matched = 0
        for entry in catalog:
            src = raw_by_id.get(entry.problem_id)
            if not src:
                src = raw_by_title.get(entry.title.lower().strip())
            if src:
                entry.source_record_ids = src.source_record_ids
                entry.agent_idea = src.agent_idea
                entry.upstream_sources = src.upstream_sources
                matched += 1
        _log(f"  Provenance matched: {matched}/{len(catalog)} catalog entries")

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
      if "patterns" in skip_before:
        _log("Stage 2: Patterns — skipped (loading from disk)")
        summary["pattern_count"] = len(store.read_patterns())
        summary["stages_completed"].append("patterns")
      else:
        _progress("patterns", "Stage 2: Analyzing patterns...")
        catalog = store.read_catalog()
        pattern_model = config.pattern_analyzer.model or model
        patterns, themes = run_pattern_analyzer(catalog, model=pattern_model, config=config.pattern_analyzer.model_dump())

        # Propagate provenance from catalog entries to patterns
        cat_by_id = {e.problem_id: e for e in catalog}
        for pat in patterns:
            all_srcs: list[str] = []
            all_upstream: set[str] = set()
            all_ideas: list[str] = []
            for pid in pat.problem_ids:
                ce = cat_by_id.get(pid)
                if ce:
                    all_srcs.extend(ce.source_record_ids)
                    all_upstream.update(ce.upstream_sources)
                    if ce.agent_idea:
                        all_ideas.append(ce.agent_idea)
            pat.source_record_ids = list(set(all_srcs))
            pat.upstream_sources = sorted(all_upstream)
            pat.agent_ideas = all_ideas[:10]  # cap to avoid bloat

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
    if not skip_solvability and "solvability" not in skip_before:
        try:
            _progress("solvability", "Stage 2.5: Evaluating pattern solvability...")
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
      if "hypotheses" in skip_before:
        _log("Stage 3: Hypotheses — skipped (loading from disk)")
        summary["hypothesis_count"] = len(store.read_hypotheses())
        summary["stages_completed"].append("hypotheses")
      else:
        _progress("hypotheses", "Stage 3: Generating hypotheses...")
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
      if "hire" in skip_before:
        _log("Stage 4: Hire — skipped (loading from disk)")
        summary["stages_completed"].append("hire")
      else:
        _progress("hire", "Stage 4: Hiring Agent New Hires — generating candidates...")
        patterns = store.read_patterns()
        hypotheses = store.read_hypotheses()
        _progress("hire", f"Stage 4: Designing agents for {len(patterns)} patterns ({len(hypotheses)} hypotheses)...")
        candidates = run_hiring_manager(patterns, hypotheses, model=model, config=config.hiring_manager.model_dump())
        _progress("hire", f"Stage 4: {len(candidates)} candidates proposed. Screening...")

        pat_by_id = {p.pattern_id: p for p in patterns}
        qualified, rejected = [], []
        for i, candidate in enumerate(candidates):
            _progress("hire", f"Stage 4: Screening {i+1}/{len(candidates)} — {candidate.name}...")
            pat = pat_by_id.get(candidate.pattern_id)
            if not pat:
                rejected.append(candidate)
                continue
            gate_result = screen_candidate(candidate, hypotheses, pat, model=solver_model)
            if gate_result.passed:
                qualified.append(candidate)
                _progress("hire", f"Stage 4: {candidate.name} — PASSED screening")
            else:
                rejected.append(candidate)
                _progress("hire", f"Stage 4: {candidate.name} — REJECTED: {gate_result.reason[:60]}")
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
