import assert from 'node:assert/strict';
import { existsSync, lstatSync, readFileSync, realpathSync, readdirSync, mkdirSync, mkdtempSync, renameSync, rmSync, rmdirSync, unlinkSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { randomUUID } from 'node:crypto';
import { test } from 'node:test';
import { resolveH1HostCwd } from '../scripts/verify-evolution-a.mjs';
import {
  assertCurrentSessionOverviewRoute,
  assertExistingH1UserDirectories,
  selectSealedRendererTarget,
  summarizeH1CdpEvents,
  waitForH1Workbench,
  assertEmptyH1Data,
  assertGuiExchangeEvidence,
  waitForH1GuiTurnEnd,
  buildIsolatedGuiEnvironment,
  assertLoopbackUrl,
  assertRunLocalRenderer,
  assertSealedAgentRootMarker,
  verifyOrdinaryChatH1AgentPaths,
  h1ComposerSendKeyboardEvents,
  summarizeH1GuiFrames,
  requireOrdinaryChatH1GuiOptIn,
  runOrdinaryChatH1,
} from '../scripts/run-ordinary-chat-h1.mjs';

function identityOf(stat) {
  if (stat.isSymbolicLink() || stat.dev === 0n || stat.ino === 0n || stat.birthtimeNs === 0n) throw new Error('filesystem identity is unavailable or linked');
  return { dev: stat.dev.toString(), ino: stat.ino.toString(), birthtimeNs: stat.birthtimeNs.toString() };
}

function assertSameIdentity(actual, expected, label) {
  const now = identityOf(lstatSync(actual, { bigint: true }));
  assert.deepEqual(now, expected, label + ' identity changed');
}

function assertNoLinks(root, current = root) {
  for (const name of readdirSync(current)) {
    const child = path.join(current, name);
    const stat = lstatSync(child);
    if (stat.isSymbolicLink()) throw new Error('refusing recursive cleanup across a link');
    if (stat.isDirectory()) assertNoLinks(root, child);
    else if (!stat.isFile()) throw new Error('refusing recursive cleanup of a non-regular entry');
    const relative = path.relative(root, child);
    if (!relative || relative === '..' || relative.startsWith('..' + path.sep) || path.isAbsolute(relative)) throw new Error('cleanup entry escaped its owned root');
  }
}

function createOwnedTempRoot(prefix, requestedParent = os.tmpdir()) {
  const parent = path.resolve(realpathSync.native(requestedParent));
  const parentStat = lstatSync(parent, { bigint: true });
  const parentIdentity = identityOf(parentStat);
  const root = mkdtempSync(path.join(parent, prefix));
  const canonicalRoot = realpathSync.native(root);
  if (canonicalRoot !== root || path.dirname(root) !== parent) throw new Error('mkdtemp root is not a canonical direct child of its expected parent');
  const ownershipId = randomUUID();
  const markerPath = path.join(root, '.ordinary-chat-h1-test-owned.json');
  const markerValue = JSON.stringify({ format: 1, ownership_id: ownershipId }) + '\n';
  writeFileSync(markerPath, markerValue, { flag: 'wx' });
  return { root, parent, parentIdentity, rootIdentity: identityOf(lstatSync(root, { bigint: true })), markerPath, markerIdentity: identityOf(lstatSync(markerPath, { bigint: true })), markerValue };
}

function assertOwnedTempRoot(owned) {
  if (!owned || typeof owned.root !== 'string' || typeof owned.parent !== 'string' || path.dirname(owned.root) !== owned.parent || path.resolve(owned.root) !== owned.root) throw new Error('cleanup root is not the expected direct child');
  if (realpathSync.native(owned.parent) !== owned.parent) throw new Error('expected temp parent is not canonical');
  assertSameIdentity(owned.parent, owned.parentIdentity, 'expected temp parent');
  if (realpathSync.native(owned.root) !== owned.root) throw new Error('cleanup root is not canonical');
  assertSameIdentity(owned.root, owned.rootIdentity, 'cleanup root');
  const markerStat = lstatSync(owned.markerPath, { bigint: true });
  if (!markerStat.isFile() || markerStat.isSymbolicLink()) throw new Error('ownership marker is not a regular file');
  assertSameIdentity(owned.markerPath, owned.markerIdentity, 'ownership marker');
  if (readFileSync(owned.markerPath, 'utf8') !== owned.markerValue) throw new Error('ownership marker content changed');
  assertNoLinks(owned.root);
  return true;
}

function cleanupIfStillOwned(owned) {
  if (!existsSync(owned.root)) return;
  try { removeOwnedTempRoot(owned); }
  catch { console.error('Preserving unverified temporary fixture: ' + owned.root); }
}

function removeOwnedTempRoot(owned) {
  assertOwnedTempRoot(owned);
  // Recheck immediately before recursive removal; on uncertainty, preserve the tree.
  assertOwnedTempRoot(owned);
  rmSync(owned.root, { recursive: true, force: false });
}

const SANDBOX = path.resolve(import.meta.dirname, '../..');
const QUERY = '当前会话的工作概况';

test('GUI H1 needs both the ordinary H1 opt-in and a separate GUI opt-in', () => {
  assert.throws(() => requireOrdinaryChatH1GuiOptIn({ REAL_ELECTRON_CHAT_H1_OPT_IN: '1' }), /REAL_ELECTRON_CHAT_H1_GUI_OPT_IN=1/);
  assert.throws(() => requireOrdinaryChatH1GuiOptIn({ REAL_ELECTRON_CHAT_H1_GUI_OPT_IN: '1' }), /REAL_ELECTRON_CHAT_H1_OPT_IN=1/);
  assert.doesNotThrow(() => requireOrdinaryChatH1GuiOptIn({ REAL_ELECTRON_CHAT_H1_OPT_IN: '1', REAL_ELECTRON_CHAT_H1_GUI_OPT_IN: '1' }));
});

test('deterministic query requires the enabled local recall route and explicit-session guard', () => {
  const agent = readFileSync(path.join(SANDBOX, 'agent-core', 'agent.py'), 'utf8');
  const server = readFileSync(path.join(SANDBOX, 'agent-core', 'server.py'), 'utf8');
  assert.doesNotThrow(() => assertCurrentSessionOverviewRoute(agent, server));
  assert.throws(() => assertCurrentSessionOverviewRoute(agent.replace('if self.enable_work_record_recall:', 'if False:'), server), /recall route/);
  assert.throws(() => assertCurrentSessionOverviewRoute(agent.replace('explicit_session = "当前会话" in user_text or "本会话" in user_text', 'explicit_session = False'), server), /explicit-session guard/);
});

test('only loopback HTTP and WebSocket endpoints are accepted', () => {
  assert.equal(assertLoopbackUrl('http://127.0.0.1:4179/json/list', 'http:'), 'http://127.0.0.1:4179/json/list');
  assert.equal(assertLoopbackUrl('ws://localhost:9321/devtools/page/id', 'ws:'), 'ws://localhost:9321/devtools/page/id');
  for (const url of ['http://example.com:4179/', 'http://127.0.0.1.evil:4179/', 'http://192.168.1.2:4179/', 'file:///tmp/index.html']) {
    assert.throws(() => assertLoopbackUrl(url), /loopback/);
  }
});

test('GUI success requires one browser WebSocket request, a bound session, persisted pair, and rendered turns', () => {
  const requestId = '123e4567-e89b-12d3-a456-426614174000';
  const wsRequestId = 'renderer-ws-1';
  const reply = '范围提示：以下仅覆盖当前会话，不代表所有项目。\n当前会话侧车没有已记录事项；这不代表相关工作没有发生。未修改工作记录数据；读取可能创建 .work-records.lock。';
  const frame = (payload) => ({ url: 'ws://127.0.0.1:8766/', websocketRequestId: wsRequestId, payload: JSON.stringify(payload) });
  const good = assertGuiExchangeEvidence({
    query: QUERY,
    sentFrames: [frame({ type: 'user.message', text: QUERY, request_id: requestId })],
    receivedFrames: [frame({ type: 'session.banner', session_id: 'isolated-session-1' }), frame({ type: 'assistant.done', text: reply }), frame({ type: 'turn.end', ok: true, finish_reason: 'work_record_overview' })],
    sessionId: 'isolated-session-1', persistedMessages: [{ role: 'user', content: QUERY }, { role: 'assistant', content: reply }],
    renderedUserText: QUERY, renderedAssistantText: reply,
  });
  assert.equal(good.finish_reason, 'work_record_overview');
  assert.equal(good.request_id, requestId);
  assert.equal(good.session_id, 'isolated-session-1');
  assert.equal(good.persisted_same_session_pair, true);
  assert.throws(() => assertGuiExchangeEvidence({ query: QUERY, sentFrames: [], receivedFrames: [], sessionId: 'isolated-session-1', persistedMessages: [], renderedUserText: QUERY, renderedAssistantText: reply }), /exactly one identified user.message/);
  assert.throws(() => assertGuiExchangeEvidence({ query: QUERY, sentFrames: [frame({ type: 'command', text: QUERY })], receivedFrames: [], sessionId: 'isolated-session-1', persistedMessages: [], renderedUserText: QUERY, renderedAssistantText: reply }), /exactly one identified user.message/);
  assert.throws(() => assertGuiExchangeEvidence({ query: QUERY, sentFrames: [frame({ type: 'user.message', text: QUERY, request_id: requestId })], receivedFrames: [frame({ type: 'session.banner', session_id: 'other-session' }), frame({ type: 'assistant.done', text: reply }), frame({ type: 'turn.end', ok: true, finish_reason: 'work_record_overview' })], sessionId: 'isolated-session-1', persistedMessages: [{ role: 'user', content: QUERY }, { role: 'assistant', content: reply }], renderedUserText: QUERY, renderedAssistantText: reply }), /session identity/);
  assert.throws(() => assertGuiExchangeEvidence({ query: QUERY, sentFrames: [frame({ type: 'user.message', text: QUERY, request_id: requestId })], receivedFrames: [frame({ type: 'session.banner', session_id: 'isolated-session-1' }), { ...frame({ type: 'assistant.done', text: reply }), websocketRequestId: 'different-socket' }, frame({ type: 'turn.end', ok: true, finish_reason: 'work_record_overview' })], sessionId: 'isolated-session-1', persistedMessages: [{ role: 'user', content: QUERY }, { role: 'assistant', content: reply }], renderedUserText: QUERY, renderedAssistantText: reply }), /one Electron WebSocket/);
  assert.throws(() => assertGuiExchangeEvidence({ query: QUERY, sentFrames: [frame({ type: 'user.message', text: QUERY, request_id: requestId })], receivedFrames: [frame({ type: 'session.banner', session_id: 'isolated-session-1' }), frame({ type: 'assistant.done', text: reply }), frame({ type: 'turn.end', ok: true, finish_reason: 'work_record_overview' })], sessionId: 'isolated-session-1', persistedMessages: [{ role: 'user', content: QUERY }, { role: 'assistant', content: 'unrelated reply' }], renderedUserText: QUERY, renderedAssistantText: '伪造 DOM' }), /same-session persisted messages/);
});

test('GUI observation waits for a late same-WebSocket turn.end after the reply is visible', async () => {
  const requestId = '123e4567-e89b-12d3-a456-426614174000';
  const socketId = 'renderer-ws-async';
  const reply = '范围提示：以下仅覆盖当前会话，不代表所有项目。当前会话侧车没有已记录事项。未修改工作记录数据。';
  const sentFrames = [{ payload: { type: 'user.message', text: QUERY, request_id: requestId }, websocketRequestId: socketId, sequence: 10 }];
  const receivedFrames = [
    { payload: { type: 'session.banner', session_id: 'isolated-session-1' }, websocketRequestId: socketId, sequence: 8 },
    { payload: { type: 'assistant.done', text: reply }, websocketRequestId: socketId, sequence: 12 },
  ];
  const source = readFileSync(path.join(SANDBOX, 'desktop', 'scripts', 'run-ordinary-chat-h1.mjs'), 'utf8');
  const start = source.indexOf('async function exerciseRealComposer');
  const visible = source.indexOf("if (!domState?.user_query_visible || !domState?.assistant_reply_visible)", start);
  const wait = source.indexOf('await waitForH1GuiTurnEnd', start);
  const strict = source.indexOf('assertGuiExchangeEvidence({', wait);
  assert.ok(start >= 0 && visible < wait && wait < strict, 'wait starts only after rendering and precedes the unchanged strict assertion');

  const pending = waitForH1GuiTurnEnd(() => ({ sentFrames, receivedFrames }), { query: QUERY, timeoutMs: 250, pollIntervalMs: 5 });
  setTimeout(() => receivedFrames.push({ payload: { type: 'turn.end', ok: true, finish_reason: 'work_record_overview' }, websocketRequestId: socketId, sequence: 13 }), 20);
  const frames = await pending;
  assert.doesNotThrow(() => assertGuiExchangeEvidence({ query: QUERY, ...frames, sessionId: 'isolated-session-1', persistedMessages: [{ role: 'user', content: QUERY }, { role: 'assistant', content: reply }], renderedUserText: QUERY, renderedAssistantText: reply }));
  const wrongReason = { ...frames, receivedFrames: frames.receivedFrames.map(frame => frame.payload?.type === 'turn.end' ? { ...frame, payload: { ...frame.payload, finish_reason: 'completed' } } : frame) };
  assert.throws(() => assertGuiExchangeEvidence({ query: QUERY, ...wrongReason, sessionId: 'isolated-session-1', persistedMessages: [{ role: 'user', content: QUERY }, { role: 'assistant', content: reply }], renderedUserText: QUERY, renderedAssistantText: reply }), /did not confirm deterministic work_record_overview route/);
});
test('GUI H1 rejects legacy desktop/dist and accepts only the run-local verified renderer build', () => {
  const runRoot = path.join(SANDBOX, 'artifacts', 'ordinary-chat-h1', 'run-test');
  assert.throws(() => assertRunLocalRenderer({ build_provenance: 'legacy-desktop-dist-renderer' }, path.join(runRoot, 'host', 'app', 'desktop', 'dist'), runRoot), /run-local/);
  assert.doesNotThrow(() => assertRunLocalRenderer({ build_provenance: 'run-local-vite-renderer-and-esbuild-main-preload; renderer-source-and-sealed-output-hashes-verified' }, path.join(runRoot, 'host', 'app', 'desktop', 'dist'), runRoot));
  assert.throws(() => assertRunLocalRenderer({ build_provenance: 'run-local-vite-renderer-and-esbuild-main-preload; renderer-source-and-sealed-output-hashes-verified' }, path.resolve(SANDBOX, 'desktop', 'dist'), runRoot), /run-local/);
});

test('GUI H1 requires empty isolated data, session, and credential/profile roots', t => {
  const owned = createOwnedTempRoot('ordinary-chat-h1-empty-');
  const root = owned.root;
  assert.equal(path.dirname(root), realpathSync.native(os.tmpdir()), 'owned root must be a direct temp-root child');
  t.after(() => removeOwnedTempRoot(owned));
  const app = path.join(root, 'app'), data = path.join(app, 'data'), sessions = path.join(data, 'sessions'), profile = path.join(root, 'profile');
  mkdirSync(sessions, { recursive: true }); mkdirSync(profile);
  for (const name of ['userprofile', 'home', 'tmp', 'xdg-cache', 'xdg-config', 'xdg-data', 'electron-userData', 'electron-sessionData']) mkdirSync(path.join(profile, name));
  mkdirSync(path.join(profile, 'appdata', 'Local'), { recursive: true }); mkdirSync(path.join(profile, 'appdata', 'Roaming'), { recursive: true });
  assert.doesNotThrow(() => assertEmptyH1Data(app, profile));
  writeFileSync(path.join(sessions, 'messages.jsonl'), 'must fail closed');
  assert.throws(() => assertEmptyH1Data(app, profile), /must be empty/);
});

test('GUI child receives isolated profile and temp paths, never caller credential values', () => {
  const env = buildIsolatedGuiEnvironment({ SYSTEMROOT: 'C:/Windows', PATH: 'C:/Windows/System32', APPDATA: 'C:/Users/secret/AppData/Roaming', USERPROFILE: 'C:/Users/secret' }, { profile: 'F:/run/host/profile', runRoot: 'F:/run', agentRoot: 'F:/run/host/app', userProfile: 'F:/run/host/profile/userprofile', localAppData: 'F:/run/host/profile/appdata/Local', appData: 'F:/run/host/profile/appdata/Roaming', home: 'F:/run/host/profile/home', xdgConfigHome: 'F:/run/host/profile/xdg-config', xdgDataHome: 'F:/run/host/profile/xdg-data', xdgCacheHome: 'F:/run/host/profile/xdg-cache', temp: 'F:/run/host/profile/tmp' });
  assert.equal(env.APPDATA, 'F:/run/host/profile/appdata/Roaming');
  assert.equal(env.USERPROFILE, 'F:/run/host/profile/userprofile');
  assert.equal(env.TEMP, 'F:/run/host/profile/tmp');
  assert.equal(env.MY_AGENT_ROOT, path.resolve('F:/run/host/app'));
  assert.equal(env.PYTHONNOUSERSITE, '1');
  assert.equal(env.MY_AGENT_ROOT, 'F:\\run\\host\\app');
  assert.equal(env.PYTHONNOUSERSITE, '1');
  assert.equal(Object.hasOwn(env, 'OPENAI_API_KEY'), false);
  assert.throws(() => buildIsolatedGuiEnvironment({ OPENAI_API_KEY: 'never-forward' }, { profile: 'F:\\run\\profile', userProfile: 'F:\\run\\profile\\user-profile', localAppData: 'F:\\run\\profile\\local-app-data', appData: 'F:\\run\\profile\\app-data', temp: 'F:\\run\\runtime-temp' }), /non-allowlisted/);
});

test('opt-in real Electron GUI composer -> Python user.message -> rendered reply', { skip: process.env.REAL_ELECTRON_CHAT_H1_OPT_IN === '1' && process.env.REAL_ELECTRON_CHAT_H1_GUI_OPT_IN === '1' ? false : 'requires main-agent review and both explicit opt-ins' }, async () => {
  const evidence = await runOrdinaryChatH1({ gui: true });
  assert.equal(evidence.status, 'gui_user_message_roundtrip_verified');
  assert.equal(evidence.gui.exchange.finish_reason, 'work_record_overview');
  assert.equal(evidence.credentials_forwarded, false);
  assert.deepEqual(evidence.credential_environment.credential_env_forwarded_keys, []);
  assert.equal(evidence.model_call_status, 'expected_no_model_call_not_observed');
  assert.equal(evidence.outbound_call_observation, 'not_observed');
  assert.equal(evidence.gui.exchange.persisted_same_session_pair, true);
  assert.equal(evidence.stable_tree_unchanged, true);
  assert.equal(evidence.forced_termination, false);
  assert.ok(evidence.gui.cdp_target_diagnostics);
  assert.ok(evidence.gui.screenshot);
});

test('temporary cleanup preserves roots whose directory or marker identity changed', t => {
  const markerTarget = createOwnedTempRoot('ordinary-chat-h1-marker-target-');
  t.after(() => cleanupIfStillOwned(markerTarget));
  const savedMarker = markerTarget.markerPath + '.original';
  renameSync(markerTarget.markerPath, savedMarker);
  writeFileSync(markerTarget.markerPath, markerTarget.markerValue, { flag: 'wx' });
  assert.throws(() => removeOwnedTempRoot(markerTarget), /marker identity changed/);
  assert.equal(existsSync(markerTarget.root), true, 'changed marker must cause preservation');
  unlinkSync(markerTarget.markerPath);
  renameSync(savedMarker, markerTarget.markerPath);
  removeOwnedTempRoot(markerTarget);
  assert.equal(existsSync(markerTarget.root), false, 'restored original ownership can be safely removed');

  const rootTarget = createOwnedTempRoot('ordinary-chat-h1-root-target-');
  t.after(() => cleanupIfStillOwned(rootTarget));
  const moved = rootTarget.root + '.original';
  renameSync(rootTarget.root, moved);
  mkdirSync(rootTarget.root);
  const replacementMarker = path.join(rootTarget.root, path.basename(rootTarget.markerPath));
  writeFileSync(replacementMarker, rootTarget.markerValue, { flag: 'wx' });
  assert.throws(() => removeOwnedTempRoot(rootTarget), /root identity changed/);
  assert.equal(existsSync(rootTarget.root), true, 'replacement directory must be preserved');
  assert.equal(existsSync(moved), true, 'original owned directory must also be preserved');
  unlinkSync(replacementMarker);
  rmdirSync(rootTarget.root);
  renameSync(moved, rootTarget.root);
  removeOwnedTempRoot(rootTarget);
  assert.equal(existsSync(rootTarget.root), false, 'restored original root can be safely removed');
});

test('H1 Electron cwd is the verified sealed app root, not the caller cwd', t => {
  const owned = createOwnedTempRoot('ordinary-chat-h1-cwd-contract-');
  t.after(() => cleanupIfStillOwned(owned));
  const artifacts = path.join(owned.root, 'artifacts');
  const runRoot = path.join(artifacts, 'ordinary-chat-h1', 'run-fixture');
  mkdirSync(runRoot, { recursive: true });
  writeFileSync(path.join(runRoot, '.evolution-test-run'), 'evolution-a\n', { flag: 'wx' });
  const appRoot = path.join(runRoot, 'host', 'app'); mkdirSync(appRoot, { recursive: true });
  const verified = { sandbox_root: owned.root, run_root: runRoot, app_root: appRoot };
  assert.equal(resolveH1HostCwd(verified), appRoot);
  assert.notEqual(resolveH1HostCwd(verified), process.cwd());
  const outside = path.join(owned.root, 'untrusted-caller-run');
  mkdirSync(outside); writeFileSync(path.join(outside, '.evolution-test-run'), 'evolution-a\n', { flag: 'wx' });
  assert.throws(() => resolveH1HostCwd({ ...verified, run_root: outside }), /sandbox artifacts/);
});

test('CDP candidate selection ignores splash/about:blank and accepts only this run sealed renderer', () => {
  const splash = { type: 'page', title: 'Opening', url: 'file:///sealed/splash.html', webSocketDebuggerUrl: 'ws://127.0.0.1:51000/devtools/page/splash' };
  const blank = { type: 'page', title: '', url: 'about:blank', webSocketDebuggerUrl: 'ws://127.0.0.1:51000/devtools/page/blank' };
  const workbench = { type: 'page', title: 'my-agent', url: 'http://127.0.0.1:57515/', webSocketDebuggerUrl: 'ws://127.0.0.1:51000/devtools/page/workbench', body: 'must never appear in diagnostics' };
  const selected = selectSealedRendererTarget([splash, blank, workbench], { cdpPort: 51000, rendererPort: 57515 });
  assert.equal(selected.target, workbench);
  assert.equal(selected.diagnostics[0].reason, 'non_http_renderer_url');
  assert.equal(selected.diagnostics[1].reason, 'non_http_renderer_url');
  assert.equal(selected.diagnostics[2].reason, 'accepted_sealed_renderer');
  assert.equal(JSON.stringify(selected.diagnostics).includes('must never appear'), false);
  assert.deepEqual(Object.keys(selected.diagnostics[0]).sort(), ['origin', 'path', 'port', 'reason', 'title', 'type']);
  const wrongDebugger = selectSealedRendererTarget([{ ...workbench, webSocketDebuggerUrl: 'ws://example.com:51000/devtools/page/external' }], { cdpPort: 51000, rendererPort: 57515 });
  assert.equal(wrongDebugger.target, null);
  assert.equal(wrongDebugger.diagnostics[0].reason, 'debugger_endpoint_not_bound_to_this_electron');
  const wrongPort = selectSealedRendererTarget([{ ...workbench, url: 'http://127.0.0.1:57516/', title: 'my-agent' }], { cdpPort: 51000, rendererPort: 57515 });
  assert.equal(wrongPort.target, null);
  assert.equal(wrongPort.diagnostics[0].reason, 'renderer_port_mismatch');
  const ambiguous = selectSealedRendererTarget([workbench, { ...workbench, webSocketDebuggerUrl: 'ws://127.0.0.1:51000/devtools/page/duplicate' }], { cdpPort: 51000, rendererPort: 57515 });
  assert.equal(ambiguous.target, null);
  assert.equal(ambiguous.diagnostics.every(item => item.reason === 'ambiguous_sealed_renderer_candidate'), true);
});

test('workbench observer retries transient Runtime.evaluate timeouts without reloading the main loadURL', async () => {
  const calls = []; let attempt = 0;
  const cdp = { command: async method => {
    calls.push(method); assert.equal(method, 'Runtime.evaluate'); attempt += 1;
    if (attempt === 1) { const error = new Error('CDP command timed out: Runtime.evaluate'); error.code = 'cdp_command_timeout'; throw error; }
    const state = attempt === 2 ? { title: '', href: 'http://127.0.0.1:57515/', input: false } : { title: 'my-agent', href: 'http://127.0.0.1:57515/', input: true };
    return { result: { value: state } };
  } };
  const result = await waitForH1Workbench(cdp, 57515, { timeoutMs: 500, pollMs: 0, evaluateTimeoutMs: 1 });
  assert.equal(result.state.input, true);
  assert.equal(result.samples.at(-1).title, 'my-agent');
  assert.equal(result.transient_timeouts, 1);
  assert.equal(calls.includes('Page.reload'), false);
});

test('startup error state and script loading observation keep only safe categories and metadata', async () => {
  const state = { title: 'my-agent', href: 'http://127.0.0.1:57515/', input: false, app_root: { present: true, startup_failure: true, error_category: 'preload_bridge_missing', child_count: 1, shell_root_present: false }, scripts: [{ type: 'module', origin: 'http://127.0.0.1:57515', port: 57515, path: '/assets/index.js', loaded: true }] };
  const cdp = { command: async () => ({ result: { value: state } }) };
  await assert.rejects(waitForH1Workbench(cdp, 57515, { timeoutMs: 100, pollMs: 0 }), error => {
    assert.equal(error.code, 'h1_app_startup_failure');
    assert.equal(error.diagnostics.app_root.error_category, 'preload_bridge_missing');
    assert.equal(JSON.stringify(error.diagnostics).includes('private user text'), false);
    return true;
  });
});

test('CDP runtime summary records sanitized network and JS errors without messages or query strings', () => {
  const summary = summarizeH1CdpEvents([
    { method: 'Network.requestWillBeSent', params: { requestId: 'r1', type: 'Script', request: { url: 'http://127.0.0.1:57515/assets/index.js?private=token' } } },
    { method: 'Network.loadingFailed', params: { requestId: 'r1', type: 'Script', errorText: 'net::ERR_CONNECTION_REFUSED', canceled: false } },
    { method: 'Runtime.exceptionThrown', params: { exceptionDetails: { text: 'Uncaught secret body', url: 'http://127.0.0.1:57515/assets/index.js?private=token', lineNumber: 7, columnNumber: 2, exception: { className: 'TypeError', description: 'secret user message' } } } },
    { method: 'Runtime.consoleAPICalled', params: { type: 'error', args: [{ type: 'string', value: 'secret page body' }], stackTrace: { callFrames: [{ url: 'http://127.0.0.1:57515/assets/index.js?private=token', lineNumber: 8, columnNumber: 1 }] } } },
  ]);
  const json = JSON.stringify(summary);
  assert.equal(json.includes('secret'), false);
  assert.equal(json.includes('private=token'), false);
  assert.equal(summary.failed_resources[0].failure_category, 'connection_refused');
  assert.equal(summary.runtime_exceptions[0].category, 'uncaught_exception');
  assert.equal(summary.console_errors[0].category, 'console_error');
  assert.equal(summary.failed_resources[0].url.path, '/assets/index.js');
});


test('composer dispatch matches the real Shift+Enter handler and preserves frame privacy', () => {
  const source = readFileSync(path.join(SANDBOX, 'desktop', 'src', 'shells', 'unified', 'index.ts'), 'utf8');
  assert.match(source, /ev.key === "Enter" && ev.shiftKey/);
  assert.match(source, /id="unified-send"/);
  assert.deepEqual(h1ComposerSendKeyboardEvents().map(event => [event.type, event.key, event.modifiers]), [['keyDown', 'Enter', 8], ['keyUp', 'Enter', 8]]);
  const sensitive = 'synthetic private text should not be persisted';
  const summary = summarizeH1GuiFrames([
    { method: 'Network.webSocketFrameSent', params: { requestId: 'ws-secret', response: { payloadData: JSON.stringify({ type: 'user.message', request_id: 'req-secret', text: QUERY }) } } },
    { method: 'Network.webSocketFrameReceived', params: { requestId: 'ws-secret', response: { payloadData: JSON.stringify({ type: 'assistant.done', text: sensitive }) } } },
    { method: 'Network.webSocketFrameReceived', params: { requestId: 'ws-secret', response: { payloadData: JSON.stringify({ type: 'turn.end', ok: false, finish_reason: 'error' }) } } },
    { method: 'Network.webSocketFrameReceived', params: { requestId: 'ws-4', response: { payloadData: JSON.stringify({ type: 'error', message: 'SYNTHETIC_PRIVATE_SENTINEL' }) } } },
    { method: 'Network.webSocketFrameReceived', params: { requestId: 'ws-5', response: { payloadData: JSON.stringify({ type: 'error', code: 'managed_readonly_rejected' }) } } },
    { method: 'Network.webSocketFrameReceived', params: { requestId: 'ws-6', response: { payloadData: JSON.stringify({ type: 'error', code: 'SYNTHETIC_UNKNOWN_CODE', token: 'SYNTHETIC_CREDENTIAL_SENTINEL' }) } } },
  ], QUERY);
  const json = JSON.stringify(summary);
  for (const value of [sensitive, 'req-secret', 'ws-secret', QUERY]) assert.equal(json.includes(value), false);
  assert.equal(summary.raw_payloads_written, false);
  assert.deepEqual(summary.ws_error_class_counts, {
    message_only: 1,
    managed_readonly_unavailable: 0,
    managed_readonly_rejected: 1,
    managed_application_rejected: 0,
    run_service_tool_not_found: 0,
    other: 1,
  });
  assert.equal(json.includes('SYNTHETIC_PRIVATE_SENTINEL'), false);
  assert.equal(json.includes('SYNTHETIC_UNKNOWN_CODE'), false);
  assert.equal(json.includes('SYNTHETIC_CREDENTIAL_SENTINEL'), false);
  assert.equal(summary.relevant_frames[0].exact_query, true);
  assert.equal(summary.relevant_frames[1].expected_empty_overview, false);
});

test('WS message errors classify only the exact fixed run-service string without retaining message content', () => {
  const fixed = 'run_service tool not found';
  const privateMessage = 'run_service tool not found: SYNTHETIC_PRIVATE_SENTINEL';
  const summary = summarizeH1GuiFrames([
    { method: 'Network.webSocketFrameReceived', params: { requestId: 'private-ws-id', response: { payloadData: JSON.stringify({ type: 'error', message: fixed }) } } },
    { method: 'Network.webSocketFrameReceived', params: { requestId: 'private-ws-id-2', response: { payloadData: JSON.stringify({ type: 'error', message: privateMessage }) } } },
  ], QUERY);
  assert.equal(summary.ws_error_class_counts.run_service_tool_not_found, 1);
  assert.equal(summary.ws_error_class_counts.message_only, 1);
  const json = JSON.stringify(summary);
  for (const value of [fixed, privateMessage, 'SYNTHETIC_PRIVATE_SENTINEL', 'private-ws-id']) assert.equal(json.includes(value), false);
});
test('runner validates already-created isolated profile directories without recreating them and rejects real-user roots', t => {
  const owned = createOwnedTempRoot('ordinary-chat-h1-existing-profile-');
  t.after(() => cleanupIfStillOwned(owned));
  const profileRoot = path.join(owned.root, 'profile'); mkdirSync(profileRoot);
  const names = ['userprofile', 'local-app-data', 'app-data', 'home', 'xdg-config', 'xdg-data', 'xdg-cache', 'runtime-temp'];
  const roots = names.map(name => { const target = path.join(profileRoot, name); mkdirSync(target); return target; });
  const before = roots.map(target => lstatSync(target, { bigint: true }).ino.toString());
  assert.deepEqual(assertExistingH1UserDirectories(roots, profileRoot), roots);
  assert.deepEqual(roots.map(target => lstatSync(target, { bigint: true }).ino.toString()), before);
  const missing = path.join(profileRoot, 'not-created');
  assert.throws(() => assertExistingH1UserDirectories([missing], profileRoot), /ENOENT/);
  assert.equal(existsSync(missing), false, 'validation must not create missing directories');
  const realUserRoot = path.join(owned.root, 'real-user-profile'); mkdirSync(realUserRoot);
  assert.throws(() => assertExistingH1UserDirectories([realUserRoot], profileRoot), /escaped the isolated H1 profile/);
});test('runner proves sealed AgentPaths before invoking the native controlled Host API', () => {
  const verified = { sandbox_root: 'F:\\sandbox', run_root: 'F:\\sandbox\\artifacts\\ordinary-chat-h1\\run-test', app_root: 'F:\\sandbox\\artifacts\\ordinary-chat-h1\\run-test\\host\\app', profile_root: 'F:\\sandbox\\artifacts\\ordinary-chat-h1\\run-test\\host\\profile', profile: { trusted_python_executable: 'C:\\Python\\python.exe' } };
  let received;
  const proof = verifyOrdinaryChatH1AgentPaths({ verifyH1AgentPaths: value => { received = value; return { agent_root: verified.app_root, data: path.join(verified.app_root, 'data'), workspace: path.join(verified.app_root, 'workspace'), evolve: path.join(verified.app_root, 'evolve') }; } }, verified);
  assert.equal(received.appRoot, verified.app_root); assert.equal(received.pythonExecutable, verified.profile.trusted_python_executable);
  assert.equal(proof.agent_paths_discovery, 'sealed_app_root');
  const roots = { profile: verified.profile_root, agentRoot: verified.app_root, runRoot: verified.run_root, userProfile: path.join(verified.profile_root, 'userprofile'), appData: path.join(verified.profile_root, 'appdata', 'Roaming'), localAppData: path.join(verified.profile_root, 'appdata', 'Local'), home: path.join(verified.profile_root, 'home'), xdgConfigHome: path.join(verified.profile_root, 'xdg-config'), xdgDataHome: path.join(verified.profile_root, 'xdg-data'), xdgCacheHome: path.join(verified.profile_root, 'xdg-cache'), temp: path.join(verified.profile_root, 'tmp') };
  const env = buildIsolatedGuiEnvironment({ PATH: 'C:\\Windows\\System32' }, roots);
  assert.equal(env.MY_AGENT_ROOT, verified.app_root); assert.equal(env.PYTHONNOUSERSITE, '1'); assert.equal(env.HOME, roots.home);
  assert.throws(() => verifyOrdinaryChatH1AgentPaths({ verifyH1AgentPaths: () => ({ agent_root: 'F:\\sandbox', data: '', workspace: '', evolve: '' }) }, verified), /exclusively to the sealed H1 app root/);
  assert.throws(() => verifyOrdinaryChatH1AgentPaths({ verifyH1AgentPaths: () => Promise.resolve({ agent_root: verified.app_root, data: path.join(verified.app_root, 'data'), workspace: path.join(verified.app_root, 'workspace'), evolve: path.join(verified.app_root, 'evolve') }) }, verified), /synchronously before launch/);
  const runner = readFileSync(path.join(SANDBOX, 'desktop', 'scripts', 'run-ordinary-chat-h1.mjs'), 'utf8');
  const entry = runner.indexOf('export async function runOrdinaryChatH1');
  assert.ok(runner.indexOf('verifyOrdinaryChatH1AgentPaths(api, verified)', entry) < runner.indexOf('api.startFrozenHost(', entry), 'Raman Python path proof must precede the native controlled Host API');
  assert.match(runner, /api\.startFrozenHost\(\{ sandboxRoot, runRoot, \.\.\.frozen \}, hostOptions\)/);
  assert.match(runner, /cdp: \{ enabled: true \}/);
  assert.match(runner, /host\.waitForCdp\(\)/);
  assert.doesNotMatch(runner, /createCdpSpawnProcess|spawnProcess\s*=/, 'runner must not wrap or inject process spawning');
});

test('H1 refuses to launch unless the sealed AgentPaths root has its own evolve index marker', t => {
  const runner = readFileSync(path.join(SANDBOX, 'desktop', 'scripts', 'run-ordinary-chat-h1.mjs'), 'utf8');
  const owned = createOwnedTempRoot('ordinary-chat-h1-agent-root-');
  t.after(() => cleanupIfStillOwned(owned));
  const app = path.join(owned.root, 'app'), evolve = path.join(app, 'evolve');
  mkdirSync(evolve, { recursive: true });
  assert.throws(() => assertSealedAgentRootMarker(app), /both index markers/);
  const coreIndex = path.join(evolve, '_index.core.toml');
  writeFileSync(coreIndex, '[core]\n', { flag: 'wx' });
  assert.throws(() => assertSealedAgentRootMarker(app), /both index markers/);
  const legacyIndex = path.join(evolve, '_index.toml');
  writeFileSync(legacyIndex, '[core]\n', { flag: 'wx' });
  assert.deepEqual(assertSealedAgentRootMarker(app), { core_index: coreIndex, legacy_index: legacyIndex });
  const entry = runner.indexOf('export async function runOrdinaryChatH1');
  assert.ok(runner.indexOf('verifyOrdinaryChatH1AgentPaths(api, verified)', entry) < runner.indexOf('api.startFrozenHost(', entry), 'Python root proof must precede Electron/sidecar launch');
  assert.ok(runner.indexOf('assertSealedAgentRootMarker(verified.app_root)') < runner.indexOf('api.startFrozenHost('), 'root marker preflight must precede Electron/sidecar Host launch');
});


test('ordinary chat H1 runner requires both process opt-ins before allocating a marked run', async () => {
  const runBase = path.join(SANDBOX, 'artifacts', 'ordinary-chat-h1');
  const before = existsSync(runBase) ? readdirSync(runBase).sort() : null;
  const savedOptIn = process.env.REAL_ELECTRON_CHAT_H1_OPT_IN;
  const savedGuiOptIn = process.env.REAL_ELECTRON_CHAT_H1_GUI_OPT_IN;
  try {
    delete process.env.REAL_ELECTRON_CHAT_H1_OPT_IN;
    delete process.env.REAL_ELECTRON_CHAT_H1_GUI_OPT_IN;
    await assert.rejects(runOrdinaryChatH1({}), /REAL_ELECTRON_CHAT_H1_OPT_IN=1/);
    process.env.REAL_ELECTRON_CHAT_H1_OPT_IN = '1';
    await assert.rejects(runOrdinaryChatH1({}), /REAL_ELECTRON_CHAT_H1_GUI_OPT_IN=1/);
    assert.deepEqual(existsSync(runBase) ? readdirSync(runBase).sort() : null, before, 'missing opt-ins must not allocate a run');
    await assert.rejects(runOrdinaryChatH1({ env: { REAL_ELECTRON_CHAT_H1_OPT_IN: '1', REAL_ELECTRON_CHAT_H1_GUI_OPT_IN: '1' } }), /real process environment/);
    assert.deepEqual(existsSync(runBase) ? readdirSync(runBase).sort() : null, before, 'injected opt-ins must not allocate a run');
  } finally {
    if (savedOptIn === undefined) delete process.env.REAL_ELECTRON_CHAT_H1_OPT_IN; else process.env.REAL_ELECTRON_CHAT_H1_OPT_IN = savedOptIn;
    if (savedGuiOptIn === undefined) delete process.env.REAL_ELECTRON_CHAT_H1_GUI_OPT_IN; else process.env.REAL_ELECTRON_CHAT_H1_GUI_OPT_IN = savedGuiOptIn;
  }
  const source = readFileSync(path.join(SANDBOX, 'desktop', 'scripts', 'run-ordinary-chat-h1.mjs'), 'utf8');
  const entry = source.indexOf('export async function runOrdinaryChatH1');
  const authorization = source.indexOf('assertOrdinaryChatH1LaunchAuthorized(process.env)', entry);
  const allocation = source.indexOf('const runRoot = newMarkedRun()', entry);
  assert.ok(entry >= 0 && authorization > entry && authorization < allocation, 'both explicit opt-ins must precede run allocation');
  const gateStart = source.indexOf('export function assertOrdinaryChatH1LaunchAuthorized', entry);
  assert.ok(source.indexOf('requireOrdinaryChatH1GuiOptIn(env)', gateStart) > gateStart, 'runner authorization must require both opt-ins');
});
