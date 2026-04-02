import gzip
import json
import sys
from pathlib import Path

import jsonschema
import pytest

# Add root-cause-analysis/scripts to path
rca_root = Path(__file__).resolve().parent.parent.parent
schemas_path = rca_root / "schemas"
sys.path.append(str(rca_root))

from scripts.job_parser import (  # noqa: E402
    _extract_failed_tasks,
    _extract_namespace,
    _extract_pod_references,
    extract_job_context,
    load_job_log,
    parse_job_log,
)


@pytest.fixture
def sample_job_data():
    data_path = Path(__file__).parent.parent / "data" / "sample_job.json"
    with open(data_path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def job_context_schema():
    schema_path = schemas_path / "job_context.schema.json"
    with open(schema_path) as f:
        return json.load(f)


# --- load_job_log tests ---


def test_load_job_log_json(tmp_path, sample_job_data):
    """Test loading a plain .json file."""
    file_path = tmp_path / "job.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(sample_job_data, f)

    loaded_data = load_job_log(file_path)
    assert loaded_data == sample_job_data


def test_load_job_log_gz(tmp_path, sample_job_data):
    """Test loading a .json.gz file."""
    file_path = tmp_path / "job.json.gz"
    with gzip.open(file_path, "wt", encoding="utf-8") as f:
        json.dump(sample_job_data, f)

    loaded_data = load_job_log(file_path)
    assert loaded_data == sample_job_data


def test_load_job_log_gz_no_ext(tmp_path, sample_job_data):
    """Test loading a gzipped file without .gz extension (UnicodeDecodeError fallback)."""
    file_path = tmp_path / "job_log_no_ext"
    with gzip.open(file_path, "wt", encoding="utf-8") as f:
        json.dump(sample_job_data, f)

    loaded_data = load_job_log(file_path)
    assert loaded_data == sample_job_data


def test_load_job_log_invalid_json(tmp_path):
    """Test that it raises on invalid JSON."""
    file_path = tmp_path / "invalid.json"
    file_path.write_text("not valid json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        load_job_log(file_path)


# --- extract_job_context tests ---


def test_extract_job_context_basic(sample_job_data):
    """Test extraction of basic metadata fields."""
    context = extract_job_context(sample_job_data)

    assert context["job_id"] == "12345"
    assert context["guid"] == "test-guid-abc"
    assert context["status"] == "failed"
    assert context["job_name"] == "Test Job"
    assert context["cluster"] == "test-cluster"


def test_extract_job_context_time_window(sample_job_data):
    """Test extraction of time window."""
    context = extract_job_context(sample_job_data)
    time_window = context["time_window"]

    assert time_window["started"] == "2023-10-27T10:00:00Z"
    assert time_window["finished"] == "2023-10-27T10:05:00Z"
    assert time_window["duration_seconds"] == 300


def test_extract_job_context_missing_fields():
    """Test that missing optional fields return empty strings/defaults."""
    empty_data = {"metadata": {}, "events": []}
    context = extract_job_context(empty_data)

    assert context["guid"] == ""
    assert context["namespace"] == ""
    assert context["failed_tasks"] == []
    assert context["pod_references"] == []


# --- _extract_namespace tests ---


def test_extract_namespace_explicit(sample_job_data):
    """Test extracting namespace from explicit 'namespace: ...' string."""
    events = [
        {"stdout": 'namespace: "sandbox-abc12-zt-ansiblebu"'},
    ]
    namespace = _extract_namespace(events, "abc12")
    assert namespace == "sandbox-abc12-zt-ansiblebu"


def test_extract_namespace_from_guid(sample_job_data):
    """Test extracting namespace using GUID pattern matching."""
    events = [
        {"stdout": "some log with sandbox-guid123-ns-name in it"},
    ]
    namespace = _extract_namespace(events, "guid123")
    assert namespace == "sandbox-guid123-ns-name"


def test_extract_namespace_not_found():
    """Test that empty string is returned when no namespace is found."""
    events = [{"stdout": "just some logs"}]
    namespace = _extract_namespace(events, "guid123")
    assert namespace == ""


def test_extract_namespace_no_stdout():
    """Test handling events with no stdout key."""
    events = [{"event": "playbook_on_start"}]
    namespace = _extract_namespace(events, "guid123")
    assert namespace == ""


# --- _extract_pod_references tests ---


def test_extract_pod_references_patterns():
    """Test extracting pod names with various patterns."""
    events = [
        {"stdout": "pod/test-pod-abc-123 created", "task": "Task 1", "created": "t1"},
        {"stdout": "showroom-abc12-xyz45 started", "task": "Task 2", "created": "t2"},
        {
            "stdout": 'kubernetes.pod_name = "pod-name-789"',
            "task": "Task 3",
            "created": "t3",
        },
    ]
    pods = _extract_pod_references(events)
    pod_names = {p["pod_name"] for p in pods}

    assert "test-pod-abc-123" in pod_names
    assert "showroom-abc12-xyz45" in pod_names
    assert "pod-name-789" in pod_names


def test_extract_pod_references_deduplication():
    """Test that duplicate pod names are ignored."""
    events = [
        {"stdout": "pod/duplicate-pod-123 created", "task": "Task 1", "created": "t1"},
        {"stdout": "pod/duplicate-pod-123 deleted", "task": "Task 2", "created": "t2"},
    ]
    pods = _extract_pod_references(events)
    assert len(pods) == 1
    assert pods[0]["pod_name"] == "duplicate-pod-123"


def test_extract_pod_references_empty():
    """Test that empty list is returned when no pods found."""
    events = [{"stdout": "no pods here"}]
    pods = _extract_pod_references(events)
    assert pods == []


# --- _extract_failed_tasks tests ---


def test_extract_failed_tasks_basic(sample_job_data):
    """Test extracting failed tasks details."""
    failed = _extract_failed_tasks(sample_job_data["events"])

    assert len(failed) == 1
    task = failed[0]
    assert task["task"] == "Install dependencies"
    assert task["play"] == "Setup"
    assert task["role"] == "common"
    assert task["error_message"] == "Package not found"
    assert task["task_action"] == "yum"
    assert task["duration"] == 5.2
    assert task["rc"] == 1
    assert task["cmd"] == ["yum", "install", "-y", "missing-package"]
    assert task["stderr"] == "Error: No matching packages to install"
    # stdout in res is empty string, so it should not be included from res
    assert "stdout" not in task or task.get("stdout") != ""


def test_extract_failed_tasks_ignores_non_failures():
    """Test that non-failed events are ignored."""
    events = [
        {"event": "runner_on_ok", "failed": False, "task": "OK Task"},
        {"event": "runner_on_failed", "failed": False, "task": "False Alarm"},
    ]
    failed = _extract_failed_tasks(events)
    assert len(failed) == 0


def test_extract_failed_tasks_string_res():
    """Test handling 'res' being a string instead of a dict."""
    events = [
        {
            "event": "runner_on_failed",
            "failed": True,
            "task": "Fail Task",
            "event_data": {"res": "Simple error string"},
        }
    ]
    failed = _extract_failed_tasks(events)
    assert failed[0]["error_message"] == "Simple error string"


def test_extract_failed_tasks_with_diagnostic_fields():
    """Test that stdout, stderr, cmd, and rc are extracted from res."""
    events = [
        {
            "event": "runner_on_failed",
            "failed": True,
            "task": "Git checkout",
            "event_data": {
                "res": {
                    "msg": "non-zero return code",
                    "rc": 1,
                    "cmd": ["git", "checkout", "cert-manager-fallback"],
                    "stdout": "",
                    "stderr": "error: pathspec 'cert-manager-fallback' did not match any file(s) known to git",
                },
            },
        }
    ]
    failed = _extract_failed_tasks(events)
    assert len(failed) == 1
    task = failed[0]
    assert task["error_message"] == "non-zero return code"
    assert task["rc"] == 1
    assert task["cmd"] == ["git", "checkout", "cert-manager-fallback"]
    assert (
        task["stderr"]
        == "error: pathspec 'cert-manager-fallback' did not match any file(s) known to git"
    )
    # stdout is empty string in res, so should not be in task_info from res
    # but event has no stdout either, so stdout key should be absent
    assert "stdout" not in task


def test_extract_failed_tasks_event_stdout_fallback():
    """Test fallback to event-level stdout when res has no stdout."""
    events = [
        {
            "event": "runner_on_failed",
            "failed": True,
            "task": "Run script",
            "stdout": "TASK [run_script] fatal: host unreachable",
            "event_data": {
                "res": {
                    "msg": "Connection timed out",
                },
            },
        }
    ]
    failed = _extract_failed_tasks(events)
    assert len(failed) == 1
    task = failed[0]
    assert task["stdout"] == "TASK [run_script] fatal: host unreachable"


def test_extract_failed_tasks_res_stdout_takes_priority():
    """Test that res.stdout takes priority over event stdout."""
    events = [
        {
            "event": "runner_on_failed",
            "failed": True,
            "task": "Run command",
            "stdout": "rendered ansible output",
            "event_data": {
                "res": {
                    "msg": "failed",
                    "stdout": "actual command output from res",
                },
            },
        }
    ]
    failed = _extract_failed_tasks(events)
    assert failed[0]["stdout"] == "actual command output from res"


def test_extract_failed_tasks_no_failures():
    """Test that empty list is returned for job with no failures."""
    events = [{"event": "runner_on_ok", "task": "OK Task"}]
    failed = _extract_failed_tasks(events)
    assert failed == []


# --- parse_job_log (integration) tests ---


def test_parse_job_log_integration(tmp_path, sample_job_data, job_context_schema):
    """End-to-end test: parse file and validate against schema."""
    file_path = tmp_path / "job.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(sample_job_data, f)

    context = parse_job_log(file_path)

    # Validate against actual JSON schema
    jsonschema.validate(instance=context, schema=job_context_schema)

    # Verify specific fields
    assert context["job_id"] == "12345"
    assert context["guid"] == "test-guid-abc"
    assert context["namespace"] == "sandbox-test-namespace"
    assert len(context["failed_tasks"]) == 1
    assert context["source_file"] == str(file_path)
    assert "parsed_at" in context
