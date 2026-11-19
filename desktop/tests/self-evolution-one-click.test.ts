import assert from 'node:assert/strict';
import { spawn, type ChildProcess } from 'node:child_process';
import { EventEmitter, once } from 'node:events';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { createAutoEvolutionLauncher, oneClickChildSpawnOptions } from '../electron/self-evolution-one-click.ts';

const fixture = `process.on('message', m => { if (m.type === 'focus') process.send({type:'focused', id:m.id}); else if (m.type === 'shutdown') process.exit(0); }); process.send({type:'renderer-ready'});`;
const startFixture = (source = fixture) => spawn(process.execPath, ['-e', source], { stdio: ['ignore','ignore','ignore','ipc'] });

test('dedicated child requires same-origin renderer mount and visible window readiness', () => {
  const main = readFileSync(new URL('../electron/main.ts', import.meta.url), 'utf8');
  const renderer = readFileSync(new URL('../src/main.ts', import.meta.url), 'utf8');
  const preload = readFileSync(new URL('../electron/preload.ts', import.meta.url), 'utf8');
  assert.match(main, /if \(oneClickChildRoot && oneClickDevBase\) \{ await win\.loadURL\(oneClickDevBase\); return; \}/);
  assert.match(main, /event\.senderFrame !== event\.sender\.mainFrame[\s\S]*?rendererOrigin\(event\.senderFrame\.url\)[\s\S]*?rendererOrigin\(oneClickDevBase\)/);
  assert.match(main, /const rendererMounted = waitForOneClickRendererMount\(workbenchWindow\);[\s\S]*?await Promise\.all\(\[loadRenderer\(workbenchWindow\), rendererMounted\]\)/);
  assert.match(main, /contents\.once\(\"render-process-gone\"[\s\S]*?contents\.on\(\"preload-error\"[\s\S]*?contents\.on\(\"did-fail-load\"/);
  assert.match(main, /if \(win\.isMinimized\(\)\) win\.restore\(\)/);
  assert.match(main, /for \(let attempt = 0; attempt < 8; attempt \+= 1\)/);
  assert.match(main, /win\.isVisible\(\) && !win\.isMinimized\(\)/);
  assert.doesNotMatch(main, /win\.isVisible\(\) && win\.isFocused\(\)/);
  assert.match(renderer, /client = await createWsClient\(\)/);
  assert.match(renderer, /await mountUi\(\);\s*window\.myAgentDesktop\?\.reportSelfEvolutionRendererStatus\?\.\(\"ready\"\)/);
  assert.match(renderer, /reportSelfEvolutionRendererStatus\?\.\(\"failed\"\)/);
  assert.match(preload, /self-evolution-auto:renderer-status/);
  const spawn = main.slice(main.indexOf('function startOneClickAuto'), main.indexOf('const autoEvolutionLauncher'));
  assert.match(spawn, /spawn\(process\.execPath, \[app\.getAppPath\(\), \"--disable-gpu\"\], oneClickChildSpawnOptions/);
  assert.doesNotMatch(spawn, /--no-sandbox/);
  const prepare = main.slice(main.indexOf('function prepareOneClickAuto'), main.indexOf('function startOneClickAuto'));
  assert.match(prepare, /windowsHide: true/);
});

test('parallel clicks prepare once, later clicks focus the live child and await its acknowledgement', async () => {
  let prepared = 0, spawned = 0;
  const launcher = createAutoEvolutionLauncher({ prepare: async () => { prepared++; }, start: () => { spawned++; return startFixture(); }, readyMs: 4000 });
  try {
    assert.deepEqual(await Promise.all([launcher.launch(), launcher.launch()]), [{ok:true,reused:false},{ok:true,reused:false}]);
    assert.deepEqual(await launcher.launch(), {ok:true,reused:true});
    assert.equal(prepared, 1);
    assert.equal(spawned, 1);
  } finally { await launcher.close(); }
});

test('dedicated Electron child is not hidden by Windows process spawning', () => {
  const options = oneClickChildSpawnOptions({ cwd: 'stable/desktop', env: { VITE_DEV_SERVER_URL: 'http://127.0.0.1:5173' } });
  assert.equal(options.windowsHide, false);
  assert.deepEqual(options.stdio, ['ignore', 'ignore', 'ignore', 'ipc']);
});

test('load completion without a renderer mount acknowledgement is not readiness', async () => {
  const launcher = createAutoEvolutionLauncher({ prepare: async () => {}, start: () => startFixture("process.send({type:'ready'}); setTimeout(() => process.exit(0), 80)"), readyMs: 30 });
  try { assert.equal((await launcher.launch()).ok, false); } finally { await launcher.close(); }
});

test('a generic child exit is not readiness and a subsequent click can retry', async () => {
  let attempts = 0;
  const launcher = createAutoEvolutionLauncher({ prepare: async () => {}, start: () => startFixture(++attempts === 1 ? 'process.exit(2)' : fixture), readyMs: 4000 });
  try {
    assert.equal((await launcher.launch()).ok, false);
    assert.deepEqual(await launcher.launch(), {ok:true,reused:false});
  } finally { await launcher.close(); }
});

test('a renderer failure while focusing a reused child evicts it so retry starts a fresh child', async () => {
  let attempts = 0;
  const launcher = createAutoEvolutionLauncher({
    prepare: async () => {},
    start: () => startFixture(++attempts === 1
      ? "process.on('message', m => { if(m.type==='focus') process.send({type:'failed',code:'renderer'}); }); process.send({type:'renderer-ready'});"
      : fixture),
    readyMs: 4000,
  });
  try {
    assert.deepEqual(await launcher.launch(), { ok: true, reused: false });
    assert.equal((await launcher.launch()).ok, false);
    assert.deepEqual(await launcher.launch(), { ok: true, reused: false });
    assert.equal(attempts, 2);
  } finally { await launcher.close(); }
});

test('a preparation error never spawns Electron; shutdown aborts preparation', async () => {
  let launched = false;
  const launcher = createAutoEvolutionLauncher({ prepare: async (signal) => new Promise((_, reject) => signal.addEventListener('abort', () => reject(new Error('cancelled')), {once:true})), start: () => { launched = true; return startFixture(); }, readyMs: 4000 });
  const pending = launcher.launch();
  await launcher.close();
  assert.equal((await pending).ok, false);
  assert.equal(launched, false);
});
test('closing the stable launcher requests child shutdown and waits for the child to exit', async () => {
  let child: ReturnType<typeof startFixture> | undefined;
  let shutdownRequested = false;
  const launcher = createAutoEvolutionLauncher({
    prepare: async () => {},
    start: () => child = startFixture("process.on('message', m => { if(m.type==='shutdown') { process.send({type:'shutdown-received'}); setTimeout(() => process.exit(0), 100); } }); process.send({type:'renderer-ready'}); setInterval(() => {}, 1000);"),
    readyMs: 4000,
  });
  try {
    assert.deepEqual(await launcher.launch(), { ok: true, reused: false });
    child!.on('message', message => { if ((message as {type?: string}).type === 'shutdown-received') shutdownRequested = true; });
    const startedAt = Date.now();
    await launcher.close();
    assert.equal(shutdownRequested, true, 'stable shutdown must request graceful child shutdown over IPC');
    assert.ok(Date.now() - startedAt >= 75, 'close must remain pending until the child exits');
    assert.notEqual(child!.exitCode, null);
  } finally {
    await launcher.close();
    if (child && child.exitCode === null) child.kill();
  }
});


test('stable and child quit paths await the child-owned shutdown lifecycle', () => {
  const main = readFileSync(new URL('../electron/main.ts', import.meta.url), 'utf8');
  assert.match(main, /if \(message\.type === "shutdown"\) \{ void performAppQuit\(\); return; \}/);
  assert.match(main, /await autoEvolutionLauncher\.close\(\);/);
  assert.match(main, /stdio: \[oneClickChildRoot \|\| fixedHostContext \? \"pipe\" : privateLaunch\.stdin, \"pipe\", \"pipe\"\]/);
  assert.match(main, /if \(oneClickChildRoot\) input\.write\(privateInput\);[\s\S]*?else input\.end\(privateInput\)/);
  assert.match(main, /await stopOneClickSidecarGracefully\(\);[\s\S]*?stopSidecar\(\);/);
  const performQuit = main.slice(main.indexOf('async function performAppQuit'), main.indexOf('async function restartSidecarAfterCrash'));
  assert.match(performQuit, /if \(oneClickChildRoot && initialWindow && !initialWindow\.isDestroyed\(\)\)[\s\S]*?initialWindow\.close\(\)/);
  const beforeQuit = main.slice(main.indexOf('app.on("before-quit"'), main.indexOf('app.on("will-quit"'));
  const stableGateStart = beforeQuit.indexOf('if (!oneClickChildRoot && !autoEvolutionQuitAttempted && autoEvolutionLauncher.active)');
  const stableGateEnd = beforeQuit.indexOf('if (oneClickChildRoot && sidecar)', stableGateStart);
  assert.ok(stableGateStart >= 0 && stableGateEnd > stableGateStart, 'stable before-quit gate remains present');
  const stableBeforeQuit = beforeQuit.slice(stableGateStart, stableGateEnd);
  assert.match(stableBeforeQuit, /autoEvolutionLauncher\.close\(\)\.then/);
  assert.doesNotMatch(stableBeforeQuit, /workbenchWindow|window\.close\(\)/, 'stable wait must preserve normal window-close ordering');
  assert.match(main, /if \(oneClickChildRoot && sidecar\)[\s\S]*?event\.preventDefault\(\)[\s\S]*?stopOneClickSidecarGracefully\(\)\.finally[\s\S]*?app\.quit\(\)/);
  assert.match(main, /if \(quitting\) throw new Error\("sidecar launch cancelled during desktop shutdown"\)/);
  assert.match(main, /process\.once\("disconnect", \(\) => app\.quit\(\)\)/);
  assert.match(main, /shutdown state is unknown after bounded EOF and termination waits/);
  assert.match(main, /process exited only after forced termination; interface-lock cleanup is unconfirmed/);
  assert.match(main, /if \(autoEvolutionQuitPending\) return;/);
  assert.match(beforeQuit, /if \(autoEvolutionQuitPending\) \{ event\.preventDefault\(\); return; \}/);
  assert.match(beforeQuit, /if \(!oneClickChildRoot && !autoEvolutionQuitAttempted && autoEvolutionLauncher\.active\)[\s\S]*?autoEvolutionLauncher\.close\(\)\.then[\s\S]*?autoEvolutionQuitAttempted = true[\s\S]*?app\.quit\(\)/);
  assert.match(beforeQuit, /if \(oneClickChildRoot && sidecar\)[\s\S]*?window\.removeAllListeners\("close"\)[\s\S]*?stopOneClickSidecarGracefully/);
  assert.match(main, /if \(autoEvolutionQuitPending\) return;/);
});

test('IPC disconnect does not forget the child before its actual exit', async () => {
  let child: ReturnType<typeof startFixture> | undefined;
  const launcher = createAutoEvolutionLauncher({
    prepare: async () => {},
    start: () => child = startFixture("process.on('disconnect', () => setTimeout(() => process.exit(0), 100)); process.send({type:'renderer-ready'}); setInterval(() => {}, 1000);"),
    readyMs: 4000,
    shutdownMs: 1000,
  });
  try {
    assert.deepEqual(await launcher.launch(), { ok: true, reused: false });
    child!.disconnect();
    assert.equal(launcher.active, true, 'a disconnected IPC channel must not hide a still-running child');
    const startedAt = Date.now();
    await launcher.close();
    assert.ok(Date.now() - startedAt >= 75, 'close must await process exit even after IPC disconnect');
    assert.equal(child!.signalCode, null, 'the child should exit from its disconnect cleanup, not forced termination');
  } finally {
    await launcher.close();
    if (child && child.exitCode === null && child.signalCode === null) child.kill();
  }
});


function syntheticChild(onSend: (child: ChildProcess, message: {type?: string}) => void): ChildProcess & { killCalls: number } {
  const child = new EventEmitter() as unknown as ChildProcess & { killCalls: number };
  Object.assign(child, {
    pid: 777, connected: true, exitCode: null, signalCode: null, killed: false, killCalls: 0,
    send(message: {type?: string}, callback?: (error: Error | null) => void) { onSend(child, message); callback?.(null); return true; },
    kill() { child.killCalls++; return false; },
    disconnect() { child.connected = false; child.emit('disconnect'); },
  });
  return child;
}

test('stable launcher accepts close as terminal evidence without requiring exit', async () => {
  let child: ReturnType<typeof syntheticChild> | undefined;
  const launcher = createAutoEvolutionLauncher({
    prepare: async () => {}, start: () => child = syntheticChild((proc, message) => {
      if (message.type === 'shutdown') setTimeout(() => proc.emit('close', 0, null), 40);
    }), readyMs: 100, shutdownMs: 80, killWaitMs: 40,
  });
  const launching = launcher.launch();
  await new Promise(resolve => setImmediate(resolve));
  child!.emit('message', {type:'renderer-ready'});
  assert.deepEqual(await launching, {ok:true,reused:false});
  const result = await Promise.race([launcher.close(), new Promise(resolve => setTimeout(() => resolve('not-settled'), 250))]);
  assert.deepEqual(result, {status:'exited',forced:false});
  assert.equal(child!.killCalls, 0);
});

test('stable launcher reports unknown after bounded failed termination without mistaking kill for exit', async () => {
  let child: ReturnType<typeof syntheticChild> | undefined;
  const launcher = createAutoEvolutionLauncher({
    prepare: async () => {}, start: () => child = syntheticChild((_proc, _message) => {}),
    readyMs: 100, shutdownMs: 35, killWaitMs: 45,
  });
  const launching = launcher.launch();
  await new Promise(resolve => setImmediate(resolve));
  child!.emit('message', {type:'renderer-ready'});
  assert.deepEqual(await launching, {ok:true,reused:false});
  const startedAt = Date.now();
  const result = await Promise.race([launcher.close(), new Promise(resolve => setTimeout(() => resolve('not-settled'), 250))]);
  assert.deepEqual(result, {status:'unknown',forced:true});
  assert.equal(child!.killCalls, 1);
  assert.ok(Date.now() - startedAt >= 70 && Date.now() - startedAt < 500);
  child!.emit('close', null, null);
});

test('spawn error without exit clears the active launcher state on close', async () => {
  let child: ChildProcess | undefined;
  let closed: Promise<void> = Promise.resolve();
  const launcher = createAutoEvolutionLauncher({
    prepare: async () => {},
    start: () => {
      child = spawn(`${process.execPath}.no-such-executable`, [], {stdio:['ignore','ignore','ignore','ipc']});
      closed = new Promise(resolve => { child!.once('close', () => resolve()); });
      return child;
    },
    readyMs: 100, shutdownMs: 80, killWaitMs: 40,
  });
  const result = await launcher.launch();
  if (!child) throw new Error('child not captured');
  await Promise.race([closed, new Promise(resolve => setTimeout(resolve, 300))]);
  assert.equal(result.ok, false);
  assert.equal(child.pid, undefined);
  assert.equal(launcher.active, false, 'failed spawn must not remain active waiting for a nonexistent exit event');
  const closeResult = await Promise.race([launcher.close(), new Promise(resolve => setTimeout(() => resolve('not-settled'), 250))]);
  assert.deepEqual(closeResult, {status:'not-running'});
});


test('launcher close waits for an aborting preparation instead of reporting no child too early', async () => {
  let preparationFinished = false;
  const launcher = createAutoEvolutionLauncher({
    prepare: signal => new Promise<void>(resolve => signal.addEventListener('abort', () => {
      setTimeout(() => { preparationFinished = true; resolve(); }, 60);
    }, {once:true})),
    start: () => { throw new Error('closed launcher must not start child'); },
    readyMs: 100, shutdownMs: 150, killWaitMs: 50,
  });
  const pending = launcher.launch();
  await new Promise(resolve => setImmediate(resolve));
  const startedAt = Date.now();
  const outcome = await launcher.close();
  assert.deepEqual(outcome, {status:'not-running'});
  assert.equal(preparationFinished, true);
  assert.ok(Date.now() - startedAt >= 45);
  assert.equal((await pending).ok, false);
});


test('an unresponsive pending preparation yields bounded unknown and cannot spawn after close', async () => {
  let finishPreparation: (() => void) | undefined;
  let started = false;
  const launcher = createAutoEvolutionLauncher({
    prepare: () => new Promise<void>(resolve => { finishPreparation = resolve; }),
    start: () => { started = true; throw new Error('must not spawn after close'); },
    readyMs: 100, shutdownMs: 35, killWaitMs: 45,
  });
  const pending = launcher.launch();
  await new Promise(resolve => setImmediate(resolve));
  const startedAt = Date.now();
  const result = await launcher.close();
  assert.deepEqual(result, {status:'unknown',forced:false});
  assert.ok(Date.now() - startedAt >= 70 && Date.now() - startedAt < 500);
  assert.equal(started, false);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(launcher.active, false, 'unknown shutdown must release the active launch state');
  finishPreparation?.();
  assert.equal((await pending).ok, false);
  assert.deepEqual(await launcher.close(), {status:'unknown',forced:false});
});
