"""Unit tests for RCA embedding metadata helpers (no live DB required)."""

from scripts.vector import build_embedding_text, build_rca_metadata


def _sample_job_context():
    return {
        "job_id": "12345",
        "status": "failed",
        "guid": "x1234",
        "namespace": "sandbox-x1234-xx-ansiblebu",
        "cluster": "test-cluster",
        "cloud_provider": "aws",
        "env_type": "sandbox",
        "action": "provision",
        "time_window": {
            "started": "2025-01-15T10:00:00Z",
            "finished": "2025-01-15T10:05:00Z",
            "duration_seconds": 300,
        },
        "failed_tasks": [
            {
                "task": "Install dependencies",
                "error_message": "Package not found",
                "task_action": "yum",
                "play": "Setup",
                "role": "common",
            },
            {
                "task": "Create secret",
                "error_message": "'aws_access_key_id' is undefined",
                "task_action": "kubernetes.core.k8s",
            },
        ],
    }


def _sample_github_context():
    return {
        "job_id": "12345",
        "job_metadata": {
            "platform": "openshift_cnv",
            "catalog_item": "ocp4-cluster",
            "env": "test",
            "guid": "x1234",
        },
        "github_fetches": [],
    }


def _sample_summary():
    return {
        "job_id": "12345",
        "analyzed_at": "2025-01-15T10:30:45Z",
        "root_cause": {
            "summary": "Missing aws_access_key_id in platform account config",
            "category": "configuration",
            "confidence": "high",
        },
        "evidence": [
            {
                "source": "aap_job",
                "timestamp": "2025-01-15T10:30:45Z",
                "message": "'aws_access_key_id' is undefined",
            },
            {
                "source": "agnosticv_config",
                "timestamp": "2025-01-15T10:30:45Z",
                "message": "Missing variable in environment config",
                "github_path": "example-org/config-repo:openshift_cnv/account.yaml",
            },
        ],
        "recommendations": [
            {
                "priority": "high",
                "action": "Add missing variable",
                "file": "openshift_cnv/account.yaml",
                "github_path": "example-org/config-repo:openshift_cnv/account.yaml",
                "change": "Add aws_access_key_id variable",
            },
            {
                "priority": "medium",
                "action": "Document required secrets",
                "file": "README.md",
            },
        ],
        "contributing_factors": [
            "Missing variable definition",
            "Incomplete configuration",
        ],
    }


def test_build_rca_metadata_assembles_fields():
    metadata = build_rca_metadata(
        _sample_job_context(),
        _sample_github_context(),
        _sample_summary(),
    )

    assert metadata["job_id"] == "12345"
    assert metadata["guid"] == "x1234"
    assert metadata["namespace"] == "sandbox-x1234-xx-ansiblebu"
    assert metadata["cluster"] == "test-cluster"
    assert metadata["status"] == "failed"
    assert metadata["action"] == "provision"
    assert metadata["cloud_provider"] == "aws"
    assert metadata["env_type"] == "sandbox"
    assert metadata["platform"] == "openshift_cnv"
    assert metadata["catalog_item"] == "ocp4-cluster"
    assert metadata["env"] == "test"
    assert metadata["job_duration_seconds"] == 300
    assert metadata["root_cause_category"] == "configuration"
    assert metadata["root_cause_summary"] == (
        "Missing aws_access_key_id in platform account config"
    )
    assert metadata["confidence"] == "high"
    assert metadata["contributing_factors"] == [
        "Missing variable definition",
        "Incomplete configuration",
    ]
    assert metadata["recommendation_count"] == 2
    assert metadata["analyzed_at"] == "2025-01-15T10:30:45Z"
    assert metadata["github_paths"] == [
        "example-org/config-repo:openshift_cnv/account.yaml",
    ]
    assert len(metadata["failed_tasks"]) == 2
    assert metadata["failed_tasks"][0] == {
        "task": "Install dependencies",
        "error_message": "Package not found",
        "task_action": "yum",
    }


def test_build_rca_metadata_without_github_context():
    metadata = build_rca_metadata(_sample_job_context(), None, _sample_summary())

    assert metadata["platform"] == ""
    assert metadata["catalog_item"] == ""
    assert metadata["env"] == ""
    assert metadata["job_id"] == "12345"
    assert metadata["root_cause_category"] == "configuration"


def test_build_rca_metadata_prefers_summary_job_id():
    summary = _sample_summary()
    summary["job_id"] = "99999"
    metadata = build_rca_metadata(_sample_job_context(), None, summary)
    assert metadata["job_id"] == "99999"


def test_build_embedding_text_includes_semantic_content():
    metadata = build_rca_metadata(
        _sample_job_context(),
        _sample_github_context(),
        _sample_summary(),
    )
    text = build_embedding_text(metadata)

    assert "configuration" in text
    assert "Missing aws_access_key_id" in text
    assert "ocp4-cluster" in text
    assert "openshift_cnv" in text
    assert "Missing variable definition" in text
    assert "Package not found" in text
    assert "Install dependencies" in text
    # IDs and ticket-like noise should not dominate
    assert "job_id" not in text.lower()


def test_build_embedding_text_empty_metadata():
    text = build_embedding_text({})
    assert text == ""
