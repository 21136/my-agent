import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, mkdtemp, readFile, readdir, symlink, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { canonical, digest } from '../../launcher/managed-build-records.mjs';
import { prepareProjectNamespace } from '../../launcher/project-namespace.mjs';
import {
  prepareManagedRuntimeRegistration,
  readManagedRuntimeRegistration,
  captureManagedRuntimeInputs,
  readManagedRuntimeDirectoryEntries,
  validateManagedRuntimeRegistrationRecords,
} from '../../launcher/managed-runtime-registration.mjs';

const uuid = () => randomUUID();
const comparePath = (left, right) => left < right ? -1 : left > right ? 1 : 0;
const sandboxRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const testArtifactRoot = path.join(sandboxRoot, 'artifacts/managed-runtime-readonly-20261025/node-tests');

async function makeOwnedTemp(prefix) {
  await mkdir(testArtifactRoot, { recursive: true });
  return mkdtemp(path.join(testArtifactRoot, prefix));
}

async function runtimeFixture(t) {
  const parent = await makeOwnedTemp('runtimes-');
  t.diagnostic(`retained sandbox-owned synthetic runtimes: ${parent}`);
  const runtimes = {};
  for (const [name, entry, payload] of [
    ['node', 'node.exe', 'synthetic node bytes'],
    ['electron', 'electron.exe', 'synthetic electron bytes'],
    ['python', 'Scripts/python.exe', 'synthetic venv python bytes'],
    ['pythonBase', 'python.exe', 'synthetic base python bytes'],
  ]) {
    const root = path.join(parent, name);
    await mkdir(path.dirname(path.join(root, entry)), { recursive: true });
    await writeFile(path.join(root, entry), payload);
    runtimes[name] = { root, entry };
  }
  await writeFile(path.join(runtimes.electron.root, '_a'), 'underscore path');
  await writeFile(path.join(runtimes.electron.root, 'A'), 'uppercase path');
  await writeFile(path.join(runtimes.python.root, 'a'), 'lowercase path');
  return { parent, runtimes };
}

async function preparedContext(t) {
  const parent = await makeOwnedTemp('context-');
  t.diagnostic(`retained sandbox-owned synthetic context: ${parent}`);
  const stableRoot = path.join(parent, 'project');
  const sandboxRoot = path.join(stableRoot, '.worktrees', 'sandbox');
  await mkdir(sandboxRoot, { recursive: true });
  const context = {
    stableRoot, sandboxRoot, installationId: uuid(), operationId: uuid(), releaseId: 'a'.repeat(64),
  };
  const namespace = await prepareProjectNamespace({
    installationId: context.installationId,
    stableSourceRoot: stableRoot,
    sandboxSourceRoot: sandboxRoot,
  });
  return { context, namespace };
}

async function publishSyntheticMetadata(namespace, context, runtimes) {
  const identity = { installationId: context.installationId, releaseId: context.releaseId, bindingDigest: 'b'.repeat(64) };
  const runtimeRecords = {};
  for (const [name, input] of Object.entries(runtimes)) {
    const files = [];
    const directories = [];
    async function scan(directory, prefix = '') {
      const entries = await readdir(directory, { withFileTypes: true });
      entries.sort((left, right) => comparePath(left.name, right.name));
      for (const entry of entries) {
        const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
        const target = path.join(directory, entry.name);
        if (entry.isDirectory()) {
          directories.push(relative);
          await scan(target, relative);
        } else {
          const bytes = await readFile(target);
          files.push({ path: relative, sha256: createHash('sha256').update(bytes).digest('hex'), byteLength: bytes.length });
        }
      }
    }
    await scan(input.root);
    files.sort((a, b) => comparePath(a.path, b.path));
    directories.sort(comparePath);
    runtimeRecords[name] = {
      root: input.root,
      entry: input.entry,
      policy: name === 'node' ? 'single_executable_v1' : 'complete_tree_v1',
      files,
      directories,
    };
  }
  const body = {
    format: 1, mode: 'managed_runtime_registration_v1', ...identity,
    runtimes: runtimeRecords, readyForLaunch: false,
  };
  const registration = { ...body, runtimeDigest: digest(body) };
  const intentBody = {
    format: 1, mode: 'managed_runtime_intent_v1', ...identity,
    runtimeDigest: registration.runtimeDigest, state: 'registration_started', readyForLaunch: false,
  };
  const intent = { ...intentBody, intentDigest: digest(intentBody) };
  const receiptBody = {
    format: 1, mode: 'managed_runtime_receipt_v1', ...identity,
    runtimeDigest: registration.runtimeDigest,
    registrationDigest: digest(registration),
    readyForLaunch: false,
  };
  const receipt = { ...receiptBody, receiptDigest: digest(receiptBody) };
  await writeFile(path.join(namespace.paths.launcher, 'runtime-intent.json'), canonical(intent) + '\n');
  await writeFile(path.join(namespace.paths.launcher, 'runtime-registration.json'), canonical(registration) + '\n');
  await writeFile(path.join(namespace.paths.launcher, 'runtime-receipt.json'), canonical(receipt) + '\n');
  return { identity, registration };
}

test('rejects an input with an implicit runtime instead of falling back to process.execPath', async t => {
  const { runtimes } = await runtimeFixture(t);
  delete runtimes.node;

  await assert.rejects(
    prepareManagedRuntimeRegistration({ context: {}, runtimes }),
    /managed_runtime_registration_invalid:/,
  );
});

test('rejects a runtime entry outside its explicitly supplied root without publication', async t => {
  const { runtimes } = await runtimeFixture(t);
  runtimes.node.entry = '../node.exe';

  await assert.rejects(
    prepareManagedRuntimeRegistration({ context: {}, runtimes }),
    /managed_runtime_registration_invalid:/,
  );
});

test('Node single-executable capture ignores unrelated directories and their contents', async t => {
  const { runtimes } = await runtimeFixture(t);
  await mkdir(path.join(runtimes.node.root, 'unrelated-directory'));
  await writeFile(path.join(runtimes.node.root, 'unrelated-directory', 'must-not-be-inventoried.txt'), 'not the pinned Node executable');
  const records = captureManagedRuntimeInputs(runtimes);
  assert.deepEqual(records.node.files.map(file => file.path), ['node.exe']);
  assert.deepEqual(records.node.directories, []);
  assert.deepEqual(records.electron.files.map(file => file.path), ['A', '_a', 'electron.exe']);
  assert.deepEqual(records.python.files.map(file => file.path), ['Scripts/python.exe', 'a']);
  assert.deepEqual(records.python.directories, ['Scripts']);
});

test('Node single-executable capture ignores an unrelated junction without traversing it', async t => {
  const { parent, runtimes } = await runtimeFixture(t);
  const linkTarget = path.join(parent, 'must-not-be-traversed');
  await mkdir(linkTarget);
  await writeFile(path.join(linkTarget, 'synthetic-only.txt'), 'outside pinned Node entry');
  try { await symlink(linkTarget, path.join(runtimes.node.root, 'unrelated-junction'), 'junction'); }
  catch (error) {
    if (['EISDIR', 'ENOTSUP', 'EOPNOTSUPP', 'EPERM'].includes(error.code)) { t.skip('sandbox volume does not support Windows directory reparse points'); return; }
    throw error;
  }
  const records = captureManagedRuntimeInputs(runtimes);
  assert.deepEqual(records.node.files.map(file => file.path), ['node.exe']);
  assert.deepEqual(records.node.directories, []);
});

test('complete-tree runtime capture still rejects an unrelated link', async t => {
  const { parent, runtimes } = await runtimeFixture(t);
  const linkTarget = path.join(parent, 'electron-link-target');
  await mkdir(linkTarget);
  await writeFile(path.join(linkTarget, 'synthetic-only.txt'), 'complete trees reject links');
  try { await symlink(linkTarget, path.join(runtimes.electron.root, 'unrelated-junction'), 'junction'); }
  catch (error) {
    if (['EISDIR', 'ENOTSUP', 'EOPNOTSUPP', 'EPERM'].includes(error.code)) { t.skip('sandbox volume does not support Windows directory reparse points'); return; }
    throw error;
  }

  assert.throws(() => captureManagedRuntimeInputs(runtimes), /managed_runtime_registration_invalid:.*link or reparse point/);
});

test('bounded directory reader stops before retaining entries over its limit', async t => {
  const root = await makeOwnedTemp('bounded-reader-');
  t.diagnostic(`retained sandbox-owned bounded-reader fixture: ${root}`);
  await writeFile(path.join(root, 'z-last'), 'z');
  await writeFile(path.join(root, 'A-first'), 'A');
  await writeFile(path.join(root, '_middle'), '_');

  assert.throws(() => readManagedRuntimeDirectoryEntries(root, 2), /managed_runtime_registration_invalid:.*entry count/);
  assert.deepEqual(readManagedRuntimeDirectoryEntries(root, 3), ['A-first', '_middle', 'z-last']);
});

test('Windows Python venv entry requires the exact canonical Scripts casing before publication', async t => {
  if (process.platform !== 'win32') { t.skip('Windows fixed runtime entry contract'); return; }
  const { runtimes } = await runtimeFixture(t);
  runtimes.python.entry = 'scripts/python.exe';

  await assert.rejects(
    prepareManagedRuntimeRegistration({ context: {}, runtimes }),
    /managed_runtime_registration_invalid: python entry must be Scripts\/python\.exe/,
  );
});

test('valid synthetic runtime paths cannot bypass the frozen production source proof', async t => {
  const { context, namespace } = await preparedContext(t);
  const { runtimes } = await runtimeFixture(t);
  // A prepared-empty namespace has no verified source/build binding; the API must fail closed
  // before publication. This asserts that a plausible runtime fixture cannot bypass frozen source proof.
  await assert.rejects(
    prepareManagedRuntimeRegistration({ context, runtimes }),
    /managed_runtime_registration_invalid:/,
  );
  assert.deepEqual(await readdir(namespace.paths.launcher), []);
  assert.deepEqual(await readdir(namespace.paths.data), []);
  assert.deepEqual(await readdir(namespace.paths.host_workspace), []);
  assert.equal(await readFile(path.join(namespace.paths.state, 'managed-namespace.json'), 'utf8').then(JSON.parse).then(x => x.stage), 'prepared_empty');
});

test('runtime read refuses a partial launcher record set without recovery or overwrite', async t => {
  const { context, namespace } = await preparedContext(t);
  await writeFile(path.join(namespace.paths.launcher, 'runtime-intent.json'), '{}\n');
  await assert.rejects(readManagedRuntimeRegistration(context), /managed_runtime_registration_invalid:/);
  assert.deepEqual(await readdir(namespace.paths.launcher), ['runtime-intent.json']);
});

test('metadata reader accepts the exact complete synthetic three-record set and rejects digest drift', async t => {
  const parent = await makeOwnedTemp('metadata-');
  t.diagnostic(`retained sandbox-owned synthetic record directory: ${parent}`);
  const namespace = { paths: { launcher: path.join(parent, 'launcher') } };
  await mkdir(namespace.paths.launcher);
  const context = { installationId: uuid(), releaseId: 'a'.repeat(64) };
  const { runtimes } = await runtimeFixture(t);
  await mkdir(path.join(runtimes.electron.root, 'empty-directory'));
  const { identity, registration } = await publishSyntheticMetadata(namespace, context, runtimes);
  const accepted = validateManagedRuntimeRegistrationRecords(namespace.paths.launcher, identity);
  assert.deepEqual(accepted, registration);
  assert.deepEqual(registration.runtimes.electron.files.map(file => file.path), ['A', '_a', 'electron.exe']);
  assert.deepEqual(registration.runtimes.python.files.map(file => file.path), ['Scripts/python.exe', 'a']);
  assert.notEqual(
    createHash('sha256').update(canonical(registration) + '\n').digest('hex'),
    (JSON.parse(await readFile(path.join(namespace.paths.launcher, 'runtime-receipt.json'), 'utf8'))).registrationDigest,
    'registrationDigest excludes the canonical file newline',
  );

  const receiptPath = path.join(namespace.paths.launcher, 'runtime-receipt.json');
  const receipt = JSON.parse(await readFile(receiptPath, 'utf8'));
  receipt.runtimeDigest = 'c'.repeat(64);
  await writeFile(receiptPath, canonical(receipt) + '\n');
  assert.throws(() => validateManagedRuntimeRegistrationRecords(namespace.paths.launcher, identity), /managed_runtime_registration_invalid:/);
});

