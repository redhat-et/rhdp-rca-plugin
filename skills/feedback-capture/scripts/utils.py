import glob
import json
import os


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
