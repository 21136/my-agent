import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const TOKEN = "a".repeat(64);

function clientHarness(api = {}) {
  const window = { myAgentDesktop: api, setTimeout: () => 1, clearTimeout() {} };
  class Socket {
    static OPEN = 1;
    static CONNECTING = 0;
    static instances = [];
    readyState = 0;
    handlers = new Map();
    constructor(...args) {
      this.args = args;
      Socket.instances.push(this);
      queueMicrotask(() => { this.readyState = Socket.OPEN; this.emit("open", {}); });
    }
    addEventListener(type, handler) { this.handlers.set(type, [...(this.handlers.get(type) ?? []), handler]); }
    emit(type, event) { for (const handler of this.handlers.get(type) ?? []) handler(event); }
    send() {}
    close() { this.readyState = 3; this.emit("close", {}); }
  }
  const exports = {};
  const js = ts.transpileModule(readFileSync(new URL("../src/api/ws.ts", import.meta.url), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  new Function("exports", "require", "window", "WebSocket", js)(
    exports,
    (name) => name === "./evolution-bridge"
      ? { normalizeEvolutionHostRequest: (request) => request }
      : name === "../shells/unified/schedule-launch" ? { SCHEDULE_PARALLEL_BATCH_MAX: 4 }
        : (() => { throw new Error(`unexpected dependency: ${name}`); })(),
    window,
    Socket,
  );
  return { AgentWsClient: exports.AgentWsClient, createWsClient: exports.createWsClient, Socket, window };
}

test("AgentWsClient offers the operator token as a WebSocket subprotocol, never in the URL", async () => {
  const { AgentWsClient, Socket } = clientHarness();
  const client = new AgentWsClient("127.0.0.1", 8765, TOKEN);
  await client.connect();
  assert.deepEqual(Socket.instances[0].args, [`ws://127.0.0.1:8765`, [`my-agent-auto.${TOKEN}`]]);
  client.destroy();
});

test("reconnect refreshes the token from the owned sidecar and drops it when absent", async () => {
  let info = { host: "127.0.0.1", port: 8765, autoToken: TOKEN };
  const { AgentWsClient, Socket } = clientHarness({
    getSessionLaneCapabilities: async () => ({ legacySessionLanes: false }),
    getSidecar: async () => info,
  });
  const client = new AgentWsClient("127.0.0.1", 8765);
  await client.connect();
  Socket.instances[0].close();
  await client.reconnect();
  assert.deepEqual(Socket.instances[1].args[1], [`my-agent-auto.${TOKEN}`]);
  info = { host: "127.0.0.1", port: 8765 };
  Socket.instances[1].close();
  await client.reconnect();
  assert.equal(Socket.instances[2].args.length, 1);
  client.destroy();
});

test("createWsClient takes autoToken only from the trusted sidecar descriptor", async () => {
  const { createWsClient, Socket } = clientHarness({
    getSidecar: async () => ({ host: "127.0.0.1", port: 9441, autoToken: TOKEN }),
  });
  const client = await createWsClient();
  assert.deepEqual(Socket.instances[0].args, ["ws://127.0.0.1:9441", [`my-agent-auto.${TOKEN}`]]);
  client.destroy();
});

test("autoToken is never sent to a non-loopback endpoint", async () => {
  const { AgentWsClient, Socket } = clientHarness();
  const client = new AgentWsClient("example.invalid", 8765, TOKEN);
  await client.connect();
  assert.deepEqual(Socket.instances[0].args, ["ws://example.invalid:8765"]);
  client.destroy();
});
