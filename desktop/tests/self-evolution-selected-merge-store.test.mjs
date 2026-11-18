import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawn, spawnSync } from 'node:child_process';
import { cpSync, mkdirSync, mkdtempSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { SELF_EVOLUTION_AUTHORIZATION_NOT_CONSUMED } from '../launcher/self-evolution-selected-merge-store.mjs';

const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(',')}]` : value && typeof value === 'object' ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}` : JSON.stringify(value);
const digest = value => createHash('sha256').update(canonical(value)).digest('hex');
const seal = (value, field) => ({ ...value, [field]: digest(Object.fromEntries(Object.entries(value).filter(([key]) => key !== field))) });
function git(cwd, ...args) {
  const result = spawnSync('git', args, { cwd, encoding: 'utf8', env: {
    ...process.env, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: process.platform === 'win32' ? 'NUL' : '/dev/null',
    GIT_TERMINAL_PROMPT: '0', GIT_AUTHOR_NAME: 'Fixture', GIT_AUTHOR_EMAIL: 'fixture@example.invalid',
    GIT_COMMITTER_NAME: 'Fixture', GIT_COMMITTER_EMAIL: 'fixture@example.invalid',
  }});
  assert.equal(result.status, 0, `${args.join(' ')}: ${result.stderr}`);
  return result.stdout.trim();
}
function fixture(t) {
  const root = mkdtempSync(path.join(os.tmpdir(), 'self-evolution-intent-'));
  const storeRoot = mkdtempSync(path.join(os.tmpdir(), 'self-evolution-private-store-'));
  t.after(() => { rmSync(root, { recursive: true, force: true }); rmSync(storeRoot, { recursive: true, force: true }); });
  git(root, 'init', '-q');
  writeFileSync(path.join(root, 'tracked.txt'), 'base\n');
  git(root, 'add', 'tracked.txt'); git(root, 'commit', '-qm', 'base');
  return { root, storeRoot };
}
const file = { path: 'desktop/change.txt', action: 'modify', beforeSha256: '1'.repeat(64), afterSha256: '2'.repeat(64), beforeMode: '100644', afterMode: '100644', beforeOid: '3'.repeat(40), afterOid: '4'.repeat(40), beforeSize: 4, afterSize: 5, contentRef: 'blob-ref-1' };
function operation() {
  const candidate = { format: 1, mode: 'self_evolution_candidate_binding_v1', taskId: 'task-1', attemptId: 'attempt-1', candidateId: 'candidate-1', candidateDigest: 'a'.repeat(64), baselineManifestDigest: 'b'.repeat(64), gitCommonId: 'common-1', stableWorktreeId: 'stable-1', candidateWorktreeId: 'candidate-worktree-1', baseHead: 'c'.repeat(40) };
  const stableIndexBytes = Buffer.from('index snapshot');
  const stableState = { head: candidate.baseHead, indexSha256: createHash('sha256').update(stableIndexBytes).digest('hex'), worktreeDigest: 'e'.repeat(64), gitMetadataDigest: 'f'.repeat(64), gitCommonId: candidate.gitCommonId, worktreeId: candidate.stableWorktreeId };
  const verificationRefs = [{ checkId: 'targeted-tests', candidateDigest: candidate.candidateDigest, evidenceRef: 'evidence-1', status: 'passed' }];
  let checkpoint = seal({ format: 1, mode: 'self_evolution_merge_checkpoint_v1', taskId: candidate.taskId, attemptId: candidate.attemptId, candidateId: candidate.candidateId, candidateDigest: candidate.candidateDigest, baselineManifestDigest: candidate.baselineManifestDigest, stableState, candidateHead: '9'.repeat(40), candidateWorktreeDigest: '8'.repeat(64), selectedFiles: [file], verificationRefs, privateBlobSetRef: 'blob-set-1' }, 'checkpointDigest');
  let preview = seal({ format: 1, mode: 'self_evolution_promotion_preview_v1', taskId: candidate.taskId, attemptId: candidate.attemptId, candidateId: candidate.candidateId, candidateDigest: candidate.candidateDigest, checkpointDigest: checkpoint.checkpointDigest, stableState, stableStateDigest: digest(stableState), selectedFiles: [file], verificationRefs, blockers: [] }, 'previewDigest');
  const confirmation = { format: 1, mode: 'self_evolution_native_confirmation_v1', decision: 'accepted', taskId: candidate.taskId, attemptId: candidate.attemptId, candidateId: candidate.candidateId, candidateDigest: candidate.candidateDigest, checkpointDigest: checkpoint.checkpointDigest, stableStateDigest: preview.stableStateDigest, previewDigest: preview.previewDigest, selectedPaths: [file.path], operationId: 'operation-1', nonce: 'nonce-1', issuer: 'electron-main', issuedAt: '2026-11-15T00:00:00.000Z' };
  const intent = seal({ format: 1, mode: 'self_evolution_merge_intent_v1', state: 'prepared', operationId: confirmation.operationId, taskId: candidate.taskId, attemptId: candidate.attemptId, candidateId: candidate.candidateId, candidateDigest: candidate.candidateDigest, checkpointDigest: checkpoint.checkpointDigest, stableStateDigest: preview.stableStateDigest, previewDigest: preview.previewDigest, confirmationNonce: confirmation.nonce }, 'intentDigest');
  return { candidate, checkpoint, preview, nativeConfirmation: confirmation, intent, stableIndexBytes };
}
function rebindOperation(input, taskId, attemptId) {
  input.candidate.taskId = taskId; input.candidate.attemptId = attemptId;
  input.checkpoint.taskId = taskId; input.checkpoint.attemptId = attemptId;
  input.checkpoint = seal(input.checkpoint, 'checkpointDigest');
  input.preview.taskId = taskId; input.preview.attemptId = attemptId;
  input.preview.checkpointDigest = input.checkpoint.checkpointDigest;
  input.preview = seal(input.preview, 'previewDigest');
  input.nativeConfirmation.taskId = taskId; input.nativeConfirmation.attemptId = attemptId;
  input.nativeConfirmation.checkpointDigest = input.checkpoint.checkpointDigest;
  input.nativeConfirmation.previewDigest = input.preview.previewDigest;
  input.intent.taskId = taskId; input.intent.attemptId = attemptId;
  input.intent.checkpointDigest = input.checkpoint.checkpointDigest;
  input.intent.previewDigest = input.preview.previewDigest;
  input.intent = seal(input.intent, 'intentDigest');
  return input;
}
async function storeApi(t) {
  const module = await import('../launcher/self-evolution-selected-merge-store.mjs');
  assert.equal(typeof module.createSelfEvolutionMergeOperationStore, 'function', 'durable operation store API must exist');
  return module.createSelfEvolutionMergeOperationStore;
}

 test('persists an operation-scoped prepared intent and makes executing single-use across store instances', async t => {
  const createStore = await storeApi(t), { root, storeRoot } = fixture(t), input = operation();
  const head = git(root, 'rev-parse', 'HEAD'), status = git(root, 'status', '--porcelain=v1', '-z');
  const first = createStore({ root: storeRoot });
  const prepared = first.prepare(input);
  assert.equal(prepared.status, 'prepared');
  assert.equal(readFileSync(path.join(prepared.directory, 'stable-index.before')).toString(), input.stableIndexBytes.toString());
  assert.equal(first.read('operation-1').status, 'prepared');

  const restarted = createStore({ root: storeRoot });
  const claimed = restarted.claim('operation-1', prepared.intentDigest, { assertAuthorized: () => true });
  assert.equal(claimed.status, 'executing');
  assert.equal(createStore({ root: storeRoot }).read('operation-1').status, 'maintenance_required');
  assert.throws(() => createStore({ root: storeRoot }).claim('operation-1', prepared.intentDigest), { code: 'self_evolution_intent_unavailable' });
  assert.equal(git(root, 'rev-parse', 'HEAD'), head);
  assert.equal(git(root, 'status', '--porcelain=v1', '-z'), status);
});

test('persists unknown after executing and never offers automatic replay', async t => {
  const createStore = await storeApi(t), { storeRoot } = fixture(t), store = createStore({ root: storeRoot });
  const prepared = store.prepare(operation());
  store.claim('operation-1', prepared.intentDigest, { assertAuthorized: () => true });
  const unknown = store.markUnknown('operation-1', 'selected_merge_result_unknown');
  assert.equal(unknown.status, 'maintenance_required');
  assert.equal(createStore({ root: storeRoot }).read('operation-1').status, 'maintenance_required');
  assert.throws(() => createStore({ root: storeRoot }).claim('operation-1', unknown.intentDigest), { code: 'self_evolution_intent_unavailable' });
});

test('rejects changed bindings and leaves Git untouched before creating durable state', async t => {
  const createStore = await storeApi(t), { root, storeRoot } = fixture(t), input = operation();
  const head = git(root, 'rev-parse', 'HEAD'), status = git(root, 'status', '--porcelain=v1', '-z');
  input.intent.confirmationNonce = 'wrong-nonce'; input.intent = seal(input.intent, 'intentDigest');
  assert.throws(() => createStore({ root: storeRoot }).prepare(input), { code: 'self_evolution_intent_mismatch' });
  assert.equal(git(root, 'rev-parse', 'HEAD'), head);
  assert.equal(git(root, 'status', '--porcelain=v1', '-z'), status);
});










test('a durable intent cannot be claimed without a successful host one-shot authorization callback', async t => {
  const createStore = await storeApi(t), { storeRoot } = fixture(t), store = createStore({ root: storeRoot });
  const prepared = store.prepare(operation());
  assert.throws(() => store.claim('operation-1', prepared.intentDigest), { code: 'self_evolution_confirmation_untrusted' });
  assert.throws(() => store.claim('operation-1', prepared.intentDigest, { assertAuthorized: () => SELF_EVOLUTION_AUTHORIZATION_NOT_CONSUMED }), { code: 'self_evolution_confirmation_untrusted' });
  assert.equal(store.read('operation-1').status, 'prepared');
  assert.equal(store.claim('operation-1', prepared.intentDigest, { assertAuthorized: () => true }).status, 'executing');
});

test('tampered durable operation evidence is rejected after reopening the store', async t => {
  const createStore = await storeApi(t), { storeRoot } = fixture(t), store = createStore({ root: storeRoot });
  const prepared = store.prepare(operation());
  const recordPath = path.join(prepared.directory, 'prepared.json');
  const record = JSON.parse(readFileSync(recordPath, 'utf8'));
  record.payload.intent.operationId = 'substituted-operation';
  writeFileSync(recordPath, `${canonical(record)}\n`);
  assert.throws(() => createStore({ root: storeRoot }).read('operation-1'), { code: 'self_evolution_store_corrupt' });
});

test('an abandoned coordinator lock becomes maintenance and is never silently cleared', async t => {
  const createStore = await storeApi(t), { storeRoot } = fixture(t), store = createStore({ root: storeRoot });
  const prepared = store.prepare(operation());
  writeFileSync(path.join(storeRoot, '.coordinator.lock'), 'abandoned-lock');
  const reopened = createStore({ root: storeRoot });
  assert.equal(reopened.read('operation-1').status, 'maintenance_required');
  assert.throws(() => reopened.claim('operation-1', prepared.intentDigest, { assertAuthorized: () => true }), { code: 'self_evolution_store_lock_busy' });
  assert.equal(readFileSync(path.join(storeRoot, '.coordinator.lock'), 'utf8'), 'abandoned-lock');
});


test('operation id is globally single-use across task and attempt directories', async t => {
  const createStore = await storeApi(t), { storeRoot } = fixture(t);
  const first = operation();
  const store = createStore({ root: storeRoot });
  store.prepare(first);
  const second = operation();
  second.candidate.taskId = 'task-2';
  second.candidate.attemptId = 'attempt-2';
  second.checkpoint.taskId = second.candidate.taskId;
  second.checkpoint.attemptId = second.candidate.attemptId;
  second.checkpoint = seal(second.checkpoint, 'checkpointDigest');
  second.preview.taskId = second.candidate.taskId;
  second.preview.attemptId = second.candidate.attemptId;
  second.preview.checkpointDigest = second.checkpoint.checkpointDigest;
  second.preview = seal(second.preview, 'previewDigest');
  second.nativeConfirmation.taskId = second.candidate.taskId;
  second.nativeConfirmation.attemptId = second.candidate.attemptId;
  second.nativeConfirmation.checkpointDigest = second.checkpoint.checkpointDigest;
  second.nativeConfirmation.previewDigest = second.preview.previewDigest;
  second.intent.taskId = second.candidate.taskId;
  second.intent.attemptId = second.candidate.attemptId;
  second.intent.checkpointDigest = second.checkpoint.checkpointDigest;
  second.intent.previewDigest = second.preview.previewDigest;
  second.intent = seal(second.intent, 'intentDigest');
  assert.throws(() => store.prepare(second), { code: 'self_evolution_intent_unavailable' });
  assert.equal(store.read('operation-1').status, 'prepared');
});

test('case-fold aliases cannot create a second operation on case-insensitive filesystems', async t => {
  const createStore = await storeApi(t), { storeRoot } = fixture(t);
  const first = operation(); first.intent.operationId = 'Operation-1'; first.nativeConfirmation.operationId = 'Operation-1';
  first.intent = seal(first.intent, 'intentDigest');
  const store = createStore({ root: storeRoot });
  store.prepare(first);
  const second = operation(); second.intent.operationId = 'operation-1'; second.nativeConfirmation.operationId = 'operation-1';
  second.intent = seal(second.intent, 'intentDigest');
  assert.throws(() => store.prepare(second), { code: 'self_evolution_operation_identity_ambiguous' });
});
test('crash after durable executing claim reopens as maintenance and is never replayed', async t => {
  const createStore = await storeApi(t), { root, storeRoot } = fixture(t), input = operation();
  const beforeHead = git(root, 'rev-parse', 'HEAD');
  const beforeStatus = git(root, 'status', '--porcelain=v1', '-z');
  const prepared = createStore({ root: storeRoot }).prepare(input);
  createStore({ root: storeRoot }).claim('operation-1', prepared.intentDigest, { assertAuthorized: () => true });
  const recovered = createStore({ root: storeRoot });
  assert.equal(recovered.read('operation-1').status, 'maintenance_required');
  assert.throws(() => recovered.claim('operation-1', prepared.intentDigest, { assertAuthorized: () => true }), { code: 'self_evolution_intent_unavailable' });
  assert.equal(git(root, 'rev-parse', 'HEAD'), beforeHead);
  assert.equal(git(root, 'status', '--porcelain=v1', '-z'), beforeStatus);
});

test('crash during prepared persistence preserves the coordinator lock and blocks retry', async t => {
  const createStore = await storeApi(t), { root, storeRoot } = fixture(t), input = operation();
  const beforeHead = git(root, 'rev-parse', 'HEAD');
  const beforeStatus = git(root, 'status', '--porcelain=v1', '-z');
  writeFileSync(path.join(storeRoot, '.coordinator.lock'), 'crash-before-prepared');
  const recovered = createStore({ root: storeRoot });
  assert.equal(recovered.read('operation-1').status, 'maintenance_required');
  assert.throws(() => recovered.prepare(input), { code: 'self_evolution_store_lock_busy' });
  assert.equal(readFileSync(path.join(storeRoot, '.coordinator.lock'), 'utf8'), 'crash-before-prepared');
  assert.equal(git(root, 'rev-parse', 'HEAD'), beforeHead);
  assert.equal(git(root, 'status', '--porcelain=v1', '-z'), beforeStatus);
});







test('partial executing marker after a crash is corrupt and never falls back to prepared', async t => {
  const createStore = await storeApi(t), { storeRoot } = fixture(t), store = createStore({ root: storeRoot });
  const prepared = store.prepare(operation());
  store.claim('operation-1', prepared.intentDigest, { assertAuthorized: () => true });
  writeFileSync(path.join(prepared.directory, 'executing.json'), '{"format":1');
  assert.throws(() => createStore({ root: storeRoot }).read('operation-1'), { code: 'self_evolution_store_corrupt' });
});

test('changed durable index evidence after restart blocks operation recovery', async t => {
  const createStore = await storeApi(t), { storeRoot } = fixture(t), store = createStore({ root: storeRoot });
  const prepared = store.prepare(operation());
  writeFileSync(path.join(prepared.directory, 'stable-index.before'), 'substituted index bytes');
  assert.throws(() => createStore({ root: storeRoot }).read('operation-1'), { code: 'self_evolution_store_corrupt' });
});

test('concurrent processes consume native one-shot authorization at most once per intent claim', async t => {
  const createStore = await storeApi(t), { storeRoot } = fixture(t), store = createStore({ root: storeRoot });
  const prepared = store.prepare(operation());
  const logPath = path.join(storeRoot, 'authorization-callbacks.log');
  const childSource = `
    import { appendFileSync } from 'node:fs';
    import { pathToFileURL } from 'node:url';
    const root = process.argv[1], digest = process.argv[2], log = process.argv[3], modulePath = process.argv[4];
    process.stdin.once('data', () => {
      import(pathToFileURL(modulePath)).then(({ createSelfEvolutionMergeOperationStore }) => {
        try {
          const result = createSelfEvolutionMergeOperationStore({ root }).claim('operation-1', digest, {
            assertAuthorized() {
              appendFileSync(log, 'authorized\\n');
              Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 150);
              return true;
            },
          });
          console.log(JSON.stringify({ status: result.status }));
        } catch (error) { console.log(JSON.stringify({ code: error.code || 'unknown' })); }
      }).catch(error => { console.error(error); process.exitCode = 1; });
    });
  `;
  const modulePath = path.resolve('desktop/launcher/self-evolution-selected-merge-store.mjs');
  const launch = () => {
    const child = spawn(process.execPath, ['--input-type=module', '-e', childSource, storeRoot, prepared.intentDigest, logPath, modulePath], { stdio: ['pipe', 'pipe', 'pipe'] });
    let stdout = '', stderr = '';
    child.stdout.setEncoding('utf8').on('data', chunk => { stdout += chunk; });
    child.stderr.setEncoding('utf8').on('data', chunk => { stderr += chunk; });
    const result = new Promise((resolve, reject) => child.on('exit', code => {
      if (code === 0) resolve(JSON.parse(stdout.trim()));
      else reject(new Error(stderr || `child exited ${code}`));
    }));
    return { child, result };
  };
  const first = launch(), second = launch();
  first.child.stdin.end('go'); second.child.stdin.end('go');
  const results = await Promise.all([first.result, second.result]);
  assert.equal(results.filter(result => result.status === 'executing').length, 1);
  assert.equal(readFileSync(logPath, 'utf8').trim().split(/\r?\n/).length, 1, 'native authorization callback must be consumed by only the winning claim');
  assert.equal(createStore({ root: storeRoot }).read('operation-1').status, 'maintenance_required');
});


test('process crash after native one-shot authorization leaves the prepared intent quarantined', async t => {
  const createStore = await storeApi(t), { storeRoot } = fixture(t), store = createStore({ root: storeRoot });
  const prepared = store.prepare(operation());
  const logPath = path.join(storeRoot, 'authorization-callbacks.log');
  const childSource = `
    import { appendFileSync } from 'node:fs';
    import { pathToFileURL } from 'node:url';
    const [root, digest, log, modulePath] = process.argv.slice(1);
    const { createSelfEvolutionMergeOperationStore } = await import(pathToFileURL(modulePath));
    createSelfEvolutionMergeOperationStore({ root }).claim('operation-1', digest, {
      assertAuthorized() { appendFileSync(log, 'consumed\\n'); process.exit(86); },
    });
  `;
  const modulePath = path.resolve('desktop/launcher/self-evolution-selected-merge-store.mjs');
  const child = spawnSync(process.execPath, ['--input-type=module', '-e', childSource, storeRoot, prepared.intentDigest, logPath, modulePath], { encoding: 'utf8' });
  assert.equal(child.status, 86, child.stderr);
  assert.equal(readFileSync(logPath, 'utf8'), 'consumed\n');

  const recovered = createStore({ root: storeRoot });
  assert.equal(recovered.read('operation-1').status, 'maintenance_required');
  assert.throws(() => recovered.claim('operation-1', prepared.intentDigest, { assertAuthorized: () => true }), { code: 'self_evolution_store_lock_busy' });
  assert.equal(readFileSync(logPath, 'utf8'), 'consumed\n', 'recovery must not consume confirmation a second time');
});


test('authorization side effect followed by throw quarantines intent across restart', async t => {
  const createStore = await storeApi(t), { storeRoot } = fixture(t), store = createStore({ root: storeRoot });
  const prepared = store.prepare(operation());
  const sideEffect = path.join(storeRoot, 'consumed.log');
  assert.throws(() => store.claim('operation-1', prepared.intentDigest, { assertAuthorized() {
    writeFileSync(sideEffect, 'consumed');
    throw new Error('failure after consuming authorization');
  } }), { code: 'self_evolution_confirmation_unknown' });
  assert.equal(readFileSync(sideEffect, 'utf8'), 'consumed');
  const recovered = createStore({ root: storeRoot });
  assert.equal(recovered.read('operation-1').status, 'maintenance_required');
  let calledAgain = false;
  assert.throws(() => recovered.claim('operation-1', prepared.intentDigest, { assertAuthorized() { calledAgain = true; return true; } }), { code: 'self_evolution_store_lock_busy' });
  assert.equal(calledAgain, false);
});

test('false and other ambiguous authorization results quarantine the operation', async t => {
  const createStore = await storeApi(t);
  for (const result of [false, undefined, null, 'rejected', Promise.resolve(true)]) {
    const { storeRoot } = fixture(t), store = createStore({ root: storeRoot });
    const prepared = store.prepare(operation());
    const sideEffect = path.join(storeRoot, 'consumed.log');
    assert.throws(() => store.claim('operation-1', prepared.intentDigest, { assertAuthorized() { writeFileSync(sideEffect, 'consumed'); return result; } }), { code: 'self_evolution_confirmation_unknown' });
    assert.equal(readFileSync(sideEffect, 'utf8'), 'consumed');
    assert.equal(createStore({ root: storeRoot }).read('operation-1').status, 'maintenance_required');
    assert.throws(() => createStore({ root: storeRoot }).claim('operation-1', prepared.intentDigest, { assertAuthorized: () => true }), { code: 'self_evolution_store_lock_busy' });
  }
});

test('read rejects operation-id reuse across task/session directories', async t => {
  const createStore = await storeApi(t), { storeRoot } = fixture(t);
  const otherStoreRoot = mkdtempSync(path.join(os.tmpdir(), 'self-evolution-private-store-other-session-'));
  t.after(() => rmSync(otherStoreRoot, { recursive: true, force: true }));
  const first = createStore({ root: storeRoot }).prepare(operation());
  const second = createStore({ root: otherStoreRoot }).prepare(rebindOperation(operation(), 'task-2', 'attempt-2'));
  const duplicateDirectory = path.join(storeRoot, 'task-2', 'attempt-2', 'operation-1');
  mkdirSync(path.dirname(duplicateDirectory), { recursive: true });
  cpSync(second.directory, duplicateDirectory, { recursive: true });

  assert.throws(() => createStore({ root: storeRoot }).read('operation-1'), { code: 'self_evolution_operation_identity_ambiguous' });
  assert.equal(first.status, 'prepared');
});

test('read does not mistake a damaged duplicate identity entry for a unique operation', async t => {
  const createStore = await storeApi(t), { storeRoot } = fixture(t);
  createStore({ root: storeRoot }).prepare(operation());
  const damagedDuplicate = path.join(storeRoot, 'task-2', 'attempt-2', 'operation-1');
  mkdirSync(damagedDuplicate, { recursive: true });
  writeFileSync(path.join(damagedDuplicate, 'prepared.json'), '{truncated');

  assert.throws(() => createStore({ root: storeRoot }).read('operation-1'), error =>
    ['self_evolution_operation_identity_ambiguous', 'self_evolution_store_corrupt'].includes(error.code));
});
test('lookup fails closed when another operation has malformed or missing prepared evidence', async t => {
  const createStore = await storeApi(t);
  for (const damage of ['truncated', 'missing']) {
    const { storeRoot } = fixture(t), store = createStore({ root: storeRoot });
    store.prepare(operation());
    const secondInput = rebindOperation(operation(), 'task-2', 'attempt-2');
    secondInput.nativeConfirmation.operationId = 'operation-2';
    secondInput.intent.operationId = 'operation-2';
    secondInput.intent = seal(secondInput.intent, 'intentDigest');
    const second = store.prepare(secondInput);
    const prepared = path.join(second.directory, 'prepared.json');
    if (damage === 'truncated') writeFileSync(prepared, '{truncated');
    else rmSync(prepared);
    assert.throws(() => store.read('operation-1'), { code: 'self_evolution_store_corrupt' });
    assert.throws(() => store.read('unknown-operation'), { code: 'self_evolution_store_corrupt' });
  }
});

test('lookup rejects other operation records whose durable identity differs from directory', async t => {
  const createStore = await storeApi(t), { storeRoot } = fixture(t), store = createStore({ root: storeRoot });
  store.prepare(operation());
  const secondInput = rebindOperation(operation(), 'task-2', 'attempt-2');
  secondInput.nativeConfirmation.operationId = 'operation-2';
  secondInput.intent.operationId = 'operation-2';
  secondInput.intent = seal(secondInput.intent, 'intentDigest');
  const second = store.prepare(secondInput);
  const otherDirectory = path.join(path.dirname(second.directory), 'different-name');
  cpSync(second.directory, otherDirectory, { recursive: true });
  rmSync(second.directory, { recursive: true });
  assert.throws(() => store.read('operation-1'), { code: 'self_evolution_store_corrupt' });
});

test('lookup accepts multiple valid identities and safe auxiliary files and preserves lock behavior', async t => {
  const createStore = await storeApi(t), { storeRoot } = fixture(t), store = createStore({ root: storeRoot });
  store.prepare(operation());
  const secondInput = rebindOperation(operation(), 'task-2', 'attempt-2');
  secondInput.nativeConfirmation.operationId = 'operation-2';
  secondInput.intent.operationId = 'operation-2';
  secondInput.intent = seal(secondInput.intent, 'intentDigest');
  store.prepare(secondInput);
  writeFileSync(path.join(storeRoot, 'safe-extra.txt'), 'allowed');
  assert.equal(store.read('operation-1').status, 'prepared');
  assert.equal(store.read('operation-2').status, 'prepared');
  assert.equal(store.read('missing').status, 'not_found');
  writeFileSync(path.join(storeRoot, '.coordinator.lock'), 'abandoned');
  assert.equal(store.read('operation-1').status, 'maintenance_required');
});
test('lookup rejects a different operation with a tampered digest or resealed invalid contract', async t => {
  const createStore = await storeApi(t);
  for (const damage of ['digest', 'contract']) {
    const { storeRoot } = fixture(t), store = createStore({ root: storeRoot });
    store.prepare(operation());
    const other = rebindOperation(operation(), 'task-2', 'attempt-2');
    other.nativeConfirmation.operationId = 'operation-2';
    other.intent.operationId = 'operation-2'; other.intent = seal(other.intent, 'intentDigest');
    const prepared = path.join(store.prepare(other).directory, 'prepared.json');
    let record = JSON.parse(readFileSync(prepared, 'utf8'));
    record.payload.intent.confirmationNonce = 'not-the-confirmed-nonce';
    if (damage === 'contract') record = seal(record, 'recordDigest');
    writeFileSync(prepared, `${canonical(record)}\n`);
    assert.throws(() => store.read('operation-1'), { code: 'self_evolution_store_corrupt' });
  }
});

test('lookup rejects records copied under a different task identity', async t => {
  const createStore = await storeApi(t), { storeRoot } = fixture(t), store = createStore({ root: storeRoot });
  store.prepare(operation());
  const other = rebindOperation(operation(), 'task-2', 'attempt-2');
  other.nativeConfirmation.operationId = 'operation-2';
  other.intent.operationId = 'operation-2'; other.intent = seal(other.intent, 'intentDigest');
  const src = store.prepare(other).directory;
  const dst = path.join(storeRoot, 'task-3', 'attempt-2', 'operation-2');
  mkdirSync(path.dirname(dst), { recursive: true }); cpSync(src, dst, { recursive: true });
  rmSync(src, { recursive: true });
  assert.throws(() => store.read('operation-1'), { code: 'self_evolution_store_corrupt' });
});
test('lookup preserves the eight-operation ledger bound', async t => {
  const createStore = await storeApi(t), { storeRoot } = fixture(t), store = createStore({ root: storeRoot });
  for (let i = 1; i <= 8; i++) {
    const input = rebindOperation(operation(), `task-${i}`, `attempt-${i}`);
    input.nativeConfirmation.operationId = `operation-${i}`;
    input.intent.operationId = `operation-${i}`; input.intent = seal(input.intent, 'intentDigest');
    store.prepare(input);
  }
  assert.equal(store.read('operation-1').status, 'prepared');
  mkdirSync(path.join(storeRoot, 'task-9', 'attempt-9', 'operation-9'), { recursive: true });
  assert.throws(() => store.read('operation-1'), { code: 'self_evolution_store_limit_exceeded' });
});
