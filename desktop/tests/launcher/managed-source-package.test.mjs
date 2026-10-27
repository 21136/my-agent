import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire, syncBuiltinESMExports } from 'node:module';
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
  throw new Error('Tests must run from F:/my-agent-main/.worktrees/sandbox; no fixtures were created');
}
const evidenceRoot = path.join(repositoryRoot, 'artifacts/managed-execution-20261024');
const sha = value => createHash('sha256').update(value).digest('hex');
const oid = value => String(value).repeat(40);
const installationId = 'f6c2d116-3dc5-4ff1-99b1-c7feac43e2f0';
const namespaceDigest = sha('test namespace identity');

function makeReview() {
  const files = [{ path: 'desktop/launcher/example.mjs', sha256: sha('after'), mode: '100644', oid: oid(2) }];
  const stableRoot = path.resolve('C:/synthetic/stable');
  const sandboxRoot = path.resolve(stableRoot, '.worktrees', 'sandbox');
  const commonDir = path.resolve(stableRoot, '.git');
  const stable = { root: stableRoot, head: oid(1), branch: 'main', indexDigest: sha('stable index'), gitDir: commonDir, commonDir, tree: oid(3), metadataDigest: sha('stable metadata'), files: [] };
  const sandbox = { root: sandboxRoot, head: oid(2), branch: 'experiment/sandbox', indexDigest: sha('sandbox index'), gitDir: path.join(commonDir, 'worktrees', 'sandbox'), commonDir, tree: oid(4), metadataDigest: sha('sandbox metadata'), files };
  const history = [{ commit: oid(2), parents: [oid(1)], paths: ['desktop/launcher/example.mjs'] }];
  const deliveryPaths = [{ path: 'desktop/launcher/example.mjs', beforeSha256: null, afterSha256: sha('after') }];
  const body = { format: 1, mode: 'managed_clean_source_review_v1', stable, sandbox, history, deliveryPaths, expectedMergedTree: sandbox.tree, preservationDigest: sha('preservation'), blockers: [] };
  body.reviewDigest = sha(canonical(body));
  return body;
}
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}
function makePackageInputs() {
  const review = validateManagedCleanSourceReview(makeReview());
  const plan = createManagedCleanSourcePlan({ review: JSON.parse(canonical(review)), installationId, namespaceDigest });
  const confirmation = { planDigest: plan.planDigest, reviewDigest: review.reviewDigest, namespaceDigest };
  return { review, plan, confirmation };
}
async function operationDirectory(t, name = 'package') {
  await mkdir(evidenceRoot, { recursive: true });
  const directory = await mkdtemp(path.join(evidenceRoot, `${name}-`));
  t.diagnostic(`retained operation directory: ${directory}`);
  return directory;
}

test('writes an exclusive canonical full package, fsyncs it, and returns an immutable clone', async t => {
  const directory = await operationDirectory(t);
  const inputs = makePackageInputs();
  const originalInputs = canonical(inputs);
  const stored = await writeManagedSourcePackage({ directory, ...inputs });
  assert.deepEqual(Object.keys(stored).sort(), ['confirmation', 'format', 'mode', 'packageDigest', 'plan', 'review'].sort());
  assert.equal(stored.format, 1);
  assert.equal(stored.mode, 'managed_source_package_v1');
  assert.ok(Object.isFrozen(stored) && Object.isFrozen(stored.review) && Object.isFrozen(stored.plan));
  assert.notEqual(stored.review, inputs.review, 'caller objects are cloned');
  assert.equal(inputs.review.deliveryPaths[0].path, 'desktop/launcher/example.mjs');
  assert.equal(canonical(inputs), originalInputs, 'validation must not mutate caller-owned package inputs');
  const bytes = await readFile(path.join(directory, 'managed-source-package.json'), 'utf8');
  assert.ok(bytes.endsWith('\n'));
  assert.equal(bytes, `${canonical(JSON.parse(bytes))}\n`);
  const readBack = await readManagedSourcePackage({ directory, installationId, namespaceDigest });
  assert.deepEqual(readBack, stored);
});

test('never takes over an existing fixed package name, even if bytes are corrupt', async t => {
  const directory = await operationDirectory(t, 'occupied');
  const file = path.join(directory, 'managed-source-package.json');
  await writeFile(file, 'occupied sentinel\n', { flag: 'wx' });
  assert.throws(() => writeManagedSourcePackage({ directory, ...makePackageInputs() }));
  assert.equal(await readFile(file, 'utf8'), 'occupied sentinel\n');
});

test('rejects mismatched confirmation before creating a package', async t => {
  const directory = await operationDirectory(t, 'bad-confirmation');
  assert.throws(() => readManagedSourcePackage({ directory, installationId, namespaceDigest }));
  const input = makePackageInputs();
  assert.throws(() => writeManagedSourcePackage({ directory, ...input, confirmation: { ...input.confirmation, reviewDigest: sha('wrong') } }));
  await assert.rejects(() => readFile(path.join(directory, 'managed-source-package.json')));
});

test('read requires exact installation and namespace binding and detects truncated or noncanonical bytes', async t => {
  const directory = await operationDirectory(t, 'binding');
  await writeManagedSourcePackage({ directory, ...makePackageInputs() });
  assert.throws(() => readManagedSourcePackage({ directory, installationId: randomUUID(), namespaceDigest }));
  assert.throws(() => readManagedSourcePackage({ directory, installationId, namespaceDigest: sha('other namespace') }));
  const file = path.join(directory, 'managed-source-package.json');
  const bytes = await readFile(file);
  await writeFile(file, bytes.subarray(0, bytes.length - 2));
  assert.throws(() => readManagedSourcePackage({ directory, installationId, namespaceDigest }));
});

test('rejects oversized package files', async t => {
  const oversized = await operationDirectory(t, 'oversized');
  await writeFile(path.join(oversized, 'managed-source-package.json'), Buffer.alloc(2 * 1024 * 1024 + 1, 0x20));
  assert.throws(() => readManagedSourcePackage({ directory: oversized, installationId, namespaceDigest }));


});

test('strict review validation rejects accessors, hidden values, cycles, and prototype keys without invoking them', () => {
  const accessor = makeReview();
  let invoked = false;
  Object.defineProperty(accessor.stable, 'head', { enumerable: true, get() { invoked = true; return oid(1); } });
  assert.throws(() => validateManagedCleanSourceReview(accessor));
  assert.equal(invoked, false);

  const hidden = makeReview();
  Object.defineProperty(hidden, 'ignored', { value: true, enumerable: false });
  assert.throws(() => validateManagedCleanSourceReview(hidden));

  const cycle = makeReview();
  cycle.stable.unexpected = cycle;
  assert.throws(() => validateManagedCleanSourceReview(cycle));

  const prototypeKey = JSON.parse('{"format":1,"mode":"managed_clean_source_review_v1","__proto__":{},"stable":{},"sandbox":{},"history":[],"deliveryPaths":[],"expectedMergedTree":"x","preservationDigest":"x","blockers":[],"reviewDigest":"x"}');
  assert.throws(() => validateManagedCleanSourceReview(prototypeKey));
});

test('recorded executor requires a complete adapter and trusted executable', async t => {
  const adapterModule = await import('../../launcher/managed-clean-source-adapter.mjs');
  if (typeof adapterModule.createManagedCleanSourceAdapter !== 'function') {
    t.skip('shared restricted adapter factory is still being implemented');
    return;
  }
  const directory = await operationDirectory(t, 'executor');
  await writeManagedSourcePackage({ directory, ...makePackageInputs() });
  await assert.rejects(() => executeRecordedManagedSourceMerge({ directory, installationId, namespaceDigest, gitExecutable: 'not-an-absolute-executable' }), /absolute|executable|Git/i);
  assert.equal((await readManagedSourcePackage({ directory, installationId, namespaceDigest })).mode, 'managed_source_package_v1');
});
test('executor requires a successful fresh fsync before creating any transaction journal', async t => {
  const directory = await operationDirectory(t, 'fsync-required');
  await writeManagedSourcePackage({ directory, ...makePackageInputs() });
  const fs = createRequire(import.meta.url)('node:fs');
  const original = fs.fsyncSync;
  fs.fsyncSync = () => { const error = new Error('synthetic fsync unsupported'); error.code = 'EIO'; throw error; };
  syncBuiltinESMExports();
  try {
    await assert.rejects(() => executeRecordedManagedSourceMerge({ directory, installationId, namespaceDigest, gitExecutable: 'not-an-absolute-executable' }), /fsync unsupported/i);
  } finally {
    fs.fsyncSync = original;
    syncBuiltinESMExports();
  }
  const names = await (await import('node:fs/promises')).readdir(directory);
  assert.deepEqual(names, ['managed-source-package.json'], 'durability refusal must precede journal claim and adapter/Git work');
});
test('rejects lexically unnormalized and case-aliased package directories', async t => {
  const directory = await operationDirectory(t, 'directory-identity');
  const inputs = makePackageInputs();
  await writeManagedSourcePackage({ directory, ...inputs });
  const lexicalAlias = `${directory}${path.sep}unused${path.sep}..`;
  assert.throws(() => readManagedSourcePackage({ directory: lexicalAlias, installationId, namespaceDigest }), /exact normalized/i);

  if (process.platform === 'win32') {
    const drive = path.parse(directory).root;
    const differentlyCasedDrive = `${drive[0] === drive[0].toLowerCase() ? drive[0].toUpperCase() : drive[0].toLowerCase()}${drive.slice(1)}${directory.slice(drive.length)}`;
    assert.throws(() => readManagedSourcePackage({ directory: differentlyCasedDrive, installationId, namespaceDigest }), /exact normalized|aliased|noncanonical/i);
  }
});