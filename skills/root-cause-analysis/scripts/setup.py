"""Preflight check for root-cause-analysis skill prerequisites."""

import json
import os
import shutil
import subprocess
from pathlib import Path

# Placeholder pattern — values that haven't been configured yet
PLACEHOLDER_PATTERN = "<"


def is_placeholder(value: str | None) -> bool:
    """Check if a value is a placeholder or unset."""
    if not value:
        return True
    return value.strip().startswith(PLACEHOLDER_PATTERN)


def check_python_venv(base_dir: Path) -> dict:
    """Check if Python venv exists and dependencies are installed."""
    venv_dir = base_dir / ".venv"
    if not venv_dir.exists():
        return {
            "name": "Python venv",
            "status": "missing",
            "message": ".venv not found. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt",
        }

    pip = venv_dir / "bin" / "pip"
    if not pip.exists():
        return {
            "name": "Python venv",
            "status": "error",
            "message": ".venv exists but pip not found. Recreate: rm -rf .venv && python3 -m venv .venv",
        }

    # Check if key dependencies are installed
    try:
        result = subprocess.run(
            [str(pip), "show", "requests", "python-dotenv", "PyYAML"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return {
                "name": "Python venv",
                "status": "missing",
                "message": "Dependencies not installed. Run: .venv/bin/pip install -r requirements.txt",
            }
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {
            "name": "Python venv",
            "status": "error",
            "message": "Could not check dependencies",
        }

    return {"name": "Python venv", "status": "ok", "message": f"{venv_dir}"}


def check_job_logs_dir() -> dict:
    """Check JOB_LOGS_DIR configuration."""
    value = os.environ.get("JOB_LOGS_DIR", "")
    if is_placeholder(value):
        return {
            "name": "JOB_LOGS_DIR",
            "status": "missing",
            "message": "Not configured. Set in .claude/settings.json",
            "env_vars": [
                {"name": "JOB_LOGS_DIR", "prompt": "Local directory path for job log files"}
            ],
            "configurable": True,
        }

    path = Path(value)
    if not path.exists():
        return {
            "name": "JOB_LOGS_DIR",
            "status": "error",
            "message": f"Directory does not exist: {value}",
            "env_vars": [
                {
                    "name": "JOB_LOGS_DIR",
                    "prompt": "Local directory path for job log files",
                    "current": value,
                }
            ],
            "configurable": True,
        }

    return {"name": "JOB_LOGS_DIR", "status": "ok", "message": value}


def _ssh_host_exists(alias: str) -> bool:
    """Check if an SSH host alias exists in ~/.ssh/config."""
    ssh_config_path = Path.home() / ".ssh" / "config"
    if not ssh_config_path.exists():
        return False
    try:
        with open(ssh_config_path) as f:
            for line in f:
                stripped = line.strip()
                if stripped.lower().startswith("host "):
                    aliases = stripped.split()[1:]
                    if alias in aliases:
                        return True
    except OSError:
        pass
    return False


def check_ssh() -> dict:
    """Check SSH remote configuration."""
    host = os.environ.get("REMOTE_HOST", "")
    remote_dir = os.environ.get("REMOTE_DIR", "")

    env_vars = [
        {"name": "REMOTE_HOST", "prompt": "SSH host alias (from ~/.ssh/config)"},
        {"name": "REMOTE_DIR", "prompt": "Remote directory path containing job logs"},
    ]

    if is_placeholder(host) or is_placeholder(remote_dir):
        missing = []
        if is_placeholder(host):
            missing.append("REMOTE_HOST")
        if is_placeholder(remote_dir):
            missing.append("REMOTE_DIR")
        return {
            "name": "SSH (log fetch)",
            "status": "missing",
            "message": f"Not configured: {', '.join(missing)}. Set in .claude/settings.json",
            "env_vars": [v for v in env_vars if v["name"] in missing],
            "configurable": True,
            "ssh_setup_needed": True,
        }

    # Check if the configured host alias exists in SSH config
    if not _ssh_host_exists(host):
        return {
            "name": "SSH (log fetch)",
            "status": "error",
            "message": f"REMOTE_HOST '{host}' not found in ~/.ssh/config",
            "env_vars": env_vars,
            "configurable": True,
            "ssh_setup_needed": True,
        }

    # Check rsync is available
    if not shutil.which("rsync"):
        return {
            "name": "SSH (log fetch)",
            "status": "error",
            "message": "rsync not found in PATH",
        }

    return {
        "name": "SSH (log fetch)",
        "status": "ok",
        "message": f"host={host}, dir={remote_dir}",
    }


def check_splunk() -> dict:
    """Check Splunk configuration."""
    host = os.environ.get("SPLUNK_HOST", "")
    username = os.environ.get("SPLUNK_USERNAME", "")
    password = os.environ.get("SPLUNK_PASSWORD", "")
    token = os.environ.get("SPLUNK_TOKEN", "")

    env_vars_host = [
        {"name": "SPLUNK_HOST", "prompt": "Splunk API URL (e.g. https://splunk.example.com:8089)"}
    ]
    env_vars_auth = [
        {"name": "SPLUNK_USERNAME", "prompt": "Splunk username"},
        {"name": "SPLUNK_PASSWORD", "prompt": "Splunk password"},
        {"name": "SPLUNK_INDEX", "prompt": "Splunk index name (optional)", "optional": True},
        {
            "name": "SPLUNK_VERIFY_SSL",
            "prompt": "Verify SSL certificates (true/false)",
            "optional": True,
            "default": "false",
        },
    ]

    if is_placeholder(host):
        return {
            "name": "Splunk",
            "status": "missing",
            "message": "SPLUNK_HOST not configured. Set in .claude/settings.json",
            "env_vars": env_vars_host + env_vars_auth,
            "configurable": True,
        }

    has_basic = not is_placeholder(username) and not is_placeholder(password)
    has_token = not is_placeholder(token)

    if not has_basic and not has_token:
        return {
            "name": "Splunk",
            "status": "missing",
            "message": "No auth configured. Set SPLUNK_USERNAME/SPLUNK_PASSWORD or SPLUNK_TOKEN in .claude/settings.json",
            "env_vars": env_vars_auth,
            "configurable": True,
        }

    auth_method = "basic" if has_basic else "token"
    return {
        "name": "Splunk",
        "status": "ok",
        "message": f"host={host}, auth={auth_method}",
    }


def check_github() -> dict:
    """Check GitHub token configuration."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if is_placeholder(token):
        return {
            "name": "GitHub token",
            "status": "missing",
            "message": "GITHUB_TOKEN not configured. Set in .claude/settings.json",
            "env_vars": [
                {
                    "name": "GITHUB_TOKEN",
                    "prompt": "GitHub personal access token (for fetching configs)",
                }
            ],
            "configurable": True,
        }

    return {"name": "GitHub token", "status": "ok", "message": "configured"}


def check_github_mcp(repo_root: Path) -> dict:
    """Check GitHub MCP server configuration."""
    mcp_path = repo_root / ".mcp.json"
    if not mcp_path.exists():
        return {
            "name": "GitHub MCP",
            "status": "missing",
            "message": ".mcp.json not found at repo root",
        }

    try:
        with open(mcp_path) as f:
            mcp_config = json.load(f)
        servers = mcp_config.get("mcpServers", {})
        if "github" not in servers:
            return {
                "name": "GitHub MCP",
                "status": "missing",
                "message": "No 'github' server in .mcp.json",
            }
    except (json.JSONDecodeError, OSError) as e:
        return {
            "name": "GitHub MCP",
            "status": "error",
            "message": f"Could not read .mcp.json: {e}",
        }

    return {"name": "GitHub MCP", "status": "ok", "message": "configured"}


def check_mlflow() -> dict:
    """Check MLFlow tracing configuration."""
    enabled = os.environ.get("MLFLOW_CLAUDE_TRACING_ENABLED", "")
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "")
    experiment = os.environ.get("MLFLOW_EXPERIMENT_NAME", "")
    jumpbox = os.environ.get("JUMPBOX_URI", "")

    env_vars = []
    issues = []
    if enabled.lower() != "true":
        issues.append("MLFLOW_CLAUDE_TRACING_ENABLED not set to 'true'")
        env_vars.append(
            {
                "name": "MLFLOW_CLAUDE_TRACING_ENABLED",
                "prompt": "Enable MLFlow tracing (true/false)",
                "default": "true",
            }
        )
    if is_placeholder(tracking_uri):
        issues.append("MLFLOW_TRACKING_URI not configured")
        env_vars.append(
            {
                "name": "MLFLOW_TRACKING_URI",
                "prompt": "MLFlow tracking server URL (e.g. http://127.0.0.1:5000)",
            }
        )
    if is_placeholder(experiment):
        issues.append("EXPERIMENT_NAME not configured")
        env_vars.append(
            {"name": "MLFLOW_EXPERIMENT_NAME", "prompt": "MLFlow experiment name for tracing"}
        )
    if is_placeholder(jumpbox):
        issues.append("JUMPBOX_URI not configured")
        env_vars.append(
            {"name": "JUMPBOX_URI", "prompt": "SSH tunnel jumpbox (e.g. user@jumpbox-host -p port)"}
        )

    if issues:
        return {
            "name": "MLFlow",
            "status": "missing",
            "message": "; ".join(issues) + ". Set in .claude/settings.json",
            "env_vars": env_vars,
            "configurable": True,
        }

    return {
        "name": "MLFlow",
        "status": "ok",
        "message": f"uri={tracking_uri}, experiment={experiment}, jumpbox={jumpbox}",
    }


def _is_server_reachable(tracking_uri: str) -> bool:
    """Check if MLFlow server is reachable by verifying the response is actually MLFlow."""
    try:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            f"{tracking_uri}/api/2.0/mlflow/experiments/search", method="GET"
        )
        resp = urllib.request.urlopen(req, timeout=5)
        body = resp.read().decode("utf-8", errors="replace")
        return "mlflow" in body.lower() or "experiments" in body.lower()
    except urllib.error.HTTPError as e:
        # MLFlow returns error JSON with "error_code" — check for it
        try:
            body = e.read().decode("utf-8", errors="replace")
            return "error_code" in body or "mlflow" in body.lower()
        except Exception:
            return False
    except Exception:
        return False


def _parse_uri_port(tracking_uri: str) -> str | None:
    """Extract port from a tracking URI like http://127.0.0.1:5000."""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(tracking_uri)
        return str(parsed.port) if parsed.port else None
    except Exception:
        return None


def _tunnel_already_running(port: str) -> bool:
    """Check if an SSH tunnel is already running for the given port."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"ssh.*-L.*{port}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _start_ssh_tunnel(tracking_uri: str, jumpbox_uri: str) -> dict:
    """Attempt to start an SSH tunnel to the MLFlow server.

    Returns a dict with status and message.
    """
    port = _parse_uri_port(tracking_uri)
    if not port:
        return {"started": False, "message": f"Could not parse port from {tracking_uri}"}

    if _tunnel_already_running(port):
        return {"started": False, "message": f"Tunnel already running on port {port}"}

    try:
        cmd = f"ssh -f -N -L {port}:127.0.0.1:{port} {jumpbox_uri}"
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return {"started": False, "message": f"SSH tunnel failed: {result.stderr.strip()}"}
        return {"started": True, "message": f"Tunnel started on port {port} via {jumpbox_uri}"}
    except subprocess.TimeoutExpired:
        return {"started": False, "message": "SSH tunnel timed out"}
    except Exception as e:
        return {"started": False, "message": f"SSH tunnel error: {e}"}


def _kill_stale_tunnel(port: str) -> None:
    """Kill any existing SSH tunnel process for the given port."""
    try:
        subprocess.run(
            ["pkill", "-f", f"ssh.*-L.*{port}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        import time

        time.sleep(1)
    except Exception:
        pass


def check_mlflow_server() -> dict:
    """Check if MLFlow server is reachable. If not, attempt to start SSH tunnel."""
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "")
    if is_placeholder(tracking_uri):
        return {
            "name": "MLFlow server",
            "status": "missing",
            "message": "MLFLOW_TRACKING_URI not configured",
        }

    # First check: is it already reachable?
    reachable = _is_server_reachable(tracking_uri)

    if not reachable:
        # Not reachable — try to start SSH tunnel if JUMPBOX_URI is configured
        jumpbox = os.environ.get("JUMPBOX_URI", "")
        if is_placeholder(jumpbox):
            return {
                "name": "MLFlow server",
                "status": "error",
                "message": f"Cannot reach {tracking_uri}. Set JUMPBOX_URI in .claude/settings.json for SSH tunnel",
            }

        # Kill any stale tunnel before starting a fresh one
        port = _parse_uri_port(tracking_uri)
        if port and _tunnel_already_running(port):
            _kill_stale_tunnel(port)

        # Attempt tunnel startup
        tunnel_result = _start_ssh_tunnel(tracking_uri, jumpbox)

        if tunnel_result["started"]:
            import time

            time.sleep(2)
            reachable = _is_server_reachable(tracking_uri)

        if not reachable:
            return {
                "name": "MLFlow server",
                "status": "error",
                "message": f"Cannot reach {tracking_uri}. Tunnel: {tunnel_result['message']}",
            }

    return {
        "name": "MLFlow server",
        "status": "ok",
        "message": f"reachable at {tracking_uri}",
    }


def check_session_hook(repo_root: Path) -> dict:
    """Check if session-start hook is configured."""
    hook_path = repo_root / ".claude" / "hooks" / "session-start.sh"
    if not hook_path.exists():
        return {
            "name": "Session hook",
            "status": "missing",
            "message": ".claude/hooks/session-start.sh not found",
        }

    # Check if settings.json has the SessionStart hook registered
    settings_path = repo_root / ".claude" / "settings.json"
    if settings_path.exists():
        try:
            with open(settings_path) as f:
                settings = json.load(f)
            hooks = settings.get("hooks", {})
            if "SessionStart" in hooks:
                return {
                    "name": "Session hook",
                    "status": "ok",
                    "message": "registered in settings.json",
                }
        except (json.JSONDecodeError, OSError):
            pass

    # Also check settings.local.json
    local_settings_path = repo_root / ".claude" / "settings.local.json"
    if local_settings_path.exists():
        try:
            with open(local_settings_path) as f:
                settings = json.load(f)
            hooks = settings.get("hooks", {})
            if "SessionStart" in hooks:
                return {
                    "name": "Session hook",
                    "status": "ok",
                    "message": "registered in settings.local.json",
                }
        except (json.JSONDecodeError, OSError):
            pass

    return {
        "name": "Session hook",
        "status": "missing",
        "message": "SessionStart hook not registered in settings. See settings.example.json",
    }


def run_checks(base_dir: Path, repo_root: Path | None = None) -> list[dict]:
    """Run all preflight checks and return results."""
    if repo_root is None:
        repo_root = base_dir.parent

    return [
        check_python_venv(base_dir),
        check_job_logs_dir(),
        check_ssh(),
        check_splunk(),
        check_github(),
        check_github_mcp(repo_root),
        check_mlflow(),
        check_mlflow_server(),
        check_session_hook(repo_root),
    ]


def print_checks(results: list[dict]) -> int:
    """Print check results in a human-readable format. Returns count of issues."""
    icons = {"ok": "[ok]", "missing": "[!!]", "error": "[!!]"}
    issues = 0

    print()
    print("RCA Skill Setup Status")
    print("=" * 60)

    for r in results:
        icon = icons.get(r["status"], "[??]")
        pad = 20 - len(r["name"])
        print(f"  {icon} {r['name']}{' ' * pad} {r['message']}")
        if r["status"] != "ok":
            issues += 1

    print()
    if issues == 0:
        print("All checks passed.")
    else:
        print(f"{issues} issue(s) found. Configure values in .claude/settings.json")
        print("See .claude/settings.example.json for a template.")

    print()
    return issues
