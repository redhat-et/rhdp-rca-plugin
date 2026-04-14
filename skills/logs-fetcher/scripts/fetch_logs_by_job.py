#!/usr/bin/env python3
import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

import mlflow
from mlflow.entities import SpanType

# --- Defaults: adjust if needed ---
REMOTE_HOST = os.environ.get("REMOTE_HOST")
REMOTE_DIR = os.environ.get("REMOTE_DIR")
DEFAULT_LOCAL_DIR = Path.home() / "etl-logs"


@mlflow.trace(name="Fetch job logs by number", span_type=SpanType.RETRIEVER)
def fetch_job_logs(job_numbers: list[str], local_dir: Path) -> dict:
    """
    Fetch specific job log files by job number (e.g., job_1234567).

    job_numbers: List of job identifiers (with or without 'job_' prefix)
    local_dir: Local directory to store the logs
    """
    local_dir.mkdir(parents=True, exist_ok=True)

    # Normalize job numbers to ensure they have 'job_' prefix
    normalized_jobs = []
    for job in job_numbers:
        if not job.startswith("job_"):
            normalized_jobs.append(f"job_{job}")
        else:
            normalized_jobs.append(job)

    print(f"[INFO] Remote host: {REMOTE_HOST}")
    print(f"[INFO] Remote dir : {REMOTE_DIR}")
    print(f"[INFO] Local dir  : {local_dir}")
    print(f"[INFO] Job numbers: {', '.join(normalized_jobs)}")

    # Build pattern to match any transform status for these jobs
    # Pattern: job_XXXXXX*.transform-*
    patterns = [f"{job}*.transform-*" for job in normalized_jobs]

    # Build remote find command to locate matching files
    # Using find with -name and -o (or) to match any of the patterns
    find_conditions = []
    for pattern in patterns:
        find_conditions.append(f"-name {shlex.quote(pattern)}")

    find_cmd_parts = " -o ".join(find_conditions)
    remote_cmd = f"cd {shlex.quote(REMOTE_DIR)} && find . -maxdepth 1 \\( {find_cmd_parts} \\) -printf '%f\\n'"

    print("[INFO] Finding files matching job patterns...")

    # 1) ssh to get the file list (filenames only, one per line)
    ssh_proc = subprocess.Popen(
        ["ssh", REMOTE_HOST, remote_cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    stdout, stderr = ssh_proc.communicate()

    if ssh_proc.returncode != 0:
        print(f"[ERROR] SSH command failed with return code {ssh_proc.returncode}")
        if stderr:
            print("[SSH STDERR]")
            print(stderr)
        sys.exit(1)

    # Filter out empty lines
    files_found = [line.strip() for line in stdout.split("\n") if line.strip()]

    if not files_found:
        print(f"[WARNING] No files found for job numbers: {', '.join(normalized_jobs)}")
        print("[INFO] Make sure the job numbers are correct and files exist on the remote server")

    print(f"[INFO] Found {len(files_found)} file(s):")
    for f in files_found:
        print(f"  - {f}")

    # 2) rsync using the file list via stdin (files-from=-)
    rsync_cmd = [
        "rsync",
        "-avz",
        "--progress",
        "--files-from=-",
        f"{REMOTE_HOST}:{REMOTE_DIR}/",
        str(local_dir),
    ]

    print("[INFO] Running rsync...")

    try:
        # Pass the file list to rsync via stdin
        subprocess.run(
            rsync_cmd,
            input="\n".join(files_found),
            text=True,
            check=True,
        )
        print(f"[SUCCESS] Files transferred to {local_dir}")
    except subprocess.CalledProcessError as e:
        print("[ERROR] rsync failed")
        raise e
    return {
        "status": "success",
        "local_dir": str(local_dir),
        "job_numbers": normalized_jobs,
        "files_found": len(files_found),
    }


@mlflow.trace(name="Logs fetcher by job", span_type=SpanType.TOOL)
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Fetch specific AAP2 ETL log files by job number via ssh + rsync."
    )
    parser.add_argument(
        "job_numbers",
        nargs="+",
        help="Job numbers to fetch (e.g., job_1234567 or 1234567). Can specify multiple.",
    )
    parser.add_argument(
        "--local-dir",
        type=Path,
        default=DEFAULT_LOCAL_DIR,
        help=f"Local directory to store logs (default: {DEFAULT_LOCAL_DIR})",
    )

    args = parser.parse_args(argv)

    span = mlflow.get_current_active_span()
    if span:
        span.set_inputs(
            {
                "request": f"log-fetcher via jobs {args}",
                "job_numbers": args.job_numbers,
                "local_dir": str(args.local_dir),
            }
        )

    mlflow.update_current_trace(
        metadata={
            "mlflow.trace.session": f"{os.environ.get('CLAUDE_SESSION_ID')}",
            "mlflow.trace.user": os.environ.get("MLFLOW_TAG_USER"),
            "mlflow.source.name": "logs-fetcher",
            "mlflow.source.git.repoURL": "https://github.com/redhat-et/aiops-skills/blob/main/skills/logs-fetcher/SKILL.md",
        },
    )

    return fetch_job_logs(
        job_numbers=args.job_numbers,
        local_dir=args.local_dir,
    )


if __name__ == "__main__":
    sys.exit(main())
