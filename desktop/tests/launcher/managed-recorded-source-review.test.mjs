import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { createRequire, syncBuiltinESMExports } from 'node:module';
import { mkdir, mkdtemp, readFile, readdir, writeFile, link, symlink } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import {
  writeManagedSourcePackage,
  readManagedSourcePackage,
  executeRecordedManagedSourceMerge,
} from '../../launcher/managed-source-package.mjs';
import {
  validateManagedCleanSourceReview,
  createManagedCleanSourcePlan,
} from '../../launcher/managed-clean-source-adapter.mjs';

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const expectedSandboxRoot = path.resolve('F:/my-agent-main/.worktrees/sandbox');
if (path.resolve(process.cwd()) !== repositoryRoot || repositoryRoot !== expectedSandboxRoot) {
  throw new Error('Tests must run from F:/my-agent-main/.worktrees/sandbox; no review fixtures were created');
}
const reviewRoot = path.join(repositoryRoot, 'artifacts/managed-execution-20261024/independent-review');
const sha = value => createHash('sha256').update(value).digest('hex');
const oid = value => String(value).repeat(40);
const installationId = 'd9f85c1c-b585-4e28-8e73-ec866aaf632a';
const namespaceDigest = sha('independent review synthetic namespace');

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}
function rawReview() {
  const stableRoot = path.resolve('C:/synthetic/stable');
  const commonDir = path.join(stableRoot, '.git');
  const sandboxRoot = path.join(stableRoot, '.worktrees', 'sandbox');
  const stable = { root: stableRoot, head: oid(1), branch: 'main', indexDigest: sha('stable index'), gitDir: commonDir, commonDir, tree: oid(3), metadataDigest: sha('stable metadata'), files: [] };
  const sandbox = { root: sandboxRoot, head: oid(2), branch: 'experiment/sandbox', indexDigest: sha('sandbox index'), gitDir: path.join(commonDir, 'worktrees', 'sandbox'), commonDir, tree: oid(4), metadataDigest: sha('sandbox metadata'), files: [{ path: 'desktop/launcher/example.mjs', sha256: sha('after'), mode: '100644', oid: oid(5) }] };
  const body = { format: 1, mode: 'managed_clean_source_review_v1', stable, sandbox, history: [{ commit: oid(2), parents: [oid(1)], paths: ['desktop/launcher/example.mjs'] }], deliveryPaths: [{ path: 'desktop/launcher/example.mjs', beforeSha256: null, afterSha256: sha('after') }], expectedMergedTree: sandbox.tree, preservationDigest: sha('preservation'), blockers: [] };
  body.reviewDigest = sha(canonical(body));
  return body;
}
function packageInputs() {
  const review = rawReview();
  const checkedReview = validateManagedCleanSourceReview(review);
  const plan = createManagedCleanSourcePlan({ review: checkedReview, installationId, namespaceDigest });
  return { review, plan: JSON.parse(canonical(plan)), confirmation: { planDigest: plan.planDigest, reviewDigest: plan.reviewDigest, namespaceDigest } };
}
async function operationDirectory(t, label) {
  await mkdir(reviewRoot, { recursive: true });
  const directory = await mkdtemp(path.join(reviewRoot, `${label}-`));
  t.diagnostic(`retained review operation directory: ${directory}`);
  return directory;
}

test('package writer rejects review, plan, and confirmation accessors without invoking them or creating a file', async t => {
  const cases = ['review', 'plan', 'confirmation'];
  for (const field of cases) {
    const directory = await operationDirectory(t, `accessor-${field}`);
    const inputs = packageInputs();
    let invoked = false;
    if (field === 'review') {
      Object.defineProperty(inputs.review.stable, 'head', { enumerable: true, get() { invoked = true; return oid(1); } });
    } else if (field === 'plan') {
      Object.defineProperty(inputs.plan, 'installationId', { enumerable: true, get() { invoked = true; return installationId; } });
    } else {
      Object.defineProperty(inputs.confirmation, 'planDigest', { enumerable: true, get() { invoked = true; return inputs.plan.planDigest; } });
    }
    assert.throws(() => writeManagedSourcePackage({ directory, ...inputs }), /accessor|hidden|plain|keys/i, `${field} accessor must be rejected`);
    assert.equal(invoked, false, `${field} getter must not execute`);
    assert.deepEqual(await readdir(directory), [], `${field} rejection must precede exclusive file creation`);
  }
});

test('executor detects same-size package mutation during its fresh descriptor fsync before journal or adapter work', async t => {
  const directory = await operationDirectory(t, 'fsync-race');
  const inputs = packageInputs();
  await writeManagedSourcePackage({ directory, ...inputs });
  const packagePath = path.join(directory, 'managed-source-package.json');
  const before = await readFile(packagePath);
  const fs = createRequire(import.meta.url)('node:fs');
  const originalFsync = fs.fsyncSync;
  const originalWrite = fs.writeSync;
  let mutated = false;
  fs.fsyncSync = fd => {
    originalFsync(fd);
    if (!mutated) {
      mutated = true;
      originalWrite(fd, Buffer.from(' '), 0, 1, 0);
      originalFsync(fd);
    }
  };
  syncBuiltinESMExports();
  try {
    await assert.rejects(
      () => executeRecordedManagedSourceMerge({ directory, installationId, namespaceDigest, gitExecutable: 'not-an-absolute-executable' }),
      /drift|changed|package|canonical/i,
    );
  } finally {
    fs.fsyncSync = originalFsync;
    syncBuiltinESMExports();
  }
  assert.equal(mutated, true, 'test must exercise the executor fsync hook');
  assert.equal((await readFile(packagePath)).length, before.length, 'mutation preserves size to exercise snapshot/content checks');
  assert.deepEqual(await readdir(directory), ['managed-source-package.json'], 'package drift must be refused before journal claim and adapter work');
});

test('package reader rejects a hard-linked package when the backing filesystem supports hard links', async t => {
  const directory = await operationDirectory(t, 'hardlink');
  await writeManagedSourcePackage({ directory, ...packageInputs() });
  const packagePath = path.join(directory, 'managed-source-package.json');
  try {
    await link(packagePath, path.join(directory, 'alias.json'));
  } catch (error) {
    if (['EPERM', 'EACCES', 'ENOTSUP', 'EOPNOTSUPP', 'EXDEV', 'EINVAL', 'EISDIR'].includes(error.code)) {
      t.skip(`filesystem does not support the hard-link adversary (${error.code})`);
      return;
    }
    throw error;
  }
  await assert.rejects(() => readManagedSourcePackage({ directory, installationId, namespaceDigest }), /single-link|hard link|ordinary/i);
});



test('package reader rejects a symlinked operation-directory spelling', async t => {
  const directory = await operationDirectory(t, 'directory-link-target');
  await writeManagedSourcePackage({ directory, ...packageInputs() });
  const alias = path.join(reviewRoot, `directory-link-${randomUUID()}`);
  try {
    await symlink(directory, alias, 'junction');
  } catch (error) {
    if (['EPERM', 'EACCES', 'ENOTSUP', 'EOPNOTSUPP', 'EINVAL', 'EISDIR'].includes(error.code)) {
      t.skip(`filesystem does not permit directory-link creation (${error.code})`);
      return;
    }
    throw error;
  }
  await assert.rejects(() => readManagedSourcePackage({ directory: alias, installationId, namespaceDigest }), /link|reparse|aliased|ancestor/i);
});


test('static call-order guard: metadata and index checks precede Git reads, replacement refs are disabled and rejected, and package lease guard immediately precedes synchronous merge spawn', async () => {
  const adapterPath = new URL('../../launcher/managed-clean-source-adapter.mjs', import.meta.url);
  const packagePath = new URL('../../launcher/managed-source-package.mjs', import.meta.url);
  const metadataPath = new URL('../../launcher/managed-clean-git-metadata.mjs', import.meta.url);
  const integrationPath = new URL('./managed-recorded-source-execution.test.mjs', import.meta.url);
  const [adapter, packageSource, metadata, integration] = await Promise.all([
    readFile(adapterPath, 'utf8'), readFile(packagePath, 'utf8'), readFile(metadataPath, 'utf8'), readFile(integrationPath, 'utf8'),
  ]);

  assert.match(adapter, /GIT_NO_REPLACE_OBJECTS:\s*'1'/);
  const stateStart = adapter.indexOf('async function state(');
  const stateEnd = adapter.indexOf('function historyFiles(', stateStart);
  assert.ok(stateStart >= 0 && stateEnd > stateStart, 'state function boundary must be present');
  const state = adapter.slice(stateStart, stateEnd);
  const packedReplaceCheck = state.indexOf("'for-each-ref', '--format=%(refname)', 'refs/replace'");
  const headRead = state.indexOf("'rev-parse', '--verify', 'HEAD'");
  assert.ok(packedReplaceCheck >= 0 && packedReplaceCheck < headRead, 'loose and packed replacement refs must be refused before resolving HEAD');
  assert.ok(state.indexOf('assertManagedPlainIndex(indexBytes') < state.indexOf("'ls-files', '--stage', '-z'"), 'raw index format must be checked before trusting Git’s projected index entries');

  const gitMetaStart = adapter.indexOf('async function gitMeta(');
  const gitMetaEnd = adapter.indexOf('function envFor(', gitMetaStart);
  assert.ok(gitMetaStart >= 0 && gitMetaEnd > gitMetaStart, 'gitMeta function boundary must be present');
  const gitMeta = adapter.slice(gitMetaStart, gitMetaEnd);
  assert.ok(gitMeta.includes('await assertManagedGitObjectStore('), 'object-store/worktree metadata guard must run before the first Git process');
  assert.ok(gitMeta.indexOf('await assertManagedGitObjectStore(') < gitMeta.indexOf('return { configHashes'), 'metadata guard must finish before Git metadata can be returned to invoke callers');
  assert.match(metadata, /export function assertManagedPlainIndex\(/);
  assert.match(metadata, /export async function assertManagedGitObjectStore\(/);

  const mergeStart = adapter.indexOf('async function merge(plan, context)');
  const verifyStart = adapter.indexOf('async function verify(plan)', mergeStart);
  assert.ok(mergeStart >= 0 && verifyStart > mergeStart, 'merge function boundary must be present');
  const merge = adapter.slice(mergeStart, verifyStart);
  const boundary = merge.indexOf('const result = assertWriteBoundary()');
  const attempted = merge.indexOf('attempted = true', boundary);
  const spawn = merge.indexOf('invoke(review.stable.root', attempted);
  assert.ok(boundary >= 0 && attempted > boundary && spawn > attempted, 'synchronous lease check and single-use latch must precede Git spawn');
  assert.doesNotMatch(merge.slice(boundary, spawn), /\bawait\b/, 'no async boundary may intervene between the package guard and Git spawn');
  assert.match(merge, /typeof result\.then === 'function'[\s\S]*?Write-boundary guard must be synchronous/);
  assert.match(packageSource, /assertWriteBoundary:\s*\(\)\s*=>\s*\{\s*assertExecutionLease\(lease, binding\);\s*\}/);
  assert.doesNotMatch(packageSource.slice(packageSource.indexOf('export async function executeRecordedManagedSourceMerge'), packageSource.indexOf('function parseCanonicalPackage')), /assertWriteBoundary\s*[,}]/, 'recorded executor must not accept a caller-supplied boundary callback');
  assert.match(integration, /if \(packageLstatsAfterIntent === 4\)/, 'fault injection must occur at the final boundary guard, after the wrapper lease check');
  assert.match(integration, /assert\.equal\(packageLstatsAfterIntent, 4,/, 'integration must assert it reached that exact post-wrapper boundary');
});






