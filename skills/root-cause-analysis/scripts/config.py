"""Configuration management for splunk-log-analysis."""

import os
from dataclasses import dataclass
from pathlib import Path

import mlflow
from dotenv import load_dotenv
from mlflow.entities import SpanType


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

    @classmethod
    @mlflow.trace(name="Load config from environment", span_type=SpanType.RETRIEVER)
    def from_env(cls, base_dir: Path | None = None) -> "Config":
        """Load configuration from environment variables."""
        if base_dir is None:
            base_dir = Path(__file__).parent.parent

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

        return cls(
            splunk=splunk,
            analysis_dir=analysis_dir,
            job_logs_dir=job_logs_dir,
            github_token=github_token,
            remote_host=remote_host,
            remote_log_dir=remote_log_dir,
        )

    @mlflow.trace(name="Find job log file", span_type=SpanType.RETRIEVER)
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

    @mlflow.trace(name="Validate Splunk config", span_type=SpanType.PARSER)
    def validate_splunk(self) -> list[str]:
        """Validate Splunk configuration, return list of errors."""
        errors = []
        if not self.splunk.host:
            errors.append("SPLUNK_HOST is required")
        if self.splunk.auth_method == "none":
            errors.append("SPLUNK_USERNAME/SPLUNK_PASSWORD or SPLUNK_TOKEN is required")
        return errors

    @mlflow.trace(name="Validate GitHub config", span_type=SpanType.PARSER)
    def validate_github(self) -> list[str]:
        """Validate GitHub configuration, return list of errors."""
        errors = []
        if not self.github_token or self.github_token == "your-github-token":
            errors.append("GITHUB_TOKEN is required")
        return errors
