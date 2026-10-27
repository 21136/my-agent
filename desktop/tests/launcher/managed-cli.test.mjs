import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, readdir, stat, rm } from 'node:fs/promises';
import { access } from 'node:fs/promises';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { runManagedCli } from '../../launcher/managed-cli.mjs';
import { managedNamespacePath } from '../../launcher/managed-namespace.mjs';
import { projectNamespaceDigest, projectNamespacePath } from '../../launcher/project-namespace.mjs';

const sandboxRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const evidenceRoot = path.join(sandboxRoot, 'artifacts/project-local-storage-20261024/cli-tests');
const installationId = 'c9f4d186-3843-4a95-b542-4a924f34d75f';
const fsExists = async (p) => access(p).then(() => true, () => false);
const gitEnv = { ...process.env, GIT_CONFIG_NOSYSTEM: '1', GIT_TERMINAL_PROMPT: '0' };

function git(cwd, args) {
  const result = spawnSync('git', args, { cwd, env: gitEnv, encoding: 'utf8', windowsHide: true });
  if (result.status !== 0) throw new Error(`synthetic git setup failed: ${result.stderr}`);
  return result.stdout.trim();
}

async function fixture(t) {
  await mkdir(evidenceRoot, { recursive: true });
  const root = path.resolve(await mkdtemp(path.join(evidenceRoot, 'test-')));
  const evidencePrefix = path.resolve(evidenceRoot) + path.sep;
  if (!root.startsWith(evidencePrefix) || !path.basename(root).startsWith('test-')) throw new Error('refusing cleanup outside this test CLI fixture');
  t.after(async () => { if (path.dirname(root) !== path.resolve(evidenceRoot) || !path.basename(root).startsWith('test-')) throw new Error('fixture cleanup target drifted'); await rm(root, { recursive: true, force: true }); });
  t.diagnostic(`owned CLI fixture: ${root}`);
  const localAppData = path.join(root, 'LocalAppData');
  await mkdir(localAppData, { recursive: true });
  const stableRoot = path.join(root, 'sources', 'stable');
  const sandboxSourceRoot = path.join(stableRoot, '.worktrees', 'sandbox');
  await mkdir(stableRoot, { recursive: true });
  await mkdir(path.dirname(sandboxSourceRoot), { recursive: true });
  // Two disposable worktrees in one common repository exercise the real inspector
  // without reading or writing either project checkout.
  git(stableRoot, ['init', '-b', 'main']);
  git(stableRoot, ['config', 'user.name', 'Synthetic Test']);
  git(stableRoot, ['config', 'user.email', 'synthetic@example.invalid']);
  await (await import('node:fs/promises')).writeFile(path.join(stableRoot, 'owned.txt'), 'base\n');
  await (await import('node:fs/promises')).writeFile(path.join(stableRoot, '.gitignore'), '.worktrees/\n');
  git(stableRoot, ['add', 'owned.txt', '.gitignore']);
  git(stableRoot, ['commit', '-m', 'synthetic base']);
  git(stableRoot, ['worktree', 'add', '-b', 'experiment/sandbox', sandboxSourceRoot, 'HEAD']);
  const dependencies = {
    platform: 'win32',
    assertLocalAppDataAnchor: async (base) => { assert.equal(base, localAppData); },
    assertNoReparse: async () => {},
    secureRoot: async () => {},
    localAppData,
    probeVolume: async () => ({ filesystem: 'NTFS', driveType: 'Fixed', local: true }),
    probeAcl: async () => ({ private: true, currentSid: 'S-1-5-21-synthetic', ownerSid: 'S-1-5-21-synthetic' }),
  };
  return { root, localAppData, stableRoot, sandboxSourceRoot, dependencies };
}

async function prepared(t) {
  const f = await fixture(t);
  const lines = [];
  const result = await runManagedCli(['--prepare', f.stableRoot, f.sandboxSourceRoot, installationId], {
    write: (line) => lines.push(String(line)),
    namespaceDependencies: f.dependencies,
  });
  assert.equal(result.code, 0, lines.join('\n'));
  return { ...f, lines, installationRoot: managedNamespacePath(f.localAppData, installationId) };
}

test('--help documents the closed command set, safety blockers, and check=0 versus inspect blockers', async () => {
  const lines = [];
  const result = await runManagedCli(['--help'], { write: (line) => lines.push(String(line)) });
  assert.equal(result.code, 0);
  const help = lines.join('\n');
  const normalizedHelp = help.replace(/\s+/g, ' ').toLowerCase();
  for (const required of ['--prepare <stableRoot> <sandboxRoot> <UUID>', '--check <installationRoot>', '--inspect <installationRoot>', '--prepare-project <stableRoot> <sandboxRoot> <UUID>', '--check-project <stableRoot> <sandboxRoot> <UUID>', '--inspect-project <stableRoot> <sandboxRoot> <UUID>', '--review-project <stableRoot> <sandboxRoot> <UUID>', 'full namespace-bound read-only review json envelope', 'logical separation only', 'credentials are forbidden', 'ready_for_launch: false', 'a successful --check may return code 0 while --inspect reports safety blockers', '--launch', '--adopt']) assert.ok(normalizedHelp.includes(required.toLowerCase()), `help missing ${required}`);
});

test('strict argument parser rejects extra, missing, malformed, launch, and adopt forms without writes', async (t) => {
  const f = await fixture(t);
  for (const args of [[], ['--prepare', f.stableRoot], ['--prepare', f.stableRoot, f.sandboxSourceRoot, 'bad'], ['--check'], ['--inspect', f.root, 'extra'], ['--launch'], ['--adopt', f.root]]) {
    const lines = [];
    const result = await runManagedCli(args, { write: (line) => lines.push(String(line)), namespaceDependencies: f.dependencies });
    assert.notEqual(result.code, 0, JSON.stringify(args));
    assert.doesNotMatch(lines.join('\n'), /secret|KEY=|NODE_OPTIONS|process\.env/i);
  }
  assert.equal(await readdir(f.localAppData).catch((error) => error.code === 'ENOENT' ? [] : Promise.reject(error)).then((entries) => entries.length), 0);
});

test('--prepare performs source inspection before creating the private empty namespace', async (t) => {
  const f = await fixture(t);
  const target = managedNamespacePath(f.localAppData, installationId);
  const result = await runManagedCli(['--prepare', f.stableRoot, f.sandboxSourceRoot, installationId], {
    write: () => {}, namespaceDependencies: f.dependencies,
  });
  assert.equal(result.code, 0);
  assert.equal(result.ready_for_launch, false);
  assert.equal(result.namespace.installation_id, installationId);
  assert.equal((await stat(target)).isDirectory(), true);
  const state = await readdir(path.join(target, 'state'));
  assert.deepEqual(state, ['managed-namespace.json']);
});

test('--prepare refuses invalid source roots before any namespace path is created', async (t) => {
  const f = await fixture(t);
  const outside = path.join(f.root, 'not-a-repository');
  await mkdir(outside);
  const result = await runManagedCli(['--prepare', outside, f.sandboxSourceRoot, installationId], {
    write: () => {}, namespaceDependencies: f.dependencies,
  });
  assert.notEqual(result.code, 0);
  assert.equal(await readdir(f.localAppData).catch((error) => error.code === 'ENOENT' ? [] : Promise.reject(error)).then((entries) => entries.length), 0);
});

test('--check is read-only and may succeed although source inspection has blockers', async (t) => {
  const f = await prepared(t);
  const marker = path.join(f.installationRoot, 'state', 'managed-namespace.json');
  const before = await readFile(marker);
  const lines = [];
  const result = await runManagedCli(['--check', f.installationRoot], {
    write: (line) => lines.push(String(line)), namespaceDependencies: f.dependencies,
  });
  assert.equal(result.code, 0);
  assert.equal(result.ready_for_launch, false);
  assert.deepEqual(await readFile(marker), before);
  assert.equal(await readdir(path.join(f.installationRoot, 'state')).then((items) => items.length), 1);
  assert.doesNotMatch(lines.join('\n'), /diff|environment|env|secret/i);
});

test('--inspect revalidates identity and returns the complete report without writing namespace state', async (t) => {
  const f = await prepared(t);
  const lines = [];
  const stateRoot = path.join(f.installationRoot, 'state');
  const markerPath = path.join(stateRoot, 'managed-namespace.json');
  const markerBefore = await readFile(markerPath);
  const entriesBefore = await readdir(stateRoot);
  const result = await runManagedCli(['--inspect', f.installationRoot], {
    write: (line) => lines.push(String(line)), namespaceDependencies: f.dependencies,
  });
  assert.equal(result.code, 3, 'inspection blockers are a distinct not-ready result');
  assert.equal(result.ready_for_launch, false);
  assert.ok(result.inspection);
  assert.ok(Array.isArray(result.inspection.blockers));
  assert.ok(result.inspection.committedRange);
  assert.ok(result.blocker_count > 0, 'successful inspect must expose safety blockers in this synthetic empty-range case');
  assert.deepEqual(await readdir(stateRoot), entriesBefore);
  assert.deepEqual(await readFile(markerPath), markerBefore);
  assert.equal(await fsExists(path.join(stateRoot, 'source-inspection.json')), false);
  assert.doesNotMatch(lines.join('\n'), /diff|environment|env|secret/i);
});

test('--inspect does not accept arbitrary output destinations and refuses altered namespace/data', async (t) => {
  const f = await prepared(t);
  const outside = path.join(f.root, 'outside.json');
  const bad = await runManagedCli(['--inspect', f.installationRoot, outside], { write: () => {}, namespaceDependencies: f.dependencies });
  assert.notEqual(bad.code, 0);
  await (await import('node:fs/promises')).writeFile(path.join(f.installationRoot, 'data', 'store', 'unexpected'), 'preserve');
  const result = await runManagedCli(['--inspect', f.installationRoot], { write: () => {}, namespaceDependencies: f.dependencies });
  assert.equal(result.code, 1);
  assert.equal(await readFile(path.join(f.installationRoot, 'data', 'store', 'unexpected'), 'utf8'), 'preserve');
  assert.equal(await import('node:fs/promises').then(({ access }) => access(outside).then(() => true, () => false)), false);
});




test('project commands prepare only by explicit roots and UUID, then check and inspect read-only', async t => {
  const f = await fixture(t);
  const projectNamespaceDependencies = {
    platform: 'win32',
    probeVolume: async () => ({ filesystem: 'exFAT', driveType: 'Fixed', local: true }),
    assertNoReparse: async () => {},
  };
  const target = projectNamespacePath(f.sandboxSourceRoot, installationId);
  const invoke = async (command) => {
    const lines = [];
    const result = await runManagedCli([command, f.stableRoot, f.sandboxSourceRoot, installationId], {
      write: line => lines.push(String(line)), projectNamespaceDependencies,
    });
    return { lines, result };
  };

  const prepared = await invoke('--prepare-project');
  assert.equal(prepared.result.code, 0, prepared.lines.join('\n'));
  assert.equal(prepared.result.ready_for_launch, false);
  assert.equal(prepared.result.namespace.mode, 'managed_project_local_v1');
  assert.equal(prepared.result.namespace.security_policy, 'logical_separation_only');
  assert.equal(prepared.result.namespace.credentials_policy, 'forbidden');
  assert.equal(prepared.result.namespace.root, target);
  assert.equal((await stat(target)).isDirectory(), true);

  const marker = path.join(target, 'state', 'managed-namespace.json');
  const before = await readFile(marker);
  const checked = await invoke('--check-project');
  assert.equal(checked.result.code, 0, checked.lines.join('\n'));
  assert.deepEqual(await readFile(marker), before);

  const inspected = await invoke('--inspect-project');
  assert.ok([0, 3].includes(inspected.result.code), inspected.lines.join('\n'));
  assert.equal(inspected.result.ready_for_launch, false);
  assert.equal(inspected.result.namespace_digest, projectNamespaceDigest(prepared.result.namespace));
  assert.deepEqual(await readFile(marker), before);
  assert.deepEqual(await readdir(path.join(target, 'state')), ['managed-namespace.json']);
});

test('project commands require exactly the explicit stable root, sandbox root and UUID and do not auto-detect', async t => {
  const f = await fixture(t);
  const deps = { platform: 'win32', probeVolume: async () => ({ filesystem: 'exFAT', driveType: 'Fixed', local: true }), assertNoReparse: async () => {} };
  for (const args of [
    ['--prepare-project', f.stableRoot, f.sandboxSourceRoot],
    ['--check-project', f.stableRoot, f.sandboxSourceRoot, 'bad'],
    ['--inspect-project', f.stableRoot, f.sandboxSourceRoot, installationId, 'extra'],
    ['--check-project', f.root, f.sandboxSourceRoot, installationId],
    ['--check-project', path.join(f.sandboxSourceRoot, '.managed-runtime', installationId), 'extra'],
  ]) {
    const result = await runManagedCli(args, { write: () => {}, projectNamespaceDependencies: deps });
    assert.notEqual(result.code, 0, JSON.stringify(args));
  }
  for (const args of [['--launch-project', f.stableRoot, f.sandboxSourceRoot, installationId], ['--adopt-project', f.stableRoot, f.sandboxSourceRoot, installationId]]) {
    const result = await runManagedCli(args, { write: () => {}, projectNamespaceDependencies: deps });
    assert.equal(result.code, 2);
  }
});

test('protected tracked workspace permits empty project preparation but inspect stays partial and blocked', async t => {
  const f = await fixture(t);
  const protectedPath = 'workspace/test/key-material.bin';
  await mkdir(path.join(f.stableRoot, 'workspace/test'), { recursive: true });
  await (await import('node:fs/promises')).writeFile(path.join(f.stableRoot, protectedPath), 'synthetic protected working bytes');
  git(f.stableRoot, ['add', '-f', '--', protectedPath]);
  const projectNamespaceDependencies = {
    platform: 'win32',
    probeVolume: async () => ({ filesystem: 'exFAT', driveType: 'Fixed', local: true }),
    assertNoReparse: async () => {},
  };
  const invoke = command => runManagedCli([command, f.stableRoot, f.sandboxSourceRoot, installationId], {
    write: () => {}, projectNamespaceDependencies,
  });
  const prepared = await invoke('--prepare-project');
  assert.equal(prepared.code, 0);
  assert.equal(prepared.ready_for_launch, false);
  assert.equal(prepared.inspection.stable.statusInspection, 'blocked_protected_tracked');
  assert.deepEqual(prepared.inspection.stable.protectedTrackedPaths, [protectedPath]);
  assert.deepEqual(prepared.inspection.stable.dirty, [], 'status is explicitly unknown, not clean');
  assert.equal(prepared.inspection.readyForScopeReview, false);
  assert.deepEqual(await readdir(prepared.namespace.paths.data), []);
  assert.deepEqual(await readdir(prepared.namespace.paths.host_state), []);

  const checked = await invoke('--check-project');
  assert.equal(checked.code, 0, 'structural namespace check remains independent of source readiness');
  const inspected = await invoke('--inspect-project');
  assert.equal(inspected.code, 3);
  assert.equal(inspected.ready_for_launch, false);
  assert.equal(inspected.inspection.stable.statusInspection, 'blocked_protected_tracked');
  assert.deepEqual(inspected.inspection.stable.protectedTrackedPaths, [protectedPath]);
  assert.deepEqual(inspected.inspection.stable.dirty, []);
  assert.ok(inspected.inspection.blockers.some(blocker => blocker.code === 'complete_worktree_inventory_unavailable'));
  assert.equal(inspected.inspection.readyForScopeReview, false);
  assert.deepEqual(await readdir(prepared.namespace.paths.data), []);
});
test('review-project emits the full namespace-bound packet read-only with blocker exit code 3', async t => {
  const f = await fixture(t);
  const projectNamespaceDependencies = {
    platform: 'win32',
    probeVolume: async () => ({ filesystem: 'exFAT', driveType: 'Fixed', local: true }),
    assertNoReparse: async () => {},
  };
  const prepared = await runManagedCli(['--prepare-project', f.stableRoot, f.sandboxSourceRoot, installationId], { write: () => {}, projectNamespaceDependencies });
  assert.equal(prepared.code, 0);
  const marker = path.join(prepared.namespace.root, 'state/managed-namespace.json');
  const before = await readFile(marker);
  const lines = [];
  const result = await runManagedCli(['--review-project', f.stableRoot, f.sandboxSourceRoot, installationId], {
    write: line => lines.push(String(line)), projectNamespaceDependencies,
  });
  assert.equal(result.code, 3);
  assert.equal(result.ready_for_launch, false);
  assert.equal(result.review.execution, false);
  assert.equal(result.review.readyForMerge, false);
  assert.equal(result.review.readyForLaunch, false);
  assert.match(result.namespace_digest, /^[a-f0-9]{64}$/);
  assert.match(result.review_digest, /^[a-f0-9]{64}$/);
  const envelope = JSON.parse(lines.join(''));
  assert.equal(envelope.review.review_digest, result.review.review_digest);
  assert.ok(envelope.review.history.paths);
  assert.deepEqual(await readFile(marker), before);
  assert.deepEqual(await readdir(path.join(prepared.namespace.root, 'state')), ['managed-namespace.json']);
});
