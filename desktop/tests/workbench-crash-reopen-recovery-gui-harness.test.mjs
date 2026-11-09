/**
 * Crash/reopen GUI contract: late WS + honest recovery overview over synthetic fixture.
 * Pairs with workbench-crash-reopen-recovery-fixture.py (POSIX).
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

const { buildCurrentWorkOverviewPanel, shouldShowCurrentWorkOverview } = loadTs(
  "../src/shells/unified/current-work-overview.ts",
);

function wsJson(url) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(url);
    const queue = [];
    const waiters = [];
    socket.addEventListener("message", event => {
      queue.push(JSON.parse(event.data));
      for (const waiter of waiters.splice(0)) waiter();
    });
    socket.addEventListener("error", reject);
    socket.addEventListener("open", () => resolve({
      socket,
      async next(match, timeoutMs = 12000) {
        const deadline = Date.now() + timeoutMs;
        while (Date.now() < deadline) {
          const index = queue.findIndex(match);
          if (index >= 0) return queue.splice(index, 1)[0];
          await new Promise(resolve => {
            waiters.push(resolve);
            setTimeout(resolve, 25);
          });
        }
        throw new Error(`timeout; tail=${JSON.stringify(queue.slice(-6))}`);
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
  const child = spawn(python, [path.join(here, "workbench-crash-reopen-recovery-fixture.py")], {
    cwd: desktop,
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", WORKBENCH_RECOVERY_LATE_WS_MS: "200" },
    stdio: ["pipe", "pipe", "pipe"],
  });
  t.after(() => { if (child.exitCode === null) child.kill(); });
  let stderr = "";
  child.stderr.on("data", chunk => { stderr += chunk; });
  const lines = createInterface({ input: child.stdout });
  const ready = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`fixture timeout: ${stderr}`)), 15000);
    lines.once("line", line => { clearTimeout(timer); try { resolve(JSON.parse(line)); } catch (e) { reject(e); } });
    child.once("exit", code => { clearTimeout(timer); reject(new Error(`exited ${code}: ${stderr}`)); });
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
  return { port: ready.port };
}

const SESSION = "20261109-crash-reopen";

test("late WS: recovery warn precedes session.banner", { timeout: 30000 }, async t => {
  const { port } = await startFixture(t);
  const client = await wsJson(`ws://127.0.0.1:${port}`);
  t.after(() => client.close());
  const notice = await client.next(e => e.type === "notice" && e.level === "warn");
  assert.match(notice.text, /未自动续跑/);
  const banner = await client.next(e => e.type === "session.banner");
  assert.equal(banner.session_id, SESSION);
});

test("recovery overview: unknown stale + cancel on live project worker", { timeout: 30000 }, async t => {
  const { port } = await startFixture(t);
  const client = await wsJson(`ws://127.0.0.1:${port}`);
  t.after(() => client.close());
  await client.next(e => e.type === "session.banner");
  const requestId = crypto.randomUUID();
  client.send({
    type: "work-records.overview.query",
    session_id: SESSION,
    request_id: requestId,
    page_size: 6,
    detail_paging: true,
  });
  const overview = await client.next(e => e.type === "work-records.overview.result" && e.request_id === requestId);
  assert.equal(shouldShowCurrentWorkOverview(overview), true);
  const panel = buildCurrentWorkOverviewPanel(overview, { open: true });
  assert.match(panel.bodyHtml, /结果待确认/);
  assert.match(panel.bodyHtml, /data-action="cancel_execution" data-expected-status="in_progress"/);

  const controlId = crypto.randomUUID();
  client.send({
    type: "work-record.control",
    session_id: SESSION,
    request_id: controlId,
    item_id: "worker",
    requirement_id: "w",
    project_id: "project-alpha",
    action: "cancel_execution",
    expected_status: "in_progress",
    expected_revision: 0,
    event_id: crypto.randomUUID(),
    idempotency_key: controlId,
  });
  const done = await client.next(e => e.type === "work-record.control.done" && e.request_id === controlId);
  assert.equal(done.status, "applied");
});

test("reconnect: second socket still gets recovery warn (tray/cold reopen simulation)", { timeout: 30000 }, async t => {
  const { port } = await startFixture(t);
  const first = await wsJson(`ws://127.0.0.1:${port}`);
  await first.next(e => e.type === "session.banner");
  first.close();
  const second = await wsJson(`ws://127.0.0.1:${port}`);
  t.after(() => second.close());
  const notice = await second.next(e => e.type === "notice" && e.level === "warn");
  assert.match(notice.text, /异常退出/);
  await second.next(e => e.type === "session.banner");
});

test("claim recovery overview exposes inspect_unknown_claim without cancel", { timeout: 30000 }, async t => {
  const { port } = await startFixture(t);
  const client = await wsJson(`ws://127.0.0.1:${port}`);
  t.after(() => client.close());
  await client.next(e => e.type === "session.banner");
  const requestId = crypto.randomUUID();
  client.send({
    type: "work-records.overview.query",
    session_id: SESSION,
    request_id: requestId,
    page_size: 6,
    detail_paging: true,
    claim_recovery_probe: true,
  });
  const overview = await client.next(e => e.type === "work-records.overview.result" && e.request_id === requestId);
  const panel = buildCurrentWorkOverviewPanel(overview, { open: true });
  assert.match(panel.bodyHtml, /data-action="inspect_unknown_claim"/);
  assert.doesNotMatch(panel.bodyHtml, /data-action="cancel_execution"[^>]*claim-req/);
});
