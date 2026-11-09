/**
 * Multi-project schedule workbench contract harness (fixture WS, no live model).
 * Pairs with schedule-workbench-multi-project-fixture.py; runs on Linux without Playwright.
 */
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createInterface } from "node:readline";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(here, "..");
const repo = path.resolve(desktop, "..");
function resolvePython() {
  const candidates = [
    path.join(repo, ".venv", "Scripts", "python.exe"),
    path.join(repo, ".venv", "bin", "python"),
    "python3",
  ];
  for (const candidate of candidates) {
    if (candidate !== "python3" && !existsSync(candidate)) continue;
    const probe = spawnSync(candidate, ["-c", "import websockets"], { encoding: "utf8" });
    if (probe.status === 0) return candidate;
  }
  return "python3";
}
const python = resolvePython();

const ts = createRequire(path.join(desktop, "package.json"))("typescript");
const cache = new Map();
function loadTs(relative) {
  const url = new URL(relative, import.meta.url);
  if (cache.has(url.href)) return cache.get(url.href);
  const exports = {};
  cache.set(url.href, exports);
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

const {
  buildScheduleDispatchWorkers,
  renderScheduleLaunchForm,
  scheduledWorkerCandidates,
} = loadTs("../src/shells/unified/schedule-launch.ts");
const {
  buildCurrentWorkOverviewPanel,
  shouldShowCurrentWorkOverview,
} = loadTs("../src/shells/unified/current-work-overview.ts");

const PROJECTS = ["synthetic-a", "synthetic-b", "synthetic-c"];

const emptyPage = {
  page_size: 6, offset: 0, total_groups: 0, total_visible_groups: 0, total_visible_items: 0,
  total_collapsed_groups: 0, total_collapsed_items: 0, has_more: false, next_cursor: null,
};
const emptyCounts = { visible_groups: 0, visible_items: 0, collapsed_groups: 0, collapsed_items: 0 };

test("local no_records overview: known unread hides work entry; unknown keeps it (fixture contract)", () => {
  const base = {
    type: "work-records.overview.result",
    session_id: "s",
    request_id: "11111111-1111-4111-8111-111111111111",
    state: "no_records",
    groups: [],
    unread_important_results: [],
    visibility_limitations: ["mapped_project_sources_available_for_schedule"],
    visibility_counts: emptyCounts,
    snapshot_id: null,
    page: emptyPage,
  };
  assert.equal(shouldShowCurrentWorkOverview({ ...base, unread_status: "known" }), false);
  assert.equal(shouldShowCurrentWorkOverview({ ...base, unread_status: "unknown" }), true);
  const panelKnown = buildCurrentWorkOverviewPanel({ ...base, unread_status: "known" }, { open: false });
  const panelUnknown = buildCurrentWorkOverviewPanel({ ...base, unread_status: "unknown" }, { open: false, unavailable: true });
  assert.equal(panelKnown.visible, false);
  assert.equal(panelUnknown.visible, true);
  const panelCompanionSchedule = buildCurrentWorkOverviewPanel(
    { ...base, unread_status: "known" },
    { open: false, mappedMultiProjectSchedule: true },
  );
  assert.equal(panelCompanionSchedule.visible, true);
  assert.match(panelCompanionSchedule.label, /多项目调度/);
});

function wsJson(url) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(url);
    const queue = [];
    socket.addEventListener("message", event => {
      const data = JSON.parse(event.data);
      queue.push(data);
      for (const waiter of waiters.splice(0)) waiter();
    });
    const waiters = [];
    socket.addEventListener("error", reject);
    socket.addEventListener("open", () => resolve({
      socket,
      async next(match, timeoutMs = 8000) {
        const deadline = Date.now() + timeoutMs;
        while (Date.now() < deadline) {
          const index = queue.findIndex(match);
          if (index >= 0) return queue.splice(index, 1)[0];
          await new Promise(resolve => {
            waiters.push(resolve);
            setTimeout(resolve, 25);
          });
        }
        throw new Error(`timeout waiting for event; got ${JSON.stringify(queue.slice(-8))}`);
      },
      async drainInitial() {
        await new Promise(resolve => setTimeout(resolve, 50));
        return [...queue];
      },
      send(payload) {
        socket.send(JSON.stringify(payload));
      },
      close() {
        socket.close();
      },
    }));
  });
}

async function startFixture(t) {
  const child = spawn(python, [path.join(here, "schedule-workbench-multi-project-fixture.py")], {
    cwd: desktop,
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
    stdio: ["pipe", "pipe", "pipe"],
  });
  t.after(() => { if (child.exitCode === null) child.kill(); });
  let stderr = "";
  child.stderr.on("data", chunk => { stderr += chunk; });
  const lines = createInterface({ input: child.stdout });
  const ready = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`fixture startup timeout: ${stderr}`)), 15000);
    lines.once("line", line => { clearTimeout(timer); try { resolve(JSON.parse(line)); } catch (e) { reject(e); } });
    child.once("exit", code => { clearTimeout(timer); reject(new Error(`fixture exited ${code}: ${stderr}`)); });
  });
  t.after(async () => {
    child.stdin.end("\n");
    await new Promise(resolve => {
      if (child.exitCode !== null) return resolve();
      child.once("exit", resolve);
      setTimeout(() => { child.kill(); resolve(); }, 3000);
    });
    lines.close();
    assert.equal(stderr.trim(), "", stderr);
  });
  return { child, port: ready.port, stderr };
}

test("multi-project schedule workbench: authorize → preview → dispatch → in_progress cancel (fixture WS)", {
  timeout: 30000,
}, async t => {
  const { port } = await startFixture(t);
  const client = await wsJson(`ws://127.0.0.1:${port}`);
  t.after(() => client.close());

  const initial = await client.drainInitial();
  assert.ok(initial.some(event => event.type === "session.banner"));
  assert.ok(initial.some(event => event.type === "project.list"));
  const list = initial.find(event => event.type === "project.list");
  assert.equal(list.projects.length, PROJECTS.length);

  const sessionId = "schedule-workbench-multi-session";
  const overviewRequestId = crypto.randomUUID();

  client.send({
    type: "work-records.overview.query",
    session_id: sessionId,
    request_id: overviewRequestId,
    project_ids: PROJECTS,
    page_size: 6,
    detail_paging: true,
  });

  const confirm = await client.next(event => event.type === "confirm.request");
  assert.equal(confirm.allow_approve_all, false);
  for (const project of PROJECTS) assert.match(confirm.preview, new RegExp(project));
  client.send({ type: "confirm.response", request_id: confirm.request_id, choice: "y" });

  const confirmDone = await client.next(event => event.type === "confirm.done"
    && event.request_id === confirm.request_id);
  assert.equal(confirmDone.choice, "y");

  const overview = await client.next(event => event.type === "work-records.overview.result"
    && event.request_id === overviewRequestId);
  assert.equal(overview.aggregation_scope, "selected_project_sessions");
  assert.deepEqual([...overview.selected_project_ids].sort(), [...PROJECTS].sort());
  assert.equal(overview.schedule_preview.candidates.length, PROJECTS.length);

  const scheduleMarkup = renderScheduleLaunchForm(overview);
  assert.match(scheduleMarkup, /按预览启动/);
  assert.match(scheduleMarkup, /并行/);
  const candidates = scheduledWorkerCandidates(overview);
  assert.equal(candidates.length, PROJECTS.length);

  const payload = buildScheduleDispatchWorkers(overview, candidates.map((row, index) => ({
    candidateIndex: index,
    taskText: row.task,
    readText: "PROJECT.md",
    writeText: "NOTES.md",
  })));
  assert.equal(payload.workers.length, PROJECTS.length);
  assert.equal(new Set(payload.workers.map(worker => worker.project_id)).size, PROJECTS.length);

  const dispatchRequestId = crypto.randomUUID();
  client.send({
    type: "work-records.schedule.dispatch",
    session_id: sessionId,
    request_id: dispatchRequestId,
    project_ids: payload.projectIds,
    workers: payload.workers,
  });

  const dispatchDone = await client.next(event => event.type === "work-records.schedule.dispatch.done"
    && event.request_id === dispatchRequestId);
  assert.equal(dispatchDone.status, "applied");

  const refreshRequestId = crypto.randomUUID();
  client.send({
    type: "work-records.overview.query",
    session_id: sessionId,
    request_id: refreshRequestId,
    project_ids: PROJECTS,
    page_size: 6,
    detail_paging: true,
  });

  const inProgress = await client.next(event => event.type === "work-records.overview.result"
    && event.request_id === refreshRequestId);
  assert.ok(inProgress.groups.every(group => group.status === "in_progress"));
  const panel = buildCurrentWorkOverviewPanel(inProgress, { open: true });
  assert.match(panel.bodyHtml, /data-action="cancel_execution"/);
  assert.match(panel.bodyHtml, /data-source-project-id="synthetic-a"/);

  const controlRequestId = crypto.randomUUID();
  const group = inProgress.groups[0];
  const item = group.items[0];
  const requirement = item.requirements[0];
  client.send({
    type: "work-record.control",
    session_id: sessionId,
    request_id: controlRequestId,
    item_id: item.item_id,
    requirement_id: requirement.requirement_id,
    project_id: group.id,
    expected_source_revision: group.source_revision,
    action: "cancel_execution",
    expected_status: "in_progress",
    expected_revision: 0,
    event_id: crypto.randomUUID(),
    idempotency_key: controlRequestId,
  });

  const controlDone = await client.next(event => event.type === "work-record.control.done"
    && event.request_id === controlRequestId);
  assert.equal(controlDone.action, "cancel_execution");
  assert.equal(controlDone.status, "applied");
});
