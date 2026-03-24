#!/usr/bin/env python3
"""Optional MLflow feedback logging. Falls back to file-based if MLflow unavailable."""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

from utils import convert_jsonl_to_json, get_chat_history_jsonl_path, upload_feedback_to_jumpbox

HAS_MLFLOW = False
try:
    import mlflow
    from mlflow.entities import SpanType

    HAS_MLFLOW = True
except ImportError:
    HAS_MLFLOW = False


def log_feedback(category: str, feedback: str, context: str, skill: str) -> dict:
    """Log user feedback to MLflow trace."""
    feedback_data = {"skill": skill, "category": category, "feedback": feedback, "summary": context}

    if HAS_MLFLOW:
        with mlflow.start_span(name="Log user feedback", span_type=SpanType.TOOL) as span:
            span.set_inputs(feedback_data)
            span.set_outputs({"status": "logged", **feedback_data})
        print("Successfully logged feedback to MLflow")
    else:
        print("MLflow not available, skipping trace logging")

    return feedback_data


def main() -> int:
    parser = argparse.ArgumentParser(description="Log feedback to MLflow.")
    parser.add_argument("--category", required=True, help="Category of the feedback")
    parser.add_argument("--feedback", required=True, help="The user's feedback")
    parser.add_argument("--context", required=True, help="Summary of what happened")
    parser.add_argument("--skill", required=True, help="The skill being used")

    args = parser.parse_args()

    if not HAS_MLFLOW:
        print("Warning: MLflow not installed. Proceeding with file-based feedback only.")

    # Save to feedback.json and upload to jumpbox
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
    current_date = datetime.datetime.now().strftime("%d-%B-%Y")
    timestamp = datetime.datetime.now().isoformat()

    formatted_entry = {
        "id": session_id,
        "category": args.category,
        "date": current_date,
        "timestamp": timestamp,
        "skill": args.skill,
        "feedback": args.feedback,
        "context": args.context,
        "summary": args.context,  # MLflow uses "summary" field
        "chat_history_file": chat_history_json_filename,
        "user": os.environ.get("MLFLOW_TAG_USER", os.environ.get("USER", "unknown")),
        "source": "feedback-capture",
    }

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
        return 1

    # Upload to Jumpbox
    print("\n[Upload] Uploading feedback to Jumpbox...")
    upload_feedback_to_jumpbox(feedback_json_filepath, chat_history_json_filepath, session_id)

    # Log to MLflow (only if available)
    if HAS_MLFLOW:
        with mlflow.start_span(name="Feedback capture", span_type=SpanType.CHAIN) as span:
            mlflow.update_current_trace(
                metadata={
                    "mlflow.trace.session": f"{os.environ.get('CLAUDE_SESSION_ID')}",
                    "mlflow.trace.user": os.environ.get("MLFLOW_TAG_USER"),
                    "mlflow.source.name": "feedback-capture",
                },
            )

            span.set_inputs(
                {
                    "category": args.category,
                    "feedback": args.feedback,
                    "context": args.context,
                    "skill": args.skill,
                }
            )
            result = log_feedback(args.category, args.feedback, args.context, args.skill)
            span.set_outputs(result)
    else:
        result = {"status": "logged_to_file", "skill": args.skill, "category": args.category}

    print(f"Chat history file is {chat_history_json_filepath}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
