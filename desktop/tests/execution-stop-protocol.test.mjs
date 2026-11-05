import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const ts = createRequire(import.meta.url)("typescript");
function loadClient(Socket) {
  const url = new URL("../src/api/ws.ts", import.meta.url);
  const exports = {};
  const js = ts.transpileModule(readFileSync(url, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  new Function("exports", "require", "window", "WebSocket", js)(
    exports,
    (name) => { assert.equal(name, "./evolution-bridge"); return { normalizeEvolutionHostRequest: (request) => request }; },
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
  sent = [];
  constructor() { queueMicrotask(() => { this.readyState = Socket.OPEN; this.emit("open"); }); }
  addEventListener(type, handler) { this.listeners.set(type, [...(this.listeners.get(type) ?? []), handler]); }
  emit(type, event) { for (const handler of this.listeners.get(type) ?? []) handler(event); }
  send(data) { this.sent.push(JSON.parse(data)); }
  close() { this.readyState = 3; this.emit("close", {}); }
}

test("execution stop is scoped to the requested work run", async () => {
  const AgentWsClient = loadClient(Socket);
  const client = new AgentWsClient("127.0.0.1", 8765);
  await client.connect();
  client.ws.sent = [];
  client.sendExecutionStop("run-123");
  assert.deepEqual(client.ws.sent, [{ type: "execution.stop", run_id: "run-123" }]);
  client.destroy();
});

test("execution stop refuses a missing run id instead of sending a global stop", async () => {
  const AgentWsClient = loadClient(Socket);
  const client = new AgentWsClient("127.0.0.1", 8765);
  await client.connect();
  client.ws.sent = [];
  assert.throws(() => client.sendExecutionStop(" "), /run id/i);
  assert.deepEqual(client.ws.sent, []);
  client.destroy();
});

test("user messages can target the current session with one request identity", async () => {
  const AgentWsClient = loadClient(Socket);
  const client = new AgentWsClient("127.0.0.1", 8765);
  await client.connect();
  client.ws.sent = [];
  client.ws.emit("message", { data: JSON.stringify({ type: "session.banner", session_id: "session-a", topics: [], turn_mode: "agent", turn_mode_label: "动手", phase: "S4" }) });
  const requestId = "12345678-1234-4234-8234-123456789abc";
  client.sendMessage("工作期间的补充", undefined, { requestId, sessionId: "session-a" });
  assert.deepEqual(client.ws.sent, [{ type: "user.message", request_id: requestId, session_id: "session-a", text: "工作期间的补充" }]);
  client.destroy();
});

test("tool prompt response uses the exact request, run, session, and text identities", async () => {
  const AgentWsClient = loadClient(Socket);
  const client = new AgentWsClient("127.0.0.1", 8765);
  await client.connect();
  client.ws.sent = [];
  client.ws.emit("message", { data: JSON.stringify({ type: "session.banner", session_id: "session-tool-1", topics: [], turn_mode: "agent", turn_mode_label: "动手", phase: "S4" }) });
  client.sendPromptResponse("12345678-1234-4234-8234-123456789abc", "run-tool-1", "session-tool-1", "src/main.ts");
  assert.deepEqual(client.ws.sent, [{ type: "prompt.response", request_id: "12345678-1234-4234-8234-123456789abc", run_id: "run-tool-1", session_id: "session-tool-1", text: "src/main.ts" }]);
  client.destroy();
});

test("parallel chat cancellation is request-scoped", async () => {
  const AgentWsClient = loadClient(Socket);
  const client = new AgentWsClient("127.0.0.1", 8765);
  await client.connect();
  client.ws.sent = [];
  client.cancelParallelChat("12345678-1234-4234-8234-123456789abc");
  assert.deepEqual(client.ws.sent, [{ type: "chat.cancel", request_id: "12345678-1234-4234-8234-123456789abc" }]);
  client.destroy();
});

test("parallel chat uses the dedicated chat.message protocol and never user.message", async () => {
  const AgentWsClient = loadClient(Socket);
  const client = new AgentWsClient("127.0.0.1", 8765);
  await client.connect();
  client.ws.sent = [];
  client.sendParallelChatMessage("只聊天", "12345678-1234-4234-8234-123456789abc");
  assert.deepEqual(client.ws.sent, [{ type: "chat.message", request_id: "12345678-1234-4234-8234-123456789abc", text: "只聊天" }]);
  client.destroy();
});


test("work instruction sends the three routing identities", async () => {
  const AgentWsClient = loadClient(Socket);
  const client = new AgentWsClient("127.0.0.1", 8765);
  await client.connect();
  client.ws.sent = [];
  client.sendWorkInstruction("12345678-1234-4234-8234-123456789abc", "session-a", "run-a", "检查生成文件");
  assert.deepEqual(client.ws.sent, [{ type: "work.instruction", request_id: "12345678-1234-4234-8234-123456789abc", session_id: "session-a", run_id: "run-a", text: "检查生成文件" }]);
  client.destroy();
});
