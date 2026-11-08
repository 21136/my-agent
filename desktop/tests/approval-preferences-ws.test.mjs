import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const ts = require("typescript");
function loadClient(Socket, desktopApi = {}) {
  const url = new URL("../src/api/ws.ts", import.meta.url);
  const exports = {};
  const js = ts.transpileModule(readFileSync(url, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  new Function("exports", "require", "window", "WebSocket", js)(
    exports, (name) => { assert.equal(name, "./evolution-bridge"); return { normalizeEvolutionHostRequest: (request) => request }; },
    { myAgentDesktop: desktopApi }, Socket,
  );
  return exports.AgentWsClient;
}
class Socket {
  static OPEN = 1; static CONNECTING = 0;
  readyState = 0; listeners = new Map(); sent = [];
  constructor() { queueMicrotask(() => { this.readyState = Socket.OPEN; this.emit("open"); }); }
  addEventListener(type, handler) { this.listeners.set(type, [...(this.listeners.get(type) ?? []), handler]); }
  emit(type, event) { for (const handler of this.listeners.get(type) ?? []) handler(event); }
  send(payload) { this.sent.push(JSON.parse(payload)); }
  close() { this.readyState = 3; this.emit("close", {}); }
}
const AVAILABLE_MODES = ["manual", "auto", "full"];

test("approval preferences are routed through trusted desktop IPC, never ordinary WebSocket", async () => {
  const calls = [];
  const payload = (type, mode) => ({ type, status: "ok", approval_mode: mode, saved_approval_mode: mode, available_modes: AVAILABLE_MODES });
  const Client = loadClient(Socket, {
    getApprovalPreferences: async () => { calls.push(["get"]); return payload("approval_preferences.state", "manual"); },
    setApprovalMode: async (mode) => { calls.push(["set", mode]); return payload("approval_preferences.done", mode); },
  });
  const client = new Client("127.0.0.1", 8765), received = [];
  await client.connect(); client.onEvent((event) => received.push(event));
  client.getApprovalPreferences(); client.setApprovalMode("auto");
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls, [["get"], ["set", "auto"]]);
  assert.equal(client.ws.sent.some((event) => event.type.startsWith("approval_preferences.")), false);
  assert.deepEqual(received.map((event) => event.type), ["approval_preferences.state", "approval_preferences.done"]);
  assert.throws(() => client.setApprovalMode("unrestricted"), /invalid approval mode/);
  client.destroy();
});

test("approval preference state and done events are forwarded only with valid conservative schema", async () => {
  const Client = loadClient(Socket), client = new Client("127.0.0.1", 8765);
  await client.connect(); const socket = client.ws, received = [];
  client.onEvent((event) => received.push(event));
  const base = { status: "ok", approval_mode: "full", saved_approval_mode: "auto", available_modes: AVAILABLE_MODES };
  socket.emit("message", { data: JSON.stringify({ type: "approval_preferences.state", ...base }) });
  socket.emit("message", { data: JSON.stringify({ type: "approval_preferences.done", ...base, message: "" }) });
  socket.emit("message", { data: JSON.stringify({ type: "approval_preferences.done", ...base, extra: true }) });
  socket.emit("message", { data: JSON.stringify({ type: "approval_preferences.state", ...base, approval_mode: "unrestricted" }) });
  socket.emit("message", { data: JSON.stringify({ type: "approval_preferences.done", status: "rejected", approval_mode: "manual", saved_approval_mode: "manual", available_modes: AVAILABLE_MODES, message: "保存失败" }) });
  assert.deepEqual(received.filter((event) => event.type.startsWith("approval_preferences.")), []);
  client.destroy();
});
