import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { SelfEvolutionProgress, renderSelfEvolutionProgress } from "../src/shells/unified/self-evolution-progress.ts";

const shell = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
const ws = readFileSync(new URL("../src/api/ws.ts", import.meta.url), "utf8");
const identity = { session_id: "session-a", task_id: "task-a", attempt_id: "attempt-a", run_id: "run-a", turn_epoch: 2 };
const snapshot = { ...identity, phase: "observing", status: "active", snapshot_revision: 1, sequence: 0, findings: [], verification: [], preview_handle: null };
const snapshotEvent = (value = snapshot, extra = {}) => ({ type: "self_evolution.snapshot", ...value, ...extra });
const updateEvent = (overrides = {}) => ({ type: "self_evolution.update", ...identity, sequence: 1, phase: "observing", kind: "phase_changed", fact_codes: ["observation_recorded"], snapshot_revision: 2, evidence_refs: ["evidence-1"], ...overrides });

test("WebSocket exposes the agreed snapshot and update as server events, without client authorization fields", () => {
  assert.ok(ws.includes('SelfEvolutionSnapshotEvent = SelfEvolutionSnapshot & { type: "self_evolution.snapshot"; request_id: string; history_status: "not_restored" }'));
  assert.ok(ws.includes("SelfEvolutionUpdateEvent = SelfEvolutionEvent"));
  assert.ok(ws.includes("| SelfEvolutionSnapshotEvent"));
  assert.ok(ws.includes("| SelfEvolutionUpdateEvent"));
  assert.ok(!/self_evolution[^\n]{0,180}(?:client_authorized|authorized\s*:\s*true)/i.test(ws));
});

test("unified shell mounts inline status/detail and only restores snapshots from the current host event session", () => {
  assert.ok(shell.includes("SelfEvolutionProgress"));
  assert.ok(shell.includes("renderSelfEvolutionReview"));
  assert.ok(shell.includes('case "self_evolution.snapshot"'));
  assert.ok(shell.includes('case "self_evolution.update"'));
  assert.ok(/event\.session_id\s*!==\s*chat\.model\.sessionId/.test(shell));
  assert.ok(/snapshotEvent\.session_id\s*!==\s*chat\.model\.sessionId/.test(shell));
  assert.ok(shell.includes("chatEl.append(selfEvolutionEl)"));
  assert.ok(/function renderChat\(\): void \{[\s\S]{0,260}selfEvolutionEl\.remove\(\);[\s\S]{0,220}chatEl\.append\(selfEvolutionEl\)/.test(shell));
  assert.ok(!shell.includes('insertAdjacentElement("afterend", selfEvolutionEl)'));
  assert.ok(shell.includes("self-evolution-shell__details"));
  assert.ok(shell.includes("selfEvolutionEl.remove();"));
  assert.ok(shell.includes("selfEvolutionEl.parentElement !== chatEl"));
  assert.ok(shell.includes("data-self-evolution-detail-close"));
  assert.ok(shell.includes("clearSelfEvolutionProgress()"));
  assert.ok(/function clearChatForSwitch\(\): void \{\s*clearSelfEvolutionProgress\(\)/.test(shell));
  assert.ok(!/selfEvolutionProgress[\s\S]{0,700}chat\.model\.(?:turnActive|executionRunId|executionState)/.test(shell));
});

test("synthetic host events produce an escaped same-session status and reject stale or cross-session updates", () => {
  const progress = new SelfEvolutionProgress();
  assert.equal(progress.restore(snapshot, { session_id: "session-a" }), true);
  const view = progress.accept(updateEvent({ fact_codes: ["observation_recorded"], evidence_refs: ["host-observation"] }));
  assert.ok(view);
  const markup = renderSelfEvolutionProgress(view);
  assert.match(markup, /role="status"/);
  assert.match(markup, /我找到现状依据了/);
  assert.doesNotMatch(markup, /position:\s*fixed|aria-modal/);
  assert.equal(progress.accept(updateEvent({ session_id: "session-b", sequence: 2, snapshot_revision: 3 })), null);
  assert.equal(progress.accept(updateEvent({ sequence: 1, snapshot_revision: 2 })), null);
  assert.equal(progress.restore(snapshot, { session_id: "session-b" }), false);
});

test("snapshot restoration uses the backend flattened projection shape, not a client-supplied authorization claim", () => {
  assert.ok(shell.includes('const exactKeys = ["session_id", "task_id", "attempt_id", "run_id", "turn_epoch", "phase", "status", "snapshot_revision", "sequence", "findings", "verification", "preview_handle", "type", "request_id", "history_status"]'));
  assert.ok(shell.includes("Object.keys(snapshotEvent).length !== exactKeys.length"));
  assert.ok(shell.includes('snapshotEvent.type !== "self_evolution.snapshot"'));
  assert.ok(shell.includes("snapshotEvent.session_id !== chat.model.sessionId"));
  assert.ok(shell.includes("const { type: _snapshotType, request_id: _requestId, history_status: _historyStatus, ...snapshot } = snapshotEvent"));
  assert.ok(!ws.includes("client_authorized"));
  const progress = new SelfEvolutionProgress();
  assert.equal(progress.restore(snapshot, { session_id: "session-b" }), false);
  assert.equal(progress.current(), null);
});


test("backend projection snapshot payload is consumed as the flattened typed event", () => {
  const projection = { snapshot: { type: "self_evolution.snapshot", ...snapshot }, events: [] };
  const payload = projection.snapshot;
  assert.equal(payload.type, "self_evolution.snapshot");
  const { type: _type, ...data } = payload;
  const progress = new SelfEvolutionProgress();
  assert.equal(progress.restore(data, { session_id: "session-a" }), true);
  assert.equal(progress.current()?.task_id, "task-a");
});

test("restored snapshots do not invent a spoken milestone or claim a worker action", () => {
  const progress = new SelfEvolutionProgress();
  assert.equal(progress.restore(snapshot, { session_id: "session-a" }), true);
  assert.equal(progress.current()?.milestones.length, 0);
  assert.ok(shell.includes('view.milestones.at(-1)?.text || ""'));
  assert.ok(!shell.includes("?????????????"));
});


test("the progress is a transcript child that cannot be removed by chat redraw or overlap the composer", () => {
  assert.match(shell, /const chatEl = root\.querySelector<HTMLElement>\("#unified-chat"\)!;[\s\S]{0,900}chatEl\.append\(selfEvolutionEl\)/);
  assert.match(shell, /selfEvolutionEl\.remove\(\);[\s\S]{0,180}doRender\(\);[\s\S]{0,180}chatEl\.append\(selfEvolutionEl\)/);
  assert.match(shell, /renderThrottleTimer = null;[\s\S]{0,140}selfEvolutionEl\.remove\(\);[\s\S]{0,140}doRender\(\);[\s\S]{0,180}chatEl\.append\(selfEvolutionEl\)/);
  const css = readFileSync(new URL("../src/shells/unified/self-evolution-progress.css", import.meta.url), "utf8");
  assert.doesNotMatch(css, /position\s*:\s*(?:fixed|absolute)/);
  assert.match(css, /max-height:\s*min\(42vh, 24rem\)/);
});

test("an update received before its snapshot requests host recovery instead of dropping the task", () => {
  assert.ok(ws.includes("requestSelfEvolutionSnapshot(taskId: string): string"));
  assert.ok(shell.includes("pendingSelfEvolutionSnapshotTasks"));
  assert.ok(shell.includes("pendingSelfEvolutionSnapshotRequests.get(event.task_id) === event.request_id"));
  assert.match(shell, /case "self_evolution\.update":[\s\S]{0,1000}requestSelfEvolutionTask\(event\.session_id, event\.task_id\)/);
  assert.ok(ws.includes('type: "self_evolution.snapshot.unavailable"'));
});

test("snapshot request adapter rejects directory-like identifiers and uses the authenticated session binding", () => {
  assert.match(ws, /requestSelfEvolutionSnapshot\(taskId: string\): string[\s\S]{0,320}this\.currentConversationId/);
  assert.match(ws, /self-evolution snapshot requires a current session and opaque task id/);
  assert.match(ws, /type: "self_evolution\.snapshot\.get", task_id: taskId, request_id: requestId/);
});


test("reconnect recovery retains only a same-session opaque task hint and re-requests its snapshot", () => {
  assert.ok(shell.includes('const selfEvolutionRecoveryKey = "my-agent:self-evolution-recovery:v1"'));
  assert.ok(shell.includes("rememberSelfEvolutionTask(snapshotEvent.session_id, snapshotEvent.task_id)"));
  assert.ok(shell.includes("requestRememberedSelfEvolutionTask(event.session_id)"));
  assert.match(shell, /case "client\.connection\.closed":[\s\S]{0,180}Keep the same-session projection[\s\S]{0,300}clearApprovalPreferenceTimeout/);
  assert.ok(shell.includes('selfEvolutionRecovery.hint(sessionId'));
  assert.ok(shell.includes('requestSelfEvolutionTask(sessionId, remembered.task_id)'));
  assert.doesNotMatch(shell, /localStorage\.(?:setItem|removeItem)\([^)]*(?:candidate_root|worker|authorization)/i);
});


test("server event bridge binds snapshot responses to current session and rejects stale unavailable responses", () => {
  assert.ok(ws.includes('data.session_id !== this.currentConversationId'));
  assert.ok(ws.includes('self_evolution.snapshot.unavailable'));
  assert.ok(shell.includes('pendingSelfEvolutionSnapshotRequests.get(event.task_id) === event.request_id'));
  assert.ok(shell.includes('event.session_id === chat.model.sessionId'));
});


test("mounted shell correlates out-of-order snapshots and reconnects with storage disabled", {timeout: 30000}, async (t) => {
  const { createRequire } = await import("node:module");
  const { existsSync, statSync } = await import("node:fs");
  const { default: path } = await import("node:path");
  const { default: os } = await import("node:os");
  const { fileURLToPath } = await import("node:url");
  const testNodeModules = process.env.CODEX_TEST_NODE_MODULES;
  if (testNodeModules && (!path.isAbsolute(testNodeModules) || !existsSync(testNodeModules) || !statSync(testNodeModules).isDirectory())) {
    throw new Error("CODEX_TEST_NODE_MODULES must be an existing absolute directory");
  }
  const requireFrom = testNodeModules
    ? path.join(testNodeModules, "..", "__codex_test__.cjs")
    : import.meta.url;
  const require = createRequire(requireFrom);
  let esbuild; try { esbuild = require("esbuild"); } catch (error) {
    if (testNodeModules) throw error;
    return t.skip("esbuild unavailable in this worktree");
  }
  const pw = process.env.CODEX_PLAYWRIGHT_PATH || path.join(os.homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
  const edge = process.env.ISOLATED_TEST_BROWSER || (process.platform === "win32" ? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" : "");
  if (!existsSync(pw) || !existsSync(edge)) return t.skip("isolated Playwright or browser unavailable");
  const root = fileURLToPath(new URL("../", import.meta.url));
  const bundle = await esbuild.build({ entryPoints: [path.join(root,"src/shells/unified/index.ts")], bundle: true,
    write: false, platform: "browser", format: "iife", globalName: "UnifiedUnderTest",
    ...(testNodeModules ? { nodePaths: [testNodeModules] } : {}),
    define: {"import.meta.url": '"https://isolated.invalid/entry.js"'},
    loader: {".png":"dataurl", ".jpg":"dataurl", ".svg":"dataurl", ".woff2":"dataurl"},
    plugins: [{name: "no-css", setup(b) { b.onLoad({filter:/\.css$/}, () => ({contents:"",loader:"js"})); }}], logLevel:"silent" });
  const browser = await require(pw).chromium.launch({headless:true, executablePath:edge, args:["--disable-background-networking","--no-first-run"]});
  try {
    const context = await browser.newContext({serviceWorkers:"block"});
    await context.route("**/*", r => r.request().url() === "https://isolated.invalid/" ? r.fulfill({status:200,contentType:"text/html",body:'<div id="root"></div>'}) : r.abort());
    const page = await context.newPage();
    await page.goto("https://isolated.invalid/");
    await page.addScriptTag({content:bundle.outputFiles[0].text});
    await page.addStyleTag({content:readFileSync(new URL("../src/shells/unified/self-evolution-progress.css", import.meta.url), "utf8")});    await page.addStyleTag({content:readFileSync(new URL("../src/styles/theme.css", import.meta.url), "utf8") + readFileSync(new URL("../src/shells/unified/unified.css", import.meta.url), "utf8") + readFileSync(new URL("../src/shells/unified/self-evolution-progress.css", import.meta.url), "utf8")});
    await page.evaluate(() => {
      const nativeStorage = window.localStorage;
      const recoveryKey = "my-agent:self-evolution-recovery:v1";
      Object.defineProperty(window, "localStorage", {get() {
        return new Proxy(nativeStorage, {get(target, key) {
          if (key === "getItem" || key === "setItem") return (item, ...args) => {
            if (item === recoveryKey) throw new Error("self-evolution storage disabled");
            return target[key](item, ...args);
          };
          const value = Reflect.get(target, key, target);
          return typeof value === "function" ? value.bind(target) : value;
        }});
      }});
      window.myAgentDesktop = {setBilibiliViewState:async()=>{},setShellDevViewState:async()=>{}};
      window.requests = [];
      const client = new Proxy({onEvent: fn => {window.deliver = fn; return ()=>{};}, listSessionsMerged:async()=>[],
        requestSelfEvolutionSnapshot: id => {const rid = `request-${window.requests.length+1}`; window.requests.push({id,rid}); return rid;}},
        {get:(o,k)=>k in o ? o[k] : (...args)=>String(k).startsWith("on") ? ()=>{} : undefined});
      window.disposeUnified = UnifiedUnderTest.mountUnifiedShell(document.querySelector("#root"), client);
      window.deliver({type:"session.banner",session_id:"session-a",topics:[],turn_mode:"agent",turn_mode_label:"",phase:"S4"});
    });
    const update = task_id => ({...updateEvent(), task_id});
    await page.evaluate(([a,b]) => {window.deliver(a);window.deliver(b);}, [update("task-a"),update("task-b")]);
    assert.deepEqual(await page.evaluate(()=>window.requests), [{id:"task-a",rid:"request-1"},{id:"task-b",rid:"request-2"}]);
    const snap = (task_id,request_id,value=snapshot) => ({type:"self_evolution.snapshot",...value,task_id,request_id,history_status:"not_restored"});    const reviewSnapshot = {...snapshot,phase:"awaiting_review",status:"completed",findings:["审阅摘要来自可信宿主"],verification:[{label:"定向验证",result:"passed",evidence_ref:"evidence-playwright-1"}],preview_handle:"opaque-only"};
    await page.evaluate(([b,a]) => {window.deliver(b);window.deliver(a);}, [snap("task-b","request-2",reviewSnapshot),snap("task-a","request-1")]);
    assert.match(await page.locator("[data-self-evolution-shell]").textContent(), /task-b/);
    assert.doesNotMatch(await page.locator("[data-self-evolution-shell]").textContent(), /task-a/);
    const detailToggle = page.locator("[data-self-evolution-detail-toggle]");
    await detailToggle.click();
    const details = page.locator("[data-self-evolution-details]");
    assert.equal(await details.evaluate(el=>el.open),true);
    const reviewText = await details.textContent();
    assert.match(reviewText,/审阅摘要来自可信宿主/);
    assert.match(reviewText,/evidence-playwright-1/);
    assert.match(reviewText,/不能当作预览入口/);
    assert.match(reviewText,/原生确认与受控合入当前不可用/);
    assert.equal(await details.locator("button[data-self-evolution-detail-close]").count(),1);
    assert.equal(await details.locator("button[data-self-evolution-review]").count(),1);
    assert.equal(await details.locator("button[data-self-evolution-apply]").count(),0);
    assert.equal(await details.locator("a").count(),0);
    await page.setViewportSize({width:1280,height:800});
    await details.evaluate(el=>{el.scrollTop=el.scrollHeight;});
    await details.evaluate(el=>{el.style.maxHeight="none";el.style.overflow="visible";el.scrollTop=0;});
    await details.locator(".self-evolution-progress__review").screenshot({path:path.join(os.tmpdir(),"my-agent-self-evolution-review.png")});
    await page.evaluate(()=>{window.deliver({type:"client.connection.closed"});window.deliver({type:"session.banner",session_id:"session-a",topics:[],turn_mode:"agent",turn_mode_label:"",phase:"S4"});});
    assert.deepEqual((await page.evaluate(()=>window.requests)).at(-1), {id:"task-b",rid:"request-3"});
    await page.evaluate(s => window.deliver(s),snap("task-b","request-3"));
    const text=await page.locator("[data-self-evolution-shell]").textContent();
    assert.match(text, /\u5386\u53f2\u8fdb\u5ea6\u672a\u6062\u590d/);
    assert.doesNotMatch(text, /\u6211\u627e\u5230\u73b0\u72b6\u4f9d\u636e/);
    await page.evaluate(()=>window.disposeUnified());
    await context.close();
  } finally { await browser.close(); }
});

test("chat review details surface evidence identifiers and capability blockers without offering fake controls", () => {
  const view = {
    ...snapshot, phase: "awaiting_review", status: "completed",
    findings: ["宿主审核后的观察摘要"],
    verification: [{ label: "候选检查", result: "passed", evidence_ref: "evidence-bound-1" }],
    preview_handle: "opaque-only",
  };
  const markup = renderSelfEvolutionProgress(view);
  assert.match(shell, /renderSelfEvolutionReview\(view,/);
  assert.match(shell, /<details class="self-evolution-shell__details"/);
  assert.match(markup, /aria-label="待审阅交付"/);
  assert.match(markup, /evidence-bound-1/);
  assert.match(markup, /不能当作预览入口/);
  assert.match(markup, /原生确认与受控合入当前不可用/);
  assert.doesNotMatch(markup, /data-self-evolution-(?:preview|confirm|merge)|<button|<a\\s/i);
});
