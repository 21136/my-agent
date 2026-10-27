import { createHash, randomUUID } from "node:crypto";
import { copyFileSync, existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, realpathSync, renameSync, writeFileSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const desktopRoot = path.resolve(scriptDir, "..");
const sandboxRoot = path.resolve(desktopRoot, "..");
const sourceRoot = path.join(desktopRoot, "electron/evolution");
const artifactsRoot = path.join(sandboxRoot, "artifacts");
const outputRoot = path.resolve(desktopRoot, "../artifacts/evolution-control-build");
const markerName = ".evolution-control-build.json";
const markerOwner = "my-agent-evolution-control-build-v1";

function fail(message) {
  console.error(JSON.stringify({ ok: false, code: "build_scope_invalid", message }));
  process.exitCode = 2;
}
function sha256(bytes) { return createHash("sha256").update(bytes).digest("hex"); }
function within(parent, child) {
  const rel = path.relative(parent, child);
  return rel !== "" && rel !== ".." && !rel.startsWith(`..${path.sep}`) && !path.isAbsolute(rel);
}
function safeRelative(rel) {
  return typeof rel === "string" && !path.isAbsolute(rel) && !rel.includes("\\") && !rel.split("/").some((part) => !part || part === "." || part === "..");
}
function assertRegularFile(file) {
  const info = lstatSync(file);
  if (!info.isFile() || info.isSymbolicLink()) throw new Error(`expected regular file: ${file}`);
}
function walk(root) {
  const files = [];
  const directories = [];
  const visit = (current, prefix = "") => {
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
      const full = path.join(current, entry.name);
      const info = lstatSync(full);
      if (info.isSymbolicLink()) throw new Error(`symlink is not allowed in generated tree: ${rel}`);
      if (info.isDirectory()) { directories.push(rel); visit(full, rel); }
      else if (info.isFile()) files.push(rel);
      else throw new Error(`non-regular output entry: ${rel}`);
    }
  };
  visit(root);
  return { files: files.sort(), directories: directories.sort() };
}

const runInput = process.env.EVOLUTION_TEST_ROOT;
if (!runInput) { fail("EVOLUTION_TEST_ROOT must point to a marked sandbox/artifacts run directory"); }
else {
  try {
    const sandboxReal = realpathSync.native(sandboxRoot);
    const artifactsReal = realpathSync.native(artifactsRoot);
    if (!within(sandboxReal, artifactsReal)) throw new Error("sandbox/artifacts resolves outside the sandbox");
    const runRoot = realpathSync.native(path.resolve(runInput));
    if (!within(artifactsReal, runRoot)) throw new Error("EVOLUTION_TEST_ROOT must be inside sandbox/artifacts");
    const runMarker = path.join(runRoot, ".evolution-test-run");
    assertRegularFile(runMarker);
    if (readFileSync(runMarker, "utf8").replace(/^\uFEFF/, "").trim() !== "evolution-a") throw new Error("run marker mismatch");
    const attemptId = randomUUID();
    const stageRoot = path.join(runRoot, `control-build-stage-${attemptId}`);
    if (existsSync(stageRoot)) throw new Error(`refusing to overwrite existing build stage: ${stageRoot}`);
    const sourceEntries = walk(sourceRoot);
    const sourceFiles = sourceEntries.files.filter((rel) => rel.endsWith(".ts")).map((rel) => `electron/evolution/${rel}`).sort();
    if (sourceFiles.length === 0 || sourceFiles.length > 512) throw new Error("control source file count is outside the supported bound");
    for (const rel of [...sourceFiles.map((file) => path.join(desktopRoot, file)), path.join(desktopRoot, "tsconfig.evolution.json")]) assertRegularFile(rel);
    const tsc = path.join(desktopRoot, "node_modules/typescript/lib/tsc.js");
    assertRegularFile(tsc);
    mkdirSync(stageRoot, { recursive: false });
    const build = spawnSync(process.execPath, [tsc, "--project", path.join(desktopRoot, "tsconfig.evolution.json"), "--outDir", stageRoot], {
      cwd: desktopRoot, stdio: "inherit", windowsHide: true, shell: false,
    });
    if (build.error) throw build.error;
    if (build.status !== 0) throw new Error(`local tsc exited with status ${build.status}`);

    writeFileSync(path.join(stageRoot, "package.json"), '{"type":"commonjs"}\n', { flag: "wx" });
    const stagedTree = walk(stageRoot);
    const stagedFiles = stagedTree.files.map((rel) => {
      if (!safeRelative(rel)) throw new Error(`unsafe generated file path: ${rel}`);
      const bytes = readFileSync(path.join(stageRoot, ...rel.split("/")));
      return { path: rel, sha256: sha256(bytes), byte_length: bytes.length };
    });
    const sourceManifest = [...sourceFiles, "tsconfig.evolution.json"].map((rel) => ({ path: rel, sha256: sha256(readFileSync(path.join(desktopRoot, ...rel.split("/")))) }));
    const markerPath = path.join(outputRoot, markerName);
    let previous = null;
    if (existsSync(outputRoot)) {
      const outputInfo = lstatSync(outputRoot);
      if (!outputInfo.isDirectory() || outputInfo.isSymbolicLink() || realpathSync.native(outputRoot) !== outputRoot) throw new Error("refusing unsafe output directory");
      if (!existsSync(markerPath)) throw new Error("existing output is unowned; refusing overwrite");
      previous = JSON.parse(readFileSync(markerPath, "utf8"));
      if (previous.owner !== markerOwner || previous.format !== 1 || !Array.isArray(previous.files)) throw new Error("existing build marker is not recognized");
      const managedNames = new Set([markerName]);
      for (const old of previous.files) {
        if (!safeRelative(old.path) || !/^[0-9a-f]{64}$/.test(old.sha256)) throw new Error("invalid prior build manifest entry");
        if (managedNames.has(old.path)) throw new Error("duplicate prior build manifest path");
        managedNames.add(old.path);
        const oldFile = path.join(outputRoot, ...old.path.split("/"));
        assertRegularFile(oldFile);
        if (sha256(readFileSync(oldFile)) !== old.sha256) throw new Error(`prior generated output changed: ${old.path}`);
      }
      const current = walk(outputRoot);
      const expectedDirs = [...new Set([...managedNames].flatMap((rel) => {
        const parts = rel.split("/"); const dirs = [];
        while (parts.length > 1) { parts.pop(); dirs.push(parts.join("/")); }
        return dirs;
      }))].sort();
      if (current.files.join("\n") !== [...managedNames].sort().join("\n") || current.directories.join("\n") !== expectedDirs.join("\n")) throw new Error("output directory contains unowned files or directories");
      const priorSet = new Set(previous.files.map((item) => item.path));
      if (stagedFiles.some((item) => !priorSet.has(item.path))) {
        // New generated modules are allowed; all previous modules must still be emitted.
      }
      if (previous.files.some((item) => !stagedFiles.some((next) => next.path === item.path))) throw new Error("a previously managed module disappeared; refusing implicit cleanup");
    }

    mkdirSync(outputRoot, { recursive: true });
    for (const item of stagedFiles) {
      const destination = path.join(outputRoot, ...item.path.split("/"));
      const parent = path.dirname(destination);
      mkdirSync(parent, { recursive: true });
      const temp = path.join(parent, `.${path.basename(item.path)}.${randomUUID()}.tmp`);
      copyFileSync(path.join(stageRoot, ...item.path.split("/")), temp);
      renameSync(temp, destination);
    }
    const manifest = { owner: markerOwner, format: 1, source_files: sourceManifest, files: stagedFiles };
    const markerTemp = path.join(outputRoot, `.${markerName}.${randomUUID()}.tmp`);
    writeFileSync(markerTemp, `${JSON.stringify(manifest, null, 2)}\n`, { flag: "wx" });
    renameSync(markerTemp, markerPath);
    const result = { ok: true, output_root: outputRoot, run_root: runRoot, files: stagedFiles, source_files: sourceManifest };
    writeFileSync(path.join(runRoot, `t1-build-result-${attemptId}.json`), `${JSON.stringify(result, null, 2)}\n`, { flag: "wx" });
    console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    fail(error instanceof Error ? error.message : String(error));
  }
}
