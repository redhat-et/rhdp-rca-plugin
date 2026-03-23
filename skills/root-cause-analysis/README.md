# Root Cause Analysis Skill

Investigate failed jobs by correlating Ansible Automation Platform (AAP) job logs with Splunk OCP pod logs and analyzing AgnosticD/AgnosticV configuration to identify root causes.

Use this skill when you need to:
- Investigate job failures
- Analyze logs for errors
- Find root causes of infrastructure issues
- Debug failed deployments
- Troubleshoot Kubernetes/OpenShift problems
- Analyze AgnosticD/AgnosticV configuration issues

## Overview

This skill uses automated Python scripts for data collection (Steps 1-4) and Claude for analysis (Step 5):

1. **Steps 1-4**: Parse job logs, query Splunk, build correlation timeline, fetch GitHub files (all automated via `cli.py analyze`)
2. **Step 5**: Analyze root causes and generate recommendations (Claude)

## Setup

### 1. Create virtual environment and install dependencies

```bash
cd root-cause-analysis
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2. Configure environment

Add the following environment variables to your Claude Code settings file:
- **Project-level**: `.claude/settings.local.json` in your project root
- **Or global**: `~/.claude/settings.json`

```json
{
  "env": {
    "JOB_LOGS_DIR": "/path/to/your/extracted_logs",
    "REMOTE_HOST": "your-ssh-host-alias",
    "REMOTE_DIR": "/path/to/remote/log/directory",
    "GITHUB_TOKEN": "your-github-token",
    "SPLUNK_HOST": "your-splunk-host",
    "SPLUNK_USERNAME": "your-username",
    "SPLUNK_PASSWORD": "your-password",
    "SPLUNK_INDEX": "your_splunk_index",
    "SPLUNK_OCP_APP_INDEX": "your_ocp_app_index",
    "SPLUNK_OCP_INFRA_INDEX": "your_ocp_infra_index",
    "SPLUNK_VERIFY_SSL": "false"
  }
}
```

Update the values:
- `JOB_LOGS_DIR` - Directory containing job log files (`job_<ID>.json.gz`, etc.)
- `REMOTE_HOST` - SSH host alias for `--fetch` (must be configured in `~/.ssh/config`)
- `REMOTE_DIR` - Remote directory containing log files on the SSH host
- `GITHUB_TOKEN` - GitHub personal access token for fetching files via GitHub API
- `SPLUNK_HOST` - Your Splunk REST API endpoint
- `SPLUNK_USERNAME` / `SPLUNK_PASSWORD` - Your Splunk credentials
- `SPLUNK_INDEX` - Default index for AAP logs
- `SPLUNK_OCP_APP_INDEX` / `SPLUNK_OCP_INFRA_INDEX` - OCP log indices

### 3. Configure SSH for auto-fetch (optional)

To use the `--fetch` flag for automatic log retrieval, set up SSH access to the remote log server:

1. Add a host entry to `~/.ssh/config`:
   ```
   Host your-ssh-host-alias
       HostName remote-server.example.com
       User your-username
       IdentityFile ~/.ssh/your-key
   ```

2. Verify SSH connectivity:
   ```bash
   ssh your-ssh-host-alias "ls /path/to/remote/log/directory"
   ```

3. Ensure `rsync` is installed locally and on the remote server.

4. Set `REMOTE_HOST` and `REMOTE_DIR` in your settings (see step 2 above).

### 4. Configure GitHub MCP Server (for 404 verification)

This skill uses GitHub MCP tools only for verifying 404 errors. Add the GitHub MCP server using the Claude CLI:

```bash
claude mcp add github -s project -e GITHUB_PERSONAL_ACCESS_TOKEN=$GITHUB_TOKEN -- npx -y @modelcontextprotocol/server-github
```

This creates `.claude/mcp.json` in your project root:

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"
      }
    }
  }
}
```

> **Note**: `GITHUB_TOKEN` must already be set in your environment variables (e.g., via `.claude/settings.local.json` `env` block — see step 2) or exported in your shell environment. Restart Claude Code after adding the MCP server.

Required GitHub MCP tools:
- `mcp__github__get_file_contents` — Check parent directories and verify file existence
- `mcp__github__search_code` — Locate files when paths fail

## Usage

### Complete Analysis Workflow

```bash
# Run all steps (1-4) automatically - requires GITHUB_TOKEN
.venv/bin/python scripts/cli.py analyze --job-id 1234567

# Step 5: Claude analyzes the data and generates summary (automatic when skill is invoked)
```

**Note**: The `cli.py analyze` command runs all steps (1-4) automatically. The GitHub fetcher can also be run separately if needed:
```bash
# Run GitHub fetcher separately (standalone execution)
.venv/bin/python scripts/github_fetcher.py --job-id 1234567
```

### Analyze by Job ID

If `JOB_LOGS_DIR` is configured, you can analyze by job ID:

```bash
.venv/bin/python scripts/cli.py analyze --job-id 1234567
```

The skill will automatically find the log file matching `job_1234567.*` in `JOB_LOGS_DIR`.

### Auto-fetch and Analyze

Use `--fetch` to automatically download the job log from the remote server if it's not found locally:

```bash
.venv/bin/python scripts/cli.py analyze --job-id 1234567 --fetch
```

This requires `REMOTE_HOST`, `REMOTE_DIR`, and `JOB_LOGS_DIR` to be configured. The log is fetched via SSH + rsync and then analyzed normally.

### Analyze by File Path

Alternatively, specify the log file directly:

```bash
.venv/bin/python scripts/cli.py analyze --job-log /path/to/job_1234567.json.gz
```

### Other Commands

```bash
# Parse job log only (Step 1)
.venv/bin/python scripts/cli.py parse --job-log /path/to/job.json.gz

# Run ad-hoc Splunk query
.venv/bin/python scripts/cli.py query 'index=$SPLUNK_OCP_APP_INDEX "x1234"' --earliest=-24h

# Check analysis status for a job
.venv/bin/python scripts/cli.py status 1234567
```

## How It Works

### Steps 1-4: Automated Analysis (Python Scripts)

All steps are executed automatically by the `cli.py analyze` command:

**Step 1: Parse Job Log**
- Extract identifiers (GUID, namespace, time window, failed tasks)
- Parse job metadata (platform, catalog_item, environment)
- Output: `step1_job_context.json`

**Step 2: Query Splunk**
- Fetch OCP pod logs matching the namespace/GUID during job execution
- Filter for errors, failures, exceptions
- Output: `step2_splunk_logs.json`

**Step 3: Build Correlation Timeline**
- Merge AAP and Splunk events into a unified timeline
- Identify causal chains (AAP task → pod event → error)
- Output: `step3_correlation.json`

**Step 4: Fetch GitHub Files**
- Parses job metadata to identify platform/catalog/environment
- Fetches AgnosticV configuration hierarchy.
- Fetches AgnosticD workload code:
  - Role defaults (`roles/{role}/defaults/main.yml`)
  - Task files (`roles/{role}/tasks/{file}.yml`)
- Reports success/404 status for each file attempt
- Output: `step4_github_fetch_history.json`

**GitHub MCP Verification**: If step4 output contains `"error": "all_paths_failed"` or any `"status": "404"` in `paths_tried` arrays, verify 404 errors using MCP tools. See [github_mcp_verification.md](github_mcp_verification.md) for complete verification process.

### Step 5: Analyze and Generate Summary (Claude)

**Input files**: Read outputs from steps 1-4 (`step1_job_context.json`, `step3_correlation.json`, `step4_github_fetch_history.json`, and if needed, `step2_splunk_logs.json`).

**Analysis Guidelines**:
- **Configuration Analysis**: Variable precedence (role defaults → common.yaml → platform/account.yaml → platform/catalog/env.yaml), check for conflicts, missing variables, secrets references
- **Task Analysis**: Task action patterns, duration analysis, time correlation, conditional execution checks
- **Root Cause**: Categorize as `configuration|infrastructure|workload_bug|credential|resource|dependency`

**Summary Requirements**:
- **Root Cause**: Category, summary, confidence
- **Evidence**: Supporting evidence from AAP logs, Splunk logs, and GitHub configs/code
  - **REQUIRED**: When `source` is `agnosticv_config` or `agnosticd_code`, **MUST** include `github_path` in format `owner/repo:path/to/file.yml:line`
  - Extract GitHub paths from step4:
    - **Configs**: From `step4.github_fetches[].fetched_configs.{purpose}.path` → construct as `{config_owner}/{config_repo}:{path}` (e.g., `example-org/config-repo:platform/account.yaml`)
    - **Workloads**: From `step4.github_fetches[].location.parsed` → construct as `{owner}/{repo}:{file_path}:{line_number}` (e.g., `example-org/workload-repo:roles/example-role/tasks/main.yml:42`)
- **Correlation**: How AAP logs link to Splunk (GUID, namespace, timestamps, pod names)
- **Recommendations**: Specific file changes with paths, actions, and reasons
  - **Include `github_path`** in recommendations when referencing GitHub files (format: `owner/repo:path/to/file.yml:line`)

**Note**: Job details, failed tasks, and configuration data are available in step1 and step4 files - reference them rather than duplicating in the summary.

**Output**: `step5_analysis_summary.json` (or present directly to user)

## Output

Analysis results are saved to `.analysis/<job-id>/`:

| File | Description | Author |
|------|-------------|--------|
| `step1_job_context.json` | Parsed job metadata (GUID, namespace, failed tasks) | Python |
| `step2_splunk_logs.json` | Correlated Splunk pod logs | Python |
| `step3_correlation.json` | Unified timeline with correlation proof | Python |
| `step4_github_fetch_history.json` | GitHub fetch results (configs and workload code) | Python (Claude updates for MCP verification) |
| `step5_analysis_summary.json` | Root cause summary with recommendations | Claude |

## Correlation Methods

The skill establishes correlation between AAP jobs and Splunk logs using:

- **Namespace Match** - `sandbox-<guid>-<env>` pattern in both sources
- **GUID Match** - 5-character deployment identifier (e.g., `x1234`)
- **Time Overlap** - Splunk logs fall within job execution window

Confidence levels:
- **High** - Namespace + time overlap confirmed
- **Medium** - GUID match + time overlap
- **Low** - Only identifier match, no time confirmation

## AgnosticD/AgnosticV Integration

This skill specifically analyzes AgnosticD/AgnosticV deployments:

- **AgnosticV Configuration**: Automatically fetches configuration hierarchy from Agnosticv repository
  - Base defaults (`common.yaml`)
  - Platform-specific configs (`{platform}/account.yaml`)
  - Environment-specific overrides (`{platform}/{catalog_item}/{env}.yaml`)

- **AgnosticD Workload Code**: Automatically fetches workload code from AgnosticD repositories
  - Role defaults (`roles/{role}/defaults/main.yml`)
  - Task files (`roles/{role}/tasks/{file}.yml`)

- **Variable Precedence**: Analyzes variable override order to identify configuration conflicts

## Supported Log Formats

The skill can read job logs in these formats:
- `.json` - Plain JSON
- `.json.gz` - Gzipped JSON
- `.json.gz.transform-processed` - Transformed gzipped JSON
- `.json.transform-processed` - Transformed plain JSON

## Troubleshooting

### GitHub Token Issues
- Ensure `GITHUB_TOKEN` is set in environment variables
- Token must have `repo` scope for private repositories
- Check token expiration and regenerate if needed

### GitHub MCP Tools Not Available
- Ensure GitHub MCP server is configured in Claude Code settings
- Restart Claude Code after adding MCP servers
- Check that `mcp__github__*` tools appear in available tools
- **Note**: MCP tools are only used for 404 verification, not for fetching files

### Files Not Found (404 errors)
- GitHub fetcher script handles path variations automatically
- If 404 errors occur, use GitHub MCP tools to verify (see GitHub MCP Verification section above)
- Check parent directories first before doing wild searches
- Document findings to identify parser bugs vs truly missing files

### Splunk Connection Issues
- Verify Splunk credentials in environment variables
- Check network connectivity to Splunk API endpoint
- Ensure SSL certificate verification settings are correct
