"""Build correlation between AAP job logs and Splunk pod logs."""

import json
from datetime import datetime, timezone
from typing import Any

from .config import Config
from .splunk_client import SplunkClient
from .tracing import SpanType, trace


@trace(name="Fetch correlated Splunk logs", span_type=SpanType.CHAIN if SpanType else None)
def fetch_correlated_logs(
    config: Config,
    job_context: dict[str, Any],
) -> dict[str, Any]:
    """
    Fetch Splunk logs correlated to the job context.

    Args:
        config: Configuration with Splunk credentials
        job_context: Output from job_parser.parse_job_log()

    Returns:
        Dictionary with Splunk logs and metadata
    """
    client = SplunkClient(config)

    guid = job_context.get("guid", "")
    namespace = job_context.get("namespace", "")
    time_window = job_context.get("time_window", {})

    # Convert timestamps to Splunk format
    earliest = time_window.get("started", "-24h")
    latest = time_window.get("finished", "now")

    results = {
        "job_id": job_context.get("job_id"),
        "guid": guid,
        "namespace": namespace,
        "query_time_window": {"earliest": earliest, "latest": latest},
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "ocp_logs": [],
        "errors": [],
        "pods_found": [],
    }

    # Query by namespace if available
    if namespace:
        print(f"Querying OCP logs for namespace: {namespace}")
        try:
            logs = client.query_ocp_namespace(
                namespace,
                earliest=earliest,
                latest=latest,
                errors_only=False,
                max_results=300,
            )
            results["ocp_logs"] = _parse_ocp_logs(logs)
            results["pods_found"] = _extract_unique_pods(logs)
            print(f"  Found {len(logs)} log entries")
        except Exception as e:
            results["errors"].append(f"Namespace query failed: {e}")
            print(f"  Query failed: {e}")

    # Also query by GUID for broader coverage
    if guid and not results["ocp_logs"]:
        print(f"Querying OCP logs by GUID: {guid}")
        try:
            logs = client.query_by_guid(guid, earliest=earliest, latest=latest)
            results["ocp_logs"] = _parse_ocp_logs(logs)
            results["pods_found"] = _extract_unique_pods(logs)
            print(f"  Found {len(logs)} log entries")
        except Exception as e:
            results["errors"].append(f"GUID query failed: {e}")
            print(f"  Query failed: {e}")

    # Query for errors specifically
    if namespace:
        print(f"Querying error logs for namespace: {namespace}")
        try:
            error_logs = client.query_ocp_namespace(
                namespace,
                earliest=earliest,
                latest=latest,
                errors_only=True,
                max_results=100,
            )
            results["error_logs"] = _parse_ocp_logs(error_logs)
            print(f"  Found {len(error_logs)} error entries")
        except Exception as e:
            results["errors"].append(f"Error query failed: {e}")

    return results


def _parse_ocp_logs(raw_logs: list[dict]) -> list[dict[str, Any]]:
    """Parse raw Splunk OCP log results into structured format."""
    parsed = []

    for row in raw_logs:
        raw = row.get("_raw", "")
        timestamp = row.get("_time", "")

        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            data = {"message": raw}

        k8s = data.get("kubernetes", {})

        parsed.append(
            {
                "timestamp": timestamp,
                "namespace": k8s.get("namespace_name", row.get("kubernetes.namespace_name", "")),
                "pod_name": k8s.get("pod_name", row.get("kubernetes.pod_name", "")),
                "container_name": k8s.get(
                    "container_name", row.get("kubernetes.container_name", "")
                ),
                "message": data.get("message", data.get("log", raw))[:2000],
                "level": data.get("level", "info"),
            }
        )

    return parsed


def _extract_unique_pods(raw_logs: list[dict]) -> list[dict[str, Any]]:
    """Extract unique pods from log results."""
    pods = {}

    for row in raw_logs:
        raw = row.get("_raw", "")
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            continue

        k8s = data.get("kubernetes", {})
        pod_name = k8s.get("pod_name", row.get("kubernetes.pod_name", ""))
        namespace = k8s.get("namespace_name", row.get("kubernetes.namespace_name", ""))
        container = k8s.get("container_name", row.get("kubernetes.container_name", ""))

        if pod_name and pod_name not in pods:
            pods[pod_name] = {
                "pod_name": pod_name,
                "namespace": namespace,
                "containers": set(),
            }
        if pod_name and container:
            pods[pod_name]["containers"].add(container)

    # Convert sets to lists for JSON serialization
    return [{**pod, "containers": list(pod["containers"])} for pod in pods.values()]


@trace(name="Build correlation timeline", span_type=SpanType.CHAIN if SpanType else None)
def build_correlation_timeline(
    job_context: dict[str, Any],
    splunk_logs: dict[str, Any],
) -> dict[str, Any]:
    """
    Build a unified timeline correlating AAP and Splunk events.

    Args:
        job_context: Parsed job context from step 1
        splunk_logs: Splunk logs from step 2

    Returns:
        Correlation timeline with merged events
    """
    timeline_events = []

    # Add AAP failed task events
    for task in job_context.get("failed_tasks", []):
        timeline_events.append(
            {
                "timestamp": task.get("timestamp", ""),
                "source": "aap_job",
                "event_type": "task_failed",
                "summary": f"Task '{task.get('task')}' failed",
                "details": {
                    "task": task.get("task"),
                    "play": task.get("play"),
                    "error_message": task.get("error_message"),
                    "task_action": task.get("task_action"),
                },
            }
        )

    # Add Splunk error events
    for log in splunk_logs.get("error_logs", []):
        msg = log.get("message", "")
        if any(kw in msg.lower() for kw in ["error", "failed", "fatal", "exception"]):
            timeline_events.append(
                {
                    "timestamp": log.get("timestamp", ""),
                    "source": "splunk_ocp",
                    "event_type": "pod_error",
                    "summary": f"Error in pod '{log.get('pod_name')}'",
                    "details": {
                        "pod_name": log.get("pod_name"),
                        "container": log.get("container_name"),
                        "message": msg[:500],
                    },
                }
            )

    # Sort by timestamp
    timeline_events.sort(key=lambda x: x.get("timestamp", ""))

    # Analyze correlation
    correlation_analysis = _analyze_correlation(job_context, splunk_logs)

    return {
        "job_id": job_context.get("job_id"),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "time_range": {
            "start": job_context.get("time_window", {}).get("started"),
            "end": job_context.get("time_window", {}).get("finished"),
            "duration_seconds": job_context.get("time_window", {}).get("duration_seconds"),
        },
        "correlation": correlation_analysis,
        "timeline_events": timeline_events,
        "summary": {
            "aap_failed_tasks": len(job_context.get("failed_tasks", [])),
            "splunk_error_logs": len(splunk_logs.get("error_logs", [])),
            "total_timeline_events": len(timeline_events),
            "pods_involved": [p["pod_name"] for p in splunk_logs.get("pods_found", [])],
        },
    }


def _analyze_correlation(
    job_context: dict[str, Any],
    splunk_logs: dict[str, Any],
) -> dict[str, Any]:
    """Analyze correlation strength between job and Splunk logs."""
    guid = job_context.get("guid", "")
    namespace = job_context.get("namespace", "")
    job_start = job_context.get("time_window", {}).get("started", "")
    job_end = job_context.get("time_window", {}).get("finished", "")

    # Check for matching identifiers
    identifiers_match = {
        "guid": guid if guid else None,
        "namespace": namespace if namespace else None,
    }

    # Check time overlap
    splunk_timestamps = [
        log.get("timestamp", "") for log in splunk_logs.get("ocp_logs", []) if log.get("timestamp")
    ]

    time_overlap = False
    splunk_first = None
    splunk_last = None

    if splunk_timestamps:
        splunk_first = min(splunk_timestamps)
        splunk_last = max(splunk_timestamps)

        # Simple overlap check (both are ISO format strings)
        if job_start and job_end and splunk_first and splunk_last:
            time_overlap = not (splunk_last < job_start or splunk_first > job_end)

    # Check pod references
    job_pod_refs = [p["pod_name"] for p in job_context.get("pod_references", [])]
    splunk_pods = [p["pod_name"] for p in splunk_logs.get("pods_found", [])]
    matching_pods = list(set(job_pod_refs) & set(splunk_pods))

    # Determine correlation method and confidence
    if namespace and time_overlap:
        method = "namespace_time_match"
        confidence = "high"
    elif guid and time_overlap:
        method = "guid_time_match"
        confidence = "high"
    elif matching_pods:
        method = "pod_name_match"
        confidence = "medium"
    elif guid or namespace:
        method = "identifier_match"
        confidence = "low"
    else:
        method = "none"
        confidence = "none"

    return {
        "method": method,
        "confidence": confidence,
        "identifiers": identifiers_match,
        "time_overlap": {
            "aap_job_start": job_start,
            "aap_job_end": job_end,
            "splunk_first_log": splunk_first,
            "splunk_last_log": splunk_last,
            "overlap_confirmed": time_overlap,
        },
        "matching_pods": matching_pods,
    }
