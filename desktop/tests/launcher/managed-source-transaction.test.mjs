import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { createManagedSourceMergePlan, executeManagedSourceMerge } from '../../launcher/managed-source-transaction.mjs';

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const expectedSandboxRoot = path.resolve('F:/my-agent-main/.worktrees/sandbox');
if (path.resolve(process.cwd()) !== repositoryRoot || repositoryRoot !== expectedSandboxRoot) {
  throw new Error('Tests must run from F:/my-agent-main/.worktrees/sandbox; no fixtures were created');
}
const evidenceRoot = path.join(repositoryRoot, 'artifacts/managed-transaction-20261024');
const fixturesRoot = path.join(evidenceRoot, 'fixtures');
const sha = (value) => createHash('sha256').update(value).digest('hex');
const oid = (n) => String(n).repeat(40);
let allowedGitRoots = new Set();
let gitIsolation = null;

function git(cwd, ...args) {
  const resolvedCwd = path.resolve(cwd);
  const fixturePrefix = `${fixturesRoot}${path.sep}`;
  if (!allowedGitRoots.has(resolvedCwd) || !resolvedCwd.startsWith(fixturePrefix)) {
    throw new Error(`Refusing Git outside an explicitly approved owned synthetic root: ${resolvedCwd}`);
  }
  if (!gitIsolation?.home || !gitIsolation?.hooks) throw new Error('Synthetic Git isolation is not initialized');
  const env = {
    PATH: process.env.PATH ?? '', SYSTEMROOT: process.env.SYSTEMROOT ?? '', WINDIR: process.env.WINDIR ?? '',
    HOME: gitIsolation.home, USERPROFILE: gitIsolation.home,
    GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: 'NUL', GIT_TERMINAL_PROMPT: '0', GIT_OPTIONAL_LOCKS: '0', LC_ALL: 'C',
  };
  const fixed = ['--no-pager', '-c', `safe.directory=${resolvedCwd}`, '-c', 'commit.gpgsign=false', '-c', 'core.fsmonitor=false', '-c', `core.hooksPath=${gitIsolation.hooks}`, '-c', 'credential.helper='];
  const result = spawnSync('git', [...fixed, ...args], { cwd: resolvedCwd, env, encoding: 'utf8', windowsHide: true, timeout: 15000, maxBuffer: 1024 * 1024 });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`synthetic git failed (${args[0]}): ${result.stderr}`);
  return result.stdout.trim();
}

function bodyFor(stableRoot, sandboxRoot, overrides = {}) {
  return {
    format: 1,
    mode: 'managed_source_merge_plan_v1',
    installationId: 'f6c2d116-3dc5-4ff1-99b1-c7feac43e2f0',
    namespaceDigest: sha('namespace'),
    reviewDigest: sha('review'),
    stable: { root: stableRoot, head: oid(1), branch: 'main', indexDigest: sha('index') },
    sandbox: { root: sandboxRoot, head: oid(2), branch: 'experiment/sandbox', indexDigest: sha('sandbox-index') },
    deliveryPaths: [{ path: 'desktop/launcher/approved.mjs', beforeSha256: null, afterSha256: sha('approved') }],
    expectedMergedTree: oid(3),
    preservationDigest: sha('preserved-unrelated-state'),
    blockers: [],
    ...overrides,
  };
}

async function journalDirectory() {
  await mkdir(fixturesRoot, { recursive: true });
  return await mkdtemp(path.join(fixturesRoot, 'transaction-'));
}

function receipt(overrides = {}) {
  return {
    mergeCommit: oid(4), mergedTree: oid(3), stableParent: oid(1), sandboxParent: oid(2),
    deliveryPaths: ['desktop/launcher/approved.mjs'], preservationDigest: sha('preserved-unrelated-state'),
    ...overrides,
  };
}

function steadyAdapter(overrides = {}) {
  return {
    async revalidate(plan) {
      return { planDigest: plan.planDigest, reviewDigest: plan.reviewDigest, namespaceDigest: plan.namespaceDigest, blockers: [] };
    },
    async merge() { return { exitCode: 0 }; },
    async verify() { return receipt(); },
    ...overrides,
  };
}

function confirmation(plan, overrides = {}) {
  return { planDigest: plan.planDigest, reviewDigest: plan.reviewDigest, namespaceDigest: plan.namespaceDigest, ...overrides };
}

test('blocked plans are rejected and create no operation journal', async (t) => {
  const directory = await journalDirectory();
  t.diagnostic(`synthetic fixture: ${directory}`);
  assert.throws(() => createManagedSourceMergePlan(bodyFor(path.join(directory, 'stable'), path.join(directory, 'sandbox'), { blockers: ['needs_review'] })));
  const { readdir } = await import('node:fs/promises');
  assert.deepEqual(await readdir(directory), []);
});
test('confirmation must bind the exact plan, review, and namespace before journal creation', async (t) => {
  const directory = await journalDirectory();
  t.diagnostic(`synthetic fixture: ${directory}`);
  const plan = createManagedSourceMergePlan(bodyFor(path.join(directory, 'stable'), path.join(directory, 'sandbox')));
  await assert.rejects(() => executeManagedSourceMerge({ directory, plan, confirmation: confirmation(plan, { reviewDigest: sha('wrong') }), adapter: steadyAdapter() }));
  const { readdir } = await import('node:fs/promises');
  assert.deepEqual(await readdir(directory), []);
});

test('missing trusted adapter fails before any write-ahead claim', async (t) => {
  const directory = await journalDirectory();
  t.diagnostic(`synthetic fixture: ${directory}`);
  const plan = createManagedSourceMergePlan(bodyFor(path.join(directory, 'stable'), path.join(directory, 'sandbox')));
  await assert.rejects(() => executeManagedSourceMerge({ directory, plan, confirmation: confirmation(plan) }));
  const { readdir } = await import('node:fs/promises');
  assert.deepEqual(await readdir(directory), []);
});

test('plan is cloned and frozen before adapter awaits; attempted mutation cannot alter approved scope', async (t) => {
  const directory = await journalDirectory();
  t.diagnostic(`synthetic fixture: ${directory}`);
  const input = bodyFor(path.join(directory, 'stable'), path.join(directory, 'sandbox'));
  const plan = createManagedSourceMergePlan(input);
  let frozenObserved = false;
  const adapter = steadyAdapter({
    async revalidate(candidate) {
      frozenObserved = Object.isFrozen(candidate) && Object.isFrozen(candidate.stable) && Object.isFrozen(candidate.deliveryPaths[0]);
      try { candidate.deliveryPaths[0].path = 'agent-core/private.txt'; } catch {}
      return { planDigest: candidate.planDigest, reviewDigest: candidate.reviewDigest, namespaceDigest: candidate.namespaceDigest, blockers: [] };
    },
  });
  const result = await executeManagedSourceMerge({ directory, plan, confirmation: confirmation(plan), adapter });
  assert.equal(result.readyForLaunch, false);
  assert.equal(frozenObserved, true);
  assert.equal(input.deliveryPaths[0].path, 'desktop/launcher/approved.mjs');
});

test('duplicate and unordered delivery paths are rejected before execution', async (t) => {
  const directory = await journalDirectory();
  t.diagnostic(`synthetic fixture: ${directory}`);
  const body = bodyFor(path.join(directory, 'stable'), path.join(directory, 'sandbox'));
  body.deliveryPaths = [body.deliveryPaths[0], { ...body.deliveryPaths[0] }];
  assert.throws(() => createManagedSourceMergePlan(body));
});


test('tampered plan digest is rejected without creating a journal', async (t) => {
  const directory = await journalDirectory();
  t.diagnostic(`synthetic fixture: ${directory}`);
  const plan = createManagedSourceMergePlan(bodyFor(path.join(directory, 'stable'), path.join(directory, 'sandbox')));
  const tampered = { ...plan, expectedMergedTree: oid(9) };
  await assert.rejects(() => executeManagedSourceMerge({ directory, plan: tampered, confirmation: confirmation(plan), adapter: steadyAdapter() }));
  const { readdir } = await import('node:fs/promises');
  assert.deepEqual(await readdir(directory), []);
});

test('case aliases, protected/runtime paths and unsafe Windows names are rejected', async (t) => {
  const directory = await journalDirectory();
  t.diagnostic(`synthetic fixture: ${directory}`);
  const base = bodyFor(path.join(directory, 'stable'), path.join(directory, 'sandbox'));
  for (const unsafe of ['Desktop/a.mjs', 'desktop/../agent-core/a.py', 'desktop/.managed-runtime/id/a', 'desktop/CON.txt', 'desktop/name. /file', 'desktop/config-store/keys.json', 'desktop/llm_secrets/key.txt', 'desktop/.env', 'desktop/llm_secrets.yaml', 'desktop/file.txt:stream', 'desktop/dist-electron/main.js']) {
    assert.throws(() => createManagedSourceMergePlan({ ...base, deliveryPaths: [{ path: unsafe, beforeSha256: null, afterSha256: sha('x') }] }), unsafe);
  }
});
test('mixed-case delivery paths use journal code-unit order and reject locale-only order before adapter calls', async () => {
  const root = 'F:\\managed-ordering-pure-test';
  const common = { beforeSha256: null };
  const paths = [
    { ...common, path: 'desktop/B.mjs', afterSha256: sha('uppercase') },
    { ...common, path: 'desktop/a.mjs', afterSha256: sha('lowercase') },
  ];
  const body = bodyFor(root, path.join(root, '.worktrees', 'sandbox'), { deliveryPaths: paths });
  const accepted = createManagedSourceMergePlan(body);
  assert.deepEqual(accepted.deliveryPaths.map((entry) => entry.path), ['desktop/B.mjs', 'desktop/a.mjs']);

  const reversed = { ...accepted, deliveryPaths: [...accepted.deliveryPaths].reverse() };
  delete reversed.planDigest;
  const canonicalJson = (value) => Array.isArray(value)
    ? `[${value.map(canonicalJson).join(',')}]`
    : value && typeof value === 'object'
      ? `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`
      : JSON.stringify(value);
  reversed.planDigest = sha(canonicalJson(reversed));
  let adapterCalls = 0;
  const adapter = {
    async revalidate() { adapterCalls++; return {}; },
    async merge() { adapterCalls++; return { exitCode: 0 }; },
    async verify() { adapterCalls++; return {}; },
  };
  await assert.rejects(() => executeManagedSourceMerge({
    directory: root,
    plan: reversed,
    confirmation: { planDigest: reversed.planDigest, reviewDigest: reversed.reviewDigest, namespaceDigest: reversed.namespaceDigest },
    adapter,
  }), /sorted and unique/);
  assert.equal(adapterCalls, 0);
});
test('only the exact stable/.worktrees/sandbox root nesting is accepted', () => {
  const root = 'F:\\managed-source-root-test';
  assert.doesNotThrow(() => createManagedSourceMergePlan(bodyFor(root, path.join(root, '.worktrees', 'sandbox'))));
  assert.throws(() => createManagedSourceMergePlan(bodyFor(root, path.join(root, 'nested', 'sandbox'))), /disjoint except/);
  assert.throws(() => createManagedSourceMergePlan(bodyFor(root, root.toLowerCase().replace('managed-source-root-test', 'MANAGED-SOURCE-ROOT-TEST'))), /disjoint except/);
});
test('oversized journal receipt path set is rejected purely before any fixture or adapter work', () => {
  const root = 'F:\\managed-transaction-pure-test';
  const paths = Array.from({ length: 100 }, (_, index) => ({
    path: `desktop/${String(index).padStart(3, '0')}-${'a'.repeat(800)}.mjs`,
    beforeSha256: null,
    afterSha256: sha(`approved-${index}`),
  })).sort((left, right) => left.path.localeCompare(right.path, 'en'));
  assert.throws(() => createManagedSourceMergePlan(bodyFor(root, path.join(root, '.worktrees', 'sandbox'), { deliveryPaths: paths })), /64 KiB/);
  const nonFinite = bodyFor(root, path.join(root, '.worktrees', 'sandbox'));
  nonFinite.expectedMergedTree = Number.NaN;
  assert.throws(() => createManagedSourceMergePlan(nonFinite), /non-finite/);
});
test('unknown command outcome is maintenance and is never retried', async (t) => {
  const directory = await journalDirectory();
  t.diagnostic(`synthetic fixture: ${directory}`);
  const plan = createManagedSourceMergePlan(bodyFor(path.join(directory, 'stable'), path.join(directory, 'sandbox')));
  let writes = 0;
  await assert.rejects(() => executeManagedSourceMerge({ directory, plan, confirmation: confirmation(plan), adapter: steadyAdapter({ async merge() { writes++; throw new Error('outcome unknown'); } }) }));
  assert.equal(writes, 1);
  await assert.rejects(() => executeManagedSourceMerge({ directory, plan, confirmation: confirmation(plan), adapter: steadyAdapter({ async merge() { writes++; return { exitCode: 0 }; } }) }));
  assert.equal(writes, 1);
});

test('postcondition receipt must independently match tree, both parents, exact paths and preservation', async (t) => {
  const directory = await journalDirectory();
  t.diagnostic(`synthetic fixture: ${directory}`);
  for (const bad of [
    { mergedTree: oid(9) }, { stableParent: oid(9) }, { sandboxParent: oid(9) },
    { deliveryPaths: ['desktop/launcher/approved.mjs', 'desktop/launcher/unreviewed.mjs'] },
    { preservationDigest: sha('tampered') },
  ]) {
    const subdir = await journalDirectory();
    const plan = createManagedSourceMergePlan(bodyFor(path.join(subdir, 'stable'), path.join(subdir, 'sandbox')));
    await assert.rejects(() => executeManagedSourceMerge({ directory: subdir, plan, confirmation: confirmation(plan), adapter: steadyAdapter({ async verify() { return receipt(bad); } }) }));
  }
});

test('revalidation drift after claim prevents merge; failed and nonzero writes never verify or retry', async (t) => {
  const directory = await journalDirectory();
  t.diagnostic(`synthetic fixture: ${directory}`);
  const plan = createManagedSourceMergePlan(bodyFor(path.join(directory, 'stable'), path.join(directory, 'sandbox')));
  let validations = 0, writes = 0, verifications = 0;
  const adapter = steadyAdapter({
    async revalidate(candidate) {
      validations++;
      return { planDigest: candidate.planDigest, reviewDigest: candidate.reviewDigest, namespaceDigest: candidate.namespaceDigest, blockers: validations === 1 ? [] : ['head_drift'] };
    },
    async merge() { writes++; return { exitCode: 1 }; },
    async verify() { verifications++; return receipt(); },
  });
  await assert.rejects(() => executeManagedSourceMerge({ directory, plan, confirmation: confirmation(plan), adapter }));
  assert.equal(validations, 2);
  assert.equal(writes, 0);
  assert.equal(verifications, 0);
});

test('genuine synthetic Git merge proves two parents, exact source delta, and unrelated dirty/untracked bytes preserved', async (t) => {
  const operationRoot = await journalDirectory();
  const stableRoot = path.join(operationRoot, 'stable');
  const sandboxRoot = path.join(stableRoot, '.worktrees', 'sandbox');
  const isolatedHome = path.join(operationRoot, 'empty-home');
  const emptyHooks = path.join(operationRoot, 'empty-hooks');
  const emptyTemplate = path.join(operationRoot, 'empty-template');
  await Promise.all([mkdir(stableRoot, { recursive: true }), mkdir(isolatedHome), mkdir(emptyHooks), mkdir(emptyTemplate)]);
  gitIsolation = { home: isolatedHome, hooks: emptyHooks };
  allowedGitRoots = new Set([path.resolve(stableRoot), path.resolve(sandboxRoot)]);
  t.diagnostic(`approved synthetic Git cwd roots=${[...allowedGitRoots].join('; ')}`);
  t.diagnostic(`synthetic Git fixture stable=${stableRoot} sandbox=${sandboxRoot}`);

  git(stableRoot, 'init', '-b', 'main', `--template=${emptyTemplate}`);
  git(stableRoot, 'config', 'user.email', 'fixture@example.invalid');
  git(stableRoot, 'config', 'user.name', 'Synthetic Fixture');
  await mkdir(path.join(stableRoot, '.git', 'info'), { recursive: true });
  await writeFile(path.join(stableRoot, '.git', 'info', 'exclude'), '/.worktrees/\n');
  await mkdir(path.join(stableRoot, 'desktop'), { recursive: true });
  await writeFile(path.join(stableRoot, 'README.md'), 'base\n');
  await writeFile(path.join(stableRoot, 'desktop', 'local-note.txt'), 'tracked base\n');
  git(stableRoot, 'add', 'README.md', 'desktop/local-note.txt');
  git(stableRoot, 'commit', '-m', 'synthetic base');
  git(stableRoot, 'branch', 'experiment/sandbox');
  git(stableRoot, 'worktree', 'add', sandboxRoot, 'experiment/sandbox');
  await mkdir(path.join(sandboxRoot, 'desktop'), { recursive: true });
  await writeFile(path.join(sandboxRoot, 'desktop', 'approved.mjs'), 'export const reviewed = true;\n');
  git(sandboxRoot, 'add', 'desktop/approved.mjs');
  git(sandboxRoot, 'commit', '-m', 'reviewed source');

  const stableHead = git(stableRoot, 'rev-parse', 'main');
  const sandboxHead = git(sandboxRoot, 'rev-parse', 'HEAD');
  const trackedDirtyBytes = Buffer.from('tracked base, unrelated local edit\0\xff\n');
  const untrackedRawBytes = Buffer.from([0, 1, 2, 254, 255, 10]);
  const trackedDirtyPath = path.join(stableRoot, 'desktop', 'local-note.txt');
  const untrackedRawPath = path.join(stableRoot, 'unrelated.raw');
  await writeFile(trackedDirtyPath, trackedDirtyBytes);
  await writeFile(untrackedRawPath, untrackedRawBytes);
  const stableIndexDigest = sha(await readFile(path.resolve(stableRoot, git(stableRoot, 'rev-parse', '--git-path', 'index'))));
  const sandboxIndexDigest = sha(await readFile(path.resolve(sandboxRoot, git(sandboxRoot, 'rev-parse', '--git-path', 'index'))));
  const preservationDigest = async () => sha(JSON.stringify({ tracked: sha(await readFile(trackedDirtyPath)), untracked: sha(await readFile(untrackedRawPath)) }));
  const expectedPreservation = await preservationDigest();
  const expectedTree = git(stableRoot, 'merge-tree', '--write-tree', stableHead, sandboxHead).split(/\r?\n/)[0];
  const body = bodyFor(stableRoot, sandboxRoot, {
    stable: { root: stableRoot, head: stableHead, branch: 'main', indexDigest: stableIndexDigest },
    sandbox: { root: sandboxRoot, head: sandboxHead, branch: 'experiment/sandbox', indexDigest: sandboxIndexDigest },
    deliveryPaths: [{ path: 'desktop/approved.mjs', beforeSha256: null, afterSha256: sha('export const reviewed = true;\n') }],
    expectedMergedTree: expectedTree, preservationDigest: expectedPreservation,
  });
  const plan = createManagedSourceMergePlan(body);

  const verifyReceipt = async () => {
    const mergeCommit = git(stableRoot, 'rev-parse', 'HEAD');
    const parents = git(stableRoot, 'rev-list', '--parents', '-n', '1', mergeCommit).split(' ').slice(1);
    assert.equal(parents.length, 2, 'merge commit must have exactly two parents');
    const mergedTree = git(stableRoot, 'rev-parse', `${mergeCommit}^{tree}`);
    const delivered = git(stableRoot, 'diff-tree', '--no-commit-id', '--name-only', '-r', `${stableHead}..${mergeCommit}`).split(/\r?\n/).filter(Boolean).sort();
    assert.equal(sha(await readFile(path.join(stableRoot, 'desktop', 'approved.mjs'))), plan.deliveryPaths[0].afterSha256);
    return { mergeCommit, mergedTree, stableParent: parents[0], sandboxParent: parents[1], deliveryPaths: delivered, preservationDigest: await preservationDigest() };
  };
  const adapter = {
    async revalidate(candidate) {
      const currentStable = git(stableRoot, 'rev-parse', 'main');
      const currentSandbox = git(sandboxRoot, 'rev-parse', 'HEAD');
      const currentStableIndex = sha(await readFile(path.resolve(stableRoot, git(stableRoot, 'rev-parse', '--git-path', 'index'))));
      const currentSandboxIndex = sha(await readFile(path.resolve(sandboxRoot, git(sandboxRoot, 'rev-parse', '--git-path', 'index'))));
      const unchanged = currentStable === stableHead && currentSandbox === sandboxHead
        && currentStableIndex === candidate.stable.indexDigest && currentSandboxIndex === candidate.sandbox.indexDigest
        && await preservationDigest() === candidate.preservationDigest;
      return { planDigest: candidate.planDigest, reviewDigest: candidate.reviewDigest, namespaceDigest: candidate.namespaceDigest, blockers: unchanged ? [] : ['baseline_drift'] };
    },
    async merge(_candidate, { operationId, writeId }) {
      assert.ok(operationId && writeId);
      const output = git(stableRoot, 'merge', '--no-edit', '--no-ff', 'experiment/sandbox');
      assert.equal(typeof output, 'string');
      return { exitCode: 0 };
    },
    async verify(candidate) {
      const observed = await verifyReceipt();
      assert.equal(observed.mergedTree, candidate.expectedMergedTree);
      return observed;
    },
  };
  const result = await executeManagedSourceMerge({ directory: operationRoot, plan, confirmation: confirmation(plan), adapter });
  assert.equal(result.status, 'source_merge_verified');
  assert.equal(result.readyForLaunch, false);
  const actual = await verifyReceipt();
  assert.deepEqual(actual.deliveryPaths, ['desktop/approved.mjs']);
  assert.equal(actual.stableParent, stableHead);
  assert.equal(actual.sandboxParent, sandboxHead);
  assert.equal(actual.mergedTree, expectedTree);
  assert.deepEqual(await readFile(trackedDirtyPath), trackedDirtyBytes);
  assert.deepEqual(await readFile(untrackedRawPath), untrackedRawBytes);
  t.diagnostic(`verified synthetic merge=${actual.mergeCommit} tree=${actual.mergedTree} parents=${actual.stableParent},${actual.sandboxParent}`);
  t.diagnostic(`preserved tracked dirty=${trackedDirtyPath} and unrelated raw/untracked=${untrackedRawPath}`);
});










