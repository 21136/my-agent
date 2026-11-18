import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const apiUrl = new URL("../src/api/ws.ts", import.meta.url);

function loadTransport(window, WebSocket) {
  const compiled = ts.transpileModule(readFileSync(apiUrl, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const exports = {};
  const localRequire = (id) => {
    if (id === "../shells/unified/schedule-launch") return { SCHEDULE_PARALLEL_BATCH_MAX: 6 };
    if (id === "./evolution-bridge") return { normalizeEvolutionHostRequest: (value) => value };
    throw new Error(`unexpected ws dependency: ${id}`);
  };
  new Function("exports", "require", "window", "WebSocket", compiled)(exports, localRequire, window, WebSocket);
  return exports;
}

function harness({ allowed = true, autoAvailable, sandboxRunning = true } = {}) {
  const calls = { ensure: 0, stable: 0, sandbox: 0, autoAvailable: 0 };
  const lists = {
    "ws://127.0.0.1:8765": [{ session_id: "stable-only", title: "stable", updated_at: "2026-10-24T10:00:00Z" }],
    "ws://127.0.0.1:18765": [{ session_id: "sandbox-only", title: "sandbox", updated_at: "2026-10-25T10:00:00Z" }],
  };
  const desktop = {
    getSessionLaneCapabilities: async () => ({ legacySessionLanes: allowed }),
    getSidecar: async () => { calls.stable++; return { host: "127.0.0.1", port: 8765 }; },
    ensureSandboxSidecar: async () => { calls.ensure++; return { ok: true }; },
    getSandboxSidecar: async () => {
      calls.sandbox++;
      return sandboxRunning ? { host: "127.0.0.1", port: 18765 } : null;
    },
    setEvolutionConversationId: async () => true,
  };
  if (autoAvailable !== undefined) {
    desktop.launchSelfEvolutionAutoAvailable = async () => {
      calls.autoAvailable++;
      if (autoAvailable instanceof Error) throw autoAvailable;
      return autoAvailable;
    };
  }
  const window = {
    myAgentDesktop: desktop,
    setTimeout: (fn) => setTimeout(fn, 1000),
    clearTimeout,
  };
  class Socket {
    static OPEN = 1;
    static CONNECTING = 0;
    static instances = [];
    readyState = 0;
    handlers = new Map();
    constructor(url) {
      this.url = url;
      Socket.instances.push(this);
      queueMicrotask(() => { this.readyState = Socket.OPEN; this.emit("open"); });
    }
    addEventListener(type, fn) { this.handlers.set(type, [...(this.handlers.get(type) ?? []), fn]); }
    emit(type, data) { for (const fn of this.handlers.get(type) ?? []) fn(data); }
    send(text) {
      if (JSON.parse(text).type === "session.list") {
        queueMicrotask(() => this.emit("message", { data: JSON.stringify({ type: "session.list", sessions: lists[this.url] ?? [] }) }));
      }
    }
    close() { this.readyState = 3; this.emit("close"); }
  }
  const api = loadTransport(window, Socket);
  return { api, client: new api.AgentWsClient("127.0.0.1", 8765), calls, desktop, Socket };
}

const rowsAsPairs = (rows) => rows.map(({ session_id, execution_lane }) => [session_id, execution_lane]);

test("auto-launch Host lists existing sandbox history without starting a runtime", async () => {
  const { client, calls, Socket } = harness({ autoAvailable: true });
  const [rows, concurrent] = await Promise.all([client.listSessionsMerged(), client.listSessionsMerged()]);
  assert.deepEqual(rowsAsPairs(rows), [["sandbox-only", "sandbox"], ["stable-only", "stable"]]);
  assert.deepEqual(concurrent, rows);
  assert.equal(calls.ensure, 0);
  assert.equal(calls.sandbox, 1);
  assert.equal(calls.autoAvailable, 1);
  assert.equal(Socket.instances.length, 2);
  client.destroy();
});

test("auto-launch Host skips sandbox history when its runtime is stopped", async () => {
  const { client, calls, Socket } = harness({ autoAvailable: true, sandboxRunning: false });
  const rows = await client.listSessionsMerged();
  assert.deepEqual(rowsAsPairs(rows), [["stable-only", "stable"]]);
  assert.equal(calls.ensure, 0);
  assert.equal(calls.sandbox, 1);
  assert.equal(Socket.instances.length, 1);
  client.destroy();
});

test("auto-launch availability IPC failure fails closed for read-only listing", async () => {
  const { client, calls, Socket } = harness({ autoAvailable: new Error("IPC unavailable") });
  const rows = await client.listSessionsMerged();
  assert.deepEqual(rowsAsPairs(rows), [["stable-only", "stable"]]);
  assert.equal(calls.ensure, 0);
  assert.equal(calls.sandbox, 0);
  assert.equal(Socket.instances.length, 1);
  client.destroy();
});

test("hosts without auto-launch and explicit false retain legacy history listing", async () => {
  for (const options of [{}, { autoAvailable: false }]) {
    const { client, calls } = harness(options);
    const rows = await client.listSessionsMerged();
    assert.deepEqual(rowsAsPairs(rows), [["sandbox-only", "sandbox"], ["stable-only", "stable"]]);
    assert.ok(calls.ensure > 0);
    client.destroy();
  }
});

test("denied hosts coalesce list requests without probing or starting the sandbox", async () => {
  const { client, calls, Socket } = harness({ allowed: false, autoAvailable: true });
  await client.connect();
  const [rows, concurrent] = await Promise.all([client.listSessionsMerged(), client.listSessionsMerged()]);
  assert.deepEqual(rows, concurrent);
  assert.equal(calls.autoAvailable, 0);
  assert.equal(calls.ensure, 0);
  assert.equal(calls.sandbox, 0);
  assert.equal(Socket.instances.length, 1);
  client.destroy();
});

test("explicit sandbox lane switch still ensures the runtime when auto-launch is available", async () => {
  const { client, calls } = harness({ autoAvailable: true });
  await client.switchLane("sandbox");
  assert.equal(client.getLane(), "sandbox");
  assert.equal(calls.ensure, 1);
  assert.equal(calls.sandbox, 1);
  assert.equal(calls.autoAvailable, 0, "an explicit lane switch uses the established ensure path");
  client.destroy();
});
