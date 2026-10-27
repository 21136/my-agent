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

function deny(message) { const error = new Error(`permission_denied: ${message}`); error.code = "permission_denied"; throw error; }
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

function defaultFreezeControlRelease(sandboxRoot, runRoot, pythonExecutable) {
  const require = createRequire(import.meta.url); const modulePath = path.join(sandboxRoot, "artifacts", "evolution-control-build", "control-release.js");
  regularFile(modulePath, "compiled control-release module"); const api = require(modulePath);
  if (typeof api.freezeControlRelease !== "function") deny("fixed control release builder is unavailable");
  return api.freezeControlRelease({ buildRoot: path.join(sandboxRoot, "artifacts", "evolution-control-build"), workerRoot: path.join(sandboxRoot, "agent-core", "evolution"), outputRoot: path.join(runRoot, "control-releases"), nodeExecutable: process.execPath, pythonExecutable });
}

export async function buildH1ElectronMainOutputs(input, options = {}) {
  if (!input || typeof input !== "object" || Array.isArray(input) || Object.keys(input).length !== 2 || typeof input.sandboxRoot !== "string" || typeof input.runRoot !== "string") deny("H1 Electron build input fields are invalid");
  const { sandboxRoot, runRoot } = validateRunRoot(input.sandboxRoot, input.runRoot);
  const desktopRoot = realDirectory(path.join(sandboxRoot, "desktop"), "sandbox desktop source root");
  const entryPoints = { main: regularFile(path.join(desktopRoot, "electron", "main.ts"), "current H1 Electron main source"), preload: regularFile(path.join(desktopRoot, "electron", "preload.ts"), "current H1 Electron preload source") };
  const esbuildEntry = regularFile(path.join(desktopRoot, "node_modules", "esbuild", "lib", "main.js"), "fixed sandbox esbuild runtime");
  const outputRoot = path.join(runRoot, "t9-electron-build-" + randomUUID());
  if (existsSync(outputRoot)) deny("T9 Electron build output already exists; refusing overwrite");
  mkdirSync(outputRoot, { recursive: false });
  const require = createRequire(import.meta.url); const esbuild = require(esbuildEntry);
  if (!esbuild || typeof esbuild.build !== "function") deny("fixed sandbox esbuild API is unavailable");
  try {
    const build = options.build ?? esbuild.build.bind(esbuild);
    await build({ entryPoints, outdir: outputRoot, bundle: true, platform: "node", format: "cjs", target: "node20", external: ["electron", "node:*"], define: { "import.meta.url": "__h1_import_meta_url" }, banner: { js: "const __h1_import_meta_url = require('node:url').pathToFileURL(__filename).href;" }, sourcemap: false, logLevel: "silent", metafile: true });
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
  const expectedInputFields = recoveryFixture ? [...inputFields, "candidateFixtureProfile"] : inputFields;
  if (!input || typeof input !== "object" || Array.isArray(input) || Object.keys(input).length !== expectedInputFields.length || expectedInputFields.some(key => !Object.hasOwn(input, key))) deny("H1 fixture input fields are invalid");
  if (recoveryFixture && input.candidateFixtureProfile !== "t4-recovery-node-v1") deny("H1 candidate fixture profile is unsupported");
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
    mkdirSync(path.join(appRoot, "data", "sessions"), { recursive: true }); mkdirSync(profileRoot, { recursive: true });
    const manifestFiles = [];
    const electronBuildRoot = realDirectory(options.electronBuildRoot ?? path.join(sandboxRoot, "desktop", "dist-electron"), "sealed Electron build output root");
    if (options.electronBuildRoot && !within(runRoot, electronBuildRoot)) deny("T9 Electron build output must stay inside the marked run root");
    const rendererSource = path.join(sandboxRoot, "desktop", "dist");
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

    const excludedDirectories = new Set([".git", ".venv", "__pycache__", ".pytest_cache", "tests", "test", "data", "profiles", "profile", "node_modules", "dist", "build"]);
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
    const profile = { profile_root: path.join(runRoot, "host", "profile"), control_release_root: release.root, trusted_python_executable: pythonExecutable, ws_port: 0, renderer_port: 0, fixture_scope_id: randomUUID(), ...(recoveryFixture ? { candidate_fixture_profile: "t4-recovery-node-v1" } : {}) };
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
    const manifest = { format: 1, scope: "evolution-slice-a-h1-test-host", app_root: "app", renderer_root: "app/desktop/dist", profile_root: "profile", files: manifestFiles, mutable_candidate_sources: [], source_inputs: sourceInputs, profile_sha256: sha256(profileBytes), control_release_id: release.release_id, runtimes: { electron: electronRuntime, python: pythonRuntime }, python_environment_recheck: "required_at_host_start_and_shutdown", build_provenance: "run-local-esbuild-main-preload; sealed-output-hashes-recorded; source-output-reproducibility-not-asserted" };
    const manifestBytes = Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`, "utf8"); writeFileSync(path.join(stageRoot, "manifest.json"), manifestBytes, { flag: "wx" });
    renameSync(stageRoot, hostRoot);
    return { app_root: path.join(hostRoot, "app"), profile_root: path.join(hostRoot, "profile"), renderer_root: path.join(hostRoot, "app", "desktop", "dist"), manifest_path: path.join(hostRoot, "manifest.json") };
  } catch (error) {
    const absoluteStage = path.resolve(stageRoot); if (within(runRoot, absoluteStage) && path.basename(absoluteStage).startsWith(".host-stage-")) rmSync(absoluteStage, { recursive: true, force: true });
    throw error;
  }
}

function sha256Hex(bytes) { return createHash("sha256").update(bytes).digest("hex"); }

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
  if (!profile || Object.keys(profile).length !== (recoveryFixture ? 7 : 6) || recoveryFixture && profile.candidate_fixture_profile !== "t4-recovery-node-v1" || profile.ws_port !== 0 || profile.renderer_port !== 0 || !UUID_RE.test(String(profile.fixture_scope_id)) || !samePath(profile.profile_root, profileRoot)) deny("frozen H1 profile violates the dynamic-port fixture contract");
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
  if (actual.join("\n") !== expectedPaths.join("\n")) deny("frozen H1 app contains missing or unmanifested files");
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
  const spawnProcess = options.spawnProcess ?? (await import("node:child_process")).spawn;
  const logRoot = path.join(verified.run_root, "host", "logs");
  if (existsSync(logRoot)) deny("H1 host log root already exists; refusing overwrite");
  mkdirSync(logRoot, { recursive: false });
  const stdoutPath = path.join(logRoot, "electron.stdout.log"); const stderrPath = path.join(logRoot, "electron.stderr.log");
  const env = {};
  for (const key of ["SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA"]) if (process.env[key]) env[key] = process.env[key];
  env.PYTHONDONTWRITEBYTECODE = "1";
  const child = spawnProcess(verified.electron.executable, [path.join(verified.app_root, "desktop")], { cwd: path.join(verified.app_root, "desktop"), env, stdio: ["ignore", "pipe", "pipe"], windowsHide: false, shell: false });
  if (!child || typeof child.once !== "function") deny("H1 Electron process handle is invalid");
  const stdout = []; const stderr = []; let stdoutBytes = 0; let stderrBytes = 0; const maxLogBytes = 5 * 1024 * 1024;
  const collect = (target, stream, label) => stream?.on("data", chunk => {
    const bytes = Buffer.from(chunk); if (target === stdout) stdoutBytes += bytes.length; else stderrBytes += bytes.length;
    if (stdoutBytes + stderrBytes > maxLogBytes) { try { child.kill(); } catch { /* retain the over-limit evidence state */ } return; }
    target.push(bytes);
  });
  collect(stdout, child.stdout, "stdout"); collect(stderr, child.stderr, "stderr");
  let startError = null;
  child.once("error", error => { startError = error; });
  const exitCode = await new Promise(resolve => child.once("close", (code, signal) => resolve({ code, signal })));
  writeFileSync(stdoutPath, Buffer.concat(stdout), { flag: "wx" }); writeFileSync(stderrPath, Buffer.concat(stderr), { flag: "wx" });
  const after = (options.capturePythonEnvironment ?? ((executable, packages) => capturePythonEnvironment(executable, packages, { cwd: verified.sandbox_root })))(verified.profile.trusted_python_executable, verified.manifest.runtimes.python.environment.packages.map(item => item.name));
  assertPythonEnvironmentUnchanged(verified.python_environment, after);
  const exitRecord = { ...exitCode, ...(startError ? { start_error: String(startError.message ?? startError).slice(0, 512) } : {}), stdout: path.relative(verified.run_root, stdoutPath).replace(/\\/g, "/"), stderr: path.relative(verified.run_root, stderrPath).replace(/\\/g, "/"), python_environment_digest: after.digest };
  writeFileSync(path.join(logRoot, "electron.exit.json"), `${JSON.stringify(exitRecord, null, 2)}\n`, { flag: "wx" });
  return exitRecord;
}

export async function startFrozenHost(input, options = {}) {
  const verified = verifyFrozenHostFixture(input, options);
  if (verified.profile.candidate_fixture_profile !== "t4-recovery-node-v1") deny("T9 launcher requires the fixed T4 recovery profile");
  const spawnProcess = options.spawnProcess ?? (await import("node:child_process")).spawn;
  const logRoot = path.join(verified.run_root, "host", "logs");
  if (existsSync(logRoot)) deny("H1 host log root already exists; refusing overwrite");
  mkdirSync(logRoot, { recursive: false });
  const stdoutPath = path.join(logRoot, "electron.stdout.log"); const stderrPath = path.join(logRoot, "electron.stderr.log");
  const env = {};
  for (const key of ["SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA"]) if (process.env[key]) env[key] = process.env[key];
  env.PYTHONDONTWRITEBYTECODE = "1";
  const token = randomBytes(32).toString("hex");
  const child = spawnProcess(verified.electron.executable, [path.join(verified.app_root, "desktop")], { cwd: path.join(verified.app_root, "desktop"), env, stdio: ["pipe", "pipe", "pipe"], windowsHide: false, shell: false });
  if (!child || typeof child.once !== "function" || !child.stdin || !child.stdout || !child.stderr) deny("H1 Electron process handle is invalid");
  const stdout = []; const stderr = []; let total = 0; let stdoutBuffer = ""; let startError = null; let closed = false; let closeRecord = null;
  let closeResolve; const closedPromise = new Promise(resolve => { closeResolve = resolve; });
  const append = (target, bytes) => { total += bytes.length; if (total > 5 * 1024 * 1024) { try { child.kill(); } catch { /* retain over-limit evidence */ } return false; } target.push(bytes); return true; };
  let readyResolve; let readyReject;
  const readyPromise = new Promise((resolve, reject) => { readyResolve = resolve; readyReject = reject; });
  child.stdout.on("data", chunk => {
    const bytes = Buffer.from(chunk); if (!append(stdout, bytes)) return;
    stdoutBuffer += bytes.toString("utf8");
    for (;;) {
      const newline = stdoutBuffer.indexOf("\n"); if (newline < 0) break;
      const line = stdoutBuffer.slice(0, newline).replace(/\r$/u, ""); stdoutBuffer = stdoutBuffer.slice(newline + 1);
      try { const message = JSON.parse(line); if (message?.type === "evolution-test-host-ready" && message.host === "127.0.0.1" && Number.isInteger(message.port) && message.port > 0 && message.port <= 65_535) readyResolve(message); } catch { /* ordinary Electron output is not a control message */ }
    }
  });
  child.stderr.on("data", chunk => { append(stderr, Buffer.from(chunk)); });
  child.once("error", error => { startError = error; readyReject(error); });
  child.once("close", (code, signal) => { closed = true; closeRecord = { code, signal }; readyReject(new Error("frozen H1 Host exited before signalling readiness")); closeResolve(closeRecord); });
  child.stdin.write(`${JSON.stringify({ type: "t9.control.bootstrap", token })}\n`);
  const timer = setTimeout(() => readyReject(new Error("frozen H1 Host ready marker timed out")), options.readyTimeoutMs ?? 180_000);
  timer.unref?.();
  let ready;
  try { ready = await readyPromise; }
  catch (error) {
    clearTimeout(timer);
    if (!closed) { try { child.stdin.end(`${JSON.stringify({ type: "t9.control.shutdown", token })}\n`); } catch { /* preserve startup failure */ } }
    const exit = await Promise.race([closedPromise, new Promise(resolve => setTimeout(() => resolve(null), 15_000))]);
    if (!exit && !closed) { try { child.kill(); } catch { /* preserve startup failure */ } await closedPromise; }
    writeFileSync(stdoutPath, Buffer.concat(stdout), { flag: "wx" }); writeFileSync(stderrPath, Buffer.concat(stderr), { flag: "wx" });
    throw error;
  } finally { clearTimeout(timer); }
  let finalized = false;
  const finalize = async () => {
    if (finalized) return closeRecord;
    finalized = true;
    const exit = await closedPromise;
    writeFileSync(stdoutPath, Buffer.concat(stdout), { flag: "wx" }); writeFileSync(stderrPath, Buffer.concat(stderr), { flag: "wx" });
    let after = null; let environmentError = null; let pythonEnvironmentUnchanged = false;
    try {
      after = (options.capturePythonEnvironment ?? ((executable, packages) => capturePythonEnvironment(executable, packages, { cwd: verified.sandbox_root })))(verified.profile.trusted_python_executable, verified.manifest.runtimes.python.environment.packages.map(item => item.name));
      assertPythonEnvironmentUnchanged(verified.python_environment, after); pythonEnvironmentUnchanged = true;
    } catch (error) { environmentError = String(error?.message ?? error).slice(0, 512); }
    const record = { ...exit, ...(startError ? { start_error: String(startError.message ?? startError).slice(0, 512) } : {}), stdout: path.relative(verified.run_root, stdoutPath).replace(/\\/g, "/"), stderr: path.relative(verified.run_root, stderrPath).replace(/\\/g, "/"), python_environment_digest: after?.digest ?? null, python_environment_unchanged: pythonEnvironmentUnchanged, ...(environmentError ? { python_environment_error: environmentError } : {}) };
    writeFileSync(path.join(logRoot, "electron.exit.json"), `${JSON.stringify(record, null, 2)}\n`, { flag: "wx" });
    if (!pythonEnvironmentUnchanged) throw new Error(environmentError ?? "trusted Python environment changed during the drill");
    return record;
  };
  return {
    child,
    port: ready.port,
    async close() {
      if (!closed) child.stdin.end(`${JSON.stringify({ type: "t9.control.shutdown", token })}\n`);
      const exit = await Promise.race([closedPromise, new Promise(resolve => setTimeout(() => resolve(null), options.shutdownTimeoutMs ?? 20_000))]);
      if (!exit && !closed) { try { child.kill(); } catch { /* retain shutdown failure evidence */ } await closedPromise; }
      return await finalize();
    },
  };
}
export function createT9ScriptedWebSocketDriver(input) {
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

