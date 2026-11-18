import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import test from 'node:test';
import { createAutoEvolutionLauncher } from '../electron/self-evolution-one-click.ts';

const fixture = `process.on('message', m => { if (m.type === 'focus') process.send({type:'focused', id:m.id}); }); process.send({type:'ready'});`;
const startFixture = (source = fixture) => spawn(process.execPath, ['-e', source], { stdio: ['ignore','ignore','ignore','ipc'] });

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

test('a generic child exit is not readiness and a subsequent click can retry', async () => {
  let attempts = 0;
  const launcher = createAutoEvolutionLauncher({ prepare: async () => {}, start: () => startFixture(++attempts === 1 ? 'process.exit(2)' : fixture), readyMs: 4000 });
  try {
    assert.equal((await launcher.launch()).ok, false);
    assert.deepEqual(await launcher.launch(), {ok:true,reused:false});
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
