"""Classify error messages against known failure patterns.

Loads a curated YAML file of regex-based error patterns and matches them
against error messages from RCA steps 1 and 3. The pattern source is fully
optional and can be provided three ways, none of which require a GitHub token:

  * ``--known-failures-file`` / ``KNOWN_FAILED_YAML`` — a local file path
  * ``--known-failures-url`` / ``KNOWN_FAILED_YAML_URL`` — any HTTP(S) URL,
    including a plain raw URL fetched over ``curl``/HTTP with no credentials
    (e.g. https://raw.githubusercontent.com/<owner>/<repo>/<branch>/known_failed.yaml)

A ``GITHUB_TOKEN`` is only used to authenticate private ``api.github.com``
repositories; when it is absent the request is still made. When no source is
configured at all, classification is skipped gracefully (zero patterns loaded).
"""

import base64
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import yaml

# Cache dir for downloaded known_failed.yaml files. Each source URL gets its
# own cache file (keyed by a hash of the URL) so a failed fetch for one URL
# never reuses patterns cached from a different URL.
_CACHE_DIR = Path(tempfile.gettempdir()) / "rhdp-rca"


def _cache_path_for(url: str) -> Path:
    """Return a per-URL cache file path so caches never cross sources."""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return _CACHE_DIR / f"known_failed_{digest}.yaml"


def fetch_known_failures_from_url(url: str) -> list[dict]:
    """Fetch known failure patterns YAML from a URL.

    Works with a plain raw URL (e.g. raw.githubusercontent.com) fetched over
    HTTP with no credentials, as well as ``api.github.com/.../contents`` URLs.
    A ``GITHUB_TOKEN`` is optional and only used to authenticate private
    repositories; when it is absent the request is still made and the raw
    ``Accept`` header is sent so GitHub returns file content rather than JSON
    metadata.

    Caches the file locally, keyed by URL so a failed fetch never falls back to
    patterns cached from a different source. Returns the parsed failures list.
    """
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _cache_path_for(url)

    import requests

    headers = {}
    is_github_api = "api.github.com" in url
    if is_github_api:
        # Request raw content even without a token; unauthenticated
        # api.github.com/contents requests otherwise return JSON metadata.
        headers["Accept"] = "application/vnd.github.v3.raw"
    github_token = os.environ.get("GITHUB_TOKEN", "")
    if github_token and is_github_api:
        headers["Authorization"] = f"token {github_token}"

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        content = _extract_yaml_text(resp.text)
        cache_file.write_text(content)
        return _parse_yaml_content(content)
    except (requests.RequestException, yaml.YAMLError) as e:
        # Fall back only to this URL's own cache, never another source's.
        if cache_file.exists():
            return load_known_failures(cache_file)
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


def _extract_yaml_text(text: str) -> str:
    """Return raw YAML text, decoding GitHub JSON metadata if necessary.

    A plain raw URL returns YAML directly. If an ``api.github.com/contents``
    request is served as JSON metadata (e.g. the raw ``Accept`` header was
    ignored on an unauthenticated request), decode the base64 ``content`` field
    so the caller always receives YAML instead of silently parsing zero
    patterns from the metadata envelope.
    """
    if not text.lstrip().startswith("{"):
        return text
    try:
        meta = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text
    if isinstance(meta, dict) and meta.get("encoding") == "base64" and "content" in meta:
        try:
            return base64.b64decode(meta["content"]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return text
    return text


def _parse_yaml_content(content: str) -> list[dict]:
    """Parse YAML content and extract the failures list."""
    data = yaml.safe_load(content)
    if not isinstance(data, dict):
        return []
    failures = data.get("failures", [])
    if not isinstance(failures, list):
        return []
    return [f for f in failures if isinstance(f, dict)]


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

    # Collect error messages from step3 timeline events. build_correlation_timeline()
    # stores the log text under details.message (splunk_ocp / pod & Splunk logs) or
    # details.error_message (aap_job). Fall back to a top-level message key so text
    # that only surfaces in pod/Splunk logs is still classified.
    for event in correlation.get("timeline_events", []):
        details = event.get("details", {})
        msg = (
            details.get("message", "")
            or details.get("error_message", "")
            or event.get("message", "")
        )
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
