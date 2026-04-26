"""JSON data store with Pydantic validation.

Every write is validated against schemas before persisting.
This is the shared state layer between all agents.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar, Type

from pydantic import BaseModel

from psm.config import settings
from psm.schemas.problem import RawProblem, CatalogEntry
from psm.schemas.pattern import Pattern, ThemeSummary
from psm.schemas.hypothesis import Hypothesis
from psm.schemas.solution import SolutionMapping, SolverOutput
from psm.schemas.agent import AgentNewHire, SkillOutput
from psm.schemas.ingestion import IngestionRecord, IngestionSyncStatus
from psm.schemas.solvability import SolvabilityReport, CapabilityInventory, OutcomeEntry
from psm.schemas.run_history import RunRecord
from psm.schemas.pipeline_config import PipelineConfig
from psm.schemas.deployment import DeploymentSpec
from psm.schemas.work_log import WorkLogEntry, WorkLog
from psm.schemas.trial import Trial
from psm.schemas.brief import Brief

T = TypeVar("T", bound=BaseModel)


class DataStore:
    """Validated JSON persistence for PSM pipeline data."""

    def _read_list(self, path: Path, model: Type[T]) -> list[T]:
        if not path.exists():
            return []
        raw = json.loads(path.read_text())
        # strict=False so JSON-serialized enums (strings) and datetimes (ISO
        # strings) re-coerce on read. The stored data was already validated
        # on write, so strict mode is inappropriate here — it would only fire
        # on our own round-trips of fields like SourceType and datetime.
        return [model.model_validate(item, strict=False) for item in raw]

    def _write_list(self, path: Path, items: list[T]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = [item.model_dump(mode="json") for item in items]
        path.write_text(json.dumps(data, indent=2, default=str))

    # --- Catalog ---

    def read_catalog(self) -> list[CatalogEntry]:
        return self._read_list(settings.catalog_path, CatalogEntry)

    def write_catalog(self, entries: list[CatalogEntry]) -> int:
        self._write_list(settings.catalog_path, entries)
        return len(entries)

    def append_catalog(self, entries: list[CatalogEntry]) -> int:
        existing = self.read_catalog()
        existing_ids = {e.problem_id for e in existing}
        new = [e for e in entries if e.problem_id not in existing_ids]
        self._write_list(settings.catalog_path, existing + new)
        return len(new)

    # --- Discovered Problems (from Find Problems / sync) ---

    def read_discovered_problems(self) -> list[RawProblem]:
        return self._read_list(settings.discovered_problems_path, RawProblem)

    def append_discovered_problems(self, problems: list[RawProblem]) -> int:
        existing = self.read_discovered_problems()
        existing_ids = {p.id for p in existing}
        new = [p for p in problems if p.id not in existing_ids]
        self._write_list(settings.discovered_problems_path, existing + new)
        return len(new)

    # --- Patterns ---

    def read_patterns(self) -> list[Pattern]:
        return self._read_list(settings.patterns_path, Pattern)

    def read_themes(self) -> list[ThemeSummary]:
        """Themes are stored alongside patterns in a combined file."""
        path = settings.patterns_path.with_name("themes.json")
        return self._read_list(path, ThemeSummary)

    def write_patterns(self, patterns: list[Pattern], themes: list[ThemeSummary]) -> dict:
        self._write_list(settings.patterns_path, patterns)
        themes_path = settings.patterns_path.with_name("themes.json")
        self._write_list(themes_path, themes)
        return {"patterns": len(patterns), "themes": len(themes)}

    # --- Hypotheses ---

    def read_hypotheses(self) -> list[Hypothesis]:
        return self._read_list(settings.hypotheses_path, Hypothesis)

    def write_hypotheses(self, hypotheses: list[Hypothesis]) -> int:
        self._write_list(settings.hypotheses_path, hypotheses)
        return len(hypotheses)

    # --- Solution Map ---

    def read_solution_map(self) -> list[SolutionMapping]:
        return self._read_list(settings.solution_map_path, SolutionMapping)

    def write_solution_map(self, mappings: list[SolutionMapping]) -> int:
        self._write_list(settings.solution_map_path, mappings)
        return len(mappings)

    # --- Solver Outputs ---

    def read_solver_outputs(self) -> list[SolverOutput]:
        path = settings.data_dir / "solver_outputs.json"
        return self._read_list(path, SolverOutput)

    def write_solver_outputs(self, outputs: list[SolverOutput]) -> int:
        path = settings.data_dir / "solver_outputs.json"
        self._write_list(path, outputs)
        return len(outputs)

    def append_solver_output(self, output: SolverOutput) -> None:
        existing = self.read_solver_outputs()
        existing.append(output)
        self._write_list(settings.data_dir / "solver_outputs.json", existing)

    # --- Agent New Hires (Tier 2) ---

    def read_new_hires(self) -> list[AgentNewHire]:
        path = settings.data_dir / "new_hires.json"
        return self._read_list(path, AgentNewHire)

    def write_new_hires(self, agents: list[AgentNewHire]) -> int:
        path = settings.data_dir / "new_hires.json"
        self._write_list(path, agents)
        return len(agents)

    # --- Skill Outputs (Tier 3 deliverables) ---

    def read_skill_outputs(self) -> list[SkillOutput]:
        path = settings.data_dir / "skill_outputs.json"
        return self._read_list(path, SkillOutput)

    def write_skill_outputs(self, outputs: list[SkillOutput]) -> int:
        path = settings.data_dir / "skill_outputs.json"
        self._write_list(path, outputs)
        return len(outputs)

    def append_skill_output(self, output: SkillOutput) -> None:
        existing = self.read_skill_outputs()
        existing.append(output)
        self._write_list(settings.data_dir / "skill_outputs.json", existing)

    # --- Ingestion Records ---

    def read_ingestion(self) -> list[IngestionRecord]:
        return self._read_list(settings.ingestion_path, IngestionRecord)

    def write_ingestion(self, records: list[IngestionRecord]) -> int:
        self._write_list(settings.ingestion_path, records)
        return len(records)

    def append_ingestion(self, records: list[IngestionRecord]) -> int:
        existing = self.read_ingestion()
        existing_ids = {r.record_id for r in existing}
        new = [r for r in records if r.record_id not in existing_ids]
        self._write_list(settings.ingestion_path, existing + new)
        return len(new)

    # --- Sync Status ---

    def read_sync_status(self) -> list[IngestionSyncStatus]:
        return self._read_list(settings.sync_status_path, IngestionSyncStatus)

    def write_sync_status(self, statuses: list[IngestionSyncStatus]) -> None:
        self._write_list(settings.sync_status_path, statuses)

    # --- Solvability ---

    def read_solvability(self) -> SolvabilityReport | None:
        if not settings.solvability_path.exists():
            return None
        raw = json.loads(settings.solvability_path.read_text())
        return SolvabilityReport.model_validate(raw)

    def write_solvability(self, report: SolvabilityReport) -> None:
        settings.solvability_path.parent.mkdir(parents=True, exist_ok=True)
        settings.solvability_path.write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, default=str)
        )

    # --- Capability Inventory ---

    def read_capability_inventory(self) -> CapabilityInventory | None:
        if not settings.capability_inventory_path.exists():
            return None
        raw = json.loads(settings.capability_inventory_path.read_text())
        return CapabilityInventory.model_validate(raw)

    def write_capability_inventory(self, inventory: CapabilityInventory) -> None:
        settings.capability_inventory_path.parent.mkdir(parents=True, exist_ok=True)
        settings.capability_inventory_path.write_text(
            json.dumps(inventory.model_dump(mode="json"), indent=2, default=str)
        )

    # --- Outcomes Log ---

    def read_outcomes_log(self) -> list[OutcomeEntry]:
        if not settings.outcomes_log_path.exists():
            return []
        entries = []
        for line in settings.outcomes_log_path.read_text().strip().split("\n"):
            if line.strip():
                entries.append(OutcomeEntry.model_validate(json.loads(line)))
        return entries

    def append_outcome(self, entry: OutcomeEntry) -> None:
        settings.outcomes_log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(settings.outcomes_log_path, "a") as f:
            f.write(json.dumps(entry.model_dump(mode="json"), default=str) + "\n")

    # --- Run History & Snapshots ---

    SNAPSHOT_FILES_KEYS = [
        "catalog_path", "patterns_path", "hypotheses_path", "solvability_path",
    ]
    SNAPSHOT_FILES_EXTRA = [
        "themes.json", "new_hires.json", "skill_outputs.json",
    ]

    def create_snapshot(self, run_id: str) -> Path:
        """Copy all data files into a timestamped snapshot directory."""
        import shutil
        snapshot_dir = settings.history_dir / run_id
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        # Named paths from settings
        for key in self.SNAPSHOT_FILES_KEYS:
            src = getattr(settings, key)
            if src.exists():
                shutil.copy2(src, snapshot_dir / src.name)

        # Extra files in data_dir
        for name in self.SNAPSHOT_FILES_EXTRA:
            src = settings.data_dir / name if name != "themes.json" else settings.patterns_path.with_name("themes.json")
            if src.exists():
                shutil.copy2(src, snapshot_dir / name)

        return snapshot_dir

    def restore_snapshot(self, snapshot_path: Path) -> None:
        """Restore data files from a snapshot directory."""
        import shutil
        if not snapshot_path.exists():
            raise FileNotFoundError(f"Snapshot not found: {snapshot_path}")

        for f in snapshot_path.iterdir():
            if f.suffix in (".json", ".jsonl"):
                # Map back to the correct destination
                if f.name == "themes.json":
                    dest = settings.patterns_path.with_name("themes.json")
                elif f.name in ("new_hires.json", "skill_outputs.json"):
                    dest = settings.data_dir / f.name
                else:
                    # Try to match by name to settings paths
                    dest = settings.data_dir / f.name
                shutil.copy2(f, dest)

    def read_run_history(self) -> list[RunRecord]:
        if not settings.run_history_path.exists():
            return []
        raw = json.loads(settings.run_history_path.read_text())
        return [RunRecord.model_validate(r) for r in raw]

    def append_run_record(self, record: RunRecord) -> None:
        records = self.read_run_history()
        records.append(record)
        settings.run_history_path.parent.mkdir(parents=True, exist_ok=True)
        data = [r.model_dump(mode="json") for r in records]
        settings.run_history_path.write_text(json.dumps(data, indent=2, default=str))

    def update_run_record(self, run_id: str, updates: dict) -> None:
        records = self.read_run_history()
        for i, r in enumerate(records):
            if r.run_id == run_id:
                merged = r.model_dump()
                merged.update(updates)
                records[i] = RunRecord.model_validate(merged)
                break
        data = [r.model_dump(mode="json") for r in records]
        settings.run_history_path.write_text(json.dumps(data, indent=2, default=str))

    # --- Pipeline Config ---

    def read_pipeline_config(self) -> PipelineConfig:
        if not settings.pipeline_config_path.exists():
            return PipelineConfig()
        raw = json.loads(settings.pipeline_config_path.read_text())
        return PipelineConfig.model_validate(raw)

    def write_pipeline_config(self, config: PipelineConfig) -> None:
        settings.pipeline_config_path.parent.mkdir(parents=True, exist_ok=True)
        settings.pipeline_config_path.write_text(
            json.dumps(config.model_dump(mode="json"), indent=2)
        )

    # --- Deployment Specs ---

    def read_deployment_specs(self) -> list[DeploymentSpec]:
        return self._read_list(settings.deployment_specs_path, DeploymentSpec)

    def write_deployment_specs(self, specs: list[DeploymentSpec]) -> int:
        self._write_list(settings.deployment_specs_path, specs)
        return len(specs)

    def get_deployment_spec(self, spec_id: str) -> DeploymentSpec | None:
        specs = self.read_deployment_specs()
        return next((s for s in specs if s.spec_id == spec_id), None)

    def update_deployment_spec(self, spec_id: str, updates: dict) -> DeploymentSpec | None:
        specs = self.read_deployment_specs()
        for i, s in enumerate(specs):
            if s.spec_id == spec_id:
                merged = s.model_dump()
                merged.update(updates)
                specs[i] = DeploymentSpec.model_validate(merged)
                self._write_list(settings.deployment_specs_path, specs)
                return specs[i]
        return None

    # --- Work Logs ---

    def _work_log_path(self, agent_id: str) -> Path:
        return settings.work_logs_dir / f"{agent_id}.jsonl"

    def read_work_log(self, agent_id: str) -> list[WorkLogEntry]:
        path = self._work_log_path(agent_id)
        if not path.exists():
            return []
        entries = []
        for line in path.read_text().strip().split("\n"):
            if line.strip():
                entries.append(WorkLogEntry.model_validate(json.loads(line)))
        return entries

    def append_work_log_entry(self, agent_id: str, entry: WorkLogEntry) -> None:
        path = self._work_log_path(agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(entry.model_dump(mode="json"), default=str) + "\n")

    def read_all_work_logs(self) -> list[WorkLog]:
        """Read work logs for all agents."""
        logs = []
        if not settings.work_logs_dir.exists():
            return logs
        for path in settings.work_logs_dir.glob("*.jsonl"):
            agent_id = path.stem
            entries = self.read_work_log(agent_id)
            if entries:
                spec_id = entries[0].spec_id if entries else ""
                logs.append(WorkLog(
                    agent_id=agent_id,
                    spec_id=spec_id,
                    entries=entries,
                    total_invocations=len(entries),
                    last_invoked_at=entries[-1].started_at if entries else None,
                    last_output_at=next((e.completed_at for e in reversed(entries) if e.completed_at), None),
                ))
        return logs


    # --- Briefs ---

    def read_briefs(self) -> list[Brief]:
        return self._read_list(settings.briefs_path, Brief)

    def get_brief(self, entity_type: str, entity_id: str) -> Brief | None:
        return next(
            (b for b in self.read_briefs() if b.entity_type == entity_type and b.entity_id == entity_id),
            None,
        )

    def save_brief(self, brief: Brief) -> None:
        briefs = [b for b in self.read_briefs() if not (b.entity_type == brief.entity_type and b.entity_id == brief.entity_id)]
        briefs.append(brief)
        self._write_list(settings.briefs_path, briefs)

    # --- Trials ---

    def read_trials(self) -> list[Trial]:
        return self._read_list(settings.trials_path, Trial)

    def append_trial(self, trial: Trial) -> None:
        trials = self.read_trials()
        trials.append(trial)
        self._write_list(settings.trials_path, trials)

    def get_trial(self, trial_id: str) -> Trial | None:
        return next((t for t in self.read_trials() if t.trial_id == trial_id), None)

    def get_trial_for_agent(self, agent_id: str) -> Trial | None:
        trials = self.read_trials()
        active = [t for t in trials if t.agent_id == agent_id and t.status.value in ("setup", "active", "evaluating")]
        if active:
            return active[0]
        completed = [t for t in trials if t.agent_id == agent_id and t.status.value == "completed"]
        return completed[-1] if completed else None

    def update_trial(self, trial_id: str, updates: dict) -> Trial | None:
        trials = self.read_trials()
        for i, t in enumerate(trials):
            if t.trial_id == trial_id:
                merged = t.model_dump()
                merged.update(updates)
                trials[i] = Trial.model_validate(merged, strict=False)
                self._write_list(settings.trials_path, trials)
                return trials[i]
        return None


# Module-level singleton
store = DataStore()
