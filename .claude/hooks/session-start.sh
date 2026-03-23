#!/bin/bash
INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id')

if [ -n "$CLAUDE_ENV_FILE" ]; then
  echo "export CLAUDE_SESSION_ID='$SESSION_ID'" >> "$CLAUDE_ENV_FILE"
fi

# MLflow setup (optional - only runs if MLFLOW_PORT is configured)
if [ -n "$MLFLOW_PORT" ]; then
  if [ -n "$CLAUDE_ENV_FILE" ]; then
    echo "export MLFLOW_TAG_USER='$MLFLOW_TAG_USER'" >> "$CLAUDE_ENV_FILE"
    echo "export MLFLOW_TRACKING_URI='http://127.0.0.1:$MLFLOW_PORT'" >> "$CLAUDE_ENV_FILE"
    echo "export MLFLOW_EXPERIMENT_NAME='$MLFLOW_EXPERIMENT_NAME'" >> "$CLAUDE_ENV_FILE"
    echo "export MLFLOW_CLAUDE_TRACING_ENABLED='$MLFLOW_CLAUDE_TRACING_ENABLED'" >> "$CLAUDE_ENV_FILE"
  fi

  # Start SSH tunnel if not already running
  if ! lsof -i :"$MLFLOW_PORT" >/dev/null 2>&1; then
    if [ -n "$JUMPBOX_URI" ]; then
      ssh -f -N -L "$MLFLOW_PORT":localhost:5000 $JUMPBOX_URI
      sleep 5
    fi
  fi

  # Setup Python environment and MLflow
  VENV_DIR="$HOME/.claude/mlflow/.venv"
  if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
  fi
  source "$VENV_DIR/bin/activate"

  if ! pip show mlflow >/dev/null 2>&1; then
    pip install mlflow
  fi

  mlflow experiments create -n "$MLFLOW_EXPERIMENT_NAME" 2>/dev/null || true
  mlflow autolog claude -u "$MLFLOW_TRACKING_URI" -n "$MLFLOW_EXPERIMENT_NAME" -t "$MLFLOW_CLAUDE_TRACING_ENABLED"
fi
