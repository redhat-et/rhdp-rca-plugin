#!/usr/bin/env python3
"""CLI for Root-Cause-Analysis skill."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Support both module and direct execution
if __name__ == "__main__" and __package__ is None:
    # Running directly as scripts/cli.py - add parent to path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from scripts.classify import classify_job_errors, resolve_known_failures
    from scripts.config import Config
    from scripts.correlator import build_correlation_timeline, fetch_correlated_logs
    from scripts.job_parser import parse_job_log
    from scripts.log_fetcher import fetch_job_log
    from scripts.setup import print_checks, run_checks
    from scripts.step4_fetch_github import GitHubClient, Step4Analyzer
    from scripts.tracing import HAS_MLFLOW, SpanType, mlflow, trace
else:
    # Running as module (-m scripts.cli)
    from .classify import classify_job_errors, resolve_known_failures
    from .config import Config
    from .correlator import build_correlation_timeline, fetch_correlated_logs
    from .job_parser import parse_job_log
    from .log_fetcher import fetch_job_log
    from .setup import print_checks, run_checks
    from .step4_fetch_github import GitHubClient, Step4Analyzer
    from .tracing import HAS_MLFLOW, SpanType, mlflow, trace


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


@trace(name="Upload analysis", span_type=SpanType.CHAIN if SpanType else None)
def upload_analysis_to_jumpbox(args: argparse.Namespace, config: Config, span=None) -> int:
    """Upload analysis directory to Jumpbox in /usr/local/mlflow/{job_id}/ with session.json."""
    analysis_dir = config.analysis_dir / args.job_id

    if not analysis_dir.exists():
        error_message = f"No analysis found for job {args.job_id}"
        print(error_message)
        if span:
            span.set_outputs({"error": error_message})
        return 1

    print(f"Uploading analysis for job {args.job_id}...")

    if not config.jumpbox_uri:
        print("  Skipping upload: JUMPBOX_URI not configured")
        return 1

    # Parse JUMPBOX_URI format: "user@host -p port"
    parts = config.jumpbox_uri.split()
    if len(parts) < 1:
        print("  Error: Invalid JUMPBOX_URI format")
        return 1

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
    job_id = args.job_id
    remote_base_dir = f"/usr/local/mlflow/{job_id}"

    # Create session.json file locally (will be overwritten if exists)
    session_file = analysis_dir / "session.json"
    try:
        with open(session_file, "w") as f:
            json.dump({"session_id": session_id, "job_id": job_id}, f, indent=2)
    except Exception as e:
        print(f"  Warning: Could not create session.json: {e}")

    # Build SSH command to create remote directory
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
        return 1

    # Upload analysis directory contents using rsync
    try:
        rsync_cmd = ["rsync"]
        if ssh_port:
            rsync_cmd.extend(["-e", f"ssh -p {ssh_port}"])
        # Add trailing slash to source to copy contents, not the directory itself
        rsync_cmd.extend(
            ["-az", "--quiet", f"{str(analysis_dir)}/", f"{ssh_target}:{remote_base_dir}/"]
        )

        subprocess.run(
            rsync_cmd,
            check=True,
            timeout=60,
        )
        print(f"  Uploaded to Jumpbox ({ssh_target}): {remote_base_dir}/")
        if span:
            span.set_outputs({"job_id": job_id, "success": True})
        return 0
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"  Error uploading to Jumpbox: {e}")
        if span:
            span.set_outputs({"job_id": job_id, "success": False, "error": str(e)})
        return 1


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


@trace(name="Run full analysis", span_type=SpanType.CHAIN if SpanType else None)
def cmd_analyze(args: argparse.Namespace, config: Config, span=None) -> int:
    """Run full analysis pipeline."""
    # --fetch only makes sense with --job-id, not --job-log
    if getattr(args, "fetch", False) and not args.job_id:
        error_message = "--fetch requires --job-id (it has no effect with --job-log)"
        print(f"Error: {error_message}")
        if span:
            span.set_outputs({"error": error_message})
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
                error_message = "--fetch requires REMOTE_HOST and REMOTE_DIR in settings"
                print(f"Error: {error_message}")
                if span:
                    span.set_outputs({"error": error_message})
                return 1
            if not config.job_logs_dir:
                error_message = "--fetch requires JOB_LOGS_DIR to be configured"
                print(f"Error: {error_message}")
                if span:
                    span.set_outputs({"error": error_message})
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
                error_message = f"Failed to fetch log: {e}"
                print(f"Error: {error_message}")
                if span:
                    span.set_outputs({"error": error_message})
                return 1
            job_log_path = config.find_job_log(args.job_id)
            if not job_log_path:
                error_message = f"Log fetched but not found in {config.job_logs_dir}"
                print(f"Error: {error_message}")
                if span:
                    span.set_outputs({"error": error_message})
                return 1
            print(f"Found job log: {job_log_path}")
        else:
            if config.job_logs_dir:
                error_message = f"No log file found for job {args.job_id} in {config.job_logs_dir}. Hint: Use --fetch to automatically download from remote server"
                print(error_message)
            else:
                error_message = "JOB_LOGS_DIR not configured. Set it in environment variables (.claude/settings.json) or use --job-log"
                print(f"Error: {error_message}")
            if span:
                span.set_outputs({"error": error_message})
            return 1
    else:
        error_message = "Either --job-log or --job-id is required"
        print(f"Error: {error_message}")
        if span:
            span.set_outputs({"error": error_message})
        return 1

    if not job_log_path.exists():
        error_message = f"Job log file not found: {job_log_path}"
        print(f"Error: {error_message}")
        if span:
            span.set_outputs({"error": error_message})
        return 1

    # Validate Splunk config
    splunk_errors = config.validate_splunk()
    if splunk_errors:
        print(f"Warning: Splunk configuration invalid: {', '.join(splunk_errors)}")
        print(
            "  Step 2 (Splunk log fetch) will be skipped. Set SPLUNK_HOST/SPLUNK_USERNAME/SPLUNK_PASSWORD in .claude/settings.json to enable."
        )
    skip_splunk = bool(splunk_errors)

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
    if skip_splunk:
        print("  Skipped: Splunk not configured")
        splunk_logs = {
            "ocp_logs": [],
            "error_logs": [],
            "pods_found": [],
            "skipped": True,
            "reason": "Splunk not configured",
        }
        save_step(analysis_dir, 2, splunk_logs)
    else:
        try:
            splunk_logs = fetch_correlated_logs(config, job_context)
            step2_path = save_step(analysis_dir, 2, splunk_logs)
            ocp_logs = splunk_logs.get("ocp_logs", [])
            error_logs = splunk_logs.get("error_logs", [])
            pods_found = splunk_logs.get("pods_found", [])
            print(f"  OCP logs: {len(ocp_logs) if isinstance(ocp_logs, list) else 0}")
            print(f"  Error logs: {len(error_logs) if isinstance(error_logs, list) else 0}")
            print(f"  Pods found: {len(pods_found) if isinstance(pods_found, list) else 0}")
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
        print("Skipped: GitHub token not configured")
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
            error_message = f"Error fetching GitHub files: {e}"
            print(f"  {error_message}")
            if span:
                span.set_outputs({"error": error_message})
            return 1

    # Classify errors against known failure patterns (optional)
    print("\n[Classify] Matching errors against known failure patterns...")
    known_failures = resolve_known_failures(
        url=getattr(args, "known_failures_url", None),
        local_path=getattr(args, "known_failures_file", None),
    )
    classification_path = analysis_dir / "classification.json"
    if known_failures:
        classifications = classify_job_errors(job_context, correlation, known_failures)
        classification_result = {
            "patterns_loaded": len(known_failures),
            "matches": classifications,
        }
        with open(classification_path, "w") as f:
            json.dump(classification_result, f, indent=2)
        if classifications:
            print(f"  Matched {len(classifications)} error(s) against known patterns")
            for c in classifications:
                print(f"    - {c['error_category']}: {c['failure_description']}")
        else:
            print("  No matches — errors may be novel/unclassified")
        print(f"  Output: {classification_path}")
    else:
        print("  Skipped: no known failure patterns configured (optional)")
        print(
            "  Hint: Use --known-failures-file <path> or --known-failures-url <url>,"
            " or set KNOWN_FAILED_YAML_URL / KNOWN_FAILED_YAML"
            " in .claude/settings.local.json env block"
        )

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

    outputs = {
        "job_id": job_id,
        "status": job_context.get("status"),
        "correlation_confidence": corr.get("confidence"),
        "failed_tasks": len(job_context.get("failed_tasks", [])),
        "pods_found": len(splunk_logs.get("pods_found", [])),
        "analysis_dir": str(analysis_dir),
    }
    if span:
        span.set_outputs(outputs)
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


@trace(name="Parse job log", span_type=SpanType.CHAIN if SpanType else None)
def cmd_parse(args: argparse.Namespace, config: Config, span=None) -> int:
    """Parse job log only (Step 1)."""
    job_log_path = Path(args.job_log)

    if not job_log_path.exists():
        error_message = f"Job log file not found: {job_log_path}"
        print(f"Error: {error_message}")
        if span:
            span.set_outputs({"error": error_message})
        return 1

    job_context = parse_job_log(job_log_path)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(job_context, f, indent=2, default=str)
        print(f"Output written to: {args.output}")
    else:
        print(json.dumps(job_context, indent=2, default=str))

    outputs = {
        "job_id": job_context.get("job_id"),
        "status": job_context.get("status"),
        "output_file": args.output or "stdout",
    }
    if span:
        span.set_outputs(outputs)

    return 0


@trace(name="Run Splunk query", span_type=SpanType.RETRIEVER if SpanType else None)
def cmd_query(args: argparse.Namespace, config: Config, span=None) -> int:
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


def cmd_setup(args: argparse.Namespace, config: Config, span=None) -> int:
    """Run preflight checks for all prerequisites."""
    base_dir = Path(__file__).parent.parent
    repo_root = base_dir.parent.parent  # skills/ -> repo root
    results = run_checks(base_dir, repo_root)

    if getattr(args, "json", False):
        print(json.dumps(results, indent=2))
        return 0 if all(r["status"] == "ok" for r in results) else 1

    issues = print_checks(results)
    return 0 if issues == 0 else 1


def cmd_status(args: argparse.Namespace, config: Config, span=None) -> int:
    """Show analysis status for a job."""
    analysis_dir = config.analysis_dir / args.job_id

    if not analysis_dir.exists():
        error_message = f"No analysis found for job {args.job_id}"
        print(error_message)
        if span:
            span.set_outputs({"error": error_message})
        return 1

    print(f"Analysis directory: {analysis_dir}")
    print("\nSteps:")

    for step in [1, 2, 3, 4, 5]:
        filename = f"step{step}_{get_step_name(step)}.json"
        path = analysis_dir / filename
        if path.exists():
            size = path.stat().st_size
            print(f"  [x] Step {step}: {filename} ({size} bytes)")
        else:
            print(f"  [ ] Step {step}: {filename}")

    return 0


def _run_mlflow_autolog(base_dir: Path):
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


@trace(name="root-cause-analysis", span_type=SpanType.TOOL if SpanType else None)
def main() -> int:
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
    analyze_parser.add_argument(
        "--known-failures-url",
        help="URL to fetch known_failed.yaml from (overrides KNOWN_FAILED_YAML_URL env var)",
    )
    analyze_parser.add_argument(
        "--known-failures-file",
        help="Local path to known_failed.yaml (overrides KNOWN_FAILED_YAML env var)",
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

    # setup command
    setup_parser = subparsers.add_parser("setup", help="Check prerequisites and configuration")
    setup_parser.add_argument("--json", action="store_true", help="Output results as JSON")

    # status command
    status_parser = subparsers.add_parser("status", help="Show analysis status")
    status_parser.add_argument("job_id", help="Job ID to check")

    # upload command
    upload_parser = subparsers.add_parser("upload", help="Upload analysis to Jumpbox")
    upload_parser.add_argument("--job-id", required=True, help="Job ID to upload")

    args = parser.parse_args()

    # Load config
    base_dir = Path(__file__).parent.parent
    config = Config.from_env(base_dir)
    span = None
    if HAS_MLFLOW:
        mlflow.update_current_trace(
            metadata={
                "mlflow.trace.session": f"{os.environ.get('CLAUDE_SESSION_ID')}",
                "mlflow.trace.user": os.environ.get("MLFLOW_TAG_USER"),
                "mlflow.source.name": "root-cause-analysis",
            },
        )
        span = mlflow.get_current_active_span()
        if span:
            span.set_inputs(
                {
                    "request": f"root-cause-analysis {args.command} {args} ",
                    "job_id": str(getattr(args, "job_id", None)),
                    "job_log": str(getattr(args, "job_log", None)),
                    "command": args.command,
                }
            )
    # Dispatch command
    commands = {
        "analyze": cmd_analyze,
        "parse": cmd_parse,
        "query": cmd_query,
        "setup": cmd_setup,
        "status": cmd_status,
        "upload": upload_analysis_to_jumpbox,
    }

    exit_code = commands[args.command](args, config, span)
    _run_mlflow_autolog(base_dir)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
