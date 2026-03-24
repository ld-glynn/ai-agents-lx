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
        with_integrations=args.with_integrations,
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
        "new-hires": settings.data_dir / "new_hires.json",
        "skills": settings.data_dir / "skill_outputs.json",
        # Legacy compat
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


def cmd_eval(args: argparse.Namespace) -> None:
    """Run evaluation on existing Agent New Hires."""
    from psm.tools.data_store import store
    from psm.eval.gate import screen_candidate, evaluate_gold

    new_hires = store.read_new_hires()
    hypotheses = store.read_hypotheses()
    patterns = store.read_patterns()
    pat_by_id = {p.pattern_id: p for p in patterns}

    if not new_hires:
        print("No Agent New Hires found. Run 'psm run --stage hire' first.", file=sys.stderr)
        sys.exit(1)

    print(f"Evaluating {len(new_hires)} agents ({args.stage} stage)...")
    print("=" * 60)

    results = []
    for agent in new_hires:
        pat = pat_by_id.get(agent.pattern_id)
        if not pat:
            print(f"SKIP {agent.name}: pattern {agent.pattern_id} not found", file=sys.stderr)
            continue

        if args.stage == "gold":
            result = evaluate_gold(agent, hypotheses, pat, model=args.model)
        else:
            result = screen_candidate(agent, hypotheses, pat, model=args.model)
        results.append(result)

    print("\n" + "=" * 60)
    print("EVALUATION RESULTS")
    print("=" * 60)
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"Stage: {args.stage}")
    print(f"Passed: {passed}/{total}")
    print()
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"  [{status}] {r.agent_name} — {r.reason}")
        if r.eval_run.failure_summary:
            for code, count in r.eval_run.failure_summary.items():
                print(f"         {code}: {count}")


def cmd_sync(args: argparse.Namespace) -> None:
    """Sync integration sources and run structurer."""
    from psm.integrations import get_adapter, get_all_adapters
    from psm.agents.structurer import structure_records
    from psm.tools.data_store import store
    from datetime import datetime

    sources = args.source
    mock = args.mock

    if sources == "all":
        adapters = get_all_adapters(mock=mock)
    else:
        adapters = [get_adapter(sources, mock=mock)]

    all_records = []
    for adapter in adapters:
        print(f"Syncing {adapter.source_type.value}...")
        records = adapter.fetch_records()
        print(f"  Fetched {len(records)} records")
        all_records.extend(records)

        status = adapter.get_status()
        status.last_sync_at = datetime.now()
        status.record_count = len(records)

    if all_records:
        new_count = store.append_ingestion(all_records)
        print(f"\nPersisted {new_count} new ingestion records")

        print("Running Structurer agent...")
        problems = structure_records(all_records, model=args.model)
        print(f"Extracted {len(problems)} problems")

        for p in problems:
            print(f"  {p.id}: {p.title}")
    else:
        print("No records fetched.")


def cmd_integrations(args: argparse.Namespace) -> None:
    """Show integration source status."""
    from psm.tools.data_store import store
    from psm.integrations import get_all_adapters

    adapters = get_all_adapters(mock=True)
    ingestion = store.read_ingestion()

    print("Integration Sources:")
    print("-" * 50)
    for adapter in adapters:
        source = adapter.source_type.value
        count = sum(1 for r in ingestion if r.source.value == source)
        status = adapter.get_status()
        print(f"  {source:12s}  status: {status.status:12s}  records: {count}")

    print(f"\nTotal ingestion records: {len(ingestion)}")


def cmd_status(args: argparse.Namespace) -> None:
    """Show pipeline status — which stages have data."""
    files = {
        "problems.csv": settings.problems_csv_path,
        "catalog.json": settings.catalog_path,
        "patterns.json": settings.patterns_path,
        "themes.json": settings.patterns_path.with_name("themes.json"),
        "hypotheses.json": settings.hypotheses_path,
        "new_hires.json": settings.data_dir / "new_hires.json",
        "skill_outputs.json": settings.data_dir / "skill_outputs.json",
        "ingestion.json": settings.ingestion_path,
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
        choices=["catalog", "patterns", "hypotheses", "hire", "execute"],
        default=None,
        help="Stop after this stage (default: run all)",
    )
    run_parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Model for Tier 1 engine agents",
    )
    run_parser.add_argument(
        "--solver-model",
        default="claude-sonnet-4-20250514",
        help="Model for Tier 2 new hire skill execution",
    )
    run_parser.add_argument(
        "--with-integrations",
        action="store_true",
        default=False,
        help="Run Stage 0: sync integration sources before pipeline",
    )
    run_parser.set_defaults(func=cmd_run)

    # psm inspect
    inspect_parser = sub.add_parser("inspect", help="View pipeline data")
    inspect_parser.add_argument(
        "target",
        choices=["catalog", "patterns", "themes", "hypotheses", "new-hires", "skills"],
    )
    inspect_parser.set_defaults(func=cmd_inspect)

    # psm eval
    eval_parser = sub.add_parser("eval", help="Evaluate Agent New Hires")
    eval_parser.add_argument(
        "--stage",
        choices=["screening", "gold"],
        default="screening",
        help="Evaluation stage (default: screening)",
    )
    eval_parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Model for evaluation runs",
    )
    eval_parser.set_defaults(func=cmd_eval)

    # psm sync
    sync_parser = sub.add_parser("sync", help="Sync integration sources")
    sync_parser.add_argument(
        "--source",
        choices=["salesforce", "gong", "slack", "all"],
        default="all",
        help="Source to sync (default: all)",
    )
    sync_parser.add_argument(
        "--mock", action="store_true", default=True,
        help="Use mock data (default: True)",
    )
    sync_parser.add_argument(
        "--no-mock", action="store_false", dest="mock",
        help="Use live API connections",
    )
    sync_parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Model for structurer agent",
    )
    sync_parser.set_defaults(func=cmd_sync)

    # psm integrations
    int_parser = sub.add_parser("integrations", help="Show integration status")
    int_parser.set_defaults(func=cmd_integrations)

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
