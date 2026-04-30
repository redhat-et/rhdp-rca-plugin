"""
Jumpbox I/O functions for RCA-Annotator.

Provides functions to download analysis files from jumpbox and upload
annotation.json back to jumpbox.
"""

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

    Args:
        jumpbox_uri: Connection string from JUMPBOX_URI env var

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

    return ssh_target, ssh_port


def verify_required_files(analysis_dir: Path) -> list[str]:
    """
    Verify that required analysis files exist.

    Args:
        analysis_dir: Path to analysis directory

    Returns:
        List of missing file names (empty if all present)
    """
    required_files = [
        "step1_job_context.json",
        "step3_correlation.json",
        "step4_github_fetch_history.json",
        "step5_analysis_summary.json",
    ]

    missing = []
    for filename in required_files:
        if not (analysis_dir / filename).exists():
            missing.append(filename)

    return missing


def download_from_jumpbox(job_id: str, jumpbox_uri: str | None = None) -> bool:
    """
    Download analysis files from jumpbox to local .analysis/<job_id>/.

    Args:
        job_id: Job ID to download
        jumpbox_uri: JUMPBOX_URI connection string (defaults to env var)

    Returns:
        True on success, False on failure
    """
    if not _validate_job_id(job_id):
        return False

    # Get JUMPBOX_URI
    if jumpbox_uri is None:
        jumpbox_uri = os.environ.get("JUMPBOX_URI", "")

    analysis_dir = Path(".analysis") / job_id

    # If JUMPBOX_URI not set, use local files
    if not jumpbox_uri:
        print("  JUMPBOX_URI not set. Using local .analysis/ directory")

        if not analysis_dir.exists():
            print(f"  Error: Local analysis directory not found: {analysis_dir}")
            return False

        # Verify required files exist locally
        missing = verify_required_files(analysis_dir)
        if missing:
            print(f"  Error: Missing required files in {analysis_dir}:")
            for filename in missing:
                print(f"    - {filename}")
            return False

        print(f"  Using local analysis files at {analysis_dir}")
        return True

    # Parse JUMPBOX_URI
    try:
        ssh_target, ssh_port = parse_jumpbox_uri(jumpbox_uri)
    except ValueError as e:
        print(f"  Error: {e}")
        return False

    remote_candidates = [f"/usr/local/mlflow/{job_id}"]
    remote_dir = None

    for candidate in remote_candidates:
        ssh_cmd = ["ssh"]
        if ssh_port:
            ssh_cmd.extend(["-p", ssh_port])
        ssh_cmd.extend([ssh_target, f"test -d {shlex.quote(candidate)}"])

        try:
            subprocess.run(
                ssh_cmd,
                check=True,
                capture_output=True,
                timeout=30,
            )
            remote_dir = candidate
            break
        except FileNotFoundError as e:
            print(f" Error: Command not found:{e.filename}")
            return False
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue

    if remote_dir is None:
        paths_tried = ", ".join(f"{ssh_target}:{c}" for c in remote_candidates)
        print(
            f"  Error: Remote directory does not exist or connection failed. Tried: {paths_tried}"
        )
        return False

    print("  Downloading analysis files from jumpbox...")
    print(f"    Remote: {ssh_target}:{remote_dir}/")
    print(f"    Local:  {analysis_dir}/")

    # Create local directory
    analysis_dir.mkdir(parents=True, exist_ok=True)

    # Build rsync command
    rsync_cmd = ["rsync"]
    if ssh_port:
        rsync_cmd.extend(["-e", f"ssh -p {ssh_port}"])
    rsync_cmd.extend(["-az", "--progress", f"{ssh_target}:{remote_dir}/", str(analysis_dir) + "/"])

    # Download files with timeout
    try:
        subprocess.run(
            rsync_cmd,
            check=True,
            timeout=60,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print("  Error: Failed to download files from jumpbox (timeout or connection error)")
        return False

    # Verify required files were downloaded
    missing = verify_required_files(analysis_dir)
    if missing:
        print("  Error: Missing required files after download:")
        for filename in missing:
            print(f"    - {filename}")
        return False

    print("   Analysis files downloaded successfully")
    print(f"    Location: {analysis_dir}/")

    return True


def upload_to_jumpbox(job_id: str, jumpbox_uri: str | None = None) -> bool:
    """
    Upload annotation.json to jumpbox at /usr/local/mlflow/<job_id>/.

    Args:
        job_id: Job ID to upload annotation for
        jumpbox_uri: JUMPBOX_URI connection string (defaults to env var)

    Returns:
        True on success, False on failure
    """
    if not _validate_job_id(job_id):
        return False

    local_file = Path(".analysis") / job_id / "annotation.json"

    # Check if local file exists
    if not local_file.exists():
        print(f"  Error: Local annotation file not found: {local_file}")
        return False

    # Get JUMPBOX_URI
    if jumpbox_uri is None:
        jumpbox_uri = os.environ.get("JUMPBOX_URI", "")

    # If JUMPBOX_URI not set, skip upload
    if not jumpbox_uri:
        print(f"  JUMPBOX_URI not set. Annotation saved locally only at: {local_file}")
        return True  # Still return True since local file exists

    # Parse JUMPBOX_URI
    try:
        ssh_target, ssh_port = parse_jumpbox_uri(jumpbox_uri)
    except ValueError as e:
        print(f"  Error: {e}")
        print(f"   Annotation saved locally at: {local_file}")
        return False

    remote_dir = f"/usr/local/mlflow/{job_id}"

    print("  Uploading annotation to jumpbox...")
    print(f"    Local:  {local_file}")
    print(f"    Remote: {ssh_target}:{remote_dir}/annotation.json")

    scp_cmd = ["scp"]
    if ssh_port:
        scp_cmd.extend(["-P", ssh_port])
    scp_cmd.extend([str(local_file), f"{ssh_target}:{remote_dir}/"])

    # Upload file with timeout
    try:
        subprocess.run(
            scp_cmd,
            check=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print("  Error: Failed to upload annotation to jumpbox (timeout or connection error)")
        print(f"    Annotation saved locally at: {local_file}")
        return False

    print("   Annotation uploaded successfully")
    print(f"    Remote location: {ssh_target}:{remote_dir}/annotation.json")
    print(f"    Local backup: {local_file}")

    return True
