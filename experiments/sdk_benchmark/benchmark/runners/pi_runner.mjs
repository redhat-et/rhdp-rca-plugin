#!/usr/bin/env node
// Pi Coding Agent SDK runner — JSON-on-stdin / JSON-on-stdout contract.

import { readFileSync } from "node:fs";

const payload = JSON.parse(readFileSync(0, "utf8"));
const started = process.hrtime.bigint();

let mod, piAi;
try {
  mod = await import("@earendil-works/pi-coding-agent");
  piAi = await import("@earendil-works/pi-ai");
} catch (err) {
  emit({ error: `Pi SDK not installed: ${err.message}.` });
  process.exit(0);
}

const { AuthStorage, ModelRegistry, SessionManager, createAgentSession } = mod;
const { getModel } = piAi;

const toolCalls = [];
let finalText = "";
const usage = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 };
let nativeCostUsd = null;
let numTurns = 0;
let error = null;

let session = null;
let unsub = null;
try {
  const authStorage = AuthStorage.create();

  if (payload.provider === "openrouter") {
    const key = process.env.OPENROUTER_API_KEY;
    if (!key) throw new Error("OPENROUTER_API_KEY not set");
    authStorage.setRuntimeApiKey("openrouter", key);
  }

  const modelRegistry = ModelRegistry.create(authStorage);

  // For OpenRouter: model id is e.g. "anthropic/claude-sonnet-4.6" under provider "openrouter".
  // For Anthropic direct: provider="anthropic", id="claude-sonnet-4-6".
  const provider = payload.provider === "openrouter" ? "openrouter" : "anthropic";
  const modelId = payload.model;
  let model = getModel(provider, modelId) ?? modelRegistry.find(provider, modelId);
  if (!model) {
    throw new Error(`Model not found: provider=${provider} id=${modelId}`);
  }

  const created = await createAgentSession({
    sessionManager: SessionManager.inMemory(),
    authStorage,
    modelRegistry,
    model,
    cwd: payload.cwd,
    systemPrompt: payload.systemPrompt,
  });
  session = created.session;

  unsub = session.subscribe?.((event) => {
    if (event?.type === "message_update") {
      const sub = event.assistantMessageEvent;
      if (!sub) return;
      if (sub.type === "text_delta") finalText += sub.delta ?? "";
      if (sub.type === "tool_call" || sub.type === "tool_use") {
        toolCalls.push(sub.name ?? sub.tool ?? "unknown");
      }
    }
  });

  await session.prompt(payload.prompt);

  // session.getSessionStats() is the documented API — returns SessionStats with
  // { tokens: {input, output, cacheRead, cacheWrite, total}, cost, toolCalls, ... }.
  const stats = typeof session.getSessionStats === "function" ? session.getSessionStats() : {};
  const tokens = stats.tokens ?? {};
  usage.input = tokens.input ?? 0;
  usage.output = tokens.output ?? 0;
  usage.cacheRead = tokens.cacheRead ?? 0;
  usage.cacheWrite = tokens.cacheWrite ?? 0;
  nativeCostUsd = typeof stats.cost === "number" ? stats.cost : null;
  numTurns = stats.assistantMessages ?? (session.messages ?? []).length;
  // Pi reports a tool-call count, not a per-call list, unless we caught them
  // via the subscribe stream above. Pad toolCalls with anonymous entries so
  // counts in the report are at least accurate.
  if (toolCalls.length === 0 && typeof stats.toolCalls === "number") {
    for (let i = 0; i < stats.toolCalls; i++) toolCalls.push("tool");
  }

  if (!finalText) {
    const msgs = session.messages ?? [];
    const lastAssistant = [...msgs].reverse().find((m) => m.role === "assistant");
    if (lastAssistant) {
      if (typeof lastAssistant.content === "string") finalText = lastAssistant.content;
      else if (Array.isArray(lastAssistant.content)) {
        finalText = lastAssistant.content
          .filter((c) => c.type === "text")
          .map((c) => c.text)
          .join("");
      }
    }
  }
} catch (err) {
  error = `${err.name}: ${err.message}`;
} finally {
  // Always release the event subscription and dispose the session, even when
  // an error short-circuited the happy path. Otherwise back-to-back benchmark
  // runs leak file handles and event listeners.
  if (typeof unsub === "function") {
    try { unsub(); } catch (_e) { /* swallow — cleanup */ }
  }
  try { session?.dispose?.(); } catch (_e) { /* swallow — cleanup */ }
}

const durationSeconds = Number(process.hrtime.bigint() - started) / 1e9;
emit({
  finalText,
  toolCalls,
  usage,
  nativeCostUsd,
  durationSeconds,
  numTurns,
  error,
});

function emit(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}
