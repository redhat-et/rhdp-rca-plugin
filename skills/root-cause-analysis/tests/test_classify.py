"""Tests for classify.py — known failure pattern matching."""

import tempfile
from pathlib import Path

import yaml

from scripts.classify import classify_error, classify_job_errors, load_known_failures

SAMPLE_FAILURES = [
    {
        "error_string": "Shared connection to.*compute.amazonaws.com closed",
        "description": "Unable to reach bastion host",
        "category": "connectivity_failure",
    },
    {
        "error_string": "Bootstrap failed to complete: timed out waiting for the condition",
        "description": "OpenShift Installer failed due to cloud timeout",
        "category": "timeout_failure",
    },
    {
        "error_string": "MODULE FAILURE",
        "description": "Ansible module failure",
        "category": "automation_failure",
    },
]


def _write_yaml(tmp_dir: Path, failures: list[dict]) -> Path:
    path = tmp_dir / "known_failed.yaml"
    with open(path, "w") as f:
        yaml.dump({"failures": failures}, f)
    return path


def test_load_known_failures_valid():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_yaml(Path(tmp), SAMPLE_FAILURES)
        failures = load_known_failures(path)
        assert len(failures) == 3


def test_load_known_failures_missing_file():
    failures = load_known_failures("/nonexistent/path.yaml")
    assert failures == []


def test_load_known_failures_empty_file():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "empty.yaml"
        path.write_text("")
        failures = load_known_failures(path)
        assert failures == []


def test_classify_error_match():
    result = classify_error(
        "Shared connection to ec2-1-2-3-4.compute.amazonaws.com closed",
        SAMPLE_FAILURES,
    )
    assert result is not None
    assert result["error_category"] == "connectivity_failure"
    assert result["failure_description"] == "Unable to reach bastion host"


def test_classify_error_no_match():
    result = classify_error("Some completely unknown error", SAMPLE_FAILURES)
    assert result is None


def test_classify_error_empty_message():
    result = classify_error("", SAMPLE_FAILURES)
    assert result is None


def test_classify_error_empty_patterns():
    result = classify_error("MODULE FAILURE", [])
    assert result is None


def test_classify_error_invalid_regex_skipped():
    bad_patterns = [
        {"error_string": "[invalid(regex", "description": "bad", "category": "x"},
        {"error_string": "MODULE FAILURE", "description": "ok", "category": "automation_failure"},
    ]
    result = classify_error("MODULE FAILURE", bad_patterns)
    assert result is not None
    assert result["error_category"] == "automation_failure"


def test_classify_job_errors_reads_details_message():
    """Timeline events store messages in details.message, not top-level message."""
    job_context = {
        "failed_tasks": [
            {
                "task": "Connect to bastion",
                "error_message": "Shared connection to ec2-1-2-3.compute.amazonaws.com closed",
            },
        ]
    }
    # Matches real build_correlation_timeline() output structure:
    # aap_job events have details.error_message
    # splunk_ocp events have details.message
    correlation = {
        "timeline_events": [
            {
                "source": "aap_job",
                "event_type": "task_failed",
                "summary": "Task 'Run module' failed",
                "details": {"task": "Run module", "error_message": "MODULE FAILURE"},
            },
            {
                "source": "splunk_ocp",
                "event_type": "pod_error",
                "summary": "Error in pod 'installer-xyz'",
                "details": {
                    "pod_name": "installer-xyz",
                    "message": "Bootstrap failed to complete: timed out waiting for the condition",
                },
            },
        ]
    }
    results = classify_job_errors(job_context, correlation, SAMPLE_FAILURES)
    assert len(results) == 3
    categories = {r["error_category"] for r in results}
    assert categories == {"connectivity_failure", "automation_failure", "timeout_failure"}


def test_classify_job_errors_deduplicates():
    """Same error in step1 and step3 should only appear once."""
    job_context = {
        "failed_tasks": [
            {"task": "Run module", "error_message": "MODULE FAILURE"},
        ]
    }
    correlation = {
        "timeline_events": [
            {
                "source": "aap_job",
                "details": {"error_message": "MODULE FAILURE"},
            },
        ]
    }
    results = classify_job_errors(job_context, correlation, SAMPLE_FAILURES)
    assert len(results) == 1
    assert results[0]["error_category"] == "automation_failure"


def test_classify_job_errors_empty_inputs():
    """No errors should produce empty results, not crash."""
    results = classify_job_errors({}, {}, SAMPLE_FAILURES)
    assert results == []
    results = classify_job_errors({"failed_tasks": []}, {"timeline_events": []}, SAMPLE_FAILURES)
    assert results == []
    results = classify_job_errors({"failed_tasks": []}, {"timeline_events": []}, [])
    assert results == []
