import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";

const ts = createRequire(import.meta.url)("typescript");
const UUID = "33333333-3333-4333-8333-333333333333";
const LEGACY_NATIVE_SESSION = "20261030-abcdef12";
const BACKEND_ONLY_SESSION = "imported-session.with.safe-name";
function load(url, cache = new Map(), window = { myAgentDesktop: {} }) {
  if (cache.has(url.href)) return cache.get(url.href);
  const exports = {};
  cache.set(url.href, exports);
  const js = ts.transpileModule(readFileSync(url, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const localRequire = (specifier) => {
    assert.ok(specifier.startsWith("."), `unexpected runtime dependency ${specifier}`);
    return load(new URL(specifier.endsWith(".ts") ? specifier : `${specifier}.ts`, url), cache, window);
  };
  new Function("exports", "require", "window", "WebSocket", "crypto", js)(
    exports, localRequire, window, class UnusedSocket {}, globalThis.crypto,
  );
  return exports;
}

test("unsupported backend session ids clear the Evolution binding without changing chat session", () => {
  const updates = [];
  const window = { myAgentDesktop: { setEvolutionConversationId: async (id) => { updates.push(id); return true; } } };
  const { AgentWsClient } = load(new URL("../src/api/ws.ts", import.meta.url), new Map(), window);
  const client = new AgentWsClient("127.0.0.1", 8765);

  client.updateEvolutionConversationId(UUID);
  client.updateEvolutionConversationId(LEGACY_NATIVE_SESSION);
  client.updateEvolutionConversationId(BACKEND_ONLY_SESSION);

  assert.deepEqual(updates, [UUID, LEGACY_NATIVE_SESSION, null], "supported formats pass through; unsupported session clears rather than sending an invalid IPC payload");
  assert.equal(client.currentConversationId, BACKEND_ONLY_SESSION, "chat must retain its actual backend session id");
});
