---
name: root-cause-analysis
description: Perform root cause analysis and log analysis for failed jobs. Use when user wants to investigate job failures, analyze logs, find root causes, debug errors, troubleshoot infrastructure issues, or understand why a job failed. Investigate AAP job failures using Splunk correlation and AgnosticD/AgnosticV configuration analysis. Correlates local Ansible/AAP job logs with Splunk OCP pod logs and retrieves relevant configuration from GitHub repositories.
allowed-tools:
  - Bash
  - Read
  - Write
  - mcp__github__search_code
  - mcp__github__get_file_contents
---

# Root Cause Analysis

Investigate failed jobs by correlating Ansible Automation Platform (AAP) job logs with Splunk OCP pod logs and analyzing AgnosticD/AgnosticV configuration to identify root causes.

## Automatic Execution

When a user asks to analyze a failed job, execute these steps automatically.
The skill's base path is provided when this skill is invoked. Run scripts relative to this folder.

### Setup (run once per session if .venv doesn't exist)

```bash
# Create virtual environment and install dependencies
python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
```

### Step 1-4: Run the analysis CLI

Always use `--fetch` when analyzing by job ID. This automatically downloads the log from the remote server if it's not already present locally, and skips fetching if the log is already there.

```bash
# By job ID (auto-fetches log from remote if not found locally)
.venv/bin/python scripts/cli.py analyze --job-id <JOB_ID> --fetch

# By explicit path (when you already have the log file)
.venv/bin/python scripts/cli.py analyze --job-log <path-to-job-log>
```

The `cli.py analyze` command automatically runs all steps:

- **Step 1**: Parse job log → Extract job ID, GUID, namespace, failed tasks, time window
- **Step 2**: Query Splunk → Fetch pod logs from namespace within job time window
- **Step 3**: Correlate → Merge AAP and Splunk events into unified timeline
- **Step 4**: Fetch GitHub files → Parse job metadata, fetch AgnosticV configs and AgnosticD workload code (requires `GITHUB_TOKEN` to be configured)

**Outputs**: `.analysis/<job-id>/step1_job_context.json`, `step2_splunk_logs.json`, `step3_correlation.json`, `step4_github_fetch_history.json`

This skill automatically searches for job logs in the configured `JOB_LOGS_DIR`.


#### Manual Usage

From this skill's directory:

```bash
# Setup virtual environment (one time)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Analyze by job ID (auto-fetches log if needed, runs steps 1-4)
.venv/bin/python scripts/cli.py analyze --job-id 1234567 --fetch

# Or analyze with explicit path
.venv/bin/python scripts/cli.py analyze --job-log /path/to/job_123.json.gz
```

---

**GitHub MCP Verification**: If step4 output contains `"error": "all_paths_failed"` or any error status (e.g., `"status": "404"`, `"status": "timeout"`, `"status": "500"`) in `paths_tried` arrays, reasoning errors using MCP tools. See [github_mcp_verification.md](github_mcp_verification.md) for complete verification process.

---

## Step 5: Analyze and Generate Summary (Claude's task)

**Input**: Read the following files in order:
1. **REQUIRED**: `step1_job_context.json` - Job metadata and failed task details
2. **REQUIRED**: `step3_correlation.json` - Correlated timeline with relevant pod logs (DO NOT read step2 unless needed)
3. **REQUIRED**: `step4_github_fetch_history.json` - Configuration and code context
4. **CONDITIONAL**: `step2_splunk_logs.json` - Only read if step3 indicates errors needing deeper investigation

**Output**: `.analysis/<job-id>/step5_analysis_summary.json` 

### Analysis Guidelines

**Configuration Analysis**:
- Variable precedence: common.yaml → {platform}/account.yaml → {platform}/{catalog_item}/common.yaml
   → {platform}/{catalog_item}/{env}.yaml (later overrides earlier)
- Check for conflicts, missing variables, secrets references (`includes/secrets`, `!vault`)

**Task Analysis**:
- Task action patterns: `kubernetes.core.k8s_info` (RBAC/resource), `ansible.builtin.uri` (network/auth), `command/shell` (paths/permissions)
- Duration: <30s (immediate failure), 30-300s (short retry), >300s (long retry/timeout)
- Time correlation: Pod error before/during/after task indicates infrastructure vs task-triggered issue
- Conditional execution: Check if `when:` conditions executed incorrectly (variable precedence issue)

### Summary Requirements

1. **Root Cause**: Category (`configuration|infrastructure|workload_bug|credential|resource|dependency`), summary, confidence
2. **Evidence**: Supporting evidence from AAP logs, Splunk logs, and GitHub configs/code
   - **REQUIRED**: When `source` is `agnosticv_config` or `agnosticd_code`, **MUST** include `github_path` in format `owner/repo:path/to/file.yml:line`
   - Extract GitHub paths from step4:
     - **Configs**: From `step4.github_fetches[].fetched_configs.{purpose}.path` → construct as `{config_owner}/{config_repo}:{path}` (e.g., `example-org/config-repo:platform/account.yaml`)
     - **Workloads**: From `step4.github_fetches[].location.parsed` → construct as `{owner}/{repo}:{file_path}:{line_number}` (e.g., `example-org/workload-repo:roles/example-role/tasks/main.yml:42`)
3. **Correlation**: How AAP logs link to Splunk (GUID, namespace, timestamps, pod names)
4. **Recommendations**: Specific file changes with paths, actions, and reasons
   - **Include `github_path`** in recommendations when referencing GitHub files (format: `owner/repo:path/to/file.yml:line`)

**Note**: Job details, failed tasks, and configuration data are available in step1 and step4 files - reference them rather than duplicating in the summary.

### Schema

See `schemas/summary.schema.json` for complete structure. Example:

```json
{
  "job_id": "{job_id}",
  "analyzed_at": "2025-01-15T10:30:45Z",
  "root_cause": {
    "summary": "Brief description of root cause",
    "category": "configuration",
    "confidence": "high"
  },
  "correlation": {
    "method": "namespace_time_match",
    "identifiers": {
      "guid": "{guid}",
      "namespace": "{namespace}",
      "pod_name": "{pod_name}"
    },
    "time_overlap": {
      "aap_job_start": "2025-01-15T10:30:00Z",
      "aap_job_end": "2025-01-15T10:35:00Z",
      "splunk_first_error": "2025-01-15T10:30:15Z",
      "splunk_last_error": "2025-01-15T10:34:45Z",
      "overlap_confirmed": true
    }
  },
  "evidence": [
    {
      "source": "aap_job",
      "timestamp": "2025-01-15T10:30:45Z",
      "message": "'aws_access_key_id' is undefined"
    },
    {
      "source": "agnosticv_config",
      "timestamp": "2025-01-15T10:30:45Z",
      "message": "Missing variable 'aws_access_key_id' in environment config",
      "github_path": "example-org/config-repo:platform/account.yaml"
    },
    {
      "source": "agnosticd_code",
      "timestamp": "2025-01-15T10:30:45Z",
      "message": "Task at line 42 uses undefined variable",
      "github_path": "example-org/workload-repo:roles/example-role/tasks/main.yml:42"
    }
  ],
  "recommendations": [
    {
      "priority": "high",
      "action": "Add missing variable",
      "file": "platform/account.yaml",
      "github_path": "example-org/config-repo:platform/account.yaml",
      "github_url": "https://github.com/example-org/config-repo/blob/main/platform/account.yaml",
      "change": "Add aws_access_key_id variable",
      "details": "Variable is referenced but not defined"
    }
  ],
  "contributing_factors": ["Missing variable definition", "Incomplete configuration"]
}
```

---

## Files

| Step | File | Author |
|------|------|--------|
| 1 | `step1_job_context.json` | Python |
| 2 | `step2_splunk_logs.json` | Python |
| 3 | `step3_correlation.json` | Python |
| 4 | `step4_github_fetch_history.json` | Python (Optional Claude updates for MCP verification) |
| 5 | `step5_analysis_summary.json` | Claude |

All files in `.analysis/<job-id>/`
