import { createHash, randomUUID, timingSafeEqual } from "node:crypto";
import { lstatSync, mkdirSync, readFileSync, readSync, realpathSync } from "node:fs";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { app, BrowserWindow, ipcMain } from "electron";
import { prepareManagedHostProfileIntentSync } from "../launcher/managed-production-context.mjs";
import {
  isManagedRendererRequestAllowed, managedReadonlyHealthMatches, managedReadonlyReadyMatches,
  managedHostStartupFailure, managedReadonlyStopMatches, ownedElectronPaths, parseManagedHostArguments, parseManagedHostBootstrap,
  withManagedPhysicalRuntimeReads,
  type ManagedHostStartupStage,
} from "./managed-host-context.js";
import { startManagedControlInput } from "./managed-host-control-reader.js";

type RecordValue = Record<string, unknown>;
const emit = (value: RecordValue) => process.stdout.write(JSON.stringify(value) + "\n");
const record = (value: unknown): value is RecordValue => value !== null && typeof value === "object" && !Array.isArray(value);
async function within<T>(promise: Promise<T>, label: string, timeout = 300000): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([promise, new Promise<T>((_, reject) => { timer = setTimeout(() => reject(new Error(label + " timeout")), timeout); })]);
  } finally { if (timer) clearTimeout(timer); }
}
function secretMatches(candidate: unknown, expected: string): boolean {
  if (typeof candidate !== "string") return false;
  const actual = Buffer.from(candidate, "utf8"), secret = Buffer.from(expected, "utf8");
  return actual.length === secret.length && timingSafeEqual(actual, secret);
}
function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return "[" + value.map(canonicalJson).join(",") + "]";
  if (record(value)) return "{" + Object.keys(value).sort().map((key) => JSON.stringify(key) + ":" + canonicalJson(value[key])).join(",") + "}";
  return JSON.stringify(value);
}
async function terminateChildBounded(child: ChildProcessWithoutNullStreams): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null) return;
  child.stdin.destroy();
  child.kill();
  await Promise.race([
    new Promise<void>((resolve) => child.once("exit", () => resolve())),
    new Promise<void>((resolve) => setTimeout(resolve, 5000)),
  ]);
  if (child.exitCode === null && child.signalCode === null) {
    child.kill("SIGKILL");
    await Promise.race([
      new Promise<void>((resolve) => child.once("exit", () => resolve())),
      new Promise<void>((resolve) => setTimeout(resolve, 5000)),
    ]);
  }
}
type SocketLike = { addEventListener(name: string, fn: (event: { data?: unknown }) => void): void; close(): void; send(value: string): void };
function applicationRequest(port: number, token: string | null, type: string, extra: RecordValue = {}): Promise<unknown> {
  const WebSocketCtor = (globalThis as unknown as { WebSocket?: new (url: string) => SocketLike }).WebSocket;
  if (!WebSocketCtor) return Promise.reject(new Error("WebSocket unavailable"));
  return new Promise((resolve, reject) => {
    const socket = new WebSocketCtor("ws://127.0.0.1:" + port);
    const timer = setTimeout(() => { socket.close(); reject(new Error("managed request timeout")); }, 5000);
    socket.addEventListener("open", () => socket.send(JSON.stringify({ type, ...(token ? { token } : {}), ...extra })));
    socket.addEventListener("message", (event) => {
      clearTimeout(timer); socket.close();
      try { resolve(JSON.parse(String(event.data))); } catch { reject(new Error("managed response rejected")); }
    });
    socket.addEventListener("error", () => { clearTimeout(timer); reject(new Error("managed request failed")); });
  });
}
function spawnPython(executable: string, pythonRoot: string, entry: string, workspace: string, temp: string, pythonBase: string, args: string[]): ChildProcessWithoutNullStreams {
  const systemRoot = process.env.SYSTEMROOT || "";
  const child = spawn(executable, ["-I", "-B", "-S", entry, ...args], {
    cwd: workspace,
    env: { SYSTEMROOT: systemRoot, WINDIR: process.env.WINDIR || systemRoot,
      PATH: [pythonBase, pythonRoot, path.dirname(executable), systemRoot ? path.join(systemRoot, "System32") : ""].filter(Boolean).join(path.delimiter),
      TMP: temp, TEMP: temp, PYTHONHOME: pythonBase },
    stdio: ["pipe", "pipe", "pipe"], windowsHide: true,
  });
  child.stderr.resume();
  return child;
}
function waitPythonReady(child: ChildProcessWithoutNullStreams): Promise<RecordValue> {
  return new Promise((resolve, reject) => {
    let output = "";
    const timeout = setTimeout(() => reject(new Error("managed Python readiness timeout")), 900000);
    child.stdout.setEncoding("utf8");
    const onData = (part: string) => {
      output += part;
      if (Buffer.byteLength(output) > 16384) { clearTimeout(timeout); reject(new Error("managed Python readiness frame too large")); return; }
      const at = output.indexOf("\n"); if (at < 0) return;
      clearTimeout(timeout); child.stdout.removeListener("data", onData);
      try { const value = JSON.parse(output.slice(0, at)); if (!record(value)) throw new Error(); resolve(value); }
      catch { reject(new Error("managed Python readiness frame rejected")); }
    };
    child.stdout.on("data", onData);
    child.once("error", () => { clearTimeout(timeout); reject(new Error("managed Python child error")); });
    child.once("exit", () => { clearTimeout(timeout); reject(new Error("managed Python exited before readiness")); });
  });
}
async function stopPython(child: ChildProcessWithoutNullStreams, control: RecordValue, verifiedReadonly: RecordValue): Promise<RecordValue> {
  let output = "";
  const onData = (part: string) => { output += part; };
  child.stdout.setEncoding("utf8");
  child.stdout.on("data", onData);
  child.stdin.write(canonicalJson(control) + "\n"); child.stdin.end();
  let timedOut = false;
  const exitCode = await new Promise<number | null>((resolve) => {
    const timer = setTimeout(() => { timedOut = true; child.kill(); resolve(null); }, 30000);
    child.once("exit", (code) => { clearTimeout(timer); resolve(code); });
  });
  if (timedOut) await terminateChildBounded(child);
  child.stdout.removeListener("data", onData);
  const result = output.split("\n").map((line) => { try { return JSON.parse(line); } catch { return null; } })
    .map((item) => record(item) && item.type === "managed.host-readonly.stopped" ? item : null)
    .find((item) => item !== null);
  if (exitCode !== 0 || !managedReadonlyStopMatches(result, verifiedReadonly)) throw new Error("managed Python clean shutdown identity mismatch");
  return result;
}

function readBootstrapSync(): Buffer {
  const bytes: number[] = [];
  const byte = Buffer.alloc(1);
  while (bytes.length <= 16384) {
    if (readSync(0, byte, 0, 1, null) !== 1) throw new Error("managed bootstrap missing");
    bytes.push(byte[0]);
    if (byte[0] === 10) return Buffer.from(bytes);
  }
  throw new Error("managed bootstrap exceeds bound");
}
function assertOwnedProfileRoot(namespaceRoot: string, sandboxRoot: string, profileRoot: string): void {
  const installationId = path.basename(namespaceRoot);
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(installationId)
      || path.resolve(sandboxRoot) !== sandboxRoot || path.resolve(namespaceRoot) !== namespaceRoot
      || namespaceRoot !== path.join(sandboxRoot, ".managed-runtime", installationId)
      || path.resolve(profileRoot) !== profileRoot) throw new Error("managed namespace path rejected");
  let current = path.parse(namespaceRoot).root;
  for (const component of namespaceRoot.slice(current.length).split(path.sep)) {
    current = path.join(current, component);
    const info = lstatSync(current) as ReturnType<typeof lstatSync> & { attributes?: number };
    if (!info.isDirectory() || info.isSymbolicLink() || (process.platform === "win32" && ((info.attributes ?? 0) & 0x400))
        || realpathSync(current) !== current) throw new Error("managed namespace alias rejected");
  }
}

async function start(): Promise<void> {
  let child: ChildProcessWithoutNullStreams | undefined;
  let controlInput: { stop(): void } | undefined;
  let stage: ManagedHostStartupStage = "bootstrap";
  try {
    const appRoot = path.resolve(process.argv[1] || "");
    parseManagedHostArguments(process.argv.slice(1), appRoot);
    const boot = parseManagedHostBootstrap(readBootstrapSync());
    const bindingBytes = readFileSync(path.join(boot.namespace_root, "state", "production-binding.json"));
    const binding = JSON.parse(bindingBytes.toString("utf8")) as RecordValue;
    const bindingBody = { ...binding }; delete bindingBody.bindingDigest;
    const source = binding.source as RecordValue;
    const sourceRoots = source.roots as RecordValue;
    const expectedProfile = path.join(boot.namespace_root, "host", "profile");
    if (bindingBytes.toString("utf8") !== canonicalJson(binding) + "\n"
        || createHash("sha256").update(canonicalJson(bindingBody)).digest("hex") !== boot.binding_digest
        || binding.bindingDigest !== boot.binding_digest || binding.installationId !== path.basename(boot.namespace_root)
        || binding.namespace === null || !record(binding.namespace) || binding.namespace.root !== boot.namespace_root
        || binding.release === null || !record(binding.release) || binding.release.releaseId !== boot.release_id
        || binding.paths === null || !record(binding.paths) || binding.paths.hostProfile !== expectedProfile
        || !record(sourceRoots) || !record(sourceRoots.stable) || !record(sourceRoots.sandbox)
        || sourceRoots.stable.worktreeRoot !== boot.stable_root || sourceRoots.sandbox.worktreeRoot !== boot.sandbox_root) {
      throw new Error("managed binding bootstrap mismatch");
    }
    stage = "context";
    const earlyProfile = ownedElectronPaths(path.join(boot.namespace_root, "host", "profile"));
    assertOwnedProfileRoot(boot.namespace_root, boot.sandbox_root, path.dirname(earlyProfile.userData));
    const hostNonce = randomUUID();
    const production = withManagedPhysicalRuntimeReads(() => prepareManagedHostProfileIntentSync({
      stableRoot: boot.stable_root, sandboxRoot: boot.sandbox_root,
      installationId: path.basename(boot.namespace_root), operationId: source.operationId as string, releaseId: boot.release_id,
    }, { access: boot.access, hostNonce }));
    if (path.resolve(appRoot) !== path.resolve(production.binding.paths.code, "desktop")) throw new Error("sealed app path mismatch");
    if (production.binding.bindingDigest !== boot.binding_digest || production.binding.namespace.root !== boot.namespace_root
        || production.binding.release.releaseId !== boot.release_id
        || !record(binding.source)) throw new Error("production binding refused");
    const registration = { registration: production.runtimeRegistration };
    if (registration.registration.runtimeDigest !== boot.runtime_digest) throw new Error("runtime refused");
    const electron = registration.registration.runtimes.electron;
    const electronExe = path.join(electron.root, ...electron.entry.split("/"));
    if (path.resolve(app.getPath("exe")) !== path.resolve(electronExe)) throw new Error("unregistered Electron");
    const paths = ownedElectronPaths(production.binding.paths.hostProfile);
    app.setPath("userData", paths.userData);
    app.setPath("sessionData", paths.sessionData);
    app.setPath("temp", paths.temp);
    for (const item of Object.values(paths)) mkdirSync(item, { recursive: true });
    process.env.TMP = paths.temp; process.env.TEMP = paths.temp; process.env.TMPDIR = paths.temp;
    const py = registration.registration.runtimes.python;
    const executable = path.join(py.root, ...py.entry.split("/"));
    const entry = path.join(production.binding.paths.code, "agent-core", "managed_host_entry.py");
    stage = "readonly";
    const readonly = spawnPython(executable, py.root, entry, production.binding.paths.hostWorkspace, paths.temp,
      registration.registration.runtimes.pythonBase.root, ["--managed-host-readonly", "--host", "127.0.0.1", "--port", "0"]);
    child = readonly;
    readonly.stdin.write(canonicalJson({ binding_digest: boot.binding_digest, control_token: boot.control_token,
      namespace_root: boot.namespace_root, nonce: boot.nonce, host_nonce: hostNonce,
      profile_intent_digest: production.profileIntent.intentDigest,
      protocol: "my-agent-managed-host-readonly-v1", runtime_digest: boot.runtime_digest }) + "\n");
    const readonlyReady = await waitPythonReady(readonly);
    const readonlyIdentity = {
      nonce: boot.nonce, host_nonce: hostNonce, profile_intent_digest: production.profileIntent.intentDigest,
      installation_id: production.binding.installationId, data_store_id: production.binding.data.storeId,
      release_id: boot.release_id, binding_digest: boot.binding_digest, runtime_digest: boot.runtime_digest,
    };
    if (!managedReadonlyReadyMatches(readonlyReady, readonlyIdentity)) {
      throw new Error("managed read-only preflight identity mismatch");
    }
    const readonlyHealth = await applicationRequest(Number(readonlyReady.port), null, "managed.health");
    if (!managedReadonlyHealthMatches(readonlyHealth, readonlyReady)) {
      throw new Error("managed read-only health refused");
    }
    let ready = readonlyReady;
    if (boot.access === "fenced_application") {
      stage = "application";
      await stopPython(readonly, { protocol: "my-agent-managed-host-readonly-control-v1", type: "shutdown",
        nonce: boot.nonce, control_token: boot.control_token, operation_id: randomUUID() }, readonlyReady);
      const application = spawnPython(executable, py.root, entry, production.binding.paths.hostWorkspace, paths.temp,
        registration.registration.runtimes.pythonBase.root, ["--managed-application", "--host", "127.0.0.1", "--port", "0"]);
      child = application;
      application.stdin.write(canonicalJson({ binding_digest: boot.binding_digest, control_token: boot.control_token,
        namespace_root: boot.namespace_root, nonce: boot.nonce, operation_id: boot.operation_id,
        host_nonce: hostNonce, profile_intent_digest: production.profileIntent.intentDigest,
        protocol: "my-agent-managed-application-v1", runtime_digest: boot.runtime_digest }) + "\n");
      ready = await waitPythonReady(application);
      if (ready.ready !== true || ready.mode !== "managed_application" || ready.access !== "fenced_application"
          || ready.host !== "127.0.0.1" || ready.nonce !== boot.nonce || ready.binding_digest !== boot.binding_digest
          || ready.runtime_digest !== boot.runtime_digest || typeof ready.port !== "number" || ready.port < 1 || ready.port > 65535) {
        throw new Error("managed Python application identity mismatch");
      }
    }
    const appToken = boot.access === "fenced_application" ? boot.control_token : null;
    const health = boot.access === "fenced_application" ? await applicationRequest(Number(ready.port), appToken, "managed.health") : readonlyHealth;
    const sessions = await applicationRequest(Number(ready.port), appToken, "session.list");
    if (!record(health) || health.type !== "managed.health" || health.installation_id !== production.binding.installationId
        || health.release_id !== boot.release_id || health.binding_digest !== boot.binding_digest
        || health.runtime_digest !== boot.runtime_digest || !record(sessions) || sessions.type !== "session.list" || !Array.isArray(sessions.sessions)) {
      throw new Error("managed health/list verification failed");
    }
    let sessionReadObserved = false;
    stage = "renderer";
    await within(app.whenReady(), "Electron readiness");
    const renderer = path.join(production.binding.paths.code, "desktop", "dist", "index.html");
    const managedTitle = boot.access === "fenced_application" ? "my-agent · 本地工作空间" : "my-agent · 本地工作空间（只读）";
    const window = new BrowserWindow({ show: false, width: 1120, height: 800, minWidth: 640, minHeight: 560,
      autoHideMenuBar: true, title: managedTitle, webPreferences: {
      preload: path.join(__dirname, "managed-preload.js"), sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true,
    } });
    window.removeMenu();
    window.on("page-title-updated", (event) => { event.preventDefault(); window.setTitle(managedTitle); });
    const rendererUrl = pathToFileURL(renderer).href;
    const rendererRoot = path.dirname(renderer);
    window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    window.webContents.on("will-navigate", (event, url) => { if (url !== rendererUrl) event.preventDefault(); });
    window.webContents.on("will-redirect", (event) => event.preventDefault());
    window.webContents.on("will-attach-webview", (event) => event.preventDefault());
    window.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    window.webContents.session.webRequest.onBeforeRequest({ urls: ["<all_urls>"] }, (details, callback) => {
      callback({ cancel: !isManagedRendererRequestAllowed(details.url, rendererRoot, Number(ready.port)) });
    });
    const trustedSender = (event: Electron.IpcMainInvokeEvent) => {
      const frameUrl = event.senderFrame?.url || "";
      if (frameUrl !== rendererUrl) throw new Error("managed IPC sender rejected");
    };
    ipcMain.handle("managed:mode", (event) => { trustedSender(event); return boot.access; });
    ipcMain.handle("managed:health", async (event) => { trustedSender(event); return applicationRequest(Number(ready.port), appToken, "managed.health"); });
    ipcMain.handle("managed:session-list", async (event) => { trustedSender(event); return applicationRequest(Number(ready.port), appToken, "session.list"); });
    ipcMain.handle("managed:session-create", async (event, input: unknown) => {
      trustedSender(event);
      if (boot.access !== "fenced_application" || !record(input) || Object.keys(input).sort().join(",") !== "sessionId,title"
          || typeof input.sessionId !== "string" || typeof input.title !== "string") throw new Error("managed mutation refused");
      return applicationRequest(Number(ready.port), boot.control_token, "session.create", { operation_id: randomUUID(), session_id: input.sessionId, title: input.title });
    });
    ipcMain.handle("managed:session-read", async (event, sessionId: unknown) => {
      trustedSender(event); if (typeof sessionId !== "string") throw new Error("managed session id rejected");
      const result = await applicationRequest(Number(ready.port), appToken, "session.read", { session_id: sessionId });
      if (record(result) && result.type === "session.read" && record(result.session)) sessionReadObserved = true;
      return result;
    });
    ipcMain.handle("managed:document-append", async (event, input: unknown) => {
      trustedSender(event);
      if (boot.access !== "fenced_application" || !record(input) || Object.keys(input).sort().join(",") !== "sessionId,text"
          || typeof input.sessionId !== "string" || typeof input.text !== "string") throw new Error("managed mutation refused");
      return applicationRequest(Number(ready.port), appToken, "session.append", { operation_id: randomUUID(), session_id: input.sessionId, text: input.text });
    });
    await within(window.loadFile(renderer), "managed renderer load");
    window.show();
    emit({ type: "managed.electron.ready", ready: true, mode: ready.mode, access: ready.access, host: ready.host, port: ready.port,
      installation_id: ready.installation_id, data_store_id: ready.data_store_id, release_id: ready.release_id,
      binding_digest: ready.binding_digest, runtime_digest: ready.runtime_digest, generation: ready.generation,
      revision: ready.revision, lifecycle: ready.lifecycle, last_known_good: ready.last_known_good, nonce: boot.nonce });
    let controlStreamFailed = false;
    const failControlStream = (error: "managed_control_stream_closed" | "managed_control_stream_failed") => {
      if (controlStreamFailed) return;
      controlStreamFailed = true;
      emit({ ready: false, error });
      void (async () => { try { if (child) await terminateChildBounded(child); } finally { app.exit(4); } })();
    };
    const handleControlLine = async (line: string): Promise<void> => {
      try {
        const frame = JSON.parse(line) as RecordValue;
        if (!record(frame) || canonicalJson(frame) !== line) return;
        const baseKeys = ["control_token", "nonce", "operation_id", "protocol", "type"];
        const reopenKeys = ["control_token", "nonce", "operation_id", "protocol", "shutdown_digest", "type"];
        if (frame.protocol !== "my-agent-managed-electron-control-v1"
            || !["shutdown", "accept_reopen"].includes(String(frame.type))
            || frame.nonce !== boot.nonce || !secretMatches(frame.control_token, boot.control_token)
            || typeof frame.operation_id !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(frame.operation_id)
            || Object.keys(frame).sort().join(",") !== (frame.type === "shutdown" ? baseKeys : reopenKeys).join(",")) return;
        if (frame.type === "accept_reopen") {
          if (boot.access !== "fenced_application") return;
          if (!sessionReadObserved || typeof frame.shutdown_digest !== "string" || !/^[a-f0-9]{64}$/.test(frame.shutdown_digest)) return;
          const control = { protocol: "my-agent-managed-application-control-v1", type: "accept_reopen",
            nonce: boot.nonce, control_token: boot.control_token, operation_id: randomUUID(),
            shutdown_digest: frame.shutdown_digest, host_nonce: hostNonce };
          await new Promise<void>((resolve) => {
            let tail = "";
            const acceptanceTimer = setTimeout(() => {
              child!.stdout.removeListener("data", onAccepted);
              emit({ ready: false, error: "managed_acceptance_unconfirmed" });
              resolve();
            }, 300000);
            const onAccepted = (part: string) => {
              tail += part;
              const accepted = tail.split("\n").map((row) => { try { return JSON.parse(row); } catch { return null; } })
                .find((item) => record(item) && item.type === "managed.application.accepted");
              if (!record(accepted)) return;
              clearTimeout(acceptanceTimer);
              child!.stdout.removeListener("data", onAccepted);
              emit({ type: "managed.electron.accepted", last_known_good: accepted.last_known_good ?? accepted.lifecycle ?? accepted });
              resolve();
            };
            child!.stdout.setEncoding("utf8");
            child!.stdout.on("data", onAccepted);
            child!.stdin.write(canonicalJson(control) + "\n");
          });
          return;
        }
        controlInput?.stop();
        const shutdown = { ...frame, protocol: boot.access === "fenced_application"
          ? "my-agent-managed-application-control-v1" : "my-agent-managed-host-readonly-control-v1" };
        await new Promise<void>((resolve) => {
          let tail = "";
          const onShutdown = (part: string) => { tail += part; };
          child!.stdout.setEncoding("utf8");
          child!.stdout.on("data", onShutdown);
          child!.stdin.write(canonicalJson(shutdown) + "\n");
          child!.stdin.end();
          child!.once("exit", (code) => {
            child!.stdout.removeListener("data", onShutdown);
            const stoppedType = boot.access === "fenced_application" ? "managed.application.stopped" : "managed.host-readonly.stopped";
            const stopped = tail.split("\n").map((row) => { try { return JSON.parse(row); } catch { return null; } })
              .find((item) => record(item) && item.type === stoppedType && item.clean === true);
            if (code === 0 && record(stopped)) {
              emit({ type: "managed.electron.stopped", clean: true, shutdown_digest: stopped.shutdown_digest });
              app.quit();
            } else emit({ ready: false, error: "managed_shutdown_unconfirmed" });
            resolve();
          });
        });
      } catch { /* malformed controls never produce clean shutdown claims */ }
    };
    controlInput = startManagedControlInput(0, {
      onLine: handleControlLine,
      onEnd: () => failControlStream("managed_control_stream_closed"),
      onFailure: () => failControlStream("managed_control_stream_failed"),
    });
  } catch {
    try { if (child) await terminateChildBounded(child); } catch { /* preserve failure state */ }
    emit(managedHostStartupFailure(stage));
    app.exit(4);
  }
}

if (process.argv.includes("--managed-host-v1")) void start();
