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
        skip_solvability=args.skip_solvability,
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
        "solvability": settings.solvability_path,
        "outcomes": settings.outcomes_log_path,
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


def cmd_deploy(args: argparse.Namespace) -> None:
    """Deploy an approved agent."""
    from psm.agents.invoker import deploy_agent
    from psm.tools.data_store import store

    # Find spec by agent_id
    specs = store.read_deployment_specs()
    spec = next((s for s in specs if s.agent_id == args.agent_id), None)
    if not spec:
        print(f"No deployment spec found for agent: {args.agent_id}", file=sys.stderr)
        sys.exit(1)

    try:
        result = deploy_agent(spec.spec_id, model=args.model)
        print(f"\nDeployed: {result['agent_id']}")
        print(f"  Spec: {result['spec_id']}")
        print(f"  Initial invocation: {result['initial_invocation_entries']} skills executed")
    except Exception as e:
        print(f"Deploy failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_invoke(args: argparse.Namespace) -> None:
    """Invoke a deployed agent."""
    from psm.agents.invoker import invoke_agent
    from psm.schemas.deployment import TriggerType

    try:
        trigger = TriggerType(args.trigger)
        entries = invoke_agent(args.agent_id, trigger, args.detail or f"CLI invocation ({trigger.value})", model=args.model)
        print(f"\nInvoked {args.agent_id}: {len(entries)} skills executed")
        for e in entries:
            status = "OK" if e.status == "completed" else "FAIL"
            print(f"  [{status}] {e.skill_type.value}: {e.action_summary}")
    except Exception as e:
        print(f"Invoke failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_agents(args: argparse.Namespace) -> None:
    """List agents with lifecycle state."""
    from psm.tools.data_store import store

    agents = store.read_new_hires()
    specs = store.read_deployment_specs()
    spec_by_agent = {s.agent_id: s for s in specs}

    if not agents:
        print("No agents found. Run the pipeline first.")
        return

    print(f"Agents ({len(agents)}):")
    print("-" * 70)
    for a in agents:
        state = a.lifecycle_state
        spec = spec_by_agent.get(a.agent_id)
        work_entries = store.read_work_log(a.agent_id)
        invocations = len(work_entries)
        print(f"  [{state:10s}] {a.agent_id}: {a.name}")
        print(f"             {a.title} | {len(a.skills)} skills | {invocations} invocations")
        if spec:
            print(f"             Spec: {spec.spec_id} | Target: {spec.deployment.target.value}")


def cmd_history(args: argparse.Namespace) -> None:
    """Show pipeline run history."""
    from psm.tools.data_store import store

    records = store.read_run_history()
    if not records:
        print("No runs recorded yet.")
        return

    print(f"Pipeline Run History ({len(records)} runs):")
    print("-" * 80)
    for r in reversed(records):
        status_icon = {"success": "OK", "partial": "!!", "failed": "XX", "running": ".."}[r.status]
        stages = ", ".join(r.stages_completed) if r.stages_completed else "(none)"
        print(f"  [{status_icon}] {r.run_id}  status={r.status}  stages=[{stages}]")
        if r.error_stage:
            print(f"       error at {r.error_stage}: {r.error_message}")


def cmd_rollback(args: argparse.Namespace) -> None:
    """Rollback to a previous run's snapshot."""
    from pathlib import Path
    from psm.tools.data_store import store

    records = store.read_run_history()
    record = next((r for r in records if r.run_id == args.run_id), None)
    if not record:
        print(f"Run not found: {args.run_id}", file=sys.stderr)
        sys.exit(1)

    snapshot_path = Path(record.snapshot_path)
    if not snapshot_path.exists():
        print(f"Snapshot directory missing: {snapshot_path}", file=sys.stderr)
        sys.exit(1)

    store.restore_snapshot(snapshot_path)
    print(f"Restored data from {args.run_id} (snapshot: {snapshot_path})")


def cmd_eval_solvability(args: argparse.Namespace) -> None:
    """Run solvability evaluation on existing patterns."""
    from psm.tools.data_store import store
    from psm.agents.solvability_evaluator import run_solvability_evaluator

    patterns = store.read_patterns()
    if not patterns:
        print("No patterns found. Run 'psm run --stage patterns' first.", file=sys.stderr)
        sys.exit(1)

    print(f"Evaluating solvability of {len(patterns)} patterns...")
    print("=" * 60)

    report = run_solvability_evaluator(patterns, model=args.model)
    store.write_solvability(report)

    print(f"\nTotal: {report.total_patterns}")
    print(f"  Pass:    {report.passed}")
    print(f"  Flag:    {report.flagged}")
    print(f"  Drop:    {report.dropped}")
    print()
    for r in report.results:
        icon = {"pass": "PASS", "flag": "FLAG", "drop": "DROP"}[r.status.value]
        print(f"  [{icon}] {r.pattern_id} (conf: {r.confidence:.0%}) — {r.reason}")


def cmd_record_outcome(args: argparse.Namespace) -> None:
    """Record an outcome for a hypothesis."""
    from psm.tools.data_store import store
    from psm.schemas.solvability import OutcomeEntry

    hypotheses = store.read_hypotheses()
    hyp = next((h for h in hypotheses if h.hypothesis_id == args.hypothesis_id), None)
    if not hyp:
        print(f"Hypothesis not found: {args.hypothesis_id}", file=sys.stderr)
        sys.exit(1)

    # Get solvability score if available
    solvability = store.read_solvability()
    score = 0.5
    if solvability:
        result = next((r for r in solvability.results if r.pattern_id == hyp.pattern_id), None)
        if result:
            score = result.confidence

    # Get domain from pattern
    patterns = store.read_patterns()
    pat = next((p for p in patterns if p.pattern_id == hyp.pattern_id), None)
    domain = pat.domains_affected[0].value if pat and pat.domains_affected else None

    entry = OutcomeEntry(
        pattern_id=hyp.pattern_id,
        hypothesis_id=args.hypothesis_id,
        solvability_score_at_time=score,
        outcome=args.outcome,
        domain=domain,
    )
    store.append_outcome(entry)
    print(f"Recorded: {args.hypothesis_id} → {args.outcome} (solvability score: {score:.0%})")


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
        "solvability.json": settings.solvability_path,
        "deployment_specs.json": settings.deployment_specs_path,
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
        choices=["catalog", "patterns", "solvability", "hypotheses", "hire", "spec"],
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
    run_parser.add_argument(
        "--skip-solvability",
        action="store_true",
        default=False,
        help="Skip solvability evaluation (Stage 2.5)",
    )
    run_parser.set_defaults(func=cmd_run)

    # psm inspect
    inspect_parser = sub.add_parser("inspect", help="View pipeline data")
    inspect_parser.add_argument(
        "target",
        choices=["catalog", "patterns", "themes", "hypotheses", "new-hires", "skills", "solvability", "outcomes"],
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

    # psm deploy
    deploy_parser = sub.add_parser("deploy", help="Deploy an approved agent")
    deploy_parser.add_argument("agent_id", help="Agent ID to deploy")
    deploy_parser.add_argument("--model", default="claude-sonnet-4-20250514")
    deploy_parser.set_defaults(func=cmd_deploy)

    # psm invoke
    invoke_parser = sub.add_parser("invoke", help="Invoke a deployed agent")
    invoke_parser.add_argument("agent_id", help="Agent ID to invoke")
    invoke_parser.add_argument("--trigger", choices=["manual", "scheduled", "webhook", "feedback", "api_call"], default="manual")
    invoke_parser.add_argument("--detail", default=None, help="Trigger detail message")
    invoke_parser.add_argument("--model", default="claude-sonnet-4-20250514")
    invoke_parser.set_defaults(func=cmd_invoke)

    # psm agents
    agents_parser = sub.add_parser("agents", help="List agents with lifecycle state")
    agents_parser.set_defaults(func=cmd_agents)

    # psm history
    history_parser = sub.add_parser("history", help="Show pipeline run history")
    history_parser.set_defaults(func=cmd_history)

    # psm rollback
    rollback_parser = sub.add_parser("rollback", help="Rollback to a previous run")
    rollback_parser.add_argument("run_id", help="Run ID to rollback to")
    rollback_parser.set_defaults(func=cmd_rollback)

    # psm eval-solvability
    solv_parser = sub.add_parser("eval-solvability", help="Run solvability evaluation on patterns")
    solv_parser.add_argument("--model", default="claude-sonnet-4-20250514", help="Model for evaluator")
    solv_parser.set_defaults(func=cmd_eval_solvability)

    # psm record-outcome
    outcome_parser = sub.add_parser("record-outcome", help="Record a hypothesis outcome")
    outcome_parser.add_argument("hypothesis_id", help="Hypothesis ID (e.g. HYP-001)")
    outcome_parser.add_argument("outcome", choices=["validated", "invalidated"], help="Outcome")
    outcome_parser.set_defaults(func=cmd_record_outcome)

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
