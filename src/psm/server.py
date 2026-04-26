"""FastAPI server — HTTP interface to the PSM pipeline.

Thin wrapper over the existing Python code. Each endpoint delegates to
store, orchestrator, or integration modules directly.

Run with: uvicorn psm.server:app --reload --port 8000
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional, Union

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
    start_stage: Optional[str] = None  # Skip stages before this one
    model: str = "claude-sonnet-4-20250514"
    solver_model: str = "claude-sonnet-4-20250514"
    with_integrations: bool = False
    skip_solvability: bool = False
    config: Optional[dict] = None


class SyncRequest(BaseModel):
    source: str = "all"
    mock: bool = True
    model: str = "claude-sonnet-4-20250514"
    # Optional overrides when source is "wisdom" (Enterpret Knowledge Graph / MCP).
    # wisdom_query accepts a single string OR a list of strings for multi-query sweeps;
    # results across all queries are merged and deduped by entity id.
    wisdom_query: Optional[Union[str, List[str]]] = None
    wisdom_limit: Optional[int] = None
    # When set, a Cypher query replaces the search_knowledge_graph flow for Wisdom.
    wisdom_cypher: Optional[str] = None
    # Record-first mode: time window in days and record limit
    wisdom_days: Optional[int] = None
    wisdom_record_limit: Optional[int] = None
    # Scout parameters
    scout_max_findings: Optional[int] = None
    scout_max_tool_calls: Optional[int] = None
    # Scan mode: "scout" (default), "deep" (record-first), "quick" (theme-first)
    scan_mode: str = "scout"


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
    "deployment-specs": store.read_deployment_specs,
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
            start_stage=request.start_stage,
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
        # Scout mode: intelligent two-phase exploration (default)
        if request.scan_mode == "scout" and request.source == "wisdom":
            from psm.agents.scout import run_scout, ScoutConfig
            from psm.schemas.problem import RawProblem

            scout_config = ScoutConfig(
                mock=request.mock,
                max_findings=request.scout_max_findings or 30,
                max_tool_calls=request.scout_max_tool_calls or 25,
                focus_hint="Focus on experimentation-related problems: A/B testing, feature flags, metrics, rollouts, holdouts, experiment setup, variation management.",
            )

            result = run_scout(scout_config)
            records = result.records
            new_count = store.append_ingestion(records)

            # Each Scout finding becomes a discovered problem with source_record_ids
            raw_problems = []
            for rec in records:
                meta = rec.metadata or {}
                title = rec.raw_text.split("\n")[0].replace("Title: ", "").strip()
                summary_line = ""
                for line in rec.raw_text.split("\n"):
                    if line.startswith("Summary: "):
                        summary_line = line.replace("Summary: ", "").strip()
                        break
                recurrence = meta.get("recurrence_evidence", "")
                description = summary_line or title
                if recurrence:
                    description += f" Evidence: {recurrence}"

                raw_problems.append(RawProblem(
                    id=f"INT-{rec.record_id}",
                    title=title,
                    description=description,
                    reported_by="Scout",
                    date_reported=rec.ingested_at.isoformat() if rec.ingested_at else datetime.now().isoformat(),
                    upstream_sources=meta.get("upstream_sources", []),
                    source_record_ids=[rec.record_id],
                    evidence=recurrence,
                ))

            saved = store.append_discovered_problems(raw_problems)

            return {
                "status": "completed",
                "scan_mode": "scout",
                "source_counts": {"wisdom": len(records)},
                "total_records": len(records),
                "new_records": new_count,
                "problems_extracted": len(raw_problems),
                "problems": [{"id": p.id, "title": p.title} for p in raw_problems],
                "scout_trace": {
                    "tool_calls_used": result.tool_calls_used,
                    "findings_emitted": result.findings_emitted,
                    "stop_cause": result.stop_cause,
                    "finish_reason": result.finish_reason,
                },
            }

        # Quick Scan: theme-first — pull Enterpret themes directly as patterns
        if request.scan_mode == "quick" and request.source == "wisdom":
            from psm.integrations.wisdom import WisdomAdapter
            from psm.schemas.pattern import Pattern

            adapter = WisdomAdapter(
                mock=request.mock,
                search_query=request.wisdom_query,
                search_limit=request.wisdom_limit,
                cypher_query=request.wisdom_cypher or (
                    "MATCH (t:Theme) WHERE t.type = 'DEFAULT' "
                    "RETURN t.record_id AS id, t.name AS title, t.display_name AS name, "
                    "t.description AS description, t.category_enum AS category LIMIT 1000"
                ),
            )
            records = adapter.fetch_records()
            store.append_ingestion(records)

            # Convert ingestion records directly to patterns
            patterns = []
            for i, rec in enumerate(records):
                title = rec.raw_text.split("\n")[0].replace("Title: ", "").strip()
                if not title:
                    continue
                meta = rec.metadata or {}
                patterns.append(Pattern(
                    pattern_id=f"QPAT-{i+1:03d}",
                    name=title,
                    description=meta.get("synthesis") or title,
                    problem_ids=[rec.record_id],
                    domains_affected=["customer"],
                    frequency=meta.get("feedback_sample_count", 1),
                    root_cause_hypothesis=None,
                    confidence=0.7,
                    upstream_sources=meta.get("upstream_sources", []),
                    agent_ideas=[meta.get("agent_idea")] if meta.get("agent_idea") else [],
                ))

            existing_patterns = store.read_patterns()
            existing_themes = store.read_themes()
            store.write_patterns(existing_patterns + patterns, existing_themes)

            return {
                "status": "completed",
                "scan_mode": "quick",
                "source_counts": {"wisdom": len(records)},
                "total_records": len(records),
                "new_records": len(records),
                "problems_extracted": 0,
                "patterns_created": len(patterns),
                "problems": [],
                "message": f"Quick scan: {len(patterns)} themes imported as patterns. Run pipeline from Hypotheses to continue.",
            }

        # Deep Scan: record-first — full pipeline
        if request.source == "all":
            adapters = get_all_adapters(mock=request.mock)
        elif request.source == "wisdom":
            from psm.integrations.wisdom import WisdomAdapter

            adapters = [
                WisdomAdapter(
                    mock=request.mock,
                    search_query=request.wisdom_query,
                    search_limit=request.wisdom_limit,
                    cypher_query=request.wisdom_cypher,
                    days=request.wisdom_days,
                    record_limit=request.wisdom_record_limit,
                )
            ]
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
        extracted = []
        problems = []
        if all_records:
            existing_ids = {r.record_id for r in store.read_ingestion()}
            new_records = [r for r in all_records if r.record_id not in existing_ids]
            new_count = store.append_ingestion(all_records)

            if new_records and request.source == "wisdom" and request.scan_mode == "deep":
                # Record-first: each record IS a problem — skip structurer, guarantee provenance
                from psm.schemas.problem import RawProblem
                raw_problems = []
                for i, rec in enumerate(new_records):
                    meta = rec.metadata or {}
                    source_name = meta.get("source_name", "unknown")
                    content = rec.raw_text.replace(f"[{source_name}] ", "", 1) if source_name else rec.raw_text
                    title = content[:100].split(".")[0].strip() or f"Feedback {i+1}"
                    raw_problems.append(RawProblem(
                        id=f"INT-{rec.record_id}",
                        title=title,
                        description=content[:500],
                        reported_by=source_name,
                        date_reported=meta.get("record_timestamp", datetime.now().isoformat()),
                        domain=None,
                        tags=source_name.lower() if source_name else None,
                        source_record_ids=[rec.record_id],
                        upstream_sources=meta.get("upstream_sources", []),
                        evidence=f"From {source_name} record {meta.get('origin_id', rec.record_id)}",
                    ))
                saved = store.append_discovered_problems(raw_problems)
                print(f"  Saved {saved} discovered problems (record-first, all with source_record_ids)")
                problems = [{"id": p.id, "title": p.title} for p in raw_problems]
            elif new_records:
                # Non-wisdom sources: use structurer as before
                extracted = structure_records(new_records, model=request.model)
                if extracted:
                    saved = store.append_discovered_problems([ep.problem for ep in extracted])
                    print(f"  Saved {saved} new discovered problems")

        # Build a lookup from record_id → ingestion record for provenance
        records_by_id = {r.record_id: r for r in all_records}

        # Build problems list from either record-first or structurer path
        if not problems and extracted:
            problems = [
                {
                    "id": ep.problem.id,
                    "title": ep.problem.title,
                    "description": ep.problem.description,
                    "domain": ep.problem.domain,
                    "evidence": ep.evidence,
                    "source_record_ids": ep.source_record_ids,
                    "sources": [
                        {
                            "record_id": rid,
                            "source": records_by_id[rid].source.value if rid in records_by_id else None,
                            "preview": records_by_id[rid].raw_text[:200] if rid in records_by_id else None,
                            "upstream_sources": records_by_id[rid].metadata.get("upstream_sources", []) if rid in records_by_id else [],
                        }
                        for rid in ep.source_record_ids
                    ],
                }
                for ep in extracted
            ]

        return {
            "status": "completed",
            "scan_mode": "deep",
            "source_counts": source_counts,
            "total_records": len(all_records),
            "new_records": new_count,
            "problems_extracted": len(problems),
            "problems": problems,
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
        extracted = structure_records([record], model=request.model)
        return {
            "problems": [
                {
                    "id": ep.problem.id,
                    "title": ep.problem.title,
                    "description": ep.problem.description,
                    "reported_by": ep.problem.reported_by,
                    "domain": ep.problem.domain,
                    "tags": ep.problem.tags,
                }
                for ep in extracted
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


@app.get("/api/runs/{run_id}")
def get_run_status(run_id: str):
    """Get the current status and progress of a specific pipeline run."""
    records = store.read_run_history()
    for r in records:
        if r.run_id == run_id:
            return r.model_dump(mode="json")
    raise HTTPException(404, f"Run {run_id} not found")


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


# --- Deployment Spec & Agent Lifecycle ---

@app.get("/api/specs/{spec_id}")
def get_spec(spec_id: str):
    """Get a single deployment spec."""
    spec = store.get_deployment_spec(spec_id)
    if not spec:
        raise HTTPException(404, f"Spec not found: {spec_id}")
    return spec.model_dump(mode="json")


@app.post("/api/specs/{spec_id}/approve")
def approve_spec(spec_id: str):
    """Approve a proposed deployment spec."""
    spec = store.update_deployment_spec(spec_id, {
        "state": "approved",
        "approved_at": datetime.now().isoformat(),
    })
    if not spec:
        raise HTTPException(404, f"Spec not found: {spec_id}")
    return {"status": "approved", "spec_id": spec_id}


@app.post("/api/specs/{spec_id}/reject")
def reject_spec(spec_id: str, body: dict = {}):
    """Reject a proposed deployment spec."""
    spec = store.update_deployment_spec(spec_id, {
        "state": "retired",
        "retired_at": datetime.now().isoformat(),
        "retirement_reason": body.get("reason", "Rejected during review"),
    })
    if not spec:
        raise HTTPException(404, f"Spec not found: {spec_id}")
    return {"status": "rejected", "spec_id": spec_id}


@app.post("/api/agents/{agent_id}/deploy")
def deploy_agent_endpoint(agent_id: str):
    """Deploy an approved agent (runs initial invocation)."""
    from psm.agents.invoker import deploy_agent
    specs = store.read_deployment_specs()
    spec = next((s for s in specs if s.agent_id == agent_id), None)
    if not spec:
        raise HTTPException(404, f"No spec found for agent: {agent_id}")
    try:
        result = deploy_agent(spec.spec_id)
        return result
    except Exception as e:
        raise HTTPException(400, str(e))


class InvokeRequest(BaseModel):
    trigger_type: str = "manual"
    trigger_detail: str = "Manual invocation from dashboard"
    model: str = "claude-sonnet-4-20250514"


@app.post("/api/agents/{agent_id}/invoke")
def invoke_agent_endpoint(agent_id: str, request: InvokeRequest):
    """Invoke a deployed agent to do work."""
    from psm.agents.invoker import invoke_agent
    from psm.schemas.deployment import TriggerType
    try:
        trigger = TriggerType(request.trigger_type)
        entries = invoke_agent(agent_id, trigger, request.trigger_detail, model=request.model)
        return {
            "status": "completed",
            "agent_id": agent_id,
            "entries": [e.model_dump(mode="json") for e in entries],
        }
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/agents/{agent_id}/pause")
def pause_agent_endpoint(agent_id: str):
    """Pause a deployed agent."""
    from psm.agents.invoker import pause_agent
    try:
        return pause_agent(agent_id)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/agents/{agent_id}/resume")
def resume_agent_endpoint(agent_id: str):
    """Resume a paused agent."""
    from psm.agents.invoker import resume_agent
    try:
        return resume_agent(agent_id)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/agents/{agent_id}/retire")
def retire_agent_endpoint(agent_id: str, body: dict = {}):
    """Retire an agent permanently."""
    from psm.agents.invoker import retire_agent
    try:
        return retire_agent(agent_id, reason=body.get("reason", ""))
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/agents/{agent_id}/work-log")
def get_work_log(agent_id: str):
    """Get the full work log for an agent."""
    entries = store.read_work_log(agent_id)
    return [e.model_dump(mode="json") for e in entries]


@app.get("/api/work-logs")
def get_all_work_logs():
    """Get work logs for all agents."""
    logs = store.read_all_work_logs()
    return [log.model_dump(mode="json") for log in logs]


# --- Briefs ---

@app.get("/api/briefs/{entity_type}/{entity_id}")
def get_brief(entity_type: str, entity_id: str):
    """Get a cached brief for an entity."""
    brief = store.get_brief(entity_type, entity_id)
    if not brief:
        return None
    return brief.model_dump(mode="json")


@app.post("/api/briefs/{entity_type}/{entity_id}")
def generate_brief_endpoint(entity_type: str, entity_id: str):
    """Generate (or regenerate) an executive brief for a pipeline entity."""
    from psm.agents.brief_generator import generate_brief

    # Build context based on entity type
    context: dict = {}

    if entity_type == "problem":
        catalog = store.read_catalog()
        entry = next((e for e in catalog if e.problem_id == entity_id), None)
        if not entry:
            raise HTTPException(404, f"Problem not found: {entity_id}")
        context["entity"] = entry.model_dump(mode="json")
        # Related patterns
        patterns = store.read_patterns()
        related_pats = [p for p in patterns if entity_id in p.problem_ids]
        context["related_patterns"] = [p.model_dump(mode="json") for p in related_pats]
        # Source evidence from ingestion records
        ingestion = store.read_ingestion()
        source_records = [r for r in ingestion if r.record_id in (entry.source_record_ids or [])]
        context["source_evidence"] = [
            {"record_id": r.record_id, "source": r.source.value, "text": r.raw_text[:500], "metadata": {k: v for k, v in r.metadata.items() if k in ("feedback_items", "synthesis", "agent_idea", "upstream_sources")}}
            for r in source_records
        ]

    elif entity_type == "pattern":
        patterns = store.read_patterns()
        pattern = next((p for p in patterns if p.pattern_id == entity_id), None)
        if not pattern:
            raise HTTPException(404, f"Pattern not found: {entity_id}")
        context["entity"] = pattern.model_dump(mode="json")
        # Constituent problems
        catalog = store.read_catalog()
        problems = [e for e in catalog if e.problem_id in pattern.problem_ids]
        context["constituent_problems"] = [e.model_dump(mode="json") for e in problems]
        # Downstream hypotheses
        hypotheses = store.read_hypotheses()
        context["downstream_hypotheses"] = [h.model_dump(mode="json") for h in hypotheses if h.pattern_id == entity_id]
        # Downstream agents
        agents = store.read_new_hires()
        context["downstream_agents"] = [a.model_dump(mode="json") for a in agents if a.pattern_id == entity_id]
        # Source evidence
        ingestion = store.read_ingestion()
        all_source_ids = set()
        for p in problems:
            all_source_ids.update(p.source_record_ids or [])
        source_records = [r for r in ingestion if r.record_id in all_source_ids]
        context["source_evidence"] = [
            {"record_id": r.record_id, "source": r.source.value, "text": r.raw_text[:500], "metadata": {k: v for k, v in r.metadata.items() if k in ("feedback_items", "synthesis", "agent_idea", "upstream_sources")}}
            for r in source_records
        ]

    elif entity_type == "hypothesis":
        hypotheses = store.read_hypotheses()
        hyp = next((h for h in hypotheses if h.hypothesis_id == entity_id), None)
        if not hyp:
            raise HTTPException(404, f"Hypothesis not found: {entity_id}")
        context["entity"] = hyp.model_dump(mode="json")
        # Parent pattern
        patterns = store.read_patterns()
        pattern = next((p for p in patterns if p.pattern_id == hyp.pattern_id), None)
        if pattern:
            context["parent_pattern"] = pattern.model_dump(mode="json")
            # Constituent problems
            catalog = store.read_catalog()
            problems = [e for e in catalog if e.problem_id in pattern.problem_ids]
            context["constituent_problems"] = [e.model_dump(mode="json") for e in problems]
        # Linked agent
        agents = store.read_new_hires()
        context["linked_agents"] = [a.model_dump(mode="json") for a in agents if entity_id in a.hypothesis_ids]
        # Source evidence from pattern
        if pattern:
            ingestion = store.read_ingestion()
            all_source_ids = set()
            for p in problems:
                all_source_ids.update(p.source_record_ids or [])
            source_records = [r for r in ingestion if r.record_id in all_source_ids]
            context["source_evidence"] = [
                {"record_id": r.record_id, "source": r.source.value, "text": r.raw_text[:500], "metadata": {k: v for k, v in r.metadata.items() if k in ("feedback_items", "synthesis", "agent_idea", "upstream_sources")}}
                for r in source_records
            ]
    else:
        raise HTTPException(400, f"Unknown entity type: {entity_type}")

    try:
        brief = generate_brief(entity_type, entity_id, context)
        store.save_brief(brief)
        return brief.model_dump(mode="json")
    except Exception as e:
        raise HTTPException(500, f"Brief generation failed: {str(e)}")


# --- Register External Agent ---

class RegisterAnalyzeRequest(BaseModel):
    name: str
    description: str
    who_uses: str = ""
    data_sources: list[str] = []
    outputs: list[str] = []
    model: str = "claude-sonnet-4-20250514"


class RegisterConfirmRequest(BaseModel):
    use_existing_pattern_id: Optional[str] = None
    use_existing_hypothesis_id: Optional[str] = None
    pattern: Optional[dict] = None
    hypothesis: Optional[dict] = None
    agent_name: str
    agent_title: str
    agent_persona: str
    skill_type: str = "recommend"


@app.post("/api/register-agent/analyze")
def register_agent_analyze(request: RegisterAnalyzeRequest):
    """Reverse-analyze an external agent to generate PSM entities."""
    from psm.agents.reverse_analyzer import reverse_analyze

    try:
        result = reverse_analyze(
            agent_description={
                "name": request.name,
                "description": request.description,
                "who_uses": request.who_uses,
                "data_sources": request.data_sources,
                "outputs": request.outputs,
            },
            existing_patterns=store.read_patterns(),
            existing_hypotheses=store.read_hypotheses(),
            existing_catalog=store.read_catalog(),
            model=request.model,
        )
        return result
    except Exception as e:
        raise HTTPException(500, f"Reverse analysis failed: {str(e)}")


@app.post("/api/register-agent/confirm")
def register_agent_confirm(request: RegisterConfirmRequest):
    """Persist the reviewed reverse-analysis as PSM entities."""
    from psm.schemas.pattern import Pattern
    from psm.schemas.hypothesis import Hypothesis
    from psm.schemas.agent import AgentNewHire, AgentSkill, SkillType
    from psm.schemas.trial import Trial, TrialStatus

    try:
        # 1. Resolve or create pattern
        if request.use_existing_pattern_id:
            pattern_id = request.use_existing_pattern_id
        else:
            patterns = store.read_patterns()
            n = sum(1 for p in patterns if p.pattern_id.startswith("RPAT-")) + 1
            pattern_id = f"RPAT-{n:03d}"
            pat_data = request.pattern or {}
            pat_data["pattern_id"] = pattern_id
            pat_data.setdefault("problem_ids", [f"REG-{request.agent_name.replace(' ', '-').lower()[:20]}"])
            pat_data.setdefault("frequency", 1)
            pat_data.setdefault("confidence", 0.7)
            new_pat = Pattern.model_validate(pat_data, strict=False)
            themes = store.read_themes()
            store.write_patterns(patterns + [new_pat], themes)

        # 2. Resolve or create hypothesis
        if request.use_existing_hypothesis_id:
            hypothesis_id = request.use_existing_hypothesis_id
        else:
            hypotheses = store.read_hypotheses()
            n = sum(1 for h in hypotheses if h.hypothesis_id.startswith("RHYP-")) + 1
            hypothesis_id = f"RHYP-{n:03d}"
            hyp_data = request.hypothesis or {}
            hyp_data["hypothesis_id"] = hypothesis_id
            hyp_data["pattern_id"] = pattern_id
            hyp_data.setdefault("assumptions", [])
            hyp_data.setdefault("risks", [])
            hyp_data.setdefault("confidence", 0.7)
            hyp_data.setdefault("effort_estimate", "medium")
            new_hyp = Hypothesis.model_validate(hyp_data, strict=False)
            store.write_hypotheses(hypotheses + [new_hyp])

        # 3. Create agent
        hires = store.read_new_hires()
        n = sum(1 for a in hires if a.agent_id.startswith("RAGT-")) + 1
        agent_id = f"RAGT-{n:03d}"
        skill = AgentSkill(
            skill_type=SkillType(request.skill_type),
            hypothesis_id=hypothesis_id,
            priority=1,
        )
        agent = AgentNewHire(
            agent_id=agent_id,
            name=request.agent_name,
            title=request.agent_title,
            persona=request.agent_persona,
            pattern_id=pattern_id,
            hypothesis_ids=[hypothesis_id],
            skills=[skill],
            lifecycle_state="deployed",
        )
        store.write_new_hires(hires + [agent])

        # 4. Create trial in setup state
        hyp_obj = next((h for h in store.read_hypotheses() if h.hypothesis_id == hypothesis_id), None)
        test_criteria = hyp_obj.test_criteria if hyp_obj else ["Agent produces useful output"]
        trials = store.read_trials()
        trial_id = f"TRIAL-{len(trials) + 1:03d}"
        trial = Trial(
            trial_id=trial_id,
            agent_id=agent_id,
            hypothesis_id=hypothesis_id,
            pattern_id=pattern_id,
            success_criteria=test_criteria,
            status=TrialStatus.SETUP,
        )
        store.append_trial(trial)

        return {
            "agent_id": agent_id,
            "pattern_id": pattern_id,
            "hypothesis_id": hypothesis_id,
            "trial_id": trial_id,
        }
    except Exception as e:
        raise HTTPException(500, f"Registration failed: {str(e)}")


# --- Trials ---

class TrialCreateRequest(BaseModel):
    agent_id: str
    hypothesis_id: str
    duration_days: int = 30
    success_criteria: list[str]
    check_in_frequency_days: int = 7


class TrialCheckInRequest(BaseModel):
    note: str
    progress_indicator: str = "on_track"


class TrialVerdictRequest(BaseModel):
    verdict: str  # validated, invalidated, extended, inconclusive
    note: str = ""
    lessons_learned: list[str] = []
    extend_days: int | None = None  # only used when verdict=extended


@app.post("/api/trials")
def create_trial(request: TrialCreateRequest):
    """Create a new trial for a deployed agent."""
    from psm.schemas.trial import Trial, TrialStatus

    # Look up the hypothesis to get pattern_id
    hypotheses = store.read_hypotheses()
    hyp = next((h for h in hypotheses if h.hypothesis_id == request.hypothesis_id), None)
    if not hyp:
        raise HTTPException(404, f"Hypothesis not found: {request.hypothesis_id}")

    # Generate trial ID
    existing = store.read_trials()
    trial_num = len(existing) + 1
    trial_id = f"TRIAL-{trial_num:03d}"

    trial = Trial(
        trial_id=trial_id,
        agent_id=request.agent_id,
        hypothesis_id=request.hypothesis_id,
        pattern_id=hyp.pattern_id,
        duration_days=request.duration_days,
        success_criteria=request.success_criteria,
        check_in_frequency_days=request.check_in_frequency_days,
        status=TrialStatus.SETUP,
    )
    store.append_trial(trial)
    return trial.model_dump(mode="json")


@app.post("/api/trials/{trial_id}/start")
def start_trial(trial_id: str):
    """Start a trial — sets timeline and status to active."""
    from psm.schemas.trial import TrialStatus
    from datetime import timedelta

    trial = store.get_trial(trial_id)
    if not trial:
        raise HTTPException(404, f"Trial not found: {trial_id}")
    if trial.status != TrialStatus.SETUP:
        raise HTTPException(400, f"Trial is {trial.status.value}, not setup")

    now = datetime.now()
    updated = store.update_trial(trial_id, {
        "started_at": now.isoformat(),
        "ends_at": (now + timedelta(days=trial.duration_days)).isoformat(),
        "status": "active",
    })
    return updated.model_dump(mode="json")


@app.post("/api/trials/{trial_id}/check-in")
def add_trial_check_in(trial_id: str, request: TrialCheckInRequest):
    """Add a check-in note to an active trial."""
    from psm.schemas.trial import TrialStatus, CheckIn, CheckInProgress

    trial = store.get_trial(trial_id)
    if not trial:
        raise HTTPException(404, f"Trial not found: {trial_id}")
    if trial.status not in (TrialStatus.ACTIVE, TrialStatus.EVALUATING):
        raise HTTPException(400, f"Trial is {trial.status.value}, cannot check in")

    check_in = CheckIn(
        check_in_id=f"CI-{trial_id}-{len(trial.check_ins) + 1:02d}",
        note=request.note,
        progress_indicator=CheckInProgress(request.progress_indicator),
    )

    check_ins = [ci.model_dump(mode="json") for ci in trial.check_ins] + [check_in.model_dump(mode="json")]
    updated = store.update_trial(trial_id, {"check_ins": check_ins})
    return updated.model_dump(mode="json")


@app.post("/api/trials/{trial_id}/verdict")
def render_trial_verdict(trial_id: str, request: TrialVerdictRequest):
    """Render the final verdict on a trial. Feeds back into solvability."""
    from psm.schemas.trial import TrialStatus, TrialVerdict
    from psm.schemas.solvability import OutcomeEntry
    from datetime import timedelta

    trial = store.get_trial(trial_id)
    if not trial:
        raise HTTPException(404, f"Trial not found: {trial_id}")

    verdict = TrialVerdict(request.verdict)

    if verdict == TrialVerdict.EXTENDED:
        # Reset the trial with extended duration
        extend_days = request.extend_days or trial.duration_days
        now = datetime.now()
        updated = store.update_trial(trial_id, {
            "ends_at": (now + timedelta(days=extend_days)).isoformat(),
            "status": "active",
            "verdict": None,
            "verdict_note": f"Extended by {extend_days} days. {request.note}",
        })
        return updated.model_dump(mode="json")

    # Complete the trial
    updates = {
        "status": "completed",
        "completed_at": datetime.now().isoformat(),
        "verdict": verdict.value,
        "verdict_note": request.note,
        "lessons_learned": request.lessons_learned,
    }
    updated = store.update_trial(trial_id, updates)

    # Write outcome to solvability log for feedback loop
    if verdict in (TrialVerdict.VALIDATED, TrialVerdict.INVALIDATED):
        # Get solvability score if available
        solvability_score = 0.5
        try:
            report = store.read_solvability()
            for r in report.results:
                if r.pattern_id == trial.pattern_id:
                    solvability_score = r.confidence
                    break
        except Exception:
            pass

        # Get domain from pattern
        domain = None
        try:
            patterns = store.read_patterns()
            pat = next((p for p in patterns if p.pattern_id == trial.pattern_id), None)
            if pat and pat.domains_affected:
                domain = pat.domains_affected[0].value
        except Exception:
            pass

        outcome = OutcomeEntry(
            pattern_id=trial.pattern_id,
            hypothesis_id=trial.hypothesis_id,
            solvability_score_at_time=solvability_score,
            outcome=verdict.value,
            domain=domain,
        )
        store.append_outcome(outcome)

    return updated.model_dump(mode="json")


@app.get("/api/trials")
def list_trials():
    """Get all trials."""
    trials = store.read_trials()
    return [t.model_dump(mode="json") for t in trials]


@app.get("/api/trials/{trial_id}")
def get_trial_detail(trial_id: str):
    """Get a single trial."""
    trial = store.get_trial(trial_id)
    if not trial:
        raise HTTPException(404, f"Trial not found: {trial_id}")
    return trial.model_dump(mode="json")


@app.get("/api/agents/{agent_id}/trial")
def get_agent_trial(agent_id: str):
    """Get the current or latest trial for an agent."""
    trial = store.get_trial_for_agent(agent_id)
    if not trial:
        return None
    return trial.model_dump(mode="json")


def main():
    """Entry point for running the server."""
    import uvicorn
    uvicorn.run("psm.server:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
