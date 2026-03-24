import argparse
import datetime
import json
import os
from pathlib import Path

from utils import convert_jsonl_to_json, get_chat_history_jsonl_path, upload_feedback_to_jumpbox


def format_entry(entry_id, category, feedback, context, skill, chat_history_file):
    """Formats the feedback entry as a dictionary."""
    current_date = datetime.datetime.now().strftime("%d-%B-%Y")
    timestamp = datetime.datetime.now().isoformat()

    entry = {
        "id": entry_id,
        "category": category,
        "date": current_date,
        "timestamp": timestamp,
        "skill": skill,
        "feedback": feedback,
        "context": context,
        "summary": context,  # MLflow uses "summary" field
        "chat_history_file": chat_history_file,
        "user": os.environ.get("MLFLOW_TAG_USER", os.environ.get("USER", "unknown")),
        "source": "feedback-capture",
    }
    return entry


def main():
    parser = argparse.ArgumentParser(description="Save feedback to a file.")
    parser.add_argument("--category", required=True, help="Category of the feedback")
    parser.add_argument("--feedback", required=True, help="The user's feedback")
    parser.add_argument("--context", required=True, help="Summary of what happened")
    parser.add_argument("--skill", required=True, help="The skill being used")

    args = parser.parse_args()

    session_id = os.environ.get("CLAUDE_SESSION_ID")
    if session_id:
        print(f"Session ID: {session_id}")
    else:
        print("Warning: CLAUDE_SESSION_ID not found in environment")

    script_dir = Path(__file__).parent
    chat_history_dir = script_dir / "chat_history"
    chat_history_dir.mkdir(exist_ok=True)

    # Single feedback.json file that contains all feedback entries
    feedback_json_filepath = script_dir / "feedback.json"

    chat_history_json_filename = f"chat_history_{session_id}.json"
    chat_history_json_filepath = chat_history_dir / chat_history_json_filename

    history_jsonl_path = get_chat_history_jsonl_path(session_id)
    if history_jsonl_path:
        print(f"Jsonl file: {history_jsonl_path}")
        convert_jsonl_to_json(history_jsonl_path, chat_history_json_filepath)
    else:
        print("No Claude session file found to auto-detect")

    # Create feedback entry as dictionary
    formatted_entry = format_entry(
        session_id,
        args.category,
        args.feedback,
        args.context,
        args.skill,
        chat_history_json_filename,
    )

    # Load existing feedback entries or create new list
    feedback_entries = []
    if feedback_json_filepath.exists():
        try:
            with open(feedback_json_filepath) as f:
                feedback_entries = json.load(f)
                if not isinstance(feedback_entries, list):
                    feedback_entries = []
        except Exception as e:
            print(f"Warning: Could not read existing feedback.json: {e}")
            feedback_entries = []

    # Append new entry
    feedback_entries.append(formatted_entry)

    # Save updated feedback.json
    try:
        with open(feedback_json_filepath, "w") as f:
            json.dump(feedback_entries, f, indent=2)
        print(f"Successfully saved feedback {session_id} to {feedback_json_filepath}")
    except Exception as e:
        print(f"Error writing to file: {e}")

    # Upload to Jumpbox
    print("\n[Upload] Uploading feedback to Jumpbox...")
    upload_feedback_to_jumpbox(feedback_json_filepath, chat_history_json_filepath, session_id)

    # Also log to MLflow if available
    try:
        from mlflow_feedback import log_feedback

        log_feedback(args.category, args.feedback, args.context, args.skill)
    except ImportError:
        pass
    except Exception as e:
        print(f"MLflow logging skipped: {e}")

    print(f"Chat history file is {chat_history_json_filepath}")


if __name__ == "__main__":
    main()
