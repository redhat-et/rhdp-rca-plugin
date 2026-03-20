#!/usr/bin/env python3
"""CLI for splunk-log-analysis skill."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import mlflow
from mlflow.entities import SpanType

# Support both module and direct execution
if __name__ == "__main__" and __package__ is None:
    # Running directly as scripts/cli.py - add parent to path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from scripts.config import Config
    from scripts.correlator import build_correlation_timeline, fetch_correlated_logs
    from scripts.job_parser import parse_job_log
    from scripts.log_fetcher import fetch_job_log
    from scripts.step4_fetch_github import GitHubClient, Step4Analyzer
else:
    # Running as module (-m scripts.cli)
    from .config import Config
    from .correlator import build_correlation_timeline, fetch_correlated_logs
    from .job_parser import parse_job_log
    from .log_fetcher import fetch_job_log
    from .step4_fetch_github import GitHubClient, Step4Analyzer


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


def upload_analysis_to_jumpbox(analysis_dir: Path, config: Config) -> bool:
    """Upload analysis directory to Jumpbox in db_analysis/$SESSION_ID/."""
    if not config.jumpbox_uri:
        print("  Skipping upload: JUMPBOX_URI not configured")
        return False

    # Parse JUMPBOX_URI format: "user@host -p port"
    parts = config.jumpbox_uri.split()
    if len(parts) < 1:
        print("  Error: Invalid JUMPBOX_URI format")
        return False

    ssh_target = parts[0]  # user@host
    ssh_port = None

    # Extract port if present
    if "-p" in parts:
        try:
            port_idx = parts.index("-p")
            if port_idx + 1 < len(parts):
                ssh_port = parts[port_idx + 1]
        except (ValueError, IndexError):
            pass

    session_id = os.environ.get("CLAUDE_SESSION_ID", "unknown")
    remote_base_dir = f"db_analysis/{session_id}"
    job_id = analysis_dir.name

    # Build SSH command
    ssh_cmd = ["ssh"]
    if ssh_port:
        ssh_cmd.extend(["-p", ssh_port])
    ssh_cmd.extend([ssh_target, f"mkdir -p {remote_base_dir}"])

    # Create remote directory structure
    try:
        subprocess.run(
            ssh_cmd,
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"  Error creating remote directory: {e}")
        return False

    # Upload analysis directory using rsync
    try:
        rsync_cmd = ["rsync"]
        if ssh_port:
            rsync_cmd.extend(["-e", f"ssh -p {ssh_port}"])
        rsync_cmd.extend(["-az", "--quiet", str(analysis_dir), f"{ssh_target}:{remote_base_dir}/"])

        subprocess.run(
            rsync_cmd,
            check=True,
            timeout=60,
        )
        print(f"  Uploaded to Jumpbox ({ssh_target}): {remote_base_dir}/{job_id}/")
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"  Error uploading to Jumpbox: {e}")
        return False


def get_step_name(step: int) -> str:
    """Get descriptive name for step."""
    names = {
        1: "job_context",
        2: "splunk_logs",
        3: "correlation",
        4: "github_fetch_history",
        5: "summary",
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


@mlflow.trace(name="Run full analysis", span_type=SpanType.CHAIN)
def cmd_analyze(args: argparse.Namespace, config: Config, span=None) -> int:
    """Run full analysis pipeline."""
    # --fetch only makes sense with --job-id, not --job-log
    if getattr(args, "fetch", False) and not args.job_id:
        print("Error: --fetch requires --job-id (it has no effect with --job-log)")
        return 1

    # Determine job log path - either from --job-log or by searching with --job-id
    if args.job_log:
        job_log_path = Path(args.job_log)
    elif args.job_id:
        # Search for job log in configured directory
        job_log_path = config.find_job_log(args.job_id)
        if job_log_path:
            print(f"Found job log: {job_log_path}")
        elif args.fetch:
            # Auto-fetch from remote server
            if not config.remote_host or not config.remote_log_dir:
                print("Error: --fetch requires REMOTE_HOST and REMOTE_DIR in settings")
                return 1
            if not config.job_logs_dir:
                print("Error: --fetch requires JOB_LOGS_DIR to be configured")
                return 1
            print("[Fetch] Job log not found locally, fetching from remote...")
            try:
                fetch_job_log(
                    args.job_id, config.job_logs_dir, config.remote_host, config.remote_log_dir
                )
            except (
                FileNotFoundError,
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
            ) as e:
                print(f"Error: Failed to fetch log: {e}")
                return 1
            job_log_path = config.find_job_log(args.job_id)
            if not job_log_path:
                print(f"Error: Log fetched but not found in {config.job_logs_dir}")
                return 1
            print(f"Found job log: {job_log_path}")
        else:
            if config.job_logs_dir:
                print(f"Error: No log file found for job {args.job_id} in {config.job_logs_dir}")
                print("  Hint: Use --fetch to automatically download from remote server")
            else:
                print(
                    "Error: JOB_LOGS_DIR not configured. Set it in environment variables (.claude/settings.json) or use --job-log"
                )
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

    # GitHub token validation will be done at Step 4 (where it's actually needed)
    github_errors = config.validate_github()
    if github_errors:
        print(f"Warning: GitHub configuration invalid: {', '.join(github_errors)}")
        print(
            "  Step 4 (GitHub fetching) will be skipped. Set GITHUB_TOKEN in environment variables (.claude/settings.json) to enable."
        )

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

    # Step 4: Fetch GitHub files
    print("\n[Step 4] Fetching GitHub files...")
    step4_path = None
    if github_errors:
        print("  Skipped: GitHub token not configured")
        step4_result = {
            "job_id": job_id,
            "skipped": True,
            "reason": "GitHub token not configured",
            "github_fetches": [],
        }
        step4_path = save_step(analysis_dir, 4, step4_result)
        print(f"  Output: {step4_path}")
    else:
        try:
            github_client = GitHubClient(config.github_token)
            analyzer = Step4Analyzer(job_id, analysis_dir, github_client)
            step4_result = analyzer.run()
            step4_path = save_step(analysis_dir, 4, step4_result)
            print(f"  GitHub fetches: {len(step4_result.get('github_fetches', []))}")
            print(f"  Output: {step4_path}")
        except Exception as e:
            print(f"  Error fetching GitHub files: {e}")
            return 1

    # Upload analysis to Jumpbox
    print("\n[Upload] Uploading analysis to Jumpbox...")
    upload_analysis_to_jumpbox(analysis_dir, config)

    # Print summary
    print("\n" + "=" * 60)
    print("Analysis Complete")
    print("=" * 60)
    print(f"\nAnalysis directory: {analysis_dir}")
    print("\nFiles created:")
    print("  - step1_job_context.json")
    print("  - step2_splunk_logs.json")
    print("  - step3_correlation.json")
    if step4_path and step4_path.exists():
        print("  - step4_github_fetch_history.json")

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


@mlflow.trace(name="Parse job log", span_type=SpanType.CHAIN)
def cmd_parse(args: argparse.Namespace, config: Config, span=None):
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


@mlflow.trace(name="Run Splunk query", span_type=SpanType.RETRIEVER)
def cmd_query(args: argparse.Namespace, config: Config, span=None):
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


@mlflow.trace(name="Show analysis status", span_type=SpanType.PARSER)
def cmd_status(args: argparse.Namespace, config: Config, span=None):
    """Show analysis status for a job."""
    analysis_dir = config.analysis_dir / args.job_id

    if not analysis_dir.exists():
        print(f"No analysis found for job {args.job_id}")
        return 1

    print(f"Analysis directory: {analysis_dir}")
    print("\nSteps:")

    for step in [1, 2, 3, 4, 5]:
        filename = f"step{step}_{get_step_name(step)}.json"
        path = analysis_dir / filename
        if path.exists():
            _ = load_step(analysis_dir, step)
            size = path.stat().st_size
            print(f"  [x] Step {step}: {filename} ({size} bytes)")
        else:
            print(f"  [ ] Step {step}: {filename}")

    return 0


def _run_session_start_hook(base_dir: Path):
    """Run MLflow autolog setup."""
    try:
        tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")
        experiment_name = os.environ.get("MLFLOW_EXPERIMENT_NAME", "Default")

        result = subprocess.run(
            ["mlflow", "autolog", "claude", "-u", tracking_uri, "-n", experiment_name],
            cwd=str(base_dir),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout:
            print(f"\n{result.stdout.strip()}")
    except Exception:
        pass

@mlflow.trace(name="root-cause-analysis", span_type=SpanType.TOOL)
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
    analyze_parser.add_argument(
        "--fetch",
        action="store_true",
        help="Fetch job log from remote server via SSH if not found locally",
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
    # Initialize tracer if available
    mlflow.update_current_trace(
        metadata={
            "mlflow.trace.session": f"{os.environ.get('CLAUDE_SESSION_ID')}",
            "mlflow.trace.user": os.environ.get("MLFLOW_TAG_USER"),
            "mlflow.source.name": "root-cause-analysis",
            "mlflow.source.git.repoURL": "https://github.com/redhat-et/aiops-skills/blob/main/skills/root-cause-analysis/SKILL.md",
        },
    )

    inputs = {
        "request": f"root-cause-analysis {args.command} {args} ",
        "job_id": str(getattr(args, "job_id", None)),
        "job_log": str(getattr(args, "job_log", None)),
        "query": getattr(args, "query", None),
        "earliest": getattr(args, "earliest", None),
        "latest": getattr(args, "latest", None),
        "max_results": getattr(args, "max_results", None),
        "command": args.command,
    }
    span = mlflow.get_current_active_span()
    span.set_inputs(inputs)
    # Dispatch command
    commands = {
        "analyze": cmd_analyze,
        "parse": cmd_parse,
        "query": cmd_query,
        "status": cmd_status,
    }

    outputs = commands[args.command](args, config, span)
    _run_session_start_hook(base_dir)
    return outputs


if __name__ == "__main__":
    sys.exit(main())
