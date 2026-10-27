import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { inspectManagedCleanSource, createManagedCleanSourcePlan } from '../../launcher/managed-clean-source-adapter.mjs';
import { writeManagedSourcePackage, executeRecordedManagedSourceMerge } from '../../launcher/managed-source-package.mjs';

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const expectedSandboxRoot = path.resolve('F:/my-agent-main/.worktrees/sandbox');
if (path.resolve(process.cwd()) !== repositoryRoot || repositoryRoot !== expectedSandboxRoot) {
  throw new Error('Tests must run from F:/my-agent-main/.worktrees/sandbox; no fixtures were created');
}
const evidenceRoot = path.join(repositoryRoot, 'artifacts/managed-execution-20261024');
const sha = (value) => createHash('sha256').update(value).digest('hex');
const canonical = (value) => Array.isArray(value)
  ? `[${value.map(canonical).join(',')}]`
  : value && typeof value === 'object'
    ? `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`
    : JSON.stringify(value);
const installationId = '8c5c1cb0-f9db-43a0-a2cd-dc2bceacbed6';
const namespaceDigest = sha('synthetic empty namespace identity');
let gitExecutable;
let isolation;
const approvedGitRoots = new Set();

function nativeGit(cwd, ...args) {
  const resolvedCwd = path.resolve(cwd);
  if (!approvedGitRoots.has(resolvedCwd) || !resolvedCwd.startsWith(`${evidenceRoot}${path.sep}`)) {
    throw new Error(`Refusing Git outside the owned synthetic fixture roots: ${resolvedCwd}`);
  }
  if (!path.isAbsolute(gitExecutable) || !isolation?.home || !isolation?.hooks) {
    throw new Error('Native Git isolation is not initialized');
  }
  const env = {
    PATH: process.env.PATH ?? '', SYSTEMROOT: process.env.SYSTEMROOT ?? '', WINDIR: process.env.WINDIR ?? '',
    HOME: isolation.home, USERPROFILE: isolation.home, XDG_CONFIG_HOME: isolation.home,
    GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: isolation.globalConfig,
    GIT_TERMINAL_PROMPT: '0', GIT_OPTIONAL_LOCKS: '0', GIT_CONFIG_COUNT: '0',
    GIT_ATTR_NOSYSTEM: '1', LC_ALL: 'C',
  };
  const fixed = [
    '--no-pager', '-c', `safe.directory=${resolvedCwd}`, '-c', 'commit.gpgsign=false',
    '-c', 'tag.gpgsign=false', '-c', 'core.fsmonitor=false', '-c', `core.hooksPath=${isolation.hooks}`,
    '-c', 'credential.helper=', '-c', 'maintenance.auto=false', '-c', 'gc.auto=0',
  ];
  const result = spawnSync(gitExecutable, [...fixed, ...args], {
    cwd: resolvedCwd, env, encoding: 'utf8', windowsHide: true, timeout: 15000, maxBuffer: 1024 * 1024,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`synthetic Git failed (${args[0]}): ${result.stderr}`);
  return result.stdout.trim();
}

async function digestFile(filename) {
  return sha(await readFile(filename));
}

function absoluteGitPath(root, gitPath) {
  return path.resolve(root, gitPath);
}

function samePath(left, right) {
  const a = path.resolve(left), b = path.resolve(right);
  return process.platform === 'win32' ? a.toLowerCase() === b.toLowerCase() : a === b;
}

test('a canonical recorded package authorizes one clean shared-worktree merge and never readiness', async (t) => {
  gitExecutable = 'C:/Program Files/Git/cmd/git.exe';
  assert.ok(path.isAbsolute(gitExecutable), 'trusted native Git executable must be an absolute path');

  const requestedFixture = process.env.MANAGED_EXECUTION_FIXTURE_ROOT;
  const runRoot = requestedFixture ? path.resolve(requestedFixture) : await mkdtemp(path.join(evidenceRoot, 'recorded-execution-'));
  const relativeFixture = path.relative(evidenceRoot, runRoot);
  assert.ok(relativeFixture && !relativeFixture.startsWith('..') && !path.isAbsolute(relativeFixture), 'reused fixture must remain below the evidence root');
  const stableRoot = path.join(runRoot, 'stable');
  const sandboxRoot = path.join(stableRoot, '.worktrees', 'sandbox');
  const home = path.join(runRoot, 'empty-home');
  const hooks = path.join(runRoot, 'empty-hooks');
  const template = path.join(runRoot, 'empty-template');
  const globalConfig = path.join(runRoot, 'empty-global.gitconfig');
  await Promise.all([mkdir(stableRoot, { recursive: true }), mkdir(home, { recursive: true }), mkdir(hooks, { recursive: true }), mkdir(template, { recursive: true }), writeFile(globalConfig, '')]);
  isolation = { home, hooks, globalConfig };
  approvedGitRoots.add(path.resolve(stableRoot));
  approvedGitRoots.add(path.resolve(sandboxRoot));
  t.diagnostic(`fixture=${runRoot}`);
  t.diagnostic(`nativeGit=${gitExecutable}`);

  const deliveredBytes = Buffer.from('export const reviewedRecordedSource = true;\n');
  if (fs.existsSync(path.join(stableRoot, '.git')) && fs.existsSync(path.join(sandboxRoot, '.git'))) {
    assert.ok(samePath(nativeGit(stableRoot, 'rev-parse', '--show-toplevel'), stableRoot), 'reused base repository must be the owned fixture');
    assert.ok(samePath(nativeGit(sandboxRoot, 'rev-parse', '--show-toplevel'), sandboxRoot), 'reused linked worktree must be the owned fixture');
    const configPath = path.join(stableRoot, '.git', 'config');
    const config = await readFile(configPath, 'utf8');
    const cleanConfig = config.replace(/^(\s*gpgsign\s*=\s*false\s*\r?\n)/gim, '');
    if (cleanConfig !== config) await writeFile(configPath, cleanConfig);
    assert.deepEqual(await readFile(path.join(sandboxRoot, 'desktop', 'launcher', 'recorded-source.mjs')), deliveredBytes, 'reused reviewed source must retain its fixture bytes');
  } else {
    nativeGit(stableRoot, 'init', '-b', 'main', `--template=${template}`);
    nativeGit(stableRoot, 'config', 'user.email', 'fixture@example.invalid');
    nativeGit(stableRoot, 'config', 'user.name', 'Synthetic Managed Execution');
    await mkdir(path.join(stableRoot, '.git', 'info'), { recursive: true });
    await writeFile(path.join(stableRoot, '.git', 'info', 'exclude'), '/.worktrees/\n');
    await mkdir(path.join(stableRoot, 'desktop', 'launcher'), { recursive: true });
    await writeFile(path.join(stableRoot, 'desktop', 'launcher', 'base.mjs'), 'export const base = true;\n');
    nativeGit(stableRoot, 'add', 'desktop/launcher/base.mjs');
    nativeGit(stableRoot, 'commit', '-m', 'synthetic stable base');
    nativeGit(stableRoot, 'branch', 'experiment/sandbox');
    nativeGit(stableRoot, 'worktree', 'add', sandboxRoot, 'experiment/sandbox');
    await writeFile(path.join(sandboxRoot, 'desktop', 'launcher', 'recorded-source.mjs'), deliveredBytes);
    nativeGit(sandboxRoot, 'add', 'desktop/launcher/recorded-source.mjs');
    nativeGit(sandboxRoot, 'commit', '-m', 'reviewed recorded source');
  }

  const stableHead = nativeGit(stableRoot, 'rev-parse', 'HEAD');
  const sandboxHead = nativeGit(sandboxRoot, 'rev-parse', 'HEAD');
  const stableIndex = absoluteGitPath(stableRoot, nativeGit(stableRoot, 'rev-parse', '--git-path', 'index'));
  const sandboxIndex = absoluteGitPath(sandboxRoot, nativeGit(sandboxRoot, 'rev-parse', '--git-path', 'index'));
  const stableIndexBefore = await digestFile(stableIndex);
  const sandboxIndexBefore = await digestFile(sandboxIndex);
  assert.equal(nativeGit(stableRoot, 'symbolic-ref', '--short', 'HEAD'), 'main', 'retained stable fixture must remain on its expected branch');
  assert.equal(nativeGit(sandboxRoot, 'symbolic-ref', '--short', 'HEAD'), 'experiment/sandbox', 'retained sandbox fixture must remain on its expected branch');
  assert.deepEqual(nativeGit(sandboxRoot, 'rev-list', '--parents', '-n', '1', sandboxHead).split(' ').slice(1), [stableHead], 'retained fixture must still be unmerged and linear');
  const verifyIndexTree = (root, head) => {
    const indexEntries = nativeGit(root, 'ls-files', '--stage').split(/\r?\n/).filter(Boolean).map((line) => {
      const match = /^(\d+) ([a-f0-9]+) 0\t(.+)$/.exec(line);
      assert.ok(match, 'unexpected stage-0 index entry: ' + line);
      return match[1] + ' ' + match[2] + '\t' + match[3];
    }).sort();
    const treeEntries = nativeGit(root, 'ls-tree', '-r', '--full-tree', head).split(/\r?\n/).filter(Boolean).map((line) => {
      const match = /^(\d+) [^ ]+ ([a-f0-9]+)\t(.+)$/.exec(line);
      assert.ok(match, 'unexpected committed tree entry: ' + line);
      return match[1] + ' ' + match[2] + '\t' + match[3];
    }).sort();
    assert.deepEqual(indexEntries, treeEntries, 'retained fixture index must match its committed tree');
  };
  verifyIndexTree(stableRoot, stableHead);
  verifyIndexTree(sandboxRoot, sandboxHead);
  t.diagnostic('premerge stableHead=' + stableHead + ' sandboxHead=' + sandboxHead + ' stableIndexSha256=' + stableIndexBefore + ' sandboxIndexSha256=' + sandboxIndexBefore);
  const sandboxStashBefore = nativeGit(sandboxRoot, 'for-each-ref', '--format=%(refname) %(objectname)', 'refs/stash');
  const stableStashBefore = nativeGit(stableRoot, 'for-each-ref', '--format=%(refname) %(objectname)', 'refs/stash');
  const stableTrackedNonDelivery = await readFile(path.join(stableRoot, 'desktop', 'launcher', 'base.mjs'));
  const sourceBytes = await readFile(path.join(sandboxRoot, 'desktop', 'launcher', 'recorded-source.mjs'));
  const sourceHash = sha(sourceBytes);
  const review = await inspectManagedCleanSource({ stableRoot, sandboxRoot, gitExecutable });
  const plan = createManagedCleanSourcePlan({ review, installationId, namespaceDigest });
  const confirmation = { planDigest: plan.planDigest, reviewDigest: plan.reviewDigest, namespaceDigest };
  const operationDirectory = path.join(runRoot, 'operation');
  await mkdir(operationDirectory);
  const writtenPackage = await writeManagedSourcePackage({ directory: operationDirectory, plan, review, confirmation });
  const packagePath = path.join(operationDirectory, 'managed-source-package.json');
  const canonicalPackageBeforeExecution = await readFile(packagePath);
  assert.equal(canonicalPackageBeforeExecution.at(-1), 0x0a, 'complete package must be LF-terminated before execution');
  assert.deepEqual(JSON.parse(canonicalPackageBeforeExecution.toString('utf8')), writtenPackage);
  assert.equal(canonicalPackageBeforeExecution.toString('utf8'), `${canonical(writtenPackage)}\n`, 'persisted package must be the complete canonical object');
  assert.ok(writtenPackage.plan && writtenPackage.review && writtenPackage.confirmation, 'persisted package binds all execution inputs');

  const intactPackageBytes = Buffer.from(canonicalPackageBeforeExecution);
  const tamperedPackage = { ...writtenPackage, packageDigest: sha('tampered package digest') };
  await writeFile(packagePath, `${JSON.stringify(tamperedPackage)}\n`);
  await assert.rejects(
    executeRecordedManagedSourceMerge({ directory: operationDirectory, installationId, namespaceDigest, gitExecutable }),
    /digest|tamper|package|canonical/i,
    'tampered full package must be refused before any Git write',
  );
  assert.equal(nativeGit(stableRoot, 'rev-parse', 'HEAD'), stableHead, 'package refusal must leave stable HEAD unchanged');
  assert.equal(await digestFile(stableIndex), stableIndexBefore, 'package refusal must leave stable index bytes unchanged');
  assert.equal(await digestFile(sandboxIndex), sandboxIndexBefore, 'package refusal must leave sandbox index bytes unchanged');
  assert.equal(nativeGit(sandboxRoot, 'rev-parse', 'HEAD'), sandboxHead, 'package refusal must leave sandbox HEAD unchanged');
  await writeFile(packagePath, intactPackageBytes);

  const boundaryOperationDirectory = path.join(runRoot, 'boundary-race-operation');
  await mkdir(boundaryOperationDirectory);
  await writeManagedSourcePackage({ directory: boundaryOperationDirectory, plan, review, confirmation });
  const boundaryPackagePath = path.join(boundaryOperationDirectory, 'managed-source-package.json');
  const boundaryPackageBefore = await readFile(boundaryPackagePath);
  const boundaryJournalPath = path.join(boundaryOperationDirectory, 'managed-source-transaction.jsonl');
  const originalLstatSync = fs.lstatSync;
  let packageLstatsAfterIntent = 0;
  let tamperedAtFinalBoundary = false;
  fs.lstatSync = function (filename, ...args) {
    if (path.resolve(String(filename)) === path.resolve(boundaryPackagePath) && fs.existsSync(boundaryJournalPath)) {
      const rows = fs.readFileSync(boundaryJournalPath, 'utf8').trimEnd().split('\n').filter(Boolean).map((line) => JSON.parse(line));
      if (rows.some((row) => row.type === 'write_intent')) {
        packageLstatsAfterIntent++;
        if (packageLstatsAfterIntent === 4) {
          const bytes = fs.readFileSync(boundaryPackagePath);
          const marker = Buffer.from('\"packageDigest\":\"');
          const digestStart = bytes.indexOf(marker) + marker.length;
          assert.ok(digestStart >= marker.length, 'canonical package digest must be present');
          const replacementByte = bytes[digestStart] === 0x30 ? 0x31 : 0x30;
          const fd = fs.openSync(boundaryPackagePath, 'r+');
          try { fs.writeSync(fd, Buffer.from([replacementByte]), 0, 1, digestStart); fs.fsyncSync(fd); }
          finally { fs.closeSync(fd); }
          tamperedAtFinalBoundary = true;
        }
      }
    }
    return originalLstatSync.call(this, filename, ...args);
  };
  syncBuiltinESMExports();
  try {
    await assert.rejects(
      executeRecordedManagedSourceMerge({ directory: boundaryOperationDirectory, installationId, namespaceDigest, gitExecutable }),
      /maintenance|unknown|expired|changed|drift|package/i,
      'same-size packet tampering at the final write boundary must block Git execution',
    );
  } finally {
    fs.lstatSync = originalLstatSync;
    syncBuiltinESMExports();
  }
  assert.equal(tamperedAtFinalBoundary, true, 'packet mutation must occur at the post-write-intent final boundary');
  assert.equal(packageLstatsAfterIntent, 4, 'race must pass all three outer-lease file checks and mutate at the final-boundary lease check');
  assert.equal(await readFile(boundaryPackagePath).then((bytes) => bytes.length), boundaryPackageBefore.length, 'tampered package must retain its exact byte length');
  assert.notDeepEqual(await readFile(boundaryPackagePath), boundaryPackageBefore, 'same-size boundary mutation must alter the persisted packet bytes');
  t.diagnostic('boundaryRace=detected after write_intent; gitWrite=none; journal=maintenance_required');
  assert.equal(nativeGit(stableRoot, 'rev-parse', 'HEAD'), stableHead, 'final-boundary refusal must not change stable HEAD');
  assert.equal(await digestFile(stableIndex), stableIndexBefore, 'final-boundary refusal must not change stable index bytes');
  assert.equal(nativeGit(sandboxRoot, 'rev-parse', 'HEAD'), sandboxHead, 'final-boundary refusal must not change sandbox HEAD');
  assert.equal(await digestFile(sandboxIndex), sandboxIndexBefore, 'final-boundary refusal must not change sandbox index bytes');
  const boundaryRows = (await readFile(boundaryJournalPath, 'utf8')).trimEnd().split('\n').map((line) => JSON.parse(line));
  assert.ok(boundaryRows.some((row) => row.type === 'write_intent'), 'failed operation must retain its write intent');
  assert.ok(boundaryRows.some((row) => row.type === 'failure' && row.payload.code === 'interrupted'), 'uncertain boundary refusal must remain maintenance-required');
  assert.ok(!boundaryRows.some((row) => row.type === 'write_exit' || row.type === 'finish'), 'no Git write or success receipt may be recorded');
  await writeFile(boundaryPackagePath, boundaryPackageBefore);
  await assert.rejects(
    executeRecordedManagedSourceMerge({ directory: boundaryOperationDirectory, installationId, namespaceDigest, gitExecutable }),
    /claim|single-use|already|journal|transaction/i,
    'a boundary-failed operation must remain single-use after its package is restored',
  );
  assert.equal(nativeGit(stableRoot, 'rev-parse', 'HEAD'), stableHead, 'single-use refusal must leave stable HEAD untouched');
  assert.equal(await digestFile(stableIndex), stableIndexBefore, 'single-use refusal must leave stable index untouched');
  assert.equal(nativeGit(sandboxRoot, 'rev-parse', 'HEAD'), sandboxHead, 'single-use refusal must leave sandbox HEAD untouched');
  assert.equal(await digestFile(sandboxIndex), sandboxIndexBefore, 'single-use refusal must leave sandbox index untouched');
  const result = await executeRecordedManagedSourceMerge({ directory: operationDirectory, installationId, namespaceDigest, gitExecutable });
  assert.equal(result.readyForLaunch, false, 'source merge must not report runtime readiness');
  assert.equal(result.status, 'source_merge_verified');
  assert.equal(await readFile(packagePath, 'utf8'), intactPackageBytes.toString('utf8'), 'execution must not rewrite the canonical package');

  const mergeCommit = nativeGit(stableRoot, 'rev-parse', 'HEAD');
  const parents = nativeGit(stableRoot, 'rev-list', '--parents', '-n', '1', mergeCommit).split(' ').slice(1);
  assert.deepEqual(parents, [stableHead, sandboxHead], 'actual merge commit must have the exact stable and sandbox parents');
  const actualTree = nativeGit(stableRoot, 'rev-parse', `${mergeCommit}^{tree}`);
  assert.equal(actualTree, review.expectedMergedTree, 'actual merged tree must equal the reviewed tree');
  const actualPaths = nativeGit(stableRoot, 'diff-tree', '--no-commit-id', '--name-only', '-r', `${stableHead}..${mergeCommit}`).split(/\r?\n/).filter(Boolean).sort();
  assert.deepEqual(actualPaths, ['desktop/launcher/recorded-source.mjs'], 'actual merge delta must contain only the reviewed path');
  assert.equal(await digestFile(path.join(stableRoot, 'desktop', 'launcher', 'recorded-source.mjs')), sourceHash, 'merged file raw bytes must match the reviewed source hash');
  assert.deepEqual(await readFile(path.join(stableRoot, 'desktop', 'launcher', 'base.mjs')), stableTrackedNonDelivery, 'stable non-delivery source bytes must remain unchanged');
  assert.equal(nativeGit(sandboxRoot, 'rev-parse', 'HEAD'), sandboxHead, 'sandbox HEAD must remain unchanged');
  assert.equal(await digestFile(sandboxIndex), sandboxIndexBefore, 'sandbox index bytes must remain unchanged');
  const stableIndexEntries = nativeGit(stableRoot, 'ls-files', '--stage').split(/\r?\n/).filter(Boolean).map((line) => {
    const match = /^(\d+) ([a-f0-9]+) \d+\t(.+)$/.exec(line);
    assert.ok(match, 'unexpected stable index entry: ' + line);
    return match[1] + ' ' + match[2] + '\t' + match[3];
  }).sort();
  const mergedTreeEntries = nativeGit(stableRoot, 'ls-tree', '-r', '--full-tree', actualTree).split(/\r?\n/).filter(Boolean).map((line) => {
    const match = /^(\d+) [^ ]+ ([a-f0-9]+)\t(.+)$/.exec(line);
    assert.ok(match, 'unexpected merged tree entry: ' + line);
    return match[1] + ' ' + match[2] + '\t' + match[3];
  }).sort();
  assert.deepEqual(stableIndexEntries, mergedTreeEntries, 'stable index mode/OID/path entries must represent the verified merged tree');
  assert.equal(nativeGit(sandboxRoot, 'for-each-ref', '--format=%(refname) %(objectname)', 'refs/stash'), sandboxStashBefore, 'sandbox stash metadata must remain unchanged');
  assert.equal(nativeGit(stableRoot, 'for-each-ref', '--format=%(refname) %(objectname)', 'refs/stash'), stableStashBefore, 'stable stash metadata must remain unchanged');

  const journalPath = path.join(operationDirectory, 'managed-source-transaction.jsonl');
  const journalText = await readFile(journalPath, 'utf8');
  const journalEvents = journalText.trimEnd().split('\n').map((line) => JSON.parse(line));
  assert.ok(journalEvents.some((event) => event.type === 'write_intent'), 'coordinator must durably record write intent for the actual merge');
  assert.ok(journalEvents.some((event) => event.type === 'finish'), 'coordinator must durably record verified receipt');

  t.diagnostic(`verifiedMerge=${mergeCommit} tree=${actualTree} parents=${parents.join(',')} pathSha256=${sourceHash}`);
  t.diagnostic(`package=${packagePath} journal=${journalPath}`);

  const headAfterFirstExecution = nativeGit(stableRoot, 'rev-parse', 'HEAD');
  const stableIndexAfterFirstExecution = await digestFile(stableIndex);
  await assert.rejects(
    executeRecordedManagedSourceMerge({ directory: operationDirectory, installationId, namespaceDigest, gitExecutable }),
    /journal|single|already|operation|exist|used|ancestor|stale|revalidation/i,
    'second execution must refuse the already-used operation without another Git mutation',
  );
  assert.equal(nativeGit(stableRoot, 'rev-parse', 'HEAD'), headAfterFirstExecution, 'second execution must not change actual stable HEAD');
  assert.equal(await digestFile(stableIndex), stableIndexAfterFirstExecution, 'second execution must not change stable index');
  assert.equal(nativeGit(sandboxRoot, 'rev-parse', 'HEAD'), sandboxHead, 'second execution must not change sandbox HEAD');
  assert.equal(await digestFile(sandboxIndex), sandboxIndexBefore, 'second execution must not change sandbox index');

});
