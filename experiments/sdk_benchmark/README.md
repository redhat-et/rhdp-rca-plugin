# SDK benchmark — root-cause-analysis

A runnable, apples-to-apples benchmark that drives the `skills/root-cause-analysis`
skill from three SDKs and measures success rate, cost, and wall time:

- **Claude Agent SDK** (Python, native — loads SKILL.md content as a system-prompt addendum)
- **OpenCode SDK** (TypeScript, via a Node runner the Python harness shells out to)
- **Pi Coding Agent SDK** (TypeScript, via a Node runner the Python harness shells out to)

All three are pointed at the same Claude model, fed the same SKILL.md body as a
system prompt, given the same tool set, and scored against the same scenarios.

## Why this design

Each SDK has its own "skills" loading semantics (`.claude/skills/`, `.opencode/agents/`,
`.pi/skills/`) with different metadata. To compare orchestration quality rather
than skill-loader quirks, the harness reads `SKILL.md` once, strips the YAML
frontmatter, and injects the body as the system prompt for every adapter. The
agent then has identical instructions in every run.

## Layout

```
sdk_benchmark/
├── benchmark/
│   ├── cli.py                  # python -m benchmark.cli run …
│   ├── pricing.py              # USD/Mtok lookup for cost fallback
│   ├── scenarios.py            # YAML loader + dataclass
│   ├── scenarios.yaml          # Edit me — define real RCA scenarios
│   ├── scoring.py              # Keyword + tool-call + budget scoring
│   ├── report.py               # Markdown + JSON report writer
│   ├── skill_loader.py         # Strip YAML frontmatter from SKILL.md
│   ├── adapters/
│   │   ├── base.py             # Adapter ABC, SkillRunRequest, SkillRunResult
│   │   ├── claude_agent.py
│   │   ├── opencode.py
│   │   ├── pi.py
│   │   └── _subprocess.py      # Shared Node-runner driver
│   └── runners/
│       ├── opencode_runner.mjs # OpenCode SDK call
│       └── pi_runner.mjs       # Pi SDK call
├── requirements.txt
├── package.json
└── results/                    # Output reports (gitignore if you wish)
```

## Setup

```bash
cd experiments/sdk_benchmark

# Python deps (in a venv of your choice)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Node deps (only needed for OpenCode + Pi adapters)
npm install
```

### Required env vars

Pick a provider:

**Option A — Anthropic direct:**
```bash
export ANTHROPIC_API_KEY=sk-ant-…
# Run with --provider anthropic --model claude-sonnet-4-6
```

**Option B — OpenRouter** (works for all three SDKs):
```bash
export OPENROUTER_API_KEY=sk-or-v1-…
# Run with --provider openrouter --model anthropic/claude-sonnet-4.6
```

OpenRouter notes:
- The harness sets `ANTHROPIC_BASE_URL=https://openrouter.ai/api`, `ANTHROPIC_AUTH_TOKEN=$OPENROUTER_API_KEY`, and blanks `ANTHROPIC_API_KEY` for the Claude Agent SDK. **It overwrites any pre-set `ANTHROPIC_*` env vars** — this matters when running the harness inside another Claude Code session, which leaks routing vars to subprocesses.
- OpenCode picks up `OPENROUTER_API_KEY` directly and uses the `openrouter/anthropic/claude-sonnet-4.6` model slug.
- Pi reads `OPENROUTER_API_KEY` via `AuthStorage.setRuntimeApiKey("openrouter", …)`.

Only needed for scenarios that actually run an RCA against real infra:
```bash
export JUMPBOX_URI="user@host -p 2222"
export SPLUNK_URL=…
export SPLUNK_TOKEN=…
export GITHUB_TOKEN=…  # if the skill's scripts/github_fetcher.py uses it
```

The skill's Python scripts read these directly; the harness does not need them.

## Quick smoke test

Verify the harness loads cleanly without spending tokens:

```bash
.venv/bin/python -m benchmark.cli run \
  --scenarios benchmark/scenarios.yaml \
  --skill ../../skills/root-cause-analysis \
  --dry-run
```

## Run the benchmark

**Anthropic direct:**
```bash
.venv/bin/python -m benchmark.cli run \
  --scenarios benchmark/scenarios.yaml \
  --skill ../../skills/root-cause-analysis \
  --provider anthropic \
  --model claude-sonnet-4-6 \
  --adapters claude-agent-sdk,opencode,pi
```

**OpenRouter:**
```bash
.venv/bin/python -m benchmark.cli run \
  --scenarios benchmark/scenarios-light.yaml \
  --skill ../../skills/root-cause-analysis \
  --provider openrouter \
  --model anthropic/claude-sonnet-4.6 \
  --adapters claude-agent-sdk,opencode,pi
```

Reports land in `results/results-<timestamp>.{md,json}`.

A lighter `scenarios-light.yaml` ships alongside `scenarios.yaml` — it exercises only reasoning + skill-loading (no SSH/Splunk/GitHub), so you can run a real comparison without RHDP infrastructure.

To benchmark a single adapter (e.g. while iterating on scenarios):

```bash
.venv/bin/python -m benchmark.cli run ... --adapters claude-agent-sdk
```

## Authoring scenarios

Edit `benchmark/scenarios.yaml`. Each scenario has:

| field | meaning |
|---|---|
| `id` | Unique identifier (used in reports). |
| `description` | Human-readable note. |
| `prompt` | What the user would type. |
| `expected_keywords` | Case-insensitive substrings that must appear in the final agent output. |
| `expected_tool_calls` | Tool names the agent **must** invoke at least once (e.g. `Bash`). |
| `forbidden_tool_calls` | Tool names that must NOT be called. |
| `max_cost_usd` | Cost cap — scenarios over budget count as failures. |
| `timeout_seconds` | Hard wall-clock cap. |

Three starter scenarios ship with the harness:
1. `preflight-only` — runs without any RHDP infra, exercises the skill's setup checks.
2. `missing-job-id` — checks the agent refuses to fabricate a job ID.
3. `real-job-analysis` — full RCA. **You must fill in a real job ID and tune the expected keywords** before this scenario produces useful signal.

## Scoring

A run **succeeds** iff all four hold:
1. The SDK call returned without raising.
2. Every `expected_keyword` appears in the final output (case-insensitive).
3. Every `expected_tool_call` was invoked at least once, and no `forbidden_tool_calls` were.
4. Total cost did not exceed `max_cost_usd`.

`success_rate = successes / total_runs` per adapter.

## Cost accounting

- **Claude Agent SDK** reports `total_cost_usd` directly on `ResultMessage`. The harness uses that.
- **OpenCode** and **Pi** expose token counts. The harness either reads any native cost field they surface or falls back to multiplying tokens by `benchmark/pricing.py`. Update that table when Anthropic publishes new rates.
- All three are pinned to the same model, so cost differences reflect prompt size, agent loop length, and retry behavior — not model price.

## Limitations & honest caveats

- **MCP servers**: The Claude-flavored `mcp__github__*` / `mcp__atlassian__*` / `mcp__slack__*` tools listed in `SKILL.md` are not used here. The agent only gets the universal tools (`Read`, `Write`, `Edit`, `Bash`, `Grep`, `Glob`). The skill's own Python scripts handle GitHub/Splunk access via env vars. If you want MCP parity across SDKs, extend each adapter with the SDK's MCP config.
- **Permissions**: All adapters run with auto-approval (`bypassPermissions` on Claude Agent SDK; equivalent flags on the others). This is benchmark code, not interactive use.
- **Same model required**: The default scenarios are tuned for Claude. If you switch one adapter to GPT or another provider, the keyword scoring may need updating — but the cost numbers won't be directly comparable.
- **OpenCode + Pi APIs are young**: The Node runners use best-effort field probing (`u.input ?? u.inputTokens ?? 0`) because both SDKs are evolving. If a field name changed upstream, the `usage` numbers may read 0 — the harness will then fall back to `pricing.py` (which needs token counts to compute cost, so the cost will also be 0). Update the runners if you see this.
- **No real RCA without creds**: `real-job-analysis` needs the same SSH + Splunk + GitHub setup that the skill itself needs. The `preflight-only` and `missing-job-id` scenarios work without any of that.

## Extending

- **Add a fourth SDK**: subclass `Adapter` in `benchmark/adapters/`, register it in `ADAPTERS` in `cli.py`.
- **Add MCP servers**: Pass `mcp_servers` through `ClaudeAgentOptions` in `claude_agent.py`; mirror in the two `.mjs` runners.
- **Different models per adapter**: extend `SkillRunRequest` with per-adapter overrides and surface a `--per-adapter-model` flag.
