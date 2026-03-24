from __future__ import annotations

"""Orchestrator — the New Hire.

Coordinates the full pipeline: CSV → Catalog → Patterns → Hypotheses → Routes → Solve.
Each stage runs sequentially. Outputs are persisted to JSON between stages.
"""

import sys

from psm.tools.csv_reader import load_problems
from psm.tools.data_store import store
from psm.agents.cataloger import run_cataloger
from psm.agents.pattern_analyzer import run_pattern_analyzer
from psm.agents.hypothesis_gen import run_hypothesis_generator
from psm.agents.solver_router import run_solver_router
from psm.agents.solvers.base import run_solver


def _log(msg: str) -> None:
    print(f"[orchestrator] {msg}", file=sys.stderr)


def run_pipeline(
    stage: str | None = None,
    model: str = "claude-sonnet-4-20250514",
    solver_model: str = "claude-haiku-4-5-20251001",
) -> dict:
    """Run the full PSM pipeline (or up to a specific stage).

    Args:
        stage: Stop after this stage. None = run all.
               Options: "catalog", "patterns", "hypotheses", "routes", "solve"
        model: Model for analytical agents.
        solver_model: Model for solver agents (cheaper).

    Returns:
        Summary dict with counts and key findings.
    """
    summary: dict = {"stages_completed": []}

    # --- Stage 1: Catalog ---
    _log("Stage 1: Loading problems from CSV...")
    raw_problems = load_problems()
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
    catalog = store.read_catalog()  # re-read to ensure we have the persisted version
    patterns, themes = run_pattern_analyzer(catalog, model=model)
    result = store.write_patterns(patterns, themes)
    _log(f"  Found {result['patterns']} patterns in {result['themes']} themes")
    summary["pattern_count"] = result["patterns"]
    summary["theme_count"] = result["themes"]
    summary["stages_completed"].append("patterns")

    if stage == "patterns":
        return summary

    # --- Stage 3: Hypotheses ---
    _log("Stage 3: Generating hypotheses...")
    patterns = store.read_patterns()
    themes = store.read_themes()
    hypotheses = run_hypothesis_generator(patterns, themes, model=model)
    count = store.write_hypotheses(hypotheses)
    _log(f"  Generated {count} hypotheses")
    summary["hypothesis_count"] = count
    summary["stages_completed"].append("hypotheses")

    if stage == "hypotheses":
        return summary

    # --- Stage 4: Route ---
    _log("Stage 4: Routing to solvers...")
    hypotheses = store.read_hypotheses()
    mappings = run_solver_router(hypotheses, model=model)
    count = store.write_solution_map(mappings)
    _log(f"  Created {count} solution mappings")
    summary["mapping_count"] = count
    summary["stages_completed"].append("routes")

    if stage == "routes":
        return summary

    # --- Stage 5: Solve ---
    _log("Stage 5: Running solver agents...")
    mappings = store.read_solution_map()
    hypotheses = store.read_hypotheses()
    patterns = store.read_patterns()

    # Build lookup dicts
    hyp_by_id = {h.hypothesis_id: h for h in hypotheses}
    pat_by_id = {p.pattern_id: p for p in patterns}

    outputs = []
    for mapping in mappings:
        hyp = hyp_by_id.get(mapping.hypothesis_id)
        if not hyp:
            _log(f"  WARNING: Hypothesis {mapping.hypothesis_id} not found, skipping")
            continue

        pat = pat_by_id.get(hyp.pattern_id)
        if not pat:
            _log(f"  WARNING: Pattern {hyp.pattern_id} not found, skipping")
            continue

        _log(f"  Solving {mapping.mapping_id} ({mapping.solver_type.value})...")
        output = run_solver(mapping, hyp, pat, model=solver_model)
        outputs.append(output)

    store.write_solver_outputs(outputs)
    _log(f"  Produced {len(outputs)} deliverables")
    summary["solver_output_count"] = len(outputs)
    summary["stages_completed"].append("solve")

    return summary
