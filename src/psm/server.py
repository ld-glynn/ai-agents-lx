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
        summary = run_pipeline(
            stage=request.stage,
            model=request.model,
            solver_model=request.solver_model,
            with_integrations=request.with_integrations,
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


def main():
    """Entry point for running the server."""
    import uvicorn
    uvicorn.run("psm.server:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
