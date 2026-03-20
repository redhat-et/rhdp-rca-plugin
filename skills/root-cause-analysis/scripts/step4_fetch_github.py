#!/usr/bin/env python3
"""
Step 4: Fetch GitHub Data - Parse paths and fetch all relevant files

Fetches all relevant GitHub configuration and workload files.
Real analysis happens in Step 5 when Claude reads and interprets the files.

Usage:
    python -m scripts.step4_fetch_github --job-id <JOB_ID>
"""

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import mlflow
import requests
from mlflow.entities import SpanType


@mlflow.trace(name="Create error result", span_type=SpanType.TOOL)
def create_error_result(path: str, status: str = "404") -> dict[str, Any]:
    """Create standardized error result dictionary."""
    return {"error": "all_paths_failed", "paths_tried": [{"path": path, "status": status}]}


class GitHubClient:
    """GitHub API client for fetching files"""

    def __init__(self, token: str):
        self.token = token
        self.base_url = "https://api.github.com"
        self.headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }

    @mlflow.trace(name="Get GitHub file content", span_type=SpanType.RETRIEVER)
    def get_file_content(self, owner: str, repo: str, path: str) -> dict:
        """Fetch file content from GitHub API"""
        url = f"{self.base_url}/repos/{owner}/{repo}/contents/{path}"
        try:
            response = requests.get(url, headers=self.headers, timeout=30)
            if response.status_code == 200:
                data = response.json()
                content = base64.b64decode(data["content"]).decode("utf-8")
                return {"path": path, "content": content, "sha": data["sha"], "size": data["size"]}
            elif response.status_code == 404:
                return create_error_result(path, status="404")
            else:
                print(f"[WARNING] GitHub API error for {path}: {response.status_code}")
                return create_error_result(path, status=str(response.status_code))
        except requests.exceptions.Timeout:
            print(f"[ERROR] Timeout fetching {path}")
            return create_error_result(path, status="timeout")
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] Request failed for {path}: {e}")
            return create_error_result(path, status="request_error")
        except Exception as e:
            print(f"[ERROR] Failed to fetch {path}: {e}")
            return create_error_result(path, status="unknown_error")

    @mlflow.trace(name="Search GitHub file", span_type=SpanType.RETRIEVER)
    def search_file(self, owner: str, repo: str, query: str) -> dict | None:
        """Search for file using GitHub code search API"""
        url = f"{self.base_url}/search/code?q=repo:{owner}/{repo} {query} in:path"
        try:
            response = requests.get(url, headers=self.headers, timeout=30)
            if response.status_code == 200:
                result = response.json()
                return result
            elif response.status_code == 403:
                print("[WARNING] Search rate limited")
                return None
            else:
                print(f"[WARNING] Search failed: {response.status_code}")
                return None
        except Exception as e:
            print(f"[ERROR] Search failed: {e}")
            return None


# Step 4a: Parse GitHub Paths Functions
@mlflow.trace(name="Parse job name", span_type=SpanType.PARSER)
def parse_job_name(job_name: str, guid: str) -> dict[str, Any]:
    """Parse RHPDS job name using GUID as anchor."""
    warnings = []
    name = job_name.removeprefix("RHPDS ").strip()

    if " " in name:
        name, uuid_suffix = name.split(maxsplit=1)
        warnings.append(f"Removed UUID suffix: {uuid_suffix}")

    guid_pattern = f"-{guid}-"
    if guid_pattern not in name:
        warnings.append(f"GUID '{guid}' not found in job_name")
        return {k: "" for k in ["platform", "catalog_item", "env", "action"]} | {
            "guid": guid,
            "warnings": warnings,
        }

    before_guid, after_guid = name.split(guid_pattern, 1)

    # Parse platform.catalog.env before the guid
    parts = before_guid.split(".")
    if len(parts) >= 3:
        platform, env, catalog = parts[0], parts[-1], ".".join(parts[1:-1])
    elif len(parts) == 2:
        platform, catalog, env = parts[0], parts[1], ""
        warnings.append("No env in job_name")
    else:
        platform, catalog, env = parts[0] if parts else "", "", ""
        warnings.append("Could not parse platform.catalog.env")

    # Parse action after the guid
    action = ""
    for word in ["provision", "destroy", "stop", "start", "status"]:
        if after_guid.startswith(word):
            action = word
            if len(after_guid) > len(word):
                warnings.append(f"Stripped action suffix: {after_guid[len(word) :]}")
            break

    if not action:
        warnings.append(f"Could not identify action from: {after_guid}")
        action = after_guid

    return {
        "platform": platform,
        "catalog_item": catalog,
        "env": env,
        "guid": guid,
        "action": action,
        "warnings": warnings,
    }


@mlflow.trace(name="Parse task path", span_type=SpanType.PARSER)
def parse_task_path(task_path: str) -> dict[str, Any]:
    """Parse task_path to extract repository and file location."""
    # Collections pattern
    if match := re.match(
        r"/home/runner/\.ansible/collections/ansible_collections/([^/]+)/([^/]+)/(.+):(\d+)",
        task_path,
    ):
        return {
            "owner": match[1],
            "repo": match[2],
            "file_path": match[3],
            "line_number": int(match[4]),
            "repos_to_try": [(match[1], match[2])],  # Single known repo
        }

    # Project pattern
    if match := re.match(r"/runner/project/(.+):(\d+)", task_path):
        return {
            "owner": "redhat-cop",
            "repo": "agnosticd",
            "file_path": match[1],
            "line_number": int(match[2]),
            "repos_to_try": [("redhat-cop", "agnosticd"), ("agnosticd", "agnosticd-v2")],
        }

    # Unknown pattern
    parts = task_path.rsplit(":", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return {
            "owner": "",
            "repo": "",
            "file_path": parts[0],
            "line_number": int(parts[1]),
            "repos_to_try": [],
        }

    return {
        "owner": "",
        "repo": "",
        "file_path": task_path,
        "line_number": 0,
        "repos_to_try": [],
    }


class Step4Analyzer:
    """Step 4: Parse paths, fetch files, analyze, generate root cause"""

    def __init__(self, job_id: str, analysis_dir: Path, github_client: GitHubClient):
        self.job_id = job_id
        self.analysis_dir = analysis_dir
        self.github = github_client

    @mlflow.trace(name="Load Step 1 context", span_type=SpanType.RETRIEVER)
    def load_step1(self) -> dict:
        """Load Step 1 job context"""
        step1_file = self.analysis_dir / "step1_job_context.json"
        if not step1_file.exists():
            raise FileNotFoundError(f"Step 1 output not found: {step1_file}")

        with open(step1_file) as f:
            return json.load(f)

    @mlflow.trace(name="Parse failed tasks", span_type=SpanType.PARSER)
    def parse_failed_tasks(self, job_context: dict) -> dict:
        """Step 4a: Parse failed tasks and extract file paths"""
        print("[INFO] Step 4a: Parsing failed tasks...")

        job_id = job_context.get("job_id", "")
        failed_tasks = job_context.get("failed_tasks", [])
        enriched_tasks = []

        for task in failed_tasks:
            task_path = task.get("task_path", "")
            location = parse_task_path(task_path)

            # Build workload_paths inline - only fetch failed_task_code
            workload_paths = [
                {
                    "file_path": location.get("file_path", ""),
                    "repos_to_try": location.get("repos_to_try", []),
                    "purpose": "failed_task_code",
                    "line_context": {
                        "target_line": location.get("line_number", 0),
                        "context_before": 10,
                        "context_after": 10,
                    },
                }
            ]

            enriched_tasks.append(
                {
                    "task_name": task.get("task", ""),
                    "play": task.get("play", ""),
                    "role": task.get("role", ""),
                    "task_action": task.get("task_action", ""),
                    "error_message": task.get("error_message", ""),
                    "duration": task.get("duration", 0),
                    "timestamp": task.get("timestamp", ""),
                    "location": {"original_path": task_path, "parsed": location},
                    "investigation_targets": {
                        "workload_code": workload_paths,
                    },
                }
            )

        return {
            "job_id": job_id,
            "failed_tasks": enriched_tasks,
        }

    @mlflow.trace(name="Fetch AgnosticV configs", span_type=SpanType.RETRIEVER)
    def fetch_configs(self, platform: str, catalog: str, env: str) -> dict:
        """Step 4b: Fetch configs with backward discovery"""
        print("[INFO] Step 4b: Fetching AgnosticV configurations...")

        start_path = f"{platform}/{catalog}/{env}.yaml"

        fetched_configs = {}
        actual_platform = platform
        actual_catalog = catalog

        # Discover actual names from env.yaml (most specific)
        print("  [4] Fetching env_overrides...")
        result = self.github.get_file_content("rhpds", "agnosticv", start_path)

        if "content" not in result:
            # Search to discover actual names
            print(f"    Direct path failed, searching for: {start_path}")
            filename = f"{env}.yaml"
            keywords = f"{platform} {catalog}".replace("-", " ").replace("_", " ")

            search = self.github.search_file(
                "rhpds", "agnosticv", f"filename:{filename} {keywords}"
            )

            if search and search.get("total_count", 0) > 0:
                found_path = search["items"][0]["path"]
                print(f"    Found via search: {found_path}")
                result = self.github.get_file_content("rhpds", "agnosticv", found_path)

                # Extract actual names
                if "content" in result:
                    parts = found_path.split("/")
                    actual_platform = parts[0]
                    actual_catalog = parts[1]

        fetched_configs["env_overrides"] = result

        # Fetch remaining configs using discovered names
        print(f"    Using discovered names: platform={actual_platform}, catalog={actual_catalog}")

        print("  [3] Fetching catalog_common...")
        catalog_common_path = f"{actual_platform}/{actual_catalog}/common.yaml"
        fetched_configs["catalog_common"] = self.github.get_file_content(
            "rhpds", "agnosticv", catalog_common_path
        )

        print("  [2] Fetching platform_config...")
        platform_config_path = f"{actual_platform}/account.yaml"
        fetched_configs["platform_config"] = self.github.get_file_content(
            "rhpds", "agnosticv", platform_config_path
        )

        print("  [1] Fetching base_defaults...")
        base_defaults_path = "common.yaml"
        fetched_configs["base_defaults"] = self.github.get_file_content(
            "rhpds", "agnosticv", base_defaults_path
        )

        return fetched_configs

    @mlflow.trace(name="Fetch AgnosticD workload code", span_type=SpanType.RETRIEVER)
    def fetch_workload_code(self, investigation_targets: dict) -> dict:
        """Step 4c: Fetch all AgnosticD workload code"""
        print("[INFO] Step 4c: Fetching AgnosticD workload code...")

        fetched_workload = {}
        workload_files = investigation_targets.get("workload_code", [])

        for workload in workload_files:
            purpose = workload.get("purpose")
            file_path = workload.get("file_path")
            repos_to_try = workload.get("repos_to_try", [])

            print(f"  Fetching {purpose}...")

            result = None

            # Step 1: Try direct fetch in all repos
            for owner, repo in repos_to_try:
                result = self.github.get_file_content(owner, repo, file_path)
                if "content" in result:
                    print(f"    Successfully fetched from {owner}/{repo}")
                    break

            # Step 2: If not found, search across all repos
            if "content" not in result and repos_to_try:
                print("    Direct fetch failed, searching...")
                search_query = f"{file_path} in:path"

                for owner, repo in repos_to_try:
                    search = self.github.search_file(owner, repo, search_query)

                    if search and search.get("total_count", 0) > 0:
                        found_path = search["items"][0]["path"]
                        print(f"    Found via search in {owner}/{repo}: {found_path}")
                        result = self.github.get_file_content(owner, repo, found_path)

                        if "content" in result:
                            break

            fetched_workload[purpose] = result

        return fetched_workload

    @mlflow.trace(name="Run Step 4 analysis", span_type=SpanType.CHAIN)
    def run(self) -> dict:
        """Execute full Step 4 - fetch all GitHub files"""
        print(f"[INFO] Starting Step 4 analysis for job {self.job_id}...")

        # Load Step 1 context
        job_context = self.load_step1()

        # Parse job metadata once
        job_name = job_context.get("job_name", "")
        guid = job_context.get("guid", "")
        metadata = parse_job_name(job_name, guid)
        warnings = metadata.get("warnings", [])

        # Step 4a: Parse failed tasks (only parses task paths, not metadata)
        github_paths = self.parse_failed_tasks(job_context)

        # Step 4b: Fetch configs once. This ensures configs are fetched even if there are no failed tasks
        platform = metadata.get("platform", "")
        catalog = metadata.get("catalog_item", "")
        env = metadata.get("env", "")

        fetched_configs = {}
        if platform and catalog and env:
            fetched_configs = self.fetch_configs(platform, catalog, env)
        else:
            print("[WARNING] Missing platform/catalog/env metadata - skipping config fetch")

        # Step 4c: For each failed task, fetch workload code
        github_fetches = []

        for task in github_paths.get("failed_tasks", []):
            investigation_targets = task.get("investigation_targets", {})

            # Fetch workload code (task-specific)
            fetched_workload = self.fetch_workload_code(investigation_targets)

            # Keep minimal fields: task_path for matching to step1, task name for readability, location and fetched files
            github_fetch = {
                "task_path": task.get("location", {}).get("original_path", ""),
                "task": task.get("task_name", ""),
                "location": task.get("location"),
                "fetched_configs": fetched_configs,
                "fetched_workload": fetched_workload,
            }

            github_fetches.append(github_fetch)

        # Combine into final output
        result = {
            "job_id": self.job_id,
            "job_name": job_name,
            "parsing_status": "success" if not warnings else "success_with_warnings",
            "warnings": warnings,
            "job_metadata": metadata,
            "github_fetches": github_fetches,
            # Include configs at top level even if no failed tasks
            "fetched_configs": fetched_configs,
        }

        return result


@mlflow.trace(name="Step 4 main", span_type=SpanType.CHAIN)
def main():
    parser = argparse.ArgumentParser(
        description="Step 4: Fetch all relevant GitHub configuration and workload files"
    )
    parser.add_argument("--job-id", required=True, help="Job ID to analyze")
    args = parser.parse_args()

    # Get skill directory
    skill_dir = Path(__file__).parent.parent

    # Analysis files are in this skill's .analysis directory
    analysis_dir = skill_dir / ".analysis" / args.job_id
    if not analysis_dir.parent.exists():
        analysis_dir.parent.mkdir(parents=True, exist_ok=True)

    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token or github_token == "your-github-token":
        print("[ERROR] GITHUB_TOKEN not found in environment variables")
        print("Get token from: https://github.com/settings/tokens")
        sys.exit(1)

    # Initialize clients
    github_client = GitHubClient(github_token)

    # Run analysis (using this skill's .analysis directory)
    analyzer = Step4Analyzer(args.job_id, analysis_dir, github_client)
    result = analyzer.run()

    # Save output (for standalone usage)
    output_file = analysis_dir / "step4_github_fetch_history.json"
    with open(output_file, "w") as f:
        json.dump(result, f, indent=2)

    print(f"[SUCCESS] Analysis complete: {output_file}")


if __name__ == "__main__":
    main()
