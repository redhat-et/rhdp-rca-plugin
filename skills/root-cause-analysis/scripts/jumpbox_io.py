"""
Jumpbox I/O functions for Root-Cause-Analysis.

Provides functions to parse JUMPBOX_URI and upload analysis directories
to the jumpbox via rsync.
"""

import json
import os
import re
import shlex
import subprocess
from pathlib import Path


def _validate_job_id(job_id: str) -> bool:
    """Validate job_id is numeric only to prevent command injection."""
    if not re.fullmatch(r"\d+", job_id):
        print(f"  Error: Invalid job_id '{job_id}'. Must be numeric.")
        return False
    return True


def parse_jumpbox_uri(jumpbox_uri: str) -> tuple[str, str | None]:
    """
    Parse JUMPBOX_URI format: "user@host -p port" or "user@host".

    Returns:
        Tuple of (ssh_target, ssh_port) where ssh_port may be None

    Raises:
        ValueError: If format is invalid
    """
    if not jumpbox_uri:
        raise ValueError("JUMPBOX_URI is empty")

    parts = jumpbox_uri.split()
    if len(parts) < 1:
        raise ValueError("Invalid JUMPBOX_URI format")

    ssh_target = parts[0]
    ssh_port = None

    if "-p" in parts:
        try:
            port_idx = parts.index("-p")
            if port_idx + 1 < len(parts):
                ssh_port = parts[port_idx + 1]
        except (ValueError, IndexError):
            pass

    return ssh_target, ssh_port


def upload_to_jumpbox(
    job_id: str,
    analysis_dir: Path,
    jumpbox_uri: str | None = None,
    session_id: str | None = None,
) -> bool:
    """
    Upload analysis directory to jumpbox at /usr/local/mlflow/<job_id>/.

    Optionally writes a session.json before uploading. Uses rsync for
    efficient directory transfer.

    Args:
        job_id: Job ID for remote path
        analysis_dir: Local analysis directory to upload
        jumpbox_uri: JUMPBOX_URI connection string (defaults to env var)
        session_id: If provided, writes session.json with this ID before upload

    Returns:
        True on success, False on failure
    """
    if not _validate_job_id(job_id):
        return False

    if not analysis_dir.exists():
        print(f"  Error: Analysis directory not found: {analysis_dir}")
        return False

    if jumpbox_uri is None:
        jumpbox_uri = os.environ.get("JUMPBOX_URI", "")

    if not jumpbox_uri:
        print("  JUMPBOX_URI not set. Skipping upload.")
        return False

    try:
        ssh_target, ssh_port = parse_jumpbox_uri(jumpbox_uri)
    except ValueError as e:
        print(f"  Error: {e}")
        return False

    remote_dir = f"/usr/local/mlflow/{job_id}"

    if session_id:
        session_file = analysis_dir / "session.json"
        try:
            with open(session_file, "w") as f:
                json.dump({"session_id": session_id, "job_id": job_id}, f, indent=2)
        except Exception as e:
            print(f"  Warning: Could not create session.json: {e}")

    print(f"  Uploading analysis for job {job_id}...")
    print(f"    Local:  {analysis_dir}/")
    print(f"    Remote: {ssh_target}:{remote_dir}/")

    ssh_cmd = ["ssh"]
    if ssh_port:
        ssh_cmd.extend(["-p", ssh_port])
    ssh_cmd.extend([ssh_target, f"mkdir -p {shlex.quote(remote_dir)}"])

    try:
        subprocess.run(ssh_cmd, check=True, capture_output=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"  Error creating remote directory: {e}")
        return False

    rsync_cmd = ["rsync"]
    if ssh_port:
        rsync_cmd.extend(["-e", f"ssh -p {ssh_port}"])
    rsync_cmd.extend(["-az", "--quiet", f"{analysis_dir}/", f"{ssh_target}:{remote_dir}/"])

    try:
        subprocess.run(rsync_cmd, check=True, timeout=60)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"  Error uploading to jumpbox: {e}")
        return False

    print(f"   Uploaded to jumpbox: {ssh_target}:{remote_dir}/")
    return True
