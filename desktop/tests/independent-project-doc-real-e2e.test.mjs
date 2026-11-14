import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
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
const projectId = 'synthetic-independent-doc-project';
const sessionId = '20261114-00000001';

test('real unified UI → WebSocket → server independently authorizes, reads, saves, protects drafts, and detects conflicts', {
  timeout: 60000,
  skip: !existsSync(python) || !existsSync(playwrightPath) || !existsSync(edge),
}, async () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'independent-project-doc-'));
  assert.ok(root.startsWith(path.join(os.tmpdir(), 'independent-project-doc-')));
  const env = {
    ...process.env,
    PYTHONDONTWRITEBYTECODE: '1', PYTHONNOUSERSITE: '1',
    HOME: root, USERPROFILE: root, APPDATA: root, LOCALAPPDATA: root,
  };
  const child = spawn(python, [path.join(desktop, 'tests/independent-project-doc-real-e2e-fixture.py'), root, core], {
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
    assert.equal(ready.session_id, sessionId);
    const guidePath = path.join(root, 'workspace', projectId, 'GUIDE.md');
    const originalGuideBytes = readFileSync(guidePath);

    const bundle = await esbuild.build({
      stdin: {
        // Match the application's global CSS entry order and retain all of the
        // real unified shell's transitive CSS imports; no stylesheet interception.
        contents: 'import "./src/styles/theme.css"; import "./src/styles/managed-session.css"; export {mountAppChrome} from "./src/app-chrome.ts"; import "./src/host-settings.css"; export {mountUnifiedShell} from "./src/shells/unified/index.ts"; import "./src/context-switch-overlay.css"; export {AgentWsClient} from "./src/api/ws.ts";',
        resolveDir: desktop, loader: 'ts',
      },
      bundle: true, write: false, outfile: path.join(root, 'isolated-app.js'), platform: 'browser', format: 'iife', globalName: 'UnderTest',
      define: { 'import.meta.url': '"http://127.0.0.1/isolated.js"' },
      loader: { '.png': 'dataurl', '.jpg': 'dataurl', '.svg': 'dataurl', '.woff2': 'dataurl' },
      logLevel: 'silent',
    });
    const script = bundle.outputFiles.find(file => file.path.endsWith('.js'));
    const stylesheet = bundle.outputFiles.find(file => file.path.endsWith('.css'));
    assert.ok(script && stylesheet, 'real JavaScript and application CSS must both be bundled');
    assert.match(stylesheet.text, /\.unified-shell/);
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
        `<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src ws://127.0.0.1:${ready.port}; img-src data:"><div id="app"><div class="app-frame"><div id="app-chrome"></div><div id="shell-root"><div id="root" class="shell-host"></div></div></div></div>` });
      blocked.push(url);
      return route.abort();
    });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    page.setDefaultTimeout(10000);
    await page.goto(`${origin}/`);
    await page.addStyleTag({ content: stylesheet.text });
    await page.addScriptTag({ content: script.text });
    assert.equal(await page.locator('.app-frame').evaluate(element => getComputedStyle(element).display), 'flex', 'application layout CSS must be active');
    await page.evaluate(async ({ port }) => {
      window.myAgentDesktop = { setBilibiliViewState: async () => {}, setShellDevViewState: async () => {} };
      UnderTest.mountAppChrome(document.querySelector('#app-chrome'), { onSwitchToCli: async () => {} });
      window.client = new UnderTest.AgentWsClient('127.0.0.1', port);
      window.events = [];
      window.client.onEvent(event => window.events.push(event));
      // Match desktop/src/main.ts: createWsClient connects before mountShell.
      // The mounted shell issues its initial project.list request on this live socket.
      await window.client.connect();
      window.disposeUnified = UnderTest.mountUnifiedShell(document.querySelector('#root'), window.client);
      window.client.refreshSession();
    }, { port: ready.port });
    await page.waitForFunction(id => window.events.some(event => event.type === 'session.banner' && event.session_id === id), sessionId);
    await page.waitForFunction(() => window.events.some(event => event.type === 'project.list'));
    const discoveredProjects = await page.evaluate(() => window.events.filter(event => event.type === 'project.list').at(-1).projects.map(project => project.id));
    assert.ok(discoveredProjects.includes(projectId), `real project.list omitted fixture project ${projectId}: ${JSON.stringify(discoveredProjects)}`);
    await page.locator('#unified-tool-docs').click();
    const documentFocus = await page.locator('.unified-shell').getAttribute('data-main-focus');
    assert.equal(documentFocus, 'document', 'the unbound docs tab must open the independent document view instead of reverting to chat');
    const projectSelect = page.locator('[data-independent-doc-project-select]');
    await projectSelect.waitFor({ state: 'visible' });
    await projectSelect.selectOption(projectId);
    await page.waitForFunction(() => window.events.some(event => event.type === 'confirm.request'));
    const firstRequest = await page.evaluate(() => window.events.filter(event => event.type === 'confirm.request').at(-1));
    assert.equal(firstRequest.allow_approve_all, false);
    assert.match(firstRequest.preview, /不会切换或绑定当前会话/);
    assert.match(firstRequest.preview, new RegExp(projectId));
    assert.equal(await page.evaluate(() => window.events.filter(event => event.type.startsWith('project.doc.view.list.done') || event.type === 'project.doc.view.read.done').length), 0,
      'candidate discovery is not authorization and must not read documents');
    // A Playwright click checks visibility, enabled state, stability and hit testing.
    const denialButton = page.locator(`[data-confirm="n"][data-id="${firstRequest.request_id}"]:visible`);
    await denialButton.click();
    await page.waitForFunction(() => window.events.some(event => event.type === 'project.doc.view.error' && event.code === 'permission_denied'));
    assert.equal(await page.evaluate(() => window.events.some(event => event.type === 'project.doc.view.list.done' || event.type === 'project.doc.view.read.done')), false,
      'denial must not return a document list or document content');

    await projectSelect.selectOption('');
    await projectSelect.selectOption(projectId);
    await page.waitForFunction(({ count }) => window.events.filter(event => event.type === 'confirm.request').length > count,
      { count: 1 });
    const approveRequest = await page.evaluate(() => window.events.filter(event => event.type === 'confirm.request').at(-1));
    await page.locator(`[data-confirm="y"][data-id="${approveRequest.request_id}"]:visible`).click();
    await page.waitForFunction(() => window.events.some(event => event.type === 'project.doc.view.authorized'));
    await page.waitForFunction(() => window.events.some(event => event.type === 'project.doc.view.read.done'));
    const guide = page.locator('[data-doc-path="GUIDE.md"]');
    await guide.waitFor({ state: 'visible' });
    const guideReadsBeforeOpen = await page.evaluate(() => window.events.filter(event => event.type === 'project.doc.view.read.done' && event.path === 'GUIDE.md').length);
    await guide.click();
    await page.waitForFunction(previous => window.events.filter(event => event.type === 'project.doc.view.read.done' && event.path === 'GUIDE.md').length > previous, guideReadsBeforeOpen);
    await page.getByText('Synthetic guide original', { exact: false }).waitFor({ state: 'visible' });
    const authorized = await page.evaluate(() => window.events.find(event => event.type === 'project.doc.view.authorized'));
    assert.ok(authorized.view_id, 'backend issues a connection-scoped view lease only after approval');

    await page.locator('[data-action="document-edit"]').click();
    const editor = page.locator('.unified-document-editor');
    const draft = '# Guide\n\nSynthetic guide saved from the real UI.';
    await editor.fill(draft);
    // Changing the independent project picker must not silently discard a draft
    // or bind the chat session. Cancel the pending clear and keep editing.
    await projectSelect.selectOption('');
    await page.locator('[data-action="document-cancel-leave"]').waitFor({ state: 'visible' });
    await page.locator('[data-action="document-cancel-leave"]').click();
    assert.equal(await projectSelect.inputValue(), projectId, 'cancel keeps the authorized project selected');
    assert.equal(await editor.inputValue(), draft, 'cancel preserves the unsaved draft');
    assert.deepEqual(readFileSync(guidePath), originalGuideBytes, 'cancel does not write the draft to disk');
    const bannersBeforeCancelCheck = await page.evaluate(() => window.events.filter(event => event.type === 'session.banner').length);
    await page.evaluate(() => window.client.refreshSession());
    await page.waitForFunction(previous => window.events.filter(event => event.type === 'session.banner').length > previous, bannersBeforeCancelCheck);
    const sessionAfterCancel = await page.evaluate(() => window.events.filter(event => event.type === 'session.banner').at(-1));
    assert.equal(sessionAfterCancel.session_id, sessionId);
    assert.ok(!sessionAfterCancel.project_id, 'canceling a project change must not bind the live session');
    assert.equal(await editor.inputValue(), draft, 'the draft remains after checking session state');

    const readsBeforeDiscard = await page.evaluate(() => window.events.filter(event => event.type === 'project.doc.view.read.done').length);
    await page.locator('[data-doc-path="README.md"]').click();
    await page.locator('[data-action="document-discard-leave"]').waitFor({ state: 'visible' });
    assert.equal(await page.evaluate(before => window.events.filter(event => event.type === 'project.doc.view.read.done').length, readsBeforeDiscard), readsBeforeDiscard,
      'switching with an unsaved draft must prompt before reading the next file');
    await page.locator('[data-action="document-discard-leave"]').click();
    await page.waitForFunction(() => window.events.some(event => event.type === 'project.doc.view.read.done' && event.path === 'README.md'));
    await page.getByText('Synthetic readme original', { exact: false }).waitFor({ state: 'visible' });
    assert.deepEqual(readFileSync(guidePath), originalGuideBytes,
      'discarding a draft must leave the exact original file bytes unchanged');

    const guideReadsBeforeReturn = await page.evaluate(() => window.events.filter(event => event.type === 'project.doc.view.read.done' && event.path === 'GUIDE.md').length);
    await page.locator('[data-doc-path="GUIDE.md"]').click();
    await page.waitForFunction(previous => window.events.filter(event => event.type === 'project.doc.view.read.done' && event.path === 'GUIDE.md').length > previous, guideReadsBeforeReturn);
    await page.locator('[data-action="document-edit"]').click();
    await page.locator('.unified-document-editor').fill('# Guide\n\nSynthetic guide saved through the real UI.');
    const writesBeforeSave = await page.evaluate(() => window.events.filter(event => event.type === 'project.doc.view.write.done').length);
    await page.locator('[data-action="document-save"]').click();
    await page.waitForFunction(previous => window.events.filter(event => event.type === 'project.doc.view.write.done').length > previous, writesBeforeSave);
    assert.equal(readFileSync(path.join(root, 'workspace', projectId, 'GUIDE.md'), 'utf8'),
      '# Guide\n\nSynthetic guide saved through the real UI.');

    await page.locator('.unified-document-editor').fill('# Guide\n\nSynthetic local edit before conflict.');
    const externalContent = '# Guide\n\nSynthetic external writer update.\n';
    writeFileSync(path.join(root, 'workspace', projectId, 'GUIDE.md'), externalContent, 'utf8');
    await page.locator('[data-action="document-save"]').click();
    await page.locator('[data-action="document-conflict-reload"]').waitFor({ state: 'visible' });
    assert.match(await page.locator('.unified-document-conflict').textContent(), /外部|变化|重新载入/);
    assert.equal(readFileSync(path.join(root, 'workspace', projectId, 'GUIDE.md'), 'utf8'), externalContent,
      'stale editor save must not overwrite an external update');
    const guideReadsBeforeReload = await page.evaluate(() => window.events.filter(event => event.type === 'project.doc.view.read.done' && event.path === 'GUIDE.md').length);
    await page.locator('[data-action="document-conflict-reload"]').click();
    await page.waitForFunction(previous => window.events.filter(event => event.type === 'project.doc.view.read.done' && event.path === 'GUIDE.md').length > previous, guideReadsBeforeReload);
    await page.getByText('Synthetic external writer update.', { exact: false }).waitFor({ state: 'visible' });

    const saved = readFileSync(path.join(root, 'workspace', projectId, 'GUIDE.md'), 'utf8');
    assert.equal(saved, externalContent, 'conflict reload leaves the external writer content intact');
    const bannersBeforeRefresh = await page.evaluate(() => window.events.filter(event => event.type === 'session.banner').length);
    await page.evaluate(() => window.client.refreshSession());
    await page.waitForFunction(previous => window.events.filter(event => event.type === 'session.banner').length > previous, bannersBeforeRefresh);
    const liveSession = await page.evaluate(() => window.events.filter(event => event.type === 'session.banner').at(-1));
    assert.equal(liveSession.session_id, sessionId);
    assert.ok(!liveSession.project_id, 'independent doc viewing must not change the live session binding');
    const sessionRecord = JSON.parse(readFileSync(path.join(root, 'data', 'sessions', sessionId, 'meta.json'), 'utf8'));
    assert.equal(sessionRecord.project_id, '', 'independent doc viewing must not bind the persisted session to the selected project');
    assert.deepEqual(errors, []);
    assert.ok(blocked.every(url => url.startsWith(origin)), `unexpected browser network attempts: ${blocked.join(', ')}`);
    console.log(`independent project-doc real E2E: denial returned no document data; approved read/save/discard/conflict/reload; session unbound; synthetic root=${root}`);
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
    rmSync(root, { recursive: true, force: true });
  }
});
