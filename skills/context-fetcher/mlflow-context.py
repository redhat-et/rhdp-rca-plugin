#!/usr/bin/env python3
"""MLflow tracing for context-fetcher skill."""

import argparse
import os

import mlflow
from mlflow.entities import SpanType


def log_context_search(
    query: str,
    sources: str,
    job_id: str | None = None,
    incident_id: str | None = None,
    results_summary: str | None = None,
) -> dict:
    """Log context search operation to MLflow trace."""
    search_data = {
        "query": query,
        "sources": sources.split(",") if sources else [],
        "job_id": job_id,
        "incident_id": incident_id,
    }

    with mlflow.start_span(name="Context search", span_type=SpanType.RETRIEVER) as span:
        span.set_inputs(search_data)
        span.set_outputs({"status": "completed", "results_summary": results_summary, **search_data})

    print("Successfully logged context search to MLflow")
    return search_data


def main():
    parser = argparse.ArgumentParser(description="Log context-fetcher operations to MLflow.")
    parser.add_argument("--query", required=True, help="Search query or keywords used")
    parser.add_argument(
        "--sources",
        required=True,
        help="Comma-separated sources searched (github,confluence,slack)",
    )
    parser.add_argument("--job-id", help="Job ID being investigated (if applicable)")
    parser.add_argument("--incident-id", help="Incident ID being investigated (if applicable)")
    parser.add_argument("--results-summary", help="Brief summary of results found")

    args = parser.parse_args()

    with mlflow.start_span(name="Context fetcher", span_type=SpanType.CHAIN) as span:
        # Set trace metadata
        mlflow.update_current_trace(
            metadata={
                "mlflow.trace.session": f"{os.environ.get('CLAUDE_SESSION_ID')}",
                "mlflow.trace.user": os.environ.get("MLFLOW_TAG_USER"),
                "mlflow.source.name": "context-fetcher",
                "mlflow.source.git.repoURL": "https://github.com/redhat-et/aiops-skills/blob/main/skills/context-fetcher/SKILL.md",
            },
        )

        span.set_inputs(
            {
                "query": args.query,
                "sources": args.sources,
                "job_id": args.job_id,
                "incident_id": args.incident_id,
            }
        )

        result = log_context_search(
            query=args.query,
            sources=args.sources,
            job_id=args.job_id,
            incident_id=args.incident_id,
            results_summary=args.results_summary,
        )

        span.set_outputs(result)

    return result


if __name__ == "__main__":
    main()
