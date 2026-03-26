"""FastAPI server — HTTP interface to the PSM pipeline.

Thin wrapper over the existing Python code. Each endpoint delegates to
store, orchestrator, or integration modules directly.

Run with: uvicorn psm.server:app --reload --port 8000
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from psm.config import settings
from psm.tools.data_store import store

app = FastAPI(title="PSM API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Request/Response models ---

class RunRequest(BaseModel):
    stage: Optional[str] = None
    model: str = "claude-sonnet-4-20250514"
    solver_model: str = "claude-sonnet-4-20250514"
    with_integrations: bool = False
    skip_solvability: bool = False
    config: Optional[dict] = None


class SyncRequest(BaseModel):
    source: str = "all"
    mock: bool = True
    model: str = "claude-sonnet-4-20250514"


# --- Endpoints ---

@app.get("/api/status")
def get_status():
    """Pipeline status — which stages have data and how many entries."""
    files = {
        "problems_csv": settings.problems_csv_path,
        "catalog": settings.catalog_path,
        "patterns": settings.patterns_path,
        "themes": settings.patterns_path.with_name("themes.json"),
        "hypotheses": settings.hypotheses_path,
        "new_hires": settings.data_dir / "new_hires.json",
        "skill_outputs": settings.data_dir / "skill_outputs.json",
        "ingestion": settings.ingestion_path,
    }

    result = {}
    for name, path in files.items():
        if path.exists():
            if path.suffix == ".csv":
                lines = len(path.read_text().strip().split("\n")) - 1
                result[name] = {"exists": True, "count": max(lines, 0)}
            else:
                data = json.loads(path.read_text())
                result[name] = {"exists": True, "count": len(data)}
        else:
            result[name] = {"exists": False, "count": 0}

    return result


STAGE_READERS = {
    "catalog": store.read_catalog,
    "patterns": store.read_patterns,
    "themes": store.read_themes,
    "hypotheses": store.read_hypotheses,
    "new-hires": store.read_new_hires,
    "skills": store.read_skill_outputs,
    "ingestion": store.read_ingestion,
    "eval": lambda: store._read_list(settings.data_dir / "eval_results.json", __import__("psm.schemas.agent", fromlist=["SkillOutput"]).SkillOutput) if (settings.data_dir / "eval_results.json").exists() else [],
    "outcomes": store.read_outcomes_log,
}


@app.get("/api/data/{stage}")
def get_data(stage: str):
    """Get pipeline data for a specific stage."""
    reader = STAGE_READERS.get(stage)
    if not reader:
        raise HTTPException(404, f"Unknown stage: {stage}. Available: {list(STAGE_READERS.keys())}")

    try:
        items = reader()
        return [item.model_dump(mode="json") for item in items]
    except Exception as e:
        raise HTTPException(500, f"Error reading {stage}: {str(e)}")


@app.post("/api/run")
def run_pipeline_endpoint(request: RunRequest):
    """Trigger a pipeline run. Blocks until complete."""
    from psm.agents.orchestrator import run_pipeline

    try:
        from psm.schemas.pipeline_config import PipelineConfig
        config = PipelineConfig.model_validate(request.config) if request.config else None
        summary = run_pipeline(
            stage=request.stage,
            model=request.model,
            solver_model=request.solver_model,
            with_integrations=request.with_integrations,
            skip_solvability=request.skip_solvability,
            config=config,
        )
        return {"status": "completed", "summary": summary}
    except Exception as e:
        raise HTTPException(500, f"Pipeline run failed: {str(e)}")


@app.post("/api/sync")
def sync_integrations(request: SyncRequest):
    """Sync integration sources and run structurer."""
    from psm.integrations import get_adapter, get_all_adapters
    from psm.agents.structurer import structure_records

    try:
        if request.source == "all":
            adapters = get_all_adapters(mock=request.mock)
        else:
            adapters = [get_adapter(request.source, mock=request.mock)]

        all_records = []
        source_counts = {}
        for adapter in adapters:
            records = adapter.fetch_records()
            source_counts[adapter.source_type.value] = len(records)
            all_records.extend(records)

            status = adapter.get_status()
            status.last_sync_at = datetime.now()
            status.record_count = len(records)

        new_count = 0
        problems = []
        if all_records:
            new_count = store.append_ingestion(all_records)
            problems = structure_records(all_records, model=request.model)

        return {
            "status": "completed",
            "source_counts": source_counts,
            "total_records": len(all_records),
            "new_records": new_count,
            "problems_extracted": len(problems),
            "problems": [{"id": p.id, "title": p.title} for p in problems],
        }
    except Exception as e:
        raise HTTPException(500, f"Sync failed: {str(e)}")


class ParseRequest(BaseModel):
    text: str
    model: str = "claude-sonnet-4-20250514"


@app.post("/api/parse")
def parse_text(request: ParseRequest):
    """Parse unstructured text into problem suggestions using the structurer agent."""
    from psm.schemas.ingestion import IngestionRecord, SourceType
    from psm.agents.structurer import structure_records

    try:
        # Wrap text as a single ingestion record
        record = IngestionRecord(
            record_id=f"PARSE-{int(datetime.now().timestamp())}",
            source=SourceType.MANUAL,
            raw_text=request.text,
        )
        problems = structure_records([record], model=request.model)
        return {
            "problems": [
                {
                    "id": p.id,
                    "title": p.title,
                    "description": p.description,
                    "reported_by": p.reported_by,
                    "domain": p.domain,
                    "tags": p.tags,
                }
                for p in problems
            ]
        }
    except Exception as e:
        raise HTTPException(500, f"Parse failed: {str(e)}")


@app.get("/api/integrations")
def get_integrations():
    """Get status of all integration sources."""
    from psm.integrations import get_all_adapters

    adapters = get_all_adapters(mock=True)
    ingestion = store.read_ingestion()

    sources = []
    for adapter in adapters:
        source = adapter.source_type.value
        count = sum(1 for r in ingestion if r.source.value == source)
        status = adapter.get_status()
        sources.append({
            "source": source,
            "status": status.status,
            "enabled": status.enabled,
            "record_count": count,
            "last_sync_at": status.last_sync_at.isoformat() if status.last_sync_at else None,
        })

    return {
        "sources": sources,
        "total_ingestion_records": len(ingestion),
    }


@app.get("/api/capabilities")
def get_capabilities():
    """Get the current capability inventory."""
    inventory = store.read_capability_inventory()
    if not inventory:
        raise HTTPException(404, "Capability inventory not found")
    return inventory.model_dump(mode="json")


@app.post("/api/capabilities")
def update_capabilities(inventory: dict):
    """Update the capability inventory."""
    from psm.schemas.solvability import CapabilityInventory
    try:
        validated = CapabilityInventory.model_validate(inventory)
        store.write_capability_inventory(validated)
        return {"status": "updated", "agent_types": len(validated.agent_types), "skill_types": len(validated.skill_types)}
    except Exception as e:
        raise HTTPException(400, f"Invalid inventory: {str(e)}")


@app.get("/api/solvability")
def get_solvability():
    """Get the latest solvability report."""
    report = store.read_solvability()
    if not report:
        return {"results": [], "total_patterns": 0, "passed": 0, "flagged": 0, "dropped": 0}
    return report.model_dump(mode="json")


class OutcomeRequest(BaseModel):
    hypothesis_id: str
    outcome: str  # "validated" | "invalidated"


@app.post("/api/outcomes")
def record_outcome(request: OutcomeRequest):
    """Record a hypothesis outcome."""
    from psm.schemas.solvability import OutcomeEntry

    hypotheses = store.read_hypotheses()
    hyp = next((h for h in hypotheses if h.hypothesis_id == request.hypothesis_id), None)
    if not hyp:
        raise HTTPException(404, f"Hypothesis not found: {request.hypothesis_id}")

    solvability = store.read_solvability()
    score = 0.5
    if solvability:
        result = next((r for r in solvability.results if r.pattern_id == hyp.pattern_id), None)
        if result:
            score = result.confidence

    patterns = store.read_patterns()
    pat = next((p for p in patterns if p.pattern_id == hyp.pattern_id), None)
    domain = pat.domains_affected[0].value if pat and pat.domains_affected else None

    entry = OutcomeEntry(
        pattern_id=hyp.pattern_id,
        hypothesis_id=request.hypothesis_id,
        solvability_score_at_time=score,
        outcome=request.outcome,
        domain=domain,
    )
    store.append_outcome(entry)
    return {"status": "recorded", "entry": entry.model_dump(mode="json")}


@app.get("/api/outcomes/summary")
def get_outcomes_summary():
    """Aggregate outcome statistics."""
    outcomes = store.read_outcomes_log()
    if not outcomes:
        return {"total": 0, "validated": 0, "invalidated": 0, "validation_rate": 0, "by_domain": {}}

    validated = sum(1 for o in outcomes if o.outcome == "validated")
    invalidated = sum(1 for o in outcomes if o.outcome == "invalidated")
    total = len(outcomes)

    by_domain: dict = {}
    for o in outcomes:
        d = o.domain or "unknown"
        if d not in by_domain:
            by_domain[d] = {"validated": 0, "invalidated": 0, "total": 0}
        by_domain[d]["total"] += 1
        by_domain[d][o.outcome] = by_domain[d].get(o.outcome, 0) + 1

    return {
        "total": total,
        "validated": validated,
        "invalidated": invalidated,
        "validation_rate": validated / total if total > 0 else 0,
        "by_domain": by_domain,
    }


@app.get("/api/runs")
def get_run_history():
    """Get pipeline run history."""
    records = store.read_run_history()
    return [r.model_dump(mode="json") for r in records]


@app.post("/api/rollback/{run_id}")
def rollback_run(run_id: str):
    """Rollback to a previous pipeline run's snapshot."""
    from pathlib import Path

    records = store.read_run_history()
    record = next((r for r in records if r.run_id == run_id), None)
    if not record:
        raise HTTPException(404, f"Run not found: {run_id}")

    snapshot_path = Path(record.snapshot_path)
    try:
        store.restore_snapshot(snapshot_path)
        return {"status": "restored", "restored_from": run_id, "snapshot_path": str(snapshot_path)}
    except Exception as e:
        raise HTTPException(500, f"Rollback failed: {str(e)}")


@app.get("/api/config")
def get_pipeline_config():
    """Get current pipeline configuration."""
    config = store.read_pipeline_config()
    return config.model_dump(mode="json")


@app.post("/api/config")
def update_pipeline_config(body: dict):
    """Update pipeline configuration (partial updates supported)."""
    from psm.schemas.pipeline_config import PipelineConfig
    try:
        existing = store.read_pipeline_config().model_dump()
        # Deep merge: update each stage section
        for key in body:
            if key in existing and isinstance(existing[key], dict) and isinstance(body[key], dict):
                existing[key].update(body[key])
            else:
                existing[key] = body[key]
        config = PipelineConfig.model_validate(existing)
        store.write_pipeline_config(config)
        return config.model_dump(mode="json")
    except Exception as e:
        raise HTTPException(400, f"Invalid config: {str(e)}")


def main():
    """Entry point for running the server."""
    import uvicorn
    uvicorn.run("psm.server:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
