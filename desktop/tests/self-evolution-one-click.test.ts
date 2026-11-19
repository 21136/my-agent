import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { createAutoEvolutionLauncher, oneClickChildSpawnOptions } from '../electron/self-evolution-one-click.ts';

const fixture = `process.on('message', m => { if (m.type === 'focus') process.send({type:'focused', id:m.id}); }); process.send({type:'renderer-ready'});`;
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
  } finally { launcher.close(); }
});

test('dedicated Electron child is not hidden by Windows process spawning', () => {
  const options = oneClickChildSpawnOptions({ cwd: 'stable/desktop', env: { VITE_DEV_SERVER_URL: 'http://127.0.0.1:5173' } });
  assert.equal(options.windowsHide, false);
  assert.deepEqual(options.stdio, ['ignore', 'ignore', 'ignore', 'ipc']);
});

test('load completion without a renderer mount acknowledgement is not readiness', async () => {
  const launcher = createAutoEvolutionLauncher({ prepare: async () => {}, start: () => startFixture("process.send({type:'ready'}); setTimeout(() => process.exit(0), 80)"), readyMs: 30 });
  try { assert.equal((await launcher.launch()).ok, false); } finally { launcher.close(); }
});

test('a generic child exit is not readiness and a subsequent click can retry', async () => {
  let attempts = 0;
  const launcher = createAutoEvolutionLauncher({ prepare: async () => {}, start: () => startFixture(++attempts === 1 ? 'process.exit(2)' : fixture), readyMs: 4000 });
  try {
    assert.equal((await launcher.launch()).ok, false);
    assert.deepEqual(await launcher.launch(), {ok:true,reused:false});
  } finally { launcher.close(); }
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
  } finally { launcher.close(); }
});

test('a preparation error never spawns Electron; shutdown aborts preparation', async () => {
  let launched = false;
  const launcher = createAutoEvolutionLauncher({ prepare: async (signal) => new Promise((_, reject) => signal.addEventListener('abort', () => reject(new Error('cancelled')), {once:true})), start: () => { launched = true; return startFixture(); }, readyMs: 4000 });
  const pending = launcher.launch();
  launcher.close();
  assert.equal((await pending).ok, false);
  assert.equal(launched, false);
});
