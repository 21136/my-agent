/**
 * Multi-project schedule.dispatch through real ProjectWorkerHost (mock model HTTP).
 * POSIX WebSocket harness; Playwright GUI is Win32 opt-in (see browser-e2e + docs).
 */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(here, "..");
const repo = path.resolve(desktop, "..");

const ts = createRequire(path.join(desktop, "package.json"))("typescript");
const scheduleLaunchCache = new Map();
function loadTs(relative) {
  const url = new URL(relative, import.meta.url);
  if (scheduleLaunchCache.has(url.href)) return scheduleLaunchCache.get(url.href);
  const exports = {};
  scheduleLaunchCache.set(url.href, exports);
  const js = ts.transpileModule(readFileSync(url, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const localRequire = specifier => {
    if (specifier.startsWith(".")) return loadTs(new URL(specifier.endsWith(".ts") ? specifier : `${specifier}.ts`, url));
    return createRequire(import.meta.url)(specifier);
  };
  new Function("exports", "require", js)(exports, localRequire);
  return exports;
}
const { buildScheduleDispatchWorkers, scheduledWorkerCandidates } = loadTs("../src/shells/unified/schedule-launch.ts");

function resolvePython() {
  const candidates = [
    path.join(repo, ".venv", "Scripts", "python.exe"),
    path.join(repo, ".venv", "bin", "python"),
    process.env.SYNTHETIC_PYTHON,
    "python3",
    "python",
  ].filter(Boolean);
  for (const candidate of candidates) {
    if (!["python3", "python"].includes(candidate) && !existsSync(candidate)) continue;
    const probe = spawnSync(candidate, ["-c", "import websockets, httpx, pytest"], { encoding: "utf8" });
    if (probe.status === 0) return candidate;
  }
  return "python3";
}
const python = resolvePython();

test("agent-core: companion schedule.dispatch real host (pytest)", {
  timeout: 120_000,
}, () => {
  const run = spawnSync(python, [
    "-m", "pytest",
    path.join(repo, "agent-core/tests/test_multi_project_schedule_dispatch_real_ws_e2e.py"),
    "-q",
  ], { cwd: repo, encoding: "utf8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
  assert.equal(run.status, 0, `${run.stdout}\n${run.stderr}`);
});

test("desktop: schedule dispatch payload builder aligns with overview candidates", () => {
  const overview = {
    type: "work-records.overview.result",
    state: "visible",
    aggregation_scope: "selected_project_sessions",
    selected_project_ids: ["mp-host-alpha", "mp-host-beta"],
    groups: [{
      kind: "project", id: "mp-host-alpha", visibility: "visible",
      items: [{
        item_id: "item-a", requirements: [{ requirement_id: "req-a", text: "Alpha task." }],
      }],
    }, {
      kind: "project", id: "mp-host-beta", visibility: "visible",
      items: [{
        item_id: "item-b", requirements: [{ requirement_id: "req-b", text: "Beta task." }],
      }],
    }],
    schedule_preview: {
      state: "visible",
      candidates: [
        { project_id: "mp-host-alpha", item_id: "item-a", requirement_id: "req-a", priority: 3,
          task: "Alpha task.", suggested_read_targets: ["PROJECT.md"], suggested_write_targets: ["NOTES.md"] },
        { project_id: "mp-host-beta", item_id: "item-b", requirement_id: "req-b", priority: 3,
          task: "Beta task.", suggested_read_targets: ["PROJECT.md"], suggested_write_targets: ["NOTES.md"] },
      ],
      blocked: [], limitations: [], omissions: [],
    },
  };
  const candidates = scheduledWorkerCandidates(overview);
  assert.equal(candidates.length, 2);
  const payload = buildScheduleDispatchWorkers(overview, candidates.map((row, index) => ({
    candidateIndex: index,
    taskText: row.task,
    readText: "PROJECT.md",
    writeText: "NOTES.md",
  })));
  assert.equal(payload.workers.length, 2);
  assert.equal(new Set(payload.workers.map(w => w.project_id)).size, 2);
});

test("coverage map: synthetic schedule fixtures vs real-host harness", () => {
  const synthetic = readFileSync(path.join(here, "schedule-workbench-multi-project-fixture.py"), "utf8");
  assert.match(synthetic, /Never starts an Agent/);
  const real = readFileSync(path.join(here, "multi-project-schedule-real-host-fixture.py"), "utf8");
  assert.match(real, /real_project_worker_host/);
  assert.match(real, /synthetic_model_transport/);
  const runner = readFileSync(path.join(repo, "docs/MULTI-PROJECT-SCHEDULE-REAL-HOST-E2E.md"), "utf8");
  assert.match(runner, /REAL_MULTI_PROJECT_SCHEDULE_GUI_OPT_IN/);
});
