from __future__ import annotations
"""JSON data store with Pydantic validation.

Every write is validated against schemas before persisting.
This is the shared state layer between all agents.
"""

import json
from pathlib import Path
from typing import TypeVar, Type

from pydantic import BaseModel, ValidationError

from psm.config import settings
from psm.schemas.problem import CatalogEntry
from psm.schemas.pattern import Pattern, ThemeSummary
from psm.schemas.hypothesis import Hypothesis
from psm.schemas.solution import SolutionMapping, SolverOutput
from psm.schemas.agent import AgentNewHire, SkillOutput

T = TypeVar("T", bound=BaseModel)


class DataStore:
    """Validated JSON persistence for PSM pipeline data."""

    def _read_list(self, path: Path, model: Type[T]) -> list[T]:
        if not path.exists():
            return []
        raw = json.loads(path.read_text())
        return [model.model_validate(item) for item in raw]

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


# Module-level singleton
store = DataStore()
