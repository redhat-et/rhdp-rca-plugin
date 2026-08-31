"""Adapter interface common to all three SDKs.

Each adapter receives the same SkillRunRequest and returns the same
SkillRunResult so the harness can score them uniformly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SkillRunRequest:
    scenario_id: str
    prompt: str
    skill_path: Path
    """Path to the skill directory (containing SKILL.md). The body of SKILL.md
    will be injected as the system prompt; scripts under the directory are
    available to the agent via Bash."""

    model: str
    """SDK-agnostic model name, e.g. 'claude-sonnet-4-6'. Adapters translate
    to the SDK's expected format."""

    allowed_tools: list[str] = field(
        default_factory=lambda: ["Read", "Write", "Edit", "Bash", "Grep", "Glob"]
    )
    cwd: Path | None = None
    timeout_seconds: int = 600
    env: dict[str, str] = field(default_factory=dict)
    provider: str = "anthropic"
    """One of: 'anthropic', 'openrouter'. Adapters use this to route auth."""


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class SkillRunResult:
    adapter: str
    scenario_id: str
    model: str
    success: bool
    """True iff the SDK call completed without raising; scoring is separate."""

    final_text: str
    """Final assistant message text. Used for keyword scoring."""

    tool_calls: list[str] = field(default_factory=list)
    """Names of tools the agent invoked, in order."""

    usage: TokenUsage = field(default_factory=TokenUsage)
    native_cost_usd: float | None = None
    """Cost reported by the SDK itself, if any."""

    duration_seconds: float = 0.0
    num_turns: int = 0
    error: str | None = None
    raw: dict = field(default_factory=dict)
    """Adapter-specific extras (kept opaque so the report can dump them)."""


class Adapter(ABC):
    name: str

    @abstractmethod
    def run(self, request: SkillRunRequest) -> SkillRunResult:
        """Execute the request synchronously and return a result."""
