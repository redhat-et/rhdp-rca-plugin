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

### Preflight Check (run before first analysis)

```bash
# Create virtual environment and install dependencies (if .venv doesn't exist)
python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt

# Check all prerequisites (use --json for structured output)
.venv/bin/python scripts/cli.py setup --json
```

Review the JSON output. Some settings are required, others are optional:

**Required** (skill will not proceed without these):
- **JOB_LOGS_DIR** — Local directory for job log files

**Recommended** (analysis works without these but functionality is reduced):
- **MLFlow** — Tracing configuration for recording analysis runs

**Optional** (skill runs with reduced functionality when missing):
- **SSH / REMOTE_HOST** not configured: `--fetch` flag won't work (user must provide logs in JOB_LOGS_DIR manually)
- **Splunk** not configured: Steps 2-3 (log correlation) will be skipped
- **GitHub token** not configured: Step 4 (config fetching) will be skipped

#### Interactive Setup for Missing Configs

If any checks have `"status": "missing"` and `"configurable": true`, offer to help the user configure them:

1. List the missing configurable items grouped by check name
2. Ask: "Would you like me to help configure these? I'll walk you through each one."
3. If yes, for each missing check with `"configurable": true`:
   - Show the check name and each `env_vars[].prompt` to explain what's needed
   - If the env var has a `"default"`, mention it (user can press enter to accept)
   - If the env var has `"optional": true`, let the user know they can skip it
   - Ask the user for the value
   - **SSH special handling**: If the SSH check has `"ssh_setup_needed": true`:
     - Ask the user for their SSH host alias name
     - Check if that alias already exists in `~/.ssh/config` — if so, use it as `REMOTE_HOST`
     - If it doesn't exist, ask: do you want to create a new SSH config entry? If yes, ask for: hostname, username, port (default 22), and optional identity file path
     - Read `~/.ssh/config`, append the new `Host` block, and write it back
     - Then set `REMOTE_HOST` to the alias name
4. After collecting all values, read the project's `.claude/settings.json` file
5. Merge the new values into the `"env"` block (create it if it doesn't exist)
6. Write the updated settings file
7. Tell the user to **restart the Claude Code session** for env vars to take effect
8. **Important**: Write secrets (tokens, passwords) to `.claude/settings.json` — ensure this file is in `.gitignore`

If checks show non-configurable errors (e.g., venv issues, rsync not found), provide the fix command instead.

#### MLFlow Server Startup

The `MLFlow server` preflight check automatically handles server connectivity:
- If the server is unreachable and `JUMPBOX_URI` is configured, it starts an SSH tunnel automatically
- If the tunnel is already running, it skips startup
- If the tunnel fails, it reports the error but the skill can still proceed (MLFlow is recommended, not required)

If any **required** checks (JOB_LOGS_DIR) are still missing after the setup flow, do **not** proceed to analysis — tell the user what's still needed. If MLFlow is missing, warn that tracing won't be recorded but proceed. If all required checks pass (recommended/optional items may remain missing), proceed to analysis.

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

**Post-Step4 GitHub MCP Verification**: If step4 output contains `"error": "all_paths_failed"` or any error status (e.g., `"status": "404"`, `"status": "timeout"`, `"status": "500"`) in `paths_tried` arrays, reasoning errors using MCP tools. See [post-step4-verification.md](post-step4-verification.md) for complete verification process.

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
