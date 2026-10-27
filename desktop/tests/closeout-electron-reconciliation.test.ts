import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import { stripTypeScriptTypes } from "node:module";
import path from "node:path";
import { PassThrough } from "node:stream";
import { test, type TestContext } from "node:test";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";

let subject: Record<string, any> = {};
try { subject = await import("../electron/legacy-sandbox-lane.ts"); }
catch (error: any) { if (error.code !== "ERR_MODULE_NOT_FOUND") throw error; }
function api() {
  assert.equal(typeof subject.createLegacySandboxLane, "function", "owned legacy lane lifecycle helper is missing");
  return subject;
}

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const main = readFileSync(path.join(desktopRoot, "electron/main.ts"), "utf8");
const preload = readFileSync(path.join(desktopRoot, "electron/preload.ts"), "utf8");

function fixture(t: TestContext) {
  const tempRoot = path.resolve(os.tmpdir());
  const stable = mkdtempSync(path.join(tempRoot, "legacy-lane-test-"));
  t.after(() => {
    assert.equal(path.dirname(path.resolve(stable)), tempRoot);
    assert.ok(path.basename(stable).startsWith("legacy-lane-test-"));
    rmSync(stable, { recursive: true, force: true });
  });
  const sandbox = path.join(stable, ".worktrees", "sandbox");
  for (const directory of ["agent-core", "desktop", "evolve", "data/sessions"]) mkdirSync(path.join(sandbox, directory), { recursive: true });
  writeFileSync(path.join(sandbox, "agent-core/server.py"), "# inert test fixture\n");
  writeFileSync(path.join(sandbox, "desktop/package.json"), "{}\n");
  writeFileSync(path.join(sandbox, "evolve/_index.toml"), "# inert test fixture\n");
  const lock = path.join(sandbox, "data/sessions/.interface.lock");
  writeFileSync(lock, "unknown-owner\n");
  return { stable, sandbox, lock };
}

class FakeChild extends EventEmitter {
  stdout = new PassThrough();
  stderr = new PassThrough();
  kills = 0;
  killed = false;
  exitOnKill = true;
  kill() {
    this.kills++; this.killed = true;
    if (this.exitOnKill) this.emit("exit", null, "SIGTERM");
    return true;
  }
  ready(port = 23456, host = "127.0.0.1") { this.stdout.write(JSON.stringify({ ready: true, host, port }) + "\n"); }
}

function laneFor(t: TestContext, overrides: Record<string, unknown> = {}) {
  const f = fixture(t), child = new FakeChild();
  let spawns = 0;
  const lane = api().createLegacySandboxLane({
    agentRoot: f.sandbox, allowed: () => true,
    spawn: (root: string) => { assert.equal(root, f.sandbox); spawns++; return child; },
    readyTimeoutMs: 1000, ...overrides,
  });
  t.after(() => lane.stop());
  return { ...f, child, lane, spawns: () => spawns };
}

test("current sandbox and stable Host resolve the same exact sandbox layout despite ambient roots", (t) => {
  const f = fixture(t), resolve = api().resolveRegisteredSandboxRoot;
  const previous = process.env.MY_AGENT_SANDBOX_ROOT;
  process.env.MY_AGENT_SANDBOX_ROOT = path.join(f.stable, "arbitrary-ambient-root");
  try {
    assert.equal(resolve(f.sandbox), f.sandbox);
    assert.equal(resolve(f.stable), f.sandbox);
    assert.equal(resolve(path.join(f.stable, "unregistered")), null);
    assert.equal(existsSync(path.join(f.sandbox, ".worktrees/sandbox")), false);
  } finally {
    if (previous === undefined) delete process.env.MY_AGENT_SANDBOX_ROOT;
    else process.env.MY_AGENT_SANDBOX_ROOT = previous;
  }
});

test("disabled Hosts do not resolve roots, inspect primary info, or spawn", async (t) => {
  const f = laneFor(t, { agentRoot: "not-a-registered-root", allowed: () => false,
    primary: () => { throw Error("must not inspect primary"); }, spawn: () => { throw Error("must not spawn"); } });
  assert.equal(f.lane.get(), null);
  assert.equal((await f.lane.ensure()).ok, false);
});

test("a verified owned primary for this sandbox is reused without creating or killing a second child", async (t) => {
  const f = fixture(t); let owned = true, running = true;
  const lane = api().createLegacySandboxLane({ agentRoot: f.sandbox, allowed: () => true,
    primary: () => ({ root: f.sandbox, owned, running, info: { host: "127.0.0.1", port: 12345 } }),
    spawn: () => { throw Error("must reuse exact owned primary"); }, readyTimeoutMs: 1000 });
  assert.deepEqual(await lane.ensure(), { ok: true, host: "127.0.0.1", port: 12345 });
  lane.stop();
  assert.equal(readFileSync(f.lock, "utf8"), "unknown-owner\n");
  owned = false; assert.equal(lane.get(), null);
  owned = true; running = false; assert.equal(lane.get(), null);
});

test("unowned primary endpoints are ignored and only the new child's readiness establishes lane info", async (t) => {
  const f = laneFor(t, { primary: () => ({ root: "foreign-root", owned: false, running: true, info: { host: "127.0.0.1", port: 8766 } }) });
  assert.equal(f.lane.get(), null);
  const pending = f.lane.ensure();
  assert.equal(f.spawns(), 1);
  assert.equal(f.lane.get(), null);
  f.child.stdout.write('{"ready":true,"host":"127.0.0.1",');
  assert.equal(f.lane.get(), null);
  f.child.stdout.write('"port":23456}\n');
  assert.deepEqual(await pending, { ok: true, host: "127.0.0.1", port: 23456 });
  f.lane.stop();
  assert.equal(f.child.kills, 1);
  assert.equal(readFileSync(f.lock, "utf8"), "unknown-owner\n");
});

test("primary reuse rejects either unowned same-root or owned foreign-root endpoints", async (t) => {
  const f = fixture(t);
  for (const primary of [
    { root: f.sandbox, owned: false, running: true, info: { host: "127.0.0.1", port: 8766 } },
    { root: f.stable, owned: true, running: true, info: { host: "127.0.0.1", port: 8766 } },
  ]) {
    const child = new FakeChild(); let spawns = 0;
    const lane = api().createLegacySandboxLane({ agentRoot: f.sandbox, allowed: () => true, primary: () => primary,
      spawn: () => { spawns++; return child; }, readyTimeoutMs: 1000 });
    assert.equal(lane.get(), null);
    const pending = lane.ensure(); assert.equal(spawns, 1);
    child.ready(); assert.equal((await pending).port, 23456); lane.stop();
  }
});

test("concurrent ensure requests create one child", async (t) => {
  const f = laneFor(t);
  const first = f.lane.ensure(), second = f.lane.ensure();
  assert.equal(f.spawns(), 1);
  f.child.ready();
  assert.deepEqual(await first, await second);
});

test("startup timeout terminates the owned child, hides late readiness, and preserves unknown locks", async (t) => {
  const f = laneFor(t, { readyTimeoutMs: 5 });
  await assert.rejects(f.lane.ensure(), /timeout/i);
  assert.equal(f.child.kills, 1);
  f.child.ready(); assert.equal(f.lane.get(), null);
  assert.equal(readFileSync(f.lock, "utf8"), "unknown-owner\n");
});

test("spawn errors terminate the child and release startup state after its exit", async (t) => {
  const f = laneFor(t);
  const pending = f.lane.ensure();
  f.child.emit("error", Error("spawn failure"));
  await assert.rejects(pending, /spawn failure/);
  assert.equal(f.child.kills, 1);
  assert.equal(f.lane.get(), null);
  assert.equal(readFileSync(f.lock, "utf8"), "unknown-owner\n");
});

test("stopping during startup settles the request and leaves lock cleanup to its owner", async (t) => {
  const f = laneFor(t); const pending = f.lane.ensure();
  f.lane.stop();
  assert.equal((await pending).ok, false);
  assert.equal(f.child.kills, 1);
  assert.equal(readFileSync(f.lock, "utf8"), "unknown-owner\n");
});

test("a child whose termination is unconfirmed stays fenced from replacement", async (t) => {
  const f = laneFor(t, { readyTimeoutMs: 5 }); f.child.exitOnKill = false;
  await assert.rejects(f.lane.ensure(), /timeout/i);
  assert.equal((await f.lane.ensure()).ok, false);
  assert.equal(f.spawns(), 1);
  f.child.emit("exit", 1);
});

test("lock conflicts stop only the spawned child and preserve the foreign lock", async (t) => {
  const f = laneFor(t); const pending = f.lane.ensure();
  f.child.stdout.write('{"error":"lock_conflict"}\n');
  assert.equal((await pending).ok, false);
  assert.equal(f.child.kills, 1);
  assert.equal(readFileSync(f.lock, "utf8"), "unknown-owner\n");
});

test("nonloopback and invalid-port ready frames never become renderer endpoints", async (t) => {
  for (const [host, port] of [["10.0.0.1", 1234], ["127.0.0.1", 0], ["127.0.0.1", 65536]] as const) {
    const f = laneFor(t); const pending = f.lane.ensure(); f.child.ready(port, host);
    await assert.rejects(pending, /invalid.*ready/i);
    assert.equal(f.lane.get(), null); assert.equal(f.child.kills, 1);
  }
});

test("trusted IPC checks reject reads/starts before invocation and suppress endpoints after navigation", async () => {
  let trusted = false, reads = 0, starts = 0;
  const handlers = api().createTrustedLegacyLaneHandlers({ isTrusted: () => trusted, allowed: () => true,
    get: () => { reads++; return { host: "127.0.0.1", port: 1234 }; },
    ensure: async () => { starts++; trusted = false; return { ok: true, host: "127.0.0.1", port: 1234 }; } });
  assert.equal(handlers.get({}), null);
  assert.equal((await handlers.ensure({})).ok, false);
  assert.equal(reads, 0); assert.equal(starts, 0);
  trusted = true;
  assert.equal((await handlers.ensure({})).ok, false);
  assert.equal(starts, 1);
});

test("the main sender guard can validate the pinned native origin without reading persisted config", () => {
  const source = main.match(/function assertShellDevWorkbenchSender\([\s\S]*?\n\}/)?.[0];
  assert.ok(source);
  const frame = { url: "pinned-origin" }, sender = { id: 7, mainFrame: frame };
  const guard = runInNewContext(stripTypeScriptTypes(`(${source})`), {
    workbenchWindow: { isDestroyed: () => false, webContents: sender },
    rendererOrigin: (url: string) => url,
    expectedWorkbenchOrigin: () => { throw Error("must not read persisted config for lane IPC"); },
    isTrustedSandboxAdoptSender: (input: any) => input.senderId === input.windowId && input.isMainFrame && input.origin === input.expectedOrigin,
  });
  assert.equal(guard({ sender, senderFrame: frame }, "pinned-origin"), true);
  assert.equal(guard({ sender, senderFrame: { url: "foreign-origin" } }, "pinned-origin"), false);
});

test("session-lane capability needs both trusted sender and mutable Host, without inspecting a lane", () => {
  let trusted = false, allowed = true;
  const handlers = api().createTrustedLegacyLaneHandlers({
    isTrusted: () => trusted, allowed: () => allowed,
    get: () => { throw Error("capability must not inspect roots or endpoints"); },
    ensure: () => { throw Error("capability must not start a child"); },
  });
  assert.deepEqual(handlers.capabilities({}), { legacySessionLanes: false });
  trusted = true;
  assert.deepEqual(handlers.capabilities({}), { legacySessionLanes: true });
  allowed = false;
  assert.deepEqual(handlers.capabilities({}), { legacySessionLanes: false });
});

test("protected Host capability and lane calls stop before sender/config and endpoint access", async () => {
  const handlers = api().createTrustedLegacyLaneHandlers({
    allowed: () => false,
    isTrusted: () => { throw Error("must not consult sender/config on protected Host"); },
    get: () => { throw Error("must not read a protected lane"); },
    ensure: () => { throw Error("must not start a protected lane"); },
  });
  assert.deepEqual(handlers.capabilities({}), { legacySessionLanes: false });
  assert.equal(handlers.get({}), null);
  assert.equal((await handlers.ensure({})).ok, false);
});

test("main uses the trusted lane adapter and preload retains the coordinated IPC names", () => {
  assert.match(main, /createTrustedLegacyLaneHandlers/);
  assert.match(main, /assertShellDevWorkbenchSender\(event, legacySessionWorkbenchOrigin\)/);
  assert.match(main, /sidecar:get-sandbox[\s\S]{0,100}legacySandboxLaneIpc\.get\(event\)/);
  assert.match(main, /sidecar:ensure-sandbox[\s\S]{0,100}legacySandboxLaneIpc\.ensure\(event\)/);
  assert.match(main, /sidecar:session-lane-capabilities[\s\S]{0,100}legacySandboxLaneIpc\.capabilities\(event\)/);
  assert.match(preload, /getSessionLaneCapabilities:\s*\(\)\s*=>\s*ipcRenderer\.invoke\("sidecar:session-lane-capabilities"\)/);
  assert.match(preload, /getSessionLaneCapabilities:\s*\(\)\s*=>\s*Promise<\{ legacySessionLanes: boolean \}>/);
  for (const name of ["getSandboxSidecar", "ensureSandboxSidecar", "syncSandboxWorktree", "resetShellDevMainLane", "onSessionControl"]) assert.ok(preload.includes(name));
});

test("sandbox sync compatibility endpoint remains disabled", () => {
  const handler = main.match(/ipcMain\.handle\("shell-dev:sync-sandbox"[\s\S]*?\n\}\);/);
  assert.ok(handler); assert.match(handler[0], /已禁用/);
  assert.doesNotMatch(handler[0], /syncSandboxWorktreeWithStable|prepareAndMerge|\.merge\(/);
});

function renderSelectedMergeDetails(withPreservation: boolean) {
  const source = main.match(/function nativeSelectedMergeDetails\([\s\S]*?\n\}/)?.[0];
  assert.ok(source);
  const render = runInNewContext(stripTypeScriptTypes(`(${source})`));
  const stable = { worktreeRoot: "stable-root", gitDir: "stable-git", commonDir: "common-git", branch: "stable-branch", head: "a".repeat(40), rawIndexSha256: "b".repeat(64) };
  const plan: any = {
    stable, sandbox: { ...stable, worktreeRoot: "sandbox-root", head: "c".repeat(40) },
    selectedPaths: ["desktop/electron/main.ts"],
    files: [{ path: "desktop/electron/main.ts", action: "replace", beforeSha256: "d".repeat(64), rawSha256: "e".repeat(64), expectedBaseOid: "f".repeat(40), expectedBaseMode: "100644", mode: "100644" }],
    blockers: [], planDigest: "1".repeat(64), checkpointDigest: "2".repeat(64),
    expectedMergedTree: "3".repeat(40), expectedCommit: "4".repeat(40), preservationDigest: "5".repeat(64), readyForLaunch: false,
  };
  if (withPreservation) plan.stablePreservation = {
    mode: "managed_stable_working_preservation_v1", rawBytes: 17,
    expectedTree: "6".repeat(40), expectedCommit: "7".repeat(40),
    files: [{ path: "desktop/electron/main.ts", sha256: "8".repeat(64), oid: "9".repeat(40), mode: "100644", bytesBase64: "RAW_BYTES_MUST_STAY_HOST_PRIVATE" }],
  };
  return { detail: render({ plan, checkpoint: {} }) as string, plan };
}

test("native preservation details disclose original identities and the full commit chain without raw bytes", () => {
  const { detail, plan } = renderSelectedMergeDetails(true), preservation = plan.stablePreservation;
  for (const value of [preservation.files[0].path, preservation.files[0].sha256, preservation.files[0].oid, preservation.files[0].mode,
    preservation.expectedTree, preservation.expectedCommit, plan.expectedCommit]) assert.ok(detail.includes(value), `missing full identity: ${value}`);
  assert.match(detail, /原始未提交工作改动.*已提交/);
  assert.match(detail, /不会.*继续.*未提交/);
  assert.ok(detail.includes(`Final expected commit parent=${preservation.expectedCommit}`));
  assert.ok(detail.includes(`Preservation parent=${plan.stable.head}`));
  assert.match(detail, /readyForLaunch=false/);
  assert.doesNotMatch(detail, /RAW_BYTES_MUST_STAY_HOST_PRIVATE|bytesBase64/);
});

test("non-preserving native details retain the stable HEAD as final expected parent", () => {
  const { detail, plan } = renderSelectedMergeDetails(false);
  assert.ok(detail.includes(`Final expected commit parent=${plan.stable.head}`));
});

test("preservation is a literal host prepare option with a native warning and default cancellation", () => {
  const prepare = main.match(/prepare: \(\{ roots, checkpoint \}\) => \{[\s\S]*?\n  \},/);
  assert.ok(prepare);
  assert.match(prepare[0], /preserveStableWorkingChanges:\s*true/);
  const confirm = main.match(/confirm: async \(\{ caller, request, plan \}\) => \{[\s\S]*?\n  \},/);
  assert.ok(confirm);
  assert.match(confirm[0], /message:\s*plan\.stablePreservation/);
  assert.match(confirm[0], /defaultId: 1, cancelId: 1/);
  assert.doesNotMatch(preload, /preserveStableWorkingChanges|bytesBase64|stablePreservation/);
});
