#!/bin/bash
set -euo pipefail

#############################################
# Batch RCA Analysis - Claude Headless Mode
#############################################
#
# This script:
# 1. Queries source PostgreSQL table for unanalyzed job IDs (ai_processed = FALSE)
# 2. Invokes Claude in headless mode to run parallel RCA on those jobs
#
# Requires SOURCE_DB_* env vars (HOST, PORT, NAME, USER, PASSWORD, TABLE)
# set in .claude/settings.json under "env".
#
# Usage:
#   ./batch_rca_headless.sh [--since 'YYYY-MM-DD HH:MM:SS'] [--limit N]
#
# Schedule via cron (every 30 min — each run analyzes the previous 30-min window):
#   7,37 * * * * /path/to/batch_rca_headless.sh >> /tmp/batch_rca.log 2>&1
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPORT_DIR="$SCRIPT_DIR/reports"
SCHEMA_FILE="$SCRIPT_DIR/schemas/batch_report.schema.json"
TIMESTAMP=$(date -u +%Y%m%d_%H%M%S)
BATCH_ID="batch_${TIMESTAMP}"

# Load environment variables from Claude settings.json
SETTINGS_FILE="$SCRIPT_DIR/.claude/settings.json"
if [ ! -f "$SETTINGS_FILE" ]; then
  echo "[ERROR] Claude settings.json not found at: $SETTINGS_FILE"
  echo "[ERROR] Please ensure .claude/settings.json exists with env variables configured"
  exit 1
fi

# Extract env vars from JSON using python
eval "$(python3 -c "
import json, sys
try:
    with open('$SETTINGS_FILE') as f:
        settings = json.load(f)
    for key, value in settings.get('env', {}).items():
        print(f'export {key}=\"{value}\"')
except Exception as e:
    print(f'echo \"[ERROR] Failed to load settings.json: {e}\"', file=sys.stderr)
    sys.exit(1)
")"

echo "[INFO] Environment variables loaded from settings.json"

# Default: look back 30 minutes (matches the cron interval)
SINCE=""
LIMIT=""

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --since)
      SINCE="$2"
      shift 2
      ;;
    --limit)
      LIMIT="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

# If --since not provided, default to 30 minutes ago
if [ -z "$SINCE" ]; then
  if date -v-1d > /dev/null 2>&1; then
    SINCE=$(date -u -v-30M "+%Y-%m-%d %H:%M:%S")
  else
    SINCE=$(date -u -d "30 minutes ago" "+%Y-%m-%d %H:%M:%S")
  fi
fi

echo "[INFO] Batch RCA Analysis - $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "[INFO] Analyzing events since: $SINCE"

#############################################
# Step 1: Query source DB for unanalyzed job IDs
#############################################
echo "[STEP 1] Querying source database for unanalyzed jobs..."

QUERY_ARGS=(--since "$SINCE")
if [ -n "$LIMIT" ]; then
  QUERY_ARGS+=(--limit "$LIMIT")
fi

JOB_IDS=$(python3 "$SCRIPT_DIR/scripts/query_source_db.py" "${QUERY_ARGS[@]}")

if [ $? -ne 0 ]; then
  echo "[ERROR] Source DB query failed"
  exit 1
fi

if [ -z "$JOB_IDS" ]; then
  echo "[INFO] No unanalyzed jobs found"
  echo "[SUCCESS] Batch RCA completed at $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
  exit 0
fi

JOB_COUNT=$(echo "$JOB_IDS" | wc -l | tr -d ' ')
JOBS_LIST=$(echo "$JOB_IDS" | tr '\n' ' ' | sed 's/ $//')
echo "[INFO] Found $JOB_COUNT job(s) to analyze: $JOBS_LIST"

#############################################
# Step 2: Build Dynamic Claude Prompt
#############################################
echo "[STEP 2] Building Claude prompt for parallel RCA..."

if [ ! -f "$SCHEMA_FILE" ]; then
  echo "[ERROR] Batch report schema not found at: $SCHEMA_FILE"
  exit 1
fi

# Build the orchestration prompt
read -r -d '' CLAUDE_PROMPT <<EOF || true
You are running in headless mode to analyze failed jobs in parallel.

**Job IDs to analyze:** $JOBS_LIST
**Batch ID:** $BATCH_ID
**Jobs requested:** $JOB_COUNT

**Instructions:**

1. **Spawn parallel agents** - For EACH job ID above, spawn a background agent in a SINGLE message with multiple Agent tool calls:

   Agent({
     description: "RCA for job {JOB_ID}",
     prompt: "Invoke the 'root-cause-analysis' skill for job {JOB_ID}. Use: Skill({skill: 'root-cause-analysis', args: '{JOB_ID}'}). Follow all skill instructions including Step 5 analysis. Report completion status.",
     run_in_background: true
   })

   **CRITICAL:** All agents must be in ONE response for true parallelism.
   Record agent_spawn as the ISO 8601 UTC timestamp when agents are launched.

2. **Wait for completion** - You'll receive task-notification for each agent when done.
   Record per-job duration_ms and status (completed|failed|timeout) in timing.agent_completion.

3. **Aggregate results** - After all agents complete:
   - Read each job's step5_analysis_summary.json from:
     .claude/skills/root-cause-analysis/.analysis/{job_id}/step5_analysis_summary.json
   - Also read step1_job_context.json from the same .analysis/{job_id}/ directory for guid,
     catalog_item, cluster/platform, and job_duration_seconds
   - Read the schema file at $SCHEMA_FILE before building the report; every required field must be present
   - Use these fixed values:
     * batch_id: "$BATCH_ID"
     * total_jobs_requested: $JOB_COUNT
     * total_jobs_analyzed: count of jobs with a valid step5_analysis_summary.json
     * total_jobs_failed: count of jobs whose agent failed or lack step5 output
     * confidence_breakdown: tally high/medium/low from each job's root_cause.confidence
     * high_priority_recommendations: top 5 across all jobs, ranked 1-5, deduplicated where possible
     * failed_analyses: one entry per failed job (empty array when none failed)
     * analysis_path for each job: ".analysis/{job_id}/step5_analysis_summary.json"

4. **Cross-job & historical correlation** - Detect patterns across current batch AND previous batches:
   a. **Within-batch:** Compare root_cause_summary values across jobs in this batch for genuine
      similarity (same failing component, same error message, same underlying issue).
      For each match, add an entry to cross_job_patterns with source: "current_batch".
   b. **Historical:** Collect the unique (root_cause_category, catalog_item) pairs from analyzed jobs.
      Write them as a JSON array to /tmp/rca_pairs_${BATCH_ID}.json:
        [{"root_cause_category": "cloud_api", "catalog_item": "sandbox-ibm"}, ...]
      Run via Bash:
        python3 scripts/query_historical_matches.py --input /tmp/rca_pairs_${BATCH_ID}.json --exclude-batch "$BATCH_ID"
      If the script returns a non-empty JSON array, compare each historical root_cause_summary
      against the current batch jobs. For genuine matches, add an entry to cross_job_patterns
      with source: "historical", historical_result_id (the id from the matching result), and
      recurrence_count (occurrence_count from the query output).
      Clean up the temporary file when done.
   c. Do NOT force correlations just because jobs share a root_cause_category — the
      root_cause_summary content must show real similarity. An empty cross_job_patterns
      array is the correct output when no high-confidence patterns exist.

5. **Save report** - Write ONLY valid JSON (no markdown, no comments) to:
   $REPORT_DIR/${BATCH_ID}.json

6. **Output completion summary** - Print to stdout:
   - Number of jobs analyzed successfully
   - Number of failures (if any)
   - Cross-job patterns found (if any)
   - Report location

**Note:** The root-cause-analysis skill handles Steps 1-5 automatically, including Claude's analysis in Step 5.
EOF

#############################################
# Step 3: Setup MLflow
#############################################
MLFLOW_VENV="$SCRIPT_DIR/.mlflow-venv"
if grep -q "MLFLOW_CLAUDE_TRACING_ENABLED.*true" "$SETTINGS_FILE" 2>/dev/null; then
  echo "[STEP 3] Setting up MLflow tracing..."

  if [ ! -d "$MLFLOW_VENV" ]; then
    echo "[INFO] Creating MLflow venv (first run)..."
    python3 -m venv "$MLFLOW_VENV"
    "$MLFLOW_VENV/bin/pip" install -q mlflow
    echo "[INFO] MLflow installed"
  fi

  echo "[INFO] MLflow tracing enabled"
else
  echo "[STEP 3] MLflow tracing disabled (skipping)"
fi

#############################################
# Step 4: Execute Claude Headless
#############################################
echo "[STEP 4] Executing Claude in headless mode..."

mkdir -p "$REPORT_DIR"

# Run claude in non-interactive mode with permissions bypass for testing
# Note: -p/--print flag for non-interactive output
# Run from repo root to pick up .claude/settings.json (MLflow hooks, env vars)

cd "$SCRIPT_DIR" || exit 1

echo "$CLAUDE_PROMPT" | claude -p \
  --allowedTools "Agent,Skill,Read,Write,Bash,mcp__github__search_code,mcp__github__get_file_contents" \
  - || {
  echo "[ERROR] Claude execution failed"
  exit 1
}

#############################################
# Step 5: Store report in local DB
#############################################
echo "[STEP 5] Storing report in local database..."

REPORT_FILE="$REPORT_DIR/batch_${TIMESTAMP}.json"
if [ -f "$REPORT_FILE" ]; then
  python3 "$SCRIPT_DIR/scripts/store_report.py" "$REPORT_FILE" || {
    echo "[WARN] Failed to store report in database (non-fatal)"
  }
else
  echo "[WARN] Report file not found at $REPORT_FILE, skipping DB store"
fi

echo "[SUCCESS] Batch RCA completed at $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "[INFO] Report: $REPORT_DIR/${BATCH_ID}.json"
