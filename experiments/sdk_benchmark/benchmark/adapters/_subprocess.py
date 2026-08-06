"""Shared logic for adapters that delegate to a Node.js runner subprocess.

The contract:
- We spawn `node <runner>` with the request as JSON on stdin.
- The runner emits a single JSON object on stdout with the result.
- Stderr is captured and surfaced if the runner exits non-zero.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from ..skill_loader import skill_body
from .base import SkillRunRequest, SkillRunResult, TokenUsage


def run_node_adapter(
    name: str,
    runner_path: Path,
    request: SkillRunRequest,
) -> SkillRunResult:
    body = skill_body(request.skill_path)
    payload = {
        "prompt": request.prompt,
        "systemPrompt": body,
        "model": request.model,
        "cwd": str(request.cwd or request.skill_path.parent),
        "allowedTools": request.allowed_tools,
        "timeoutSeconds": request.timeout_seconds,
        "provider": request.provider,
    }

    started = time.monotonic()
    # Prepend our local node_modules/.bin to PATH so the SDK can spawn the
    # bundled `opencode` binary without a global install.
    benchmark_root = Path(__file__).resolve().parents[2]
    local_bin = benchmark_root / "node_modules" / ".bin"
    env = _runner_env(request)
    env["PATH"] = f"{local_bin}{':' if env.get('PATH') else ''}{env.get('PATH', '')}"
    try:
        proc = subprocess.run(
            ["node", str(runner_path)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
            timeout=request.timeout_seconds + 30,
        )
    except FileNotFoundError:
        return _error(
            name,
            request,
            "node executable not found. Install Node 18+ and ensure `node` is on PATH.",
        )
    except subprocess.TimeoutExpired:
        return _error(name, request, f"runner exceeded timeout ({request.timeout_seconds}s)")

    duration = time.monotonic() - started

    if proc.returncode != 0:
        return _error(
            name,
            request,
            f"runner exited {proc.returncode}: {proc.stderr.strip()[:2000]}",
            duration=duration,
        )

    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return _error(
            name,
            request,
            f"could not parse runner output. stdout={proc.stdout[:500]!r} stderr={proc.stderr[:500]!r}",
            duration=duration,
        )

    usage_raw = data.get("usage") or {}
    return SkillRunResult(
        adapter=name,
        scenario_id=request.scenario_id,
        model=request.model,
        success=data.get("error") is None,
        final_text=data.get("finalText", ""),
        tool_calls=data.get("toolCalls", []),
        usage=TokenUsage(
            input_tokens=usage_raw.get("input", 0),
            output_tokens=usage_raw.get("output", 0),
            cache_read_tokens=usage_raw.get("cacheRead", 0),
            cache_write_tokens=usage_raw.get("cacheWrite", 0),
        ),
        native_cost_usd=data.get("nativeCostUsd"),
        duration_seconds=data.get("durationSeconds", duration),
        num_turns=data.get("numTurns", 0),
        error=data.get("error"),
        raw=data.get("raw") or {},
    )


def _runner_env(request: SkillRunRequest) -> dict[str, str]:
    import os

    env = os.environ.copy()
    env.update(request.env)
    return env


def _error(name: str, request: SkillRunRequest, msg: str, duration: float = 0.0) -> SkillRunResult:
    return SkillRunResult(
        adapter=name,
        scenario_id=request.scenario_id,
        model=request.model,
        success=False,
        final_text="",
        duration_seconds=duration,
        error=msg,
    )
