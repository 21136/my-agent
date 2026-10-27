import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { validateManagedSelectedMergeRequest } from '../../launcher/managed-selected-merge.mjs';

const engine = readFileSync(new URL('../../launcher/managed-selected-merge.mjs', import.meta.url), 'utf8');
const store = readFileSync(new URL('../../launcher/managed-selected-merge-store.mjs', import.meta.url), 'utf8');
const bridge = readFileSync(new URL('../../electron/managed-selected-merge-bridge.ts', import.meta.url), 'utf8');

test('renderer merge request schema rejects preservation authority and extra fields', () => {
  const checkpointDigest = 'a'.repeat(64);
  const request = { format: 1, mode: 'managed_selected_merge_request_v1', checkpointDigest };
  assert.deepEqual(validateManagedSelectedMergeRequest(request), request);
  assert.throws(() => validateManagedSelectedMergeRequest({ ...request, preserveStableWorkingChanges: true }), { code: 'selected_merge_request_invalid' });
});

test('host preservation option is a strict boolean checked before either root is accessed', () => {
  const start = engine.indexOf('export function prepareManagedSelectedMerge(');
  const body = engine.slice(start, engine.indexOf('\n}', start));
  assert.ok(body.indexOf('typeof preserveStableWorkingChanges !== \'boolean\'') >= 0);
  assert.ok(body.indexOf('typeof preserveStableWorkingChanges !== \'boolean\'') < body.indexOf('root(stableRoot'));
  assert.match(body, /if \(!preserveStableWorkingChanges\) fail\('selected_stable_working_dirty'/);
  assert.doesNotMatch(bridge, /preserveStableWorkingChanges/);
});

test('execution trust denial precedes Git executable and root access', () => {
  const start = engine.indexOf('export function executeManagedSelectedMerge(');
  const body = engine.slice(start, engine.indexOf('\n}', start));
  const trustGate = body.indexOf("typeof assertTrusted !== 'function' || assertTrusted() !== true");
  assert.ok(trustGate >= 0);
  assert.ok(trustGate < body.indexOf('gitExecutable(executable)'));
  assert.ok(trustGate < body.indexOf('root(stableRoot'));
});

test('preservation intent and original index evidence are durable before real index mutation', () => {
  const executeStart = engine.indexOf('export function executeManagedSelectedMerge(');
  const executeBody = engine.slice(executeStart);
  const durableClaim = executeBody.indexOf('claimSelectedMergeStore({sandboxRoot:sandbox,plan,assertTrusted,durableBundle})');
  const realIndexMutation = executeBody.indexOf("run(git,stable,['update-index','--cacheinfo'");
  assert.ok(durableClaim >= 0);
  assert.ok(realIndexMutation >= 0);
  assert.ok(durableClaim < realIndexMutation);
  assert.match(executeBody, /durableBundle=\{checkpoint,stableIndex:readFileSync\(path\.join\(plan\.stable\.gitDir,'index'\)\)\}/);
  assert.match(store, /exactKeys\(durableBundle, \['checkpoint','stableIndex'\]\)/);
  assert.match(store, /writeExclusive\(path\.join\(operationDir, 'stable-index\.before'\), bundle\.stableIndex/);
  assert.match(store, /validateDurableEvidence\(operationDir, intent, bundle\)/);
  assert.match(store, /Preserve coordinator lock: an execution failure\/uncertain result is maintenance-only/);
  const storeStart = store.indexOf('export function claimSelectedMergeStore(');
  const storeBody = store.slice(storeStart);
  assert.ok(storeBody.indexOf('if (!trusted) reject') < storeBody.indexOf('mkdirSync(artifacts)'));
});
