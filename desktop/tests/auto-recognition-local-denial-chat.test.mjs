import assert from 'node:assert/strict';
import test from 'node:test';
import { existsSync, mkdtempSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const esbuild = require('esbuild');
const desktop = fileURLToPath(new URL('../', import.meta.url));
const core = path.resolve(desktop, '../agent-core');
const python = process.env.SYNTHETIC_PYTHON || 'F:/my-agent-main/.venv/Scripts/python.exe';
const playwright = process.env.CODEX_PLAYWRIGHT_PATH || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const edge = process.env.ISOLATED_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const fixture = path.join(desktop, 'tests/auto-recognition-local-fixture.py');

test('real UI + isolated real WS: rejected candidate is not saved; chat has no confirmation or record', {
  timeout: 60000,
  skip: !existsSync(python) || !existsSync(playwright) || !existsSync(edge) || !existsSync(fixture),
}, async () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'synthetic-auto-denial-chat-'));
  const env = { ...process.env, PYTHONDONTWRITEBYTECODE: '1', PYTHONNOUSERSITE: '1', HOME: root,
    USERPROFILE: root, APPDATA: root, LOCALAPPDATA: root };
  const child = spawn(python, [fixture, root, core], { cwd: desktop, env, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
  let stderr = '';
  child.stderr.on('data', chunk => { stderr += chunk; });
  let browser;
  try {
    const rl = createInterface({ input: child.stdout });
    const ready = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`fixture startup timeout: ${stderr}`)), 10000);
      rl.once('line', line => { clearTimeout(timer); try { resolve(JSON.parse(line)); } catch (error) { reject(error); } });
      child.once('exit', code => { clearTimeout(timer); reject(new Error(`fixture exited ${code}: ${stderr}`)); });
    });
    assert.ok(ready.port > 0 && ![5173, 5174, 8000, 8765].includes(ready.port));
    const bundle = await esbuild.build({
      stdin: { contents: 'export {mountUnifiedShell} from "./src/shells/unified/index.ts"; export {AgentWsClient} from "./src/api/ws.ts";', resolveDir: desktop, loader: 'ts' },
      bundle: true, write: false, platform: 'browser', format: 'iife', globalName: 'UnderTest',
      define: { 'import.meta.url': '"http://127.0.0.1/isolated.js"' },
      loader: { '.png': 'dataurl', '.jpg': 'dataurl', '.svg': 'dataurl', '.woff2': 'dataurl' },
      plugins: [{ name: 'no-css', setup(build) { build.onLoad({ filter: /\.css$/ }, () => ({ contents: '', loader: 'js' })); } }],
      logLevel: 'silent',
    });
    browser = await require(playwright).chromium.launch({ headless: true, executablePath: edge, args: [
      '--disable-background-networking', '--disable-features=LocalNetworkAccessChecks,LocalNetworkAccessRestrictions,BlockInsecurePrivateNetworkRequests',
      '--disable-component-update', '--disable-sync', '--no-first-run',
      '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1',
    ] });
    const context = await browser.newContext({ serviceWorkers: 'block', viewport: { width: 1100, height: 800 } });
    const errors = [];
    const origin = `http://127.0.0.1:${ready.port}`;
    await context.route('**/*', route => route.request().url() === `${origin}/`
      ? route.fulfill({ status: 200, contentType: 'text/html', body: `<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src ws://127.0.0.1:${ready.port}; img-src data:"><div id="root"></div>` })
      : route.abort());
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    page.setDefaultTimeout(8000);
    await page.goto(origin + '/');
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    await page.evaluate(async port => {
      window.myAgentDesktop = { setBilibiliViewState: async () => {}, setShellDevViewState: async () => {} };
      const Native = WebSocket;
      window.WebSocket = class extends Native {
        constructor(url, ...args) { if (url !== `ws://127.0.0.1:${port}`) throw Error('external WS forbidden'); super(url, ...args); }
      };
      window.client = new UnderTest.AgentWsClient('127.0.0.1', port);
      window.events = [];
      client.onEvent(event => events.push(event));
      window.disposeUnified = UnderTest.mountUnifiedShell(document.querySelector('#root'), client);
      await client.connect();
    }, ready.port);
    await page.waitForFunction(() => events.some(event => event.type === 'session.banner' && event.session_id === 'synthetic-session'));

    const recordsFile = path.join(root, 'data', 'sessions', 'synthetic-session', 'work-records.v1.json');
    assert.equal(existsSync(recordsFile), false, 'fixture starts without persisted records');

    await page.locator('.unified-topbar-menu-trigger').click();
    await page.locator('#unified-auto-recognition-enable').click();
    await page.waitForFunction(() => events.some(event => event.type === 'work-record.auto-recognition.done' && event.action === 'enable' && event.status === 'ok' && event.enabled));

    await page.locator('#unified-input').fill('合成拒绝任务');
    await page.locator('#unified-send').click();
    await page.waitForFunction(() => events.some(event => event.type === 'confirm.request' && event.confirm_subtype === 'work_record_auto_candidate'));
    const candidate = page.locator('[data-work-record-auto-candidate]');
    assert.match(await candidate.textContent(), /合成拒绝事项/);
    await candidate.locator('[data-confirm="n"]').click();
    await page.waitForFunction(() => events.some(event => event.type === 'confirm.done' && event.confirm_subtype === 'work_record_auto_candidate' && event.choice === 'n'));
    await page.waitForFunction(() => events.some(event => event.type === 'work-record.auto-recognition.result' && event.status === 'storage_not_authorized'));
    assert.equal(existsSync(recordsFile), false, 'rejecting the local-save confirmation creates no record file');

    const eventCount = await page.evaluate(() => events.length);
    await page.locator('#unified-input').fill('合成闲聊');
    await page.locator('#unified-send').click();
    await page.waitForFunction((start) => events.slice(start).some(event => event.type === 'work-record.auto-recognition.result' && event.status === 'not_recorded'), eventCount);
    const afterChatEvents = await page.evaluate(start => events.slice(start), eventCount);
    assert.equal(afterChatEvents.filter(event => event.type === 'confirm.request' && event.confirm_subtype === 'work_record_auto_candidate').length, 0,
      'chat produces no candidate-confirmation prompt');
    assert.equal(existsSync(recordsFile), false, 'chat creates no record file');
    assert.deepEqual(errors, []);
    await page.evaluate(() => { disposeUnified(); client.destroy(); });
    await context.close();
    console.log(`synthetic isolation verified; random localhost WS port=${ready.port}; root=${root}`);
  } finally {
    if (browser) await browser.close();
    child.stdin.end('\n');
    await new Promise(resolve => {
      if (child.exitCode !== null) return resolve();
      child.once('exit', resolve);
      setTimeout(() => { child.kill(); resolve(); }, 3000);
    });
    if (stderr) console.log('fixture diagnostics:', stderr.slice(-2000));
    console.log('synthetic fixture root retained:', root);
  }
});
