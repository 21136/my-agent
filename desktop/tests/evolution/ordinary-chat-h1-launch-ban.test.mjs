import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { createHash, randomUUID } from "node:crypto";
import { existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { createRequire } from "node:module";
import { assertH1SpawnEnvironment, buildH1ElectronMainOutputs, buildH1NativeLaunchPlan, cleanupFailedH1NativeLaunch, closeH1BrowserViaCdp, createH1SpawnEnvironmentEvidence, freezeHostFixture, launchFrozenHost, startFrozenHost, startH1ControlledLifecycle, verifyFrozenHostFixture } from "../../scripts/verify-evolution-a.mjs";

const sandboxRoot = path.resolve(process.cwd());
const hash = value => createHash("sha256").update(value).digest("hex");
function createRun() {
  const runRoot = path.join(sandboxRoot, "artifacts", `ordinary-chat-h1-launch-ban-${randomUUID()}`);
  mkdirSync(runRoot, { recursive: false });
  writeFileSync(path.join(runRoot, ".evolution-test-run"), "evolution-a\n", { flag: "wx" });
  const identity = lstatSync(runRoot);
  const runtime = path.join(runRoot, "runtime");
  mkdirSync(path.join(runtime, "electron", "resources"), { recursive: true });
  const python = path.join(runtime, "python.exe"), electron = path.join(runtime, "electron", "electron.exe");
  writeFileSync(python, "fixture python", { flag: "wx" });
  writeFileSync(electron, "fixture electron", { flag: "wx" });
  writeFileSync(path.join(runtime, "electron", "resources", "resources.pak"), "fixture resource", { flag: "wx" });
  return { runRoot, python, electron, identity };
}
function cleanupOwnedRun(fixture) {
  const root = path.resolve(fixture.runRoot);
  const artifacts = path.join(sandboxRoot, "artifacts");
  const expectedArtifacts = realpathSync.native(artifacts);
  const current = lstatSync(root);
  assert.ok(current.isDirectory() && !current.isSymbolicLink(), "test run root must remain a real directory");
  assert.equal(realpathSync.native(root), root, "test run root must remain canonical");
  assert.equal(path.dirname(root), expectedArtifacts, "test run must remain an artifacts child");
  assert.equal(current.dev, fixture.identity.dev);
  assert.equal(current.ino, fixture.identity.ino);
  assert.equal(readFileSync(path.join(root, ".evolution-test-run"), "utf8"), "evolution-a\n");
  rmSync(root, { recursive: true, force: false });
}
function fixtureFactories() {
  return {
    freezeControlRelease: async ({ outputRoot }) => {
      const root = path.join(outputRoot, "fixture-release");
      mkdirSync(root, { recursive: true });
      const manifest_path = path.join(root, "manifest.json");
      writeFileSync(manifest_path, "{}\n", { flag: "wx" });
      return { root, manifest_path, release_id: "fixture-release" };
    },
    capturePythonEnvironment: executable => ({ executable, digest: `sha256:${hash("fixture-python-environment")}` }),
  };
}

test("ordinary-chat H1 launch plan binds Electron userData/sessionData/temp and app root to the marked run", async t => {
  const fixture = createRun(); t.after(() => cleanupOwnedRun(fixture));
  const esbuildEntry = process.env.H1_TEST_ESBUILD_ENTRY;
  assert.ok(path.isAbsolute(esbuildEntry), "test-only esbuild injection must be an absolute read-only module path");
  const esbuildInfo = lstatSync(esbuildEntry); assert.ok(esbuildInfo.isFile() && !esbuildInfo.isSymbolicLink());
  assert.equal(realpathSync.native(esbuildEntry), path.resolve(esbuildEntry));
  const esbuild = createRequire(import.meta.url)(esbuildEntry);
  const electronBuild = await buildH1ElectronMainOutputs({ sandboxRoot, runRoot: fixture.runRoot }, { build: esbuild.build.bind(esbuild) });
  const frozen = await freezeHostFixture({ sandboxRoot, runRoot: fixture.runRoot, trustedPythonExecutable: fixture.python, electronExecutable: fixture.electron, chatFixtureProfile: "ordinary-chat-h1-v1" }, { factories: fixtureFactories(), electronBuildRoot: electronBuild.output_root, rendererBuild: true });
  const verified = verifyFrozenHostFixture({ sandboxRoot, runRoot: fixture.runRoot, ...frozen }, { capturePythonEnvironment: fixtureFactories().capturePythonEnvironment });
  const plan = buildH1NativeLaunchPlan(verified, { mode: "controlled", cdp: { enabled: true }, sourceEnvironment: { PATH: "C:/Windows/System32", OPENAI_API_KEY: "must-not-forward" } });
  const profile = verified.profile_root;
  assert.equal(plan.options.cwd, verified.app_root);
  assert.equal(plan.options.env.MY_AGENT_ROOT, verified.app_root);
  assert.equal(plan.options.env.MY_AGENT_H1_PROFILE_ROOT, profile);
  assert.equal(plan.options.env.MY_AGENT_H1_RUN_ID, path.basename(fixture.runRoot));
  assert.equal(plan.options.env.MY_AGENT_ELECTRON_USER_DATA, path.join(profile, "electron-userData"));
  assert.equal(plan.options.env.MY_AGENT_ELECTRON_SESSION_DATA, path.join(profile, "electron-sessionData"));
  assert.equal(plan.options.env.TEMP, path.join(profile, "tmp"));
  assert.equal(plan.options.env.OPENAI_API_KEY, undefined);
  assert.throws(() => assertH1SpawnEnvironment({ ...plan.options.env, MY_AGENT_H1_RUN_ID: "changed-run" }, verified.app_root, profile), /marked run identity/);
  assert.throws(() => assertH1SpawnEnvironment({ ...plan.options.env, MY_AGENT_ELECTRON_SESSION_DATA: "C:/outside" }, verified.app_root, profile), /isolated to this run profile/);
  assert.deepEqual(plan.args.slice(-1), [path.join(verified.app_root, "desktop")]);
  const launchInput = { sandboxRoot, runRoot: fixture.runRoot, ...frozen };
  const safeOptions = { capturePythonEnvironment: fixtureFactories().capturePythonEnvironment };
  const savedOptIn = process.env.REAL_ELECTRON_CHAT_H1_OPT_IN;
  const savedGuiOptIn = process.env.REAL_ELECTRON_CHAT_H1_GUI_OPT_IN;
  try {
    delete process.env.REAL_ELECTRON_CHAT_H1_OPT_IN;
    delete process.env.REAL_ELECTRON_CHAT_H1_GUI_OPT_IN;
    await assert.rejects(launchFrozenHost(launchInput, safeOptions), /REAL_ELECTRON_CHAT_H1_OPT_IN=1/);
    await assert.rejects(startFrozenHost(launchInput, safeOptions), /REAL_ELECTRON_CHAT_H1_OPT_IN=1/);
    process.env.REAL_ELECTRON_CHAT_H1_OPT_IN = "1";
    await assert.rejects(launchFrozenHost(launchInput, safeOptions), /REAL_ELECTRON_CHAT_H1_GUI_OPT_IN=1/);
    await assert.rejects(startFrozenHost(launchInput, safeOptions), /REAL_ELECTRON_CHAT_H1_GUI_OPT_IN=1/);
    process.env.REAL_ELECTRON_CHAT_H1_GUI_OPT_IN = "1";
    await assert.rejects(launchFrozenHost(launchInput, safeOptions), /custom Python environment attestation is forbidden/);
    await assert.rejects(startFrozenHost(launchInput, safeOptions), /custom Python environment attestation is forbidden/);
  } finally {
    if (savedOptIn === undefined) delete process.env.REAL_ELECTRON_CHAT_H1_OPT_IN; else process.env.REAL_ELECTRON_CHAT_H1_OPT_IN = savedOptIn;
    if (savedGuiOptIn === undefined) delete process.env.REAL_ELECTRON_CHAT_H1_GUI_OPT_IN; else process.env.REAL_ELECTRON_CHAT_H1_GUI_OPT_IN = savedGuiOptIn;
  }
  assert.equal(existsSync(path.join(fixture.runRoot, "host", "logs")), false, "opt-in rejection must precede host launch side effects");
});

test("controlled H1 spawn evidence is defined from the validated environment", () => {
  const env = { SYSTEMROOT: "C:/Windows", PATH: "C:/Windows/System32", MY_AGENT_ROOT: "F:/run/host/app", USERPROFILE: "F:/run/host/profile/userprofile", APPDATA: "F:/run/host/profile/appdata/Roaming", LOCALAPPDATA: "F:/run/host/profile/appdata/Local", TEMP: "F:/run/host/profile/tmp", PYTHONNOUSERSITE: "1", PYTHONDONTWRITEBYTECODE: "1" };
  const evidence = createH1SpawnEnvironmentEvidence(env);
  assert.deepEqual(evidence.forwarded_keys, Object.keys(env).sort());
  assert.deepEqual(evidence.credential_env_forwarded_keys, []);
  assert.equal(evidence.isolated_agent_root, env.MY_AGENT_ROOT);
  assert.equal(evidence.isolated_temp, env.TEMP);
  assert.throws(() => createH1SpawnEnvironmentEvidence({ ...env, API_TOKEN: "not-forwarded" }), /credential-like/);
});

test("failed H1 start cleanup targets only its child handle", async () => {
  const child = new EventEmitter(); child.exitCode = null; child.signalCode = null;
  let childKills = 0, unrelatedKills = 0;
  child.kill = () => { childKills++; queueMicrotask(() => { child.signalCode = "SIGTERM"; child.emit("close", null, "SIGTERM"); }); return true; };
  const unrelated = { kill() { unrelatedKills++; } };
  await cleanupFailedH1NativeLaunch(child);
  assert.equal(childKills, 1);
  assert.equal(unrelatedKills, 0);
  assert.equal(child.signalCode, "SIGTERM");
});

test("startFrozenHost catches every post-spawn failure and cleans up the returned lifecycle/child", () => {
  const source = readFileSync(path.join(sandboxRoot, "desktop", "scripts", "verify-evolution-a.mjs"), "utf8");
  const start = source.indexOf("export async function startFrozenHost(");
  const end = source.indexOf("export function createT9ScriptedWebSocketDriver", start);
  const body = source.slice(start, end);
  const spawn = body.indexOf("const child = spawnProcess(");
  const lifecycle = body.indexOf("startH1ControlledLifecycle(child");
  const cleanup = body.indexOf("cleanupFailedH1NativeLaunch(child, lifecycle)");
  const failureThrow = body.lastIndexOf("throw error;");
  assert.ok(spawn >= 0 && spawn < lifecycle && lifecycle < cleanup && cleanup < failureThrow);
  assert.ok(body.includes("const spawnEnvironmentEvidence = createH1SpawnEnvironmentEvidence(env)"));
});
test("H1 Browser.close uses only the validated dynamic loopback browser endpoint", async () => {
  const prior = globalThis.WebSocket;
  class FakeWebSocket extends EventTarget {
    static behavior = "ack"; static commands = []; static onBrowserClose = null;
    constructor(url) { super(); this.url = url; queueMicrotask(() => this.dispatchEvent(new Event("open"))); }
    send(raw) {
      const command = JSON.parse(raw); FakeWebSocket.commands.push(command);
      if (command.method !== "Browser.close") return;
      FakeWebSocket.onBrowserClose?.();
      if (FakeWebSocket.behavior === "transport-close-during-send") { this.dispatchEvent(new Event("error")); this.dispatchEvent(new Event("close")); return; }
      if (FakeWebSocket.behavior === "transport-close-send-throws") { this.dispatchEvent(new Event("close")); this.dispatchEvent(new Event("error")); throw new Error("synthetic send failure"); }
      if (FakeWebSocket.behavior === "ack") queueMicrotask(() => this.dispatchEvent(new MessageEvent("message", { data: JSON.stringify({ id: command.id, result: {} }) })));
      if (FakeWebSocket.behavior === "error") queueMicrotask(() => this.dispatchEvent(new MessageEvent("message", { data: JSON.stringify({ id: command.id, error: { message: "synthetic rejection" } }) })));
    }
    close() { queueMicrotask(() => this.dispatchEvent(new Event("close"))); }
  }
  globalThis.WebSocket = FakeWebSocket;
  try {
    const ok = await closeH1BrowserViaCdp("ws://127.0.0.1:49152/devtools/browser/h1-test", { timeoutMs: 100 });
    assert.deepEqual(FakeWebSocket.commands, [{ id: 1, method: "Browser.close" }]);
    assert.equal(ok.command_sent, true); assert.equal(ok.acknowledged, true);
    FakeWebSocket.behavior = "transport-close-during-send";
    const closing = await closeH1BrowserViaCdp("ws://127.0.0.1:49152/devtools/browser/h1-test", { timeoutMs: 100 });
    assert.deepEqual(closing, { command_sent: true, acknowledged: false }, "send-time transport events are classified only after send returns normally");
    FakeWebSocket.behavior = "transport-close-send-throws";
    await assert.rejects(closeH1BrowserViaCdp("ws://127.0.0.1:49152/devtools/browser/h1-test", { timeoutMs: 100 }), /could not be sent/);
    await assert.rejects(closeH1BrowserViaCdp("ws://localhost:49152/devtools/browser/h1-test", { timeoutMs: 50 }), /dynamic 127.0.0.1/);
    FakeWebSocket.behavior = "error";
    await assert.rejects(closeH1BrowserViaCdp("ws://127.0.0.1:49152/devtools/browser/h1-test", { timeoutMs: 100 }), /Browser.close command was rejected/);
    FakeWebSocket.behavior = "timeout";
    await assert.rejects(closeH1BrowserViaCdp("ws://127.0.0.1:49152/devtools/browser/h1-test", { timeoutMs: 25 }), /Browser.close timed out/);
  } finally { globalThis.WebSocket = prior; }
});

test("ordinary-chat H1 controlled close uses CDP while retaining private stdin bootstrap", async t => {
  const fixture = createRun(); const logs = path.join(fixture.runRoot, "host", "logs"); mkdirSync(logs, { recursive: true });
  t.after(() => cleanupOwnedRun(fixture));
  const prior = globalThis.WebSocket; const frames = []; let childCloseFromBrowser = null;
  class FakeWebSocket extends EventTarget {
    static commands = [];
    constructor(url) { super(); this.url = url; queueMicrotask(() => this.dispatchEvent(new Event("open"))); }
    send(raw) { const command = JSON.parse(raw); FakeWebSocket.commands.push(command); if (command.method === "Browser.close") { childCloseFromBrowser?.(); queueMicrotask(() => this.dispatchEvent(new MessageEvent("message", { data: JSON.stringify({ id: command.id, result: {} }) }))); } }
    close() { queueMicrotask(() => this.dispatchEvent(new Event("close"))); }
  }
  globalThis.WebSocket = FakeWebSocket;
  const child = new EventEmitter(); child.exitCode = null; child.signalCode = null; child.stdout = new EventEmitter(); child.stderr = new EventEmitter();
  child.stdin = { write(raw) { frames.push(JSON.parse(raw)); }, end(raw) { if (raw) frames.push(JSON.parse(raw)); } };
  child.kill = () => { child.signalCode = "SIGTERM"; queueMicrotask(() => child.emit("close", null, "SIGTERM")); return true; };
  childCloseFromBrowser = () => { child.exitCode = 0; child.signalCode = null; queueMicrotask(() => child.emit("close", 0, null)); };
  const before = { digest: `sha256:${hash("a")}` };
  try {
    const starting = startH1ControlledLifecycle(child, { profile: "ordinary-chat-h1-v1", token: "b".repeat(64), cdpEnabled: true, stdoutPath: path.join(logs, "stdout.log"), stderrPath: path.join(logs, "stderr.log"), exitPath: path.join(logs, "exit.json"), runRoot: fixture.runRoot, pythonExecutable: fixture.python, pythonPackages: [], beforeEnvironment: before, captureEnvironment: () => before, readyTimeoutMs: 500, shutdownTimeoutMs: 100 });
    child.stderr.emit("data", Buffer.from("DevTools listening on ws://127.0.0.1:49152/devtools/browser/h1-test\n"));
    child.stdout.emit("data", Buffer.from(JSON.stringify({ type: "ordinary-chat-h1-host-ready", host: "127.0.0.1", port: 43210, renderer_host: "127.0.0.1", renderer_port: 43211 }) + "\n"));
    const lifecycle = await starting; const exit = await lifecycle.close();
    assert.deepEqual(frames, [{ type: "ordinary-chat-h1.control.bootstrap", token: "b".repeat(64) }], "H1 must not use stdin shutdown");
    assert.deepEqual(FakeWebSocket.commands, [{ id: 1, method: "Browser.close" }]);
    assert.equal(exit.code, 0); assert.equal(exit.signal, null); assert.equal(exit.forced_termination, false); assert.equal(exit.shutdown_method, "cdp_browser_close");
  } finally { globalThis.WebSocket = prior; }
});
test("H1 CDP close timeout remains marked as forced failure", async t => {
  const fixture = createRun(); const logs = path.join(fixture.runRoot, "host", "logs"); mkdirSync(logs, { recursive: true });
  t.after(() => cleanupOwnedRun(fixture));
  const prior = globalThis.WebSocket;
  class FakeWebSocket extends EventTarget {
    constructor(url) { super(); this.url = url; queueMicrotask(() => this.dispatchEvent(new Event("open"))); }
    send(raw) { const command = JSON.parse(raw); queueMicrotask(() => this.dispatchEvent(new MessageEvent("message", { data: JSON.stringify({ id: command.id, result: {} }) }))); }
    close() { queueMicrotask(() => this.dispatchEvent(new Event("close"))); }
  }
  globalThis.WebSocket = FakeWebSocket;
  const child = new EventEmitter(); child.exitCode = null; child.signalCode = null; child.stdout = new EventEmitter(); child.stderr = new EventEmitter();
  child.stdin = { write() {}, end() { assert.fail("ordinary H1 must not use stdin shutdown"); } };
  child.kill = () => { child.signalCode = "SIGTERM"; queueMicrotask(() => child.emit("close", null, "SIGTERM")); return true; };
  const before = { digest: `sha256:${hash("e")}` };
  try {
    const starting = startH1ControlledLifecycle(child, { profile: "ordinary-chat-h1-v1", token: "f".repeat(64), cdpEnabled: true, stdoutPath: path.join(logs, "stdout.log"), stderrPath: path.join(logs, "stderr.log"), exitPath: path.join(logs, "exit.json"), runRoot: fixture.runRoot, pythonExecutable: fixture.python, pythonPackages: [], beforeEnvironment: before, captureEnvironment: () => before, readyTimeoutMs: 500, shutdownTimeoutMs: 25 });
    child.stderr.emit("data", Buffer.from("DevTools listening on ws://127.0.0.1:49152/devtools/browser/h1-test\n"));
    child.stdout.emit("data", Buffer.from(JSON.stringify({ type: "ordinary-chat-h1-host-ready", host: "127.0.0.1", port: 43210, renderer_host: "127.0.0.1", renderer_port: 43211 }) + "\n"));
    const lifecycle = await starting; const exit = await lifecycle.close();
    assert.equal(exit.forced_termination, true); assert.equal(exit.signal, "SIGTERM"); assert.equal(exit.shutdown_request_sent, true); assert.equal(exit.shutdown_method, "cdp_browser_close");
    const runner = readFileSync(path.join(sandboxRoot, "desktop", "scripts", "run-ordinary-chat-h1.mjs"), "utf8");
    assert.match(runner, /exit\.forced_termination === true/);
  } finally { globalThis.WebSocket = prior; }
});
test("T4 controlled lifecycle retains its stdin shutdown frame", async t => {
  const fixture = createRun(); const logs = path.join(fixture.runRoot, "host", "logs"); mkdirSync(logs, { recursive: true });
  t.after(() => cleanupOwnedRun(fixture));
  const child = new EventEmitter(); child.exitCode = null; child.signalCode = null; child.stdout = new EventEmitter(); child.stderr = new EventEmitter();
  const frames = []; child.stdin = { write(raw) { frames.push(JSON.parse(raw)); }, end(raw) { frames.push(JSON.parse(raw)); child.exitCode = 0; queueMicrotask(() => child.emit("close", 0, null)); } }; child.kill = () => { child.signalCode = "SIGTERM"; queueMicrotask(() => child.emit("close", null, "SIGTERM")); return true; };
  const before = { digest: `sha256:${hash("c")}` };
  const starting = startH1ControlledLifecycle(child, { profile: "t4-recovery-node-v1", token: "d".repeat(64), stdoutPath: path.join(logs, "stdout.log"), stderrPath: path.join(logs, "stderr.log"), exitPath: path.join(logs, "exit.json"), runRoot: fixture.runRoot, pythonExecutable: fixture.python, pythonPackages: [], beforeEnvironment: before, captureEnvironment: () => before, readyTimeoutMs: 500, shutdownTimeoutMs: 100 });
  child.stdout.emit("data", Buffer.from(JSON.stringify({ type: "evolution-test-host-ready", host: "127.0.0.1", port: 43212 }) + "\n"));
  const lifecycle = await starting; const exit = await lifecycle.close();
  assert.deepEqual(frames.map(item => item.type), ["t9.control.bootstrap", "t9.control.shutdown"]);
  assert.equal(exit.code, 0); assert.equal(exit.signal, null); assert.equal(exit.forced_termination, false); assert.equal(exit.shutdown_method, "stdin_control_frame");
});
test("native launch APIs run seal, AgentPaths, and spawn-environment checks before Electron spawn", () => {
  const verifyPath = path.join(sandboxRoot, "desktop", "scripts", "verify-evolution-a.mjs");
  const source = readFileSync(verifyPath, "utf8");
  for (const [name, endMarker] of [["launchFrozenHost", "export function resolveH1HostCwd"], ["startFrozenHost", "export function createT9ScriptedWebSocketDriver"]]) {
    const start = source.indexOf(`export async function ${name}(`);
    const end = source.indexOf(endMarker, start);
    assert.ok(start >= 0 && end > start, `${name} implementation must be present`);
    const body = source.slice(start, end);
    const checks = ["verifyFrozenHostFixture(input", "assertH1LaunchProfileEnabled(verified.profile, options)", "verifyAgentPathsBeforeH1Start(verified)", "buildH1NativeLaunchPlan(verified", "mkdirSync(logRoot", "spawnProcess(plan.executable"];
    const positions = checks.map(check => body.indexOf(check));
    assert.ok(positions.every(position => position >= 0), `${name} must retain every pre-spawn check`);
    assert.deepEqual(positions, [...positions].sort((a, b) => a - b), `${name} must validate before creating logs or spawning Electron`);
  }
  const planStart = source.indexOf("export function buildH1NativeLaunchPlan(");
  const planEnd = source.indexOf("export function awaitH1ChildExit(", planStart);
  const plan = source.slice(planStart, planEnd);
  assert.ok(plan.includes("assertH1SpawnEnvironment(env, verified.app_root, verified.profile_root)"), "launch plan must validate the allowlisted run-local environment");
});
test("native Electron main validates isolated H1 paths and reads Electron bindings back before ready", () => {
  const mainPath = path.join(sandboxRoot, "desktop", "electron", "main.ts");
  const source = readFileSync(mainPath, "utf8");
  const profile = source.indexOf('const ordinaryChatH1 = evolutionTestHostContext?.profile.chat_fixture_profile === "ordinary-chat-h1-v1";');
  const isolation = source.indexOf("? assertOrdinaryChatH1Isolation(evolutionTestHostContext)");
  const isolationProof = source.indexOf('if (!identity || Object.keys(identity).length !== 4', profile);
  const setTemp = source.indexOf('app.setPath("temp", isolated.temp)', profile);
  const setUserData = source.indexOf('app.setPath("userData", isolated.userData)', setTemp);
  const setSessionData = source.indexOf('app.setPath("sessionData", isolated.sessionData)', setUserData);
  const readTemp = source.indexOf('app.getPath("temp")', setSessionData);
  const readUserData = source.indexOf('app.getPath("userData")', readTemp);
  const readSessionData = source.indexOf('app.getPath("sessionData")', readUserData);
  const readbackCompare = source.indexOf('!samePath(sessionData, isolated.sessionData)', readSessionData);
  const ready = source.indexOf("app.whenReady()");
  assert.notEqual(profile, -1);
  assert.notEqual(isolation, -1, "ordinary H1 must run its isolation proof instead of an unconditional ban");
  assert.notEqual(isolationProof, -1, "profile marker validation must remain active");
  assert.ok(profile < isolation && isolation < isolationProof && isolationProof < setTemp);
  assert.ok(setTemp < setUserData && setUserData < setSessionData && setSessionData < readTemp);
  assert.ok(readTemp < readUserData && readUserData < readSessionData && readSessionData < readbackCompare);
  assert.ok(readbackCompare < ready, "Electron path bindings must be read back before app readiness");
  for (const later of ["async function runSidecarStartup()", "new BrowserWindow("]) assert.ok(readbackCompare < source.indexOf(later), "path readback must precede " + later);
  assert.doesNotMatch(source, /if\s*\(ordinaryChatH1\)\s*throw Error\(["']ordinary-chat H1 fixture is disabled/u);
});