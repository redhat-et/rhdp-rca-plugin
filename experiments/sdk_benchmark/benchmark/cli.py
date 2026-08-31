"""Benchmark CLI.

Usage:
    python -m benchmark.cli run \\
        --scenarios benchmark/scenarios.yaml \\
        --skill ../../skills/root-cause-analysis \\
        --model claude-sonnet-4-6 \\
        --adapters claude-agent-sdk,opencode,pi
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .adapters.base import Adapter, SkillRunRequest
from .adapters.claude_agent import ClaudeAgentAdapter
from .adapters.opencode import OpenCodeAdapter
from .adapters.pi import PiAdapter
from .pricing import cost_from_tokens
from .report import write_reports
from .scenarios import load_scenarios
from .scoring import score

ADAPTERS: dict[str, type[Adapter]] = {
    "claude-agent-sdk": ClaudeAgentAdapter,
    "opencode": OpenCodeAdapter,
    "pi": PiAdapter,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="benchmark")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the benchmark")
    run.add_argument("--scenarios", type=Path, required=True)
    run.add_argument(
        "--skill",
        type=Path,
        required=True,
        help="Path to the skill directory (containing SKILL.md)",
    )
    run.add_argument("--model", default="claude-sonnet-4-6")
    run.add_argument(
        "--adapters",
        default="claude-agent-sdk,opencode,pi",
        help="Comma-separated subset of adapters to run.",
    )
    run.add_argument(
        "--provider",
        default="anthropic",
        choices=["anthropic", "openrouter"],
        help="Routes auth + model naming. 'openrouter' expects OPENROUTER_API_KEY in env.",
    )
    run.add_argument(
        "--out-dir", type=Path, default=Path(__file__).resolve().parent.parent / "results"
    )
    run.add_argument(
        "--cwd",
        type=Path,
        default=None,
        help="Working directory for the agent. Defaults to skill parent.",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip SDK calls; just verify scenarios load and adapters are reachable.",
    )

    args = parser.parse_args(argv)
    if args.command != "run":
        parser.error("unknown command")

    scenarios = load_scenarios(args.scenarios)
    adapter_names = [a.strip() for a in args.adapters.split(",") if a.strip()]
    unknown = [a for a in adapter_names if a not in ADAPTERS]
    if unknown:
        parser.error(f"unknown adapter(s): {unknown}. Choose from {list(ADAPTERS)}")

    if args.dry_run:
        print(f"Loaded {len(scenarios)} scenarios:")
        for s in scenarios:
            print(f"  - {s.id}: {s.description}")
        print(f"Selected adapters: {adapter_names}")
        print(f"Skill directory: {args.skill.resolve()}")
        return 0

    if not (args.skill / "SKILL.md").exists():
        parser.error(f"no SKILL.md at {args.skill}")

    if args.provider == "openrouter":
        _wire_openrouter_env()

    rows = []
    for adapter_name in adapter_names:
        adapter = ADAPTERS[adapter_name]()
        for scenario in scenarios:
            print(f"[{adapter_name}] running {scenario.id}…", flush=True)
            request = SkillRunRequest(
                scenario_id=scenario.id,
                prompt=scenario.prompt,
                skill_path=args.skill,
                model=args.model,
                cwd=args.cwd,
                timeout_seconds=scenario.timeout_seconds,
                provider=args.provider,
            )
            try:
                result = adapter.run(request)
            except Exception as exc:
                # An unhandled adapter crash shouldn't sink the whole matrix —
                # synthesize a failure row and keep going.
                from .adapters.base import SkillRunResult

                result = SkillRunResult(
                    adapter=adapter_name,
                    scenario_id=scenario.id,
                    model=args.model,
                    success=False,
                    final_text="",
                    error=f"adapter raised: {type(exc).__name__}: {exc}",
                )
            cost = result.native_cost_usd
            if cost is None:
                cost = cost_from_tokens(
                    args.model,
                    result.usage.input_tokens,
                    result.usage.output_tokens,
                    result.usage.cache_read_tokens,
                    result.usage.cache_write_tokens,
                )
            score_obj = score(result, scenario, cost)
            rows.append((scenario, result, score_obj, cost))
            status = "OK" if score_obj.success else "FAIL"
            cost_str = f"${cost:.4f}" if cost is not None else "(no cost)"
            print(
                f"  → {status} duration={result.duration_seconds:.1f}s cost={cost_str}", flush=True
            )

    md_path, json_path = write_reports(args.out_dir, rows, args.model)
    print(f"\nReports written:\n  - {md_path}\n  - {json_path}")
    return 0


def _wire_openrouter_env() -> None:
    """Route Claude Agent SDK at OpenRouter via ANTHROPIC_BASE_URL.

    OpenCode and Pi pick up the provider from request.provider; they need
    OPENROUTER_API_KEY in env, which the user already provides.

    We unconditionally overwrite ANTHROPIC_* env vars: when this harness runs
    inside another Claude Code process, those are pre-set to the parent's own
    routing and would otherwise leak into the spawned `claude` subprocess.
    """
    import os

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("--provider openrouter requires OPENROUTER_API_KEY in env")
    os.environ["ANTHROPIC_BASE_URL"] = "https://openrouter.ai/api"
    os.environ["ANTHROPIC_AUTH_TOKEN"] = key
    # Per OpenRouter's Claude Code guide, blank ANTHROPIC_API_KEY so the SDK
    # uses ANTHROPIC_AUTH_TOKEN instead.
    os.environ["ANTHROPIC_API_KEY"] = ""


if __name__ == "__main__":
    sys.exit(main())
