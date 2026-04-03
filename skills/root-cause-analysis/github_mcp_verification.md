# GitHub MCP Verification for Errors

After GitHub fetch step completes and outputs `step4_github_fetch_history.json`, Claude reasoning any errors (e.g., 404, timeout, HTTP errors) using MCP tools.

**Verification Process**:
1. **Check parent directories** using `mcp__github__get_file_contents` to list actual folder/file names (reveals case sensitivity, hyphens vs underscores)
   - For `{platform}/{catalog}/file.yaml`: check `{platform}/` for catalog names; if platform folder not found, check parent directory of platform for platform folder names (continue up directory tree as needed)
2. If folder not found in parent directory listing, use `mcp__github__search_code` to search for the filename or partial path
3. Document findings: wrong format → parser bug, missing → truly missing, empty → rare
4. **No assumptions**: "empty", "test", "demo" in names are just labels - always verify errors (especially 404s) before concluding files are missing
5. **After MCP verification**: Update `step4_github_fetch_history.json` by adding `mcp_verified: true` and `mcp_findings` (e.g., "parser_bug: expected underscore but found hyphen", "file_missing: verified via MCP search", "case_sensitivity: file is 'Config.yml' not 'config.yml'") to the relevant error objects in `fetched_configs` and `fetched_workload`