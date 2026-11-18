import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { preflightSelfEvolutionSelectedMerge, selfEvolutionPromotionPreviewDigest } from '../launcher/self-evolution-selected-merge.mjs';

const sha = value => createHash('sha256').update(value).digest('hex');
const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(',')}]` : value && typeof value === 'object' ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}` : JSON.stringify(value);
const sealed = (value, field) => ({ ...value, [field]: sha(canonical(Object.fromEntries(Object.entries(value).filter(([key]) => key !== field)))) });
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
  const root = mkdtempSync(path.join(os.tmpdir(), 'self-evolution-merge-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  git(root, 'init', '-q');
  writeFileSync(path.join(root, 'tracked.txt'), 'base\n');
  git(root, 'add', 'tracked.txt'); git(root, 'commit', '-qm', 'base');
  return root;
}
const manifestDigest = 'a'.repeat(64), oid = 'b'.repeat(40);
function contract() {
  const candidate = {
    format: 1, mode: 'self_evolution_candidate_binding_v1', taskId: 'task-1', attemptId: 'attempt-1',
    candidateId: 'candidate-1', candidateDigest: manifestDigest, baselineManifestDigest: 'e'.repeat(64),
    gitCommonId: 'common-1', stableWorktreeId: 'stable-1', candidateWorktreeId: 'worktree-1', baseHead: oid,
  };
  const stableState = { head: oid, indexSha256: 'c'.repeat(64), worktreeDigest: 'f'.repeat(64), gitMetadataDigest: '1'.repeat(64), gitCommonId: 'common-1', worktreeId: 'stable-1' };
  const verificationRefs = [{ checkId: 'targeted-tests', candidateDigest: manifestDigest, evidenceRef: 'evidence-1', status: 'passed' }];
  const selectedFiles = [
    { path: 'desktop/delete.txt', action: 'delete', beforeSha256: '5'.repeat(64), afterSha256: null, beforeMode: '100644', afterMode: null, beforeOid: '6'.repeat(40), afterOid: null, beforeSize: 3, afterSize: null, contentRef: null },
    { path: 'desktop/modify.txt', action: 'modify', beforeSha256: '7'.repeat(64), afterSha256: '8'.repeat(64), beforeMode: '100644', afterMode: '100755', beforeOid: '9'.repeat(40), afterOid: 'a'.repeat(40), beforeSize: 3, afterSize: 4, contentRef: 'blob-2' },
    { path: 'desktop/new.txt', action: 'add', beforeSha256: null, afterSha256: '2'.repeat(64), beforeMode: null, afterMode: '100644', beforeOid: null, afterOid: '3'.repeat(40), beforeSize: null, afterSize: 4, contentRef: 'blob-1' },
  ];
  const checkpoint = sealed({
    format: 1, mode: 'self_evolution_merge_checkpoint_v1', taskId: candidate.taskId, attemptId: candidate.attemptId,
    candidateId: candidate.candidateId, candidateDigest: candidate.candidateDigest,
    baselineManifestDigest: candidate.baselineManifestDigest, stableState, candidateHead: oid,
    candidateWorktreeDigest: '4'.repeat(64), selectedFiles, verificationRefs, privateBlobSetRef: 'blob-set-1',
  }, 'checkpointDigest');
  const preview = sealed({
    format: 1, mode: 'self_evolution_promotion_preview_v1', taskId: candidate.taskId, attemptId: candidate.attemptId,
    candidateId: candidate.candidateId, candidateDigest: candidate.candidateDigest, checkpointDigest: checkpoint.checkpointDigest,
    stableState, stableStateDigest: sha(canonical(stableState)), selectedFiles, verificationRefs, blockers: [],
  }, 'previewDigest');
  const nativeConfirmation = {
    format: 1, mode: 'self_evolution_native_confirmation_v1', decision: 'accepted',
    taskId: candidate.taskId, attemptId: candidate.attemptId, candidateId: candidate.candidateId,
    candidateDigest: candidate.candidateDigest, checkpointDigest: checkpoint.checkpointDigest,
    stableStateDigest: preview.stableStateDigest, previewDigest: preview.previewDigest,
    selectedPaths: selectedFiles.map(file => file.path), operationId: 'operation-1', nonce: 'nonce-1',
    issuer: 'electron-main', issuedAt: '2026-11-15T00:00:00.000Z',
  };
  const intent = sealed({
    format: 1, mode: 'self_evolution_merge_intent_v1', state: 'prepared', operationId: nativeConfirmation.operationId,
    taskId: candidate.taskId, attemptId: candidate.attemptId, candidateId: candidate.candidateId,
    candidateDigest: candidate.candidateDigest, checkpointDigest: checkpoint.checkpointDigest,
    stableStateDigest: preview.stableStateDigest, previewDigest: preview.previewDigest, confirmationNonce: nativeConfirmation.nonce,
  }, 'intentDigest');
  return { candidate, checkpoint, preview, nativeConfirmation, intent };
}
const trustedMain = { verifyNativeConfirmation: receipt => receipt.issuer === 'electron-main' };
function bindSelectedPaths(value, selectedPaths) {
  const files = value.checkpoint.selectedFiles;
  files.forEach((file, index) => { file.path = selectedPaths[index]; });
  files.sort((left, right) => left.path.localeCompare(right.path, 'en'));
  value.checkpoint = sealed(value.checkpoint, 'checkpointDigest');
  value.preview.selectedFiles = value.checkpoint.selectedFiles;
  value.preview.checkpointDigest = value.checkpoint.checkpointDigest;
  value.preview = sealed(value.preview, 'previewDigest');
  value.nativeConfirmation.checkpointDigest = value.checkpoint.checkpointDigest;
  value.nativeConfirmation.previewDigest = value.preview.previewDigest;
  value.nativeConfirmation.selectedPaths = value.preview.selectedFiles.map(file => file.path);
  value.intent.checkpointDigest = value.checkpoint.checkpointDigest;
  value.intent.previewDigest = value.preview.previewDigest;
  value.intent = sealed(value.intent, 'intentDigest');
  return value;
}

test('strict contract binds exact candidate, checkpoint, preview, native receipt and prepared intent', t => {
  const root = fixture(t), value = contract();
  const head = git(root, 'rev-parse', 'HEAD'), status = git(root, 'status', '--porcelain=v1', '-z');
  const result = preflightSelfEvolutionSelectedMerge(value, trustedMain);
  assert.equal(result.status, 'blocked');
  assert.equal(result.code, 'self_evolution_merge_integration_required');
  assert.equal(result.operationId, 'operation-1');
  assert.equal(result.sourceMergeExecuted, false);
  assert.equal(result.readyForLaunch, false);
  assert.deepEqual(result.executionGates, { trustedNativeConfirmationConnected: false, completeManifestRevalidated: false, transactionalWritePathConnected: false });
  assert.equal(selfEvolutionPromotionPreviewDigest(value.preview), value.preview.previewDigest);
  assert.equal(git(root, 'rev-parse', 'HEAD'), head);
  assert.equal(git(root, 'status', '--porcelain=v1', '-z'), status);
});

test('strict contract failure stays read-only against a temporary real Git repository', t => {
  const root = fixture(t), head = git(root, 'rev-parse', 'HEAD');
  const status = git(root, 'status', '--porcelain=v1', '-z');
  const bytes = readFileSync(path.join(root, 'tracked.txt'));
  const value = contract(); value.preview.selectedFiles[0].path = '../secret.txt';
  assert.throws(() => preflightSelfEvolutionSelectedMerge(value, trustedMain), { code: 'self_evolution_files_invalid' });
  assert.equal(git(root, 'rev-parse', 'HEAD'), head);
  assert.equal(git(root, 'status', '--porcelain=v1', '-z'), status);
  assert.deepEqual(readFileSync(path.join(root, 'tracked.txt')), bytes);
});

test('refuses confirmation unless trusted native verifier accepts the exact receipt', () => {
  const value = contract();
  assert.throws(() => preflightSelfEvolutionSelectedMerge(value), { code: 'self_evolution_confirmation_untrusted' });
  assert.throws(() => preflightSelfEvolutionSelectedMerge(value, { verifyNativeConfirmation: () => false }), { code: 'self_evolution_confirmation_untrusted' });
});

test('rejects stale bindings, failed evidence, blockers and consumed/unknown intents', () => {
  const mismatch = contract(); mismatch.nativeConfirmation.previewDigest = 'd'.repeat(64);
  assert.throws(() => preflightSelfEvolutionSelectedMerge(mismatch, trustedMain), { code: 'self_evolution_confirmation_mismatch' });
  const failed = contract(); failed.preview.verificationRefs[0].status = 'failed';
  assert.throws(() => preflightSelfEvolutionSelectedMerge(failed, trustedMain), { code: 'self_evolution_verification_unbound' });
  const blocked = contract(); blocked.preview.blockers = ['attribution_unknown']; blocked.preview = sealed(blocked.preview, 'previewDigest');
  assert.throws(() => preflightSelfEvolutionSelectedMerge(blocked, trustedMain), { code: 'self_evolution_preview_blocked' });
  const consumed = contract(); consumed.intent.state = 'consumed';
  assert.throws(() => preflightSelfEvolutionSelectedMerge(consumed, trustedMain), { code: 'self_evolution_intent_unavailable' });
  const unknown = contract(); unknown.intent.state = 'unknown';
  assert.throws(() => preflightSelfEvolutionSelectedMerge(unknown, trustedMain), { code: 'self_evolution_intent_unavailable' });
});

test('rejects changed stable identity and candidate evidence not bound to candidate digest', () => {
  const value = contract(); value.preview.stableState.worktreeId = 'substituted';
  value.preview.stableStateDigest = sha(canonical(value.preview.stableState));
  value.preview = sealed(value.preview, 'previewDigest');
  value.nativeConfirmation.stableStateDigest = value.preview.stableStateDigest;
  value.nativeConfirmation.previewDigest = value.preview.previewDigest;
  value.intent.stableStateDigest = value.preview.stableStateDigest;
  value.intent.previewDigest = value.preview.previewDigest;
  value.intent = sealed(value.intent, 'intentDigest');
  assert.throws(() => preflightSelfEvolutionSelectedMerge(value, trustedMain), { code: 'self_evolution_checkpoint_identity_mismatch' });
  const badEvidence = contract(); badEvidence.preview.verificationRefs[0].candidateDigest = '9'.repeat(64);
  badEvidence.preview = sealed(badEvidence.preview, 'previewDigest');
  assert.throws(() => preflightSelfEvolutionSelectedMerge(badEvidence, trustedMain), { code: 'self_evolution_verification_unbound' });
});



test('rejects a correctly sealed intent rebound to a different native-confirmation nonce without touching Git', t => {
  const root = fixture(t), head = git(root, 'rev-parse', 'HEAD');
  const status = git(root, 'status', '--porcelain=v1', '-z');
  const value = contract();
  value.intent.confirmationNonce = 'substituted-nonce';
  value.intent = sealed(value.intent, 'intentDigest');

  assert.throws(() => preflightSelfEvolutionSelectedMerge(value, trustedMain), { code: 'self_evolution_intent_mismatch' });
  assert.equal(git(root, 'rev-parse', 'HEAD'), head);
  assert.equal(git(root, 'status', '--porcelain=v1', '-z'), status);
});

test('reuses managed selected-path policy and rejects additional private path classes', () => {
  const value = contract();
  value.checkpoint.selectedFiles[0].path = 'desktop/config-store/settings.ts';
  assert.throws(() => preflightSelfEvolutionSelectedMerge(value, trustedMain), { code: 'self_evolution_files_invalid' });
});

test('rejects extensionless and tokenized sensitive paths case-insensitively across every path segment', () => {
  const cases = [
    ['agent-core/keys/signing', 'agent-core/keys/signing-helper', 'agent-core/keys/signing2'],
    ['agent-core/KEYS/signing', 'agent-core/keys/signing-helper', 'agent-core/keys/signing2'],
    ['desktop/certificates/private-material', 'desktop/certificates/private-helper', 'desktop/certificates/private2'],
    ['desktop/credentials/token', 'desktop/credentials/token-helper', 'desktop/credentials/token2'],
    ['desktop/identity/id_ed25519', 'desktop/identity/id_rsa', 'desktop/identity/known_hosts'],
  ];
  for (const paths of cases) {
    const value = bindSelectedPaths(contract(), paths);
    assert.throws(() => preflightSelfEvolutionSelectedMerge(value, trustedMain), { code: 'self_evolution_files_invalid' }, paths[0]);
  }
});
test('native verifier exceptions fail closed as untrusted instead of escaping as host errors', () => {
  const value = contract();
  assert.throws(
    () => preflightSelfEvolutionSelectedMerge(value, { verifyNativeConfirmation: () => { throw new Error('native state unavailable'); } }),
    { code: 'self_evolution_confirmation_untrusted' },
  );
});
