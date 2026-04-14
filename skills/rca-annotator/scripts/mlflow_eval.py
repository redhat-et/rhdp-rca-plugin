"""
MLflow evaluation functions for RCA-Annotator.

Provides functions to download annotation files from jumpbox and log
evaluation results to MLflow.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import mlflow  # type: ignore
from jumpbox_io import download_from_jumpbox


def load_annotation(job_id: str) -> dict[str, Any] | None:
    """
    Load annotation_draft.json for a given job_id.

    Args:
        job_id: Job ID to load annotation for

    Returns:
        Annotation dict if successful, None on failure
    """
    annotation_file = Path(".analysis") / job_id / "annotation_draft.json"

    if not annotation_file.exists():
        print(f"  Error: Annotation file not found: {annotation_file}")
        return None

    try:
        with open(annotation_file) as f:
            annotation = json.load(f)
        return annotation
    except json.JSONDecodeError as e:
        print(f"  Error: Invalid JSON in {annotation_file}: {e}")
        return None
    except Exception as e:
        print(f"  Error: Failed to read {annotation_file}: {e}")
        return None


def download_annotations_for_eval(
    job_ids: list[str], jumpbox_uri: str | None = None
) -> dict[str, dict[str, Any]]:
    """
    Download and load annotations for multiple jobs.

    Args:
        job_ids: List of job IDs to download
        jumpbox_uri: JUMPBOX_URI connection string (defaults to env var)

    Returns:
        Dict mapping job_id to annotation data
    """
    annotations = {}

    print(f"Downloading annotations for {len(job_ids)} jobs...")

    for i, job_id in enumerate(job_ids, 1):
        print(f"\n[{i}/{len(job_ids)}] Job {job_id}")

        if download_from_jumpbox(job_id, jumpbox_uri):
            annotation = load_annotation(job_id)
            if annotation:
                annotations[job_id] = annotation
                print("  ✓ Loaded annotation")
            else:
                print("  ✗ Failed to load annotation")
        else:
            print("  ✗ Download failed")

    print(f"\n{'=' * 60}")
    print(f"Downloaded {len(annotations)}/{len(job_ids)} annotations")
    print(f"{'=' * 60}")

    return annotations


def log_annotation_feedback(trace_id: str, annotations: dict[str, Any]) -> None:
    """Log annotation details as MLflow feedback."""
    root_cause = annotations.get("root_cause", {})
    if root_cause:
        mlflow.log_feedback(
            trace_id=trace_id,
            name="Root Cause",
            value=f"Category: {root_cause.get('category')} \n Confidence: {root_cause.get('confidence')}",
            rationale=root_cause.get("summary"),
        )

    for evidence_item in annotations.get("evidence", []):
        mlflow.log_feedback(
            trace_id=trace_id,
            name="Evidence",
            value=f"{evidence_item.get('source')}: {evidence_item.get('message')} \n Confidence {evidence_item.get('confidence')}",
        )

    for recommendation in annotations.get("recommendations", []):
        mlflow.log_feedback(
            trace_id=trace_id,
            name="Recommendation",
            value=f"Priority: {recommendation.get('priority')} \n Action: {recommendation.get('action')}",
            rationale=f"File: {recommendation.get('file')}",
        )

    for alt_diagnosis in annotations.get("alternative_diagnoses", []):
        mlflow.log_feedback(
            trace_id=trace_id,
            name="Alternative Diagnosis",
            value=f"Category: {alt_diagnosis.get('category')} \n Summary: {alt_diagnosis.get('summary')}",
            rationale=alt_diagnosis.get("why_wrong"),
        )

    for factor in annotations.get("contributing_factors", []):
        mlflow.log_feedback(trace_id=trace_id, name="Contributing Factor", value=factor)

    for key, value in annotations.get("consistency_check", {}).items():
        mlflow.log_feedback(
            trace_id=trace_id, name=f"Consistency Check: {key}", value=f"{key}: {value}"
        )


def log_expectation(trace_id: str, annotations: dict[str, Any]) -> None:
    """Log expectations for the given annotations."""
    human_review = annotations.get("human_review", {})
    if human_review:
        # Category review
        mlflow.log_expectation(
            trace_id=trace_id,
            name="Human Review",
            value=f"Summary accurate: {human_review.get('summary_accurate')} \n Summary comment: {human_review.get('summary_comment')}",
        )

        # Summary review
        mlflow.log_expectation(
            trace_id=trace_id,
            name="Summary (Human Review)",
            value=f"Summary accurate: {human_review.get('summary_accurate')}",
        )

        # Evidence review
        mlflow.log_expectation(
            trace_id=trace_id, name="Evidence ", value=human_review.get("evidence_feedback")
        )

        # Difficulty review
        mlflow.log_expectation(
            trace_id=trace_id,
            name="Difficulty",
            value=f"Difficulty appropriate: {human_review.get('difficulty_appropriate')}",
        )

        # Alternative diagnoses added by human reviewer
        for alt_diagnosis in human_review.get("alternative_diagnoses_added", []):
            mlflow.log_expectation(
                trace_id=trace_id,
                name="Alternative Diagnosis Added",
                value=f"Category: {alt_diagnosis.get('category')} | Plausibility: {alt_diagnosis.get('plausibility')} \nSummary: {alt_diagnosis.get('summary')}",
            )


def evaluate_jobs(job_ids: list[str]) -> None:
    """Run evaluation for the given job IDs."""
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
    mlflow.set_tracking_uri(tracking_uri)
    experiment_name = os.environ.get("MLFLOW_EXPERIMENT_NAME", "Default")
    mlflow.set_experiment(experiment_name)

    with mlflow.start_run(run_name="ANNOTATOR_EVALUATION"):
        # Create a traced span within the run to ensure linkage
        with mlflow.start_span(name="download_annotations") as span:
            data = download_annotations_for_eval(job_ids)
            trace_id = span.request_id

            for job_id in job_ids:
                if job_id not in data:
                    print(f"Warning: No data for job {job_id}")
                    continue

                annotations = data[job_id]

                # Log feedback for annotation quality metrics
                log_annotation_feedback(trace_id, annotations)

                # Log ground truth annotation as expectation
                log_expectation(trace_id, annotations)

            # Log run params
            mlflow.log_param("job_ids", job_ids)
            if job_ids and job_ids[0] in data:
                mlflow.log_param("annotator", data[job_ids[0]].get("annotator"))

        print(f"Traces are {trace_id}")


def main():
    parser = argparse.ArgumentParser(description="Evaluate RCA annotations using MLflow.")
    parser.add_argument("job_ids", nargs="+", help="List of job IDs to evaluate")
    args = parser.parse_args()
    evaluate_jobs(args.job_ids)


if __name__ == "__main__":
    sys.exit(main())
