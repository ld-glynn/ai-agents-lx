"""Central configuration — all paths resolved relative to project root."""

from pathlib import Path
from dataclasses import dataclass


def _find_project_root() -> Path:
    """Walk up from this file to find the pyproject.toml."""
    current = Path(__file__).resolve().parent
    for parent in [current, *current.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return current


@dataclass(frozen=True)
class Settings:
    root: Path
    data_dir: Path
    input_dir: Path
    context_dir: Path
    playbooks_dir: Path
    job_descriptions_dir: Path

    # Data files
    catalog_path: Path
    patterns_path: Path
    hypotheses_path: Path
    solution_map_path: Path
    problems_csv_path: Path

    # Integration paths
    mock_data_dir: Path
    ingestion_path: Path
    sync_status_path: Path

    # Solvability & outcomes
    capability_inventory_path: Path
    solvability_path: Path
    outcomes_log_path: Path

    # Run history & config
    history_dir: Path
    run_history_path: Path
    pipeline_config_path: Path

    @classmethod
    def default(cls) -> "Settings":
        root = _find_project_root()
        data = root / "data"
        context = root / "context"
        return cls(
            root=root,
            data_dir=data,
            input_dir=data / "input",
            context_dir=context,
            playbooks_dir=context / "solver_playbooks",
            job_descriptions_dir=root / "docs" / "job_descriptions",
            catalog_path=data / "catalog.json",
            patterns_path=data / "patterns.json",
            hypotheses_path=data / "hypotheses.json",
            solution_map_path=data / "solution_map.json",
            problems_csv_path=data / "input" / "problems.csv",
            # Integrations
            mock_data_dir=data / "mock",
            ingestion_path=data / "ingestion.json",
            sync_status_path=data / "sync_status.json",
            # Solvability & outcomes
            capability_inventory_path=data / "capability_inventory.json",
            solvability_path=data / "solvability.json",
            outcomes_log_path=data / "outcomes_log.jsonl",
            # Run history & config
            history_dir=data / "history",
            run_history_path=data / "run_history.json",
            pipeline_config_path=data / "pipeline_config.json",
        )


settings = Settings.default()
