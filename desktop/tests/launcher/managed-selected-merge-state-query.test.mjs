import test from 'node:test';
import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { createHash } from 'node:crypto';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, lstatSync, readdirSync, realpathSync, rmSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import * as current from '../../launcher/managed-selected-merge.mjs';
import { captureManagedSourceCheckpoint } from '../../launcher/managed-source-checkpoint.mjs';
import { createManagedSourceSelectionProposal } from '../../launcher/managed-source-selection.mjs';
const { inspectManagedSelectedMergeSourceIdentity } = current;

const git = 'C:/Program Files/Git/cmd/git.exe';
const parent = path.dirname(fileURLToPath(import.meta.url));
const digest = data => createHash('sha256').update(data).digest('hex');
function gitRun(cwd, ...args) {
  const result = childProcess.spawnSync(git, ['-c', `safe.directory=${cwd}`, '-c', 'core.hooksPath=NUL', '-c', 'commit.gpgSign=false', ...args], {
    cwd, windowsHide: true, encoding: 'utf8', env: { ...process.env, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: 'NUL', GIT_TERMINAL_PROMPT: '0' },
  });
  assert.equal(result.status, 0, 'isolated fixture Git command failed');
  return result.stdout.replace(/[\r\n]+$/, '');
}
function removeFixture(base, created) {
  const resolvedParent = path.resolve(parent), resolved = path.resolve(base);
  if (path.dirname(resolved) !== resolvedParent || !path.basename(resolved).startsWith('managed state query ') || realpathSync.native(resolved) !== resolved) throw new Error('fixture cleanup boundary rejected');
  let current = path.parse(resolved).root;
  for (const part of resolved.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    const st = lstatSync(current);
    if (st.isSymbolicLink() || (st.attributes & 0x400)) throw new Error('fixture cleanup linked ancestor rejected');
  }
  const st = lstatSync(resolved);
  if (st.dev !== created.dev || st.ino !== created.ino || st.birthtimeMs !== created.birthtimeMs) throw new Error('fixture cleanup identity changed');
  function inspect(dir) {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const f = path.join(dir, entry.name), s = lstatSync(f);
      if (s.isSymbolicLink() || (s.attributes & 0x400) || (!s.isFile() && !s.isDirectory())) throw new Error('fixture cleanup linked or special entry rejected');
      if (s.isDirectory()) inspect(f);
    }
  }
  inspect(resolved); rmSync(resolved, { recursive: true, force: false });
}
function fixture(t) {
  const base = mkdtempSync(path.join(parent, 'managed state query ')), created = lstatSync(base);
  t.after(() => removeFixture(base, created));
  const stable = path.join(base, 'project'), sandbox = path.join(stable, '.worktrees', 'sandbox');
  mkdirSync(path.join(stable, 'desktop'), { recursive: true });
  writeFileSync(path.join(stable, 'desktop', 'selected.mjs'), 'export const x = 1;\n');
  mkdirSync(path.join(stable, 'docs'));
  writeFileSync(path.join(stable, 'docs', 'keep.md'), 'preserved\n');
  gitRun(stable, 'init', '-b', 'main');
  gitRun(stable, 'add', 'desktop/selected.mjs', 'docs/keep.md');
  gitRun(stable, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture');
  gitRun(stable, 'worktree', 'add', '-b', 'experiment/sandbox', sandbox, 'HEAD');
  return { base, stable, sandbox };
}
const inspect = (stable, sandbox) => inspectManagedSelectedMergeSourceIdentity({ stableRoot: stable, sandboxRoot: sandbox, gitExecutable: git });

test('groups git directory lookups per state while retaining real linked-worktree identity and drift checks', t => {
  const { stable, sandbox } = fixture(t), commands = [];
  const original = childProcess.spawnSync;
  childProcess.spawnSync = (file, args, options) => {
    if (String(file).toLowerCase().endsWith('git.exe') && args.includes('rev-parse')) commands.push(args.slice(args.indexOf('rev-parse') + 1));
    return original(file, args, options);
  };
  syncBuiltinESMExports();
  let state;
  try { state = inspect(stable, sandbox); }
  finally { childProcess.spawnSync = original; syncBuiltinESMExports(); }
  const grouped = commands.filter(args => args.includes('--show-toplevel') && args.includes('--git-dir') && args.includes('--git-common-dir'));
  assert.equal(grouped.length, 2, 'each worktree needs one combined directory query');
  assert.equal(commands.length, 4, 'each worktree still verifies HEAD independently');
  for (const [root, actual] of [[stable, state.stable], [sandbox, state.sandbox]]) {
    assert.equal(actual.worktreeRoot, path.resolve(gitRun(root, 'rev-parse', '--show-toplevel')));
    assert.equal(actual.gitDir, path.resolve(root, gitRun(root, 'rev-parse', '--path-format=absolute', '--git-dir')));
    assert.equal(actual.commonDir, path.resolve(root, gitRun(root, 'rev-parse', '--path-format=absolute', '--git-common-dir')));
    assert.equal(actual.head, gitRun(root, 'rev-parse', '--verify', 'HEAD'));
    assert.equal(actual.branch, gitRun(root, 'symbolic-ref', '--quiet', '--short', 'HEAD'));
    assert.equal(actual.rawIndexSha256, digest(readFileSync(path.join(actual.gitDir, 'index'))));
  }
  writeFileSync(path.join(sandbox, 'desktop', 'selected.mjs'), 'export const x = 2;\n');
  gitRun(sandbox, 'add', 'desktop/selected.mjs');
  const after = inspect(stable, sandbox);
  assert.notEqual(after.sandbox.rawIndexSha256, state.sandbox.rawIndexSha256, 'index must be read again rather than cached');
  assert.equal(after.sandbox.rawIndexSha256, digest(readFileSync(path.join(after.sandbox.gitDir, 'index'))));
  const other = path.join(stable, '.worktrees', 'other');
  gitRun(stable, 'worktree', 'add', '-b', 'experiment/other', other, 'HEAD');
  assert.throws(() => inspect(stable, other), e => e.code === 'selected_merge_source_identity_invalid');
  gitRun(sandbox, 'checkout', '--detach', 'HEAD');
  assert.throws(() => inspect(stable, sandbox), e => e.code === 'git_metadata_rejected');
});

const canonical = v => Array.isArray(v) ? `[${v.map(canonical).join(',')}]` : v && typeof v === 'object' ? `{${Object.keys(v).sort().map(k => `${JSON.stringify(k)}:${canonical(v[k])}`).join(',')}}` : JSON.stringify(v);
const seal = (value, key) => { const copy = structuredClone(value); copy[key] = digest(Buffer.from(canonical(value))); return copy; };
const blob = data => createHash('sha1').update(Buffer.from(`blob ${data.length}\0`)).update(data).digest('hex');
function makeCheckpoint(stable, sandbox) {
  const selectedPath = 'desktop/selected.mjs', otherPath = 'docs/keep.md';
  const base = readFileSync(path.join(stable, selectedPath)), other = readFileSync(path.join(stable, otherPath));
  const changed = Buffer.from('export const x = 2;\n');
  writeFileSync(path.join(sandbox, selectedPath), changed);
  const entry = (p, head, work = head) => ({ path: p, headOid: blob(head), headMode: '100644', indexOid: blob(head), indexMode: '100644', workingSha256: digest(work), workingOid: blob(work), staged: false, rawDiverged: !head.equals(work), modeDiverged: false, state: head.equals(work) ? 'clean' : 'raw_diverged', mode: '100644', size: work.length });
  function lane(root, branch, entries) {
    const head = gitRun(root, 'rev-parse', '--verify', 'HEAD');
    const gitDir = path.resolve(root, gitRun(root, 'rev-parse', '--path-format=absolute', '--git-dir'));
    const commonDir = path.resolve(root, gitRun(root, 'rev-parse', '--path-format=absolute', '--git-common-dir'));
    const indexDigest = digest(readFileSync(path.join(gitDir, 'index')));
    const inventory = seal({ format: 1, mode: 'managed_allowed_source_inventory_v1', root, head, indexDigest, entries, excluded: [], blockers: [{ code: 'review_only_not_execution', paths: [] }], limitations: [], execution: false, readyForMerge: false, readyForLaunch: false }, 'inventory_digest');
    return { identity: { worktreeRoot: root, gitDir, commonDir }, head, branch, indexDigest, inventory };
  }
  const a = lane(stable, 'main', [entry(selectedPath, base), entry(otherPath, other)]);
  const b = lane(sandbox, 'experiment/sandbox', [entry(selectedPath, base, changed), entry(otherPath, other)]);
  const review = seal({ format: 1, mode: 'managed_source_review_v1', inspectionDigest: digest('fixture inspection'), stable: a, sandbox: b, history: { mergeBase: b.head, commits: [], paths: [] }, effectiveDelta: { basis: 'merge_base_to_sandbox_head', paths: [] }, blockers: [{ code: 'review_only_not_execution', paths: [] }], execution: false, readyForMerge: false, readyForLaunch: false }, 'review_digest');
  const request = { format: 1, mode: 'managed_source_selection_request_v1', reviewDigest: review.review_digest, selectedPaths: [selectedPath] };
  const proposal = createManagedSourceSelectionProposal({ review, request });
  const checkpoint = captureManagedSourceCheckpoint({ review, request: { format: 1, mode: 'managed_source_checkpoint_request_v1', reviewDigest: review.review_digest, proposalDigest: proposal.proposal_digest, selectedPaths: [selectedPath] }, stableRoot: stable, sandboxRoot: sandbox });
  return { checkpoint, changed };
}

test('write-boundary selected sandbox reread rejects drift after the snapshot was captured', t => {
  const { stable, sandbox } = fixture(t), { checkpoint } = makeCheckpoint(stable, sandbox);
  const plan = current.prepareManagedSelectedMerge({ stableRoot: stable, sandboxRoot: sandbox, checkpoint, gitExecutable: git });
  const selectedSandboxFile = path.join(sandbox, 'desktop', 'selected.mjs');
  const originalReadFileSync = fs.readFileSync;
  const originalWriteFileSync = fs.writeFileSync;
  let selectedSandboxReads = 0;
  fs.readFileSync = function(file, ...args) {
    if (typeof file === 'string' && path.resolve(file) === selectedSandboxFile && ++selectedSandboxReads === 2) {
      originalWriteFileSync(selectedSandboxFile, Buffer.from('raced after snapshot\n'));
    }
    return originalReadFileSync.call(this, file, ...args);
  };
  syncBuiltinESMExports();
  try {
    assert.throws(
      () => current.executeManagedSelectedMerge({ plan, checkpoint, stableRoot: stable, sandboxRoot: sandbox, gitExecutable: git, assertTrusted: () => true }),
      error => error.code === 'sandbox_selected_source_drift',
    );
  } finally {
    fs.readFileSync = originalReadFileSync;
    syncBuiltinESMExports();
  }
  assert.equal(selectedSandboxReads, 2, 'execution rereads the selected sandbox file after the write-boundary snapshot');
});

test('changed index is rejected before the real selected merge starts', t => {
  const { stable, sandbox } = fixture(t), { checkpoint } = makeCheckpoint(stable, sandbox);
  const plan = current.prepareManagedSelectedMerge({ stableRoot: stable, sandboxRoot: sandbox, checkpoint, gitExecutable: git });
  writeFileSync(path.join(stable, 'docs', 'keep.md'), 'index drift\n');
  gitRun(stable, 'add', 'docs/keep.md');
  const before = gitRun(stable, 'rev-parse', '--verify', 'HEAD');
  assert.throws(() => current.executeManagedSelectedMerge({ plan, checkpoint, stableRoot: stable, sandboxRoot: sandbox, gitExecutable: git, assertTrusted: () => true }), e => e.code === 'selected_merge_plan_stale');
  assert.equal(gitRun(stable, 'rev-parse', '--verify', 'HEAD'), before);
});

// Opt-in A/B: identical isolated selected merges, differing only in commonDir batch validation.
test('isolated merge A/B batches the shared object-store and Windows reparse guard', { skip: process.env.MANAGED_OBJECT_GUARD_AB !== '1', timeout: 300000 }, async t => {
  const modulePath = fileURLToPath(new URL('../../launcher/managed-selected-merge.mjs', import.meta.url));
  const moduleUrl = new URL('../../launcher/managed-clean-git-metadata.mjs', import.meta.url).href;
  const batchScript = "const script=`import {assertManagedGitObjectStores} from ${JSON.stringify(moduleUrl)};const input=JSON.parse((await import('node:fs')).readFileSync(0,'utf8'));await assertManagedGitObjectStores(input);process.stdout.write('ok');`;";
  const legacyScript = "const script=`import {assertManagedGitObjectStore} from ${JSON.stringify(moduleUrl)};const input=JSON.parse((await import('node:fs')).readFileSync(0,'utf8'));for(const d of input)await assertManagedGitObjectStore(d);process.stdout.write('ok');`;";
  let baseline = readFileSync(modulePath, 'utf8');
  assert.ok(baseline.includes(batchScript), 'current source must call the batched metadata guard');
  baseline = baseline.replace(batchScript, legacyScript);
  baseline = baseline.replace("new URL('./managed-clean-git-metadata.mjs',import.meta.url).href", JSON.stringify(moduleUrl));
  const launcher = new URL('../../launcher/', import.meta.url);
  baseline = baseline.replace(/from '(\.\/[^']+)'/g, (_, rel) => `from '${new URL(rel, launcher).href}'`);
  const legacy = await import(`data:text/javascript;base64,${Buffer.from(baseline).toString('base64')}`);
  const results = [];
  for (const [label, impl] of [['two-scans', legacy], ['batched-commonDir', current]]) {
    const { stable, sandbox } = fixture(t), { checkpoint } = makeCheckpoint(stable, sandbox);
    const plan = impl.prepareManagedSelectedMerge({ stableRoot: stable, sandboxRoot: sandbox, checkpoint, gitExecutable: git });
    let guardProcesses = 0, guardProcessMs = 0;
    const original = childProcess.spawnSync;
    childProcess.spawnSync = function(file, args, options) {
      const isGuard = path.resolve(String(file)) === path.resolve(process.execPath) && Array.isArray(args) && args.includes('--input-type=module');
      const start = isGuard ? performance.now() : 0;
      try { return original.call(this, file, args, options); }
      finally { if (isGuard) { guardProcesses++; guardProcessMs += performance.now() - start; } }
    };
    syncBuiltinESMExports();
    let receipt, elapsedMs;
    try {
      const start = performance.now();
      receipt = impl.executeManagedSelectedMerge({ plan, checkpoint, stableRoot: stable, sandboxRoot: sandbox, gitExecutable: git, assertTrusted: () => true });
      elapsedMs = Math.round(performance.now() - start);
    } finally { childProcess.spawnSync = original; syncBuiltinESMExports(); }
    assert.equal(receipt.status, 'source_merge_verified');
    assert.equal(receipt.readyForLaunch, false);
    assert.equal(gitRun(stable, 'rev-parse', '--verify', 'HEAD'), plan.expectedCommit);
    results.push({ label, elapsedMs, guardProcesses, guardProcessMs: Math.round(guardProcessMs) });
  }
  assert.equal(results[0].guardProcesses, results[1].guardProcesses, 'per-boundary Node guard invocation count remains unchanged');
  console.log(`managed-selected-merge-object-guard-ab ${JSON.stringify({ scope: 'isolated real merge; linked fixture only', results })}`);
});

// Opt in explicitly: one real selected merge per version; no live worktree is touched.
test('isolated real selected merge A/B against original directory queries', { skip: process.env.MANAGED_STATE_AB !== '1', timeout: 300000 }, async t => {
  const { performance } = await import('node:perf_hooks');
  const modulePath = fileURLToPath(new URL('../../launcher/managed-selected-merge.mjs', import.meta.url));
  let baseline = readFileSync(modulePath, 'utf8');
  const legacy = `function state(git, cwd) {
  const top = path.resolve(text(run(git, cwd, ['rev-parse', '--show-toplevel'])));
  if (top !== cwd) fail('git_root_mismatch', 'supplied path is not exact Git worktree root');
  const gitDir = path.resolve(cwd, text(run(git, cwd, ['rev-parse', '--path-format=absolute', '--git-dir'])));
  const commonDir = path.resolve(cwd, text(run(git, cwd, ['rev-parse', '--path-format=absolute', '--git-common-dir'])));
  for (const d of [gitDir, commonDir]) { const st = lstatSync(d); if (!st.isDirectory() || st.isSymbolicLink() || (st.attributes & 0x400)) fail('git_directory_unsafe', 'Git metadata directory is unsafe'); }
  const head = text(run(git, cwd, ['rev-parse', '--verify', 'HEAD']));
  if (!OID.test(head)) fail('git_head_invalid', 'Git HEAD is not a full object ID');
  const branch = text(run(git, cwd, ['symbolic-ref', '--quiet', '--short', 'HEAD']));
  if (!branch || branch.startsWith('-')) fail('detached_head_rejected', 'detached or invalid branch is not supported');
  const indexPath = path.join(gitDir, 'index');
  let rawIndexSha256 = null;
  if (existsSync(indexPath)) { const st = lstatSync(indexPath); if (!st.isFile() || st.isSymbolicLink() || st.nlink !== 1) fail('git_index_unsafe', 'Git index is not an ordinary single-link file'); if(st.size>MAX_RAW_INDEX_BYTES)fail('selected_merge_raw_index_limit_exceeded','existing raw Git index exceeds 1 MiB'); const raw=readFileSync(indexPath);if(raw.length!==st.size||lstatSync(indexPath).size!==st.size)fail('git_index_changed_during_read','Git index changed while being read');rawIndexSha256 = sha(raw); }
  return { worktreeRoot: cwd, gitDir, commonDir, branch, head, rawIndexSha256 };
}
`;
  const stateBlock = /function state\(git, cwd\) \{[\s\S]*?\n\}\r?\n(?=function treeMap)/;
  assert.match(baseline, stateBlock);
  baseline = baseline.replace(stateBlock, legacy);
  const launcher = new URL('../../launcher/', import.meta.url);
  baseline = baseline.replace(/from '(\.\/[^']+)'/g, (_, rel) => `from '${new URL(rel, launcher).href}'`);
  baseline = baseline.replace("new URL('./managed-clean-git-metadata.mjs',import.meta.url).href", JSON.stringify(new URL('./managed-clean-git-metadata.mjs', launcher).href));
  const old = await import(`data:text/javascript;base64,${Buffer.from(baseline).toString('base64')}`);
  const results = [];
  for (const [label, impl] of [['baseline', old], ['grouped', current]]) {
    const { stable, sandbox } = fixture(t), { checkpoint, changed } = makeCheckpoint(stable, sandbox);
    const plan = impl.prepareManagedSelectedMerge({ stableRoot: stable, sandboxRoot: sandbox, checkpoint, gitExecutable: git });
    let gitCalls = 0, locationQueries = 0, trustCalls = 0;
    const original = childProcess.spawnSync;
    childProcess.spawnSync = (file, args, options) => {
      if (String(file).toLowerCase().endsWith('git.exe')) {
        gitCalls++;
        if (args.includes('--show-toplevel') || args.includes('--git-dir') || args.includes('--git-common-dir')) locationQueries++;
      }
      return original(file, args, options);
    };
    syncBuiltinESMExports();
    let receipt, ms;
    try {
      const start = performance.now();
      receipt = impl.executeManagedSelectedMerge({ plan, checkpoint, stableRoot: stable, sandboxRoot: sandbox, gitExecutable: git, assertTrusted: () => { trustCalls++; return true; } });
      ms = performance.now() - start;
    } finally { childProcess.spawnSync = original; syncBuiltinESMExports(); }
    assert.equal(receipt.status, 'source_merge_verified');
    assert.equal(receipt.sourceMergeExecuted, true);
    assert.equal(receipt.readyForLaunch, false);
    assert.deepEqual(readFileSync(path.join(stable, 'desktop', 'selected.mjs')), changed);
    assert.equal(gitRun(stable, 'rev-parse', '--verify', 'HEAD'), plan.expectedCommit);
    results.push({ label, elapsedMs: Math.round(ms), gitCalls, locationQueries, trustCalls });
  }
  assert.equal(results[0].trustCalls, results[1].trustCalls);
  assert.ok(results[1].locationQueries < results[0].locationQueries);
  console.log(`managed-selected-merge-ab ${JSON.stringify({ scope: 'isolated-real-merge-single-pair-no-human-wait', results })}`);
});
