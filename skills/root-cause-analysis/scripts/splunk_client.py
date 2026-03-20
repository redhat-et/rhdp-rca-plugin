"""Splunk API client for log queries."""

import base64
import json
import ssl
import time
import urllib.parse
import urllib.request
from typing import Any

from .config import Config


def get_auth_header(config: Config) -> dict[str, str]:
    """Get authorization header based on auth method."""
    if config.splunk.auth_method == "basic":
        credentials = f"{config.splunk.username}:{config.splunk.password}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return {"Authorization": f"Basic {encoded}"}
    elif config.splunk.auth_method == "token":
        return {"Authorization": f"Bearer {config.splunk.token}"}
    else:
        raise ValueError("No authentication configured for Splunk")


def splunk_request(
    config: Config,
    endpoint: str,
    method: str = "GET",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make a request to Splunk API."""
    url = f"{config.splunk.host}/services{endpoint}"

    headers = get_auth_header(config)
    headers["Content-Type"] = "application/x-www-form-urlencoded"

    if data:
        data["output_mode"] = "json"
        encoded_data = urllib.parse.urlencode(data).encode("utf-8")
    else:
        encoded_data = urllib.parse.urlencode({"output_mode": "json"}).encode("utf-8")
        if method == "GET":
            url = f"{url}?output_mode=json"
            encoded_data = None

    req = urllib.request.Request(url, data=encoded_data, headers=headers, method=method)

    # Handle SSL verification
    ctx = ssl.create_default_context()
    if not config.splunk.verify_ssl:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    with urllib.request.urlopen(req, context=ctx, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def create_search_job(
    config: Config,
    search_query: str,
    earliest: str = "-24h",
    latest: str = "now",
) -> str:
    """Create a Splunk search job and return the job SID."""
    # Ensure query starts with 'search' command
    if not search_query.strip().startswith("search ") and not search_query.strip().startswith("|"):
        search_query = f"search {search_query}"

    data = {
        "search": search_query,
        "earliest_time": earliest,
        "latest_time": latest,
    }

    result = splunk_request(config, "/search/jobs", method="POST", data=data)
    return result.get("sid", "")


def wait_for_job(config: Config, sid: str, timeout: int = 300) -> dict[str, Any]:
    """Wait for a Splunk search job to complete. Returns job info."""
    start = time.time()

    while time.time() - start < timeout:
        result = splunk_request(config, f"/search/jobs/{sid}")

        if "entry" in result and result["entry"]:
            content = result["entry"][0].get("content", {})
            state = content.get("dispatchState", "")

            if state == "DONE":
                return {
                    "status": "done",
                    "result_count": content.get("resultCount", 0),
                    "scan_count": content.get("scanCount", 0),
                }
            elif state == "FAILED":
                return {"status": "failed", "error": content.get("messages", [])}

        time.sleep(2)

    return {"status": "timeout"}


def get_search_results(config: Config, sid: str, count: int = 1000) -> list[dict[str, Any]]:
    """Get results from a completed Splunk search job."""
    url = f"{config.splunk.host}/services/search/jobs/{sid}/results?output_mode=json&count={count}"

    headers = get_auth_header(config)

    req = urllib.request.Request(url, headers=headers, method="GET")

    ctx = ssl.create_default_context()
    if not config.splunk.verify_ssl:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    with urllib.request.urlopen(req, context=ctx, timeout=120) as response:
        result = json.loads(response.read().decode("utf-8"))

    return result.get("results", [])


class SplunkClient:
    """High-level Splunk client for log queries."""

    def __init__(self, config: Config):
        self.config = config

    def query(
        self,
        query: str,
        earliest: str = "-24h",
        latest: str = "now",
        max_results: int = 100,
    ) -> list[dict[str, Any]]:
        """Execute a Splunk query and return results."""
        sid = create_search_job(self.config, query, earliest, latest)
        job_result = wait_for_job(self.config, sid)

        if job_result["status"] != "done":
            raise RuntimeError(f"Search failed: {job_result}")

        return get_search_results(self.config, sid, count=max_results)

    def query_ocp_namespace(
        self,
        namespace: str,
        earliest: str = "-24h",
        latest: str = "now",
        errors_only: bool = False,
        max_results: int = 200,
    ) -> list[dict[str, Any]]:
        """Query OCP app logs for a specific namespace."""
        query = f'index={self.config.splunk.ocp_app_index} kubernetes.namespace_name="{namespace}"'
        if errors_only:
            query += " (error OR failed OR fatal OR exception OR FAILED OR ERROR)"
        return self.query(query, earliest, latest, max_results)

    def query_by_guid(
        self,
        guid: str,
        earliest: str = "-24h",
        latest: str = "now",
        index: str | None = None,
        max_results: int = 200,
    ) -> list[dict[str, Any]]:
        """Query logs by GUID across indices."""
        if index is None:
            index = self.config.splunk.ocp_app_index
        query = f'index={index} "{guid}"'
        return self.query(query, earliest, latest, max_results)
