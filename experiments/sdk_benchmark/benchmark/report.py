"""Render benchmark results as Markdown + JSON."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from statistics import mean

from .adapters.base import SkillRunResult
from .scenarios import Scenario
from .scoring import Score


def write_reports(
    out_dir: Path,
    rows: list[tuple[Scenario, SkillRunResult, Score, float | None]],
    model: str,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    json_path = out_dir / f"results-{ts}.json"
    md_path = out_dir / f"results-{ts}.md"

    json_path.write_text(json.dumps(_json_payload(rows, model), indent=2, default=str))
    md_path.write_text(_markdown(rows, model))
    return md_path, json_path


def _json_payload(rows, model: str) -> dict:
    return {
        "model": model,
        "timestamp_utc": datetime.utcnow().isoformat() + "Z",
        "runs": [
            {
                "scenario": asdict(scenario),
                "result": _result_to_dict(result),
                "score": asdict(score_obj),
                "cost_usd": cost,
            }
            for scenario, result, score_obj, cost in rows
        ],
    }


def _result_to_dict(result: SkillRunResult) -> dict:
    return {
        "adapter": result.adapter,
        "scenario_id": result.scenario_id,
        "model": result.model,
        "success_adapter_level": result.success,
        "final_text": result.final_text,
        "tool_calls": result.tool_calls,
        "usage": asdict(result.usage),
        "native_cost_usd": result.native_cost_usd,
        "duration_seconds": result.duration_seconds,
        "num_turns": result.num_turns,
        "error": result.error,
        "raw": result.raw,
    }


def _markdown(rows, model: str) -> str:
    by_adapter: dict[str, list[tuple[Scenario, SkillRunResult, Score, float | None]]] = {}
    for row in rows:
        by_adapter.setdefault(row[1].adapter, []).append(row)

    lines = [
        "# SDK benchmark — root-cause-analysis",
        "",
        f"- **Model**: `{model}`",
        f"- **Timestamp (UTC)**: {datetime.utcnow().isoformat()}Z",
        f"- **Scenarios**: {len({r[0].id for r in rows})}",
        f"- **Adapters**: {', '.join(sorted(by_adapter))}",
        "",
        "## Summary",
        "",
        "| Adapter | Runs | Success rate | Avg cost (USD) | Avg duration (s) | Avg turns |",
        "|---|---|---|---|---|---|",
    ]

    for adapter, items in sorted(by_adapter.items()):
        n = len(items)
        success_rate = sum(1 for _, _, s, _ in items if s.success) / n
        costs = [c for _, _, _, c in items if c is not None]
        avg_cost = mean(costs) if costs else float("nan")
        avg_duration = mean(r.duration_seconds for _, r, _, _ in items)
        avg_turns = mean(r.num_turns for _, r, _, _ in items)
        lines.append(
            f"| `{adapter}` | {n} | {success_rate:.0%} | {avg_cost:.4f} | {avg_duration:.1f} | {avg_turns:.1f} |"
        )

    lines.extend(["", "## Per-scenario detail", ""])
    for adapter in sorted(by_adapter):
        lines.append(f"### `{adapter}`")
        lines.append("")
        lines.append(
            "| Scenario | Success | Cost (USD) | Duration (s) | Turns | Keywords hit | Notes |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        for scenario, result, score_obj, cost in by_adapter[adapter]:
            cost_str = f"{cost:.4f}" if cost is not None else "—"
            kw = f"{len(score_obj.keyword_hits)}/{len(scenario.expected_keywords)}"
            notes = "; ".join(score_obj.notes) or "—"
            lines.append(
                f"| {scenario.id} | {'✅' if score_obj.success else '❌'} | {cost_str} | "
                f"{result.duration_seconds:.1f} | {result.num_turns} | {kw} | {notes} |"
            )
        lines.append("")

    return "\n".join(lines)
