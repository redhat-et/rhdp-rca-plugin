import argparse
import datetime
import os

from utils import convert_jsonl_to_json, get_chat_history_jsonl_path


def format_entry(entry_id, category, feedback, context, skill, chat_history_file):
    """Formats the feedback entry."""
    # Date format: day-Month-year (e.g., 29-January-2026)
    current_date = datetime.datetime.now().strftime("%d-%B-%Y")

    entry = (
        f"ID: {entry_id}\n"
        f"Category: {category}\n"
        f"Date: {current_date}\n"
        f"Skill: {skill}\n"
        f"Feedback: {feedback}\n"
        f"Context: {context}\n"
        f"Chat History File: {chat_history_file}\n"
        "\n"
    )
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

    script_dir = os.path.dirname(os.path.abspath(__file__))
    chat_history_dir = os.path.join(script_dir, "chat_history")
    feedback_file = os.path.join(script_dir, "feedback.txt")
    os.makedirs(chat_history_dir, exist_ok=True)

    chat_history_json_filename = f"chat_history_{session_id}.json"
    chat_history_json_filepath = os.path.join(chat_history_dir, chat_history_json_filename)

    history_jsonl_path = get_chat_history_jsonl_path(session_id)
    if history_jsonl_path:
        print(f"Jsonl file: {history_jsonl_path}")
        convert_jsonl_to_json(history_jsonl_path, chat_history_json_filepath)
    else:
        print("No Claude session file found to auto-detect")

    formatted_entry = format_entry(
        session_id,
        args.category,
        args.feedback,
        args.context,
        args.skill,
        chat_history_json_filename,
    )

    try:
        with open(feedback_file, "a") as f:
            f.write(formatted_entry)
        print(f"Successfully saved Entry {session_id} to {feedback_file}")
    except Exception as e:
        print(f"Error writing to file: {e}")

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
