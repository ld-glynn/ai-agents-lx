"""CLI entry point for Problem Solution Mapping."""

import argparse
import json
import sys

from psm.config import settings


def cmd_run(args: argparse.Namespace) -> None:
    """Run the PSM pipeline."""
    from psm.agents.orchestrator import run_pipeline

    summary = run_pipeline(
        stage=args.stage,
        model=args.model,
        solver_model=args.solver_model,
    )

    print("\n" + "=" * 60)
    print("PIPELINE SUMMARY")
    print("=" * 60)
    print(json.dumps(summary, indent=2))


def cmd_inspect(args: argparse.Namespace) -> None:
    """Pretty-print a pipeline data file."""
    file_map = {
        "catalog": settings.catalog_path,
        "patterns": settings.patterns_path,
        "themes": settings.patterns_path.with_name("themes.json"),
        "hypotheses": settings.hypotheses_path,
        "solutions": settings.solution_map_path,
        "outputs": settings.data_dir / "solver_outputs.json",
    }

    path = file_map.get(args.target)
    if not path:
        print(f"Unknown target: {args.target}", file=sys.stderr)
        print(f"Available: {', '.join(file_map.keys())}", file=sys.stderr)
        sys.exit(1)

    if not path.exists():
        print(f"No data yet: {path}", file=sys.stderr)
        sys.exit(1)

    data = json.loads(path.read_text())
    print(json.dumps(data, indent=2, default=str))


def cmd_status(args: argparse.Namespace) -> None:
    """Show pipeline status — which stages have data."""
    files = {
        "problems.csv": settings.problems_csv_path,
        "catalog.json": settings.catalog_path,
        "patterns.json": settings.patterns_path,
        "themes.json": settings.patterns_path.with_name("themes.json"),
        "hypotheses.json": settings.hypotheses_path,
        "solution_map.json": settings.solution_map_path,
        "solver_outputs.json": settings.data_dir / "solver_outputs.json",
    }

    print("Pipeline Status:")
    print("-" * 40)
    for name, path in files.items():
        if path.exists():
            if path.suffix == ".csv":
                lines = len(path.read_text().strip().split("\n")) - 1
                print(f"  {name}: {lines} rows")
            else:
                data = json.loads(path.read_text())
                print(f"  {name}: {len(data)} entries")
        else:
            print(f"  {name}: (empty)")


def cli() -> None:
    parser = argparse.ArgumentParser(
        prog="psm",
        description="Problem Solution Mapping — Agent as New Hire",
    )
    sub = parser.add_subparsers(dest="command")

    # psm run
    run_parser = sub.add_parser("run", help="Run the pipeline")
    run_parser.add_argument(
        "--stage",
        choices=["catalog", "patterns", "hypotheses", "routes", "solve"],
        default=None,
        help="Stop after this stage (default: run all)",
    )
    run_parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Model for analytical agents",
    )
    run_parser.add_argument(
        "--solver-model",
        default="claude-haiku-4-5-20251001",
        help="Model for solver agents",
    )
    run_parser.set_defaults(func=cmd_run)

    # psm inspect
    inspect_parser = sub.add_parser("inspect", help="View pipeline data")
    inspect_parser.add_argument(
        "target",
        choices=["catalog", "patterns", "themes", "hypotheses", "solutions", "outputs"],
    )
    inspect_parser.set_defaults(func=cmd_inspect)

    # psm status
    status_parser = sub.add_parser("status", help="Show pipeline status")
    status_parser.set_defaults(func=cmd_status)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    cli()
