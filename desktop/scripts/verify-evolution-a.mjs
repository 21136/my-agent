import { createHash, randomBytes, randomUUID } from "node:crypto";
import { constants, copyFileSync, existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, realpathSync, renameSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const MAX_SOURCE_FILES = 40_000;
const MAX_ATTESTED_PYTHON_FILES = 50_000;
const MAX_ATTESTED_PYTHON_BYTES = 768 * 1024 * 1024;
const REQUIRED_PYTHON_DISTRIBUTIONS = ["httpx", "websockets", "rich", "prompt_toolkit", "python-docx", "pypdf", "pymupdf"];
const PYTHON_IMPORT_NAMES = { httpx: "httpx", websockets: "websockets", rich: "rich", prompt_toolkit: "prompt_toolkit", "python-docx": "docx", pypdf: "pypdf", pymupdf: "fitz" };
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
let defaultControlReleaseQueue = Promise.resolve();

function deny(message) { const error = new Error(`permission_denied: ${message}`); error.code = "permission_denied"; throw error; }
function assertH1LaunchProfileEnabled(profile, options = {}) {
  if (profile?.chat_fixture_profile === "ordinary-chat-h1-v1") {
    if (process.env.REAL_ELECTRON_CHAT_H1_OPT_IN !== "1") deny("set REAL_ELECTRON_CHAT_H1_OPT_IN=1 to launch ordinary-chat H1");
    if (process.env.REAL_ELECTRON_CHAT_H1_GUI_OPT_IN !== "1") deny("set REAL_ELECTRON_CHAT_H1_GUI_OPT_IN=1 to launch ordinary-chat H1");
    if (options.capturePythonEnvironment !== undefined) deny("custom Python environment attestation is forbidden for ordinary-chat H1 native launch");
  }
  if (profile?.chat_fixture_profile === "workbench-crash-reopen-recovery-v1") {
    if (process.env.REAL_ELECTRON_WORKBENCH_RECOVERY_OPT_IN !== "1") deny("set REAL_ELECTRON_WORKBENCH_RECOVERY_OPT_IN=1 to launch workbench crash/reopen Electron H1");
    if (process.env.REAL_ELECTRON_WORKBENCH_RECOVERY_GUI_OPT_IN !== "1") deny("set REAL_ELECTRON_WORKBENCH_RECOVERY_GUI_OPT_IN=1 to launch workbench crash/reopen Electron GUI");
    if (options.capturePythonEnvironment !== undefined) deny("custom Python environment attestation is forbidden for workbench recovery H1 native launch");
  }
}
function sha256(bytes) { return createHash("sha256").update(bytes).digest("hex"); }
function within(parent, child) { const rel = path.relative(parent, child); return rel !== "" && rel !== ".." && !rel.startsWith(`..${path.sep}`) && !path.isAbsolute(rel); }
function samePath(a, b) { const left = path.resolve(a), right = path.resolve(b); return process.platform === "win32" ? left.toLowerCase() === right.toLowerCase() : left === right; }
function regularFile(value, label) {
  if (!path.isAbsolute(value)) deny(`${label} must be absolute`);
  const absolute = path.resolve(value); const info = lstatSync(absolute);
  if (!info.isFile() || info.isSymbolicLink() || !samePath(realpathSync.native(absolute), absolute)) deny(`${label} must be a regular non-link file`);
  return absolute;
}
function realDirectory(value, label) {
  if (!path.isAbsolute(value)) deny(`${label} must be absolute`);
  const absolute = path.resolve(value); const info = lstatSync(absolute);
  if (!info.isDirectory() || info.isSymbolicLink() || !samePath(realpathSync.native(absolute), absolute)) deny(`${label} must be a real non-link directory`);
  return absolute;
}
function safeRelative(rel) { return typeof rel === "string" && rel.length > 0 && !path.isAbsolute(rel) && !rel.includes("\\") && !rel.split("/").some(part => !part || part === "." || part === ".."); }
function canonical(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
}
function writeExclusive(file, bytes) { mkdirSync(path.dirname(file), { recursive: true }); writeFileSync(file, bytes, { flag: "wx" }); }
function h1UserDirectoryMap(profileRoot) {
  return {
    USERPROFILE: path.join(profileRoot, "userprofile"), APPDATA: path.join(profileRoot, "appdata", "Roaming"), LOCALAPPDATA: path.join(profileRoot, "appdata", "Local"),
    HOME: path.join(profileRoot, "home"), XDG_CONFIG_HOME: path.join(profileRoot, "xdg-config"), XDG_DATA_HOME: path.join(profileRoot, "xdg-data"), XDG_CACHE_HOME: path.join(profileRoot, "xdg-cache"),
    TEMP: path.join(profileRoot, "tmp"), TMP: path.join(profileRoot, "tmp"),
    MY_AGENT_ELECTRON_USER_DATA: path.join(profileRoot, "electron-userData"), MY_AGENT_ELECTRON_SESSION_DATA: path.join(profileRoot, "electron-sessionData"),
  };
}
function prepareH1UserDirectories(profileRoot) { for (const directory of new Set(Object.values(h1UserDirectoryMap(profileRoot)))) mkdirSync(directory, { recursive: true }); }
export function buildH1RuntimeEnvironment(profileRoot, appRoot, sourceEnvironment = process.env) {
  const env = {}; for (const key of ["SYSTEMROOT", "WINDIR", "PATH"]) if (sourceEnvironment[key]) env[key] = sourceEnvironment[key];
  Object.assign(env, h1UserDirectoryMap(profileRoot), { MY_AGENT_ROOT: appRoot, MY_AGENT_H1_PROFILE_ROOT: profileRoot, MY_AGENT_H1_RUN_ID: path.basename(path.dirname(path.dirname(appRoot))), PYTHONDONTWRITEBYTECODE: "1", PYTHONNOUSERSITE: "1", PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" }); return env;
}
function verifyAgentPathsBeforeH1Start(verified) {
  const input = { sandboxRoot: verified.sandbox_root, runRoot: verified.run_root, appRoot: verified.app_root, profileRoot: verified.profile_root, pythonExecutable: verified.profile.trusted_python_executable };
  const actual = verifyH1AgentPaths(input);
  if (actual && typeof actual.then === "function") deny("async AgentPaths preflight is not supported before H1 spawn");
  const expected = { agent_root: verified.app_root, data: path.join(verified.app_root, "data"), workspace: path.join(verified.app_root, "workspace"), evolve: path.join(verified.app_root, "evolve") };
  if (!actual || Object.keys(actual).length !== 4 || Object.keys(expected).some(key => typeof actual[key] !== "string" || !samePath(actual[key], expected[key]))) deny("H1 AgentPaths preflight did not bind every root to this sealed app");
  return actual;
}
export function assertH1SpawnEnvironment(env, appRoot, profileRoot) {
  if (env?.MY_AGENT_ROOT !== appRoot) deny("H1 Electron spawn environment must set MY_AGENT_ROOT to the sealed app");
  if (env?.MY_AGENT_H1_PROFILE_ROOT !== profileRoot || env?.MY_AGENT_H1_RUN_ID !== path.basename(path.dirname(path.dirname(appRoot)))) deny("H1 Electron spawn environment is not bound to the marked run identity");
  const expected = h1UserDirectoryMap(profileRoot);
  for (const [key, value] of Object.entries(expected)) if (env[key] !== value || !samePath(env[key], value)) deny("H1 Electron spawn environment is not isolated to this run profile");
  if (env.PYTHONDONTWRITEBYTECODE !== "1" || env.PYTHONNOUSERSITE !== "1") deny("H1 Python user-site/bytecode isolation is missing from the Electron environment");
  return true;
}
export function buildH1ElectronArgs(desktopAppPath, cdpOptions) {
  if (typeof desktopAppPath !== "string" || !path.isAbsolute(desktopAppPath)) deny("sealed H1 Electron app path must be absolute");
  if (cdpOptions === undefined) return [desktopAppPath];
  if (!cdpOptions || typeof cdpOptions !== "object" || Array.isArray(cdpOptions) || Object.keys(cdpOptions).length !== 1 || cdpOptions.enabled !== true) deny("H1 CDP options accept only { enabled: true }");
  return ["--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0", desktopAppPath];
}

export function buildH1NativeLaunchPlan(verified, { mode, cdp, sourceEnvironment = process.env } = {}) {
  if (!verified || typeof verified !== "object" || typeof verified.app_root !== "string" || typeof verified.profile_root !== "string" || typeof verified.electron?.executable !== "string") deny("verified H1 paths and Electron identity are required for a launch plan");
  if (mode !== "exit" && mode !== "controlled") deny("H1 native launch mode is invalid");
  if (mode === "exit" && cdp !== undefined) deny("uncontrolled H1 exit launch cannot enable CDP");
  const env = buildH1RuntimeEnvironment(verified.profile_root, verified.app_root, sourceEnvironment);
  assertH1SpawnEnvironment(env, verified.app_root, verified.profile_root);
  const appEntry = path.join(verified.app_root, "desktop");
  const args = mode === "controlled" ? buildH1ElectronArgs(appEntry, cdp) : [appEntry];
  return { executable: verified.electron.executable, args, options: { cwd: verified.app_root, env, stdio: mode === "controlled" ? ["pipe", "pipe", "pipe"] : ["ignore", "pipe", "pipe"], windowsHide: false, shell: false } };
}

export function awaitH1ChildExit(child) {
  if (!child || typeof child.once !== "function") deny("H1 child exit observer requires an event-capable child handle");
  return new Promise(resolve => {
    let startError = null;
    child.once("error", error => { startError = error; });
    child.once("close", (code, signal) => resolve({ code, signal, ...(startError ? { start_error: String(startError.message ?? startError).slice(0, 512) } : {}) }));
  });
}

export async function completeH1ExitLifecycle({ child, stdoutPath, stderrPath, exitPath, runRoot, pythonExecutable, pythonPackages, beforeEnvironment, captureEnvironment }) {
  if (!child?.stdout || !child?.stderr || typeof child.once !== "function" || typeof captureEnvironment !== "function") deny("H1 exit lifecycle inputs are incomplete");
  const stdout = [], stderr = []; let stdoutBytes = 0, stderrBytes = 0;
  const collect = (target, stream, which) => stream.on("data", chunk => {
    const bytes = Buffer.from(chunk); if (which === "stdout") stdoutBytes += bytes.length; else stderrBytes += bytes.length;
    if (stdoutBytes + stderrBytes > 5 * 1024 * 1024) { try { child.kill?.(); } catch { /* retain bounded log failure */ } return; }
    target.push(bytes);
  });
  collect(stdout, child.stdout, "stdout"); collect(stderr, child.stderr, "stderr");
  const exit = await awaitH1ChildExit(child);
  writeFileSync(stdoutPath, Buffer.concat(stdout), { flag: "wx" }); writeFileSync(stderrPath, Buffer.concat(stderr), { flag: "wx" });
  const after = captureEnvironment(pythonExecutable, pythonPackages);
  assertPythonEnvironmentUnchanged(beforeEnvironment, after);
  const record = { code: exit.code, signal: exit.signal, ...(exit.start_error ? { start_error: exit.start_error } : {}), stdout: path.relative(runRoot, stdoutPath).replace(/\\/g, "/"), stderr: path.relative(runRoot, stderrPath).replace(/\\/g, "/"), python_environment_digest: after.digest };
  writeFileSync(exitPath, `${JSON.stringify(record, null, 2)}\n`, { flag: "wx" });
  return record;
}
export function createH1LifecycleProtocol(profile, token) {
  const ordinaryChat = profile === "ordinary-chat-h1-v1" || profile === "workbench-crash-reopen-recovery-v1";
  if (!ordinaryChat && profile !== "t4-recovery-node-v1") deny("H1 lifecycle profile is not an approved synthetic profile");
  if (typeof token !== "string" || !/^[0-9a-f]{64}$/.test(token)) deny("H1 lifecycle shutdown token is invalid");
  const controlType = ordinaryChat ? "ordinary-chat-h1.control" : "t9.control";
  return {
    bootstrapFrame: `${JSON.stringify({ type: `${controlType}.bootstrap`, token })}\n`,
    shutdownFrame: `${JSON.stringify({ type: `${controlType}.shutdown`, token })}\n`,
    parseReadyLine(line) {
      try {
        const message = JSON.parse(line);
        if (message?.type !== (ordinaryChat ? "ordinary-chat-h1-host-ready" : "evolution-test-host-ready") || message.host !== "127.0.0.1" || !Number.isInteger(message.port) || message.port < 1 || message.port > 65_535) return null;
        if (ordinaryChat && (message.renderer_host !== "127.0.0.1" || !Number.isInteger(message.renderer_port) || message.renderer_port < 1 || message.renderer_port > 65_535)) return null;
        return message;
      } catch { return null; }
    },
  };
}
export function extractH1CdpEndpoint(output) {
  if (typeof output !== "string") return null;
  const match = /DevTools listening on (ws:\/\/[^\s]+)/u.exec(output);
  return match ? validateH1CdpEndpoint(match[1]) : null;
}
export function validateH1CdpEndpoint(value) {
  if (typeof value !== "string") deny("H1 CDP endpoint must be a string");
  let parsed; try { parsed = new URL(value); } catch { deny("H1 CDP endpoint is invalid"); }
  if (parsed.protocol !== "ws:" || parsed.hostname !== "127.0.0.1" || !Number.isInteger(Number(parsed.port)) || Number(parsed.port) < 1 || Number(parsed.port) > 65_535 || parsed.username || parsed.password || parsed.search || parsed.hash || !/^\/devtools\/browser\/[A-Za-z0-9_-]+$/.test(parsed.pathname)) deny("H1 CDP endpoint must be a dynamic 127.0.0.1 browser endpoint");
  return parsed.href;
}

export async function closeH1BrowserViaCdp(value, { WebSocketClass = globalThis.WebSocket, timeoutMs = 5_000 } = {}) {
  const endpoint = validateH1CdpEndpoint(value);
  if (typeof WebSocketClass !== "function") deny("H1 dynamic CDP WebSocket is unavailable");
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 30_000) deny("H1 CDP close timeout is invalid");
  let socket;
  try { socket = new WebSocketClass(endpoint); } catch { deny("H1 CDP Browser.close connection could not be created"); }
  return await new Promise((resolve, reject) => {
    let settled = false, commandSent = false, sending = false, pendingClose = false, pendingError = false, pendingMessage = null, timer;
    const cleanup = () => {
      clearTimeout(timer);
      try { socket.removeEventListener("open", onOpen); socket.removeEventListener("message", onMessage); socket.removeEventListener("error", onError); socket.removeEventListener("close", onClose); } catch { /* closing socket */ }
      try { socket.close(); } catch { /* socket may already be closed */ }
    };
    const finish = (error, result) => { if (settled) return; settled = true; cleanup(); if (error) reject(error); else resolve(result); };
    const transportClosed = () => {
      if (sending) { pendingClose = true; return; }
      if (commandSent) finish(null, { command_sent: true, acknowledged: false });
      else finish(new Error("H1 CDP connection closed before Browser.close was sent"));
    };
    const processMessage = event => {
      let message; try { message = JSON.parse(typeof event.data === "string" ? event.data : String(event.data)); } catch { return; }
      if (message?.id !== 1) return;
      if (message.error) finish(new Error("H1 CDP Browser.close command was rejected"));
      else finish(null, { command_sent: true, acknowledged: true });
    };
    const onOpen = () => {
      if (commandSent || sending || settled) return;
      sending = true;
      try { socket.send(JSON.stringify({ id: 1, method: "Browser.close" })); commandSent = true; }
      catch { sending = false; finish(new Error("H1 CDP Browser.close command could not be sent")); return; }
      sending = false;
      if (pendingMessage) { const message = pendingMessage; pendingMessage = null; processMessage(message); }
      if (!settled && (pendingClose || pendingError)) transportClosed();
    };
    const onMessage = event => { if (sending) { pendingMessage = event; return; } processMessage(event); };
    const onError = () => {
      if (sending) { pendingError = true; return; }
      if (commandSent) finish(null, { command_sent: true, acknowledged: false });
      else finish(new Error("H1 CDP Browser.close connection failed before send"));
    };
    const onClose = () => transportClosed();
    socket.addEventListener("open", onOpen); socket.addEventListener("message", onMessage); socket.addEventListener("error", onError); socket.addEventListener("close", onClose);
    timer = setTimeout(() => finish(new Error("H1 CDP Browser.close timed out")), timeoutMs);
  });
}
function addSealedSyntheticFile(root, relative, content, manifestFiles) {
  const target = path.join(root, ...relative.split("/")); const bytes = Buffer.from(content, "utf8"); writeExclusive(target, bytes);
  manifestFiles.push({ path: relative, byte_length: bytes.length, sha256: sha256(bytes) });
}

function validateRunRoot(sandboxRootValue, runRootValue) {
  const sandboxRoot = realDirectory(path.resolve(sandboxRootValue), "sandbox root");
  const artifactsRoot = realDirectory(path.join(sandboxRoot, "artifacts"), "sandbox artifacts root");
  const runRoot = realDirectory(path.resolve(runRootValue), "marked run root");
  if (!within(artifactsRoot, runRoot)) deny("run root must stay under the current sandbox artifacts directory");
  const marker = regularFile(path.join(runRoot, ".evolution-test-run"), "evolution test run marker");
  if (readFileSync(marker, "utf8").replace(/^\uFEFF/, "").trim() !== "evolution-a") deny("run marker is invalid");
  return { sandboxRoot, artifactsRoot, runRoot };
}

function listFiles(rootValue, options = {}) {
  const root = realDirectory(rootValue, "source tree"); const files = [];
  const visit = (directory, prefix = "") => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
      const absolute = path.join(directory, entry.name); const info = lstatSync(absolute);
      if (info.isSymbolicLink()) deny(`source tree contains a link: ${rel}`);
      if (info.isDirectory()) {
        if (options.skipDirectory?.(rel, entry.name)) continue;
        visit(absolute, rel);
      } else if (info.isFile()) {
        if (options.includeFile?.(rel, entry.name, info)) files.push({ absolute, relative: rel.replace(/\\/g, "/"), byte_length: info.size });
      } else deny(`source tree contains a non-regular entry: ${rel}`);
      if (files.length > MAX_SOURCE_FILES) deny("source tree exceeds the file-count limit");
    }
  };
  visit(root); return files.sort((a, b) => a.relative.localeCompare(b.relative, "en"));
}

function distributionFingerprint(electronExecutable) {
  const executable = regularFile(electronExecutable, "fixed Electron executable");
  const root = realDirectory(path.dirname(executable), "Electron distribution root");
  if (!within(root, executable)) deny("Electron executable is outside its recorded distribution root");
  const files = listFiles(root, { includeFile: () => true });
  if (!files.some(item => samePath(item.absolute, executable))) deny("Electron distribution omits its executable");
  let total = 0;
  const entries = files.map(item => {
    const bytes = readFileSync(item.absolute); total += bytes.length;
    return { path: item.relative, byte_length: bytes.length, sha256: sha256(bytes) };
  });
  return { executable, executable_sha256: sha256(readFileSync(executable)), distribution_root: root, distribution_digest: `sha256:${sha256(Buffer.from(canonical(entries), "utf8"))}`, file_count: entries.length, byte_length: total };
}

function requirementsFromFile(sandboxRoot) {
  const file = regularFile(path.join(sandboxRoot, "requirements.txt"), "runtime requirements file");
  return readFileSync(file, "utf8").split(/\r?\n/).map(line => line.replace(/\s+#.*$/, "").trim()).filter(line => line && !line.startsWith("#") && !line.startsWith("-") && !line.startsWith(".")).map(line => {
    const match = /^([A-Za-z0-9_.-]+)/.exec(line); return match?.[1] ?? "";
  }).filter((name, index, all) => name && all.indexOf(name.toLowerCase()) === index);
}

export function capturePythonEnvironment(pythonExecutable, requiredDistributions = REQUIRED_PYTHON_DISTRIBUTIONS, options = {}) {
  const python = regularFile(pythonExecutable, "trusted Python executable");
  const script = String.raw`import hashlib, importlib.metadata as metadata, importlib.util, json, os, pathlib, sys
sys.dont_write_bytecode=True
def beneath(child, parent):
    try:
        child=os.path.normcase(str(child)); parent=os.path.normcase(str(parent))
        return os.path.commonpath((child,parent))==parent
    except (ValueError,OSError):
        return False
names=json.loads(sys.stdin.read())
out=[]
total=0
for name in names:
    dist=metadata.distribution(name)
    root=pathlib.Path(dist.locate_file("")).resolve(strict=True)
    records=[]
    for item in dist.files or ():
        target=pathlib.Path(dist.locate_file(item))
        try:
            resolved=target.resolve(strict=True)
        except FileNotFoundError:
            raise RuntimeError("installed package file is missing: "+str(item))
        prefix=pathlib.Path(sys.prefix).resolve(strict=True)
        if not resolved.is_file() or target.is_symlink() or (root not in resolved.parents and prefix not in resolved.parents):
            raise RuntimeError("installed package file escaped its trusted Python environment: "+str(item))
        data=resolved.read_bytes()
        total+=len(data)
        if total>805306368 or len(records)>50000:
            raise RuntimeError("Python environment fingerprint exceeds its bound")
        records.append({"path":str(item).replace("\\","/"),"byte_length":len(data),"sha256":hashlib.sha256(data).hexdigest()})
    records.sort(key=lambda value:value["path"])
    spec=importlib.util.find_spec({"python-docx":"docx","pymupdf":"fitz"}.get(name,name))
    if spec is None or not spec.origin:
        raise RuntimeError("required package import origin is unavailable: "+name)
    origin=pathlib.Path(spec.origin).resolve(strict=True)
    if not any(beneath(origin, pathlib.Path(item).resolve()) for item in sys.path if item and pathlib.Path(item).exists()):
        raise RuntimeError("required package import origin is outside sys.path: "+name)
    out.append({"name":name,"version":dist.version,"distribution_root":str(root),"import_origin":str(origin),"files":records})
print(json.dumps({"executable":str(pathlib.Path(sys.executable).resolve()),"python_home":sys.prefix,"base_prefix":sys.base_prefix,"sys_path":sys.path,"packages":out},sort_keys=True,separators=(",",":")))`;
  const env = {};
  for (const key of ["SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "USERPROFILE", "LOCALAPPDATA", "APPDATA"]) if (process.env[key]) env[key] = process.env[key];
  const completed = spawnSync(python, ["-I", "-c", script], { cwd: options.cwd ?? path.dirname(python), env, input: JSON.stringify(requiredDistributions), encoding: "utf8", windowsHide: true, timeout: 60_000, maxBuffer: 32 * 1024 * 1024, shell: false });
  if (completed.error || completed.status !== 0 || typeof completed.stdout !== "string" || !completed.stdout.trim()) deny("trusted Python environment attestation failed");
  let parsed; try { parsed = JSON.parse(completed.stdout); } catch { deny("trusted Python environment attestation returned invalid JSON"); }
  if (!parsed || parsed.executable == null || !Array.isArray(parsed.sys_path) || !Array.isArray(parsed.packages) || parsed.packages.length !== requiredDistributions.length) deny("trusted Python environment attestation is incomplete");
  const canonicalValue = { executable: parsed.executable, python_home: parsed.python_home, base_prefix: parsed.base_prefix, sys_path: parsed.sys_path, packages: parsed.packages };
  return { ...canonicalValue, digest: `sha256:${sha256(Buffer.from(canonical(canonicalValue), "utf8"))}` };
}

export function assertPythonEnvironmentUnchanged(before, after) {
  if (!before || !after || typeof before.digest !== "string" || before.digest !== after.digest) deny("trusted Python environment changed during the drill");
  return true;
}

export function createDrillSummary(mode) {
  if (mode !== "scripted" && mode !== "agent") throw new TypeError("mode must be scripted or agent");
  return {
    scope: "evolution-slice-a-prototype",
    mode: mode === "scripted" ? "scripted_recovery" : "agent_recovery",
    installed_stable_upgraded: false,
    main_conversation_preserved: false,
    control_release_unchanged: false,
    candidate_fail_observed: false,
    candidate_repaired_ready: false,
    scripted_recovery_verified: false,
    agent_recovery_verified: false,
    isolation: "local_managed",
    protected_guest: "not_run",
    descendant_cleanup: "declared_processes_only",
    independent_review: "incomplete",
    security_audit: "not_run",
    source_delivery: "uncommitted",
    runtime_adoption: "not_requested",
    evidence: [],
    open_checks: [],
  };
}

export function validateDrillAuthorization(input) {
  const fields = ["mode", "localConfirmed", "hostConfirmed", "modelConfirmed"];
  if (!input || typeof input !== "object" || Array.isArray(input) || Object.keys(input).length !== fields.length || fields.some(key => !Object.hasOwn(input, key))) deny("drill authorization fields are invalid");
  if (input.mode !== "scripted" && input.mode !== "agent") deny("drill mode is invalid");
  if (typeof input.localConfirmed !== "boolean" || typeof input.hostConfirmed !== "boolean" || typeof input.modelConfirmed !== "boolean") deny("drill confirmations must be trusted booleans");
  if (!input.localConfirmed || !input.hostConfirmed || (input.mode === "agent" && !input.modelConfirmed)) deny("required local, Host, or model authorization is missing");
}

function copySelectedTree(sourceValue, destinationValue, includeFile, skipDirectory, manifestFiles) {
  const source = realDirectory(sourceValue, "fixture source tree"); const destination = path.resolve(destinationValue); mkdirSync(destination, { recursive: true });
  const candidates = listFiles(source, { includeFile, skipDirectory });
  for (const item of candidates) {
    if (!safeRelative(item.relative)) deny(`fixture source path is unsafe: ${item.relative}`);
    const target = path.join(destination, ...item.relative.split("/"));
    mkdirSync(path.dirname(target), { recursive: true }); copyFileSync(item.absolute, target, constants.COPYFILE_EXCL);
    const bytes = readFileSync(target); manifestFiles.push({ path: path.relative(path.dirname(destination), target).replace(/\\/g, "/"), byte_length: bytes.length, sha256: sha256(bytes) });
  }
  return candidates.length;
}

function copySingleFile(sourceValue, destinationValue, manifestFiles, manifestPath) {
  const source = regularFile(sourceValue, "fixture source file"); const destination = path.resolve(destinationValue);
  mkdirSync(path.dirname(destination), { recursive: true }); copyFileSync(source, destination, constants.COPYFILE_EXCL);
  const bytes = readFileSync(destination); manifestFiles.push({ path: manifestPath, byte_length: bytes.length, sha256: sha256(bytes) });
}

async function defaultFreezeControlRelease(sandboxRoot, runRoot, pythonExecutable) {
  const require = createRequire(import.meta.url); const modulePath = path.join(sandboxRoot, "artifacts", "evolution-control-build", "control-release.js");
  regularFile(modulePath, "compiled control-release module"); const api = require(modulePath);
  if (typeof api.freezeControlRelease !== "function") deny("fixed control release builder is unavailable");
  let releaseTurn;
  const queued = defaultControlReleaseQueue;
  defaultControlReleaseQueue = new Promise(resolve => { releaseTurn = resolve; });
  await queued;
  const hadRunRoot = Object.hasOwn(process.env, "EVOLUTION_TEST_ROOT");
  const previousRunRoot = process.env.EVOLUTION_TEST_ROOT;
  const previousCwd = process.cwd();
  try {
    let pending;
    try {
      // freezeControlRelease synchronously captures both globals before its first await.
      process.env.EVOLUTION_TEST_ROOT = runRoot;
      process.chdir(sandboxRoot);
      pending = api.freezeControlRelease({ runRoot, buildRoot: path.join(sandboxRoot, "artifacts", "evolution-control-build"), workerRoot: path.join(sandboxRoot, "agent-core", "evolution"), outputRoot: path.join(runRoot, "control-releases"), nodeExecutable: process.execPath, pythonExecutable });
    } finally {
      try { process.chdir(previousCwd); }
      finally {
        if (hadRunRoot) process.env.EVOLUTION_TEST_ROOT = previousRunRoot;
        else delete process.env.EVOLUTION_TEST_ROOT;
      }
    }
    return await pending;
  } finally { releaseTurn(); }
}
export async function buildH1ElectronMainOutputs(input, options = {}) {
  if (!input || typeof input !== "object" || Array.isArray(input) || Object.keys(input).length !== 2 || typeof input.sandboxRoot !== "string" || typeof input.runRoot !== "string") deny("H1 Electron build input fields are invalid");
  const { sandboxRoot, runRoot } = validateRunRoot(input.sandboxRoot, input.runRoot);
  const desktopRoot = realDirectory(path.join(sandboxRoot, "desktop"), "sandbox desktop source root");
  const entryPoints = { main: regularFile(path.join(desktopRoot, "electron", "main.ts"), "current H1 Electron main source"), preload: regularFile(path.join(desktopRoot, "electron", "preload.ts"), "current H1 Electron preload source") };
  let esbuildEntry = null;
  let build = options.build;
  if (build !== undefined && typeof build !== "function") deny("injected H1 esbuild build function is invalid");
  if (!build) {
    esbuildEntry = regularFile(path.join(desktopRoot, "node_modules", "esbuild", "lib", "main.js"), "fixed sandbox esbuild runtime");
    const esbuild = createRequire(import.meta.url)(esbuildEntry);
    if (!esbuild || typeof esbuild.build !== "function") deny("fixed sandbox esbuild API is unavailable");
    build = esbuild.build.bind(esbuild);
  }
  const outputRoot = path.join(runRoot, "t9-electron-build-" + randomUUID());
  if (existsSync(outputRoot)) deny("T9 Electron build output already exists; refusing overwrite");
  mkdirSync(outputRoot, { recursive: false });
  try {
    const common = { outdir: outputRoot, bundle: true, platform: "node", format: "cjs", target: "node20", external: ["electron", "node:*"], sourcemap: false, logLevel: "silent", metafile: true };
    await build({ ...common, entryPoints: { main: entryPoints.main }, define: { "import.meta.url": "__h1_import_meta_url" }, banner: { js: "const __h1_import_meta_url = require('node:url').pathToFileURL(__filename).href;" } });
    // Sandboxed preload only allows require("electron"); keep Node URL shims/globals out of this bundle.
    await build({ ...common, entryPoints: { preload: entryPoints.preload } });
    const files = ["main.js", "preload.js"].map(name => {
      const absolute = regularFile(path.join(outputRoot, name), `T9 bundled Electron ${name}`); const bytes = readFileSync(absolute);
      return { path: name, byte_length: bytes.length, sha256: sha256Hex(bytes), absolute };
    });
    return { output_root: outputRoot, main_path: files[0].absolute, preload_path: files[1].absolute, files: files.map(({ absolute: _, ...item }) => item), esbuild_entry: esbuildEntry };
  } catch (error) {
    throw new Error(`T9 Electron main/preload bundle failed in ${path.relative(runRoot, outputRoot)}: ${String(error?.message ?? error).slice(0, 512)}`, { cause: error });
  }
}
export async function freezeHostFixture(input, options = {}) {
  const inputFields = ["sandboxRoot", "runRoot", "trustedPythonExecutable", "electronExecutable"];
  const recoveryFixture = input && typeof input === "object" && Object.hasOwn(input, "candidateFixtureProfile");
  const chatFixture = input && typeof input === "object" && Object.hasOwn(input, "chatFixtureProfile");
  if (recoveryFixture && chatFixture) deny("T4 recovery and ordinary chat fixtures are mutually exclusive");
  const expectedInputFields = recoveryFixture ? [...inputFields, "candidateFixtureProfile"] : chatFixture ? [...inputFields, "chatFixtureProfile"] : inputFields;
  if (!input || typeof input !== "object" || Array.isArray(input) || Object.keys(input).length !== expectedInputFields.length || expectedInputFields.some(key => !Object.hasOwn(input, key))) deny("H1 fixture input fields are invalid");
  if (recoveryFixture && input.candidateFixtureProfile !== "t4-recovery-node-v1") deny("H1 candidate fixture profile is unsupported");
  if (chatFixture && input.chatFixtureProfile !== "ordinary-chat-h1-v1" && input.chatFixtureProfile !== "workbench-crash-reopen-recovery-v1") deny("H1 sealed chat fixture profile is unsupported");
  const { sandboxRoot, runRoot } = validateRunRoot(input.sandboxRoot, input.runRoot);
  const pythonExecutable = regularFile(input.trustedPythonExecutable, "trusted fixture Python executable");
  const electron = distributionFingerprint(input.electronExecutable);
  const hostRoot = path.join(runRoot, "host"); if (existsSync(hostRoot)) deny("H1 host fixture already exists; refusing overwrite");
  const stageRoot = path.join(runRoot, `.host-stage-${randomUUID()}`); mkdirSync(stageRoot, { recursive: false });
  try {
    const freeze = options.factories?.freezeControlRelease ?? ((value) => defaultFreezeControlRelease(value.sandboxRoot, value.runRoot, value.pythonExecutable));
    const release = await freeze({ sandboxRoot, runRoot, pythonExecutable, outputRoot: path.join(runRoot, "control-releases") });
    if (!release || typeof release.root !== "string" || !path.isAbsolute(release.root) || !within(runRoot, path.resolve(release.root))) deny("fixed control release escaped the approved run root");
    regularFile(release.manifest_path, "fixed control release manifest");
    if (!samePath(path.dirname(release.manifest_path), release.root)) deny("fixed control release manifest path is inconsistent");

    const appRoot = path.join(stageRoot, "app"); const desktopRoot = path.join(appRoot, "desktop"); const profileRoot = path.join(stageRoot, "profile");
    mkdirSync(path.join(appRoot, "data", "sessions"), { recursive: true }); mkdirSync(path.join(appRoot, "workspace"), { recursive: true }); mkdirSync(path.join(appRoot, "evolve"), { recursive: true }); mkdirSync(profileRoot, { recursive: true }); prepareH1UserDirectories(profileRoot);
    const manifestFiles = [];
    addSealedSyntheticFile(appRoot, "evolve/_index.core.toml", "[core]\n", manifestFiles);
    addSealedSyntheticFile(appRoot, "evolve/_index.toml", "# synthetic H1 legacy root marker\n", manifestFiles);
    const electronBuildRoot = realDirectory(options.electronBuildRoot ?? path.join(sandboxRoot, "desktop", "dist-electron"), "sealed Electron build output root");
    if (options.electronBuildRoot && !within(runRoot, electronBuildRoot)) deny("T9 Electron build output must stay inside the marked run root");
    if (options.rendererBuild !== undefined && options.rendererBuild !== true) deny("H1 rendererBuild option must be true when enabled");
    const rendererBuild = options.rendererBuild === true ? await (options.factories?.buildRenderer ?? buildH1RendererOutputs)({ sandboxRoot, runRoot }) : null;
    let rendererSource = path.join(sandboxRoot, "desktop", "dist");
    if (rendererBuild) {
      if (!rendererBuild || typeof rendererBuild.output_root !== "string" || !path.isAbsolute(rendererBuild.output_root) || typeof rendererBuild.source_digest !== "string" || !/^sha256:[a-f0-9]{64}$/.test(rendererBuild.source_digest) || !Array.isArray(rendererBuild.files)) deny("run-local renderer build attestation is invalid");
      rendererSource = realDirectory(rendererBuild.output_root, "run-local renderer build output");
      if (!within(runRoot, rendererSource) || !path.basename(rendererSource).startsWith("t9-renderer-build-")) deny("renderer build output must stay inside the marked run root");
      if (rendererBuild.source_digest !== rendererSourceDigest(path.join(sandboxRoot, "desktop"))) deny("renderer build source digest does not match current sandbox sources");
      if (canonical(rendererBuild.files) !== canonical(rendererOutputFiles(rendererSource))) deny("renderer build output digest does not match its attestation");
    }
    const agentCoreSource = path.join(sandboxRoot, "agent-core");
    const catalogSource = path.join(sandboxRoot, "evolve", "tool-catalog", "INDEX.md");
    const electronBuildFiles = listFiles(electronBuildRoot, { includeFile: (rel, name) => /\.(?:js|cjs|mjs|json|node|wasm)$/i.test(name) && !/(?:^|\/)vite(?:\.shell-preview)?\.config\.(?:js|mjs|cjs)$/i.test(rel) });
    const rendererFiles = listFiles(rendererSource, { includeFile: (_rel, name) => !/\.(?:map|tsbuildinfo|d\.ts)$/i.test(name) });
    if (!electronBuildFiles.some(item => item.relative === "main.js") || !electronBuildFiles.some(item => item.relative === "preload.js")) deny("sealed Electron main/preload outputs are missing");
    if (!rendererFiles.some(item => item.relative === "index.html")) deny("sealed renderer index is missing");
    for (const [sourceRoot, destRoot, candidates, prefix] of [
      [electronBuildRoot, path.join(desktopRoot, "dist-electron"), electronBuildFiles, "desktop/dist-electron"],
      [rendererSource, path.join(desktopRoot, "dist"), rendererFiles, "desktop/dist"],
    ]) {
      for (const item of candidates) {
        if (!safeRelative(item.relative)) deny(`sealed fixture path is unsafe: ${item.relative}`);
        const target = path.join(destRoot, ...item.relative.split("/")); mkdirSync(path.dirname(target), { recursive: true });
        copyFileSync(item.absolute, target, constants.COPYFILE_EXCL); const bytes = readFileSync(target);
        manifestFiles.push({ path: `${prefix}/${item.relative}`, byte_length: bytes.length, sha256: sha256(bytes) });
      }
    }
    const sourcePackage = regularFile(path.join(sandboxRoot, "desktop", "package.json"), "desktop package metadata");
    let packageMetadata; try { packageMetadata = JSON.parse(readFileSync(sourcePackage, "utf8")); } catch { deny("desktop package metadata is invalid"); }
    if (typeof packageMetadata.name !== "string" || typeof packageMetadata.version !== "string") deny("desktop package identity is incomplete");
    const runtimePackage = { name: packageMetadata.name, version: packageMetadata.version, main: "dist-electron/main.js", ...(packageMetadata.type === "module" ? { type: "module" } : {}) };
    const packageBytes = Buffer.from(`${JSON.stringify(runtimePackage, null, 2)}\n`, "utf8"); writeExclusive(path.join(desktopRoot, "package.json"), packageBytes);
    manifestFiles.push({ path: "desktop/package.json", byte_length: packageBytes.length, sha256: sha256(packageBytes) });

    const excludedDirectories = new Set([".git", ".venv", "__pycache__", ".pytest_cache", "tests", "test", "data", "workspace", "profiles", "profile", "config", "configs", ".config", "credentials", "secrets", "node_modules", "dist", "build"]);
    const allowedCoreExtensions = new Set([".py", ".md", ".toml", ".json", ".txt", ".yaml", ".yml", ".jinja", ".jinja2", ".html"]);
    const coreFiles = listFiles(agentCoreSource, {
      skipDirectory: (_rel, name) => excludedDirectories.has(name.toLowerCase()),
      includeFile: (rel, name) => {
        if (name.toLowerCase().startsWith(".env") || /\.(?:key|pem|p12|pfx|sqlite|sqlite3|db|log|jsonl|pyc)$/i.test(name)) return false;
        if (!allowedCoreExtensions.has(path.extname(name).toLowerCase())) return false;
        const parts = rel.split("/").map(part => part.toLowerCase());
        return !parts.includes("data") && !parts.includes("tests") && !parts.includes("profiles");
      },
    });
    for (const item of coreFiles) {
      if (!safeRelative(item.relative)) deny(`trusted Python module path is unsafe: ${item.relative}`);
      const target = path.join(appRoot, "agent-core", ...item.relative.split("/")); mkdirSync(path.dirname(target), { recursive: true });
      copyFileSync(item.absolute, target, constants.COPYFILE_EXCL); const bytes = readFileSync(target);
      manifestFiles.push({ path: `agent-core/${item.relative}`, byte_length: bytes.length, sha256: sha256(bytes) });
    }
    copySingleFile(catalogSource, path.join(appRoot, "evolve", "tool-catalog", "INDEX.md"), manifestFiles, "evolve/tool-catalog/INDEX.md");
    manifestFiles.sort((a, b) => a.path.localeCompare(b.path, "en"));

    const attestor = options.factories?.capturePythonEnvironment ?? ((executable, packages) => capturePythonEnvironment(executable, packages, { cwd: sandboxRoot }));
    const requiredPackages = requirementsFromFile(sandboxRoot);
    const pythonEnvironment = await attestor(pythonExecutable, requiredPackages);
    if (!pythonEnvironment || typeof pythonEnvironment.digest !== "string" || !pythonEnvironment.digest.startsWith("sha256:")) deny("trusted Python environment attestation is invalid");
    const electronRuntime = { executable: electron.executable, executable_sha256: electron.executable_sha256, distribution_root: electron.distribution_root, distribution_digest: electron.distribution_digest, file_count: electron.file_count, byte_length: electron.byte_length };
    const pythonRuntime = { executable: pythonExecutable, executable_sha256: sha256(readFileSync(pythonExecutable)), environment: pythonEnvironment };
    const profile = { profile_root: path.join(runRoot, "host", "profile"), control_release_root: release.root, trusted_python_executable: pythonExecutable, ws_port: 0, renderer_port: 0, fixture_scope_id: randomUUID(), ...(recoveryFixture ? { candidate_fixture_profile: "t4-recovery-node-v1" } : chatFixture ? { chat_fixture_profile: input.chatFixtureProfile, chat_run_id: path.basename(runRoot) } : {}) };
    const profileBytes = Buffer.from(`${JSON.stringify(profile, null, 2)}\n`, "utf8"); writeFileSync(path.join(stageRoot, "evolution-test-profile.json"), profileBytes, { flag: "wx" });
    const sourceInputs = [];
    const desktopSource = path.join(sandboxRoot, "desktop");
    for (const relative of ["vite.config.ts", "index.html", "package.json"]) {
      const source = regularFile(path.join(desktopSource, relative), "approved desktop source input");
      sourceInputs.push({ path: "desktop/" + relative, byte_length: lstatSync(source).size, sha256: sha256(readFileSync(source)) });
    }
    for (const item of listFiles(path.join(desktopSource, "electron"), { includeFile: (_rel, name) => /\.tsx?$/i.test(name) && !/\.d\.ts$/i.test(name) })) sourceInputs.push({ path: "desktop/electron/" + item.relative, byte_length: item.byte_length, sha256: sha256(readFileSync(item.absolute)) });
    for (const item of listFiles(path.join(desktopSource, "src"), { includeFile: (_rel, name) => !/\.(?:map|tsbuildinfo|d\.ts)$/i.test(name) })) sourceInputs.push({ path: "desktop/src/" + item.relative, byte_length: item.byte_length, sha256: sha256(readFileSync(item.absolute)) });
    sourceInputs.sort((a, b) => a.path.localeCompare(b.path, "en"));
    const manifest = { format: 1, scope: "evolution-slice-a-h1-test-host", app_root: "app", renderer_root: "app/desktop/dist", profile_root: "profile", files: manifestFiles, mutable_candidate_sources: [], source_inputs: sourceInputs, profile_sha256: sha256(profileBytes), control_release_id: release.release_id, runtimes: { electron: electronRuntime, python: pythonRuntime }, python_environment_recheck: "required_at_host_start_and_shutdown", build_provenance: rendererBuild ? "run-local-vite-renderer-and-esbuild-main-preload; renderer-source-and-sealed-output-hashes-verified" : "run-local-esbuild-main-preload; legacy-desktop-dist-renderer; sealed-output-hashes-recorded; source-output-reproducibility-not-asserted" };
    const manifestBytes = Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`, "utf8"); writeFileSync(path.join(stageRoot, "manifest.json"), manifestBytes, { flag: "wx" });
    renameSync(stageRoot, hostRoot);
    return { app_root: path.join(hostRoot, "app"), profile_root: path.join(hostRoot, "profile"), renderer_root: path.join(hostRoot, "app", "desktop", "dist"), manifest_path: path.join(hostRoot, "manifest.json") };
  } catch (error) {
    const absoluteStage = path.resolve(stageRoot); if (within(runRoot, absoluteStage) && path.basename(absoluteStage).startsWith(".host-stage-")) rmSync(absoluteStage, { recursive: true, force: true });
    throw error;
  }
}

function sha256Hex(bytes) { return createHash("sha256").update(bytes).digest("hex"); }
export function verifyH1AgentPaths(input) {
  const fields = ["sandboxRoot", "runRoot", "appRoot", "profileRoot", "pythonExecutable"];
  if (!input || typeof input !== "object" || Array.isArray(input) || Object.keys(input).length !== fields.length || fields.some(key => typeof input[key] !== "string")) deny("H1 AgentPaths verification input fields are invalid");
  const { sandboxRoot, runRoot } = validateRunRoot(input.sandboxRoot, input.runRoot);
  const appRoot = realDirectory(input.appRoot, "sealed H1 app root"), profileRoot = realDirectory(input.profileRoot, "isolated H1 profile root");
  if (!samePath(appRoot, path.join(runRoot, "host", "app")) || !samePath(profileRoot, path.join(runRoot, "host", "profile")) || !within(runRoot, appRoot) || !within(runRoot, profileRoot)) deny("H1 AgentPaths roots are outside the current sealed run");
  regularFile(path.join(appRoot, "agent-core", "paths.py"), "sealed AgentPaths implementation");
  for (const name of ["data", "workspace", "evolve"]) { const directory = realDirectory(path.join(appRoot, name), `sealed AgentPaths ${name} root`); if (!within(appRoot, directory)) deny(`sealed AgentPaths ${name} root escaped the app`); }
  regularFile(path.join(appRoot, "evolve", "_index.core.toml"), "synthetic sealed core root marker");
  regularFile(path.join(appRoot, "evolve", "_index.toml"), "synthetic sealed legacy root marker");
  for (const directory of new Set(Object.values(h1UserDirectoryMap(profileRoot)))) { const real = realDirectory(directory, "isolated H1 user configuration directory"); if (!within(profileRoot, real)) deny("H1 user configuration directory escaped the run profile"); }
  listFiles(appRoot, { includeFile: () => true });
  const python = regularFile(input.pythonExecutable, "trusted H1 AgentPaths Python executable");
  const script = String.raw`import json, pathlib, sys
sealed=pathlib.Path(sys.argv[1]).resolve(strict=True)
expected_module=pathlib.Path(sys.argv[2]).resolve(strict=True)
sys.path.insert(0,str(sealed/"agent-core"))
import paths
from paths import AgentPaths
module_origin=pathlib.Path(paths.__file__).resolve(strict=True)
if module_origin != expected_module or AgentPaths.__module__ != paths.__name__:
    raise RuntimeError("AgentPaths module is not the sealed app implementation")
p=AgentPaths.discover()
actual={"agent_root":str(p.agent_root.resolve(strict=True)),"data":str(p.data.resolve(strict=True)),"workspace":str(p.workspace.resolve(strict=True)),"evolve":str(p.evolve.resolve(strict=True)),"module_origin":str(module_origin)}
print(json.dumps(actual,sort_keys=True,separators=(",",":")))`;
  const expected = { agent_root: appRoot, data: path.join(appRoot, "data"), workspace: path.join(appRoot, "workspace"), evolve: path.join(appRoot, "evolve") };
  const probe = env => {
    const result = spawnSync(python, ["-I", "-B", "-c", script, appRoot, path.join(appRoot, "agent-core", "paths.py")], { cwd: appRoot, env, encoding: "utf8", windowsHide: true, timeout: 15_000, maxBuffer: 1024 * 1024, shell: false });
    if (result.error || result.status !== 0 || typeof result.stdout !== "string") deny("H1 Python AgentPaths preflight failed");
    let actual; try { actual = JSON.parse(result.stdout.trim()); } catch { deny("H1 Python AgentPaths preflight returned invalid JSON"); }
    if (!actual || Object.keys(actual).length !== 5 || Object.keys(expected).some(key => typeof actual[key] !== "string" || !samePath(actual[key], expected[key])) || typeof actual.module_origin !== "string" || !samePath(actual.module_origin, path.join(appRoot, "agent-core", "paths.py"))) deny("H1 Python AgentPaths resolved outside this sealed run or imported an unsealed implementation");
    return Object.fromEntries(Object.keys(expected).map(key => [key, actual[key]]));
  };
  const explicitRoot = probe(buildH1RuntimeEnvironment(profileRoot, appRoot));
  // The Electron sidecar starts with cwd=AGENT_ROOT; prove marker-based discovery still binds if a runner strips MY_AGENT_ROOT.
  const inheritedRootFreeEnv = buildH1RuntimeEnvironment(profileRoot, appRoot); delete inheritedRootFreeEnv.MY_AGENT_ROOT;
  const cwdDiscovery = probe(inheritedRootFreeEnv);
  if (canonical(explicitRoot) !== canonical(cwdDiscovery)) deny("H1 AgentPaths differs when MY_AGENT_ROOT is absent");
  return explicitRoot;
}

export function createCandidateSourceFixture(input) {
  if (!input || typeof input !== "object" || Object.keys(input).length !== 2 || typeof input.sandboxRoot !== "string" || typeof input.runRoot !== "string") deny("candidate fixture input fields are invalid");
  const { sandboxRoot, runRoot } = validateRunRoot(input.sandboxRoot, input.runRoot);
  const hostRoot = realDirectory(path.join(runRoot, "host"), "frozen H1 host root");
  const appRoot = realDirectory(path.join(hostRoot, "app"), "frozen H1 app root");
  const profilePath = regularFile(path.join(hostRoot, "evolution-test-profile.json"), "H1 profile for T4 fixture");
  let profile; try { profile = JSON.parse(readFileSync(profilePath, "utf8")); } catch { deny("H1 profile for T4 fixture is invalid JSON"); }
  if (!profile || Object.keys(profile).length !== 7 || profile.candidate_fixture_profile !== "t4-recovery-node-v1" || !samePath(profile.profile_root, path.join(hostRoot, "profile"))) deny("H1 profile does not authorize the fixed T4 recovery fixture");
  const hostManifestPath = regularFile(path.join(hostRoot, "manifest.json"), "H1 fixture manifest");
  let hostManifest; try { hostManifest = JSON.parse(readFileSync(hostManifestPath, "utf8")); } catch { deny("H1 fixture manifest is invalid JSON"); }
  const manifestKeys = ["format", "scope", "app_root", "renderer_root", "profile_root", "files", "mutable_candidate_sources", "source_inputs", "profile_sha256", "control_release_id", "runtimes", "python_environment_recheck", "build_provenance"];
  if (!hostManifest || Object.keys(hostManifest).length !== manifestKeys.length || manifestKeys.some(key => !Object.hasOwn(hostManifest, key)) || !Array.isArray(hostManifest.files) || !Array.isArray(hostManifest.mutable_candidate_sources) || hostManifest.mutable_candidate_sources.length !== 0) deny("H1 fixture manifest cannot register mutable T4 sources");
  const source = realDirectory(path.join(sandboxRoot, "desktop", "tests", "evolution", "fixtures", "candidate-node"), "approved T4 candidate fixture source");
  const required = ["main.cjs", "service.cjs", "manifest.json"];
  const extra = readdirSync(source).filter(name => !required.includes(name));
  if (extra.length) deny("T4 fixture source contains unplanned files");
  const target = path.join(runRoot, "candidate-source"); if (existsSync(target)) deny("candidate source fixture already exists; refusing overwrite");
  const recoveryRoot = path.join(appRoot, "evolve", "tool-catalog", "recovery");
  if (!samePath(path.dirname(recoveryRoot), path.join(appRoot, "evolve", "tool-catalog"))) deny("T4 source root differs from the fixed owned path");
  if (existsSync(recoveryRoot)) deny("H1 T4 mutable source target already exists; refusing overwrite");
  mkdirSync(target, { recursive: false }); mkdirSync(recoveryRoot, { recursive: false });
  const files = []; const mutableSources = [];
  for (const relative of required) {
    const sourceFile = regularFile(path.join(source, relative), "approved T4 candidate fixture file");
    const destination = path.join(target, relative); copyFileSync(sourceFile, destination, constants.COPYFILE_EXCL);
    const bytes = readFileSync(destination); files.push({ path: relative, byte_length: bytes.length, sha256: sha256Hex(bytes) });
    const ownedPath = `evolve/tool-catalog/recovery/${relative}`; const ownedDestination = path.join(recoveryRoot, relative);
    copyFileSync(sourceFile, ownedDestination, constants.COPYFILE_EXCL);
    const ownedBytes = readFileSync(ownedDestination); mutableSources.push({ path: ownedPath, byte_length: ownedBytes.length, sha256: sha256Hex(ownedBytes) });
  }
  const fixtureManifest = JSON.parse(readFileSync(path.join(target, "manifest.json"), "utf8"));
  if (!fixtureManifest || Object.keys(fixtureManifest).length !== 1 || typeof fixtureManifest.broken !== "boolean" || fixtureManifest.broken !== true) deny("T4 candidate fixture must start in its declared broken state");
  hostManifest.mutable_candidate_sources = mutableSources;
  writeFileSync(hostManifestPath, `${JSON.stringify(hostManifest, null, 2)}\n`, { flag: "w" });
  return { source_root: target, owned_source_root: recoveryRoot, files, mutable_sources: mutableSources, broken: true, fixture_id: randomUUID(), fixture_profile: "t4-recovery-node-v1" };
}
export function verifyFrozenHostFixture(input, options = {}) {
  if (!input || typeof input !== "object" || Array.isArray(input)) deny("frozen Host descriptor is invalid");
  const { sandboxRoot, runRoot } = validateRunRoot(input.sandboxRoot, input.runRoot);
  const hostRoot = realDirectory(path.join(runRoot, "host"), "frozen H1 host root");
  const appRoot = realDirectory(path.resolve(input.app_root), "frozen H1 app root");
  const profileRoot = realDirectory(path.resolve(input.profile_root), "frozen H1 profile root");
  const rendererRoot = realDirectory(path.resolve(input.renderer_root), "frozen H1 renderer root");
  const manifestPath = regularFile(path.resolve(input.manifest_path), "frozen H1 manifest");
  if (!samePath(appRoot, path.join(hostRoot, "app")) || !samePath(profileRoot, path.join(hostRoot, "profile")) || !samePath(rendererRoot, path.join(appRoot, "desktop", "dist")) || !samePath(manifestPath, path.join(hostRoot, "manifest.json"))) deny("frozen Host layout differs from the H1 contract");
  let manifest; try { manifest = JSON.parse(readFileSync(manifestPath, "utf8")); } catch { deny("frozen H1 manifest is invalid JSON"); }
  const keys = ["format", "scope", "app_root", "renderer_root", "profile_root", "files", "mutable_candidate_sources", "source_inputs", "profile_sha256", "control_release_id", "runtimes", "python_environment_recheck", "build_provenance"];
  if (!manifest || typeof manifest !== "object" || Array.isArray(manifest) || Object.keys(manifest).length !== keys.length || keys.some(key => !Object.hasOwn(manifest, key)) || manifest.format !== 1 || manifest.scope !== "evolution-slice-a-h1-test-host" || !Array.isArray(manifest.files) || !Array.isArray(manifest.mutable_candidate_sources) || !Array.isArray(manifest.source_inputs)) deny("frozen H1 manifest fields are invalid");
  const profilePath = regularFile(path.join(hostRoot, "evolution-test-profile.json"), "frozen H1 profile");
  const profileBytes = readFileSync(profilePath);
  if (sha256Hex(profileBytes) !== manifest.profile_sha256) deny("frozen H1 profile digest changed");
  let profile; try { profile = JSON.parse(profileBytes.toString("utf8")); } catch { deny("frozen H1 profile is invalid JSON"); }
  const recoveryFixture = !!profile && Object.hasOwn(profile, "candidate_fixture_profile");
  const chatFixture = !!profile && Object.hasOwn(profile, "chat_fixture_profile");
  if (recoveryFixture && chatFixture) deny("T4 recovery and ordinary chat fixture profiles are mutually exclusive");
  if (!profile || Object.keys(profile).length !== (recoveryFixture ? 7 : chatFixture ? 8 : 6) || recoveryFixture && profile.candidate_fixture_profile !== "t4-recovery-node-v1" || chatFixture && ((profile.chat_fixture_profile !== "ordinary-chat-h1-v1" && profile.chat_fixture_profile !== "workbench-crash-reopen-recovery-v1") || profile.chat_run_id !== path.basename(runRoot)) || profile.ws_port !== 0 || profile.renderer_port !== 0 || !UUID_RE.test(String(profile.fixture_scope_id)) || !samePath(profile.profile_root, profileRoot)) deny("frozen H1 profile violates the dynamic-port fixture contract");
  if (!path.isAbsolute(profile.control_release_root) || !within(runRoot, path.resolve(profile.control_release_root))) deny("fixed release path escaped the marked run root");
  const expected = new Map();
  for (const item of manifest.files) {
    if (!item || typeof item !== "object" || Object.keys(item).length !== 3 || !safeRelative(item.path) || !Number.isSafeInteger(item.byte_length) || item.byte_length < 0 || !/^[0-9a-f]{64}$/.test(item.sha256) || expected.has(item.path)) deny("frozen H1 file manifest entry is invalid");
    expected.set(item.path, item);
    const target = path.join(appRoot, ...item.path.split("/")); const actual = regularFile(target, `frozen H1 file ${item.path}`); const bytes = readFileSync(actual);
    if (bytes.length !== item.byte_length || sha256Hex(bytes) !== item.sha256) deny(`frozen H1 file changed: ${item.path}`);
  }
  const requiredMutablePaths = recoveryFixture ? ["evolve/tool-catalog/recovery/main.cjs", "evolve/tool-catalog/recovery/service.cjs", "evolve/tool-catalog/recovery/manifest.json"] : [];
  if (manifest.mutable_candidate_sources.length !== requiredMutablePaths.length) deny("frozen H1 mutable-source registry differs from its fixed profile");
  const mutablePaths = [];
  for (let index = 0; index < manifest.mutable_candidate_sources.length; index++) {
    const item = manifest.mutable_candidate_sources[index];
    if (!item || typeof item !== "object" || Object.keys(item).length !== 3 || item.path !== requiredMutablePaths[index] || !Number.isSafeInteger(item.byte_length) || item.byte_length < 0 || !/^[0-9a-f]{64}$/.test(item.sha256) || expected.has(item.path)) deny("frozen H1 mutable-source entry is invalid");
    const target = regularFile(path.join(appRoot, ...item.path.split("/")), "fixed mutable H1 candidate source"); const bytes = readFileSync(target);
    if (bytes.length !== item.byte_length || sha256Hex(bytes) !== item.sha256) deny(`mutable H1 candidate source changed: ${item.path}`);
    mutablePaths.push(item.path);
  }
  const actual = listFiles(appRoot, { skipDirectory: rel => rel === "data", includeFile: () => true }).map(item => item.relative);
  const expectedPaths = [...expected.keys(), ...mutablePaths].sort((a, b) => a.localeCompare(b, "en"));
  const workbenchRecoveryRuntimeAllowed = chatFixture && profile.chat_fixture_profile === "workbench-crash-reopen-recovery-v1"
    ? ["evolve/user_profile.md"]
    : [];
  const actualForContract = actual.filter(rel => !workbenchRecoveryRuntimeAllowed.includes(rel));
  const extra = actualForContract.filter(rel => !expectedPaths.includes(rel));
  const missing = expectedPaths.filter(rel => !actualForContract.includes(rel));
  if (extra.length || missing.length) {
    const error = new Error("frozen H1 app contains missing or unmanifested files");
    error.code = "permission_denied";
    error.diagnostics = {
      extra,
      missing,
      runtime_allowed_app_paths: workbenchRecoveryRuntimeAllowed,
      unfiltered_extra: actual.filter(rel => !expectedPaths.includes(rel)),
    };
    throw error;
  }
  const sourceRoots = { desktop: path.join(sandboxRoot, "desktop") };
  for (const item of manifest.source_inputs) {
    if (!item || typeof item !== "object" || Object.keys(item).length !== 3 || typeof item.path !== "string" || !item.path.startsWith("desktop/") || !Number.isSafeInteger(item.byte_length) || !/^[0-9a-f]{64}$/.test(item.sha256)) deny("frozen H1 source-input entry is invalid");
    const rel = item.path.slice("desktop/".length);
    if (!safeRelative(rel)) deny("frozen H1 source-input path is unsafe");
    const source = regularFile(path.join(sourceRoots.desktop, ...rel.split("/")), "approved H1 source input"); const bytes = readFileSync(source);
    if (bytes.length !== item.byte_length || sha256Hex(bytes) !== item.sha256) deny(`approved H1 source changed after fixture freeze: ${item.path}`);
  }
  const pythonExecutable = regularFile(profile.trusted_python_executable, "attested H1 Python executable");
  const attestor = options.capturePythonEnvironment ?? ((executable, packages) => capturePythonEnvironment(executable, packages, { cwd: sandboxRoot }));
  const before = manifest.runtimes?.python?.environment;
  if (!before || typeof before.digest !== "string" || sha256Hex(readFileSync(pythonExecutable)) !== manifest.runtimes.python.executable_sha256) deny("frozen Python executable identity changed");
  const currentPython = attestor(pythonExecutable, before.packages?.map(item => item.name) ?? requirementsFromFile(sandboxRoot));
  if (currentPython && typeof currentPython.then === "function") deny("async Python attestor is not supported by synchronous verification");
  assertPythonEnvironmentUnchanged(before, currentPython);
  const electronExecutable = regularFile(manifest.runtimes?.electron?.executable, "attested H1 Electron executable");
  const currentElectron = distributionFingerprint(electronExecutable);
  if (currentElectron.distribution_digest !== manifest.runtimes.electron.distribution_digest || currentElectron.executable_sha256 !== manifest.runtimes.electron.executable_sha256) deny("frozen Electron distribution identity changed");
  return { sandbox_root: sandboxRoot, run_root: runRoot, host_root: hostRoot, app_root: appRoot, profile_root: profileRoot, renderer_root: rendererRoot, manifest_path: manifestPath, profile, manifest, python_environment: currentPython, electron: currentElectron };
}

export async function launchFrozenHost(input, options = {}) {
  const verified = verifyFrozenHostFixture(input, options);
  assertH1LaunchProfileEnabled(verified.profile, options);
  // Real launches always validate Python AgentPaths before native spawn.
  verifyAgentPathsBeforeH1Start(verified);
  if (options.spawnProcess !== undefined) deny("custom H1 spawn adapter is disabled; native spawn is required");
  const spawnProcess = (await import("node:child_process")).spawn;
  const plan = buildH1NativeLaunchPlan(verified, { mode: "exit", sourceEnvironment: process.env });
  const env = plan.options.env;
  const logRoot = path.join(verified.run_root, "host", "logs");
  if (existsSync(logRoot)) deny("H1 host log root already exists; refusing overwrite");
  mkdirSync(logRoot, { recursive: false });
  const stdoutPath = path.join(logRoot, "electron.stdout.log"); const stderrPath = path.join(logRoot, "electron.stderr.log");
  const exitPath = path.join(logRoot, "electron.exit.json");
  const child = spawnProcess(plan.executable, plan.args, plan.options);
  if (!child || typeof child.once !== "function") deny("H1 Electron process handle is invalid");
  return await completeH1ExitLifecycle({ child, stdoutPath, stderrPath, exitPath, runRoot: verified.run_root, pythonExecutable: verified.profile.trusted_python_executable, pythonPackages: verified.manifest.runtimes.python.environment.packages.map(item => item.name), beforeEnvironment: verified.python_environment, captureEnvironment: options.capturePythonEnvironment ?? ((executable, packages) => capturePythonEnvironment(executable, packages, { cwd: verified.sandbox_root })) });
}

export function resolveH1HostCwd(verified) {
  if (!verified || typeof verified !== "object" || typeof verified.sandbox_root !== "string" || typeof verified.run_root !== "string" || typeof verified.app_root !== "string") deny("verified H1 sandbox/run/app roots are required for Electron cwd");
  const { runRoot } = validateRunRoot(verified.sandbox_root, verified.run_root);
  const appRoot = realDirectory(verified.app_root, "sealed H1 Electron cwd");
  if (!samePath(appRoot, path.join(runRoot, "host", "app")) || !within(runRoot, appRoot)) deny("H1 Electron cwd escaped the sealed app root");
  return appRoot;
}
export function createH1SpawnEnvironmentEvidence(env) {
  if (!env || typeof env !== "object" || Array.isArray(env)) deny("H1 spawn environment evidence input is invalid");
  const forwardedKeys = Object.keys(env).sort();
  const credentialKeys = forwardedKeys.filter(key => /(?:API.?KEY|TOKEN|SECRET|CREDENTIAL)/i.test(key));
  if (credentialKeys.length) deny("H1 spawn environment contains a credential-like key");
  return { forwarded_keys: forwardedKeys, credential_env_forwarded_keys: credentialKeys, isolated_agent_root: env.MY_AGENT_ROOT, isolated_user_profile: env.USERPROFILE, isolated_app_data: env.APPDATA, isolated_local_app_data: env.LOCALAPPDATA, isolated_temp: env.TEMP };
}

export async function cleanupFailedH1NativeLaunch(child, lifecycle = null) {
  if (lifecycle && typeof lifecycle.close === "function") {
    try { await lifecycle.close(); } catch { /* preserve the startup failure; still check this child handle */ }
  }
  if (!child || (child.exitCode !== null && child.exitCode !== undefined) || (child.signalCode !== null && child.signalCode !== undefined)) return;
  if (typeof child.once !== "function" || typeof child.kill !== "function") return;
  let resolveClose;
  const closed = new Promise(resolve => { resolveClose = resolve; });
  child.once("close", resolveClose);
  if ((child.exitCode !== null && child.exitCode !== undefined) || (child.signalCode !== null && child.signalCode !== undefined)) return;
  try { child.kill(); } catch { /* cleanup is bound to this native child handle only */ }
  if ((child.exitCode !== null && child.exitCode !== undefined) || (child.signalCode !== null && child.signalCode !== undefined)) return;
  await closed;
}
export async function startH1ControlledLifecycle(child, { profile, token, cdpEnabled = false, readyTimeoutMs = 180_000, shutdownTimeoutMs = 20_000, stdoutPath, stderrPath, exitPath, runRoot, pythonExecutable, pythonPackages, beforeEnvironment, captureEnvironment } = {}) {
  if (!child?.stdin || !child?.stdout || !child?.stderr || typeof child.once !== "function" || typeof child.kill !== "function") deny("H1 controlled lifecycle requires an event-capable private-stdin child");
  if (![stdoutPath, stderrPath, exitPath, runRoot, pythonExecutable].every(value => typeof value === "string" && path.isAbsolute(value)) || typeof captureEnvironment !== "function") deny("H1 controlled lifecycle evidence and recheck paths are required");
  const protocol = createH1LifecycleProtocol(profile, token);
  const ordinaryChat = profile === "ordinary-chat-h1-v1" || profile === "workbench-crash-reopen-recovery-v1";
  const stdout = [], stderr = []; let total = 0, stdoutBuffer = "", startError = null, closed = false, closeRecord = null;
  let shutdownMethod = ordinaryChat ? "cdp_browser_close" : "stdin_control_frame", shutdownRequestSent = false, shutdownRequestAcknowledged = false, shutdownRequestError = null;
  let closeResolve, readyResolve, readyReject;
  const closedPromise = new Promise(resolve => { closeResolve = resolve; });
  const readyPromise = new Promise((resolve, reject) => { readyResolve = resolve; readyReject = reject; });
  let cdpBuffer = "", cdpWebSocketUrl = null, cdpFailure = null, resolveCdpReady;
  const cdpReadyPromise = new Promise(resolve => { resolveCdpReady = resolve; });
  const observeCdpOutput = chunk => {
    if (!cdpEnabled || cdpWebSocketUrl || cdpFailure) return;
    cdpBuffer = (cdpBuffer + Buffer.from(chunk).toString("utf8")).slice(-16_384);
    const match = /DevTools listening on (ws:\/\/[^\s]+)/u.exec(cdpBuffer);
    if (!match) return;
    try { cdpWebSocketUrl = validateH1CdpEndpoint(match[1]); resolveCdpReady(cdpWebSocketUrl); }
    catch (error) { cdpFailure = error; resolveCdpReady(null); }
  };
  const waitForCdp = async () => {
    if (!cdpEnabled) deny("H1 CDP was not requested for this Host");
    if (cdpFailure) throw cdpFailure;
    if (cdpWebSocketUrl) return cdpWebSocketUrl;
    let timer;
    const timeout = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("dynamic loopback CDP endpoint timed out")), 20_000); });
    try {
      const endpoint = await Promise.race([cdpReadyPromise, timeout, closedPromise.then(() => { throw new Error("Host exited before CDP became ready"); })]);
      if (cdpFailure) throw cdpFailure;
      return validateH1CdpEndpoint(endpoint);
    } finally { clearTimeout(timer); }
  };
  const append = (target, bytes) => { total += bytes.length; if (total > 5 * 1024 * 1024) { try { child.kill(); } catch { /* retain over-limit evidence */ } return false; } target.push(bytes); return true; };
  child.stdout.on("data", chunk => {
    const bytes = Buffer.from(chunk); if (!append(stdout, bytes)) return; observeCdpOutput(bytes);
    stdoutBuffer += bytes.toString("utf8");
    for (;;) {
      const newline = stdoutBuffer.indexOf("\n"); if (newline < 0) break;
      const line = stdoutBuffer.slice(0, newline).replace(/\r$/u, ""); stdoutBuffer = stdoutBuffer.slice(newline + 1);
      const message = protocol.parseReadyLine(line); if (message) readyResolve(message);
    }
  });
  child.stderr.on("data", chunk => { const bytes = Buffer.from(chunk); if (append(stderr, bytes)) observeCdpOutput(bytes); });
  child.once("error", error => { startError = error; readyReject(error); });
  child.once("close", (code, signal) => { closed = true; closeRecord = { code, signal }; readyReject(new Error("frozen H1 Host exited before signalling readiness")); closeResolve(closeRecord); });
  child.stdin.write(protocol.bootstrapFrame);
  const readyTimer = setTimeout(() => readyReject(new Error("frozen H1 Host ready marker timed out")), readyTimeoutMs); readyTimer.unref?.();
  let ready;
  try { ready = await readyPromise; }
  catch (error) {
    clearTimeout(readyTimer);
    if (!closed) {
      if (!ordinaryChat) { try { child.stdin.end(protocol.shutdownFrame); } catch { /* preserve startup failure */ } }
      else if (cdpEnabled && cdpWebSocketUrl) { try { await closeH1BrowserViaCdp(cdpWebSocketUrl, { timeoutMs: Math.min(5_000, shutdownTimeoutMs) }); } catch { /* startup failure remains primary; bounded child-handle cleanup follows */ } }
    }
    const exit = await Promise.race([closedPromise, new Promise(resolve => setTimeout(() => resolve(null), 15_000))]);
    if (!exit && !closed) { try { child.kill(); } catch { /* preserve startup failure */ } await closedPromise; }
    writeFileSync(stdoutPath, Buffer.concat(stdout), { flag: "wx" }); writeFileSync(stderrPath, Buffer.concat(stderr), { flag: "wx" }); throw error;
  } finally { clearTimeout(readyTimer); }
  let finalized = false, forcedTermination = false;
  const finalize = async () => {
    if (finalized) return closeRecord;
    finalized = true; const exit = await closedPromise;
    writeFileSync(stdoutPath, Buffer.concat(stdout), { flag: "wx" }); writeFileSync(stderrPath, Buffer.concat(stderr), { flag: "wx" });
    let after = null, environmentError = null, pythonEnvironmentUnchanged = false;
    try { after = captureEnvironment(pythonExecutable, pythonPackages); assertPythonEnvironmentUnchanged(beforeEnvironment, after); pythonEnvironmentUnchanged = true; }
    catch (error) { environmentError = String(error?.message ?? error).slice(0, 512); }
    const record = { ...exit, shutdown_method: shutdownMethod, shutdown_request_sent: shutdownRequestSent, shutdown_request_acknowledged: shutdownRequestAcknowledged, shutdown_request_error: shutdownRequestError, forced_termination: forcedTermination, shutdown_timeout_ms: shutdownTimeoutMs, ...(startError ? { start_error: String(startError.message ?? startError).slice(0, 512) } : {}), stdout: path.relative(runRoot, stdoutPath).replace(/\\/g, "/"), stderr: path.relative(runRoot, stderrPath).replace(/\\/g, "/"), python_environment_digest: after?.digest ?? null, python_environment_unchanged: pythonEnvironmentUnchanged, ...(environmentError ? { python_environment_error: environmentError } : {}) };
    writeFileSync(exitPath, `${JSON.stringify(record, null, 2)}\n`, { flag: "wx" });
    if (!pythonEnvironmentUnchanged) throw new Error(environmentError ?? "trusted Python environment changed during the drill");
    return record;
  };
  let closePromise = null;
  const closeOnce = async () => {
    if (!closed) {
      if (ordinaryChat) {
        if (!cdpEnabled) shutdownRequestError = "dynamic_cdp_not_enabled";
        else {
          try {
            const endpoint = cdpWebSocketUrl ?? await waitForCdp();
            const result = await closeH1BrowserViaCdp(endpoint, { timeoutMs: Math.min(5_000, shutdownTimeoutMs) });
            shutdownRequestSent = result.command_sent === true; shutdownRequestAcknowledged = result.acknowledged === true;
          } catch (error) { shutdownRequestError = error?.message === "H1 CDP Browser.close timed out" ? "cdp_close_timeout" : "cdp_close_failed"; }
        }
      } else {
        try { child.stdin.end(protocol.shutdownFrame); shutdownRequestSent = true; }
        catch { shutdownRequestError = "stdin_shutdown_failed"; }
      }
    }
    const exit = await Promise.race([closedPromise, new Promise(resolve => setTimeout(() => resolve(null), shutdownTimeoutMs))]);
    if (!exit && !closed) { forcedTermination = true; try { child.kill(); } catch { /* retain shutdown evidence */ } await closedPromise; }
    return await finalize();
  };
  return {
    child, ready, ...(cdpEnabled ? { waitForCdp } : {}),
    close() { if (!closePromise) closePromise = closeOnce(); return closePromise; },
  };
}
export async function startFrozenHost(input, options = {}) {
  const verified = verifyFrozenHostFixture(input, options);
  assertH1LaunchProfileEnabled(verified.profile, options);
  verifyAgentPathsBeforeH1Start(verified);
  if (options.spawnProcess !== undefined) deny("custom spawnProcess is forbidden; use native H1 spawn with the strict CDP option");
  const sealedChat = verified.profile.chat_fixture_profile === "ordinary-chat-h1-v1" || verified.profile.chat_fixture_profile === "workbench-crash-reopen-recovery-v1";
  const profile = sealedChat ? verified.profile.chat_fixture_profile : verified.profile.candidate_fixture_profile;
  if (profile !== "ordinary-chat-h1-v1" && profile !== "workbench-crash-reopen-recovery-v1" && profile !== "t4-recovery-node-v1") deny("H1 controlled launcher requires an explicit synthetic T4 or sealed chat profile");
  if (sealedChat && options.cdp?.enabled !== true) deny("sealed chat H1 requires the existing dynamic CDP close path");
  resolveH1HostCwd(verified);
  const plan = buildH1NativeLaunchPlan(verified, { mode: "controlled", cdp: options.cdp, sourceEnvironment: process.env });
  const env = plan.options.env;
  const logRoot = path.join(verified.run_root, "host", "logs");
  if (existsSync(logRoot)) deny("H1 host log root already exists; refusing overwrite");
  mkdirSync(logRoot, { recursive: false });
  const stdoutPath = path.join(logRoot, "electron.stdout.log"), stderrPath = path.join(logRoot, "electron.stderr.log"), exitPath = path.join(logRoot, "electron.exit.json");
  const spawnProcess = (await import("node:child_process")).spawn;
  const child = spawnProcess(plan.executable, plan.args, plan.options);
  let lifecycle = null;
  try {
    lifecycle = await startH1ControlledLifecycle(child, {
      profile, token: randomBytes(32).toString("hex"), cdpEnabled: options.cdp !== undefined,
      readyTimeoutMs: options.readyTimeoutMs ?? 180_000, shutdownTimeoutMs: options.shutdownTimeoutMs ?? 20_000,
      stdoutPath, stderrPath, exitPath, runRoot: verified.run_root,
      pythonExecutable: verified.profile.trusted_python_executable,
      pythonPackages: verified.manifest.runtimes.python.environment.packages.map(item => item.name),
      beforeEnvironment: verified.python_environment,
      captureEnvironment: options.capturePythonEnvironment ?? ((executable, packages) => capturePythonEnvironment(executable, packages, { cwd: verified.sandbox_root })),
    });
    const spawnEnvironmentEvidence = createH1SpawnEnvironmentEvidence(env);
    return { child, port: lifecycle.ready.port, spawn_environment_evidence: spawnEnvironmentEvidence, ...(lifecycle.waitForCdp ? { waitForCdp: lifecycle.waitForCdp } : {}), ...(sealedChat ? { renderer_port: lifecycle.ready.renderer_port } : {}), close: lifecycle.close };
  } catch (error) {
    await cleanupFailedH1NativeLaunch(child, lifecycle);
    throw error;
  }
}export function createT9ScriptedWebSocketDriver(input) {
  if (!input || typeof input !== "object" || typeof input.port !== "number" || !Number.isSafeInteger(input.port) || input.port < 1 || input.port > 65_535 || typeof input.runRoot !== "string" || !path.isAbsolute(input.runRoot)) deny("T9 WebSocket driver input is invalid");
  const WebSocketClass = input.WebSocketClass ?? globalThis.WebSocket;
  if (typeof WebSocketClass !== "function") deny("this Node runtime does not provide the built-in WebSocket client");
  const runRoot = realDirectory(input.runRoot, "T9 WebSocket run root");
  const socket = new WebSocketClass(`ws://127.0.0.1:${input.port}`);
  const pending = new Map(); let conversationId = null; let closed = false; let fatal = null; let evidenceRoot = null; let activeBinding = null; let initialBaseline = null; let lifecycleStarted = false; let activeInstanceId = null;
  let resolveOpen; let rejectOpen; const opened = new Promise((resolve, reject) => { resolveOpen = resolve; rejectOpen = reject; });
  let resolveSession; let rejectSession; const sessionReady = new Promise((resolve, reject) => { resolveSession = resolve; rejectSession = reject; });
  const waiters = new Map();
  const fileEvidence = (label, value) => {
    if (!evidenceRoot) deny("T9 evidence root is not initialized");
    const target = path.join(evidenceRoot, `${label}-${randomUUID()}.json`);
    writeFileSync(target, `${JSON.stringify(value, null, 2)}\n`, { flag: "wx" });
    return target;
  };
  async function askConfirmation(preview) {
    if (typeof input.io?.confirm === "function") return (await input.io.confirm(`H1 T9 action confirmation:\n${preview}`)) === true;
    const terminalInput = input.io?.input ?? process.stdin; const terminalOutput = input.io?.output ?? process.stdout;
    if (!terminalInput.isTTY || !terminalOutput.isTTY) return false;
    const readline = await import("node:readline/promises"); const terminal = readline.createInterface({ input: terminalInput, output: terminalOutput });
    try { return (await terminal.question(`H1 T9 action confirmation:\n${preview}\nType yes to allow: `)).trim().toLowerCase() === "yes"; }
    finally { terminal.close(); }
  }
  socket.addEventListener("open", () => resolveOpen());
  socket.addEventListener("error", () => { const error = new Error("T9 H1 WebSocket connection failed"); fatal = error; rejectOpen(error); rejectSession(error); for (const waiter of waiters.values()) waiter.reject(error); waiters.clear(); });
  socket.addEventListener("close", () => { closed = true; const error = fatal ?? new Error("T9 H1 WebSocket closed"); rejectSession(error); for (const waiter of waiters.values()) waiter.reject(error); waiters.clear(); });
  socket.addEventListener("message", event => {
    let message; try { message = JSON.parse(String(event.data)); } catch { return; }
    if (!message || typeof message !== "object" || Array.isArray(message)) return;
    if (message.type === "session.banner" && typeof message.session_id === "string" && UUID_RE.test(message.session_id)) { conversationId = message.session_id; resolveSession(conversationId); return; }
    if (message.type === "confirm.request" && typeof message.request_id === "string" && typeof message.preview === "string") {
      void askConfirmation(message.preview).then(allowed => {
        if (socket.readyState === WebSocketClass.OPEN) socket.send(JSON.stringify({ type: "confirm.response", request_id: message.request_id, choice: allowed ? "yes" : "no" }));
      }).catch(() => { if (socket.readyState === WebSocketClass.OPEN) socket.send(JSON.stringify({ type: "confirm.response", request_id: message.request_id, choice: "no" })); });
      return;
    }
    if (message.type === "evolution.scripted.result" && typeof message.request_id === "string") {
      const waiter = waiters.get(message.request_id); if (waiter) { waiters.delete(message.request_id); waiter.resolve(message); }
    }
  });
  async function sendOperation(operation, fields = {}) {
    await opened;
    if (!conversationId) await sessionReady;
    if (closed || fatal) throw fatal ?? new Error("T9 H1 WebSocket is closed");
    const requestId = randomUUID();
    const response = new Promise((resolve, reject) => {
      const timer = setTimeout(() => { waiters.delete(requestId); reject(new Error(`T9 scripted operation timed out: ${operation}`)); }, input.requestTimeoutMs ?? 600_000);
      waiters.set(requestId, { resolve: value => { clearTimeout(timer); resolve(value); }, reject: error => { clearTimeout(timer); reject(error); } });
    });
    socket.send(JSON.stringify({ type: "evolution.scripted", request_id: requestId, conversation_id: conversationId, operation, ...fields }));
    const result = await response;
    if (result.status !== "succeeded") throw Object.assign(new Error(result.error?.message ?? `T9 scripted ${operation} failed`), { code: result.error?.code ?? "outcome_unknown", response: result });
    return result.result;
  }
  function saveActionEvidence(label, value) { return fileEvidence(label, { observed_at: new Date().toISOString(), conversation_id: conversationId, value }); }
  function requireControlResult(value, label) {
    if (!value || typeof value !== "object" || typeof value.status !== "string") throw Object.assign(new Error(`${label} returned an invalid trusted control result`), { code: "outcome_unknown" });
    return value;
  }
  function unwrapTool(value, label) {
    if (!value || typeof value !== "object") throw Object.assign(new Error(`${label} returned an invalid tool envelope`), { code: "outcome_unknown" });
    if (value.ok === true && value.data && typeof value.data === "object" && typeof value.data.status === "string") return requireControlResult({ operation_id: value.data.operation_id, status: value.data.status, result: value.data.result, error: null }, label);
    const control = value.error?.details?.control_result;
    if (control && typeof control === "object") return requireControlResult(control, label);
    throw Object.assign(new Error(value.error?.message ?? `${label} failed without a terminal supervisor receipt`), { code: value.error?.code ?? "outcome_unknown", tool_result: value });
  }
  async function action(actionName, payload) { return await sendOperation("action", { action: actionName, payload }); }
  return {
    evidenceMode: "trusted_host",
    async captureBaseline({ evidenceRoot: target }) { evidenceRoot = realDirectory(target, "T9 scripted evidence root"); initialBaseline = input.baseline; if (!initialBaseline) initialBaseline = captureStableBaseline(input.sandboxRoot, runRoot, input.baselineOptions ?? {}); const locator = fileEvidence("stable-baseline", initialBaseline); return { ...initialBaseline, evidence_locator: locator }; },
    async bindSession() { await opened; await sessionReady; await sendOperation("lifecycle.begin"); lifecycleStarted = true; const result = await sendOperation("bind"); activeBinding = { conversation_id: result.conversation_id, evolution_id: result.evolution_id, host_run_id: result.host_run_id }; if (activeBinding.conversation_id !== conversationId) deny("T9 bound Session identity changed during startup"); return activeBinding; },
    async inspect(binding) { const result = requireControlResult(await action("inspect", { query: "summary" }), "inspect"); if (result.status !== "succeeded" || !result.result || result.result.conversation_id !== binding.conversation_id || result.result.evolution_id !== binding.evolution_id) throw Object.assign(new Error("H1 inspect did not return the active Session projection"), { code: "identity_mismatch" }); const evidence_locator = saveActionEvidence("inspect", result); return { ...result.result, evidence_locator }; },
    async patch({ binding, broken }) {
      if (activeBinding?.conversation_id !== binding.conversation_id) deny("T9 patch binding changed");
      const target = "evolve/tool-catalog/recovery/manifest.json";
      if (broken) {
        const current = requireControlResult(await action("inspect", { query: "source_text", path: target }), "initial manifest inspect");
        let manifest; try { manifest = JSON.parse(current.result?.content_utf8); } catch { deny("initial T4 manifest is not valid JSON"); }
        if (manifest?.broken !== true || typeof current.result?.sha256 !== "string") deny("initial T4 manifest is not in the declared broken state");
        return { path: target, before_hash: current.result.sha256, workspace_revision: current.result.workspace_revision, broken: true, evidence_locator: saveActionEvidence("broken-fixture-inventory", current) };
      }
      const inventory = requireControlResult(await action("inspect", { query: "source_list" }), "source inventory");
      const file = Array.isArray(inventory.result?.files) ? inventory.result.files.find(item => item?.path === target) : null;
      if (!file || typeof file.sha256 !== "string") throw Object.assign(new Error("fixed T4 manifest is missing from the trusted source inventory"), { code: "scope_conflict" });
      const result = requireControlResult(unwrapTool(await action("patch", { path: target, expected_before_hash: file.sha256, content_utf8: `${JSON.stringify({ broken: false })}\n` }), "source patch"), "source patch");
      if (result.status !== "succeeded" || result.result?.path !== target) throw Object.assign(new Error("repair patch did not return a successful exact-path CAS receipt"), { code: result.error?.code ?? "outcome_unknown", response: result });
      const evidence_locator = saveActionEvidence("repair-patch", result);
      return { ...result.result, evidence_locator, path: target, broken: false };
    },    async snapshot({ binding, patch }) {
      if (activeBinding?.conversation_id !== binding.conversation_id || typeof patch.workspace_revision !== "number") {
        const inventory = requireControlResult(await action("inspect", { query: "source_list" }), "source inventory");
        patch.workspace_revision = inventory.result.workspace_revision;
      }
      const include_paths = ["evolve/tool-catalog/recovery/main.cjs", "evolve/tool-catalog/recovery/service.cjs", "evolve/tool-catalog/recovery/manifest.json"];
      const result = requireControlResult(unwrapTool(await action("snapshot", { workspace_revision: patch.workspace_revision, include_paths }), "source snapshot"), "source snapshot");
      const evidence_locator = saveActionEvidence("source-snapshot", result);
      return { ...result.result, evidence_locator };
    },
    async build({ binding, snapshot }) {
      const identity = snapshot.t4_recovery_descriptor;
      if (!identity || identity.artifact_id !== "99999999-9999-4999-8999-999999999999" || typeof identity.build_input_digest !== "string") throw Object.assign(new Error("trusted snapshot omitted the fixed T4 descriptor identity"), { code: "identity_mismatch" });
      const result = requireControlResult(unwrapTool(await action("build", { source_snapshot_id: snapshot.source_snapshot_id, descriptor_artifact_id: identity.artifact_id, build_input_digest: identity.build_input_digest, isolation_mode: "local_managed" }), "candidate build"), "candidate build");
      const evidence_locator = saveActionEvidence("candidate-build", result);
      return { ...result.result, evidence_locator };
    },
    async launch({ binding, build }) {
      const result = requireControlResult(unwrapTool(await action("launch", { candidate_id: build.candidate_id, profile_fixture_id: "88888888-8888-4888-8888-888888888888" }), "candidate launch"), "candidate launch");
      const receipt = result.result; const components = Array.isArray(receipt?.components) ? receipt.components : []; if (typeof receipt?.instance_id === "string") activeInstanceId = receipt.instance_id;
      const failedComponent = components.find(item => item?.exit_code === 17);
      const evidence_locator = saveActionEvidence(receipt?.status === "ready" ? "candidate-ready" : "candidate-failed", { control_result: result, components });
      return { status: receipt?.status, exit_code: failedComponent?.exit_code ?? (receipt?.status === "ready" ? 0 : null), stderr_excerpt: failedComponent?.stderr_excerpt ?? components.map(item => item?.stderr_excerpt ?? "").join("\n").slice(0, 1024), instance_id: receipt?.instance_id, candidate_id: receipt?.candidate_id, source_snapshot_id: receipt?.source_snapshot_id ?? build.source_snapshot_id, components, evidence_locator };
    },
    async deliverLateReady({ binding, failed_instance_id, failed_candidate_id }) {
      const result = await sendOperation("late_ready", { evolution_id: binding.evolution_id, candidate_id: failed_candidate_id, instance_id: failed_instance_id, instance_sequence: 1 });
      const evidence_locator = saveActionEvidence("late-ready-rejected", result);
      return { ...result, evidence_locator };
    },
    async stop({ binding, instance_id }) {
      const result = requireControlResult(unwrapTool(await action("stop", { instance_id }), "candidate stop"), "candidate stop");
      const evidence_locator = saveActionEvidence("candidate-stop", result);
      if (result.result?.status === "stopped") activeInstanceId = null;
      return { ...result.result, evidence_locator };
    },
    async captureConversationEvidence({ binding, before, after, evidenceRoot: target }) { const locator = fileEvidence("same-conversation", { conversation_id: binding.conversation_id, before, after, same_conversation: before.conversation_id === binding.conversation_id && after.conversation_id === binding.conversation_id }); return locator; },
    async captureFinal({ baseline, binding, failed, repaired, stop }) {
      const journalPath = regularFile(path.join(runRoot, "supervisor-state", "journal.jsonl"), "T9 operation journal");
      const journalBytes = readFileSync(journalPath); const journalEvidence = fileEvidence("operation-journal", { path: path.relative(runRoot, journalPath).replace(/\\/g, "/"), sha256: sha256Hex(journalBytes), content_utf8: journalBytes.toString("utf8") });
      const candidateLogLocators = [];
      for (const receipt of [failed, repaired]) for (const component of receipt.components ?? []) {
        if (component.pid === null || component.pid === undefined) continue;
        for (const stream of ["stdout", "stderr"]) { const log = regularFile(path.join(runRoot, "candidate-logs", receipt.instance_id, `${component.component_id}.${stream}.log`), `T9 candidate ${stream} log`); candidateLogLocators.push(log); }
      }
      const current = captureStableBaseline(input.sandboxRoot, runRoot, input.baselineOptions ?? {});
      let stableError = null; try { verifyStableBaselineUnchanged(baseline, current); } catch (error) { stableError = String(error?.message ?? error); }
      const stable_tree_unchanged = stableError === null; const stable_tree_evidence = fileEvidence("stable-final", { baseline, current, stable_tree_unchanged, stable_error: stableError });
      const profile = JSON.parse(readFileSync(path.join(runRoot, "host", "evolution-test-profile.json"), "utf8"));
      const releaseRoot = realDirectory(profile.control_release_root, "fixed T9 control release"); const manifestPath = regularFile(path.join(releaseRoot, "manifest.json"), "fixed T9 control release manifest");
      const manifestBytes = readFileSync(manifestPath); const manifestDigest = `sha256:${sha256Hex(manifestBytes)}`;
      const release = { release_id: manifestDigest, manifest_digest: manifestDigest, root: releaseRoot, manifest_path: manifestPath, node_executable: JSON.parse(manifestBytes.toString("utf8")).runtimes.node.path, python_executable: JSON.parse(manifestBytes.toString("utf8")).runtimes.python.path };
      const require = createRequire(import.meta.url); const releaseApi = require(path.join(input.sandboxRoot, "artifacts", "evolution-control-build", "control-release.js"));
      let control_release_unchanged = true; let controlError = null; try { await releaseApi.verifyControlRelease(release); } catch (error) { control_release_unchanged = false; controlError = String(error?.message ?? error); }
      const frozenHostManifest = JSON.parse(readFileSync(path.join(runRoot, "host", "manifest.json"), "utf8"));
      const expectedReleaseId = frozenHostManifest.control_release_id; if (expectedReleaseId !== manifestDigest) { control_release_unchanged = false; controlError = "fixed control release id differs from the frozen H1 manifest"; }
      const control_release_evidence = fileEvidence("control-release-final", { expected_release_id: expectedReleaseId, observed_release_id: manifestDigest, control_release_unchanged, error: controlError });
      const finish = await sendOperation("lifecycle.finish"); lifecycleStarted = false;
      const conversation = fileEvidence("scripted-recovery-evidence", { conversation_id: binding.conversation_id, failed, repaired, stop, lifecycle_finish: finish, created_at: new Date().toISOString() });
      const evidence_locators = [conversation, journalEvidence, ...candidateLogLocators, stable_tree_evidence, control_release_evidence, failed.evidence_locator, repaired.evidence_locator, stop.evidence_locator];
      return { evidence_locators, summary_evidence: conversation, stable_tree_unchanged, stable_tree_evidence, control_release_unchanged, control_release_evidence };
    },
    async close() {
      if (socket.readyState === WebSocketClass.OPEN && activeBinding) {
        try {
          const projection = requireControlResult(await action("inspect", { query: "summary" }), "cleanup inspect").result;
          const ownedInstance = activeInstanceId ?? projection?.current_instance_id;
          if (typeof ownedInstance === "string" && projection?.candidate_status !== "stopped") {
            const stopped = requireControlResult(unwrapTool(await action("stop", { instance_id: ownedInstance }), "cleanup stop"), "cleanup stop");
            if (stopped.result?.status === "stopped") activeInstanceId = null;
          }
        } catch { /* preserve the primary drill failure; the Host remains available for explicit cleanup */ }
        if (lifecycleStarted) { try { await sendOperation("lifecycle.finish"); lifecycleStarted = false; } catch { /* retain incomplete lifecycle evidence */ } }
      }
      if (socket.readyState === WebSocketClass.OPEN) { socket.close(1000, "T9 scripted driver complete"); await new Promise(resolve => { const timer = setTimeout(resolve, 2_000); socket.addEventListener("close", () => { clearTimeout(timer); resolve(); }, { once: true }); }); }
    },
  };
}
export async function runScriptedRecovery(input) {
  if (!input || typeof input !== "object" || Array.isArray(input) || !input.driver || typeof input.driver !== "object" || typeof input.runRoot !== "string" || !path.isAbsolute(input.runRoot)) deny("scripted recovery driver is invalid");
  const { sandboxRoot, runRoot } = validateRunRoot(input.sandboxRoot, input.runRoot);
  validateDrillAuthorization(input.authorization);
  if (input.authorization.mode !== "scripted") deny("scripted recovery requires scripted authorization");
  const required = ["captureBaseline", "bindSession", "inspect", "patch", "snapshot", "build", "launch", "deliverLateReady", "stop", "captureConversationEvidence", "captureFinal"];
  for (const key of required) if (typeof input.driver[key] !== "function") deny("scripted recovery driver is missing " + key);
  if (!["trusted_host", "unit_fake"].includes(input.driver.evidenceMode)) deny("scripted driver evidence mode is invalid");
  const trustedEvidence = input.driver.evidenceMode === "trusted_host";
  const summary = createDrillSummary("scripted");
  const evidenceRoot = path.join(runRoot, "scripted-evidence-" + randomUUID()); mkdirSync(evidenceRoot, { recursive: false });
  const baseline = await input.driver.captureBaseline({ sandboxRoot, runRoot, evidenceRoot });
  const binding = await input.driver.bindSession({ runRoot, evidenceRoot });
  if (!binding || typeof binding.conversation_id !== "string" || !UUID_RE.test(binding.conversation_id) || typeof binding.evolution_id !== "string" || !UUID_RE.test(binding.evolution_id)) deny("scripted drill did not bind a trusted control session");
  const firstState = await input.driver.inspect(binding);
  if (!firstState || firstState.conversation_id !== binding.conversation_id || typeof firstState.evidence_locator !== "string") deny("initial inspect lacks same-conversation evidence");
  const brokenPatch = await input.driver.patch({ binding, broken: true });
  const failedSnapshot = await input.driver.snapshot({ binding, patch: brokenPatch });
  const failedBuild = await input.driver.build({ binding, snapshot: failedSnapshot });
  const failed = await input.driver.launch({ binding, build: failedBuild });
  if (!failed || failed.status !== "failed" || failed.exit_code !== 17 || !UUID_RE.test(failed.instance_id ?? "") || typeof failed.evidence_locator !== "string" || typeof failed.stderr_excerpt !== "string" || !failed.stderr_excerpt.includes("fixture_start_failed")) deny("fresh broken candidate did not produce the expected observed exit 17 and fixture stderr");
  if (trustedEvidence) recordDrillEvidence(summary, { field: "candidate_fail_observed", value: true, locator: failed.evidence_locator, observed_at: new Date().toISOString() }, runRoot);

  const afterFailure = await input.driver.inspect(binding);
  if (!afterFailure || afterFailure.conversation_id !== binding.conversation_id || typeof afterFailure.evidence_locator !== "string") deny("main conversation identity changed or lacks evidence after candidate failure");
  const conversationEvidence = await input.driver.captureConversationEvidence({ binding, before: firstState, after: afterFailure, evidenceRoot });
  if (trustedEvidence) recordDrillEvidence(summary, { field: "main_conversation_preserved", value: true, locator: conversationEvidence, observed_at: new Date().toISOString() }, runRoot);

  const fixedPatch = await input.driver.patch({ binding, broken: false });
  const repairedSnapshot = await input.driver.snapshot({ binding, patch: fixedPatch });
  const repairedBuild = await input.driver.build({ binding, snapshot: repairedSnapshot });
  const repaired = await input.driver.launch({ binding, build: repairedBuild });
  if (!repaired || repaired.status !== "ready" || !UUID_RE.test(repaired.instance_id ?? "") || repaired.instance_id === failed.instance_id || repaired.candidate_id === failed.candidate_id || repaired.source_snapshot_id === failed.source_snapshot_id || typeof repaired.evidence_locator !== "string" || !Array.isArray(repaired.components) || repaired.components.length !== 2 || !repaired.components.every(item => item.ready === true)) deny("recovery was not demonstrated by a distinct candidate with both declared components ready");
  if (trustedEvidence) recordDrillEvidence(summary, { field: "candidate_repaired_ready", value: true, locator: repaired.evidence_locator, observed_at: new Date().toISOString() }, runRoot);

  const late = await input.driver.deliverLateReady({ binding, failed_instance_id: failed.instance_id, failed_candidate_id: failed.candidate_id, repaired_instance_id: repaired.instance_id, repaired_candidate_id: repaired.candidate_id });
  if (!late || late.accepted !== false || !late.projection || late.projection.current_instance_id !== repaired.instance_id || late.projection.current_candidate_id !== repaired.candidate_id) deny("late ready event from the failed instance altered the repaired projection");
  const finalProjection = await input.driver.inspect(binding);
  if (!finalProjection || finalProjection.conversation_id !== binding.conversation_id || finalProjection.current_instance_id !== repaired.instance_id || finalProjection.candidate_status !== "ready") deny("final projection does not match the repaired ready instance");
  const stop = await input.driver.stop({ binding, instance_id: repaired.instance_id });
  if (!stop || stop.status !== "stopped" || !Array.isArray(stop.components) || stop.components.length !== 2 || !stop.components.every(item => item.exit_observed === true) || stop.descendants_verified !== false) deny("declared components were not observed exited or cleanup scope was overstated");
  const final = await input.driver.captureFinal({ baseline, binding, failed, repaired, stop, evidenceRoot });
  if (!final || !Array.isArray(final.evidence_locators) || final.evidence_locators.length < 1 || typeof final.summary_evidence !== "string") deny("final scripted evidence bundle is missing");
  for (const locator of final.evidence_locators) assertEvidenceLocator(runRoot, locator);
  const attestedFinal = final.control_release_unchanged === true && final.stable_tree_unchanged === true && typeof final.control_release_evidence === "string" && typeof final.stable_tree_evidence === "string";
  if (trustedEvidence && final.control_release_unchanged === true) recordDrillEvidence(summary, { field: "control_release_unchanged", value: true, locator: final.control_release_evidence, observed_at: new Date().toISOString() }, runRoot);
  if (trustedEvidence && final.stable_tree_unchanged === true) {
    const locator = assertEvidenceLocator(runRoot, final.stable_tree_evidence);
    summary.evidence.push({ field: "stable_tree_unchanged", locator: path.relative(runRoot, locator).replace(/\\/g, "/"), sha256: sha256Hex(readFileSync(locator)), observed_at: new Date().toISOString() });
  }
  if (trustedEvidence && attestedFinal) recordDrillEvidence(summary, { field: "scripted_recovery_verified", value: true, locator: final.summary_evidence, observed_at: new Date().toISOString() }, runRoot);

  summary.descendant_cleanup = "declared_processes_only";
  summary.open_checks = trustedEvidence ? ["h1_electron_preload_live_acceptance", "independent_review", "security_audit", "agent_model_recovery"] : ["scripted_driver_is_not_a_trusted_host", "h1_electron_preload_live_acceptance", "independent_review", "security_audit", "agent_model_recovery"];
  if (trustedEvidence && !attestedFinal) summary.open_checks.push("stable_tree_or_control_release_unchanged_not_proven");
  validateDrillSummary(summary, runRoot);
  const summaryPath = path.join(evidenceRoot, "drill-summary.json"); writeFileSync(summaryPath, JSON.stringify(summary, null, 2) + "\n", { flag: "wx" });
  return { summary, summary_path: summaryPath };
}

function assertEvidenceLocator(runRoot, locator) {
  if (typeof locator !== "string" || !path.isAbsolute(locator)) deny("evidence locator must be absolute");
  const target = path.resolve(locator);
  if (!within(runRoot, target)) deny("evidence locator escaped the marked run root");
  return regularFile(target, "drill evidence locator");
}

function sandboxRootForRun(runRootValue) {
  const target = path.resolve(runRootValue); let current = path.dirname(target);
  while (current !== path.dirname(current)) {
    if (path.basename(current).toLowerCase() === "artifacts" && existsSync(path.join(path.dirname(current), "desktop"))) return path.dirname(current);
    current = path.dirname(current);
  }
  deny("cannot resolve the owning sandbox for this evidence run");
}

export function recordDrillEvidence(summary, observation, runRootValue) {
  if (!summary || typeof summary !== "object" || !Array.isArray(summary.evidence) || !Array.isArray(summary.open_checks)) deny("drill summary is malformed");
  const fields = ["field", "value", "locator", "observed_at"];
  if (!observation || typeof observation !== "object" || Object.keys(observation).length !== fields.length || fields.some(key => !Object.hasOwn(observation, key))) deny("drill observation fields are invalid");
  const allowed = new Set(["main_conversation_preserved", "control_release_unchanged", "candidate_fail_observed", "candidate_repaired_ready", "scripted_recovery_verified", "agent_recovery_verified"]);
  if (!allowed.has(observation.field) || observation.value !== true) deny("only a supported observed true value may be recorded");
  const { runRoot } = validateRunRoot(sandboxRootForRun(runRootValue), path.resolve(runRootValue));
  const locator = assertEvidenceLocator(runRoot, observation.locator);
  if (typeof observation.observed_at !== "string" || !Number.isFinite(Date.parse(observation.observed_at))) deny("observation time is invalid");
  const entry = { field: observation.field, locator: path.relative(runRoot, locator).replace(/\\/g, "/"), sha256: sha256Hex(readFileSync(locator)), observed_at: observation.observed_at };
  summary[observation.field] = true;
  if (observation.field === "scripted_recovery_verified") summary.mode = "scripted_recovery";
  if (observation.field === "agent_recovery_verified") summary.mode = "agent_recovery";
  summary.evidence.push(entry);
  summary.open_checks = summary.open_checks.filter(item => item !== observation.field);
  return entry;
}

export function validateDrillSummary(summary, runRootValue) {
  if (!summary || typeof summary !== "object" || !Array.isArray(summary.evidence) || !Array.isArray(summary.open_checks)) deny("drill summary is malformed");
  const booleanFields = ["installed_stable_upgraded", "main_conversation_preserved", "control_release_unchanged", "candidate_fail_observed", "candidate_repaired_ready", "scripted_recovery_verified", "agent_recovery_verified"];
  for (const field of booleanFields) if (typeof summary[field] !== "boolean") deny("summary boolean field is invalid: " + field);
  const { runRoot } = validateRunRoot(sandboxRootForRun(runRootValue), path.resolve(runRootValue));
  const recorded = new Map();
  for (const entry of summary.evidence) {
    if (!entry || typeof entry !== "object" || typeof entry.field !== "string" || typeof entry.locator !== "string" || !/^[0-9a-f]{64}$/.test(entry.sha256) || typeof entry.observed_at !== "string" || !Number.isFinite(Date.parse(entry.observed_at))) deny("drill evidence entry is malformed");
    const locator = assertEvidenceLocator(runRoot, path.resolve(runRoot, ...entry.locator.split("/")));
    if (sha256Hex(readFileSync(locator)) !== entry.sha256) deny("drill evidence digest changed: " + entry.locator);
    recorded.set(entry.field, (recorded.get(entry.field) ?? 0) + 1);
  }
  for (const field of booleanFields) if (summary[field] && !recorded.has(field)) deny("summary true field lacks evidence: " + field);
  if (summary.agent_recovery_verified && (summary.mode !== "agent_recovery" || typeof summary.provider_id !== "string" || !summary.provider_id || typeof summary.resolved_model !== "string" || !summary.resolved_model)) deny("agent recovery requires actual provider and resolved model identity");
  if (summary.mode === "scripted_recovery" && summary.agent_recovery_verified) deny("scripted recovery cannot claim agent recovery");
  return true;
}

export function recoveryFixtureScopeCompatibility(ownedPaths) {
  const required = ["evolve/tool-catalog/INDEX.md", "evolve/tool-catalog/recovery/main.cjs", "evolve/tool-catalog/recovery/service.cjs", "evolve/tool-catalog/recovery/manifest.json"];
  const valid = Array.isArray(ownedPaths) && ownedPaths.length === required.length && ownedPaths.every(item => typeof item === "string");
  const available = valid ? new Set(ownedPaths) : new Set();
  const compatible = valid && required.every((item, index) => ownedPaths[index] === item);
  return { compatible, required_paths: required, missing_paths: required.filter(item => !available.has(item)) };
}
function createOrValidateCliRunRoot(sandboxRoot, targetValue) {
  const artifactsRoot = realDirectory(path.join(sandboxRoot, "artifacts"), "sandbox artifacts root");
  if (typeof targetValue !== "string" || !path.isAbsolute(targetValue)) deny("CLI run root must be absolute");
  const target = path.resolve(targetValue);
  if (!within(artifactsRoot, target)) deny("CLI run root must be a new child of sandbox/artifacts");
  if (existsSync(target)) {
    const root = realDirectory(target, "CLI run root");
    const marker = regularFile(path.join(root, ".evolution-test-run"), "CLI run marker");
    if (readFileSync(marker, "utf8").replace(/^\uFEFF/, "").trim() !== "evolution-a") deny("CLI run marker is invalid");
    return root;
  }
  const parent = realDirectory(path.dirname(target), "CLI run parent");
  if (!within(artifactsRoot, parent)) deny("CLI run parent escaped sandbox/artifacts");
  mkdirSync(target, { recursive: false });
  writeFileSync(path.join(target, ".evolution-test-run"), "evolution-a\n", { flag: "wx" });
  return realDirectory(target, "new CLI run root");
}

async function interactiveAuthorization(mode, io = {}) {
  const input = io.input ?? process.stdin; const output = io.output ?? process.stdout;
  if (typeof io.confirm === "function") {
    const localConfirmed = await io.confirm("确认仅在当前 sandbox/artifacts 运行 local_managed 夹具？");
    const hostConfirmed = await io.confirm("确认启动本次专用的 H1 Electron Host 与 T4 local_managed 候选进程；它们与当前用户权限相同，且只操作 sandbox/artifacts？");
    const modelConfirmed = mode === "agent" ? await io.confirm("确认 M1 实际 provider/model、输入范围和费用预算？") : false;
    return { mode, localConfirmed: localConfirmed === true, hostConfirmed: hostConfirmed === true, modelConfirmed: modelConfirmed === true };
  }
  if (!input.isTTY || !output.isTTY) deny("interactive confirmations are required; CLI flags cannot self-authorize a drill");
  const readline = await import("node:readline/promises");
  const terminal = readline.createInterface({ input, output });
  const ask = async prompt => (await terminal.question(prompt + " 输入 yes 确认：")).trim().toLowerCase() === "yes";
  try {
    const localConfirmed = await ask("本次只使用 sandbox/artifacts 内 local_managed 夹具，候选与当前用户同权限，非 OS 隔离。");
    const hostConfirmed = await ask("启动 run-root/host/app 独立 Electron H1 Host 和 T4 local_managed 候选进程；候选与当前用户权限相同，仅能操作固定三文件，不触碰稳定工作台。");
    const modelConfirmed = mode === "agent" ? await ask("将显示实际 provider/model、输入夹具范围与预算；批准本次真实模型费用。") : false;
    return { mode, localConfirmed, hostConfirmed, modelConfirmed };
  } finally { terminal.close(); }
}

export function verifyStableBaselineUnchanged(baseline, current) {
  if (!baseline || typeof baseline !== "object" || !Array.isArray(baseline.stable_files) || !Array.isArray(baseline.baseline_listeners) || !current || typeof current !== "object" || !Array.isArray(current.stable_files) || !Array.isArray(current.baseline_listeners)) deny("stable baseline fields are invalid");
  if (JSON.stringify(baseline.stable_files) !== JSON.stringify(current.stable_files)) deny("stable source fingerprint changed during the drill");
  if (JSON.stringify(baseline.baseline_listeners) !== JSON.stringify(current.baseline_listeners)) deny("baseline listener identities changed during the drill");
  return true;
}

export async function main(argv = process.argv.slice(2), options = {}) {
  const sandboxRoot = realDirectory(path.resolve(scriptDir, "../.."), "current sandbox root");
  if (!samePath(realDirectory(process.cwd(), "current working directory"), sandboxRoot)) deny("CLI must run from the current sandbox root");
  if (!Array.isArray(argv) || argv.length !== 4 || argv[0] !== "--mode" || argv[2] !== "--run-root") deny("expected only --mode <scripted|agent> --run-root <marked-run-root>");
  const mode = argv[1]; if (mode !== "scripted" && mode !== "agent") deny("mode must be scripted or agent");
  const authorization = await interactiveAuthorization(mode, options.io);
  validateDrillAuthorization(authorization);
  const runRoot = createOrValidateCliRunRoot(sandboxRoot, path.resolve(argv[3]));
  let summary = createDrillSummary(mode);
  summary.open_checks.push("electron_output_source_reproducibility_not_asserted", "external_review_incomplete", "security_audit_not_run");
  if (mode === "agent") {
    summary.open_checks.push("m1_provider_adapter_not_configured");
  } else {
    let frozenHost = null; let driver = null;
    try {
      const baseline = (options.captureBaseline ?? captureStableBaseline)(sandboxRoot, runRoot, options.baselineOptions ?? {});
      const baselinePath = path.join(runRoot, "t9-stable-baseline-" + randomUUID() + ".json");
      writeFileSync(baselinePath, JSON.stringify(baseline, null, 2) + "\n", { flag: "wx" });
      const pythonExecutable = options.trustedPythonExecutable ?? path.join(path.resolve(sandboxRoot, "../.."), ".venv", "Scripts", "python.exe");
      const electronExecutable = options.electronExecutable ?? path.join(sandboxRoot, "desktop", "node_modules", "electron", "dist", "electron.exe");
      const electronBuild = await (options.buildH1ElectronMainOutputs ?? buildH1ElectronMainOutputs)({ sandboxRoot, runRoot }, options.electronBuildOptions ?? {});
      const fixtureOptions = { ...(options.fixtureOptions ?? {}), electronBuildRoot: electronBuild.output_root };
      const freezer = options.freezeHostFixture ?? freezeHostFixture;
      const frozen = await freezer({ sandboxRoot, runRoot, trustedPythonExecutable: pythonExecutable, electronExecutable, candidateFixtureProfile: "t4-recovery-node-v1" }, fixtureOptions);
      if (!frozen || typeof frozen.app_root !== "string" || typeof frozen.manifest_path !== "string") deny("H1 fixture freezer returned an invalid descriptor");
      const candidateFixture = (options.createCandidateSourceFixture ?? createCandidateSourceFixture)({ sandboxRoot, runRoot });
      const verifier = options.verifyFrozenHostFixture ?? verifyFrozenHostFixture;
      verifier({ sandboxRoot, runRoot, ...frozen }, fixtureOptions);
      const hostManifest = regularFile(frozen.manifest_path, "frozen H1 manifest");
      summary.open_checks.push("h1_fixture_frozen:" + path.relative(runRoot, hostManifest).replace(/\\/g, "/"));
      summary.open_checks.push("stable_baseline:" + path.relative(runRoot, baselinePath).replace(/\\/g, "/"));
      summary.open_checks.push("candidate_fixture:" + path.relative(runRoot, candidateFixture.source_root).replace(/\\/g, "/"));
      const scope = recoveryFixtureScopeCompatibility(["evolve/tool-catalog/INDEX.md", "evolve/tool-catalog/recovery/main.cjs", "evolve/tool-catalog/recovery/service.cjs", "evolve/tool-catalog/recovery/manifest.json"]);
      if (!scope.compatible) deny("T9 fixed candidate profile does not match the H1 owned scope");

      if (options.scriptedDriver) {
        driver = options.scriptedDriver;
      } else {
        frozenHost = await (options.startFrozenHost ?? startFrozenHost)({ sandboxRoot, runRoot, ...frozen }, options.fixtureOptions ?? {});
        driver = (options.createScriptedDriver ?? createT9ScriptedWebSocketDriver)({ sandboxRoot, runRoot, port: frozenHost.port, baseline, baselineOptions: options.baselineOptions ?? {}, io: options.io });
      }
      const drill = await runScriptedRecovery({ sandboxRoot, runRoot, authorization, driver });
      summary = drill.summary;
      summary.open_checks = summary.open_checks.filter(item => !["t9_candidate_driver_not_connected", "live_drill_not_run", "h1_host_not_started_by_this_cli_run"].includes(item));
      summary.open_checks.push("electron_output_source_reproducibility_not_asserted", "external_review_incomplete", "security_audit_not_run");
      summary.open_checks = [...new Set(summary.open_checks)];
    } catch (error) {
      summary.open_checks.push("scripted_driver_failed:" + String(error?.code ?? "invalid_request") + ":" + String(error?.message ?? error).replace(/[\\r\\n]/g, " ").slice(0, 240));
    } finally {
      try { await driver?.close?.(); } catch (error) { summary.open_checks.push("scripted_driver_close_failed:" + String(error?.message ?? error).slice(0, 160)); }
      try { const exit = await frozenHost?.close?.(); if (exit) summary.open_checks.push("h1_exit:" + String(exit.code) + ":" + String(exit.signal), "h1_stdout:" + String(exit.stdout), "h1_stderr:" + String(exit.stderr)); } catch (error) { summary.open_checks.push("h1_host_close_failed:" + String(error?.message ?? error).slice(0, 160)); }
    }
  }
  validateDrillSummary(summary, runRoot);
  const target = path.join(runRoot, "t9-" + mode + "-summary-" + randomUUID() + ".json");
  writeFileSync(target, JSON.stringify(summary, null, 2) + "\n", { flag: "wx" });
  const result = { ...summary, summary_path: target };
  (options.output ?? process.stdout).write(JSON.stringify(result, null, 2) + "\n");
  return result;
}
if (process.argv[1] && samePath(path.resolve(process.argv[1]), fileURLToPath(import.meta.url))) {
  main().then(result => { if (result?.open_checks?.length) process.exitCode = 2; }).catch(error => { process.stderr.write((error?.code ?? "invalid_request") + ": " + String(error?.message ?? error).slice(0, 512) + "\n"); process.exitCode = 2; });
}


export function captureStableBaseline(sandboxRootValue, runRootValue, options = {}) {
  const sandboxRoot = realDirectory(path.resolve(sandboxRootValue), "current sandbox root");
  const { runRoot } = validateRunRoot(sandboxRoot, runRootValue);
  const stableRoot = realDirectory(options.stableRoot ?? path.resolve(sandboxRoot, "../.."), "stable repository root");
  const selected = ["desktop/electron/main.ts", "desktop/electron/preload.ts", "desktop/package.json", "agent-core/server.py", "agent-core/session.py", "agent-core/evolution/client.py"];
  const stableFiles = selected.map(relative => {
    const target = path.join(stableRoot, ...relative.split("/"));
    if (!existsSync(target)) return { path: relative, status: "missing" };
    const file = regularFile(target, "stable baseline source file"); const bytes = readFileSync(file);
    return { path: relative, status: "present", byte_length: bytes.length, sha256: sha256Hex(bytes) };
  });
  const netstat = options.netstatResult ?? spawnSync("netstat", ["-ano", "-p", "tcp"], { encoding: "utf8", windowsHide: true, shell: false, timeout: 10_000, maxBuffer: 4 * 1024 * 1024 });
  const listeners = [];
  if (!netstat.error && netstat.status === 0) {
    for (const line of netstat.stdout.split(/\r?\n/)) {
      const match = /^\s*(TCP)\s+(\S+)\s+(\S+)\s+(LISTENING)\s+(\d+)\s*$/i.exec(line);
      if (!match) continue;
      const local = match[2]; const port = Number(local.slice(local.lastIndexOf(":") + 1));
      if (port === 8765 || port === 8766) listeners.push({ protocol: match[1].toUpperCase(), local, remote: match[3], state: match[4].toUpperCase(), pid: Number(match[5]) });
    }
  }
  return { captured_at: new Date().toISOString(), sandbox_root: sandboxRoot, stable_root: stableRoot, stable_files: stableFiles, baseline_listeners: listeners, listener_probe: !netstat.error && netstat.status === 0 ? "netstat_observed" : "unavailable", stable_changes_by_this_drill: false };
}

function rendererSourceDigest(desktopRoot) {
  const files = ["index.html", ...listFiles(path.join(desktopRoot, "src"), { includeFile: (_rel, name) => !/\.(?:map|tsbuildinfo|d\.ts)$/i.test(name) }).map(item => `src/${item.relative}`)];
  const entries = files.sort((a, b) => a.localeCompare(b, "en")).map(relative => ({ path: relative, sha256: sha256(readFileSync(path.join(desktopRoot, ...relative.split("/")))) }));
  return `sha256:${sha256(Buffer.from(canonical(entries), "utf8"))}`;
}

function rendererOutputFiles(outputRoot) {
  return listFiles(outputRoot, { includeFile: (_rel, name) => !/\.(?:map|tsbuildinfo|d\.ts)$/i.test(name) }).map(item => ({ path: item.relative, byte_length: item.byte_length, sha256: sha256(readFileSync(item.absolute)) }));
}

export async function buildH1RendererOutputs(input) {
  if (!input || typeof input !== "object" || Array.isArray(input) || Object.keys(input).length !== 2 || typeof input.sandboxRoot !== "string" || typeof input.runRoot !== "string") deny("H1 renderer build input fields are invalid");
  const { sandboxRoot, runRoot } = validateRunRoot(input.sandboxRoot, input.runRoot);
  const desktopRoot = realDirectory(path.join(sandboxRoot, "desktop"), "sandbox desktop source root");
  const sourceDigest = rendererSourceDigest(desktopRoot);
  const outputRoot = path.join(runRoot, "t9-renderer-build-" + randomUUID());
  if (existsSync(outputRoot)) deny("T9 renderer build output already exists; refusing overwrite");
  const require = createRequire(import.meta.url);
  const configuredVite = process.env.MY_AGENT_H1_VITE_ENTRY;
  if (configuredVite !== undefined && !path.isAbsolute(configuredVite)) deny("MY_AGENT_H1_VITE_ENTRY must be an absolute read-only tool path");
  const viteEntry = configuredVite ? regularFile(configuredVite, "fixed H1 Vite runtime") : require.resolve("vite", { paths: [desktopRoot] });
  const { build } = await import(pathToFileURL(viteEntry).href);
  const configuredModules = process.env.MY_AGENT_H1_NODE_MODULES;
  let aliases = [];
  if (configuredModules !== undefined) {
    if (!path.isAbsolute(configuredModules)) deny("MY_AGENT_H1_NODE_MODULES must be an absolute read-only dependency directory");
    const moduleRoot = realDirectory(configuredModules, "H1 read-only dependency directory");
    for (const entry of readdirSync(moduleRoot, { withFileTypes: true })) {
      if (entry.name === ".bin" || entry.name.startsWith(".") || ["vite", "rollup", "esbuild"].includes(entry.name)) continue;
      if (entry.name.startsWith("@") && entry.isDirectory()) {
        for (const scoped of readdirSync(path.join(moduleRoot, entry.name), { withFileTypes: true })) {
          if (scoped.isDirectory()) aliases.push({ find: entry.name + "/" + scoped.name, replacement: path.join(moduleRoot, entry.name, scoped.name) });
        }
      } else if (entry.isDirectory()) aliases.push({ find: entry.name, replacement: path.join(moduleRoot, entry.name) });
    }
  }
  if (typeof build !== "function") deny("fixed sandbox Vite build API is unavailable");
  try {
    await build({ configFile: false, root: desktopRoot, logLevel: "silent", resolve: aliases.length ? { alias: aliases } : undefined, build: { outDir: outputRoot, emptyOutDir: false, rollupOptions: { input: path.join(desktopRoot, "index.html") } } });
    const actualSourceDigest = rendererSourceDigest(desktopRoot);
    if (actualSourceDigest !== sourceDigest) deny("renderer source changed during its run-local build");
    const files = rendererOutputFiles(outputRoot);
    if (!files.some(file => file.path === "index.html")) deny("run-local renderer build omitted index.html");
    return { output_root: outputRoot, source_digest: sourceDigest, files };
  } catch (error) {
    throw new Error(`T9 renderer build failed in ${path.relative(runRoot, outputRoot)}: ${String(error?.message ?? error).slice(0, 512)}`, { cause: error });
  }
}
