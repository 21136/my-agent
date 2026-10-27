import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const apiUrl = new URL("../src/api/ws.ts", import.meta.url);
const shell = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");

// Compile only the two actual transport modules in memory: no generated files,
// real sockets, sidecars, stores, or global loader hooks.
function loadTransport(window, WebSocket) {
  const cache = new Map();
  function load(url) {
    if (cache.has(url.href)) return cache.get(url.href);
    const exports = {};
    cache.set(url.href, exports);
    const compiled = ts.transpileModule(readFileSync(url, "utf8"), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    const localRequire = (id) => {
      assert.equal(id, "./evolution-bridge", "unexpected transport dependency");
      return load(new URL("./evolution-bridge.ts", url));
    };
    new Function("exports", "require", "window", "WebSocket", compiled)(exports, localRequire, window, WebSocket);
    return exports;
  }
  return load(apiUrl);
}

function loadShellFunction(name, bindings) {
  const source = ts.createSourceFile("index.ts", shell, ts.ScriptTarget.Latest, true);
  let declaration;
  const visit = (node) => {
    if (ts.isFunctionDeclaration(node) && node.name?.text === name) declaration = node;
    ts.forEachChild(node, visit);
  };
  visit(source);
  assert.ok(declaration, `missing shell function ${name}`);
  const compiled = ts.transpileModule(declaration.getText(source), {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText;
  return new Function(...Object.keys(bindings), `${compiled}\nreturn ${name};`)(...Object.values(bindings));
}

function harness(allowed = true) {
  const calls = { ensure: 0, stable: 0, sandbox: 0, conversations: [] };
  const timers = new Map();
  let timerSerial = 0;
  const lists = {
    "ws://127.0.0.1:8765": [{ session_id: "stable-only", title: "stable", updated_at: "2026-10-24T10:00:00Z" }],
    "ws://127.0.0.1:18765": [{ session_id: "sandbox-only", title: "sandbox", updated_at: "2026-10-25T10:00:00Z" }],
  };
  const desktop = {
    getSessionLaneCapabilities: async () => ({ legacySessionLanes: allowed }),
    getSidecar: async () => { calls.stable++; return { host: "127.0.0.1", port: 8765 }; },
    ensureSandboxSidecar: async () => { calls.ensure++; return { ok: allowed, message: "legacy lane disabled" }; },
    getSandboxSidecar: async () => { calls.sandbox++; return allowed ? { host: "127.0.0.1", port: 18765 } : null; },
    setEvolutionConversationId: async (id) => { calls.conversations.push(id); return true; },
  };
  const window = {
    myAgentDesktop: desktop,
    setTimeout: (fn) => { const id = ++timerSerial; timers.set(id, fn); return id; },
    clearTimeout: (id) => timers.delete(id),
  };
  class Socket {
    static OPEN = 1;
    static CONNECTING = 0;
    static instances = [];
    readyState = 0;
    sent = [];
    handlers = new Map();
    constructor(url) {
      this.url = url;
      Socket.instances.push(this);
      queueMicrotask(() => { this.readyState = Socket.OPEN; this.emit("open"); });
    }
    addEventListener(type, fn) {
      this.handlers.set(type, [...this.handlers.get(type) ?? [], fn]);
    }
    emit(type, data) { for (const fn of this.handlers.get(type) ?? []) fn(data); }
    send(text) {
      const frame = JSON.parse(text);
      this.sent.push(frame);
      if (frame.type === "session.list") {
        queueMicrotask(() => this.emit("message", { data: JSON.stringify({ type: "session.list", sessions: lists[this.url] ?? [] }) }));
      }
    }
    close() { this.readyState = 3; this.emit("close"); }
  }
  const api = loadTransport(window, Socket);
  const client = new api.AgentWsClient("127.0.0.1", 8765);
  return { api, client, Socket, calls, timers, desktop, window };
}

const tick = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

test("merged rows deduplicate copied ids, prefer newer rows, preserve stable ties and cap 50", () => {
  const { api } = harness();
  const stable = [{ session_id: "same", title: "stable", updated_at: "2026-10-24" }];
  const tie = api.mergeSessionLists(stable, [{ ...stable[0], title: "sandbox" }]);
  assert.equal(tie[0].execution_lane, "stable");
  const newer = api.mergeSessionLists(stable, [{ ...stable[0], title: "new", updated_at: "2026-10-25" }]);
  assert.equal(newer[0].execution_lane, "sandbox");
  assert.equal(newer[0].title, "new");
  const many = Array.from({ length: 60 }, (_, n) => ({ session_id: String(n), title: "row", updated_at: String(n).padStart(2, "0") }));
  const bounded = api.mergeSessionLists(many, []);
  assert.equal(bounded.length, 50);
  assert.equal(bounded[0].session_id, "59");
  assert.equal(bounded[49].session_id, "10");
});

test("mutable Host obtains both lane lists without replacing the active connection", async () => {
  const { client, Socket, calls } = harness();
  await client.connect();
  const active = Socket.instances[0];
  const [rows, concurrent] = await Promise.all([client.listSessionsMerged(), client.listSessionsMerged()]);
  assert.deepEqual(rows.map((row) => [row.session_id, row.execution_lane]), [["sandbox-only", "sandbox"], ["stable-only", "stable"]]);
  assert.deepEqual(concurrent, rows);
  const pushed = client.applySessionListPush([{ session_id: "stable-new", title: "new", updated_at: "2026-10-26" }]);
  assert.deepEqual(pushed.map((row) => row.session_id), ["stable-new", "sandbox-only"]);
  assert.equal(active.readyState, Socket.OPEN);
  assert.equal(Socket.instances.length, 3);
  assert.ok(calls.ensure > 0);
  client.destroy();
});

test("denied fixed/test/packaged Host only lists the current socket; no legacy endpoint reads", async () => {
  const { client, Socket, calls } = harness(false);
  await client.connect();
  await client.listSessionsMerged();
  await tick();
  assert.equal(calls.stable, 0);
  assert.equal(calls.sandbox, 0);
  assert.equal(Socket.instances.length, 1);
  assert.ok(Socket.instances[0].sent.some((frame) => frame.type === "session.list"));
  await client.openSession("bound-chat");
  assert.deepEqual(Socket.instances[0].sent.at(-1), { type: "session.open", session_id: "bound-chat" });
  assert.equal(calls.stable, 0, "same-lane open must use the current bound socket");
  assert.equal(calls.ensure, 0);
  await assert.rejects(client.switchLane("sandbox"), /disabled|legacy|fixed/i);
  client.destroy();
});

test("fixed session-list pushes cannot turn stored lane hints into cross-lane opens", async () => {
  const { client } = harness(false);
  await client.connect();
  const rows = client.applySessionListPush([{ session_id: "bound", title: "current", updated_at: "", execution_lane: "sandbox" }]);
  assert.equal(rows[0].execution_lane, "stable");
  client.destroy();
});

test("missing or failed Host guard fails closed and never infers permission from the renderer URL", async () => {
  const { client, Socket, calls, desktop } = harness();
  desktop.getSessionLaneCapabilities = async () => { throw Error("unavailable"); };
  await client.connect();
  await client.listSessionsMerged();
  assert.equal(calls.stable, 0);
  assert.equal(calls.sandbox, 0);
  assert.equal(Socket.instances.length, 1);
  client.destroy();
});

test("missing capability bridge allows current-session opens while denying legacy enumeration", async () => {
  const { client, Socket, calls, desktop } = harness();
  delete desktop.getSessionLaneCapabilities;
  await client.connect();
  await client.openSession("current-host");
  await client.listSessionsMerged();
  assert.equal(calls.stable + calls.sandbox + calls.ensure, 0);
  assert.equal(Socket.instances.length, 1);
  client.destroy();
});

test("same-lane CLOSED socket reconnects on its bound endpoint before opening the session", async () => {
  const { client, Socket, calls, timers } = harness(false);
  await client.connect();
  Socket.instances[0].close();
  assert.equal(timers.size, 1, "close schedules normal retry");

  await client.openSession("after-close", "stable");

  assert.equal(Socket.instances.length, 2, "reuse the client connection flow exactly once");
  assert.equal(Socket.instances[1].url, "ws://127.0.0.1:8765");
  assert.deepEqual(Socket.instances[1].sent.at(-1), { type: "session.open", session_id: "after-close" });
  assert.equal(timers.size, 0, "explicit open consumes the pending retry timer");
  assert.equal(calls.stable + calls.sandbox + calls.ensure, 0, "fixed Host does not resolve legacy endpoints");
  client.destroy();
});

test("same-lane CONNECTING socket is awaited without opening a duplicate", async () => {
  const { client, Socket } = harness(false);
  const initial = client.connect();
  const ensured = client.ensureLane("stable");

  await Promise.all([initial, ensured]);

  assert.equal(Socket.instances.length, 1);
  assert.equal(Socket.instances[0].readyState, Socket.OPEN);
  client.destroy();
});

test("destroy rejects an in-flight connect instead of leaving its caller pending", async () => {
  const { client } = harness(false);
  const connecting = client.connect();
  client.destroy();

  await assert.rejects(Promise.race([
    connecting,
    new Promise((_, reject) => setTimeout(() => reject(new Error("probe timed out")), 40)),
  ]), /destroyed/);
});

test("socket close before open rejects the pending connect and leaves one scheduled retry", async () => {
  const { client, Socket, timers } = harness(false);
  const connecting = client.connect();
  Socket.instances[0].close();

  await assert.rejects(connecting, /closed before connection opened/);
  assert.equal(timers.size, 1);
  client.destroy();
  assert.equal(timers.size, 0);
});

test("socket error rejects the pending connect promptly", async () => {
  const { client, Socket } = harness(false);
  const connecting = client.connect();
  Socket.instances[0].emit("error");

  await assert.rejects(connecting, /WebSocket error/);
  client.destroy();
});

test("all session-open flows defer destructive view reset until the open frame is sent", async () => {
  const { client, Socket } = harness(false);
  await client.connect();
  const order = [];
  const socket = Socket.instances[0];
  client.onEvent((event) => { if (event.type === "session.banner") order.push("session.banner task"); });
  socket.send = (text) => {
    const frame = JSON.parse(text);
    if (frame.type === "session.open") {
      order.push("session.open sent");
      setTimeout(() => socket.emit("message", {
        data: JSON.stringify({ type: "session.banner", session_id: "target" }),
      }), 0);
    }
  };
  const openThenSwitch = loadShellFunction("openSessionThenBeginSwitch", {});

  await openThenSwitch(
    () => client.openSession("target", "stable"),
    () => order.push("clear and hydrate"),
  );
  await new Promise((resolve) => setTimeout(resolve, 10));

  assert.deepEqual(order, ["session.open sent", "clear and hydrate", "session.banner task"]);
  assert.equal((shell.match(/await openSessionThenBeginSwitch\(/g) ?? []).length, 3, "sidebar, sessions menu, and project thread share the safe ordering");
  client.destroy();
});

test("failed session open leaves the current chat view untouched", async () => {
  let view = "current chat";
  const openThenSwitch = loadShellFunction("openSessionThenBeginSwitch", {});

  await assert.rejects(openThenSwitch(
    async () => { throw new Error("WebSocket not connected"); },
    () => { view = "cleared"; },
  ), /WebSocket not connected/);
  assert.equal(view, "current chat");
});

test("explicit sandbox session switch retires old socket and clears evolution conversation", async () => {
  const { client, Socket, timers, calls } = harness();
  await client.connect();
  const old = Socket.instances[0];
  old.emit("message", { data: JSON.stringify({ type: "session.banner", session_id: "01234567-89ab-4cde-8123-456789abcdef" }) });
  await client.createSandboxSession();
  assert.equal(client.getLane(), "sandbox");
  assert.equal(Socket.instances.at(-1).url, "ws://127.0.0.1:18765");
  assert.equal(Socket.instances.at(-1).sent.at(-1).type, "session.create_sandbox");
  assert.equal(calls.conversations.at(-1), null);
  assert.equal(timers.size, 0, "retired socket must not reconnect");
  const count = calls.conversations.length;
  old.emit("message", { data: JSON.stringify({ type: "session.banner", session_id: "stale" }) });
  assert.equal(calls.conversations.length, count, "retired socket must not bind evolution");
  client.destroy();
  assert.equal(timers.size, 0, "destroy must not reconnect");
});

test("sandbox reconnect keeps its lane instead of falling back to stable", async () => {
  const { client, Socket, timers } = harness();
  await client.connect();
  await client.switchLane("sandbox");
  Socket.instances.at(-1).close();
  const reconnect = timers.values().next().value;
  timers.clear();
  reconnect();
  await tick();
  assert.equal(Socket.instances.at(-1).url, "ws://127.0.0.1:18765");
  assert.equal(client.getLane(), "sandbox");
  client.destroy();
});

test("active evolution events and conversation-bound requests survive lane reconciliation", async () => {
  const { client, Socket } = harness();
  await client.connect();
  const events = [];
  client.onEvent((event) => events.push(event.type));
  const conversation_id = "01234567-89ab-4cde-8123-456789abcdef";
  const socket = Socket.instances.at(-1);
  socket.emit("message", { data: JSON.stringify({ type: "session.banner", session_id: conversation_id }) });
  socket.emit("message", { data: JSON.stringify({ type: "evolution.state", conversation_id, projection: {} }) });
  client.sendEvolutionRequest({ type: "evolution.inspect", conversation_id });
  assert.deepEqual(events, ["session.banner", "evolution.state"]);
  assert.deepEqual(socket.sent.at(-1), { type: "evolution.inspect", conversation_id });
  await client.openSession("next");
  assert.throws(() => client.sendEvolutionRequest({ type: "evolution.inspect", conversation_id }), /current session/);
  client.destroy();
});

test("shell binds lane-aware row clicks, awaits opens, renders sync hint and retains managed flow", () => {
  assert.ok(/row\.dataset\.sidecarLane/.test(shell));
  assert.equal((shell.match(/await openSessionThenBeginSwitch\(/g) ?? []).length, 3);
  assert.ok(/\(\) => client\.openSession\(sid, lane\)/.test(shell));
  assert.ok(/await client\.createSandboxSession\(\)/.test(shell));
  assert.ok(/createElement\("p"\)/.test(shell) && /data-shell-dev-sync-hint/.test(shell));
  for (const name of ["createManagedReviewController", "mountManagedSourceSelection", "createManagedSourceCheckpoint", "executeManagedSelectedMerge", "evolution.state"]) {
    assert.ok(shell.includes(name), name);
  }
});

test("sync hint only reads source status with affirmative mutable Host capability", async () => {
  const classes = new Set(["hidden"]);
  const hint = { textContent: "", classList: { add: (name) => classes.add(name), remove: (name) => classes.delete(name) } };
  let statusReads = 0;
  const desktop = {
    getSessionLaneCapabilities: async () => ({ legacySessionLanes: false }),
    getSandboxAdoptStatus: async () => { statusReads++; return { needsSandboxSync: true, stableAheadOfSandbox: 2 }; },
  };
  const refresh = loadShellFunction("refreshShellDevSyncHint", {
    shellDevEl: { querySelector: () => hint }, window: { myAgentDesktop: desktop },
  });
  await refresh();
  assert.equal(statusReads, 0);
  assert.equal(hint.textContent, "");
  desktop.getSessionLaneCapabilities = async () => ({ legacySessionLanes: true });
  await refresh();
  assert.equal(statusReads, 1);
  assert.ok(hint.textContent.includes("2 个提交"));
  assert.ok(hint.textContent.includes("不会自动同步"));
  assert.equal(classes.has("hidden"), false);
});

test("generated-image render fingerprints change with the actual image path", () => {
  const print = loadShellFunction("blockPrint", {});
  const first = print({ kind: "generated-image", turnIndex: 1, agentPath: "one.png" });
  const next = print({ kind: "generated-image", turnIndex: 1, agentPath: "two.png" });
  assert.equal(typeof first, "string");
  assert.notEqual(first, next);
});
