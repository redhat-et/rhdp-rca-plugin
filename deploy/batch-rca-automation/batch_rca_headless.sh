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
SO_SCHEMA_FILE="$SCRIPT_DIR/schemas/batch_report.structured_output.schema.json"
TIMESTAMP=$(date -u +%Y%m%d_%H%M%S)
BATCH_ID="batch_${TIMESTAMP}"
REPORT_FILE="$REPORT_DIR/${BATCH_ID}.json"

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
NO_PRE_FILTER=false

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
    --no-pre-filter)
      NO_PRE_FILTER=true
      shift
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

if [ -z "$JOB_IDS" ]; then
  echo "[INFO] No unanalyzed jobs found"
  echo "[SUCCESS] Batch RCA completed at $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
  exit 0
fi

JOB_COUNT=$(echo "$JOB_IDS" | wc -l | tr -d ' ')
JOBS_LIST=$(echo "$JOB_IDS" | tr '\n' ' ' | sed 's/ $//')
echo "[INFO] Found $JOB_COUNT job(s) to analyze: $JOBS_LIST"

#############################################
# Step 1b: Pre-filter against known issues
#############################################
PRE_MATCHED="[]"
PRE_MATCHED_COUNT=0
KNOWN_ISSUES="[]"

KNOWN_ISSUES=$(python3 "$SCRIPT_DIR/scripts/fetch_known_issues.py" \
  --lookback-hours 4 --limit 50 2>/dev/null) || KNOWN_ISSUES="[]"

KNOWN_COUNT=$(echo "$KNOWN_ISSUES" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")

if [ "$NO_PRE_FILTER" = true ]; then
  echo "[STEP 1b] Pre-filter disabled (--no-pre-filter)"
  echo "[INFO] $KNOWN_COUNT known issue(s) loaded for agent context"
elif [ "$KNOWN_COUNT" = "0" ]; then
  echo "[STEP 1b] No known issues found, skipping pre-filter"
else
  echo "[STEP 1b] Pre-filtering $JOB_COUNT job(s) against $KNOWN_COUNT known issue(s)..."

  PRE_FILTER_OUTPUT=$(echo "$JOB_IDS" | python3 "$SCRIPT_DIR/scripts/pre_filter_jobs.py" \
    --lookback-hours 4 2>/dev/null) || PRE_FILTER_OUTPUT=""

  if [ -n "$PRE_FILTER_OUTPUT" ]; then
    PRE_MATCHED_COUNT=$(echo "$PRE_FILTER_OUTPUT" | python3 -c "
import json, sys
print(len(json.load(sys.stdin).get('pre_matched', [])))
" 2>/dev/null || echo "0")

    if [ "$PRE_MATCHED_COUNT" -gt 0 ] 2>/dev/null; then
      PRE_MATCHED=$(echo "$PRE_FILTER_OUTPUT" | python3 -c "
import json, sys
print(json.dumps(json.load(sys.stdin).get('pre_matched', [])))
")

      ANALYZE_IDS=$(echo "$PRE_FILTER_OUTPUT" | python3 -c "
import json, sys
for jid in json.load(sys.stdin).get('analyze', []):
    print(jid)
")

      echo "[INFO] Pre-filtered: $PRE_MATCHED_COUNT job(s) matched known issues"

      # Store pre-matched results immediately so they are saved regardless of
      # whether the Claude invocation below succeeds or fails.
      python3 "$SCRIPT_DIR/scripts/store_report.py" --pre-matched "$PRE_MATCHED" || {
        echo "[ERROR] Failed to store pre-matched results"
        exit 1
      }

      if [ -n "$ANALYZE_IDS" ]; then
        JOB_IDS="$ANALYZE_IDS"
        JOB_COUNT=$(echo "$JOB_IDS" | wc -l | tr -d ' ')
        JOBS_LIST=$(echo "$JOB_IDS" | tr '\n' ' ' | sed 's/ $//')
        echo "[INFO] Remaining: $JOB_COUNT job(s) for full RCA: $JOBS_LIST"
      else
        echo "[INFO] All jobs matched known issues, skipping Claude invocation"
        echo "[SUCCESS] Batch RCA completed at $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
        exit 0
      fi
    else
      echo "[INFO] No pre-filter matches, all $JOB_COUNT job(s) proceed to full RCA"
    fi
  fi
fi

#############################################
# Step 2: Build Dynamic Claude Prompt
#############################################
echo "[STEP 2] Building Claude prompt for parallel RCA..."

if [ ! -f "$SO_SCHEMA_FILE" ]; then
  echo "[ERROR] Structured output schema not found at: $SO_SCHEMA_FILE"
  exit 1
fi

# Build the orchestration prompt
read -r -d '' CLAUDE_PROMPT <<EOF || true
You are running in headless mode to analyze failed jobs in parallel.

**Job IDs to analyze:** $JOBS_LIST
**Batch ID:** $BATCH_ID
**Jobs requested:** $JOB_COUNT

**Known Recent Issues (last 4 hours — for aggregation historical matching only):**
$KNOWN_ISSUES

**Instructions:**

1. **Spawn parallel agents** - For EACH job ID above, spawn a background agent in a SINGLE message with multiple Agent tool calls:

   Agent({
     description: "RCA for job {JOB_ID}",
     prompt: "Invoke the 'root-cause-analysis' skill for job {JOB_ID}. Use: Skill({skill: 'root-cause-analysis', args: '{JOB_ID}'}). Follow all skill instructions including Step 5 analysis and upload. Report completion status.",
     run_in_background: true
   })

   **CRITICAL:** All agents must be in ONE response for true parallelism.
   Record agent_spawn as the ISO 8601 UTC timestamp when agents are launched.

2. **Wait for completion** - You'll receive task-notification for each agent when done.
   Record per-job duration_ms and status (completed|failed|timeout) in timing.agent_completion.

3. **Aggregate results** - After all agents complete:
   - For each job ID, read its step5 summary from:
     ~/.claude/plugins/marketplaces/*/skills/root-cause-analysis/.analysis/{job_id}/step5_analysis_summary.json
     and step1 context from the same directory's step1_job_context.json.
     Use a glob to find the correct marketplace path.
   - Tally: total_jobs_analyzed, total_jobs_failed, confidence_breakdown,
     root_cause_category_breakdown, top-5 high_priority_recommendations
     (deduplicated by action text, highest priority first)
   - **Historical matches (semantic)** — For each job with high or medium confidence,
     compare its root_cause_summary against every entry in the Known Recent Issues list.
     A match requires the failure to be genuinely the same: same error type, same
     failing component, same failure mode — not just the same category label.
     For each genuine match set historical_matches to:
       [{"matched_result_id": <result_id>, "recurrence_count": <recurrence_count>,
         "similarity_reasoning": "<one sentence explaining why it is the same failure>"}]
     If no entry in the Known Recent Issues list is a genuine match, set historical_matches to [].
   - Use batch_id: "$BATCH_ID", total_jobs_requested: $JOB_COUNT

4. **Cross-job patterns** - Only note genuine patterns (same failing component,
   same error). Leave cross_job_patterns as an empty array if there is no clear
   overlap across jobs.

5. **Write the report** - Read schemas/batch_report.structured_output.schema.json,
   then use the Write tool to save the complete batch report as valid JSON to:
   $REPORT_FILE

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
cd "$SCRIPT_DIR" || exit 1

CLAUDE_STDERR_FILE=$(mktemp)
CLAUDE_MODEL="${CLAUDE_MODEL:-claude-sonnet-4-6}"
echo "[INFO] Using model: $CLAUDE_MODEL"

claude -p \
  --allowedTools "Agent,Bash,Read,Write,Skill,mcp__github__search_code,mcp__github__get_file_contents" \
  --model "$CLAUDE_MODEL" \
  "$CLAUDE_PROMPT" 2>"$CLAUDE_STDERR_FILE" || {
  echo "[ERROR] Claude execution failed"
  echo "[DEBUG] stderr: $(cat "$CLAUDE_STDERR_FILE")"
  rm -f "$CLAUDE_STDERR_FILE"
  exit 1
}
rm -f "$CLAUDE_STDERR_FILE"

#############################################
# Step 4b: Verify report was written
#############################################
echo "[STEP 4b] Verifying report..."

if [ ! -f "$REPORT_FILE" ]; then
  echo "[ERROR] Claude did not write report to $REPORT_FILE"
  exit 1
fi
echo "[INFO] Report written to $REPORT_FILE"

#############################################
# Step 5: Store report in local DB
#############################################
echo "[STEP 5] Storing report in local database..."

python3 "$SCRIPT_DIR/scripts/store_report.py" "$REPORT_FILE" || {
  echo "[ERROR] Failed to store report in database"
  exit 1
}

echo "[SUCCESS] Batch RCA completed at $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "[INFO] Report: $REPORT_FILE"
