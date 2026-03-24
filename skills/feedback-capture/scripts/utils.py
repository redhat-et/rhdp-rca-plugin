import glob
import json
import os
import subprocess
from pathlib import Path


def convert_jsonl_to_json(jsonl_path, output_path=None):
    """Converts a JSONL file to a list of JSON objects. Optionally saves to a file."""
    data = []
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        data.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        print(f"Skipping invalid JSON line in {jsonl_path}: {e}")

        if output_path:
            try:
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                print(f"Chat history JSON saved to {output_path}")
            except Exception as e:
                print(f"Error saving JSON file: {e}")

    except Exception as e:
        print(f"Error reading JSONL file: {e}")


def get_chat_history_jsonl_path(session_id=None):
    """Finds the latest Claude Code session .jsonl file.
    If session_id is provided, tries to find that specific session file.
    """
    home = os.path.expanduser("~")
    projects_dir = os.path.join(home, ".claude", "projects")

    if not os.path.exists(projects_dir):
        return None

    if session_id:
        pattern = os.path.join(projects_dir, "**", f"*{session_id}*.jsonl")
        files = glob.glob(pattern, recursive=True)
        if files:
            return files[0]
        print(
            f"Warning: Specific session file for ID {session_id} not found. Falling back to latest."
        )

    else:
        return None


def upload_feedback_to_jumpbox(
    feedback_file: Path, chat_history_file: Path, session_id: str | None = None
) -> bool:
    """Upload feedback.json and chat history to Jumpbox."""
    jumpbox_uri = os.environ.get("JUMPBOX_URI", "")

    if not jumpbox_uri:
        print("  Skipping upload: JUMPBOX_URI not configured")
        return False

    # Parse JUMPBOX_URI format: "user@host -p port"
    parts = jumpbox_uri.split()
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

    # Create remote directories
    ssh_cmd = ["ssh"]
    if ssh_port:
        ssh_cmd.extend(["-p", ssh_port])
    ssh_cmd.extend([ssh_target, "mkdir -p /tmp/feedback/chat_history"])

    try:
        subprocess.run(
            ssh_cmd,
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"  Error creating remote directories: {e}")
        return False

    # Upload feedback.json (single file with all entries)
    if feedback_file.exists():
        try:
            scp_cmd = ["scp"]
            if ssh_port:
                scp_cmd.extend(["-P", ssh_port])

            dest_filename = f"feedback_{session_id}.json" if session_id else feedback_file.name
            scp_cmd.extend([str(feedback_file), f"{ssh_target}:/tmp/feedback/{dest_filename}"])

            subprocess.run(
                scp_cmd,
                check=True,
                capture_output=True,
                timeout=30,
            )
            print(f"  Uploaded feedback to Jumpbox ({ssh_target}): /tmp/feedback/{dest_filename}")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            print(f"  Error uploading feedback: {e}")
            return False

    # Upload chat history to feedback/chat_history/ directory if it exists
    if chat_history_file.exists():
        try:
            scp_cmd = ["scp"]
            if ssh_port:
                scp_cmd.extend(["-P", ssh_port])
            scp_cmd.extend([str(chat_history_file), f"{ssh_target}:/tmp/feedback/chat_history/"])

            subprocess.run(
                scp_cmd,
                check=True,
                capture_output=True,
                timeout=30,
            )
            print(
                f"  Uploaded chat history to Jumpbox ({ssh_target}): /tmp/feedback/chat_history/{chat_history_file.name}"
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            print(f"  Warning: Could not upload chat history: {e}")

    return True
