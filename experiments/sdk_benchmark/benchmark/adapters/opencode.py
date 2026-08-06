"""Adapter for the OpenCode SDK (delegates to a Node runner)."""

from __future__ import annotations

from pathlib import Path

from ._subprocess import run_node_adapter
from .base import Adapter, SkillRunRequest, SkillRunResult

RUNNER = Path(__file__).resolve().parents[1] / "runners" / "opencode_runner.mjs"


class OpenCodeAdapter(Adapter):
    name = "opencode"

    def run(self, request: SkillRunRequest) -> SkillRunResult:
        return run_node_adapter(self.name, RUNNER, request)
