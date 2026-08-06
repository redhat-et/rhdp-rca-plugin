"""Adapter for the Claude Agent SDK (Python)."""

from __future__ import annotations

import asyncio
import time

from ..skill_loader import skill_body
from .base import Adapter, SkillRunRequest, SkillRunResult, TokenUsage


class ClaudeAgentAdapter(Adapter):
    name = "claude-agent-sdk"

    def run(self, request: SkillRunRequest) -> SkillRunResult:
        # asyncio.wait_for around the SDK's query() trips its anyio-based
        # subprocess cleanup and surfaces as "Claude Code returned an error
        # result: success". Skip the outer timeout; the SDK enforces its own.
        return asyncio.run(self._run(request))

    async def _run(self, request: SkillRunRequest) -> SkillRunResult:
        try:
            from claude_agent_sdk import (
                AssistantMessage,
                ClaudeAgentOptions,
                ResultMessage,
                query,
            )
        except ImportError as e:
            return _import_error(request, str(e), self.name)

        body = skill_body(request.skill_path)
        options = ClaudeAgentOptions(
            model=request.model,
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": body,
            },
            allowed_tools=request.allowed_tools,
            cwd=str(request.cwd or request.skill_path.parent),
            permission_mode="bypassPermissions",
        )

        started = time.monotonic()
        tool_calls: list[str] = []
        final_text_parts: list[str] = []
        usage = TokenUsage()
        cost: float | None = None
        num_turns = 0
        error: str | None = None
        result_raw: dict = {}

        try:
            async for message in query(prompt=request.prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if hasattr(block, "text"):
                            final_text_parts.append(block.text)
                        if hasattr(block, "name") and getattr(block, "type", "") == "tool_use":
                            tool_calls.append(block.name)
                elif isinstance(message, ResultMessage):
                    if message.usage:
                        usage = TokenUsage(
                            input_tokens=message.usage.get("input_tokens", 0),
                            output_tokens=message.usage.get("output_tokens", 0),
                            cache_read_tokens=message.usage.get("cache_read_input_tokens", 0),
                            cache_write_tokens=message.usage.get("cache_creation_input_tokens", 0),
                        )
                    cost = message.total_cost_usd
                    num_turns = message.num_turns
                    if message.result:
                        final_text_parts = [message.result]
                    result_raw = {
                        "subtype": message.subtype,
                        "stop_reason": message.stop_reason,
                        "duration_api_ms": message.duration_api_ms,
                    }
                    if message.is_error:
                        error = "; ".join(message.errors or []) or message.subtype
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        duration = time.monotonic() - started
        return SkillRunResult(
            adapter=self.name,
            scenario_id=request.scenario_id,
            model=request.model,
            success=error is None,
            final_text="\n".join(final_text_parts),
            tool_calls=tool_calls,
            usage=usage,
            native_cost_usd=cost,
            duration_seconds=duration,
            num_turns=num_turns,
            error=error,
            raw=result_raw,
        )


def _import_error(request: SkillRunRequest, msg: str, name: str) -> SkillRunResult:
    return SkillRunResult(
        adapter=name,
        scenario_id=request.scenario_id,
        model=request.model,
        success=False,
        final_text="",
        error=f"claude_agent_sdk not installed: {msg}. Install with `pip install claude-agent-sdk`.",
    )
