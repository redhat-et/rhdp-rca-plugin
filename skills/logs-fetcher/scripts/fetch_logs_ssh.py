#!/usr/bin/env python3
import argparse

# --- Defaults: adjust if needed ---
import os
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import mlflow
from mlflow.entities import SpanType

REMOTE_HOST = os.environ.get("REMOTE_HOST")
REMOTE_DIR = os.environ.get("REMOTE_DIR")
DEFAULT_LOCAL_DIR = Path(os.environ.get("DEFAULT_LOCAL_DIR", Path.home() / "aiops_extracted_logs"))


@mlflow.trace(name="Parse datetime string", span_type=SpanType.PARSER)
def parse_datetime(dt_str: str) -> datetime:
    """
    Parse datetime string in ISO format (YYYY-MM-DD HH:MM:SS or YYYY-MM-DD).
    Returns datetime object.
    """
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Invalid datetime format: {dt_str}. Expected YYYY-MM-DD [HH:MM[:SS]]")


@mlflow.trace(name="Build remote ls command", span_type=SpanType.TOOL)
def build_remote_ls_command(
    mode: str,
    order: str,
    limit: int | None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> str:
    """
    Build the remote shell command that lists matching files in the desired order.

    mode       : 'processed' | 'ignored' | 'all'
    order      : 'desc' (newest first) | 'asc' (oldest first)
    limit      : int or None
    start_time : Optional start datetime string (inclusive)
    end_time   : Optional end datetime string (inclusive)
    """
    # Patterns based on suffix
    if mode == "processed":
        pattern = "*.transform-processed"
    elif mode == "ignored":
        pattern = "*.transform-ignored"
    elif mode == "all":
        pattern = "*.transform-*"
    else:
        raise ValueError(f"Unknown mode: {mode}")

    # Build command based on whether time filtering is needed
    if start_time or end_time:
        # Use find with time-based filtering
        find_parts = [f"find . -maxdepth 1 -type f -name '{pattern}'"]

        if start_time:
            # Parse the start time and convert to find's -newermt format
            start_dt = parse_datetime(start_time)
            find_parts.append(f"-newermt '{start_dt.strftime('%Y-%m-%d %H:%M:%S')}'")

        if end_time:
            # Parse the end time and use ! -newermt to get files before/at end time
            end_dt = parse_datetime(end_time)
            find_parts.append(f"! -newermt '{end_dt.strftime('%Y-%m-%d %H:%M:%S')}'")

        # Remove './' prefix from find output and sort
        find_cmd = " ".join(find_parts)
        if order == "desc":
            # Sort by mtime, newest first
            list_cmd = f"{find_cmd} -printf '%T@ %f\\n' | sort -rn | cut -d' ' -f2-"
        elif order == "asc":
            # Sort by mtime, oldest first
            list_cmd = f"{find_cmd} -printf '%T@ %f\\n' | sort -n | cut -d' ' -f2-"
        else:
            raise ValueError(f"Unknown order: {order}")
    else:
        # Use ls for simpler cases without time filtering
        # Sort by mtime:
        #   ls -1t  -> newest first (desc)
        #   ls -1tr -> oldest first (asc)
        # IMPORTANT: do NOT quote the glob, or the shell won't expand it.
        if order == "desc":
            list_cmd = f"ls -1t {pattern}"
        elif order == "asc":
            list_cmd = f"ls -1tr {pattern}"
        else:
            raise ValueError(f"Unknown order: {order}")

    if limit is not None:
        list_cmd += f" | head -n {int(limit)}"

    # cd into the directory first so output is just filenames
    cmd = f"cd {shlex.quote(REMOTE_DIR)} && {list_cmd}"
    return cmd


@mlflow.trace(name="Run SSH sync", span_type=SpanType.RETRIEVER)
def run_sync(
    local_dir: Path,
    mode: str,
    order: str,
    limit: int | None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> None:
    local_dir.mkdir(parents=True, exist_ok=True)

    remote_cmd = build_remote_ls_command(mode, order, limit, start_time, end_time)

    print(f"[INFO] Remote host: {REMOTE_HOST}")
    print(f"[INFO] Remote dir : {REMOTE_DIR}")
    print(f"[INFO] Local dir  : {local_dir}")
    print(f"[INFO] Mode       : {mode}")
    print(f"[INFO] Order      : {order}")
    print(f"[INFO] Limit      : {limit if limit is not None else 'no limit'}")
    if start_time:
        print(f"[INFO] Start time : {start_time}")
    if end_time:
        print(f"[INFO] End time   : {end_time}")
    print(f"[INFO] Remote cmd : {remote_cmd}")

    # 1) ssh to get the file list (filenames only, one per line)
    ssh_proc = subprocess.Popen(
        ["ssh", REMOTE_HOST, remote_cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # 2) rsync using that file list via stdin (files-from=-)
    rsync_cmd = [
        "rsync",
        "-avz",
        "--progress",
        "--files-from=-",
        f"{REMOTE_HOST}:{REMOTE_DIR}/",
        str(local_dir),
    ]

    print(f"[INFO] Running rsync: {' '.join(rsync_cmd)}")

    try:
        subprocess.run(
            rsync_cmd,
            stdin=ssh_proc.stdout,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        # Read SSH stderr for clues
        _, ssh_err = ssh_proc.communicate()
        print("[ERROR] rsync failed")
        if ssh_err:
            print("[SSH STDERR]")
            print(ssh_err)
        raise e
    finally:
        if ssh_proc.stdout:
            ssh_proc.stdout.close()
        ssh_proc.wait()

    return {
        "status": "success",
        "local_dir": str(local_dir),
        "mode": mode,
        "order": order,
        "limit": limit,
    }


@mlflow.trace(name="Logs fetcher by ssh", span_type=SpanType.TOOL)
def main(argv=None):
    parser = argparse.ArgumentParser(description="Fetch AAP2 ETL log files via ssh + rsync.")
    parser.add_argument(
        "--local-dir",
        type=Path,
        default=DEFAULT_LOCAL_DIR,
        help=f"Local directory to store logs (default: {DEFAULT_LOCAL_DIR})",
    )
    parser.add_argument(
        "--mode",
        choices=["processed", "ignored", "all"],
        default="processed",
        help="Which logs to select: processed, ignored, or all (default: processed)",
    )
    parser.add_argument(
        "--order",
        choices=["desc", "asc"],
        default="desc",
        help="Sort by modification time: desc = newest first, asc = oldest first (default: desc)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only transfer the first N files after sorting (default: no limit)",
    )
    parser.add_argument(
        "--start-time",
        type=str,
        default=None,
        help="Filter logs created on or after this time (format: 'YYYY-MM-DD [HH:MM[:SS]]')",
    )
    parser.add_argument(
        "--end-time",
        type=str,
        default=None,
        help="Filter logs created on or before this time (format: 'YYYY-MM-DD [HH:MM[:SS]]')",
    )

    args = parser.parse_args(argv)

    span = mlflow.get_current_active_span()
    if span:
        span.set_inputs(
            {
                "request": f"log-fetcher via ssh{args}",
                "local_dir": str(args.local_dir),
                "mode": args.mode,
                "order": args.order,
                "limit": args.limit,
                "start_time": args.start_time,
                "end_time": args.end_time,
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

    result = run_sync(
        local_dir=args.local_dir,
        mode=args.mode,
        order=args.order,
        limit=args.limit,
        start_time=args.start_time,
        end_time=args.end_time,
    )

    return result


if __name__ == "__main__":
    sys.exit(main())
