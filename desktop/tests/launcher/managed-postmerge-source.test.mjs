import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { captureManagedPostmergeSource } from '../../launcher/managed-postmerge-source.mjs';
import { inspectManagedSelectedMergeSourceIdentity } from '../../launcher/managed-selected-merge.mjs';

const ROOT = realpathSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..'));
const ARTIFACTS = path.join(ROOT, 'artifacts', 'continuous-closeout-20261025');
const PROBE_ROOT = path.join(ARTIFACTS, 'git-ff-local-probe');
const PROBE_OP = path.join(PROBE_ROOT, '.worktrees', 'sandbox', 'artifacts', 'managed-selected-merges', 'ba2a0507ac32da66c120de9b5fd038b920f5799eac9a0c290aca480849c89d66');
const OWNED_ROOT = path.join(ARTIFACTS, 'postmerge-fixture');
const OWNED_REPO = path.join(OWNED_ROOT, 'managed-repo');
const GIT_EXE = (() => {
  const locator = spawnSync(process.platform === 'win32' ? 'where.exe' : 'which', [process.platform === 'win32' ? 'git.exe' : 'git'], { encoding: 'utf8', windowsHide: true });
  const candidate = locator.stdout?.split(/\r?\n/).find(value => value.trim())?.trim();
  if (!candidate) throw new Error('native Git executable not found for focused fixture');
  return realpathSync(candidate);
})();
const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(',')}]` : value && typeof value === 'object' ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}` : JSON.stringify(value);
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
function probeEvidence() {
  const plan = JSON.parse(readFileSync(path.join(PROBE_OP, 'plan.json'), 'utf8'));
  const receipt = JSON.parse(readFileSync(path.join(PROBE_OP, 'receipt.json'), 'utf8'));
  return { plan, receipt, stableRoot: plan.stable.worktreeRoot, sandboxRoot: plan.sandbox.worktreeRoot };
}
function git(cwd, args, input) {
  const env = { PATH: process.env.PATH, SystemRoot: process.env.SystemRoot, WINDIR: process.env.WINDIR, TEMP: process.env.TEMP, TMP: process.env.TMP, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: process.platform === 'win32' ? 'NUL' : '/dev/null', GIT_ATTR_NOSYSTEM: '1', GIT_OPTIONAL_LOCKS: '0', GIT_TERMINAL_PROMPT: '0', GIT_AUTHOR_NAME: 'Postmerge Fixture', GIT_AUTHOR_EMAIL: 'postmerge-fixture@invalid', GIT_COMMITTER_NAME: 'Postmerge Fixture', GIT_COMMITTER_EMAIL: 'postmerge-fixture@invalid', GIT_AUTHOR_DATE: '@1700000000 +0000', GIT_COMMITTER_DATE: '@1700000000 +0000' };
  const result = spawnSync('git', ['-c', `safe.directory=${cwd}`, '-c', 'commit.gpgSign=false', ...args], { cwd, env, input, encoding: 'buffer', windowsHide: true, maxBuffer: 4 * 1024 * 1024 });
  if (result.error || result.status !== 0) throw new Error(`fixture Git command failed: ${args[0]} ${result.stderr?.toString('utf8')}`);
  return result.stdout.toString('utf8').trim();
}
function persistFixtureEvidence(evidence, stableIndexBytes, checkpointBytes) {
  const { sandboxRoot, plan, receipt } = evidence;
  const operation = path.join(sandboxRoot, 'artifacts', 'managed-selected-merges', plan.planDigest);
  mkdirSync(operation, { recursive: true });
  const planBytes = Buffer.from(`${canonical(plan)}\n`);
  const intent = { format: 2, mode: 'managed_selected_merge_intent_v2', planDigest: plan.planDigest, checkpointDigest: plan.checkpointDigest, selectedPaths: [...plan.selectedPaths], expectedStableHeadBefore: plan.stable.head, expectedStableHeadAfter: plan.expectedCommit, expectedTree: plan.expectedMergedTree, planFileSha256: sha(planBytes), checkpointFileSha256: sha(checkpointBytes), stableIndexBeforeSha256: sha(stableIndexBytes), state: 'started' };
  writeFileSync(path.join(operation, 'intent.json'), `${canonical(intent)}\n`, { flag: 'wx' });
  writeFileSync(path.join(operation, 'plan.json'), planBytes, { flag: 'wx' });
  writeFileSync(path.join(operation, 'checkpoint.json'), checkpointBytes, { flag: 'wx' });
  writeFileSync(path.join(operation, 'stable-index.before'), stableIndexBytes, { flag: 'wx' });
  writeFileSync(path.join(operation, 'receipt.json'), `${canonical(receipt)}\n`, { flag: 'wx' });
}
function ensureOwnedFixture() {
  const evidencePath = path.join(OWNED_REPO, '.fixture-evidence.json');
  if (existsSync(evidencePath)) {
    const evidence = JSON.parse(readFileSync(evidencePath, 'utf8'));
    const actual = inspectManagedSelectedMergeSourceIdentity({ stableRoot: evidence.stableRoot, sandboxRoot: evidence.sandboxRoot, gitExecutable: GIT_EXE });
    const identityPaths = [evidence.plan.stable.worktreeRoot,evidence.plan.stable.gitDir,evidence.plan.stable.commonDir,evidence.plan.sandbox.worktreeRoot,evidence.plan.sandbox.gitDir,evidence.plan.sandbox.commonDir];
    if (evidence.plan.gitMetadataDigest !== actual.gitMetadataDigest || identityPaths.some(value => path.resolve(value) !== value)) {
      const oldOperation = path.join(evidence.sandboxRoot, 'artifacts', 'managed-selected-merges', evidence.plan.planDigest);
      const indexBytes = readFileSync(path.join(oldOperation, 'stable-index.before'));
      const checkpointBytes = readFileSync(path.join(oldOperation, 'checkpoint.json'));
      const plan = structuredClone(evidence.plan);
      plan.stable.commonDir = path.resolve(plan.stable.commonDir);
      plan.sandbox.gitDir = path.resolve(plan.sandbox.gitDir); plan.sandbox.commonDir = path.resolve(plan.sandbox.commonDir);
      plan.gitMetadataDigest = actual.gitMetadataDigest; delete plan.planDigest; plan.planDigest = sha(Buffer.from(canonical(plan)));
      const receipt = { ...evidence.receipt, planDigest: plan.planDigest }; delete receipt.receiptDigest; receipt.receiptDigest = sha(Buffer.from(canonical(receipt)));
      evidence.plan = plan; evidence.receipt = receipt;
      persistFixtureEvidence(evidence, indexBytes, checkpointBytes);
      writeFileSync(evidencePath, `${JSON.stringify(evidence)}\n`);
    }
    return evidence;
  }
  if (existsSync(OWNED_REPO)) throw new Error('owned postmerge Git fixture exists without its evidence marker; refusing to clean or overwrite it');
  mkdirSync(OWNED_REPO, { recursive: true });
  mkdirSync(path.join(OWNED_REPO, 'desktop'), { recursive: true });
  writeFileSync(path.join(OWNED_REPO, 'desktop', 'selected.mjs'), 'stable baseline\n', { flag: 'wx' });
  mkdirSync(path.join(OWNED_REPO, 'docs'), { recursive: true });
  writeFileSync(path.join(OWNED_REPO, 'docs', 'readme.md'), 'fixture source\n', { flag: 'wx' });
  git(OWNED_REPO, ['init', '-b', 'main']);
  git(OWNED_REPO, ['add', '--', 'desktop/selected.mjs', 'docs/readme.md']);
  git(OWNED_REPO, ['commit', '--no-gpg-sign', '-m', 'fixture baseline']);
  const base = git(OWNED_REPO, ['rev-parse', 'HEAD']);
  const baseTree = git(OWNED_REPO, ['rev-parse', 'HEAD^{tree}']);
  const baseBlob = git(OWNED_REPO, ['rev-parse', 'HEAD:desktop/selected.mjs']);
  const stableIndexBeforeBytes = readFileSync(path.join(OWNED_REPO, '.git', 'index'));
  const stableIndexBefore = sha(stableIndexBeforeBytes);
  const sandboxRoot = path.join(OWNED_REPO, '.worktrees', 'sandbox');
  mkdirSync(path.dirname(sandboxRoot), { recursive: true });
  git(OWNED_REPO, ['worktree', 'add', '-b', 'experiment/sandbox', sandboxRoot, base]);
  const selected = Buffer.from('selected merged source\n');
  writeFileSync(path.join(sandboxRoot, 'desktop', 'selected.mjs'), selected);
  git(sandboxRoot, ['add', '--', 'desktop/selected.mjs']);
  git(sandboxRoot, ['commit', '--no-gpg-sign', '-m', 'sandbox selected source']);
  const sandboxHead = git(sandboxRoot, ['rev-parse', 'HEAD']);
  const sandboxGitDir = path.resolve(git(sandboxRoot, ['rev-parse', '--absolute-git-dir']));
  const sandboxIndex = sha(readFileSync(path.join(sandboxGitDir, 'index')));
  writeFileSync(path.join(OWNED_REPO, 'desktop', 'selected.mjs'), selected);
  git(OWNED_REPO, ['add', '--', 'desktop/selected.mjs']);
  git(OWNED_REPO, ['commit', '--no-gpg-sign', '-m', 'merge selected source']);
  const expectedCommit = git(OWNED_REPO, ['rev-parse', 'HEAD']);
  const expectedMergedTree = git(OWNED_REPO, ['rev-parse', 'HEAD^{tree}']);
  const commonDir = path.resolve(git(OWNED_REPO, ['rev-parse', '--path-format=absolute', '--git-common-dir']));
  const plan = {
    format: 1, mode: 'managed_selected_merge_plan_v1', checkpointDigest: JSON.parse(readFileSync(path.join(PROBE_OP, 'checkpoint.json'), 'utf8')).checkpointDigest,
    stable: { worktreeRoot: OWNED_REPO, gitDir: path.join(OWNED_REPO, '.git'), commonDir, branch: 'main', head: base, rawIndexSha256: stableIndexBefore },
    sandbox: { worktreeRoot: sandboxRoot, gitDir: sandboxGitDir, commonDir, branch: 'experiment/sandbox', head: sandboxHead, rawIndexSha256: sandboxIndex },
    selectedPaths: ['desktop/selected.mjs'],
    files: [{ path: 'desktop/selected.mjs', action: 'replace', rawSha256: sha(selected), mode: '100644', expectedBaseOid: baseBlob, beforeSha256: sha(Buffer.from('stable baseline\n')), expectedBaseMode: '100644' }],
    preservationDigest: sha(Buffer.from('fixture preservation digest')), expectedMergedTree, expectedCommit, sourceHistoryMerged: false,
    gitMetadataDigest: inspectManagedSelectedMergeSourceIdentity({ stableRoot: OWNED_REPO, sandboxRoot, gitExecutable: GIT_EXE }).gitMetadataDigest, preservationBasis: { stable: { sources: [], index: [], tree: [], stash: { refOid: null, reflogOids: [] } }, sandbox: { sources: [], index: [], tree: [], stash: { refOid: null, reflogOids: [] } } },
    temporaryIndexUpperBoundBytes: 4096, blockers: [], execution: false, readyForLaunch: false,
  };
  plan.planDigest = sha(Buffer.from(canonical(plan)));
  const receipt = { format: 1, mode: 'managed_selected_merge_receipt_v1', status: 'source_merge_verified', planDigest: plan.planDigest, checkpointDigest: plan.checkpointDigest, selectedPaths: [...plan.selectedPaths], stableHeadBefore: base, stableHeadAfter: expectedCommit, mergedTree: expectedMergedTree, sourceMergeExecuted: true, sourceHistoryMerged: false, readyForLaunch: false };
  receipt.receiptDigest = sha(Buffer.from(canonical(receipt)));
  const evidence = { stableRoot: OWNED_REPO, sandboxRoot, plan, receipt, base, baseTree };
  const checkpointBytes = readFileSync(path.join(PROBE_OP, 'checkpoint.json'));
  persistFixtureEvidence(evidence, stableIndexBeforeBytes, checkpointBytes);
  writeFileSync(evidencePath, `${JSON.stringify(evidence)}\n`, { flag: 'wx' });
  return evidence;
}

test('captures a digest-bound source snapshot from the committed merged tree, including preservation ancestry', () => {
  const evidence = probeEvidence();
  const result = captureManagedPostmergeSource({ ...evidence, gitExecutable: GIT_EXE });
  assert.equal(result.mode, 'managed_postmerge_source_snapshot_v1');
  assert.equal(result.mergedHead, evidence.receipt.stableHeadAfter);
  assert.equal(result.mergedTree, evidence.receipt.mergedTree);
  assert.equal(result.planDigest, evidence.plan.planDigest);
  assert.equal(result.receiptDigest, evidence.receipt.receiptDigest);
  assert.equal(result.readyForLaunch, false);
  assert.equal(result.buildVerified, false);
  assert.deepEqual(Object.keys(result.files[0]).sort(), ['blobOid','byteLength','mode','path','sha256'].sort());
  assert.equal(result.roots.stable.worktreeRoot, evidence.plan.stable.worktreeRoot);
  assert.ok(result.files.some(file => file.path === 'desktop/selected.mjs' && file.mode === '100644' && file.blobOid && file.sha256 && file.byteLength > 0));
  const { snapshotDigest, ...snapshotBody } = result;
  assert.equal(snapshotDigest, sha(Buffer.from(canonical(snapshotBody))));
  assert.ok(Object.isFrozen(result));
});

test('rejects a modified or resealed receipt before reading Git state', () => {
  const evidence = ensureOwnedFixture();
  const bad = structuredClone(evidence.receipt); bad.receiptDigest = '0'.repeat(64);
  assert.throws(() => captureManagedPostmergeSource({ ...evidence, receipt: bad, gitExecutable: GIT_EXE }), /receipt|digest|invalid/i);
});

test('rejects selected working-byte drift against the plan on the owned Git fixture', () => {
  const evidence = ensureOwnedFixture();
  const selectedPath = path.join(evidence.stableRoot, 'desktop', 'selected.mjs');
  const original = readFileSync(selectedPath), forged = Buffer.from('forged selected working bytes\n');
  try {
    writeFileSync(selectedPath, forged);
    assert.throws(() => captureManagedPostmergeSource({ ...evidence, gitExecutable: GIT_EXE }), /selected committed, working, or index content differs from plan/i);
  } finally {
    writeFileSync(selectedPath, original);
  }
  assert.deepEqual(readFileSync(selectedPath), original);
});

test('rejects stable HEAD drift and restores only the owned fixture ref', () => {
  const evidence = ensureOwnedFixture();
  const current = git(evidence.stableRoot, ['rev-parse', 'HEAD']);
  if (current === evidence.base) git(evidence.stableRoot, ['update-ref', 'refs/heads/main', evidence.plan.expectedCommit]);
  else assert.equal(current, evidence.plan.expectedCommit, 'owned fixture HEAD must be expected commit or known test base');
  assert.equal(git(evidence.stableRoot, ['rev-parse', 'HEAD']), evidence.plan.expectedCommit);
  try {
    git(evidence.stableRoot, ['update-ref', 'refs/heads/main', evidence.base]);
    assert.throws(() => captureManagedPostmergeSource({ ...evidence, gitExecutable: GIT_EXE }), /actual stable HEAD differs from verified merge receipt/i);
  } finally {
    const after = git(evidence.stableRoot, ['rev-parse', 'HEAD']);
    if (after === evidence.base) git(evidence.stableRoot, ['update-ref', 'refs/heads/main', evidence.plan.expectedCommit]);
    else assert.equal(after, evidence.plan.expectedCommit, 'owned fixture HEAD changed to an unexpected object');
  }
  assert.equal(git(evidence.stableRoot, ['rev-parse', 'HEAD']), evidence.plan.expectedCommit);
});
