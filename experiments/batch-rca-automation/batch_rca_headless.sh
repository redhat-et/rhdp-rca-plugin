#!/bin/bash
set -euo pipefail

#############################################
# Batch RCA Analysis - Claude Headless Mode
#############################################
#
# This script:
# 1. Fetches recent failed job logs
# 2. Filters out already-analyzed jobs
# 3. Invokes Claude in headless mode to run parallel RCA
# 4. Tracks analyzed jobs to prevent re-analysis
#
# Usage:
#   ./batch_rca_headless.sh [--limit N] [--period 5m|30m|1h|24h]
#
# Schedule via cron: (avoid :00, :30 load spikes)
#   # Every 5 mins (fast testing):  3,8,13,18,23,28,33,38,43,48,53,58 * * * *
#   # Every 30 mins (prod):         7,37 * * * *
#   7,37 * * * * /path/to/batch_rca_headless.sh --limit 15 --period 30m >> /tmp/batch_rca.log 2>&1
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="$SCRIPT_DIR/analyzed_jobs.txt"
TIMESTAMP_FILE="$SCRIPT_DIR/last_fetch_timestamp.txt"
REPORT_DIR="$SCRIPT_DIR/reports"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Load environment variables from Claude settings.json
SETTINGS_FILE="$SCRIPT_DIR/.claude/settings.json"
if [ ! -f "$SETTINGS_FILE" ]; then
  echo "[ERROR] Claude settings.json not found at: $SETTINGS_FILE"
  echo "[ERROR] Please ensure .claude/settings.json exists with env variables configured"
  exit 1
fi

# Extract env vars from JSON using python
eval $(python3 -c "
import json, sys
try:
    with open('$SETTINGS_FILE') as f:
        settings = json.load(f)
    for key, value in settings.get('env', {}).items():
        print(f'export {key}=\"{value}\"')
except Exception as e:
    print(f'echo \"[ERROR] Failed to load settings.json: {e}\"', file=sys.stderr)
    sys.exit(1)
")

echo "[INFO] Environment variables loaded from settings.json"
echo "  REMOTE_HOST: $REMOTE_HOST"
echo "  REMOTE_DIR: $REMOTE_DIR"
echo "  JOB_LOGS_DIR: $JOB_LOGS_DIR"

# Default settings
LIMIT=10
PERIOD="30m"

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --limit)
      LIMIT="$2"
      shift 2
      ;;
    --period)
      PERIOD="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

echo "[INFO] Batch RCA Analysis - $(date)"
echo "[INFO] Limit: $LIMIT | Period: $PERIOD"

#############################################
# Step 1: Fetch Recent Logs
#############################################
echo "[STEP 1] Fetching recent failed job logs..."

# Calculate time window using state-based tracking to avoid overlaps
if [ -f "$TIMESTAMP_FILE" ]; then
  # Use last fetch timestamp as start time
  START_TIME=$(cat "$TIMESTAMP_FILE")
  echo "[INFO] Using last fetch time: $START_TIME"
else
  # First run - use period-based window
  echo "[INFO] First run - using period-based window"
  # Cross-platform date calculation (macOS vs Linux)
  if date -v-1d > /dev/null 2>&1; then
    # macOS (BSD date)
    case $PERIOD in
      5m)
        START_TIME=$(date -u -v-5M "+%Y-%m-%d %H:%M:%S")
        ;;
      30m)
        START_TIME=$(date -u -v-30M "+%Y-%m-%d %H:%M:%S")
        ;;
      1h)
        START_TIME=$(date -u -v-1H "+%Y-%m-%d %H:%M:%S")
        ;;
      24h)
        START_TIME=$(date -u -v-24H "+%Y-%m-%d %H:%M:%S")
        ;;
      *)
        echo "[ERROR] Invalid period: $PERIOD (use 5m, 30m, 1h, or 24h)"
        exit 1
        ;;
    esac
  else
    # Linux (GNU date)
    case $PERIOD in
      5m)
        START_TIME=$(date -u -d "5 minutes ago" "+%Y-%m-%d %H:%M:%S")
        ;;
      30m)
        START_TIME=$(date -u -d "30 minutes ago" "+%Y-%m-%d %H:%M:%S")
        ;;
      1h)
        START_TIME=$(date -u -d "1 hour ago" "+%Y-%m-%d %H:%M:%S")
        ;;
      24h)
        START_TIME=$(date -u -d "24 hours ago" "+%Y-%m-%d %H:%M:%S")
        ;;
      *)
        echo "[ERROR] Invalid period: $PERIOD (use 5m, 30m, 1h, or 24h)"
        exit 1
        ;;
    esac
  fi
fi

# Record current time BEFORE fetch (this becomes next run's start time)
# Always use UTC to avoid timezone mismatches with remote server
CURRENT_TIME=$(date -u "+%Y-%m-%d %H:%M:%S")
echo "[INFO] Fetching logs from $START_TIME (UTC) to now"
echo "[INFO] Local time: $(date '+%Y-%m-%d %H:%M:%S %Z')"

# Fetch logs using logs-fetcher script from plugin cache
# Assumes: REMOTE_HOST, REMOTE_DIR, JOB_LOGS_DIR are set in env
LOGS_FETCHER_SCRIPT=$(find ~/.claude/plugins/cache -name "fetch_logs_ssh.py" 2>/dev/null | head -1)

if [ -z "$LOGS_FETCHER_SCRIPT" ]; then
  echo "[ERROR] logs-fetcher plugin not found. Please install aiops-plugin."
  exit 1
fi

# First, get the list of files to fetch from remote (for job ID extraction)
FILE_LIST_CMD="cd ${REMOTE_DIR} && find . -maxdepth 1 -type f -name '*.transform-processed' -newermt '${START_TIME}' -printf '%T@ %f\\n' | sort -rn | cut -d' ' -f2- | head -n ${LIMIT}"
FILE_LIST=$(ssh "$REMOTE_HOST" "$FILE_LIST_CMD" 2>&1 | grep -v "WARNING:" | grep -v "vulnerable" | grep -v "upgraded")

# Now fetch the files using the logs-fetcher script
FETCH_OUTPUT=$(python3 "$LOGS_FETCHER_SCRIPT" \
  --mode processed \
  --order desc \
  --limit "$LIMIT" \
  --start-time "$START_TIME" \
  --local-dir "$JOB_LOGS_DIR" \
  2>&1)

FETCH_EXIT_CODE=$?
if [ $FETCH_EXIT_CODE -ne 0 ]; then
  echo "[ERROR] Log fetch failed with exit code: $FETCH_EXIT_CODE"
  echo "[DEBUG] Fetch output:"
  echo "$FETCH_OUTPUT"
  exit 1
fi

echo "$FETCH_OUTPUT"

#############################################
# Step 2: Extract Job IDs from NEWLY FETCHED files
#############################################
echo "[STEP 2] Extracting job IDs from newly fetched logs..."

# Extract job IDs from the file list we got from SSH
JOB_IDS=$(echo "$FILE_LIST" | grep -E '^job_[0-9]+\.json\.gz\.transform-processed' | grep -oE 'job_[0-9]+' | sed 's/job_//' | sort -u || true)

if [ -z "$JOB_IDS" ]; then
  echo ""
  echo "=========================================="
  echo "[INFO] No new jobs found in time window"
  echo "[INFO] Time window: $START_TIME → $CURRENT_TIME (UTC)"
  echo "[INFO] Next run will check from: $CURRENT_TIME"
  echo "=========================================="
  echo ""

  # Update timestamp even when no jobs (prevents checking same window repeatedly)
  echo "$CURRENT_TIME" > "$TIMESTAMP_FILE"
  echo "[SUCCESS] Batch RCA completed at $(date)"
  exit 0
fi

TOTAL_JOBS=$(echo "$JOB_IDS" | wc -l | tr -d ' ')
echo "[INFO] Found $TOTAL_JOBS job(s): $(echo $JOB_IDS | tr '\n' ' ')"

#############################################
# Step 3: Filter Already-Analyzed Jobs
#############################################
echo "[STEP 3] Filtering out already-analyzed jobs..."

# Create state file if not exists
mkdir -p "$(dirname "$STATE_FILE")"
touch "$STATE_FILE"

# Filter new jobs using comm
NEW_JOBS=$(comm -23 \
  <(echo "$JOB_IDS" | sort) \
  <(sort "$STATE_FILE") \
)

if [ -z "$NEW_JOBS" ]; then
  echo "[INFO] No new jobs to analyze (all already processed)"
  exit 0
fi

NEW_COUNT=$(echo "$NEW_JOBS" | wc -l | tr -d ' ')
echo "[INFO] Found $NEW_COUNT new job(s) to analyze: $(echo $NEW_JOBS | tr '\n' ' ')"

#############################################
# Step 4: Build Dynamic Claude Prompt
#############################################
echo "[STEP 4] Building Claude prompt for parallel RCA..."

# Convert job list to space-separated for prompt
JOBS_LIST=$(echo "$NEW_JOBS" | tr '\n' ' ' | sed 's/ $//')

# Build the orchestration prompt
read -r -d '' CLAUDE_PROMPT <<EOF || true
You are running in headless mode to analyze failed jobs in parallel.

**Job IDs to analyze:** $JOBS_LIST

**Instructions:**

1. **Spawn parallel agents** - For EACH job ID above, spawn a background agent in a SINGLE message with multiple Agent tool calls:

   Agent({
     description: "RCA for job {JOB_ID}",
     prompt: "Invoke the 'root-cause-analysis' skill for job {JOB_ID}. Use: Skill({skill: 'root-cause-analysis', args: '{JOB_ID}'}). Follow all skill instructions including Step 5 analysis and upload. Report completion status.",
     run_in_background: true
   })

   **CRITICAL:** All agents must be in ONE response for true parallelism.

2. **Wait for completion** - You'll receive task-notification for each agent when done.

3. **Aggregate results** - After all agents complete:
   - Read each job's step5_analysis_summary.json from: ~/.claude/plugins/cache/aiops-marketplace/aiops-plugin/*/skills/root-cause-analysis/.analysis/{job_id}/step5_analysis_summary.json
   - Create aggregated report with:
     * Total jobs analyzed
     * Root cause category breakdown (count by category)
     * High-priority recommendations (collect top 5 from all jobs)
     * Any failed analyses
   - Report time taken for each step (agent spawn, wait, aggregation)

4. **Save report** - Write to: $REPORT_DIR/batch_${TIMESTAMP}.json

5. **Output completion summary** - Print to stdout:
   - Number of jobs analyzed successfully
   - Number of failures (if any)
   - Report location

**Note:** The root-cause-analysis skill handles Steps 1-5 automatically, including Claude's analysis in Step 5.
EOF

#############################################
# Step 5: Execute Claude Headless
#############################################
echo "[STEP 5] Executing Claude in headless mode..."

mkdir -p "$REPORT_DIR"

# Run claude in non-interactive mode with permissions bypass for testing
# Note: -p/--print flag for non-interactive output
# Using --dangerously-skip-permissions for testing only
# Run from script directory to pick up .claude/settings.json
cd "$SCRIPT_DIR" || exit 1

claude -p --dangerously-skip-permissions "$CLAUDE_PROMPT" || {
  echo "[ERROR] Claude execution failed"
  exit 1
}

#############################################
# Step 6: Update State
#############################################
echo "[STEP 6] Updating analyzed jobs state..."

# Append new jobs to state file
echo "$NEW_JOBS" >> "$STATE_FILE"

# Keep state file sorted and deduplicated
sort -u "$STATE_FILE" -o "$STATE_FILE"

# Save timestamp ONLY AFTER successful analysis (prevents lost jobs on failure)
echo "$CURRENT_TIME" > "$TIMESTAMP_FILE"
echo "[INFO] Saved timestamp for next run: $CURRENT_TIME"

echo "[SUCCESS] Batch RCA completed at $(date)"
echo "[INFO] Report: $REPORT_DIR/batch_${TIMESTAMP}.json"
echo "[INFO] Analyzed jobs added to: $STATE_FILE"
