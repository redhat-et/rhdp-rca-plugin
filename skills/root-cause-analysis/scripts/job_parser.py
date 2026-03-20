"""Parse local AAP job logs and extract correlation identifiers."""

import gzip
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import mlflow
from mlflow.entities import SpanType


@mlflow.trace(name="Load job log file", span_type=SpanType.RETRIEVER)
def load_job_log(file_path: Path) -> dict[str, Any]:
    """Load a job log file (supports .json and .json.gz)."""
    path_str = str(file_path)

    # Try gzip first if extension suggests it, or if .json fails
    if ".gz" in path_str:
        try:
            with gzip.open(file_path, "rt", encoding="utf-8") as f:
                return json.load(f)
        except gzip.BadGzipFile:
            pass

    # Try plain JSON
    try:
        with open(file_path, encoding="utf-8") as f:
            return json.load(f)
    except UnicodeDecodeError:
        # File might be gzipped without .gz extension
        with gzip.open(file_path, "rt", encoding="utf-8") as f:
            return json.load(f)


@mlflow.trace(name="Extract job context", span_type=SpanType.PARSER)
def extract_job_context(job_data: dict[str, Any]) -> dict[str, Any]:
    """
    Extract correlation identifiers from job log.

    Returns:
        Dictionary with job_id, guid, namespace, time_window, failed_tasks, etc.
    """
    metadata = job_data.get("metadata", {}).get("job_metadata", {})
    events = job_data.get("events", [])

    # Basic job info
    job_id = str(metadata.get("job_id", ""))
    guid = metadata.get("guid", "")
    started = metadata.get("started", "")
    finished = metadata.get("finished", "")

    # Extract namespace from events
    namespace = _extract_namespace(events, guid)

    # Extract pod references
    pod_refs = _extract_pod_references(events)

    # Extract failed tasks
    failed_tasks = _extract_failed_tasks(events)

    # Extract all unique plays and roles
    plays = set()
    roles = set()
    for event in events:
        if event.get("play"):
            plays.add(event["play"])
        if event.get("role"):
            roles.add(event["role"])

    return {
        "job_id": job_id,
        "job_name": metadata.get("job_name", ""),
        "status": metadata.get("status", ""),
        "guid": guid,
        "namespace": namespace,
        "cluster": metadata.get("sandbox_openshift_cluster", ""),
        "cloud_provider": metadata.get("cloud_provider", ""),
        "env_type": metadata.get("env_type", ""),
        "action": metadata.get("action", ""),
        "time_window": {
            "started": started,
            "finished": finished,
            "duration_seconds": metadata.get("duration_seconds", 0),
        },
        "failed_tasks": failed_tasks,
        "pod_references": pod_refs,
        "plays": list(plays),
        "roles": list(roles),
        "total_events": len(events),
        "host_status_counts": metadata.get("host_status_counts", {}),
    }


@mlflow.trace(name="Extract namespace", span_type=SpanType.PARSER)
def _extract_namespace(events: list[dict], guid: str) -> str:
    """Extract OCP namespace from events."""
    namespace_pattern = re.compile(
        r"namespace['\"]?\s*[:=]\s*['\"]?(sandbox-[a-z0-9-]+)['\"]?", re.IGNORECASE
    )
    sandbox_pattern = re.compile(rf"sandbox-{re.escape(guid)}-[a-z0-9-]+")

    for event in events:
        stdout = event.get("stdout", "")
        if stdout:
            # Try namespace pattern
            match = namespace_pattern.search(stdout)
            if match:
                return match.group(1)

            # Try sandbox pattern with guid
            if guid:
                match = sandbox_pattern.search(stdout)
                if match:
                    return match.group(0)

    # Fallback: construct from guid if available
    if guid:
        # Check events for env_type to construct namespace
        for event in events:
            stdout = event.get("stdout", "")
            if f"sandbox-{guid}" in stdout:
                match = re.search(rf"(sandbox-{re.escape(guid)}-[a-z0-9-]+)", stdout)
                if match:
                    return match.group(1)

    return ""


@mlflow.trace(name="Extract pod references", span_type=SpanType.PARSER)
def _extract_pod_references(events: list[dict]) -> list[dict[str, str]]:
    """Extract pod names referenced in events."""
    pod_refs = []
    seen = set()

    # Patterns for pod names
    pod_patterns = [
        re.compile(r"pod[/\s]+([a-z0-9-]+-[a-z0-9]+-[a-z0-9]+)", re.IGNORECASE),
        re.compile(r"(showroom-[a-z0-9]+-[a-z0-9]+)"),
        re.compile(r"kubernetes\.pod_name['\"]?\s*[:=]\s*['\"]?([a-z0-9-]+)['\"]?"),
    ]

    for event in events:
        stdout = event.get("stdout", "")
        task = event.get("task", "")

        for pattern in pod_patterns:
            matches = pattern.findall(stdout)
            for match in matches:
                if match not in seen:
                    seen.add(match)
                    pod_refs.append(
                        {
                            "pod_name": match,
                            "task": task,
                            "timestamp": event.get("created", ""),
                        }
                    )

    return pod_refs


@mlflow.trace(name="Extract failed tasks", span_type=SpanType.PARSER)
def _extract_failed_tasks(events: list[dict]) -> list[dict[str, Any]]:
    """Extract failed tasks with error details."""
    failed = []

    for event in events:
        if event.get("failed") and event.get("event") == "runner_on_failed":
            event_data = event.get("event_data", {})
            res = event_data.get("res", {})

            failed.append(
                {
                    "task": event.get("task", ""),
                    "play": event.get("play", ""),
                    "role": event.get("role", ""),
                    "timestamp": event.get("created", ""),
                    "error_message": res.get("msg", "") if isinstance(res, dict) else str(res),
                    "task_path": event_data.get("task_path", ""),
                    "task_action": event_data.get("task_action", ""),
                    "duration": event_data.get("duration", 0),
                }
            )

    return failed


@mlflow.trace(name="Parse job log", span_type=SpanType.PARSER)
def parse_job_log(file_path: Path) -> dict[str, Any]:
    """
    Parse a job log file and extract all correlation context.

    Args:
        file_path: Path to job log file (.json or .json.gz)

    Returns:
        Job context dictionary with identifiers and failed task info
    """
    job_data = load_job_log(file_path)
    context = extract_job_context(job_data)

    # Add metadata about parsing
    context["parsed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    context["source_file"] = str(file_path)

    return context
