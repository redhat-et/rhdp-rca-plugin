"""Configuration management for splunk-log-analysis."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def load_settings_env(repo_root: Path) -> dict[str, str]:
    """Load env vars from .claude/settings.json into os.environ.

    Reads the ``"env"`` object from ``repo_root/.claude/settings.json``
    (or ``settings.local.json``, checked first).  Each key/value pair is
    injected into ``os.environ`` only if the key is not already set, so
    real environment variables always take precedence.

    Returns the dict of env vars that were actually applied.
    """
    applied: dict[str, str] = {}

    # Load settings.json first, then settings.local.json on top (local overrides).
    # os.environ.setdefault ensures real env vars always win over both files,
    # and processing local second ensures its values override base ones.
    merged: dict[str, str] = {}
    for name in ("settings.json", "settings.local.json"):
        settings_path = repo_root / ".claude" / name
        if not settings_path.is_file():
            continue
        try:
            with open(settings_path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        env_block = data.get("env")
        if isinstance(env_block, dict):
            merged.update({k: str(v) for k, v in env_block.items()})

    for key, value in merged.items():
        if key not in os.environ:
            os.environ[key] = value
            applied[key] = value

    return applied


def _none_if_empty(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value if value else None


@dataclass
class SplunkConfig:
    host: str
    username: str
    password: str
    index: str | None = None
    verify_ssl: bool = True
    # Legacy token-based auth (optional, username/password preferred)
    token: str | None = None
    # Additional indices for OCP logs
    ocp_app_index: str | None = None
    ocp_infra_index: str | None = None

    @property
    def auth_method(self) -> str:
        """Return the authentication method being used."""
        if self.username and self.password:
            return "basic"
        elif self.token:
            return "token"
        return "none"


@dataclass
class Config:
    splunk: SplunkConfig
    analysis_dir: Path
    job_logs_dir: Path | None = None
    github_token: str | None = None
    remote_host: str = ""
    remote_log_dir: str = ""
    jumpbox_uri: str = ""
    # Database configuration for per-job bastion lookup
    source_db_host: str = ""
    source_db_port: int = 5432
    source_db_name: str = ""
    source_db_user: str = ""
    source_db_password: str = ""
    source_db_table: str = ""
    source_db_bastion_table: str = ""
    # SSH configuration
    ssh_jumpbox_alias: str = ""
    bastion_ssh_user: str = ""
    # pgvector configuration for RCA embedding store
    pgvector_host: str = ""
    pgvector_port: int = 5432
    pgvector_db_name: str = ""
    pgvector_db_user: str = ""
    pgvector_db_password: str = ""
    pgvector_table: str = "rca_analysis_embeddings"

    @classmethod
    def from_env(cls, base_dir: Path | None = None) -> "Config":
        """Load configuration from environment variables."""
        if base_dir is None:
            base_dir = Path(__file__).parent.parent

        # Load env vars from .claude/settings.json at the repo root
        # (base_dir is skills/root-cause-analysis, repo root is two levels up)
        repo_root = base_dir.resolve().parent.parent
        load_settings_env(repo_root)

        # Load .env file if present
        env_file = base_dir / ".env"
        if env_file.exists():
            load_dotenv(env_file)

        splunk = SplunkConfig(
            host=os.environ.get("SPLUNK_HOST", ""),
            username=os.environ.get("SPLUNK_USERNAME", ""),
            password=os.environ.get("SPLUNK_PASSWORD", ""),
            index=_none_if_empty(os.environ.get("SPLUNK_INDEX")),
            verify_ssl=os.environ.get("SPLUNK_VERIFY_SSL", "false").lower() == "true",
            token=os.environ.get("SPLUNK_TOKEN"),
            ocp_app_index=_none_if_empty(os.environ.get("SPLUNK_OCP_APP_INDEX")),
            ocp_infra_index=_none_if_empty(os.environ.get("SPLUNK_OCP_INFRA_INDEX")),
        )

        analysis_dir = base_dir / ".analysis"

        # Default directory for job log files
        job_logs_dir_str = os.environ.get("JOB_LOGS_DIR", "")
        job_logs_dir = Path(job_logs_dir_str) if job_logs_dir_str else None

        # GitHub token for Step 4
        github_token = os.environ.get("GITHUB_TOKEN")

        # Remote log server settings (for --fetch), shared with logs-fetcher.
        remote_host = os.environ.get("REMOTE_HOST", "")
        remote_log_dir = os.environ.get("REMOTE_DIR", "")

        # Jumpbox URI for uploading analysis files
        jumpbox_uri = os.environ.get("JUMPBOX_URI", "")

        # Database configuration for per-job bastion lookup
        source_db_host = os.environ.get("SOURCE_DB_HOST", "")
        source_db_port = int(os.environ.get("SOURCE_DB_PORT", "5432"))
        source_db_name = os.environ.get("SOURCE_DB_NAME", "")
        source_db_user = os.environ.get("SOURCE_DB_USER", "")
        source_db_password = os.environ.get("SOURCE_DB_PASSWORD", "")
        source_db_table = os.environ.get("SOURCE_DB_TABLE", "aap2_events")
        source_db_bastion_table = os.environ.get("SOURCE_DB_BASTION_TABLE", "aap2_user_url")

        # SSH configuration
        ssh_jumpbox_alias = os.environ.get("SSH_JUMPBOX_ALIAS", "rca-jumpbox")
        bastion_ssh_user = os.environ.get("BASTION_SSH_USER", "")

        # pgvector configuration (falls back to SOURCE_DB_* for name/user/password)
        pgvector_host = os.environ.get("PGVECTOR_HOST", "")
        pgvector_port = int(os.environ.get("PGVECTOR_PORT", "5432"))
        pgvector_db_name = os.environ.get("PGVECTOR_DB_NAME", "") or source_db_name
        pgvector_db_user = os.environ.get("PGVECTOR_DB_USER", "") or source_db_user
        pgvector_db_password = (
            os.environ.get("PGVECTOR_DB_PASSWORD", "") or source_db_password
        )
        pgvector_table = os.environ.get("PGVECTOR_TABLE", "rca_analysis_embeddings")

        return cls(
            splunk=splunk,
            analysis_dir=analysis_dir,
            job_logs_dir=job_logs_dir,
            github_token=github_token,
            remote_host=remote_host,
            remote_log_dir=remote_log_dir,
            jumpbox_uri=jumpbox_uri,
            source_db_host=source_db_host,
            source_db_port=source_db_port,
            source_db_name=source_db_name,
            source_db_user=source_db_user,
            source_db_password=source_db_password,
            source_db_table=source_db_table,
            source_db_bastion_table=source_db_bastion_table,
            ssh_jumpbox_alias=ssh_jumpbox_alias,
            bastion_ssh_user=bastion_ssh_user,
            pgvector_host=pgvector_host,
            pgvector_port=pgvector_port,
            pgvector_db_name=pgvector_db_name,
            pgvector_db_user=pgvector_db_user,
            pgvector_db_password=pgvector_db_password,
            pgvector_table=pgvector_table,
        )

    def find_job_log(self, job_id: str) -> Path | None:
        """Find a job log file by job ID in the configured directory."""
        if not self.job_logs_dir or not self.job_logs_dir.exists():
            return None

        # Search for files matching job_<id>.*
        patterns = [
            f"job_{job_id}.json",
            f"job_{job_id}.json.gz",
            f"job_{job_id}.json.gz.transform-processed",
            f"job_{job_id}.json.transform-processed",
        ]

        for pattern in patterns:
            path = self.job_logs_dir / pattern
            if path.exists():
                return path

        # Fallback: glob search for any file starting with job_<id>
        matches = list(self.job_logs_dir.glob(f"job_{job_id}.*"))
        if matches:
            return matches[0]

        return None

    def validate_splunk(self) -> list[str]:
        """Validate Splunk configuration, return list of errors."""
        errors = []
        if not self.splunk.host:
            errors.append("SPLUNK_HOST is required")
        if self.splunk.auth_method == "none":
            errors.append("SPLUNK_USERNAME/SPLUNK_PASSWORD or SPLUNK_TOKEN is required")
        return errors

    def validate_github(self) -> list[str]:
        """Validate GitHub configuration, return list of errors."""
        errors = []
        if not self.github_token or self.github_token == "your-github-token":
            errors.append("GITHUB_TOKEN is required")
        return errors

    def has_source_db(self) -> bool:
        """Check if source database is configured."""
        return bool(
            self.source_db_host
            and self.source_db_name
            and self.source_db_user
            and self.source_db_password
        )

    def has_pgvector(self) -> bool:
        """Check if pgvector embedding store is configured."""
        return bool(
            self.pgvector_host
            and self.pgvector_db_name
            and self.pgvector_db_user
            and self.pgvector_db_password
        )
