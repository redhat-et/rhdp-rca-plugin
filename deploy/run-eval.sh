#!/bin/bash
set -euo pipefail

POD_NAME="${POD_NAME:-claude-rca-eval}"
REPO_URL="${REPO_URL:-https://github.com/redhat-et/rhdp-rca-plugin.git}"
BRANCH="${BRANCH:-main}"

ALLOWED_TOOLS=(
  "Bash(python3*)" "Bash(python3 *)"
  "Bash(.venv/bin/python *)" "Bash(.venv/bin/pip *)"
  "Bash(mkdir *)" "Bash(mkdir -p *)"
  "Bash(cp *)" "Bash(ls *)" "Bash(cat *)"
  "Bash(ssh *)" "Bash(rsync *)" "Bash(chmod *)"
  "Bash(gzip *)" "Bash(gunzip *)"
  "Bash(head *)" "Bash(tail *)" "Bash(wc *)"
  "Bash(find *)" "Bash(grep *)" "Bash(jq *)"
  "Read" "Write" "Glob" "Grep"
)

usage() {
  echo "Usage: $0 <prompt>"
  echo ""
  echo "Run a Claude Code prompt inside the OpenShift eval pod."
  echo ""
  echo "Examples:"
  echo "  $0 'Investigate why job <JOB_ID> failed'"
  echo "  $0 'Analyze the root cause of the failure in extracted_log/job_<JOB_ID>.json.gz.transform-processed'"
  echo ""
  echo "Environment variables:"
  echo "  POD_NAME   Pod name (default: claude-rca-eval)"
  echo "  REPO_URL   Git repo to clone (default: redhat-et/rhdp-rca-plugin)"
  echo "  BRANCH     Branch to checkout (default: main)"
  exit 1
}

if [ $# -eq 0 ]; then
  usage
fi

PROMPT="$1"

echo "==> Checking pod status..."
oc wait "pod/${POD_NAME}" --for=condition=Ready --timeout=60s

echo "==> Cloning repo (branch: ${BRANCH})..."
oc exec "${POD_NAME}" -- bash -c \
  "rm -rf /workspace/rhdp-rca-plugin && git clone --branch ${BRANCH} ${REPO_URL} /workspace/rhdp-rca-plugin"

echo "==> Running eval..."
oc exec "${POD_NAME}" -- bash -c \
  "cd /workspace/rhdp-rca-plugin && claude -p '${PROMPT}' \
    --allowedTools $(printf '"%s" ' "${ALLOWED_TOOLS[@]}") \
    --output-format json"
