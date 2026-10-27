import assert from 'node:assert/strict';
import { test } from 'node:test';
import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync, existsSync, readFileSync, utimesSync } from 'node:fs';
import os from 'node:os';
import { createRequire, syncBuiltinESMExports } from 'node:module';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { inspectManagedSources, revalidateManagedInspection, deriveManagedScopePlan } from '../../launcher/managed-source-plan.mjs';

const evidenceRoot = path.resolve('artifacts/fixed-control-e1-20261024/managed-preparation/source-plan');
mkdirSync(evidenceRoot, { recursive: true });
function git(cwd, ...args) {
  const env = { PATH: process.env.PATH, SYSTEMROOT: process.env.SYSTEMROOT, WINDIR: process.env.WINDIR, HOME: path.join(os.tmpdir(), 'codex-managed-source-empty-home'), USERPROFILE: path.join(os.tmpdir(), 'codex-managed-source-empty-home'), GIT_CONFIG_NOSYSTEM: '1', GIT_TERMINAL_PROMPT: '0' };
  return execFileSync('git', ['--no-optional-locks', '--literal-pathspecs', '-c', 'core.fsmonitor=false', '-c', `safe.directory=${cwd}`, '-c', 'core.hooksPath=NUL', '-c', 'core.autocrlf=false', ...args], { cwd, env, encoding: 'utf8', windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] }).trim();
}
function repo(t, { rename = false } = {}) {
  const dir = mkdtempSync(path.join(evidenceRoot, `repo-${randomUUID()}-`));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const stable = path.join(dir, 'stable');
  mkdirSync(stable);
  git(stable, 'init', '-b', 'main');
  git(stable, 'config', 'user.name', 'Disposable Test');
  git(stable, 'config', 'user.email', 'test@example.invalid');
  git(stable, 'config', 'commit.gpgsign', 'false');
  git(stable, 'config', 'core.hooksPath', 'NUL');
  writeFileSync(path.join(stable, 'README.md'), 'base\n');
  writeFileSync(path.join(stable, '.gitignore'), '.worktrees/\n');
  git(stable, 'add', '--', 'README.md', '.gitignore'); git(stable, 'commit', '-m', 'base');
  const sandbox = path.join(stable, '.worktrees', 'sandbox');
  mkdirSync(path.dirname(sandbox), { recursive: true });
  git(stable, 'worktree', 'add', '-b', 'experiment/sandbox', sandbox);
  mkdirSync(path.join(sandbox, 'desktop'), { recursive: true });
  writeFileSync(path.join(sandbox, 'desktop/desktop-source.mjs'), 'export const value = 1;\n');
  writeFileSync(path.join(sandbox, 'desktop/second-source.mjs'), 'export const second = 2;\n');
  git(sandbox, 'add', '--', 'desktop/desktop-source.mjs', 'desktop/second-source.mjs'); git(sandbox, 'commit', '-m', 'sandbox source');
  if (rename) { git(sandbox, 'mv', 'desktop/desktop-source.mjs', 'desktop/renamed-source.mjs'); git(sandbox, 'commit', '-m', 'rename source'); }
  return { dir, stable, sandbox };
}

test('inspects the complete committed range and permits intentional nested worktrees', async t => {
  const r = repo(t);
  const result = await inspectManagedSources({ stableRoot: r.stable, sandboxRoot: r.sandbox });
  assert.equal(result.format, 1);
  assert.equal(result.mode, 'managed_source_inspection_v1');
  assert.equal(result.stable.statusInspection, 'complete', 'out-of-scope README is not a protected tracked path');
  assert.deepEqual(result.stable.protectedTrackedPaths, []);
  assert.equal(result.sandbox.statusInspection, 'complete');
  assert.equal(result.readyForScopeReview, true, JSON.stringify(result.blockers));
  assert.equal(result.readyForLaunch, false);
  assert.equal(result.stable.identity.commonDir, result.sandbox.identity.commonDir);
  assert.ok(result.committedRange.commits.length >= 1);
  assert.ok(result.committedRange.paths.some(p => p.path === 'desktop/desktop-source.mjs'));
  assert.equal((await revalidateManagedInspection(result)).snapshot_digest, result.snapshot_digest);
});

test('revalidation rejects tracked, index and untracked byte drift', async t => {
  for (const mutation of [
    r => writeFileSync(path.join(r.sandbox, 'desktop/desktop-source.mjs'), 'changed working bytes'),
    r => { writeFileSync(path.join(r.sandbox, 'new.txt'), 'index bytes'); git(r.sandbox, 'add', '--', 'new.txt'); },
    r => writeFileSync(path.join(r.sandbox, 'untracked.txt'), 'untracked bytes'),
  ]) {
    const r = repo(t); const snapshot = await inspectManagedSources({ stableRoot: r.stable, sandboxRoot: r.sandbox });
    mutation(r);
    assert.throws(() => revalidateManagedInspection(snapshot), /drift|changed|baseline/i);
  }
});

test('reports protected paths by name only and blocks readiness', async t => {
  const r = repo(t);
  mkdirSync(path.join(r.sandbox, 'data'), { recursive: true });
  writeFileSync(path.join(r.sandbox, 'data', 'private.db'), 'DO_NOT_READ_SECRET_BYTES');
  writeFileSync(path.join(r.sandbox, '.env'), 'DO_NOT_READ_ENV_BYTES');
  const result = await inspectManagedSources({ stableRoot: r.stable, sandboxRoot: r.sandbox });
  const paths = result.blockers.flatMap(b => b.paths ?? []);
  assert.ok(paths.includes('data/private.db'));
  assert.ok(paths.includes('.env'));
  assert.equal(JSON.stringify(result).includes('DO_NOT_READ'), false);
  assert.equal(result.readyForScopeReview, false);
  assert.equal(result.sandbox.dirty.some(x => x.path === 'data/private.db' && x.sha256), false);
});

test('rejects detached sandbox and derives digest-bound exact scope proposal', async t => {
  const r = repo(t); const result = await inspectManagedSources({ stableRoot: r.stable, sandboxRoot: r.sandbox });
  const proposal = deriveManagedScopePlan({ installationId: randomUUID(), namespaceDigest: 'a'.repeat(64), inspection: result, deliveryPaths: result.committedRange.paths.map(p => p.path) });
  assert.equal(proposal.format, 1); assert.match(proposal.scope_digest, /^[a-f0-9]{64}$/);
  assert.deepEqual(proposal.deliveryPaths.map(p => p.path), ['desktop/desktop-source.mjs', 'desktop/second-source.mjs']);
  assert.equal(proposal.inspectionDigest, result.snapshot_digest);
  assert.throws(() => deriveManagedScopePlan({ installationId: randomUUID(), namespaceDigest: 'a'.repeat(64), inspection: result, deliveryPaths: ['desktop/desktop-source.mjs'] }), /account for every committed range path/i);
  git(r.sandbox, 'checkout', '--detach');
  assert.throws(() => inspectManagedSources({ stableRoot: r.stable, sandboxRoot: r.sandbox }), /detached|branch/i);
});










test('committed renames retain both path identities and the rename pairing', async t => {
  const r = repo(t, { rename: true });
  const result = await inspectManagedSources({ stableRoot: r.stable, sandboxRoot: r.sandbox });
  const oldPath = result.committedRange.paths.find(p => p.path === 'desktop/desktop-source.mjs');
  const newPath = result.committedRange.paths.find(p => p.path === 'desktop/renamed-source.mjs');
  assert.ok(oldPath); assert.ok(newPath);
  assert.ok(oldPath.renamePairs.some(pair => pair.from === oldPath.path && pair.to === newPath.path));
  assert.ok(newPath.renamePairs.some(pair => pair.from === oldPath.path && pair.to === newPath.path));
});

test('ordinary source dirty state requires ownership review and blocks scope proposals', async t => {
  const r = repo(t);
  writeFileSync(path.join(r.sandbox, 'desktop/desktop-source.mjs'), 'unclaimed local edit');
  const result = await inspectManagedSources({ stableRoot: r.stable, sandboxRoot: r.sandbox });
  assert.ok(result.blockers.some(b => b.code === 'sandbox_dirty_requires_ownership_review'));
  assert.equal(result.readyForScopeReview, false);
  assert.throws(() => deriveManagedScopePlan({ installationId: randomUUID(), namespaceDigest: 'a'.repeat(64), inspection: result, deliveryPaths: result.committedRange.paths.map(p => p.path) }), /not eligible/i);
});

test('stash ref metadata participates in the inspection digest without subjects or contents', async t => {
  const r = repo(t);
  const snapshot = await inspectManagedSources({ stableRoot: r.stable, sandboxRoot: r.sandbox });
  writeFileSync(path.join(r.sandbox, 'desktop/desktop-source.mjs'), 'stashed local edit');
  git(r.sandbox, 'stash', 'push', '-m', 'PRIVATE_STASH_SUBJECT_MUST_NOT_APPEAR');
  const current = await inspectManagedSources({ stableRoot: r.stable, sandboxRoot: r.sandbox });
  assert.notEqual(current.snapshot_digest, snapshot.snapshot_digest);
  assert.ok(current.sandbox.stashes.length > 0);
  assert.equal(current.sandbox.stashCount, current.sandbox.stashes.length);
  assert.deepEqual(Object.keys(current.sandbox.stashes[0]).sort(), ['oid', 'ref', 'selector']);
  assert.equal(JSON.stringify(current).includes('PRIVATE_STASH_SUBJECT'), false);
  assert.throws(() => revalidateManagedInspection(snapshot), /drift/i);
});



test('identity rejects a detached stable source', t => {
  const r = repo(t);
  git(r.stable, 'checkout', '--detach');
  assert.throws(() => inspectManagedSources({ stableRoot: r.stable, sandboxRoot: r.sandbox }), /stable.*detached|stable.*symbolic branch/i);
});

test('identity rejects identical stable and sandbox roots', t => {
  const r = repo(t);
  assert.throws(() => inspectManagedSources({ stableRoot: r.sandbox, sandboxRoot: r.sandbox }), /source roots.*distinct|source roots.*different/i);
});

test('identity rejects the sandbox branch as the stable source branch', t => {
  const r = repo(t);
  git(r.stable, 'checkout', '--ignore-other-worktrees', 'experiment/sandbox');
  assert.throws(() => inspectManagedSources({ stableRoot: r.stable, sandboxRoot: r.sandbox }), /stable branch.*experiment\/sandbox/i);
});

test('stash inventory binds every reflog entry and rejects removal of an older entry', t => {
  const r = repo(t);
  writeFileSync(path.join(r.sandbox, 'desktop/desktop-source.mjs'), 'first stashed edit');
  git(r.sandbox, 'stash', 'push', '-m', 'PRIVATE_OLDER_STASH_SUBJECT');
  writeFileSync(path.join(r.sandbox, 'desktop/desktop-source.mjs'), 'second stashed edit');
  git(r.sandbox, 'stash', 'push', '-m', 'PRIVATE_NEWER_STASH_SUBJECT');
  const snapshot = inspectManagedSources({ stableRoot: r.stable, sandboxRoot: r.sandbox });
  assert.equal(snapshot.sandbox.stashCount, 2);
  assert.deepEqual(snapshot.sandbox.stashes.map(entry => entry.selector), ['refs/stash@{0}', 'refs/stash@{1}']);
  assert.equal(JSON.stringify(snapshot).includes('PRIVATE_'), false);
  const tip = snapshot.sandbox.stashes[0].oid;
  git(r.sandbox, 'stash', 'drop', 'stash@{1}');
  const current = inspectManagedSources({ stableRoot: r.stable, sandboxRoot: r.sandbox });
  assert.equal(current.sandbox.stashCount, 1);
  assert.equal(current.sandbox.stashes[0].oid, tip, 'tip identity stays unchanged when only the older entry is removed');
  assert.notEqual(current.sandbox.stashDigest, snapshot.sandbox.stashDigest);
  assert.throws(() => revalidateManagedInspection(snapshot), /drift/i);
});

test('reserved managed runtime paths stay name-only protected even when force-tracked', t => {
  const r = repo(t);
  const runtimePath = '.managed-runtime/11111111-1111-4111-8111-111111111111/state/private.bin';
  const nestedRuntimePath = 'desktop/.managed-runtime/forced-tracked/private.bin';
  const secret = 'RUNTIME_SECRET_BYTES_MUST_NEVER_BE_READ_OR_RETURNED';
  mkdirSync(path.dirname(path.join(r.sandbox, ...runtimePath.split('/'))), { recursive: true });
  mkdirSync(path.dirname(path.join(r.sandbox, ...nestedRuntimePath.split('/'))), { recursive: true });
  writeFileSync(path.join(r.sandbox, ...runtimePath.split('/')), secret);
  writeFileSync(path.join(r.sandbox, ...nestedRuntimePath.split('/')), secret);
  git(r.sandbox, 'add', '-f', '--', runtimePath, nestedRuntimePath);
  git(r.sandbox, 'commit', '-m', 'synthetic forced runtime paths');
  // Synthetic key bytes verify the preflight records only the path for tracked runtime changes.
  writeFileSync(path.join(r.sandbox, ...nestedRuntimePath.split('/')), Buffer.alloc(128 * 1024, 0x41));

  const result = inspectManagedSources({ stableRoot: r.stable, sandboxRoot: r.sandbox });
  const runtimeEntries = result.committedRange.paths.filter(item => item.path.includes('.managed-runtime'));
  assert.equal(runtimeEntries.length, 2);
  assert.ok(runtimeEntries.every(item => item.protected && !item.allowed));
  assert.ok(result.blockers.some(item => item.code === 'committed_protected_or_out_of_scope_paths' && item.paths.includes(runtimePath) && item.paths.includes(nestedRuntimePath)));
  assert.equal(result.sandbox.statusInspection, 'blocked_protected_tracked');
  assert.deepEqual(result.sandbox.protectedTrackedPaths, [runtimePath, nestedRuntimePath].sort());
  assert.deepEqual(result.sandbox.dirty, [], 'tracked protected status is unknown, not factual dirty state');
  assert.ok(result.blockers.some(item => item.code === 'complete_worktree_inventory_unavailable'));
  assert.equal(result.readyForScopeReview, false);
  assert.equal(JSON.stringify(result).includes(secret), false);
  assert.equal(runtimeEntries.some(item => Object.hasOwn(item, 'sha256')), false);
});

test('protected tracked paths block Git status before clean filters can read working bytes', t => {
  const r = repo(t);
  const runtimePath = '.managed-runtime/11111111-1111-4111-8111-111111111111/state/key-material.bin';
  const workspacePath = 'workspace/test/key-material.bin';
  const sentinel = path.join(r.dir, 'clean-filter-read.log');
  const filter = path.join(r.dir, 'clean-filter.cjs');
  writeFileSync(sentinel, '');
  writeFileSync(filter, 'const fs = require("node:fs"); fs.appendFileSync(process.argv[2], "FILTER_READ\\n"); process.stdin.pipe(process.stdout);\n');
  const quote = value => `"${value.replaceAll('\\', '/')}"`;
  git(r.stable, 'config', 'filter.protected-sentinel.clean', [process.execPath, filter, sentinel].map(quote).join(' '));
  const tracked = [[r.stable, workspacePath], [r.sandbox, runtimePath]];
  let statusFilterReads = 0;
  for (const [root, relative] of tracked) {
    const file = path.join(root, ...relative.split('/'));
    mkdirSync(path.dirname(file), { recursive: true });
    writeFileSync(file, Buffer.alloc(128 * 1024, 0x49));
    git(root, 'add', '-f', '--', relative);
    writeFileSync(path.join(root, '.gitattributes'), `${relative} filter=protected-sentinel\n`);
    writeFileSync(file, Buffer.alloc(128 * 1024, 0x41));
    utimesSync(file, new Date('2001-01-01'), new Date('2001-01-01'));
    const status = git(root, 'status', '--porcelain=v2', '--untracked-files=all');
    assert.ok(status.includes(relative), `calibration status must report the synthetic changed path ${relative}`);
    if (readFileSync(sentinel, 'utf8').includes('FILTER_READ')) statusFilterReads++;
    else {
      t.diagnostic(`status clean filter not observed for ${relative}; calibrating via read-only git hash-object`);
      git(root, 'hash-object', `--path=${relative}`, relative);
      assert.match(readFileSync(sentinel, 'utf8'), /FILTER_READ/, 'hash-object calibration must prove the configured clean filter executes');
    }
    writeFileSync(sentinel, '');
  }
  t.diagnostic(`status_clean_filter_reads=${statusFilterReads}/${tracked.length}; fallback uses git hash-object without -w`);
  for (const [root, relative] of tracked) {
    const file = path.join(root, ...relative.split('/'));
    writeFileSync(file, Buffer.alloc(128 * 1024, 0x42));
    utimesSync(file, new Date('2002-01-01'), new Date('2002-01-01'));
  }

  const childProcess = createRequire(import.meta.url)('node:child_process');
  const originalSpawnSync = childProcess.spawnSync;
  const commands = [];
  let result;
  childProcess.spawnSync = (command, args, options) => {
    if (command === 'git') commands.push({ args, cwd: options.cwd });
    return originalSpawnSync(command, args, options);
  };
  syncBuiltinESMExports();
  try {
    result = inspectManagedSources({ stableRoot: r.stable, sandboxRoot: r.sandbox });
  } finally {
    childProcess.spawnSync = originalSpawnSync;
    syncBuiltinESMExports();
  }
  assert.equal(readFileSync(sentinel, 'utf8'), '', 'source inspection must not let Git read protected working bytes');
  assert.equal(commands.some(command => command.args.includes('status')), false, 'neither protected worktree may run Git status');
  for (const [label, relative] of [['stable', workspacePath], ['sandbox', runtimePath]]) {
    assert.equal(result[label].statusInspection, 'blocked_protected_tracked');
    assert.deepEqual(result[label].protectedTrackedPaths, [relative]);
    assert.deepEqual(result[label].dirty, [], 'empty dirty is explicitly uninspected, not a clean-worktree assertion');
    assert.ok(result[label].identity.worktreeRoot);
  }
  assert.ok(result.blockers.some(blocker => blocker.code === 'complete_worktree_inventory_unavailable'));
  assert.equal(result.readyForScopeReview, false);
  assert.equal(result.readyForLaunch, false);
  assert.throws(() => deriveManagedScopePlan({ installationId: randomUUID(), namespaceDigest: 'a'.repeat(64), inspection: result, deliveryPaths: result.committedRange.paths.map(item => item.path) }), /not eligible/i);
  assert.equal(readFileSync(sentinel, 'utf8'), '', 'scope-plan refusal must not read protected working bytes either');
});

test('metadata-only history mode avoids status and rename similarity, and revalidation preserves the mode', t => {
  const r = repo(t);
  const source = path.join(r.sandbox, 'desktop/desktop-source.mjs');
  const sentinel = path.join(r.dir, 'allowed-clean-filter.log');
  const filter = path.join(r.dir, 'allowed-clean-filter.cjs');
  writeFileSync(sentinel, '');
  writeFileSync(filter, 'const fs = require("node:fs"); fs.appendFileSync(process.argv[2], "FILTER_READ\\n"); process.stdin.pipe(process.stdout);\n');
  const quote = value => `"${value.replaceAll('\\', '/') }"`;
  git(r.sandbox, 'config', 'filter.review-sentinel.clean', [process.execPath, filter, sentinel].map(quote).join(' '));
  writeFileSync(source, Buffer.alloc(128 * 1024, 0x49));
  git(r.sandbox, 'add', '-f', '--', 'desktop/desktop-source.mjs');
  writeFileSync(path.join(r.sandbox, '.gitattributes'), 'desktop/desktop-source.mjs filter=review-sentinel\n');
  writeFileSync(source, Buffer.alloc(128 * 1024, 0x41));
  utimesSync(source, new Date('2001-01-01'), new Date('2001-01-01'));
  writeFileSync(source, Buffer.alloc(128 * 1024, 0x42));
  utimesSync(source, new Date('2002-01-01'), new Date('2002-01-01'));

  const childProcess = createRequire(import.meta.url)('node:child_process');
  const original = childProcess.spawnSync;
  const commands = [];
  childProcess.spawnSync = (command, args, options) => {
    if (command === 'git') commands.push(args);
    return original(command, args, options);
  };
  syncBuiltinESMExports();
  let report;
  try {
    report = inspectManagedSources({ stableRoot: r.stable, sandboxRoot: r.sandbox, metadataOnlyHistory: true });
    assert.equal(revalidateManagedInspection(report).snapshot_digest, report.snapshot_digest);
  } finally {
    childProcess.spawnSync = original;
    syncBuiltinESMExports();
  }
  assert.equal(readFileSync(sentinel, 'utf8'), '', 'review and revalidation must not invoke allowed-source clean filter');
  assert.equal(commands.some(args => args.includes('status')), false);
  const historyCommands = commands.filter(args => args.includes('diff-tree'));
  assert.ok(historyCommands.length > 0);
  assert.ok(historyCommands.every(args => args.includes('--no-renames') && args.includes('--no-ext-diff') && args.includes('--no-textconv') && !args.includes('--find-renames')));
  assert.equal(report.historyInspection, 'metadata_only_no_renames');
  assert.ok(['metadata_only', 'metadata_only_no_status'].includes(report.sandbox.statusInspection));
  assert.deepEqual(report.sandbox.dirty, []);
  assert.ok(report.blockers.some(item => item.code === 'complete_worktree_inventory_unavailable'));
});
test('metadataOnlyHistory option rejects non-boolean values before inspecting roots', () => {
  assert.throws(() => inspectManagedSources({ stableRoot: '', sandboxRoot: '', metadataOnlyHistory: 'true' }), /metadataOnlyHistory must be a boolean/);
});
