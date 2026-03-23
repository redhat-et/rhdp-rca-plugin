<div align="center">
  <img src="./docs/images/rhlogo.png" alt="Red Hat Logo" width="200"/>

  # RHDP RCA Plugin

  [![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](https://github.com/redhat-et/rhdp-rca-plugin)
  [![GitHub Stars](https://img.shields.io/github/stars/redhat-et/rhdp-rca-plugin?style=flat&logo=github)](https://github.com/redhat-et/rhdp-rca-plugin)
  [![Visitors](https://visitor-badge.laobi.icu/badge?page_id=redhat-et.rhdp-rca-plugin)](https://github.com/redhat-et/rhdp-rca-plugin)
  [![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](./LICENSE)

  Claude Code plugin for AI-assisted root-cause analysis of infrastructure failures and operational incidents.

  <p align="center">
    <a href="#how-it-works">How It Works</a> • <a href="#quick-start">Quick Start</a> • <a href="#available-skills">Available Skills</a> • <a href="#contributing">Contributing</a>
  </p>
</div>

---

## What is RHDP RCA Plugin?

RHDP RCA Plugin is a Claude Code marketplace containing specialized skills designed for Red Hat Demo Platform (RHDP) root cause analysis. This plugin suite enables AI-powered investigation of infrastructure failures, log analysis, and root cause diagnosis. These skills provide Claude with the tools to:

- Fetch and analyze logs from remote servers
- Correlate multiple data sources (Ansible, Splunk, GitHub)
- Perform automated root cause analysis
- Capture and organize user feedback

## Quick Start

### Prerequisites

- [Claude Code](https://claude.ai/claude-code) installed
- SSH access to remote servers (for log fetching)
- Splunk credentials (for correlation analysis)

### Installation

1. **Install via Claude Code UI**:
   - Navigate to Plugins (`/plugin` in terminal)
   - Add marketplace `redhat-et/rhdp-rca-plugin`
   - Browse and install plugins
   - Restart Claude Code

2. **Configure environment variables** in `.claude/settings.local.json`:

```json
{
  "env": {
    "REMOTE_HOST": "<remote-host>",
    "REMOTE_DIR": "<remote-dir>",
    "DEFAULT_LOCAL_DIR": "Path.home() / 'aiops_extracted_logs'",
    "JOB_LOGS_DIR": "/path/to/your/extracted_logs",
    "GITHUB_TOKEN": "ghp_xxxxxxxxxxxx",
    "SPLUNK_HOST": "<your-remote-splunk>",
    "SPLUNK_USERNAME": "your-username",
    "SPLUNK_PASSWORD": "your-password",
    "SPLUNK_INDEX": "<your-splunk-index>",
    "SPLUNK_OCP_APP_INDEX": "<splunk-ocp-app-index>",
    "SPLUNK_OCP_INFRA_INDEX": "<splunk-ocp-infra-index>",
    "SPLUNK_VERIFY_SSL": "false"
  }
}
```

### SSH Credentials
Please setup your SSH connection to the server before invoking log fetching skills.

The current log fetcher skills assumed the current settings: REMOTE_HOST = "" REMOTE_DIR = "" DEFAULT_LOCAL_DIR = Path.home() / "aiops_extracted_logs"

We encourage you to setup your profile under `~/.ssh/config`:

```
Host <remote-host>
    HostName <host-name>
    User <your-username>-redhat.com
    Port 22
    IdentityFile /Users/<User>/.ssh/<SSH_Public_Key>
```

---

## Available Skills

| Skill | Description | Key Features |
|-------|-------------|--------------|
| [template-skill](./skills/template-skill/) | Template for creating new skills | Starter template, best practices |
| [logs-fetcher](./skills/logs-fetcher/) | Fetch Ansible/AAP logs via SSH | Time-based filtering, job number lookup |
| [root-cause-analysis](./skills/root-cause-analysis/) | Automated RCA for failed jobs | Log correlation, Splunk + GitHub integration |
| [context-fetcher](./skills/context-fetcher/) | Fetch job configs and docs | GitHub and Confluence integration |
| [feedback-capture](./skills/feedback-capture/) | Capture user feedback | Structured storage, categorization |

---

## Skill Details

### 🔍 logs-fetcher

**Fetch Ansible/AAP logs from remote servers with flexible filtering**

```bash
# Fetch logs from a specific time range
python -m scripts.fetch_logs_ssh \
  --start-time "2025-12-09 08:00:00" \
  --end-time "2025-12-10 17:00:00" \
  --mode processed

# Fetch logs by job number
python -m scripts.fetch_logs_by_job 1234567 1234568 1234569
```

**Use cases:**
- Fetch logs from specific time windows (minute/second precision)
- Retrieve logs for specific job numbers
- Download recent processed or ignored job logs
- Investigate incidents within a known timeframe

**[View detailed documentation →](./skills/logs-fetcher/)**

---

### 🔎 root-cause-analysis

**Investigate failed jobs by correlating Ansible/AAP logs with Splunk OCP pod logs and GitHub configuration**

```
Step 1   [Python]  Parse local job log (extract GUID, namespace, failed tasks)
Step 2   [Python]  Query Splunk for correlated pod logs
Step 3   [Python]  Build correlation timeline
Step 4   [Python]  Fetch GitHub configs (AgnosticD/AgnosticV)
Step 5   [Claude]  Analyze and summarize root cause
```

**Command Usage:**
```bash
# By job ID (auto-fetches log from remote if not found locally)
.venv/bin/python scripts/cli.py analyze --job-id <JOB_ID> --fetch

# By explicit path (when you already have the log file)
.venv/bin/python scripts/cli.py analyze --job-log <path-to-job-log>
```

**Use cases:**
- Investigate job failures
- Analyze logs for errors and patterns
- Find root causes of infrastructure issues
- Debug failed deployments
- Troubleshoot Kubernetes/OpenShift problems

**[View detailed documentation →](./skills/root-cause-analysis/README.md)**
---
```json
{
  "env": {
    "REMOTE_HOST":"<remote-host>",
    "REMOTE_DIR": "<remote-dir>",
    "DEFAULT_LOCAL_DIR":"Path.home() / "aiops_extracted_logs"",
    "JOB_LOGS_DIR": "/path/to/your/extracted_logs",
    "SPLUNK_HOST": "<your-remote-splunk>",
    "SPLUNK_USERNAME": "your-username",
    "SPLUNK_PASSWORD": "your-password",
    "SPLUNK_INDEX": "<your-splunk-index>",
    "SPLUNK_OCP_APP_INDEX": "<splunk-ocp-app-index>",
    "SPLUNK_OCP_INFRA_INDEX": "<splunk-ocp-infra-index>",
    "SPLUNK_VERIFY_SSL": "false",
    "JUMPBOX_URI": "<username>.com@<jumpbox> -p <port>",
    "MLFLOW_CLAUDE_TRACING_ENABLED": "true",
    "MLFLOW_PORT":"<set localhost port>",
    "MLFLOW_EXPERIMENT_NAME": "<experiment name or default>"
  }
}

```
```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "*",
        "hooks": [   
          {
            "type": "command",
            "command": "./.claude/hooks/session-start.sh"
          }
        ]
      }
    ]
  }
}
```
---
**Note:** Run the following to make the script executable: `chmod +x ./.claude/hooks/session-start.sh`

### context-fetcher

**Fetch configuration and documentation context via MCP servers**

Integrates with:
- **GitHub**: Job configs, recent commits, CI workflows
- **Confluence**: Runbooks, troubleshooting guides, documentation

**Use cases:**
- Retrieve job configuration from repositories
- Access relevant documentation during investigations
- Review recent code changes related to failures

**[View detailed documentation →](./skills/context-fetcher/)**

---

### 💬 feedback-capture

**Capture and store user feedback during interactions**

Features:
- Ask users for feedback interactively
- Categorize feedback (Complexity, Clarity, Accuracy, etc.)
- Summarize interaction context
- Record structured feedback with timestamps

Feedback is appended to `~/feedback.txt` by default with session tracking.

**Use cases:**
- Collect feedback at the end of skill invocations
- Track user sentiment across sessions
- Categorize and store bug reports

**[View detailed documentation →](./skills/feedback-capture/README.md)**

---

## How It Works

### Architecture

```
                    ┌─────────────────────┐
                    │   Claude Code UI    │
                    │  (User Interface)   │
                    └──────────┬──────────┘
                               │
         ┌─────────────────────┴─────────────────────┐
         │         RHDP RCA Plugin Marketplace       │
         │                                           │
         │  ┌─────────────────────────────────────┐  │
         │  │  Skills (SKILL.md definitions)      │  │
         │  │                                     │  │
         │  │  • template-skill                   │  │
         │  │  • logs-fetcher ──────► SSH         │  │
         │  │  • root-cause-analysis ──► Splunk   │  │
         │  │                      └──► GitHub API│  │
         │  │  • context-fetcher ──► MCP Servers  │  │
         │  │  • feedback-capture ──► Local FS    │  │
         │  └─────────────────────────────────────┘  │
         └───────────────────────────────────────────┘
                               │
         ┌─────────────────────┴─────────────────────┐
         │                                           │
    ┌────▼────┐    ┌────────┐    ┌──────────────┐   │
    │ GitHub  │    │Confluen│    │ External     │   │
    │   MCP   │    │ce MCP  │    │ Systems      │   │
    │         │    │        │    │ (SSH/Splunk) │   │
    └─────────┘    └────────┘    └──────────────┘   │
```

**Integration Points:**
- **MCP Servers**: GitHub (code search, file retrieval) and Confluence (documentation)
- **Direct APIs**: Splunk REST API, GitHub API
- **SSH**: Remote log server access
- **Local**: File system for logs and feedback

Each skill follows the [Anthropic Agent Skills Specification](./docs/agent_skills_spec.md) with `SKILL.md` definitions that Claude Code loads automatically.

### End-to-End RCA Workflow

When investigating a failed job:

1. **User Query**: "/root-cause-analysis job 1234567"
2. **Skill Selection**: Claude selects root-cause-analysis
3. **Data Collection** (Steps 1-4, automated):
   - Parse job log (local file)
   - Query Splunk for pod logs
   - Correlate timeline
   - Fetch GitHub configs via API
4. **AI Analysis** (Step 5): Claude analyzes and identifies root cause
5. **Results**: Summary with evidence and recommendations

### Usage with Claude Code

Simply invoke skills by describing your task:

```
"Analyze job 1234567 for root cause"
"Investigate why this deployment failed"
"Fetch logs from the last 2 hours"
```

Claude will automatically select and invoke the appropriate skill based on your request.

---

## Creating a New Skill

1. Create a directory with your skill name (lowercase, hyphen-separated)
2. Add a `SKILL.md` file:

```markdown
---
name: my-skill
description: Brief description of what this skill does
allowed-tools:
  - Bash
  - Read
---

# My Skill

Instructions for Claude...
```

See [template-skill](./template-skill/) for a minimal example and [agent_skills_spec.md](./agent_skills_spec.md) for the full specification.

---

## MLFlow Tracing Setup Guide for Claude Code

### Step 1: Install Dependencies

1. Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install the package and MLFlow:

```bash
pip install -e .
pip install mlflow
```

3. Enable MLFlow autologging for Claude:

```bash
mlflow autolog claude
```

### Step 2: Configure Claude Settings

Add the following environment variables to your Claude settings file at `~/.claude/settings.json`:

```json
{
  "env": {
    "JUMPBOX_URI": "<your-username>@<your-jumpbox> -p <port>",
    "MLFLOW_CLAUDE_TRACING_ENABLED": "true",
    "MLFLOW_PORT":"<set localhost port>",
    "MLFLOW_EXPERIMENT_NAME": "<experiment-name>"
  }
}
```

**Note**: Replace `<your-username>@<your-jumpbox> -p <port>` with your actual jumpbox connection details.
Replace `<experiment-name>` to a experiment name to the desired experiment name. This will automatically create the experiment for you if it does not exist.

### Step 3: Add hooks SessionStart to hooks in .claude/settings.json
```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/session-start.sh"
          }
        ]
      }
    ]
  }
}
```

Make the script executable:
```bash
chmod +x ./.claude/hooks/session-start.sh
```

### Step 4: cd into AIOPS-SKILLS/ dir (where claude will be running)

### Step 5: Enable Claude Autologging

Before starting Claude, run:

```bash
mlflow autolog claude
```

### Step 6: Start Claude

```bash
claude
```

### Step 7: Run a Prompt

Enter any prompt in Claude to generate a trace.

### Step 8: View Traces
Open your browser and navigate to:                                                                                                            
http://localhost:5000                                                                                                                                             
Your MLFlow dashboard will display your trace along with any previous traces.

## Contributing

We welcome contributions! Please ensure your skill:

- Follows the [Agent Skills Spec](./docs/agent_skills_spec.md)
- Includes clear, actionable instructions
- Is focused on a specific AIOps domain
- Includes appropriate documentation and examples

See [CONTRIBUTING.md](./docs/CONTRIBUTING.md) for detailed contribution guidelines.

---

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](./LICENSE) file for details.

Copyright 2025 Red Hat ACE Team

Individual skills may specify their own licenses in their frontmatter.

---

## Support

- **Issues**: Report issues on [GitHub Issues](https://github.com/redhat-et/rhdp-rca-plugin/issues)
- **Documentation**: See [docs/](./docs/) for additional guides

---

**Built by the Red Hat ACE Team**
