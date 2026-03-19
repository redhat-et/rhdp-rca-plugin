---
name: context-fetcher
description: Retrieve configuration and documentation context from GitHub, Atlassian Confluence and Slack for job investigation. Use when you need to find relevant code, configuration files, documentation, runbooks, procedures, or messages related to a job or incident. You must use feedback-capture skill at the end
allowed-tools:
  - mcp__github__search_code
  - mcp__github__get_file_contents
  - mcp__github__search_repositories
  - mcp__atlassian__confluence_search
  - mcp__atlassian__confluence_get_page
  - mcp__slack__slack_get_channel_history
  - Read
  - Write
  - Bash
---

# Context Fetcher

Retrieve configuration and documentation context from GitHub, Confluence and Slack to support job investigation and incident analysis.

```
Step 1   [Claude]  Identify search keywords from job/incident
Step 2   [MCP]     Search GitHub repositories
Step 3   [MCP]     Search Confluence pages
Step 4   [MCP]     Search Slack channel
Step 5   [Claude]  Synthesize and organize findings
Step 6   [Claude]  Log search to MLflow (run scripts/mlflow_context.py)
```

## MLflow Tracing

After synthesizing findings, you MUST log the search to MLflow:

```bash
python scripts/mlflow_context.py \
  --query "{search keywords used}" \
  --sources "{comma-separated: github,confluence,slack}" \
  --job-id "{job ID if applicable}" \
  --incident-id "{incident ID if applicable}" \
  --results-summary "{brief summary of what was found}"
```

## Capabilities

### GitHub

Search GitHub repositories for:
- **Configuration files** - YAML, JSON, TOML configs (e.g., `config.yaml`, `values.yaml`, `deployment.yaml`)
- **Code references** - Source code related to the job or service
- **Documentation** - README files, docs directories, markdown files
- **CI/CD definitions** - GitHub Actions workflows, Jenkinsfiles
- **Infrastructure as Code** - Terraform, Ansible, Helm charts

**Search strategies:**
- Search by file path patterns (e.g., `path:config/*.yaml`)
- Search by content keywords (e.g., `job-name` or `service-name`)
- Search by file type (e.g., `extension:yaml`, `extension:md`)
- Search in specific repositories or organizations

### Confluence

Search Confluence spaces and pages for:
- **Runbooks** - Operational procedures and troubleshooting guides
- **Architecture documentation** - System designs and component interactions
- **Configuration guides** - Setup and configuration instructions
- **Incident reports** - Historical incidents and resolutions
- **Knowledge base articles** - Best practices and common issues

**Search strategies:**
- Search by page title keywords
- Search by content/body text
- Filter by space (e.g., Operations, Engineering, DevOps)
- Search by labels or tags
- Use CQL (Confluence Query Language) for advanced queries

### Slack

Search Slack channel:
- **Updates** - Messages related to the query
- **Queries** - Questions of the related topic from others
- **Replies** - Check replies to posts for related information
- **Documentation** - relevant links to documentation, repos and urls

**Search strategies:**
- Search all channels
- Search the content of each messages for keywords
- Search for replies to messages additional information


## Usage

### Basic Workflow

1. **Identify search terms** from the job name, service name, error messages, or incident details
2. **Search GitHub** for relevant code and configuration files
3. **Search Confluence** for related documentation and runbooks
4. **Search Slack** for relevant messages
5. **Synthesize findings** into organized context
6. **Log trace**  run scripts/mlflow_context.py


### Example: Finding Job Configuration

  **Step 1:** Search GitHub for job configuration
  - Tool: `mcp__github__search_code`
  - Query: `"job-name" path:config extension:yaml`
  - Expected: Configuration YAML files for the job

  **Step 2:** Retrieve the file content
  - Tool: `mcp__github__get_file_contents`
  - Parameters: owner, repo, path from search results

  **Step 3:** Search Confluence for documentation
  - Tool: `mcp__atlassian__confluence_search`
  - Query: `"job-name" AND space:Operations`
  - Expected: Runbooks and operational guides

  **Step 4:** Search Slack channels for messages
  - Tool: `mcp__slack__slack_get_channel_history`
  - Query: `"job-name" AND messages`
  - Expected: Messages and replies

   **Step 5:** Run mlflow_context.py
  - Expected: The input and output of this skill is traced

## Prerequisites

Configure MCP servers in your Claude Code settings:

```json
{
  "mcpServers": {
    "github": {
      },
    "confluence": {
      },
   "Slack": {

      }
    }
}
```

Once configured, use the MCP tools directly:
- `mcp__github__*` - GitHub operations (search_code, get_file, list_repositories, etc.)
- `mcp__confluence__*` - Confluence operations (search, get_page, get_space, etc.)
- `mcp__slack__*` - Slack messages (search, message, get_message, etc.)

## Search Best Practices

### GitHub Search Tips

1. **Use specific file paths** when you know where configs are stored
   - `path:kubernetes/ path:deployment.yaml`
   - `path:config/ extension:yaml`

2. **Combine keywords** for better results
   - `"job-name" AND "config"`
   - `"service-name" OR "component-name"`

3. **Search in specific repos** when you know the repository
   - `repo:org/repo-name "search-term"`

4. **Use file extensions** to narrow results
   - `extension:yaml "config"`
   - `extension:md "documentation"`

### Confluence Search Tips

1. **Use space filters** to narrow results
   - Search in Operations space for runbooks
   - Search in Engineering space for architecture docs

2. **Combine title and content** searches
   - Search by page title for specific documents
   - Search by content for mentions within pages

3. **Use labels** to find categorized content
   - `label:runbook`
   - `label:troubleshooting`

4. **Leverage CQL** for complex queries
   - `space = "Operations" AND text ~ "job-name"`

### Slack Search Tips

1. **Use channels** to find relevant info
   - Search in messages from relevant info
   - Search replies for additional info

2. **Combine keywords** searches
   - Search messages by keywords


## Presenting Results

  Always structure findings as:

  ### GitHub Results
  - **File**: `path/to/file` ([repo link])
  - **Relevance**: High/Medium/Low
  - **Summary**: Brief description
  - **Key Content**: Relevant excerpts

  ### Confluence Results
  - **Page**: "Page Title" ([page link])
  - **Space**: SpaceKey
  - **Summary**: What the page contains
  - **Relevant Sections**: Specific sections to review

  ### Slack Results
  - **Channel**: "Channel Name"
  - **Username**: Username of the message
  - **Summary**: What the message and replies contain


## Troubleshooting

  ### Common Issues

  **MCP Server Not Available**
  - Ensure MCP servers are configured in Claude Code settings
  - Restart Claude Code after adding new MCP servers
  - Check server connection: Look for "mcp__github__*" tools in
  available tools

  **No Results Found**
  - Try broader search terms (remove quotes, use OR operators)
  - Check if searching in correct organization/space
  - Verify repository/space access permissions

  **Too Many Results**
  - Add more specific filters (path:, extension:, space:)
  - Use exact phrases in quotes
  - Limit by file type or date range
