"""Fetch job logs from remote server via SSH + rsync."""

import shlex
import subprocess
from pathlib import Path

import mlflow
from mlflow.entities import SpanType

SSH_TIMEOUT = 30
RSYNC_TIMEOUT = 120


@mlflow.trace(name="Fetch job log from remote", span_type=SpanType.RETRIEVER)
def fetch_job_log(job_id: str, local_dir: Path, remote_host: str, remote_dir: str) -> list[str]:
    """
    Fetch log files for a single job from the remote server.

    Args:
        job_id: Job identifier (with or without 'job_' prefix)
        local_dir: Local directory to store fetched logs
        remote_host: SSH host alias (from ~/.ssh/config)
        remote_dir: Remote directory containing log files

    Returns:
        List of filenames fetched

    Raises:
        FileNotFoundError: If no matching files found on remote
        subprocess.CalledProcessError: If SSH or rsync fails
    """
    normalized = job_id if job_id.startswith("job_") else f"job_{job_id}"
    local_dir.mkdir(parents=True, exist_ok=True)

    # Find matching files on remote (using -exec basename for portability across GNU/BSD find)
    remote_cmd = (
        f"cd {shlex.quote(remote_dir)} && "
        f"find . -maxdepth 1 -name {shlex.quote(normalized + '*')} -exec basename {{}} \\;"
    )
    result = subprocess.run(
        ["ssh", remote_host, remote_cmd],
        capture_output=True,
        text=True,
        check=True,
        timeout=SSH_TIMEOUT,
    )

    files = [f.strip() for f in result.stdout.splitlines() if f.strip()]
    if not files:
        raise FileNotFoundError(
            f"No log files found on {remote_host}:{remote_dir} for {normalized}"
        )

    print(f"[Fetch] Found {len(files)} file(s) on remote: {', '.join(files)}")

    subprocess.run(
        [
            "rsync",
            "-avz",
            "--progress",
            "--files-from=-",
            f"{remote_host}:{remote_dir}/",
            str(local_dir),
        ],
        input="\n".join(files),
        text=True,
        check=True,
        timeout=RSYNC_TIMEOUT,
    )

    print(f"[Fetch] Files transferred to {local_dir}")
    return files
