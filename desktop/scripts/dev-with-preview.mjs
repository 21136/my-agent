/**
 * Dev entry: Vite main (5173 + Electron) and shell preview lane (5174).
 * Set MY_AGENT_SKIP_PREVIEW_DEV=1 to run main only (same as dev:main-only).
 */
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { resolvePreviewLanePaths } from "./sandbox-paths.mjs";
import { assertDependencies, auditDependencies, formatAudit } from "./dependency-audit.mjs";

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const viteBin = path.join(desktopDir, "node_modules", "vite", "bin", "vite.js");
assertDependencies(desktopDir);
const previewLane = resolvePreviewLanePaths();
const skipPreview = process.env.MY_AGENT_SKIP_PREVIEW_DEV === "1";

if (!skipPreview) {
  console.log(`[my-agent] Shell preview 5174 → ${previewLane.label}`);
}

/** @type {import('node:child_process').ChildProcess | null} */
let previewProc = null;

function logPreview(chunk, isErr) {
  const lines = String(chunk).split(/\r?\n/);
  for (const line of lines) {
    if (!line) continue;
    const out = isErr ? process.stderr : process.stdout;
    out.write(`[preview:5174] ${line}\n`);
  }
}

function startPreviewLane() {
  if (skipPreview) return;
  const preflight = auditDependencies(previewLane.previewCwd);
  if (!preflight.ok) {
    console.warn(formatAudit(preflight));
    console.warn("[preview:5174] Disabled because dependencies drifted. The main lane is not changed.");
    return;
  }
  previewProc = spawn(process.execPath, [previewLane.previewViteBin, "--config", previewLane.previewConfig], {
    cwd: previewLane.previewCwd,
    env: process.env,
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  previewProc.stdout?.on("data", (d) => logPreview(d, false));
  previewProc.stderr?.on("data", (d) => logPreview(d, true));
  previewProc.on("exit", (code, signal) => {
    if (skipPreview) return;
    if (signal) return;
    if (code === 0 || code === null) return;
    console.warn(`[preview:5174] dev server exited (code=${code}). Preview is unavailable; this does not validate the experimental version.`);
  });
}

function stopPreviewLane() {
  const proc = previewProc;
  previewProc = null;
  if (!proc || proc.killed || proc.pid == null) return;
  try {
    if (process.platform === "win32") {
      spawn("taskkill", ["/PID", String(proc.pid), "/T", "/F"], { stdio: "ignore" });
    } else {
      proc.kill("SIGTERM");
    }
  } catch {
    try {
      proc.kill("SIGKILL");
    } catch {
      /* ignore */
    }
  }
}

startPreviewLane();

const mainProc = spawn(process.execPath, [viteBin], {
  cwd: desktopDir,
  env: process.env,
  stdio: "inherit",
});

let shuttingDown = false;
function shutdownMain(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  stopPreviewLane();
  if (!mainProc.killed) {
    try {
      mainProc.kill(signal);
    } catch {
      /* ignore */
    }
  }
}

process.on("SIGINT", () => shutdownMain("SIGINT"));
process.on("SIGTERM", () => shutdownMain("SIGTERM"));

mainProc.on("exit", (code, signal) => {
  stopPreviewLane();
  if (signal === "SIGINT" || signal === "SIGTERM") {
    process.exit(0);
    return;
  }
  process.exit(code ?? 0);
});
