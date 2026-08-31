"""Scenario schema + loader."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Scenario:
    id: str
    description: str
    prompt: str
    expected_keywords: list[str] = field(default_factory=list)
    expected_tool_calls: list[str] = field(default_factory=list)
    forbidden_tool_calls: list[str] = field(default_factory=list)
    max_cost_usd: float = 5.0
    timeout_seconds: int = 600


def load_scenarios(path: Path) -> list[Scenario]:
    with path.open() as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, list):
        raise ValueError(f"{path} must contain a YAML list of scenarios")
    scenarios = []
    for entry in raw:
        scenarios.append(Scenario(**entry))
    return scenarios
