import assert from 'node:assert/strict';
import test from 'node:test';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const desktop = fileURLToPath(new URL('../', import.meta.url));
const core = path.resolve(desktop, '../agent-core');
const python = process.env.SYNTHETIC_PYTHON || 'F:/my-agent-main/.venv/Scripts/python.exe';
const playwright = process.env.CODEX_PLAYWRIGHT_PATH || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const edge = process.env.ISOLATED_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const optIn = process.env.MY_AGENT_AUTO_RECOGNITION_LIVE_OPTIN === '1';

function nextLine(lines, timeoutMs = 10000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('fixture line timeout')), timeoutMs);
    lines.once('line', (line) => { clearTimeout(timer); resolve(line); });
  });
}

async function fixtureStatus(child, lines) {
  child.stdin.write('verify\n');
  return JSON.parse(await nextLine(lines, 5000));
}
test('isolated live fixture handshake is loopback and confirms zero provider calls before user input', {
  timeout: 20000,
  skip: !existsSync(python) ? 'isolated Python runtime unavailable' : false,
}, async () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'synthetic-auto-live-handshake-'));
  const child = spawn(python, [path.join(desktop, 'tests/auto-recognition-local-live-fixture.py'), root, core, os.tmpdir()], {
    cwd: desktop,
    env: {
      SYSTEMROOT: process.env.SYSTEMROOT, WINDIR: process.env.WINDIR, PATH: process.env.PATH,
      PYTHONDONTWRITEBYTECODE: '1', PYTHONNOUSERSITE: '1',
      HOME: root, USERPROFILE: root, APPDATA: root, LOCALAPPDATA: root, TEMP: root, TMP: root,
    },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  const lines = createInterface({ input: child.stdout });
  try {
    const ready = JSON.parse(await nextLine(lines));
    assert.ok(Number.isInteger(ready.port) && ready.port > 0 && ready.port !== 5173 && ready.port !== 5174);
    assert.equal(ready.session_id, 'synthetic-auto-live-session');
    child.stdin.write('verify\n');
    const status = JSON.parse(await nextLine(lines));
    assert.equal(status.model_calls, 0);
  } finally {
    child.stdin.end('\n');
    await new Promise((resolve) => {
      if (child.exitCode !== null) return resolve();
      child.once('exit', resolve);
      setTimeout(() => { child.kill(); resolve(); }, 3000);
    });
    const resolvedRoot = path.resolve(root);
    assert.ok(resolvedRoot.startsWith(path.resolve(os.tmpdir()) + path.sep));
    rmSync(resolvedRoot, { recursive: true, force: true });
  }
});
test('opt-in: mounted unified UI -> isolated real WsSessionHandler -> one real 0x567 gpt-6-luna recognition -> confirmed synthetic save -> overview', {
  timeout: 90000,
  skip: !optIn ? 'explicit synthetic live opt-in required' : (!existsSync(python) || !existsSync(playwright) || !existsSync(edge))
    ? 'isolated Python, Playwright, or Edge runtime unavailable' : false,
}, async () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'synthetic-auto-live-'));
  const env = {
    SYSTEMROOT: process.env.SYSTEMROOT,
    WINDIR: process.env.WINDIR,
    PATH: process.env.PATH,
    PYTHONDONTWRITEBYTECODE: '1',
    PYTHONNOUSERSITE: '1',
    HOME: root,
    USERPROFILE: root,
    APPDATA: root,
    LOCALAPPDATA: root,
    TEMP: root,
    TMP: root,
  };
  const child = spawn(python, [path.join(desktop, 'tests/auto-recognition-local-live-fixture.py'), root, core, os.tmpdir(), '--allow-real-provider'], {
    cwd: desktop,
    env,
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  child.stderr.on('data', () => {});
  let browser;
  let context;
  let lines;
  try {
    lines = createInterface({ input: child.stdout });
    const ready = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('isolated live fixture startup timed out')), 15000);
      lines.once('line', (line) => {
        clearTimeout(timer);
        try { resolve(JSON.parse(line)); } catch { reject(new Error('isolated fixture handshake invalid')); }
      });
      child.once('exit', () => {
        clearTimeout(timer);
        reject(new Error('isolated live fixture exited before handshake'));
      });
    });
    assert.ok(Number.isInteger(ready.port) && ready.port > 0 && ![5173, 5174, 8000, 8765].includes(ready.port));
    assert.equal(ready.session_id, 'synthetic-auto-live-session');

    const bundle = await require('esbuild').build({
      stdin: {
        contents: 'export {mountUnifiedShell} from "./src/shells/unified/index.ts"; export {AgentWsClient} from "./src/api/ws.ts";',
        resolveDir: desktop,
        loader: 'ts',
      },
      bundle: true,
      write: false,
      platform: 'browser',
      format: 'iife',
      globalName: 'UnderTest',
      define: { 'import.meta.url': '"http://127.0.0.1/isolated.js"' },
      loader: { '.png': 'dataurl', '.jpg': 'dataurl', '.svg': 'dataurl', '.woff2': 'dataurl' },
      plugins: [{ name: 'no-css', setup(build) { build.onLoad({ filter: /\.css$/ }, () => ({ contents: '', loader: 'js' })); } }],
      logLevel: 'silent',
    });

    browser = await require(playwright).chromium.launch({
      headless: true,
      executablePath: edge,
      env: { SYSTEMROOT: process.env.SYSTEMROOT, WINDIR: process.env.WINDIR, PATH: process.env.PATH, TEMP: root, TMP: root, HOME: root, USERPROFILE: root, APPDATA: root, LOCALAPPDATA: root },
      args: [
        '--disable-background-networking', '--disable-features=LocalNetworkAccessChecks,LocalNetworkAccessRestrictions,BlockInsecurePrivateNetworkRequests',
        '--disable-component-update', '--disable-sync', '--no-first-run',
        '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1',
      ],
    });
    context = await browser.newContext({ serviceWorkers: 'block', viewport: { width: 1100, height: 800 } });
    const origin = `http://127.0.0.1:${ready.port}`;
    const blockedSameOrigin = [];
    const blockedExternal = [];
    const errors = [];
    await context.route('**/*', (route) => {
      if (route.request().url() === `${origin}/`) {
        return route.fulfill({
          status: 200,
          contentType: 'text/html',
          body: `<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src ws://127.0.0.1:${ready.port}; img-src data:"><div id="root"></div>`,
        });
      }
      const requestedUrl = route.request().url();
      if (requestedUrl.startsWith(origin + '/')) blockedSameOrigin.push(requestedUrl);
      else blockedExternal.push(requestedUrl);
      return route.abort();
    });
    const page = await context.newPage();
    page.on('pageerror', (error) => errors.push(error.message));
    page.setDefaultTimeout(12000);
    await page.goto(`${origin}/`);
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    await page.evaluate((port) => {
      window.myAgentDesktop = { setBilibiliViewState: async () => {}, setShellDevViewState: async () => {} };
      const NativeWebSocket = window.WebSocket;
      window.WebSocket = class extends NativeWebSocket {
        constructor(url, ...args) {
          if (url !== `ws://127.0.0.1:${port}`) throw new Error('non-loopback WebSocket blocked');
          super(url, ...args);
        }
      };
      window.client = new UnderTest.AgentWsClient('127.0.0.1', port);
      window.events = [];
      window.client.onEvent((event) => window.events.push(event));
      window.disposeUnified = UnderTest.mountUnifiedShell(document.querySelector('#root'), window.client);
      return window.client.connect();
    }, ready.port);

    await page.waitForFunction(() => events.some((event) => event.type === 'session.banner' && event.session_id === 'synthetic-auto-live-session'));
    await page.locator('.unified-topbar-menu-trigger').click();
    await page.locator('#unified-auto-recognition-enable').click();
    await page.waitForFunction(() => events.some((event) => event.type === 'work-record.auto-recognition.done' && event.action === 'enable' && event.status === 'ok' && event.enabled));
    const syntheticMessage = '\u8bf7\u628a\u5408\u6210\u7b80\u5386\u4e2d\u7684\u4e2a\u4eba\u4ecb\u7ecd\u7cbe\u7b80\u4e3a\u4e09\u53e5\u8bdd\u3002\u8fd9\u662f\u4e00\u4e2a\u72ec\u7acb\u4e8b\u9879\u3002';
    await page.locator('#unified-input').fill(syntheticMessage);
    await page.locator('#unified-send').click();

    try {
      await page.waitForFunction(() => events.some((event) =>
        event.type === 'confirm.request' ||
        (event.type === 'work-record.auto-recognition.result' && event.status !== 'recorded')
      ), null, { timeout: 40000 });
    } catch (error) {
      child.stdin.write('verify\n');
      const diagnostic = await nextLine(lines, 5000);
      console.log('isolated synthetic diagnostics', JSON.stringify(await page.evaluate(() => ({
        events: events.map((event) => ({
          type: event.type, status: event.status, action: event.action,
          confirm_kind: event.confirm_kind, confirm_subtype: event.confirm_subtype,
        })),
      }))), diagnostic);
      throw error;
    }
    let recognitionState = await page.evaluate(() => ({
      statuses: events.filter((event) => event.type === 'work-record.auto-recognition.result').map((event) => event.status),
      confirmations: events.filter((event) => event.type === 'confirm.request'),
    }));
    if (recognitionState.statuses.length) {
      const status = await fixtureStatus(child, lines);
      throw new Error(`recognition ended without a candidate (status=${recognitionState.statuses.at(-1)}, provider_calls=${status.model_calls})`);
    }
    const outboundConfirmation = recognitionState.confirmations.find((event) =>
      event.confirm_kind === undefined && event.confirm_subtype === undefined
    );
    let candidateConfirmation = recognitionState.confirmations.find((event) =>
      event.confirm_kind === 'work_record_post_turn' && event.confirm_subtype === 'work_record_auto_candidate'
    );
    if (outboundConfirmation && !candidateConfirmation) {
      const sameSession = outboundConfirmation.session_id === undefined || outboundConfirmation.session_id === 'synthetic-auto-live-session';
      child.stdin.write(JSON.stringify({ op: 'validate_preview', preview: outboundConfirmation.preview }) + '\n');
      const previewGate = JSON.parse(await nextLine(lines, 5000));
      if (!sameSession || previewGate.preview_allowed !== true) {
        const status = await fixtureStatus(child, lines);
        throw new Error(`model-request confirmation rejected (provider_calls=${status.model_calls})`);
      }
      const yesButtons = page.locator('[data-confirm="y"]');
      assert.equal(await yesButtons.count(), 1, 'only the exact synthetic model-request confirmation may be accepted');
      await yesButtons.click();
      await page.waitForFunction(() => events.some((event) =>
        (event.type === 'confirm.request' && event.confirm_subtype === 'work_record_auto_candidate') ||
        (event.type === 'work-record.auto-recognition.result' && event.status !== 'recorded')
      ), null, { timeout: 40000 });
      recognitionState = await page.evaluate(() => ({
        statuses: events.filter((event) => event.type === 'work-record.auto-recognition.result').map((event) => event.status),
        confirmations: events.filter((event) => event.type === 'confirm.request'),
      }));
      if (recognitionState.statuses.length) {
        const status = await fixtureStatus(child, lines);
        throw new Error(`authorized model request returned no candidate (status=${recognitionState.statuses.at(-1)}, provider_calls=${status.model_calls})`);
      }
      candidateConfirmation = recognitionState.confirmations.find((event) =>
        event.confirm_kind === 'work_record_post_turn' && event.confirm_subtype === 'work_record_auto_candidate'
      );
    }
    if (!candidateConfirmation) {
      const status = await fixtureStatus(child, lines);
      throw new Error(`no pending synthetic candidate confirmation (provider_calls=${status.model_calls})`);
    }
    const candidate = candidateConfirmation.work_record_candidate;
    assert.match(candidate.title, /\u7b80\u5386/);
    assert.match(candidate.requirement, /\u4e2a\u4eba\u4ecb\u7ecd/);
    assert.match(candidate.requirement, /\u4e09\u53e5\u8bdd/);
    assert.deepEqual(candidate.scope, { kind: 'independent', object_ref: null });
    assert.equal(candidate.session_id, 'synthetic-auto-live-session');
    const candidateCard = page.locator('[data-work-record-auto-candidate]');
    await candidateCard.waitFor({ state: 'visible', timeout: 12000 });
    const candidateCardText = await candidateCard.textContent();
    assert.match(candidateCardText, /\u7b80\u5386/);
    assert.match(candidateCardText, /\u4e2a\u4eba\u4ecb\u7ecd/);
    assert.match(candidateCardText, /\u4e09\u53e5\u8bdd/);

    const verification = await fixtureStatus(child, lines);
    console.log('LIVE_SYNTHETIC_CANDIDATE=' + JSON.stringify({
      model_guard_entries: verification.model_calls, http_posts: verification.http_posts,
      model_request_confirmation: outboundConfirmation ? 'explicit' : 'server_bound_permit',
      candidate_confirmation: { kind: candidateConfirmation.confirm_kind, subtype: candidateConfirmation.confirm_subtype },
      candidate_present: true,
    }));
    assert.equal(verification.model_calls, 1);
    assert.equal(verification.http_posts, 1, 'exactly one synthetic HTTP request must be dispatched');
    assert.equal(await candidateCard.locator('[data-confirm="y"]').count(), 1);
    // Only the verified candidate card is authorized, never a generic confirmation.
    await candidateCard.locator('[data-confirm="y"]').click();
    await page.waitForFunction(() => events.some((event) =>
      event.type === 'work-record.auto-recognition.result' && event.status === 'recorded'
    ), null, { timeout: 12000 });
    console.log('LIVE_SYNTHETIC_SAVE=' + JSON.stringify({ status: 'recorded', candidate_confirmation_accepted: true }));
    assert.match(await page.locator('#unified-chat').textContent(), /工作记录已保存/);
    const overview = page.locator('#unified-request-current-work');
    await overview.waitFor({ state: 'visible', timeout: 5000 });
    await overview.click();
    await page.waitForFunction((title) => document.querySelector('#unified-current-work-popover')?.textContent?.includes(title), candidate.title, { timeout: 12000 });
    assert.deepEqual(errors, []);
    assert.deepEqual(blockedExternal, []);
    const finalStatus = await fixtureStatus(child, lines);
    assert.equal(finalStatus.model_calls, 1);
    assert.equal(finalStatus.http_posts, 1);
    console.log('LIVE_SYNTHETIC_ACCEPTANCE=' + JSON.stringify({
      model: 'gpt-6-luna', endpoint: 'api-cdn.0x567.com',
      model_guard_entries: finalStatus.model_calls, http_posts: finalStatus.http_posts,
      recognition_status: 'recorded', overview_contains_candidate: true,
      candidate_confirmation: { kind: candidateConfirmation.confirm_kind, subtype: candidateConfirmation.confirm_subtype, accepted: true },
      random_loopback_ws: true, external_browser_requests_blocked: true,
    }));

    await page.evaluate(() => { disposeUnified(); client.destroy(); });

  } finally {
    // Report counts even when an assertion fails before the success log.
    if (lines && child.exitCode === null && !child.killed) {
      try { console.log('LIVE_SYNTHETIC_FINAL_COUNTS=' + JSON.stringify(await fixtureStatus(child, lines))); }
      catch { console.log('LIVE_SYNTHETIC_FINAL_COUNTS=unavailable'); }
    }
    if (context) await context.close();
    if (browser) await browser.close();
    child.stdin.end('\n');
    await new Promise((resolve) => {
      if (child.exitCode !== null) return resolve();
      child.once('exit', resolve);
      setTimeout(() => { child.kill(); resolve(); }, 4000);
    });
    const resolvedRoot = path.resolve(root);
    assert.ok(resolvedRoot.startsWith(path.resolve(os.tmpdir()) + path.sep));
    rmSync(resolvedRoot, { recursive: true, force: true });
  }
});

















