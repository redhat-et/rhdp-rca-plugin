"""Classify error messages against known failure patterns.

Loads a curated YAML file of regex-based error patterns and matches them
against error messages from RCA steps 1 and 3. The YAML file can be
provided via URL, local file path, or CLI flags.

Configuration (in .claude/settings.local.json env block):
  KNOWN_FAILED_YAML_URL — URL to fetch the YAML file (cached locally)
  KNOWN_FAILED_YAML     — local file path (fallback)
"""

import os
import re
import tempfile
from pathlib import Path

import yaml

# Cache dir for downloaded known_failed.yaml
_CACHE_DIR = Path(tempfile.gettempdir()) / "rhdp-rca"
_CACHE_FILE = _CACHE_DIR / "known_failed.yaml"


def fetch_known_failures_from_url(url: str) -> list[dict]:
    """Fetch known failure patterns YAML from a URL.

    Caches the file locally. Returns the parsed failures list.
    """
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    import requests

    headers = {}
    github_token = os.environ.get("GITHUB_TOKEN", "")
    if github_token and "api.github.com" in url:
        headers["Authorization"] = f"token {github_token}"
        headers["Accept"] = "application/vnd.github.v3.raw"

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        _CACHE_FILE.write_text(resp.text)
        return _parse_yaml_content(resp.text)
    except (requests.RequestException, yaml.YAMLError) as e:
        # Fall back to cache if fetch fails
        if _CACHE_FILE.exists():
            return load_known_failures(_CACHE_FILE)
        print(f"  Warning: Failed to fetch known failure patterns: {e}")
        return []


def load_known_failures(yaml_path: str | Path) -> list[dict]:
    """Load known failure patterns from a local YAML file."""
    path = Path(yaml_path)
    if not path.exists():
        return []
    try:
        with open(path) as f:
            return _parse_yaml_content(f.read())
    except (yaml.YAMLError, OSError):
        return []


def _parse_yaml_content(content: str) -> list[dict]:
    """Parse YAML content and extract the failures list."""
    data = yaml.safe_load(content)
    if not data:
        return []
    return data.get("failures", [])


def classify_error(error_message: str, known_failures: list[dict]) -> dict | None:
    """Match an error message against known failure patterns.

    Returns a dict with classification info on match, or None.
    """
    if not error_message or not known_failures:
        return None

    error_message = error_message.strip()

    for failure in known_failures:
        pattern = failure.get("error_string", "")
        if not pattern:
            continue
        try:
            if re.search(pattern, error_message, re.IGNORECASE | re.DOTALL):
                return {
                    "error_category": failure.get("category", "general_failure"),
                    "matched_pattern": pattern,
                    "failure_description": failure.get("description", ""),
                }
        except re.error:
            continue

    return None


def classify_job_errors(
    job_context: dict, correlation: dict, known_failures: list[dict]
) -> list[dict]:
    """Classify all error messages found in step1 and step3 outputs.

    Returns a list of classification results (one per matched error).
    """
    results: list[dict] = []
    seen_messages: set[str] = set()

    # Collect error messages from step1 failed tasks
    for task in job_context.get("failed_tasks", []):
        msg = task.get("error_message", "")
        if msg and msg not in seen_messages:
            seen_messages.add(msg)
            match = classify_error(msg, known_failures)
            if match:
                match["source"] = "aap_failed_task"
                match["task"] = task.get("task", "")
                results.append(match)

    # Collect error messages from step3 timeline events
    # Timeline events store messages in details.message (for aap_job) or
    # details.message (for splunk_ocp), not at the top level.
    for event in correlation.get("timeline_events", []):
        details = event.get("details", {})
        msg = details.get("message", "") or details.get("error_message", "")
        if msg and msg not in seen_messages:
            seen_messages.add(msg)
            match = classify_error(msg, known_failures)
            if match:
                match["source"] = "correlation_timeline"
                results.append(match)

    return results


def resolve_known_failures(url: str | None = None, local_path: str | None = None) -> list[dict]:
    """Resolve and load known failure patterns.

    Args:
        url: URL to fetch YAML from (overrides env var)
        local_path: Local file path (overrides env var)

    Priority:
    1. Explicit url/local_path arguments (from CLI flags)
    2. KNOWN_FAILED_YAML_URL env var — fetch from URL (cached locally)
    3. KNOWN_FAILED_YAML env var — read from local file path
    4. Returns empty list if none configured
    """
    # CLI flag: URL
    if url:
        return fetch_known_failures_from_url(url)

    # CLI flag: local path
    if local_path:
        return load_known_failures(local_path)

    # Env var: URL
    env_url = os.environ.get("KNOWN_FAILED_YAML_URL", "")
    if env_url:
        return fetch_known_failures_from_url(env_url)

    # Env var: local path
    env_path = os.environ.get("KNOWN_FAILED_YAML", "")
    if env_path:
        return load_known_failures(env_path)

    return []
