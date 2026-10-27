import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { chmodSync, existsSync, linkSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, renameSync, rmSync, unlinkSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { test } from 'node:test';
import { digest } from '../../launcher/managed-build-records.mjs';
import { acquireManagedToolchainReadGuard } from '../../launcher/managed-toolchain-read-guard.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, '../../../../../');
const sandboxRoot = path.resolve(here, '../../..');
const fixtureRoot = path.join(sandboxRoot, 'artifacts', 'managed-toolchain-read-guard-tests');
const pinnedPython = path.join(repoRoot, '.venv', 'Scripts', 'python.exe');
const FIXTURE_MARKER = '.managed-toolchain-read-guard-fixture';
const OWNERSHIP_TOKEN = 'managed-toolchain-read-guard-test-v1';
const sha = bytes => createHash('sha256').update(bytes).digest('hex');

function makeFixture(baseRoot = fixtureRoot) {
  mkdirSync(baseRoot, { recursive: true });
  const parent = mkdtempSync(path.join(baseRoot, 'run-'));
  const root = path.join(parent, 'fixture', '.worktrees', 'sandbox', 'desktop');
  const packageDir = path.join(root, 'node_modules', 'fixture-native');
  mkdirSync(packageDir, { recursive: true });
  writeFileSync(path.join(parent, FIXTURE_MARKER), OWNERSHIP_TOKEN, { flag: 'wx' });
  const entries = [
    ['node_modules/fixture-native/native-helper.exe', Buffer.from('inert executable-shaped test data\n')],
    ['node_modules/fixture-native/native-addon.node', Buffer.from('inert native-module-shaped test data\n')],
    ['node_modules/fixture-native/data.bin', Buffer.from('ordinary pinned fixture bytes\n')],
    ['node_modules/fixture-native/guard-probe.mjs', Buffer.from('export const value = "read-load-ok";\n')],
  ].map(([rel, bytes]) => {
    const target = path.join(root, ...rel.split('/'));
    writeFileSync(target, bytes, { flag: 'wx' });
    chmodSync(target, 0o644);
    return { path: rel, mode: '100644', sha256: sha(bytes), byteLength: bytes.length };
  }).sort((a, b) => a.path.localeCompare(b.path));
  const packageJson = Buffer.from('{"name":"managed-toolchain-read-guard-fixture"}\n');
  const packageLock = Buffer.from('{"lockfileVersion":3,"name":"managed-toolchain-read-guard-fixture"}\n');
  writeFileSync(path.join(root, 'package.json'), packageJson, { flag: 'wx' });
  writeFileSync(path.join(root, 'package-lock.json'), packageLock, { flag: 'wx' });
  const body = {
    format: 1, mode: 'managed_postmerge_toolchain_v1', root,
    packageJsonSha256: sha(packageJson), packageLockSha256: sha(packageLock),
    node: { executable: process.execPath, sha256: sha(readFileSync(process.execPath)), version: process.version },
    files: entries,
  };
  const toolchain = { ...body, toolchainDigest: digest(body) };
  return { parent, baseRoot, root, entries, toolchain };
}

function cleanupFixture(fixture) {
  const root = path.resolve(fixture.parent);
  assert.equal(path.dirname(root), path.resolve(fixture.baseRoot), 'cleanup must target one direct owned fixture directory');
  assert.match(path.basename(root), /^run-/);
  assert.equal(realpathSync.native(root), root);
  assert.equal(lstatSync(root).isSymbolicLink(), false);
  assert.equal(readFileSync(path.join(root, FIXTURE_MARKER), 'utf8'), OWNERSHIP_TOKEN);
  rmSync(root, { recursive: true });
}

function toolchainInput(fixture) {
  return { toolchain: fixture.toolchain };
}

test('Windows guard blocks writes and renames of native-shaped files until release, then permits them', { skip: process.platform !== 'win32' || !existsSync(pinnedPython) }, async t => {
  const fixture = makeFixture(); t.after(() => cleanupFixture(fixture));
  const guard = await acquireManagedToolchainReadGuard(toolchainInput(fixture));
  assert.equal(guard.receipt.mode, 'managed_toolchain_read_guard_v1');
  assert.equal(guard.receipt.toolchainDigest, fixture.toolchain.toolchainDigest);
  assert.equal(guard.receipt.fileCount, fixture.entries.length + 2);
  assert.equal(guard.receipt.runtimeFileCount, 1);
  assert.ok(guard.receipt.directoryCount > 0);
  for (const entry of fixture.entries) {
    const target = path.join(fixture.root, ...entry.path.split('/'));
    assert.throws(() => writeFileSync(target, 'blocked while guard held\n'), error => ['EACCES', 'EPERM', 'EBUSY'].includes(error.code));
    assert.throws(() => renameSync(target, target + '.renamed'), error => ['EACCES', 'EPERM', 'EBUSY'].includes(error.code));
    assert.throws(() => unlinkSync(target), error => ['EACCES', 'EPERM', 'EBUSY'].includes(error.code));
    assert.equal(readFileSync(target).length, entry.byteLength, 'reads remain available while guarded');
    assert.equal(sha(readFileSync(target)), entry.sha256);
  }
  const modulePath = path.join(fixture.root, 'node_modules', 'fixture-native', 'guard-probe.mjs');
  const loaded = await import(pathToFileURL(modulePath).href + '?guard-held=' + randomUUID());
  assert.equal(loaded.value, 'read-load-ok', 'module loading remains available while pinned files are guarded');
  await guard.release();
  for (const entry of fixture.entries) {
    const target = path.join(fixture.root, ...entry.path.split('/'));
    writeFileSync(target, 'write allowed after release\n');
    const renamed = target + '.renamed';
    renameSync(target, renamed);
    renameSync(renamed, target);
    assert.equal(readFileSync(target, 'utf8'), 'write allowed after release\n');
  }
  const deleteTarget = path.join(fixture.root, 'node_modules', 'fixture-native', 'native-addon.node');
  unlinkSync(deleteTarget);
  assert.equal(existsSync(deleteTarget), false, 'delete is permitted after release');
});

test('invalid traversal and digest records reject before a ready guard', { skip: process.platform !== 'win32' || !existsSync(pinnedPython) }, async t => {
  const fixture = makeFixture(); t.after(() => cleanupFixture(fixture));
  const original = fixture.toolchain;
  const traversalBody = { ...original, files: original.files.map((file, index) => index ? file : { ...file, path: 'node_modules/../outside.bin' }) };
  delete traversalBody.toolchainDigest;
  const traversal = { ...traversalBody, toolchainDigest: digest(traversalBody) };
  await assert.rejects(acquireManagedToolchainReadGuard({ ...toolchainInput(fixture), toolchain: traversal }), /toolchain|path|invalid/i);
  const badDigest = { ...original, toolchainDigest: '0'.repeat(64) };
  await assert.rejects(
    acquireManagedToolchainReadGuard({ ...toolchainInput(fixture), toolchain: badDigest }),
    error => error instanceof TypeError && error.message.startsWith('managed_toolchain_read_guard_invalid:'),
  );
  const wrongNodeBody = { ...original, node: { ...original.node, executable: path.join(fixture.root, ...fixture.entries[0].path.split('/')) } };
  delete wrongNodeBody.toolchainDigest;
  const wrongNode = { ...wrongNodeBody, toolchainDigest: digest(wrongNodeBody) };
  await assert.rejects(acquireManagedToolchainReadGuard({ ...toolchainInput(fixture), toolchain: wrongNode }), /Node|executable|toolchain|invalid/i);
  const target = path.join(fixture.root, ...fixture.entries[0].path.split('/'));
  writeFileSync(target, 'guard was never acquired\n');
  assert.equal(readFileSync(target, 'utf8'), 'guard was never acquired\n');
});

test('file digest mismatch rejects without retaining file locks', { skip: process.platform !== 'win32' || !existsSync(pinnedPython) }, async t => {
  const fixture = makeFixture(); t.after(() => cleanupFixture(fixture));
  const target = path.join(fixture.root, ...fixture.entries[0].path.split('/'));
  const before = readFileSync(target);
  writeFileSync(target, 'mismatching bytes\n');
  await assert.rejects(acquireManagedToolchainReadGuard(toolchainInput(fixture)), /guard|toolchain|verification|protocol/i);
  writeFileSync(target, before);
  writeFileSync(target, 'write allowed after failed acquisition\n');
  assert.equal(readFileSync(target, 'utf8'), 'write allowed after failed acquisition\n');
});

test('hard-linked toolchain files reject before readiness when the fixture volume supports hard links', { skip: process.platform !== 'win32' || !existsSync(pinnedPython) }, async t => {
  // exFAT does not support hard links; isolate this one negative case on the OS temp volume.
  const fixture = makeFixture(path.resolve(os.tmpdir())); t.after(() => cleanupFixture(fixture));
  const target = path.join(fixture.root, ...fixture.entries[0].path.split('/'));
  const alias = target + '.hardlink';
  try { linkSync(target, alias); }
  catch (error) {
    if (['EPERM', 'ENOTSUP', 'EOPNOTSUPP', 'ERROR_NOT_SUPPORTED', 'EISDIR'].includes(error.code)) return t.skip('fixture volume does not support hard links');
    throw error;
  }
  await assert.rejects(acquireManagedToolchainReadGuard(toolchainInput(fixture)), /guard|toolchain|verification|protocol/i);
  rmSync(alias);
  writeFileSync(target, 'write allowed after failed acquisition\n');
  assert.equal(readFileSync(target, 'utf8'), 'write allowed after failed acquisition\n');
});

test('non-Windows platforms fail closed without starting a guard', { skip: process.platform === 'win32' }, async () => {
  await assert.rejects(acquireManagedToolchainReadGuard({ toolchain: {} }), /Windows|unsupported|guard/i);
});
