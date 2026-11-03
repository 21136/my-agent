import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const SESSION = "12345678-1234-4234-8234-123456789abc";

function loadClient(Socket) {
  const url = new URL("../src/api/ws.ts", import.meta.url);
  const exports = {};
  const js = ts.transpileModule(readFileSync(url, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const bridge = { normalizeEvolutionHostRequest: (request) => request };
  new Function("exports", "require", "window", "WebSocket", js)(
    exports,
    (name) => { assert.equal(name, "./evolution-bridge"); return bridge; },
    { myAgentDesktop: {} },
    Socket,
  );
  return exports.AgentWsClient;
}

class Socket {
  static OPEN = 1;
  static CONNECTING = 0;
  readyState = 0;
  listeners = new Map();
  constructor() { queueMicrotask(() => { this.readyState = Socket.OPEN; this.emit("open"); }); }
  addEventListener(type, handler) { this.listeners.set(type, [...(this.listeners.get(type) ?? []), handler]); }
  emit(type, event) { for (const handler of this.listeners.get(type) ?? []) handler(event); }
  send() {}
  close() { this.readyState = 3; this.emit("close", {}); }
}

test("auto-recognition session state accepts only exact state for the current session", async () => {
  const AgentWsClient = loadClient(Socket);
  const client = new AgentWsClient("127.0.0.1", 8765);
  await client.connect();
  const socket = client.ws;
  const received = [];
  client.onEvent((event) => received.push(event));
  socket.emit("message", { data: JSON.stringify({ type: "session.banner", session_id: SESSION }) });
  socket.emit("message", { data: JSON.stringify({ type: "work-record.auto-recognition.state", session_id: SESSION, enabled: true }) });
  socket.emit("message", { data: JSON.stringify({ type: "work-record.auto-recognition.state", session_id: "stale", enabled: false }) });
  socket.emit("message", { data: JSON.stringify({ type: "work-record.auto-recognition.state", session_id: SESSION, enabled: false, extra: true }) });

  assert.deepEqual(received.filter((event) => event.type === "work-record.auto-recognition.state"), [
    { type: "work-record.auto-recognition.state", session_id: SESSION, enabled: true },
  ]);
  client.destroy();
});


test("a stale enabled snapshot cannot overwrite a pending revoke acknowledgement", async () => {
  const AgentWsClient = loadClient(Socket);
  const client = new AgentWsClient("127.0.0.1", 8765);
  await client.connect();
  const socket = client.ws;
  const received = [];
  client.onEvent((event) => received.push(event));
  socket.emit("message", { data: JSON.stringify({ type: "session.banner", session_id: SESSION }) });

  const requestId = client.setWorkRecordAutoRecognition(SESSION, "revoke");
  socket.emit("message", { data: JSON.stringify({
    type: "work-record.auto-recognition.state", session_id: SESSION, enabled: true,
  }) });
  socket.emit("message", { data: JSON.stringify({
    type: "work-record.auto-recognition.done", session_id: SESSION, request_id: requestId,
    action: "revoke", status: "ok", enabled: false,
  }) });

  assert.equal(received.some((event) => event.type === "work-record.auto-recognition.state"), false);
  assert.deepEqual(received.filter((event) => event.type === "work-record.auto-recognition.done").map((event) => event.enabled), [false]);
  client.destroy();
});
