#!/usr/bin/env python3
import argparse
import os

import mlflow
from mlflow.entities import SpanType


def log_feedback(category: str, feedback: str, context: str, skill: str) -> dict:
    """Log user feedback to MLflow trace."""
    feedback_data = {"skill": skill, "category": category, "feedback": feedback, "summary": context}

    with mlflow.start_span(name="Log user feedback", span_type=SpanType.TOOL) as span:
        span.set_inputs(feedback_data)
        span.set_outputs({"status": "logged", **feedback_data})

    print("Successfully logged feedback to MLflow")
    return feedback_data


def main():
    parser = argparse.ArgumentParser(description="Log feedback to MLflow.")
    parser.add_argument("--category", required=True, help="Category of the feedback")
    parser.add_argument("--feedback", required=True, help="The user's feedback")
    parser.add_argument("--context", required=True, help="Summary of what happened")
    parser.add_argument("--skill", required=True, help="The skill being used")

    args = parser.parse_args()

    with mlflow.start_span(name="Feedback capture", span_type=SpanType.CHAIN) as span:
        # Set trace metadata
        mlflow.update_current_trace(
            metadata={
                "mlflow.trace.session": f"{os.environ.get('CLAUDE_SESSION_ID')}",
                "mlflow.trace.user": os.environ.get("MLFLOW_TAG_USER"),
                "mlflow.source.name": "feedback-capture",
                "mlflow.source.git.repoURL": "https://github.com/redhat-et/aiops-skills/blob/main/skills/feedback-capture/SKILL.md",
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

    return result


if __name__ == "__main__":
    main()
