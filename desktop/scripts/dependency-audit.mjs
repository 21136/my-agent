/** Read-only npm lock/install preflight. No imports from node_modules, no installs. */
import { existsSync, readFileSync, realpathSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const defaultRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sections = ["dependencies", "devDependencies", "optionalDependencies"];
const isRecord = value => value !== null && typeof value === "object" && !Array.isArray(value);

function compatible(list, value) {
  if (!Array.isArray(list) || list.length === 0) return true;
  if (list.includes(`!${value}`)) return false;
  const positives = list.filter(v => !v.startsWith("!"));
  return positives.length === 0 || positives.includes(value) || positives.includes("any");
}
function within(file, directory) {
  const rel = path.relative(directory, file);
  return rel !== ".." && !rel.startsWith(`..${path.sep}`) && !path.isAbsolute(rel);
}

export function auditDependencies(directory = defaultRoot, options = {}) {
  const root = path.resolve(directory);
  const platform = options.platform ?? process.platform;
  const arch = options.arch ?? process.arch;
  const issues = [];
  const issue = (code, pkg, message) => issues.push({ code, package: pkg, message });
  const result = () => ({ ok: issues.length === 0, root, platform, arch, checked, issues,
    repairHint: `In ${root}, explicitly run npm ci, then node ./node_modules/electron/install.js to install the locked Electron runtime, then npm run deps:check. No repair was performed.` });
  let checked = 0;
  let pkg, lock;
  try {
    pkg = JSON.parse(readFileSync(path.join(root, "package.json"), "utf8"));
    lock = JSON.parse(readFileSync(path.join(root, "package-lock.json"), "utf8"));
  } catch (error) {
    issue("unreadable_manifest_or_lock", "", error.message);
    return result();
  }
  if (!isRecord(pkg)) {
    issue("unreadable_manifest_or_lock", "", "Expected package.json to contain an object.");
    return result();
  }
  if (!isRecord(lock) || !isRecord(lock.packages) || !isRecord(lock.packages[""])
      || ![2, 3].includes(lock.lockfileVersion)) {
    issue("unsupported_lockfile", "", "Expected npm lockfile v2/v3 with root packages metadata.");
    return result();
  }
  for (const section of sections) {
    const declared = Object.entries(pkg[section] ?? {}).sort();
    const locked = Object.entries(lock.packages[""][section] ?? {}).sort();
    if (JSON.stringify(declared) !== JSON.stringify(locked)) {
      issue("manifest_lock_mismatch", section, `package.json ${section} differs from package-lock.json root.`);
    }
  }
  const modules = path.join(root, "node_modules");
  let realModules;
  try { realModules = realpathSync(modules); }
  catch {
    issue("missing_installation", "", "node_modules is missing; stable dependencies are not borrowed.");
    return result();
  }
  if (!within(realModules, realpathSync(root))) {
    issue("external_dependency_root", "", "node_modules resolves outside this desktop root; no implicit shared mutable installation.");
    return result();
  }
  for (const [key, expected] of Object.entries(lock.packages)) {
    if (!key.startsWith("node_modules/")) continue;
    if (!isRecord(expected) || [expected.os, expected.cpu].some(
      value => value !== undefined && (!Array.isArray(value) || value.some(v => typeof v !== "string"))
    )) {
      issue("unsupported_lock_entry", key, "Invalid package or platform metadata in lockfile.");
      continue;
    }
    if (!compatible(expected.os, platform) || !compatible(expected.cpu, arch)) continue;
    const name = key.slice("node_modules/".length);
    const folder = path.resolve(root, key);
    if (!within(folder, modules) || key.includes("\\") || key.split("/").includes("..")) {
      issue("unsafe_lock_path", name, "Lock entry escapes node_modules.");
      continue;
    }
    if (expected.link || typeof expected.version !== "string") {
      issue("unsupported_lock_entry", name, "Linked/workspace packages require an explicit audit policy.");
      continue;
    }
    const manifest = path.join(folder, "package.json");
    if (!existsSync(manifest)) {
      issue("missing_package", name, `Locked package ${name}@${expected.version} is not installed.`);
      continue;
    }
    try {
      if (!within(realpathSync(manifest), realModules)) {
        issue("external_package_link", name, "Package resolves outside this dependency root.");
        continue;
      }
      const installed = JSON.parse(readFileSync(manifest, "utf8"));
      checked += 1;
      if (installed.version !== expected.version) {
        issue("version_mismatch", name, `Locked ${expected.version}; installed ${installed.version ?? "unknown"}.`);
      }
    } catch (error) {
      issue("unreadable_package", name, error.message);
    }
  }
  const electron = lock.packages["node_modules/electron"];
  if (electron?.version) {
    const dist = path.join(modules, "electron", "dist");
    const exe = platform === "win32" ? "electron.exe" : platform === "darwin"
      ? "Electron.app/Contents/MacOS/Electron" : "electron";
    try {
      const versionFile = path.join(dist, "version");
      const executable = path.join(dist, exe);
      if (!within(realpathSync(versionFile), realModules) || !within(realpathSync(executable), realModules)) {
        issue("external_electron_binary", "electron", "Electron runtime resolves outside this dependency root.");
      } else {
        const actual = readFileSync(versionFile, "utf8").trim().replace(/^v/, "");
        if (actual !== electron.version) {
          issue("electron_binary_mismatch", "electron", `Locked ${electron.version}; dist/version reports ${actual}.`);
        }
      }
    } catch (error) {
      issue("missing_electron_binary", "electron", `Electron runtime is incomplete: ${error.message}`);
    }
  }
  return result();
}

export function formatAudit(result) {
  if (result.ok) return `[dependencies] OK: ${result.checked} locked packages checked (${result.root}).`;
  return [`[dependencies] BLOCKED: ${result.root}`,
    ...result.issues.slice(0, 12).map(i => `- ${i.package || i.code}: ${i.message}`),
    ...(result.issues.length > 12 ? [`- ${result.issues.length - 12} additional issues (use --json).`] : []),
    result.repairHint].join("\n");
}

export function assertDependencies(directory = defaultRoot) {
  const result = auditDependencies(directory);
  if (!result.ok) throw new Error(formatAudit(result));
  return result;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  const index = args.indexOf("--root");
  const invalid = args.some((arg, i) => arg !== "--json" && arg !== "--root" && args[i - 1] !== "--root");
  if (invalid || (index >= 0 && (!args[index + 1] || args[index + 1].startsWith("--")))) {
    console.error("Usage: node dependency-audit.mjs [--root DESKTOP_DIR] [--json]");
    process.exitCode = 2;
  } else {
    const result = auditDependencies(index < 0 ? defaultRoot : args[index + 1]);
    console.log(args.includes("--json") ? JSON.stringify(result, null, 2) : formatAudit(result));
    process.exitCode = result.ok ? 0 : 1;
  }
}
