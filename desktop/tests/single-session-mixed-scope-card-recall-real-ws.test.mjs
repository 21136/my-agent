import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir, homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import test from "node:test";

const here = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(here, "..");
const repo = path.resolve(desktop, "..", "..", "..");
const core = path.join(desktop, "..", "agent-core");
const python = path.join(repo, ".venv", "Scripts", "python.exe");
const require = createRequire(path.join(desktop, "package.json"));
const esbuild = require("esbuild");
const playwrightPath = process.env.CODEX_PLAYWRIGHT_PATH
  || path.join(homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const edge = process.env.ISOLATED_TEST_BROWSER || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";

function safeRemoveFixture(root, identity, tempCanonical) {
  const stat = lstatSync(root, { bigint: true });
  if (!stat.isDirectory() || stat.isSymbolicLink() || realpathSync.native(root) !== identity.path
      || stat.dev !== identity.dev || stat.ino !== identity.ino || stat.birthtimeNs !== identity.birthtimeNs) {
    throw new Error("isolated fixture identity changed; preserving it");
  }
  const relative = path.relative(tempCanonical, identity.path);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error("isolated fixture escaped OS temp; preserving it");
  }
  rmSync(identity.path, { recursive: true, force: false });
}

function waitForStartup(child, stderr, timeoutMs = 15000) {
  const lines = createInterface({ input: child.stdout });
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`fixture_start_timeout ${stderr()}`)), timeoutMs);
    lines.once("line", line => {
      clearTimeout(timer);
      try { resolve(JSON.parse(line)); } catch { reject(new Error("fixture_start_protocol_error")); }
    });
    child.once("error", () => { clearTimeout(timer); reject(new Error("fixture_spawn_error")); });
    child.once("exit", () => { clearTimeout(timer); reject(new Error(`fixture_exit_before_ready ${stderr()}`)); });
  });
}

const rendererEntry = `
import { AgentWsClient } from "./src/api/ws.ts";
import { mountUnifiedShell } from "./src/shells/unified/index.ts";
(window).startMixedScopeDesktop = async (port) => {
  window.myAgentDesktop = {
    setBilibiliViewState: async () => true,
    setShellDevViewState: async () => true,
    setEvolutionConversationId: async () => true,
  };
  const client = new AgentWsClient("127.0.0.1", port);
  window.__received = [];
  client.onEvent(event => window.__received.push(event));
  window.__agentWsClient = client;
  window.__disposeUnified = mountUnifiedShell(document.querySelector("#app"), client);
  await client.connect();
};
`;

test("one reloaded session keeps five project requirements and an independent event separate through overview and confirmed card recall", {
  skip: !existsSync(python) || !existsSync(playwrightPath) || !existsSync(edge), timeout: 90000,
}, async (t) => {
  const tempCanonical = realpathSync.native(tmpdir());
  const root = mkdtempSync(path.join(tempCanonical, "single-session-mixed-scope-recall-"));
  const initial = lstatSync(root, { bigint: true });
  const identity = { path: realpathSync.native(root), dev: initial.dev, ino: initial.ino, birthtimeNs: initial.birthtimeNs };
  const profile = path.join(root, "profile");
  const profilePaths = {
    USERPROFILE: path.join(profile, "userprofile"), APPDATA: path.join(profile, "appdata", "Roaming"),
    LOCALAPPDATA: path.join(profile, "appdata", "Local"), TEMP: path.join(profile, "temp"),
    TMP: path.join(profile, "temp"), HOME: path.join(profile, "home"),
  };
  for (const value of Object.values(profilePaths)) mkdirSync(value, { recursive: true });
  const env = {};
  for (const key of ["SYSTEMROOT", "WINDIR", "PATH", "PATHEXT"]) if (typeof process.env[key] === "string") env[key] = process.env[key];
  Object.assign(env, profilePaths, {
    MY_AGENT_ROOT: root, MY_AGENT_SYNTHETIC_CARD_RECALL_SECRET: "synthetic-only-never-real",
    PYTHONDONTWRITEBYTECODE: "1", PYTHONNOUSERSITE: "1", PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8",
  });
  assert.deepEqual(Object.keys(env).filter(key => /(?:API.?KEY|TOKEN|SECRET|CREDENTIAL)/i.test(key) && !key.startsWith("MY_AGENT_SYNTHETIC_")), []);

  const fixturePath = path.join(here, "single-session-mixed-scope-card-recall-fixture.py");
  const spawnFixture = (phase, seedReceipt) => spawn(python,
    ["-I", "-B", fixturePath, root, core, phase, ...(seedReceipt ? [JSON.stringify(seedReceipt)] : [])],
    { cwd: core, env, stdio: ["pipe", "pipe", "pipe"], windowsHide: true, shell: false });
  let seedChild;
  let child;
  let seedStderr = "";
  let stderr = "";
  let browser;
  let context;
  let serverReady = false;
  try {
    // Seed only in the first isolated backend, then fully stop it before a new
    // Python process loads the same session directory without any seed path.
    seedChild = spawnFixture("seed");
    seedChild.stderr.on("data", chunk => { seedStderr += chunk.toString(); });
    const seeded = await waitForStartup(seedChild, () => seedStderr);
    assert.equal(seeded.phase, "seed");
    assert.equal(seeded.session_id, "20261101-10000001");
    seedChild.stdin.end("stop\n");
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`seed backend shutdown timeout ${seedStderr}`)), 5000);
      seedChild.once("exit", code => { clearTimeout(timer); code === 0 ? resolve() : reject(new Error(`seed backend exit ${code}: ${seedStderr}`)); });
    });
    assert.equal(seedChild.exitCode, 0, "seed backend must exit before persisted-session reload");

    child = spawnFixture("serve", seeded);
    child.stderr.on("data", chunk => { stderr += chunk.toString(); });
    const ready = await waitForStartup(child, () => stderr);
    serverReady = true;
    assert.equal(ready.phase, "serve");
    assert.equal(ready.fixture, "reloaded persisted session; actual Agent/WsSessionHandler; controlled MockTransport");
    assert.equal(ready.session_id, seeded.session_id, "reloaded backend must retain the seeded conversation identity");
    assert.ok(ready.port > 0 && ![5173, 5174, 8000, 8765].includes(ready.port));
    assert.equal(ready.session_id, "20261101-10000001");

    const bundle = await esbuild.build({
      stdin: { contents: rendererEntry, resolveDir: desktop, loader: "ts" },
      bundle: true, write: false, platform: "browser", format: "iife", globalName: "UnderTest",
      define: { "import.meta.url": '"http://127.0.0.1/isolated.js"' },
      loader: { ".png": "dataurl", ".jpg": "dataurl", ".svg": "dataurl", ".woff2": "dataurl" },
      plugins: [{ name: "no-css", setup(build) { build.onLoad({ filter: /\.css$/ }, () => ({ contents: "", loader: "js" })); } }],
      logLevel: "silent",
    });
    const { chromium } = require(playwrightPath);
    browser = await chromium.launch({
      headless: true, executablePath: edge,
      args: ["--disable-background-networking", "--disable-features=LocalNetworkAccessChecks,LocalNetworkAccessRestrictions,BlockInsecurePrivateNetworkRequests", "--disable-component-update", "--disable-sync", "--no-first-run",
        "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1"],
    });
    context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 1100, height: 800 } });
    const origin = `http://127.0.0.1:${ready.port}`;
    const blocked = [];
    const errors = [];
    await context.route("**/*", route => {
      const url = route.request().url();
      if (url === `${origin}/`) return route.fulfill({ status: 200, contentType: "text/html", body:
        `<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src ws://127.0.0.1:${ready.port}; img-src data:"><div id="app"></div>` });
      blocked.push(url);
      return route.abort();
    });
    const page = await context.newPage();
    page.on("pageerror", error => errors.push(error.message));
    page.setDefaultTimeout(12000);
    await page.goto(`${origin}/`);
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    await page.evaluate(async port => window.startMixedScopeDesktop(port), ready.port);
    await page.waitForFunction(() => window.__received?.some(event => event.type === "session.banner" && event.session_id === "20261101-10000001"));

    // Use the ordinary explicit current-session overview request in the mounted chat.
    await page.locator("#unified-input").fill("请给我当前工作整体概况");
    await page.locator("#unified-send").click();
    await page.waitForFunction(() => window.__received?.some(event => event.type === "turn.end" && event.finish_reason === "work_record_overview"));
    const overviewText = await page.locator("#unified-chat").innerText();
    assert.match(overviewText, /共 6 组（5 个项目组、1 个独立事项组）/);
    const projectRequirements = [
      ["alpha", "Synthetic alpha requirement"],
      ["beta", "Synthetic beta requirement"],
      ["gamma", "Synthetic gamma requirement"],
      ["delta", "Synthetic delta requirement"],
      ["epsilon", "Synthetic epsilon requirement"],
    ];
    const projectSections = projectRequirements.map(([projectId, requirement]) => {
      const heading = `\n项目 ${projectId}（`;
      const start = overviewText.indexOf(heading);
      assert.notEqual(start, -1, `missing rendered project ownership: ${projectId}`);
      const remainder = overviewText.slice(start + heading.length);
      const nextGroup = remainder.search(/\n(?:项目 .+|独立事项)（/);
      const section = remainder.slice(0, nextGroup < 0 ? undefined : nextGroup);
      assert.ok(section.includes(requirement), `${projectId} must own ${requirement}`);
      for (const [otherProject, otherRequirement] of projectRequirements) {
        if (otherProject !== projectId) assert.ok(!section.includes(otherRequirement), `${projectId} must not own ${otherRequirement}`);
      }
      return section;
    });
    assert.equal(projectSections.length, 5);
    assert.ok(overviewText.includes("Synthetic independent event"));
    assert.ok(overviewText.includes("Synthetic independent event detail"));
    assert.doesNotMatch(overviewText, /FOREIGN-SESSION-PRIVATE/);
    const ask = "请找回之前关于SYNTHETIC-ALPHA-ORBIT的工作卡";
    const sendRecall = async () => {
      const priorCards = await page.locator("[data-work-card-recall]").count();
      await page.locator("#unified-input").fill(ask);
      await page.locator("#unified-send").click();
      await page.waitForFunction(count => document.querySelectorAll("[data-work-card-recall]").length > count, priorCards);
      const card = page.locator("[data-work-card-recall]").last();
      await card.waitFor({ state: "visible" });
      await card.locator("details.unified-confirm-details > summary").click();
      const preview = JSON.parse(await card.locator("pre.unified-confirm-preview").textContent());
      assert.equal(preview.session_id, ready.session_id);
      const candidates = JSON.parse(preview.provider_payload.messages[1].content).candidates;
      assert.deepEqual(new Set(candidates.map(candidate => candidate.id)), new Set(ready.current_card_ids));
      assert.equal(candidates.length, 2);
      assert.equal(candidates.find(candidate => candidate.id === ready.alpha_card_id).title, "Synthetic Alpha Orbit Work Card");
      assert.equal(JSON.parse(candidates.find(candidate => candidate.id === ready.alpha_card_id).excerpt).project_id, "alpha");
      const independent = JSON.parse(candidates.find(candidate => candidate.id === ready.independent_card_id).excerpt);
      assert.equal(independent.scope, "independent");
      assert.equal(Object.hasOwn(independent, "project_id"), false);
      assert.doesNotMatch(JSON.stringify(candidates), /FOREIGN-SESSION-PRIVATE|foreign-session-card-id/);
      return card;
    };

    // Refusal must be explicit and must prevent every model request.
    const rejected = await sendRecall();
    await rejected.locator('[data-confirm="n"]').click();
    await page.waitForFunction(() => window.__received?.some(event => event.type === "confirm.done" && event.choice === "n"));
    await page.waitForFunction(() => window.__received?.some(event => event.type === "turn.end" && event.finish_reason === "work_card_recall"));
    assert.equal(JSON.parse(readFileSync(path.join(root, "provider-report.json"), "utf8")).attempts, 0);

    // A fresh user request and affirmative consent permit only the frozen synthetic lookup.
    const approved = await sendRecall();
    const previousRecallEnds = await page.evaluate(() => window.__received.filter(event => event.type === "turn.end" && event.finish_reason === "work_card_recall").length);
    await approved.locator('[data-confirm="y"]').click();
    await page.waitForFunction(count => window.__received.filter(event => event.type === "turn.end" && event.finish_reason === "work_card_recall").length > count, previousRecallEnds, { timeout: 15000 });
    const answer = await page.locator("#unified-chat").innerText();
    assert.match(answer, /Synthetic Alpha Orbit Work Card/);
    assert.match(answer, /Synthetic alpha requirement/);
    assert.doesNotMatch(answer, /FOREIGN-SESSION-PRIVATE/);
    const report = JSON.parse(readFileSync(path.join(root, "provider-report.json"), "utf8"));
    assert.equal(report.attempts, 1);
    assert.equal(report.validated_calls, 1);
    assert.deepEqual(report.candidate_ids.sort(), [...ready.current_card_ids].sort());
    assert.equal(report.selected_card_id, ready.alpha_card_id);
    assert.equal(report.foreign_session_reads, 0);
    assert.deepEqual(errors, []);
    assert.ok(blocked.every(url => url.startsWith(origin)), `unexpected network attempts: ${blocked.join(", ")}`);
    t.diagnostic(`isolated loopback backend=${ready.port}; actual desktop mount; five project ownership/requirement sections plus independent event passed after reload; explicit refusal made 0 provider calls; approval made 1 controlled synthetic call; foreign-session reads=${report.foreign_session_reads}`);
    await page.evaluate(() => { window.__disposeUnified(); window.__agentWsClient.destroy(); });
  } finally {
    if (context) await context.close();
    if (browser) await browser.close();
    if (child && serverReady && child.exitCode === null) child.stdin.end("\n");
    if (child && child.exitCode === null) {
      await new Promise(resolve => {
        child.once("exit", resolve);
        setTimeout(() => { child.kill(); resolve(); }, 4000);
      });
    }
    if (seedChild && seedChild.exitCode === null) {
      seedChild.stdin.end("stop\n");
      await new Promise(resolve => { seedChild.once("exit", resolve); setTimeout(() => { seedChild.kill(); resolve(); }, 4000); });
    }
    if (existsSync(root)) safeRemoveFixture(root, identity, tempCanonical);
  }
});







