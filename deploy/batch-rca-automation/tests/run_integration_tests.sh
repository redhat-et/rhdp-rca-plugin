#!/usr/bin/env bash
set -euo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$TESTS_DIR/docker-compose.test.yml"

export TEST_DB_HOST="${TEST_DB_HOST:-localhost}"
export TEST_DB_PORT="${TEST_DB_PORT:-5433}"
export TEST_DB_NAME="${TEST_DB_NAME:-rca_test}"
export TEST_DB_USER="${TEST_DB_USER:-rca_test}"
export TEST_DB_PASSWORD="${TEST_DB_PASSWORD:-rca_test}"
export SOURCE_DB_TABLE="${SOURCE_DB_TABLE:-aap2_events}"

cleanup() {
  docker compose -f "$COMPOSE_FILE" down -v >/dev/null 2>&1 || true
}

if [[ "${KEEP_TEST_DB:-}" != "1" ]]; then
  trap cleanup EXIT
fi

echo "[INFO] Starting Postgres test container..."
docker compose -f "$COMPOSE_FILE" up -d --wait

echo "[INFO] Running integration tests..."
python3 -m pytest "$TESTS_DIR" -v "$@"
