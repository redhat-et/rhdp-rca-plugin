"""Score a SkillRunResult against a Scenario."""

from __future__ import annotations

from dataclasses import dataclass

from .adapters.base import SkillRunResult
from .scenarios import Scenario


@dataclass
class Score:
    completed: bool
    """Adapter returned without an SDK-level error."""

    keyword_hits: list[str]
    keyword_misses: list[str]
    tool_hits: list[str]
    tool_misses: list[str]
    forbidden_tool_calls: list[str]
    over_budget: bool
    success: bool
    """Composite: completed AND all expected keywords found AND all expected tools called AND no forbidden tools AND under budget."""

    notes: list[str]


def score(result: SkillRunResult, scenario: Scenario, cost_usd: float | None) -> Score:
    text = result.final_text.lower()
    keyword_hits = [k for k in scenario.expected_keywords if k.lower() in text]
    keyword_misses = [k for k in scenario.expected_keywords if k.lower() not in text]

    called = set(result.tool_calls)
    tool_hits = [t for t in scenario.expected_tool_calls if t in called]
    tool_misses = [t for t in scenario.expected_tool_calls if t not in called]
    forbidden = [t for t in scenario.forbidden_tool_calls if t in called]

    over_budget = cost_usd is not None and cost_usd > scenario.max_cost_usd

    notes: list[str] = []
    if result.error:
        notes.append(f"adapter error: {result.error}")
    if keyword_misses:
        notes.append(f"missing keywords: {', '.join(keyword_misses)}")
    if tool_misses:
        notes.append(f"missing expected tools: {', '.join(tool_misses)}")
    if forbidden:
        notes.append(f"called forbidden tools: {', '.join(forbidden)}")
    if over_budget:
        notes.append(f"cost ${cost_usd:.4f} exceeded budget ${scenario.max_cost_usd:.4f}")

    success = bool(
        result.error is None
        and not keyword_misses
        and not tool_misses
        and not forbidden
        and not over_budget
    )

    return Score(
        completed=result.error is None,
        keyword_hits=keyword_hits,
        keyword_misses=keyword_misses,
        tool_hits=tool_hits,
        tool_misses=tool_misses,
        forbidden_tool_calls=forbidden,
        over_budget=over_budget,
        success=success,
        notes=notes,
    )
