import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readFileSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const require = createRequire(import.meta.url);
const esbuild = require('esbuild');
const desktop = fileURLToPath(new URL('../', import.meta.url));
const core = path.resolve(desktop, '../agent-core');
const python = process.env.SYNTHETIC_PYTHON || 'F:/my-agent-main/.venv/Scripts/python.exe';
const playwrightPath = process.env.CODEX_PLAYWRIGHT_PATH
  || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const edge = process.env.ISOLATED_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const projects = Array.from({ length: 5 }, (_, index) => `synthetic-project-${index + 1}`);
const selected = [projects[1], projects[3]];

test('mounted unified chat + real WS handler: scoped multi-project overview reads only after confirmation', {
  timeout: 60000,
  skip: !existsSync(python) || !existsSync(playwrightPath) || !existsSync(edge),
}, async () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'synthetic-multi-overview-'));
  const env = {
    ...process.env,
    PYTHONDONTWRITEBYTECODE: '1', PYTHONNOUSERSITE: '1',
    HOME: root, USERPROFILE: root, APPDATA: root, LOCALAPPDATA: root,
  };
  const child = spawn(python, [path.join(desktop, 'tests/multi-project-overview-local-fixture.py'), root, core, '--with-history'], {
    cwd: desktop, env, stdio: ['pipe', 'pipe', 'pipe'],
  });
  let stderr = '';
  child.stderr.on('data', chunk => { stderr += chunk; });
  let browser;
  let context;
  try {
    const lines = createInterface({ input: child.stdout });
    const ready = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`fixture startup timeout\n${stderr}`)), 12000);
      lines.once('line', line => {
        clearTimeout(timer);
        try { resolve(JSON.parse(line)); } catch (error) { reject(error); }
      });
      child.once('exit', code => {
        clearTimeout(timer);
        reject(new Error(`fixture exited ${code}\n${stderr}`));
      });
    });
    assert.ok(ready.port > 0 && ![5173, 5174, 8000, 8765].includes(ready.port));

    const bundle = await esbuild.build({
      stdin: {
        contents: 'export {mountUnifiedShell} from "./src/shells/unified/index.ts"; export {AgentWsClient} from "./src/api/ws.ts";',
        resolveDir: desktop, loader: 'ts',
      },
      bundle: true, write: false, platform: 'browser', format: 'iife', globalName: 'UnderTest',
      define: { 'import.meta.url': '"http://127.0.0.1/isolated.js"' },
      loader: { '.png': 'dataurl', '.jpg': 'dataurl', '.svg': 'dataurl', '.woff2': 'dataurl' },
      plugins: [{ name: 'no-css', setup(build) { build.onLoad({ filter: /\.css$/ }, () => ({ contents: '', loader: 'js' })); } }],
      logLevel: 'silent',
    });
    browser = await require(playwrightPath).chromium.launch({
      headless: true, executablePath: edge,
      args: ['--disable-background-networking', '--disable-features=LocalNetworkAccessChecks,LocalNetworkAccessRestrictions,BlockInsecurePrivateNetworkRequests', '--disable-component-update', '--disable-sync', '--no-first-run',
        '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1'],
    });
    context = await browser.newContext({ serviceWorkers: 'block', viewport: { width: 1100, height: 800 } });
    const origin = `http://127.0.0.1:${ready.port}`;
    const blocked = [];
    const errors = [];
    await context.route('**/*', route => {
      const url = route.request().url();
      if (url === `${origin}/`) return route.fulfill({ status: 200, contentType: 'text/html', body:
        `<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src ws://127.0.0.1:${ready.port}; img-src data:"><div id="root"></div>` });
      blocked.push(url);
      return route.abort();
    });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    page.setDefaultTimeout(10000);
    await page.goto(`${origin}/`);
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    await page.evaluate(async ({ port }) => {
      window.myAgentDesktop = { setBilibiliViewState: async () => {}, setShellDevViewState: async () => {} };
      window.client = new UnderTest.AgentWsClient('127.0.0.1', port);
      window.events = [];
      window.client.onEvent(event => window.events.push(event));
      window.disposeUnified = UnderTest.mountUnifiedShell(document.querySelector('#root'), window.client);
      await window.client.connect();
    }, { port: ready.port });
    await page.waitForFunction(() => window.events.some(event => event.type === 'session.banner' && event.session_id === '20261030-00000001'));

    const sidecarLedger = path.join(root, 'project-sidecar-reads.jsonl');
    await page.locator('#unified-input').fill('所有项目的工作概况');
    await page.locator('#unified-send').click();
    await page.waitForFunction(() => window.events.some(event => event.type === 'confirm.request' && event.allow_approve_all === false));
    const firstConfirmId = await page.evaluate(() => window.events.filter(event => event.type === 'confirm.request').at(-1).request_id);
    const denial = page.locator(`[data-confirm="n"][data-id="${firstConfirmId}"]`);
    await denial.waitFor({ state: 'visible' });
    await denial.click();
    await page.waitForFunction(() => window.events.some(event => event.type === 'confirm.done' && event.choice === 'n'));
    await page.waitForFunction(() => window.events.some(event => event.type === 'turn.end' && event.finish_reason === 'multi_project_work_record_overview'));
    assert.equal(existsSync(sidecarLedger), false, 'declining confirmation must not read any project sidecar');
    assert.match(await page.locator('#unified-chat').textContent(), /未读取/);

    const completedTurnsBeforeSelection = await page.evaluate(() => window.events.filter(event => event.type === 'turn.end').length);
    await page.locator('#unified-input').fill(`查看项目 ${selected.join('、')} 的工作概况`);
    await page.locator('#unified-send').click();
    await page.waitForFunction(() => {
      const requests = window.events.filter(event => event.type === 'confirm.request');
      return requests.length >= 2 && requests.at(-1).allow_approve_all === false;
    });
    const secondConfirmId = await page.evaluate(() => window.events.filter(event => event.type === 'confirm.request').at(-1).request_id);
    const approval = page.locator(`[data-confirm="y"][data-id="${secondConfirmId}"]`);
    await approval.waitFor({ state: 'visible' });
    const preview = await page.evaluate(id => window.events.find(event => event.type === 'confirm.request' && event.request_id === id).preview, secondConfirmId);
    for (const project of selected) assert.match(preview, new RegExp(project));
    for (const project of projects.filter(project => !selected.includes(project))) assert.doesNotMatch(preview, new RegExp(project));
    await approval.click();

    await page.waitForFunction(({ previous }) => { const ends = window.events.filter(event => event.type === 'turn.end'); return ends.length > previous && ends.at(-1).finish_reason === 'multi_project_work_record_overview'; }, { previous: completedTurnsBeforeSelection });
    const transcript = await page.locator('#unified-chat').textContent();
    for (const project of selected) {
      assert.match(transcript, new RegExp(`${project} recorded item`));
      assert.match(transcript, new RegExp(`${project} requirement 1`));
      assert.match(transcript, new RegExp(`${project} requirement 2`));
    }
    for (const project of projects.filter(project => !selected.includes(project))) {
      assert.doesNotMatch(transcript, new RegExp(`${project} recorded item`));
      assert.doesNotMatch(transcript, new RegExp(`${project} requirement`));
    }
    // Exercise the structured multi-project panel and a separately confirmed
    // remote record control. No worker or project-file mutation is requested.
    await page.evaluate(() => window.client.listProjects());
    await page.waitForFunction(() => window.events.some(event => event.type === 'project.list'));
    const selectedPanel = page.locator('[data-selected-project-overview]');
    try { await selectedPanel.locator('summary').waitFor({ state: 'visible', timeout: 3000 }); }
    catch (error) { throw new Error(`${error.message}\\nprojectEvents=${JSON.stringify(await page.evaluate(() => window.events.filter(event => event.type.startsWith('project.'))))}`); }
    await page.evaluate(ids => {
      const panel = document.querySelector('[data-selected-project-overview]');
      if (!(panel instanceof HTMLDetailsElement)) throw new Error('selected-project overview panel missing');
      panel.open = true;
      for (const id of ids) {
        const input = panel.querySelector(`input[data-project-overview-id="${CSS.escape(id)}"]`);
        if (!(input instanceof HTMLInputElement)) throw new Error(`project selector missing: ${id}`);
        input.checked = true;
        input.dispatchEvent(new Event('change', { bubbles: true }));
      }
    }, selected);
    const overviewEventCount = await page.evaluate(() => window.events.filter(event => event.type === 'work-records.overview.result').length);
    const sourceConfirmCount = await page.evaluate(() => window.events.filter(event => event.type === 'confirm.request').length);
    await page.evaluate(() => document.querySelector('[data-run-selected-project-overview]')?.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    await page.waitForFunction(({ previous }) => window.events.filter(event => event.type === 'confirm.request').length > previous,
      { previous: sourceConfirmCount });
    await page.locator('[data-confirm="y"]:not([disabled])').last().click();
    await page.waitForFunction(({ previous }) => window.events.filter(event => event.type === 'work-records.overview.result').length > previous,
      { previous: overviewEventCount });
    // The client records protocol events before the mounted shell consumes them.
    // Wait for the rendered projection too, so its per-session request lock has
    // been released before issuing the next independently authorized query.
    await page.getByText(`${selected[0]} recorded item`, { exact: true }).waitFor({ state: 'visible' });

    // Each selected-project unified-history page is separately authorized.
    const openSelectedPanel = () => page.evaluate(() => {
      const panel = document.querySelector('[data-selected-project-overview]');
      if (panel instanceof HTMLDetailsElement) panel.open = true;
    });
    const historyResultsBeforeDenial = await page.evaluate(() => window.events.filter(event => event.type === 'work-records.overview.result' && event.history_scope === 'selected_projects_history').length);
    let confirmationCount = await page.evaluate(() => window.events.filter(event => event.type === 'confirm.request').length);
    await openSelectedPanel();
    await page.locator('[data-run-selected-project-history]').click();
    await page.waitForFunction(({ previous }) => window.events.filter(event => event.type === 'confirm.request').length > previous,
      { previous: confirmationCount });
    await page.locator('[data-confirm="n"]:not([disabled])').last().click();
    await page.waitForTimeout(100);
    assert.equal(await page.evaluate(() => window.events.filter(event => event.type === 'work-records.overview.result' && event.history_scope === 'selected_projects_history').length), historyResultsBeforeDenial,
      'declining history authorization returns no history page');

    confirmationCount = await page.evaluate(() => window.events.filter(event => event.type === 'confirm.request').length);
    await openSelectedPanel();
    await page.locator('[data-run-selected-project-history]').click();
    await page.waitForFunction(({ previous }) => window.events.filter(event => event.type === 'confirm.request').length > previous,
      { previous: confirmationCount });
    await page.locator('[data-confirm="y"]:not([disabled])').last().click();
    await page.waitForFunction(({ previous }) => window.events.filter(event => event.type === 'work-records.overview.result' && event.history_scope === 'selected_projects_history').length > previous,
      { previous: historyResultsBeforeDenial });
    const firstHistoryResult = await page.evaluate(() => window.events.filter(event => event.type === 'work-records.overview.result' && event.history_scope === 'selected_projects_history').at(-1));
    assert.equal(firstHistoryResult.history.length, 10);
    assert.equal(firstHistoryResult.history_page.has_more, true);
    assert.deepEqual(firstHistoryResult.history_sources.map(source => source.project_id).sort(), [...selected].sort());
    assert.match(await page.locator('[data-merged-history]').textContent(), /<img src=x onerror=alert\(1\)>/);
    assert.equal(await page.locator('[data-merged-history] img').count(), 0, 'history summaries render as escaped text');
    const nextHistoryButton = page.locator('[data-merged-history-next]');
    await nextHistoryButton.waitFor({ state: 'visible' });
    confirmationCount = await page.evaluate(() => window.events.filter(event => event.type === 'confirm.request').length);
    await nextHistoryButton.click();
    await page.waitForFunction(({ previous }) => window.events.filter(event => event.type === 'confirm.request').length > previous,
      { previous: confirmationCount });
    await page.locator('[data-confirm="y"]:not([disabled])').last().click();
    await page.waitForFunction(({ previous }) => window.events.filter(event => event.type === 'work-records.overview.result' && event.history_scope === 'selected_projects_history').length > previous,
      { previous: historyResultsBeforeDenial + 1 });
    const historyPages = await page.evaluate(() => window.events.filter(event => event.type === 'work-records.overview.result' && event.history_scope === 'selected_projects_history'));
    assert.equal(historyPages.at(-1).history.length, 2);
    assert.equal(historyPages.at(-1).history_page.has_more, false);
    assert.equal(new Set(historyPages.flatMap(event => event.history.map(row => row.result_id))).size, 12,
      'separately authorized pages contain twelve unique synthetic history entries');

    await page.waitForSelector('[data-work-record-control][data-action="queue"]');
    const authConfirmCount = await page.evaluate(() => window.events.filter(event => event.type === 'confirm.request').length);
    await page.locator(`[data-work-record-control][data-action="queue"][data-source-project-id="${selected[1]}"]`).first().click();
    await page.waitForFunction(({ previous }) => window.events.filter(event => event.type === 'confirm.request').length > previous,
      { previous: authConfirmCount });
    await page.locator('[data-confirm="y"]:not([disabled])').last().click();
    const operationConfirmCount = await page.evaluate(() => window.events.filter(event => event.type === 'confirm.request').length);
    await page.waitForFunction(({ previous }) => window.events.filter(event => event.type === 'confirm.request').length > previous,
      { previous: operationConfirmCount });
    const operationPreview = await page.evaluate(() => window.events.filter(event => event.type === 'confirm.request').at(-1).preview);
    assert.match(operationPreview, /确认更新另一个项目 Session/);
    assert.match(operationPreview, /仅变更工作记录元数据/);
    await page.locator('[data-confirm="y"]:not([disabled])').last().click();
    await page.waitForFunction(() => window.events.some(event => event.type === 'work-record.control.done' && event.status === 'applied'));
    const controlEvent = await page.evaluate(() => window.events.filter(event => event.type === 'work-record.control.done').at(-1));
    assert.equal(controlEvent.project_id, selected[1]);
    assert.equal(controlEvent.action, 'queue');
    assert.match(controlEvent.message, /未启动 worker/);

    const reads = readFileSync(sidecarLedger, 'utf8').trim().split(/\r?\n/).filter(Boolean).map(line => JSON.parse(line).session_id);
    assert.deepEqual(new Set(reads), new Set(selected.map(project => `20261030-${(projects.indexOf(project) + 2).toString(16).padStart(8, "0")}`)));
    assert.deepEqual(errors, []);
    assert.ok(blocked.every(url => url.startsWith(origin)), `unexpected network attempts: ${blocked.join(', ')}`);
    console.log(`synthetic acceptance: 5 isolated projects, selected=${selected.join(',')}, denied reads=0, approved project sidecar reads=${reads.length}, port=${ready.port}, root=${root}`);
    await page.evaluate(() => { window.disposeUnified(); window.client.destroy(); });
  } finally {
    if (context) await context.close();
    if (browser) await browser.close();
    child.stdin.end('\n');
    await new Promise(resolve => {
      if (child.exitCode !== null) return resolve();
      child.once('exit', resolve);
      setTimeout(() => { child.kill(); resolve(); }, 4000);
    });
    if (stderr) console.log(`fixture diagnostics: ${stderr.slice(-3000)}`);
    console.log(`isolated synthetic root retained for inspection: ${root}`);
  }
});





