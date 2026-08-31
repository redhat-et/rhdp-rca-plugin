#!/usr/bin/env node
// OpenCode runner — invokes the `opencode run --format json` CLI and parses
// its JSON event stream. The OpenCode TypeScript SDK is essentially a thin
// HTTP client around the same `opencode serve` process; for benchmark use
// the CLI is faster (no server spin-up) and emits per-step token + cost.

import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const payload = JSON.parse(readFileSync(0, "utf8"));
const started = process.hrtime.bigint();

const here = dirname(fileURLToPath(import.meta.url));
const opencodeBin = resolve(here, "..", "..", "node_modules", ".bin", "opencode");

// Model format: "openrouter/anthropic/claude-sonnet-4.6" or "anthropic/claude-sonnet-4-6".
const modelArg = payload.provider === "openrouter"
  ? `openrouter/${payload.model}`
  : `anthropic/${payload.model}`;

const args = [
  "run",
  "--format=json",
  "--dangerously-skip-permissions",
  "-m", modelArg,
  // Prepend the skill body as a user "system instructions" preamble. OpenCode
  // doesn't expose a `--system` flag at the CLI, so we inline it.
  `[SYSTEM]\n${payload.systemPrompt}\n[/SYSTEM]\n\n${payload.prompt}`,
];

const usage = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 };
let nativeCostUsd = 0;
let finalText = "";
const toolCalls = [];
let numTurns = 0;
let error = null;

try {
  const result = await runOpencode(opencodeBin, args, payload);
  for (const ev of result.events) {
    if (ev.type === "text" && ev.part?.text) finalText += ev.part.text;
    if (ev.type === "tool_use" || ev.type === "tool") {
      const name = ev.part?.tool ?? ev.part?.name;
      if (name) toolCalls.push(name);
    }
    if (ev.type === "step_finish" && ev.part?.tokens) {
      const t = ev.part.tokens;
      usage.input += t.input ?? 0;
      usage.output += t.output ?? 0;
      usage.cacheRead += t.cache?.read ?? 0;
      usage.cacheWrite += t.cache?.write ?? 0;
      if (typeof ev.part.cost === "number") nativeCostUsd += ev.part.cost;
      numTurns += 1;
    }
  }
  if (result.exitCode !== 0) {
    // Non-zero exit is always a failure — even if some text streamed before the
    // crash, the run can't be trusted. Prefer the last stderr line as the error
    // string since it's usually the actual reason.
    const stderrTail = result.stderr?.trim().split("\n").slice(-1)[0];
    error = error ?? stderrTail ?? `opencode exited ${result.exitCode}`;
  } else if (result.stderr && !finalText) {
    // Successful exit with stderr noise and no text → surface stderr.
    error = result.stderr.trim().split("\n").slice(-1)[0] || null;
  }
} catch (err) {
  error = `${err.name}: ${err.message}`;
}

const durationSeconds = Number(process.hrtime.bigint() - started) / 1e9;
emit({
  finalText,
  toolCalls,
  usage,
  nativeCostUsd: nativeCostUsd > 0 ? nativeCostUsd : null,
  durationSeconds,
  numTurns,
  error,
});

function emit(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function runOpencode(bin, args, payload) {
  return new Promise((resolveP) => {
    let stdoutBuf = "";
    let stderrBuf = "";
    const events = [];
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(killer);
      resolveP(result);
    };

    const proc = spawn(bin, args, {
      env: process.env,
      cwd: payload.cwd,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const killer = setTimeout(() => proc.kill("SIGTERM"), (payload.timeoutSeconds + 10) * 1000);
    proc.stdout.on("data", (chunk) => {
      stdoutBuf += chunk.toString();
      let nl;
      while ((nl = stdoutBuf.indexOf("\n")) !== -1) {
        const line = stdoutBuf.slice(0, nl);
        stdoutBuf = stdoutBuf.slice(nl + 1);
        if (!line.trim()) continue;
        try { events.push(JSON.parse(line)); } catch (_e) { /* ignore non-JSON */ }
      }
    });
    proc.stderr.on("data", (chunk) => { stderrBuf += chunk.toString(); });
    // spawn() emits 'error' (and not 'close') when the binary can't be
    // exec'd (ENOENT, EACCES) — without this handler the Promise hangs.
    proc.on("error", (err) => {
      finish({ exitCode: 1, events, stderr: `${stderrBuf}spawn error: ${err.message}` });
    });
    proc.on("close", (code) => {
      finish({ exitCode: code ?? 1, events, stderr: stderrBuf });
    });
  });
}
