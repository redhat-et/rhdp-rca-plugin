#!/usr/bin/env python3
"""CLI for Root-Cause-Analysis skill."""

import argparse
import json
import sys
from pathlib import Path

# Support both module and direct execution
if __name__ == "__main__" and __package__ is None:
    # Running directly as scripts/cli.py - add parent to path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from scripts.config import Config
    from scripts.correlator import build_correlation_timeline, fetch_correlated_logs
    from scripts.job_parser import parse_job_log
else:
    # Running as module (-m scripts.cli)
    from .config import Config
    from .correlator import build_correlation_timeline, fetch_correlated_logs
    from .job_parser import parse_job_log


def get_analysis_dir(config: Config, job_id: str) -> Path:
    """Get or create analysis directory for a job."""
    analysis_dir = config.analysis_dir / job_id
    analysis_dir.mkdir(parents=True, exist_ok=True)
    return analysis_dir


def save_step(analysis_dir: Path, step: int, data: dict) -> Path:
    """Save step output to JSON file."""
    filename = f"step{step}_{get_step_name(step)}.json"
    output_path = analysis_dir / filename
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    return output_path


def get_step_name(step: int) -> str:
    """Get descriptive name for step."""
    names = {
        1: "job_context",
        2: "splunk_logs",
        3: "correlation",
        4: "summary",
    }
    return names.get(step, f"step{step}")


def load_step(analysis_dir: Path, step: int) -> dict | None:
    """Load step output from JSON file."""
    filename = f"step{step}_{get_step_name(step)}.json"
    path = analysis_dir / filename
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


def cmd_analyze(args: argparse.Namespace, config: Config) -> int:
    """Run full analysis pipeline."""
    # Determine job log path - either from --job-log or by searching with --job-id
    if args.job_log:
        job_log_path = Path(args.job_log)
    elif args.job_id:
        # Search for job log in configured directory
        job_log_path = config.find_job_log(args.job_id)
        if job_log_path:
            print(f"Found job log: {job_log_path}")
        else:
            if config.job_logs_dir:
                print(f"Error: No log file found for job {args.job_id} in {config.job_logs_dir}")
            else:
                print("Error: JOB_LOGS_DIR not configured. Set it in .env or use --job-log")
            return 1
    else:
        print("Error: Either --job-log or --job-id is required")
        return 1

    if not job_log_path.exists():
        print(f"Error: Job log file not found: {job_log_path}")
        return 1

    # Validate Splunk config
    errors = config.validate_splunk()
    if errors:
        print(f"Error: Splunk configuration invalid: {', '.join(errors)}")
        return 1

    print("=" * 60)
    print("Splunk Log Analysis")
    print("=" * 60)

    # Step 1: Parse job log
    print("\n[Step 1] Parsing job log...")
    job_context = parse_job_log(job_log_path)

    job_id = args.job_id or job_context.get("job_id") or "unknown"
    analysis_dir = get_analysis_dir(config, job_id)

    step1_path = save_step(analysis_dir, 1, job_context)
    print(f"  Job ID: {job_context.get('job_id')}")
    print(f"  GUID: {job_context.get('guid')}")
    print(f"  Namespace: {job_context.get('namespace')}")
    print(f"  Status: {job_context.get('status')}")
    print(f"  Failed tasks: {len(job_context.get('failed_tasks', []))}")
    print(f"  Output: {step1_path}")

    # Step 2: Fetch Splunk logs
    print("\n[Step 2] Fetching Splunk logs...")
    try:
        splunk_logs = fetch_correlated_logs(config, job_context)
        step2_path = save_step(analysis_dir, 2, splunk_logs)
        print(f"  OCP logs: {len(splunk_logs.get('ocp_logs', []))}")
        print(f"  Error logs: {len(splunk_logs.get('error_logs', []))}")
        print(f"  Pods found: {len(splunk_logs.get('pods_found', []))}")
        print(f"  Output: {step2_path}")
    except Exception as e:
        print(f"  Error fetching Splunk logs: {e}")
        splunk_logs = {"ocp_logs": [], "error_logs": [], "pods_found": [], "errors": [str(e)]}
        save_step(analysis_dir, 2, splunk_logs)

    # Step 3: Build correlation
    print("\n[Step 3] Building correlation timeline...")
    correlation = build_correlation_timeline(job_context, splunk_logs)
    step3_path = save_step(analysis_dir, 3, correlation)

    corr = correlation.get("correlation", {})
    print(f"  Correlation method: {corr.get('method')}")
    print(f"  Confidence: {corr.get('confidence')}")
    print(f"  Time overlap: {corr.get('time_overlap', {}).get('overlap_confirmed')}")
    print(f"  Timeline events: {len(correlation.get('timeline_events', []))}")
    print(f"  Output: {step3_path}")

    # Print summary
    print("\n" + "=" * 60)
    print("Analysis Complete")
    print("=" * 60)
    print(f"\nAnalysis directory: {analysis_dir}")
    print("\nFiles created:")
    print("  - step1_job_context.json")
    print("  - step2_splunk_logs.json")
    print("  - step3_correlation.json")

    print("\n[Step 4] Next: Review correlation and write summary")
    print("  Read the correlation file and create step4_summary.json")
    print("  See SKILL.md for the summary schema")

    # Print quick summary if high confidence correlation
    if corr.get("confidence") == "high":
        print("\n" + "-" * 60)
        print("Quick Summary")
        print("-" * 60)
        _print_quick_summary(job_context, splunk_logs, correlation)

    return 0


def _print_quick_summary(job_context: dict, splunk_logs: dict, correlation: dict):
    """Print a quick summary of the analysis."""
    print(f"\nJob {job_context.get('job_id')} ({job_context.get('status')})")
    print(f"GUID: {job_context.get('guid')}")
    print(f"Namespace: {job_context.get('namespace')}")

    # Failed tasks
    failed = job_context.get("failed_tasks", [])
    if failed:
        print(f"\nFailed Tasks ({len(failed)}):")
        for task in failed[:3]:
            print(f"  - {task.get('task')}: {task.get('error_message', '')[:80]}")

    # Correlated pods
    pods = splunk_logs.get("pods_found", [])
    if pods:
        print(f"\nCorrelated Pods ({len(pods)}):")
        for pod in pods[:5]:
            print(f"  - {pod.get('pod_name')} ({', '.join(pod.get('containers', []))})")

    # Sample errors from Splunk
    errors = splunk_logs.get("error_logs", [])
    if errors:
        print(f"\nSplunk Errors ({len(errors)}):")
        for err in errors[:3]:
            msg = err.get("message", "")[:100]
            print(f"  - [{err.get('pod_name')}] {msg}")

    # Correlation confidence
    corr = correlation.get("correlation", {})
    print(f"\nCorrelation: {corr.get('method')} ({corr.get('confidence')} confidence)")


def cmd_parse(args: argparse.Namespace, config: Config) -> int:
    """Parse job log only (Step 1)."""
    job_log_path = Path(args.job_log)

    if not job_log_path.exists():
        print(f"Error: Job log file not found: {job_log_path}")
        return 1

    job_context = parse_job_log(job_log_path)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(job_context, f, indent=2, default=str)
        print(f"Output written to: {args.output}")
    else:
        print(json.dumps(job_context, indent=2, default=str))

    return 0


def cmd_query(args: argparse.Namespace, config: Config) -> int:
    """Run ad-hoc Splunk query."""
    from .splunk_client import SplunkClient

    errors = config.validate_splunk()
    if errors:
        print(f"Error: {', '.join(errors)}")
        return 1

    client = SplunkClient(config)

    try:
        results = client.query(
            args.query,
            earliest=args.earliest,
            latest=args.latest,
            max_results=args.max_results,
        )
        print(f"Found {len(results)} results")

        if args.output:
            with open(args.output, "w") as f:
                json.dump(results, f, indent=2)
            print(f"Output written to: {args.output}")
        else:
            for r in results[:10]:
                raw = r.get("_raw", "")[:200]
                print(f"{r.get('_time', '')}: {raw}")

    except Exception as e:
        print(f"Query failed: {e}")
        return 1

    return 0


def cmd_status(args: argparse.Namespace, config: Config) -> int:
    """Show analysis status for a job."""
    analysis_dir = config.analysis_dir / args.job_id

    if not analysis_dir.exists():
        print(f"No analysis found for job {args.job_id}")
        return 1

    print(f"Analysis directory: {analysis_dir}")
    print("\nSteps:")

    for step in [1, 2, 3, 4]:
        filename = f"step{step}_{get_step_name(step)}.json"
        path = analysis_dir / filename
        if path.exists():
            _ = load_step(analysis_dir, step)
            size = path.stat().st_size
            print(f"  [x] Step {step}: {filename} ({size} bytes)")
        else:
            print(f"  [ ] Step {step}: {filename}")

    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Splunk Log Analysis - Correlate AAP job logs with Splunk OCP logs"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Run full analysis pipeline")
    analyze_parser.add_argument("--job-log", help="Path to job log file (.json or .json.gz)")
    analyze_parser.add_argument(
        "--job-id", help="Job ID to analyze (searches JOB_LOGS_DIR if --job-log not provided)"
    )

    # parse command
    parse_parser = subparsers.add_parser("parse", help="Parse job log only (Step 1)")
    parse_parser.add_argument("--job-log", required=True, help="Path to job log file")
    parse_parser.add_argument("--output", "-o", help="Output file (default: stdout)")

    # query command
    query_parser = subparsers.add_parser("query", help="Run ad-hoc Splunk query")
    query_parser.add_argument("query", help="Splunk search query")
    query_parser.add_argument("--earliest", default="-24h", help="Earliest time (default: -24h)")
    query_parser.add_argument("--latest", default="now", help="Latest time (default: now)")
    query_parser.add_argument(
        "--max-results", type=int, default=100, help="Max results (default: 100)"
    )
    query_parser.add_argument("--output", "-o", help="Output file (default: print summary)")

    # status command
    status_parser = subparsers.add_parser("status", help="Show analysis status")
    status_parser.add_argument("job_id", help="Job ID to check")

    args = parser.parse_args()

    # Load config
    base_dir = Path(__file__).parent.parent
    config = Config.from_env(base_dir)

    # Dispatch command
    commands = {
        "analyze": cmd_analyze,
        "parse": cmd_parse,
        "query": cmd_query,
        "status": cmd_status,
    }

    return commands[args.command](args, config)


if __name__ == "__main__":
    sys.exit(main())
