import assert from 'node:assert/strict';
import { existsSync, lstatSync, mkdtempSync, mkdirSync, readFileSync, readdirSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { test } from 'node:test';
import { assertEvolutionTestProfileLoaded, detectEvolutionTestLaunchIntent } from '../electron/evolution/test-launch-intent.mjs';

const source = readFileSync(fileURLToPath(new URL('../electron/main.ts', import.meta.url)), 'utf8');
function bodyAfter(signature, nextSignature) {
  const start = source.indexOf(signature);
  assert.notEqual(start, -1, `missing ${signature}`);
  const end = source.indexOf(nextSignature, start + signature.length);
  assert.notEqual(end, -1, `missing boundary ${nextSignature}`);
  return source.slice(start, end);
}

test('ordinary-chat H1 only launches the verified profile Python from its sealed app root', () => {
  const python = bodyAfter('function pythonCommand(', '/** DOC-08');
  assert.match(python, /if\s*\(ordinaryChatH1\)[\s\S]*?path\.resolve\(root\)\s*!==\s*context\.app_root/);
  assert.match(python, /return context\.profile\.trusted_python_executable/);
  assert.match(python, /ordinaryChatH1[\s\S]*?throw Error\(["']ordinary-chat H1/);
  assert.match(python, /trustedHost[\s\S]*?process\.platform/);
});

test('ordinary-chat H1 requires private bootstrap and seals the sidecar script and cwd before spawn', () => {
  const start = bodyAfter('function startSidecar(', 'type CandidateSidecar');
  assert.match(start, /ordinaryChatH1[\s\S]*?evolutionTestHostContext!?\.app_root/);
  assert.match(start, /ordinaryChatH1[\s\S]*?selectedBootstrap/);
  assert.match(start, /ordinaryChatH1[\s\S]*?privateLaunch\.privateInput\s*===\s*null/);
  assert.match(start, /path\.join\(evolutionTestHostContext!?\.app_root, "agent-core", "server\.py"\)/);
  assert.match(start, /cwd:\s*ordinaryChatH1\s*\?\s*evolutionTestHostContext!?\.app_root/);
  assert.ok(start.indexOf("privateLaunch.privateInput === null") < start.indexOf("sidecar = spawn("), "bootstrap rejection must precede spawn");
  assert.match(start, /spawn\(pythonCommand\(root, privateLaunch\.privateInput !== null\)/);
});

test('ordinary-chat H1 disables direct candidate switching and CLI switching without changing T4 gates', () => {
  const switchCandidate = bodyAfter('async function switchSidecarCandidate(', 'async function ensureSidecar');
  const switchCli = bodyAfter('async function switchToCli(', 'function registerShortcuts');
  assert.match(switchCandidate, /if\s*\(ordinaryChatH1\)/);
  assert.ok(switchCandidate.indexOf("if (ordinaryChatH1)") < switchCandidate.indexOf("sidecarSupervisor.switchCandidate"));
  assert.match(switchCli, /if\s*\(ordinaryChatH1\)/);
  assert.ok(switchCli.indexOf("if (ordinaryChatH1)") < switchCli.indexOf("setPreferredUi("));
  assert.match(source, /candidate_fixture_profile\s*===\s*"t4-recovery-node-v1"\s*\|\|\s*ordinaryChatH1/);
  assert.match(source, /ordinaryChatH1[\s\S]*?h1Control/);
});

test('marked test-host intent with profile-load failure refuses before Host path setup while ordinary Host remains allowed', t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'h1-intent-offline-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const run = path.join(root, 'artifacts', 'run-non-uuid-fixture');
  const appRoot = path.join(run, 'host', 'app');
  mkdirSync(appRoot, { recursive: true });
  writeFileSync(path.join(run, '.evolution-test-run'), 'malformed marker still signals test intent');
  const intent = detectEvolutionTestLaunchIntent(appRoot, [], {});
  assert.equal(intent, true);
  const wouldBeData = path.join(appRoot, 'data');
  assert.throws(() => assertEvolutionTestProfileLoaded(intent, null, new Error('invalid profile')), /test startup intent detected/);
  assert.equal(existsSync(wouldBeData), false, 'refusal must happen before ordinary Host data creation');

  const ordinaryRoot = path.join(root, 'normal-host'); mkdirSync(ordinaryRoot);
  assert.equal(detectEvolutionTestLaunchIntent(ordinaryRoot, [], {}), false);
  assert.doesNotThrow(() => assertEvolutionTestProfileLoaded(false, null, null));

  const detectAt = source.indexOf('detectEvolutionTestLaunchIntent(AGENT_ROOT');
  const loadAt = source.indexOf('loadEvolutionTestHostProfile(AGENT_ROOT)');
  const rejectAt = source.indexOf('assertEvolutionTestProfileLoaded(evolutionTestLaunchIntent');
  for (const boundary of ['const DATA_ROOT', 'createAdoptionJournal(', 'mkdirSync(', 'app.setPath(', 'app.whenReady()'])
    assert.ok(rejectAt < source.indexOf(boundary), 'profile-load failure gate must precede ' + boundary);
  assert.ok(detectAt >= 0 && detectAt < source.indexOf('loadFixedHostContext('), 'test intent must be detected before Host context/path setup');
  assert.ok(loadAt >= 0 && loadAt < rejectAt, 'failed profile load must be checked before startup proceeds');
});

test('unrelated ancestor profile filenames do not misclassify an ordinary Host as test intent', t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'h1-ordinary-host-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const ordinaryApp = path.join(root, 'release', 'desktop-app');
  const ordinaryArtifactsHost = path.join(root, 'artifacts', 'ordinary-release', 'host', 'app');
  mkdirSync(ordinaryApp, { recursive: true });
  mkdirSync(ordinaryArtifactsHost, { recursive: true });
  for (const name of ['evolution-test-profile.json', '.evolution-test-run', '.ordinary-chat-h1.json']) writeFileSync(path.join(root, name), 'unrelated ancestor file');
  assert.equal(detectEvolutionTestLaunchIntent(ordinaryApp, [], {}), false);
  assert.equal(detectEvolutionTestLaunchIntent(ordinaryArtifactsHost, [], {}), false);
  assert.doesNotThrow(() => assertEvolutionTestProfileLoaded(false, null, null));
});

test('H1 isolation is validated once before Host initialization and the checked result is reused', () => {
  const root = path.resolve(os.tmpdir(), 'plain-host');
  assert.equal(detectEvolutionTestLaunchIntent(root, ['--evolution-test-profile=/missing/profile.json'], {}), true);
  assert.equal(detectEvolutionTestLaunchIntent(root, [], { MY_AGENT_H1_RUN_ID: 'run-1' }), true);
  assert.equal(detectEvolutionTestLaunchIntent('', [], {}), true);
  const profileGateAt = source.indexOf('assertEvolutionTestProfileLoaded(evolutionTestLaunchIntent');
  const validationAt = source.indexOf('const ordinaryChatH1Isolation =');
  const dataAt = source.indexOf('const DATA_ROOT');
  assert.ok(profileGateAt >= 0 && validationAt > profileGateAt && validationAt < dataAt);
  for (const boundary of ['createAdoptionJournal(', 'app.setPath(', 'app.whenReady()'])
    assert.ok(validationAt < source.indexOf(boundary), 'H1 isolation must precede ' + boundary);
  assert.doesNotMatch(source, /ordinary-chat H1 fixture is disabled by fail-closed policy/);
  assert.equal((source.match(/assertOrdinaryChatH1Isolation\(evolutionTestHostContext\)/g) ?? []).length, 1, 'validate exactly once');
  const branch = bodyAfter('} else if (ordinaryChatH1 && evolutionTestHostContext) {', '} else if (evolutionTestHostContext) {');
  assert.match(branch, /const isolated = ordinaryChatH1Isolation/);
  assert.ok(branch.indexOf('const isolated = ordinaryChatH1Isolation') < branch.indexOf('app.setPath("temp", isolated.temp)'));

  const start = source.indexOf('const ordinaryChatH1 =');
  const choose = new Function('evolutionTestHostContext', 'assertOrdinaryChatH1Isolation', source.slice(start, dataAt) + '\nreturn ordinaryChatH1Isolation;');
  let calls = 0;
  const expected = { temp: 'synthetic-temp', userData: 'synthetic-user', sessionData: 'synthetic-session' };
  const context = { profile: { chat_fixture_profile: 'ordinary-chat-h1-v1' } };
  assert.strictEqual(choose(context, () => { calls++; return expected; }), expected);
  assert.equal(calls, 1);
  assert.equal(choose(null, () => { throw new Error('ordinary Host must not validate'); }), null);
  assert.throws(() => choose(context, () => { throw new Error('invalid isolation'); }), /invalid isolation/);
});

test('exact artifacts run Host app layout is test intent even when all identity files and env are missing', t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'h1-markerless-host-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const appRoot = path.join(root, 'artifacts', 'run-123e4567-e89b-42d3-a456-426614174000', 'host', 'app');
  mkdirSync(appRoot, { recursive: true });

  const intent = detectEvolutionTestLaunchIntent(appRoot, [], {});
  assert.equal(intent, true, 'the loader would otherwise return null and continue as an ordinary Host');
  assert.throws(() => assertEvolutionTestProfileLoaded(intent, null, null), /refusing ordinary Host fallback/);
});


test('non-UUID artifacts run layout is test intent when its marker or profile exists', t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'h1-non-uuid-markers-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));

  const markedApp = path.join(root, 'artifacts', 'custom-run-id', 'host', 'app');
  mkdirSync(markedApp, { recursive: true });
  writeFileSync(path.join(root, 'artifacts', 'custom-run-id', '.evolution-test-run'), 'invalid marker content');
  const markedIntent = detectEvolutionTestLaunchIntent(markedApp, [], {});
  assert.equal(markedIntent, true);
  assert.throws(() => assertEvolutionTestProfileLoaded(markedIntent, null, new Error('loader rejected marker')), /refusing ordinary Host fallback/);

  const profileOnlyApp = path.join(root, 'artifacts', 'another-run-name', 'host', 'app');
  mkdirSync(profileOnlyApp, { recursive: true });
  writeFileSync(path.join(path.dirname(profileOnlyApp), 'evolution-test-profile.json'), '{broken profile');
  assert.equal(detectEvolutionTestLaunchIntent(profileOnlyApp, [], {}), true);
});

test('real ordinary-chat H1 UUID layout rejects missing test identity with no environment signals', t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'h1-real-layout-missing-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const appRoot = path.join(root, 'artifacts', 'ordinary-chat-h1', 'run-123e4567-e89b-42d3-a456-426614174000', 'host', 'app');
  mkdirSync(appRoot, { recursive: true });
  const intent = detectEvolutionTestLaunchIntent(appRoot, [], {});
  assert.equal(intent, true);
  assert.throws(() => assertEvolutionTestProfileLoaded(intent, null, null), /refusing ordinary Host fallback/);
  assert.equal(existsSync(path.join(appRoot, 'data')), false);
});

test('real ordinary-chat H1 layout rejects a damaged profile without environment signals', t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'h1-real-layout-broken-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const runRoot = path.join(root, 'artifacts', 'ordinary-chat-h1', 'run-123e4567-e89b-42d3-a456-426614174001');
  const appRoot = path.join(runRoot, 'host', 'app');
  mkdirSync(appRoot, { recursive: true });
  writeFileSync(path.join(runRoot, '.evolution-test-run'), 'evolution-a\n');
  writeFileSync(path.join(runRoot, 'host', 'evolution-test-profile.json'), '{broken');
  const intent = detectEvolutionTestLaunchIntent(appRoot, [], {});
  assert.equal(intent, true);
  assert.throws(() => assertEvolutionTestProfileLoaded(intent, null, new Error('invalid profile')), /refusing ordinary Host fallback/);
  assert.equal(existsSync(path.join(appRoot, 'data')), false);
});

test('marked non-UUID runs nested under artifacts are detected without misclassifying unmarked ordinary apps', t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'h1-nested-run-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const runRoot = path.join(root, 'artifacts', 'fixtures', 'group', 'named-run');
  const appRoot = path.join(runRoot, 'host', 'app');
  mkdirSync(appRoot, { recursive: true });
  assert.equal(detectEvolutionTestLaunchIntent(appRoot, [], {}), false);
  writeFileSync(path.join(runRoot, '.evolution-test-run'), 'invalid');
  assert.equal(detectEvolutionTestLaunchIntent(appRoot, [], {}), true);
});

test('H1 Electron paths are synchronously read back after assignment and mismatch or read errors fail closed without paths', () => {
  const branch = bodyAfter('} else if (ordinaryChatH1 && evolutionTestHostContext) {', '} else if (evolutionTestHostContext) {');
  const start = branch.indexOf('app.setPath("temp", isolated.temp)');
  assert.ok(start >= 0, 'missing H1 path setup');
  const setup = new Function('app', 'isolated', 'samePath', branch.slice(start));
  const isolated = { temp: 'synthetic-temp', userData: 'synthetic-user', sessionData: 'synthetic-session' };
  const run = (mismatch, throwsOnRead = false) => {
    const calls = [], values = {};
    const app = {
      setPath(name, value) { calls.push(`set:${name}`); values[name] = value; },
      getPath(name) { calls.push(`get:${name}`); if (throwsOnRead) throw new Error('sensitive-path-from-electron'); return name === mismatch ? 'unexpected-sensitive-path' : values[name]; },
    };
    const invoke = () => setup(app, isolated, (a, b) => a === b);
    return { calls, invoke };
  };
  const ok = run(null);
  assert.doesNotThrow(ok.invoke);
  assert.deepEqual(ok.calls, ['set:temp', 'set:userData', 'set:sessionData', 'get:temp', 'get:userData', 'get:sessionData']);
  for (const key of ['temp', 'userData', 'sessionData']) {
    const trial = run(key);
    assert.throws(trial.invoke, error => error instanceof Error && !error.message.includes('sensitive') && !Object.values(isolated).some(value => error.message.includes(value)));
  }
  const readFailure = run(null, true);
  assert.throws(readFailure.invoke, error => error instanceof Error && !error.message.includes('sensitive-path-from-electron'));
  const branchAt = source.indexOf('} else if (ordinaryChatH1 && evolutionTestHostContext) {');
  assert.ok(branchAt >= 0 && branchAt < source.indexOf('app.whenReady()'), 'path readback must precede window and sidecar startup');
});

test('the existing H1 isolation validator accepts an isolated fixture and rejects changed flags or data', t => {
  const tmp = mkdtempSync(path.join(os.tmpdir(), 'h1-main-validator-'));
  t.after(() => rmSync(tmp, { recursive: true, force: true }));
  const sandbox = path.join(realpathSync.native(tmp), 'sandbox');
  const runId = 'run-123e4567-e89b-42d3-a456-426614174002';
  const runRoot = path.join(sandbox, 'artifacts', 'ordinary-chat-h1', runId);
  const appRoot = path.join(runRoot, 'host', 'app');
  const profileRoot = path.join(runRoot, 'host', 'profile');
  const expected = {
    MY_AGENT_ROOT: appRoot, MY_AGENT_H1_PROFILE_ROOT: profileRoot, MY_AGENT_H1_RUN_ID: runId,
    USERPROFILE: path.join(profileRoot, 'userprofile'), APPDATA: path.join(profileRoot, 'appdata', 'Roaming'), LOCALAPPDATA: path.join(profileRoot, 'appdata', 'Local'),
    HOME: path.join(profileRoot, 'home'), XDG_CONFIG_HOME: path.join(profileRoot, 'xdg-config'), XDG_DATA_HOME: path.join(profileRoot, 'xdg-data'), XDG_CACHE_HOME: path.join(profileRoot, 'xdg-cache'),
    TEMP: path.join(profileRoot, 'tmp'), TMP: path.join(profileRoot, 'tmp'), MY_AGENT_ELECTRON_USER_DATA: path.join(profileRoot, 'electron-userData'), MY_AGENT_ELECTRON_SESSION_DATA: path.join(profileRoot, 'electron-sessionData'),
  };
  for (const dir of Object.values(expected).filter(value => value.startsWith(profileRoot + path.sep))) mkdirSync(dir, { recursive: true });
  for (const dir of [path.join(appRoot, 'data', 'sessions'), path.join(appRoot, 'workspace'), path.join(appRoot, 'evolve')]) mkdirSync(dir, { recursive: true });
  for (const name of ['_index.core.toml', '_index.toml']) writeFileSync(path.join(appRoot, 'evolve', name), 'fixture');
  writeFileSync(path.join(runRoot, '.ordinary-chat-h1.json'), JSON.stringify({ format: 1, fixture: 'ordinary-chat-h1-v1', run_id: runId, created_at: 'fixture' }));

  const begin = source.indexOf('function samePath(');
  const end = source.indexOf('const controlledH1', begin);
  assert.ok(begin >= 0 && end > begin);
  const ts = createRequire(import.meta.url)('typescript');
  const js = ts.transpileModule(source.slice(begin, end), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  const fakeProcess = { platform: process.platform, env: { ...expected, PYTHONNOUSERSITE: '1', PYTHONDONTWRITEBYTECODE: '1' } };
  const validate = new Function('path', 'process', 'lstatSync', 'realpathSync', 'readFileSync', 'readdirSync', js + '\nreturn assertOrdinaryChatH1Isolation;')(path, fakeProcess, lstatSync, realpathSync, readFileSync, readdirSync);
  const context = { app_root: appRoot, sandbox_root: sandbox, run_root: runRoot, profile: { profile_root: profileRoot, chat_run_id: runId } };
  assert.deepEqual(validate(context), { temp: expected.TEMP, userData: expected.MY_AGENT_ELECTRON_USER_DATA, sessionData: expected.MY_AGENT_ELECTRON_SESSION_DATA });
  fakeProcess.env.PYTHONNOUSERSITE = '0';
  assert.throws(() => validate(context), /Python isolation flags are missing/);
  fakeProcess.env.PYTHONNOUSERSITE = '1';
  writeFileSync(path.join(appRoot, 'data', 'unexpected'), 'fixture');
  assert.throws(() => validate(context), /data root is not empty/);
});

test('H1 shutdown diagnostics are bounded, private, H1-only and do not affect shutdown on log errors', () => {
  const begin = source.indexOf('const h1ShutdownStages =');
  const end = source.indexOf('if (controlledH1) {', begin);
  assert.ok(begin >= 0 && end > begin, 'missing H1-only shutdown stage logger');
  const ts = createRequire(import.meta.url)('typescript');
  const js = ts.transpileModule(source.slice(begin, end), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  const makeLogger = new Function('ordinaryChatH1', 'process', js + '\nreturn h1ShutdownStage;');
  const lines = [];
  const logger = makeLogger(true, { stderr: { write: value => lines.push(value) } });
  logger('bootstrap_accepted'); logger('bootstrap_accepted'); logger('shutdown_accepted');
  assert.deepEqual(lines.map(value => JSON.parse(value)), [
    { type: 'ordinary-chat-h1.shutdown-stage', stage: 'bootstrap_accepted' },
    { type: 'ordinary-chat-h1.shutdown-stage', stage: 'shutdown_accepted' },
  ]);
  const silent = makeLogger(false, { stderr: { write() { throw new Error('ordinary paths must not log'); } } });
  assert.doesNotThrow(() => silent('shutdown_accepted'));
  const broken = makeLogger(true, { stderr: { write() { throw new Error('stderr unavailable'); } } });
  assert.doesNotThrow(() => broken('shutdown_accepted'));
});

test('H1 shutdown stages identify frame acceptance, cleanup progress, reentry and Electron quit events', () => {
  const frame = bodyAfter('if (controlledH1) {', '/** Dev: keep Chromium/session cache');
  for (const stage of ['bootstrap_accepted', 'shutdown_accepted', 'shutdown_rejected']) assert.ok(frame.includes(`h1ShutdownStage("${stage}")`), `missing ${stage}`);
  assert.ok(frame.indexOf('h1ShutdownStage("shutdown_accepted")') < frame.indexOf('app.quit()'));
  const close = bodyAfter('app.on("before-quit",', 'app.on("will-quit",');
  for (const stage of ['before_quit', 'adoption_blocked', 'sidecars_stop_started', 'sidecars_stop_finished', 'cleanup_reentry', 'renderer_close_started', 'renderer_close_finished', 'renderer_close_failed', 'supervisor_close_started', 'supervisor_close_finished', 'supervisor_close_failed', 'quit_reissued']) assert.ok(close.includes(`h1ShutdownStage("${stage}")`), `missing ${stage}`);
  for (const [label, call] of [['renderer', 'closeEvolutionTestRendererServer()'], ['supervisor', 'closeEvolutionSupervisorHost()']]) {
    assert.ok(close.indexOf(`h1ShutdownStage("${label}_close_started")`) < close.indexOf(`await ${call}`));
    assert.ok(close.indexOf(`await ${call}`) < close.indexOf(`h1ShutdownStage("${label}_close_finished")`));
  }
  assert.ok(source.includes('h1ShutdownStage("window_all_closed")'));
  assert.ok(source.includes('h1ShutdownStage("will_quit")'));
});

test('shutdown stage reporting preserves private control-frame validation and does not expose tokens', () => {
  const control = bodyAfter('if (controlledH1) {', '/** Dev: keep Chromium/session cache');
  const ts = createRequire(import.meta.url)('typescript');
  const js = ts.transpileModule(control, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  const frames = [], stages = [];
  let receive = null, quits = 0;
  const fakeProcess = { stdin: { setEncoding() {}, on(event, callback) { if (event === 'data') receive = callback; } } };
  const controlTypes = { bootstrap: 'ordinary-chat-h1.control.bootstrap', shutdown: 'ordinary-chat-h1.control.shutdown' };
  new Function('controlledH1', 'ordinaryChatH1', 'process', 'Buffer', 'h1Control', 'app', 'h1ShutdownStage', 'let t9ShutdownToken = null;\n' + js)(true, true, fakeProcess, Buffer, controlTypes, { quit() { quits++; } }, stage => stages.push(stage));
  assert.equal(typeof receive, 'function');
  const token = 'a'.repeat(64);
  frames.push({ type: controlTypes.bootstrap, token });
  frames.push({ type: controlTypes.shutdown, token: 'b'.repeat(64) });
  frames.push({ type: controlTypes.shutdown, token });
  for (const frame of frames) receive(JSON.stringify(frame) + '\n');
  assert.equal(quits, 1);
  assert.deepEqual(stages, ['stdin_listening', 'stdin_data_received', 'bootstrap_accepted', 'stdin_data_received', 'shutdown_rejected', 'stdin_data_received', 'shutdown_accepted']);
  assert.ok(!JSON.stringify(stages).includes(token));
});

test('private H1 control frames traverse a real Node stdin pipe with safe ingress diagnostics', async () => {
  const start = source.indexOf('const h1ShutdownStages =');
  const end = source.indexOf('/** Dev: keep Chromium/session cache', start);
  assert.ok(start >= 0 && end > start);
  const ts = createRequire(import.meta.url)('typescript');
  const compiled = ts.transpileModule(source.slice(start, end), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  const childSource = `const ordinaryChatH1 = true, controlledH1 = true; let t9ShutdownToken = null;
const h1Control = { bootstrap: "ordinary-chat-h1.control.bootstrap", shutdown: "ordinary-chat-h1.control.shutdown" };
const app = { quit() { process.stdout.write("quit\\n"); } };
${compiled}`;
  const child = spawn(process.execPath, ['-e', childSource], { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true, env: {} });
  let out = '', err = '';
  child.stdout.setEncoding('utf8'); child.stdout.on('data', chunk => { out += chunk; });
  child.stderr.setEncoding('utf8'); child.stderr.on('data', chunk => { err += chunk; });
  const closed = new Promise((resolve, reject) => { child.once('error', reject); child.once('close', (code, signal) => resolve({ code, signal })); });
  const timeout = setTimeout(() => child.kill(), 10000);
  const token = 'c'.repeat(64);
  try {
    child.stdin.write(JSON.stringify({ type: 'ordinary-chat-h1.control.bootstrap', token: 'not-a-valid-token' }) + '\n');
    child.stdin.write(JSON.stringify({ type: 'ordinary-chat-h1.control.bootstrap', token }) + '\n');
    child.stdin.write(JSON.stringify({ type: 'ordinary-chat-h1.control.shutdown', token: 'd'.repeat(64) }) + '\n');
    child.stdin.end(JSON.stringify({ type: 'ordinary-chat-h1.control.shutdown', token }) + '\n');
    assert.deepEqual(await closed, { code: 0, signal: null });
  } finally { clearTimeout(timeout); }
  assert.equal(out, 'quit\n');
  const stages = err.trim().split(/\r?\n/).map(line => JSON.parse(line).stage);
  for (const stage of ['stdin_listening', 'stdin_data_received', 'bootstrap_rejected', 'bootstrap_accepted', 'shutdown_rejected', 'shutdown_accepted', 'stdin_end'])
    assert.ok(stages.includes(stage), `missing safe stage ${stage}`);
  assert.ok(!err.includes(token) && !err.includes('not-a-valid-token'));
});
