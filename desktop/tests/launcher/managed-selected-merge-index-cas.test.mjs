import test from 'node:test';
import assert from 'node:assert/strict';
import { constants as fsConstants, mkdirSync, writeFileSync, readFileSync, lstatSync, readdirSync, rmSync, realpathSync, unlinkSync, openSync, closeSync, writeSync, fsyncSync, renameSync, existsSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const gitExe = 'C:/Program Files/Git/cmd/git.exe';
function git(cwd, args, input) {
  return spawnSync(gitExe, ['-c', `safe.directory=${cwd}`, '-c', 'core.hooksPath=NUL', '-c', 'commit.gpgSign=false', '-c', 'protocol.allow=never', ...args], {
    cwd, input, encoding: 'utf8', windowsHide: true,
    env: { ...process.env, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: 'NUL', GIT_TERMINAL_PROMPT: '0' },
  });
}
function fixture(t) {
  const artifactRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../artifacts');
  mkdirSync(artifactRoot, { recursive: true });
  const root = path.join(artifactRoot, `index-cas-${randomUUID()}`);
  mkdirSync(root);
  const identity = lstatSync(root);
  t.after(() => {
    const target = path.resolve(root);
    if (path.dirname(target) !== artifactRoot || !path.basename(target).startsWith('index-cas-') || realpathSync.native(target) !== target) throw new Error('cleanup target escaped the dedicated fixture directory');
    const scan = dir => { for (const entry of readdirSync(dir, { withFileTypes: true })) { const full = path.join(dir, entry.name), st = lstatSync(full); if (st.isSymbolicLink() || (st.attributes & 0x400) || (!st.isDirectory() && !st.isFile())) throw new Error('cleanup rejected linked or unsupported fixture content'); if (st.isDirectory()) scan(full); } };
    scan(target);
    const current = lstatSync(target);
    if (!current.isDirectory() || current.dev !== identity.dev || current.ino !== identity.ino || current.birthtimeMs !== identity.birthtimeMs) throw new Error('cleanup target identity changed');
    rmSync(target, { recursive: true, force: false });
  });
  const repo = path.join(root, 'repo'); mkdirSync(repo);
  const initial = Buffer.from('selected base\n'); writeFileSync(path.join(repo, 'selected.txt'), initial); writeFileSync(path.join(repo, 'unselected.txt'), 'unchanged index entry\n');
  for (const args of [['init','-b','main'], ['config','user.name','Fixture'], ['config','user.email','fixture@example.invalid'], ['add','.'], ['commit','-m','initial']]) {
    const result = git(repo, args); assert.equal(result.status, 0, result.stderr);
  }
  const oid = git(repo, ['rev-parse', 'HEAD:selected.txt']); assert.equal(oid.status, 0);
  const gitDir = git(repo, ['rev-parse', '--path-format=absolute', '--git-dir']); assert.equal(gitDir.status, 0);
  const unselectedOid = git(repo, ['rev-parse', 'HEAD:unselected.txt']); assert.equal(unselectedOid.status, 0);
  return { repo, gitDir: gitDir.stdout.trim(), index: path.join(gitDir.stdout.trim(), 'index'), oid: oid.stdout.trim(), unselectedOid: unselectedOid.stdout.trim(), initial };
}

test('selected index-only stage between boundary and managed cacheinfo is overwritten (TOCTOU reproduction)', t => {
  const f = fixture(t);
  const originalIndex = readFileSync(f.index), workingFile = path.join(f.repo, 'selected.txt');
  const workingBefore = readFileSync(workingFile);

  // Model the last boundary's selected-entry validation.
  const boundaryOid = git(f.repo, ['rev-parse', ':selected.txt']);
  assert.equal(boundaryOid.status, 0);
  assert.equal(boundaryOid.stdout.trim(), f.oid);

  // A separate Git process changes only the real index after that check.
  const concurrentBytes = Buffer.from('external index-only selected stage\n');
  const concurrentBlob = git(f.repo, ['hash-object', '-w', '--stdin'], concurrentBytes);
  assert.equal(concurrentBlob.status, 0, concurrentBlob.stderr);
  const externalStage = git(f.repo, ['update-index', '--cacheinfo', `100644,${concurrentBlob.stdout.trim()},selected.txt`]);
  assert.equal(externalStage.status, 0, externalStage.stderr);
  assert.deepEqual(readFileSync(workingFile), workingBefore, 'the external update is index-only');
  assert.equal(git(f.repo, ['rev-parse', ':selected.txt']).stdout.trim(), concurrentBlob.stdout.trim());

  // This is the managed per-file update-index --cacheinfo write. Git's own
  // index.lock serializes this command but does not CAS the boundary snapshot.
  const managedWrite = git(f.repo, ['update-index', '--cacheinfo', `100644,${f.oid},selected.txt`]);
  assert.equal(managedWrite.status, 0, managedWrite.stderr);
  assert.equal(git(f.repo, ['rev-parse', ':selected.txt']).stdout.trim(), f.oid);
  assert.notEqual(git(f.repo, ['rev-parse', ':selected.txt']).stdout.trim(), concurrentBlob.stdout.trim());
  assert.deepEqual(readFileSync(workingFile), workingBefore, 'neither index-only update changes working-tree bytes');
  assert.notDeepEqual(readFileSync(f.index), originalIndex, 'the concurrent staged state was replaced in the real index');
});

test('Git-compatible index.lock blocks writers and atomically renames a verified candidate over index on Windows', t => {
  const f = fixture(t), original = readFileSync(f.index), candidate = path.join(path.dirname(f.repo), 'candidate.index');
  const externalBytes = Buffer.from('candidate selected content\n');
  const blob = git(f.repo, ['hash-object', '-w', '--stdin'], externalBytes); assert.equal(blob.status, 0, blob.stderr);
  writeFileSync(candidate, original, { flag: 'wx' });
  const env = { ...process.env, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: 'NUL', GIT_TERMINAL_PROMPT: '0', GIT_INDEX_FILE: candidate };
  const privateUpdate = spawnSync(gitExe, ['-c', `safe.directory=${f.repo}`, 'update-index', '--cacheinfo', `100644,${blob.stdout.trim()},selected.txt`], { cwd: f.repo, encoding: 'utf8', windowsHide: true, env });
  assert.equal(privateUpdate.status, 0, privateUpdate.stderr);
  const candidateBytes = readFileSync(candidate);
  const lockPath = `${f.index}.lock`;
  const fd = openSync(lockPath, fsConstants.O_CREAT | fsConstants.O_EXCL | fsConstants.O_WRONLY, 0o666);
  try { let offset = 0; while (offset < candidateBytes.length) offset += writeSync(fd, candidateBytes, offset, candidateBytes.length - offset); fsyncSync(fd); }
  finally { closeSync(fd); }
  const competingWriter = git(f.repo, ['update-index', '--cacheinfo', `100644,${f.oid},selected.txt`]);
  assert.notEqual(competingWriter.status, 0, 'Git writer must honor our exclusive index.lock');
  assert.deepEqual(readFileSync(f.index), original, 'real index remains unchanged while candidate lock is held');
  assert.deepEqual(readFileSync(lockPath), candidateBytes, 'lock contains the complete private candidate');
  renameSync(lockPath, f.index);
  assert.equal(existsSync(lockPath), false, 'atomic commit consumes index.lock');
  assert.deepEqual(readFileSync(f.index), candidateBytes, 'Windows rename atomically replaces the prior index bytes');
  assert.equal(git(f.repo, ['rev-parse', ':selected.txt']).stdout.trim(), blob.stdout.trim());
  assert.equal(git(f.repo, ['rev-parse', ':unselected.txt']).stdout.trim(), f.unselectedOid, 'unselected index entry is preserved');
  assert.deepEqual(readFileSync(path.join(f.repo, 'selected.txt')), f.initial, 'index publication does not touch worktree bytes');
  unlinkSync(candidate);
});
import { createHash } from 'node:crypto';
import { captureManagedSourceCheckpoint } from '../../launcher/managed-source-checkpoint.mjs';
import { createManagedSourceSelectionProposal } from '../../launcher/managed-source-selection.mjs';
import { prepareManagedSelectedMerge, executeManagedSelectedMerge } from '../../launcher/managed-selected-merge.mjs';
const sha256 = value => createHash('sha256').update(value).digest('hex');
const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(',')}]` : value && typeof value === 'object' ? `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}` : JSON.stringify(value);
const seal = (value, key) => { delete value[key]; value[key] = sha256(Buffer.from(canonical(value))); return value; };

test('managed preservation refuses selected index-only staging after its boundary check', t => {
  const f = fixture(t), selectedPath = 'desktop/selected.txt', keepPath = 'docs/keep.md';
  const preserved = Buffer.from('stable preserved bytes\n'), selected = Buffer.from('sandbox selected bytes\n'), keep = Buffer.from('keep\n');
  const writeSource = (root, rel, bytes) => { const full = path.join(root, ...rel.split('/')); mkdirSync(path.dirname(full), { recursive: true }); writeFileSync(full, bytes); };
  writeSource(f.repo, selectedPath, Buffer.from('base\n')); writeSource(f.repo, keepPath, keep);
  for (const args of [['add', '.'], ['-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','managed fixture']]) { const r = git(f.repo, args); assert.equal(r.status, 0, r.stderr); }
  const sandbox = path.join(f.repo, '.worktrees', 'sandbox'); mkdirSync(path.dirname(sandbox), { recursive: true });
  const addWorktree = git(f.repo, ['worktree', 'add', '-b', 'experiment/sandbox', sandbox, 'HEAD']); assert.equal(addWorktree.status, 0, addWorktree.stderr);
  writeSource(f.repo, selectedPath, preserved); writeSource(sandbox, selectedPath, selected);
  const oid = (root, bytes) => { const r = git(root, ['hash-object', '-w', '--stdin'], bytes); assert.equal(r.status, 0, r.stderr); return r.stdout.trim(); };
  const lane = (root, branch, entries) => {
    const gitDir = git(root, ['rev-parse', '--path-format=absolute', '--git-dir']).stdout.trim();
    const commonDir = git(root, ['rev-parse', '--path-format=absolute', '--git-common-dir']).stdout.trim();
    const head = git(root, ['rev-parse', 'HEAD']).stdout.trim(), indexBytes = readFileSync(path.join(gitDir, 'index')), indexDigest = sha256(indexBytes);
    return { identity: { worktreeRoot: root, gitDir, commonDir }, head, branch, indexDigest, inventory: seal({ format: 1, mode: 'managed_allowed_source_inventory_v1', root, head, indexDigest, entries, excluded: [], blockers: [{ code: 'review_only_not_execution', paths: [] }], limitations: [], execution: false, readyForMerge: false, readyForLaunch: false }, 'inventory_digest') };
  };
  const sourceEntry = (root, file, base, working) => ({ path: file, headOid: oid(root, base), headMode: '100644', indexOid: oid(root, base), indexMode: '100644', workingSha256: sha256(working), workingOid: oid(root, working), staged: false, rawDiverged: !base.equals(working), modeDiverged: false, state: base.equals(working) ? 'clean' : 'raw_diverged', mode: '100644', size: working.length });
  const base = Buffer.from('base\n');
  const stable = lane(f.repo, 'main', [sourceEntry(f.repo, selectedPath, base, preserved), sourceEntry(f.repo, keepPath, keep, keep)]);
  const sandboxLane = lane(sandbox, 'experiment/sandbox', [sourceEntry(sandbox, selectedPath, base, selected), sourceEntry(sandbox, keepPath, keep, keep)]);
  const review = seal({ format: 1, mode: 'managed_source_review_v1', inspectionDigest: sha256(Buffer.from('index-race')), stable, sandbox: sandboxLane, history: { mergeBase: stable.head, commits: [], paths: [] }, effectiveDelta: { basis: 'merge_base_to_sandbox_head', paths: [] }, blockers: [{ code: 'review_only_not_execution', paths: [] }], execution: false, readyForMerge: false, readyForLaunch: false }, 'review_digest');
  const request = { format: 1, mode: 'managed_source_selection_request_v1', reviewDigest: review.review_digest, selectedPaths: [selectedPath] };
  const proposal = createManagedSourceSelectionProposal({ review, request });
  const checkpoint = captureManagedSourceCheckpoint({ review, request: { format: 1, mode: 'managed_source_checkpoint_request_v1', reviewDigest: review.review_digest, proposalDigest: proposal.proposal_digest, selectedPaths: [selectedPath] }, stableRoot: f.repo, sandboxRoot: sandbox });
  const plan = prepareManagedSelectedMerge({ stableRoot: f.repo, sandboxRoot: sandbox, checkpoint, gitExecutable: gitExe, preserveStableWorkingChanges: true });
  assert.equal(plan.stablePreservation.files.length, 1);
  const realIndex = path.join(plan.stable.gitDir, 'index');
  const beforeHead = git(f.repo, ['rev-parse', 'HEAD']).stdout.trim();
  const beforeKeepOid = git(f.repo, ['rev-parse', `:${keepPath}`]).stdout.trim();
  const beforeSelectedBytes = readFileSync(path.join(f.repo, ...selectedPath.split('/')));
  const beforeKeepBytes = readFileSync(path.join(f.repo, ...keepPath.split('/')));
  let raced = false, concurrentOid = null;
  const assertTrusted = () => {
    if (!raced && git(f.repo, ['cat-file', '-e', `${plan.stablePreservation.expectedCommit}^{commit}`]).status === 0) {
      concurrentOid = oid(f.repo, Buffer.from('external selected index-only stage\n'));
      const write = git(f.repo, ['update-index', '--cacheinfo', `100644,${concurrentOid},${selectedPath}`]);
      assert.equal(write.status, 0, write.stderr); raced = true;
    }
    return true;
  };
  assert.throws(() => executeManagedSelectedMerge({ plan, checkpoint, stableRoot: f.repo, sandboxRoot: sandbox, gitExecutable: gitExe, assertTrusted }), e => e.code === 'selected_merge_index_race_detected');
  assert.equal(raced, true, 'the competing index-only write must occur after boundary validation and before managed publication');
  assert.equal(git(f.repo, ['rev-parse', 'HEAD']).stdout.trim(), beforeHead, 'raced publication must not fast-forward');
  assert.equal(git(f.repo, ['rev-parse', `:${selectedPath}`]).stdout.trim(), concurrentOid, 'real index must retain the external selected OID');
  assert.equal(git(f.repo, ['rev-parse', `:${keepPath}`]).stdout.trim(), beforeKeepOid, 'unselected index entry must not change');
  assert.deepEqual(readFileSync(path.join(f.repo, ...selectedPath.split('/'))), beforeSelectedBytes, 'selected worktree bytes must remain unchanged');
  assert.deepEqual(readFileSync(path.join(f.repo, ...keepPath.split('/'))), beforeKeepBytes, 'unselected worktree bytes must remain unchanged');
  assert.equal(readFileSync(realIndex).length > 0, true);


});

test('ff-only fast-forward rejects a selected index-only stage made after lock release', t => {
  const f = fixture(t), selected = path.join(f.repo, 'selected.txt');
  const targetBytes = Buffer.from('incoming selected bytes\n');
  const branch = git(f.repo, ['switch', '-c', 'ff-target']); assert.equal(branch.status, 0, branch.stderr);
  writeFileSync(selected, targetBytes);
  for (const args of [['add', 'selected.txt'], ['-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','ff target']]) { const r = git(f.repo, args); assert.equal(r.status, 0, r.stderr); }
  const target = git(f.repo, ['rev-parse', 'HEAD']).stdout.trim();
  const back = git(f.repo, ['switch', 'main']); assert.equal(back.status, 0, back.stderr);
  writeFileSync(selected, targetBytes); // worktree already has the incoming bytes
  const concurrent = git(f.repo, ['hash-object', '-w', '--stdin'], Buffer.from('external staged selected bytes\n')); assert.equal(concurrent.status, 0, concurrent.stderr);
  const stage = git(f.repo, ['update-index', '--cacheinfo', `100644,${concurrent.stdout.trim()},selected.txt`]); assert.equal(stage.status, 0, stage.stderr);
  const originalHead = git(f.repo, ['rev-parse', 'HEAD']).stdout.trim();
  const ff = git(f.repo, ['merge', '--ff-only', '--no-edit', target]);
  assert.notEqual(ff.status, 0, 'ff-only must not silently replace a concurrently staged selected entry');
  assert.equal(git(f.repo, ['rev-parse', 'HEAD']).stdout.trim(), originalHead, 'failed ff leaves HEAD unchanged');
  assert.equal(git(f.repo, ['rev-parse', ':selected.txt']).stdout.trim(), concurrent.stdout.trim(), 'failed ff retains the external selected index entry');
  assert.deepEqual(readFileSync(selected), targetBytes, 'failed ff does not change worktree bytes');
});
