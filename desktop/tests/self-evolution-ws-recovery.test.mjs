import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, existsSync, statSync } from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
const testNodeModules = process.env.CODEX_TEST_NODE_MODULES;
if (testNodeModules && (!path.isAbsolute(testNodeModules) || !existsSync(testNodeModules) || !statSync(testNodeModules).isDirectory())) throw new Error("CODEX_TEST_NODE_MODULES must be an existing absolute directory");
const require = createRequire(testNodeModules ? path.join(testNodeModules, "..", "__codex_test__.cjs") : import.meta.url);
const ts = require("typescript");
function loadClient(Socket) {
  const js = ts.transpileModule(readFileSync(new URL("../src/api/ws.ts", import.meta.url), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const exports = {};
  new Function("exports", "require", "window", "WebSocket", js)(exports,
    name => { if (name === "./evolution-bridge") return { normalizeEvolutionHostRequest: r => r }; if (name === "../shells/unified/schedule-launch") return { SCHEDULE_PARALLEL_BATCH_MAX: 8 }; throw new Error(`unexpected module ${name}`); },
    { myAgentDesktop: {} }, Socket);
  return exports.AgentWsClient;
}
class Socket {
  static OPEN = 1; static CONNECTING = 0;
  readyState = 0; listeners = new Map(); sent = [];
  constructor() { queueMicrotask(() => { this.readyState = Socket.OPEN; this.emit("open", {}); }); }
  addEventListener(type, fn) { this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]); }
  emit(type, event) { for (const fn of this.listeners.get(type) ?? []) fn(event); }
  send(raw) { this.sent.push(JSON.parse(raw)); }
  close() { this.readyState = 3; this.emit("close", {}); }
}
test("snapshot recovery sends only opaque lookup data under the active session binding", async () => {
  const Client = loadClient(Socket), client = new Client("127.0.0.1", 8765);
  await client.connect();
  const socket = client.ws;
  socket.emit("message", { data: JSON.stringify({ type: "session.banner", session_id: "session-a" }) });
  const requestId = client.requestSelfEvolutionSnapshot("task_opaque-1");
  assert.deepEqual(socket.sent.at(-1), { type: "self_evolution.snapshot.get", task_id: "task_opaque-1", request_id: requestId });
  assert.equal(Object.keys(socket.sent.at(-1)).some(key => /auth|mode|authorized|root/i.test(key)), false);
  assert.throws(() => client.requestSelfEvolutionSnapshot("../outside"), /opaque task id/);
  socket.emit("message", { data: JSON.stringify({ type: "session.banner", session_id: "session-b" }) });
  const nextRequest = client.requestSelfEvolutionSnapshot("task_opaque-1");
  assert.notEqual(nextRequest, requestId);
  client.destroy();
});
