import sys
from pathlib import Path
from unittest.mock import MagicMock

# Add root-cause-analysis to path
rca_root = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(rca_root))

from scripts.config import Config, SplunkConfig  # noqa: E402

# --- Config.from_env tests ---


def test_config_from_env_loads_vars(monkeypatch, tmp_path):
    """Test loading configuration from environment variables."""
    monkeypatch.setenv("SPLUNK_HOST", "https://splunk.test")
    monkeypatch.setenv("SPLUNK_USERNAME", "testuser")
    monkeypatch.setenv("SPLUNK_PASSWORD", "testpass")
    monkeypatch.setenv("SPLUNK_INDEX", "main")
    monkeypatch.setenv("SPLUNK_VERIFY_SSL", "true")
    monkeypatch.setenv("SPLUNK_OCP_APP_INDEX", "ocp_apps")
    monkeypatch.setenv("SPLUNK_OCP_INFRA_INDEX", "ocp_infra")
    monkeypatch.setenv("JOB_LOGS_DIR", str(tmp_path / "logs"))

    config = Config.from_env(base_dir=tmp_path)

    assert config.splunk.host == "https://splunk.test"
    assert config.splunk.username == "testuser"
    assert config.splunk.password == "testpass"
    assert config.splunk.index == "main"
    assert config.splunk.verify_ssl is True
    assert config.splunk.ocp_app_index == "ocp_apps"
    assert config.splunk.ocp_infra_index == "ocp_infra"
    assert config.job_logs_dir == tmp_path / "logs"


def test_config_from_env_defaults(monkeypatch, tmp_path):
    """Test defaults when env vars are missing."""
    # Clear relevant env vars
    monkeypatch.delenv("SPLUNK_HOST", raising=False)
    monkeypatch.delenv("SPLUNK_USERNAME", raising=False)
    monkeypatch.delenv("SPLUNK_PASSWORD", raising=False)
    monkeypatch.delenv("SPLUNK_INDEX", raising=False)
    monkeypatch.delenv("SPLUNK_VERIFY_SSL", raising=False)
    monkeypatch.delenv("SPLUNK_TOKEN", raising=False)
    monkeypatch.delenv("SPLUNK_OCP_APP_INDEX", raising=False)
    monkeypatch.delenv("SPLUNK_OCP_INFRA_INDEX", raising=False)
    monkeypatch.delenv("JOB_LOGS_DIR", raising=False)

    config = Config.from_env(base_dir=tmp_path)

    assert config.splunk.host == ""
    assert config.splunk.username == ""
    assert config.splunk.password == ""
    assert config.splunk.index is None
    assert config.splunk.verify_ssl is False  # Default is False (from "false")
    assert config.splunk.token is None
    assert config.splunk.ocp_app_index is None
    assert config.splunk.ocp_infra_index is None
    assert config.job_logs_dir is None


def test_config_from_env_verify_ssl_parsing(monkeypatch, tmp_path):
    """Test parsing of SPLUNK_VERIFY_SSL."""
    monkeypatch.setenv("SPLUNK_VERIFY_SSL", "true")
    assert Config.from_env(base_dir=tmp_path).splunk.verify_ssl is True

    monkeypatch.setenv("SPLUNK_VERIFY_SSL", "True")
    assert Config.from_env(base_dir=tmp_path).splunk.verify_ssl is True

    monkeypatch.setenv("SPLUNK_VERIFY_SSL", "false")
    assert Config.from_env(base_dir=tmp_path).splunk.verify_ssl is False

    monkeypatch.setenv("SPLUNK_VERIFY_SSL", "foo")
    assert Config.from_env(base_dir=tmp_path).splunk.verify_ssl is False


# --- Config.find_job_log tests ---


def test_find_job_log_json(tmp_path):
    """Test finding plain .json log file."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "job_123.json").touch()

    config = Config(splunk=MagicMock(), analysis_dir=tmp_path, job_logs_dir=logs_dir)

    found = config.find_job_log("123")
    assert found == logs_dir / "job_123.json"


def test_find_job_log_gz(tmp_path):
    """Test finding .json.gz log file."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "job_123.json.gz").touch()

    config = Config(splunk=MagicMock(), analysis_dir=tmp_path, job_logs_dir=logs_dir)

    found = config.find_job_log("123")
    assert found == logs_dir / "job_123.json.gz"


def test_find_job_log_transform(tmp_path):
    """Test finding transformed log file."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "job_123.json.gz.transform-processed").touch()

    config = Config(splunk=MagicMock(), analysis_dir=tmp_path, job_logs_dir=logs_dir)

    found = config.find_job_log("123")
    assert found == logs_dir / "job_123.json.gz.transform-processed"


def test_find_job_log_no_match(tmp_path):
    """Test returning None when no match found."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    config = Config(splunk=MagicMock(), analysis_dir=tmp_path, job_logs_dir=logs_dir)

    found = config.find_job_log("123")
    assert found is None


def test_find_job_log_no_dir(tmp_path):
    """Test returning None when job_logs_dir is None."""
    config = Config(splunk=MagicMock(), analysis_dir=tmp_path, job_logs_dir=None)

    found = config.find_job_log("123")
    assert found is None


# --- Config.validate_splunk tests ---


def test_validate_splunk_valid():
    """Test valid configuration returns empty list."""
    splunk = SplunkConfig(host="host", username="user", password="pass", index="main")
    config = Config(splunk=splunk, analysis_dir=Path("."))
    assert config.validate_splunk() == []


def test_validate_splunk_missing_host():
    """Test error when host is missing."""
    splunk = SplunkConfig(host="", username="user", password="pass", index="main")
    config = Config(splunk=splunk, analysis_dir=Path("."))
    errors = config.validate_splunk()
    assert len(errors) == 1
    assert "SPLUNK_HOST is required" in errors[0]


def test_validate_splunk_no_auth():
    """Test error when no auth configured."""
    splunk = SplunkConfig(host="host", username="", password="", index="main", token=None)
    config = Config(splunk=splunk, analysis_dir=Path("."))
    errors = config.validate_splunk()
    assert len(errors) == 1
    assert "SPLUNK_USERNAME/SPLUNK_PASSWORD or SPLUNK_TOKEN is required" in errors[0]


# --- Config pgvector tests ---


def test_config_from_env_loads_pgvector(monkeypatch, tmp_path):
    """Test loading pgvector configuration from environment variables."""
    monkeypatch.setenv("PGVECTOR_HOST", "127.0.0.1")
    monkeypatch.setenv("PGVECTOR_PORT", "5433")
    monkeypatch.setenv("PGVECTOR_DB_NAME", "rca_vectors")
    monkeypatch.setenv("PGVECTOR_DB_USER", "rca_pgvector")
    monkeypatch.setenv("PGVECTOR_DB_PASSWORD", "secret")
    monkeypatch.setenv("PGVECTOR_TABLE", "custom_embeddings")

    config = Config.from_env(base_dir=tmp_path)

    assert config.pgvector_host == "127.0.0.1"
    assert config.pgvector_port == 5433
    assert config.pgvector_db_name == "rca_vectors"
    assert config.pgvector_db_user == "rca_pgvector"
    assert config.pgvector_db_password == "secret"
    assert config.pgvector_table == "custom_embeddings"
    assert config.has_pgvector() is True


def test_config_pgvector_falls_back_to_source_db(monkeypatch, tmp_path):
    """Test PGVECTOR_DB_* falls back to SOURCE_DB_* when unset."""
    monkeypatch.setenv("PGVECTOR_HOST", "127.0.0.1")
    monkeypatch.delenv("PGVECTOR_DB_NAME", raising=False)
    monkeypatch.delenv("PGVECTOR_DB_USER", raising=False)
    monkeypatch.delenv("PGVECTOR_DB_PASSWORD", raising=False)
    monkeypatch.setenv("SOURCE_DB_NAME", "aap2-agents")
    monkeypatch.setenv("SOURCE_DB_USER", "aap2-agent-ro")
    monkeypatch.setenv("SOURCE_DB_PASSWORD", "source-secret")

    config = Config.from_env(base_dir=tmp_path)

    assert config.pgvector_db_name == "aap2-agents"
    assert config.pgvector_db_user == "aap2-agent-ro"
    assert config.pgvector_db_password == "source-secret"
    assert config.has_pgvector() is True


def test_has_pgvector_false_when_missing(monkeypatch, tmp_path):
    """Test has_pgvector returns False when host is unset."""
    monkeypatch.delenv("PGVECTOR_HOST", raising=False)
    monkeypatch.delenv("PGVECTOR_DB_NAME", raising=False)
    monkeypatch.delenv("PGVECTOR_DB_USER", raising=False)
    monkeypatch.delenv("PGVECTOR_DB_PASSWORD", raising=False)
    monkeypatch.delenv("SOURCE_DB_NAME", raising=False)
    monkeypatch.delenv("SOURCE_DB_USER", raising=False)
    monkeypatch.delenv("SOURCE_DB_PASSWORD", raising=False)

    config = Config.from_env(base_dir=tmp_path)
    assert config.has_pgvector() is False
    assert config.pgvector_table == "rca_analysis_embeddings"
