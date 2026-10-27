import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  managedNamespacePath,
  prepareManagedNamespace,
  readManagedNamespace,
  managedNamespaceDigest,
} from '../../launcher/managed-namespace.mjs';

const sandboxRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const evidenceRoot = path.join(sandboxRoot, 'artifacts/fixed-control-e1-20261024/managed-preparation/namespace');
const uuid = '2f1d8d4e-9d2a-4f90-8e11-01ed6b1a7c91';

async function fixture(t) {
  await mkdir(evidenceRoot, { recursive: true });
  const root = await mkdtemp(path.join(evidenceRoot, 'test-'));
  t.diagnostic(`owned fixture: ${root}`);
  const localAppData = path.join(root, 'LocalAppData');
  await mkdir(localAppData, { recursive: true });
  const stableSourceRoot = path.join(root, 'source', 'stable');
  const sandboxSourceRoot = path.join(stableSourceRoot, '.worktrees', 'sandbox');
  await mkdir(sandboxSourceRoot, { recursive: true });
  const dependencies = {
    platform: 'win32',
    assertLocalAppDataAnchor: async (value) => { assert.equal(path.resolve(value), path.resolve(localAppData)); },
    probeVolume: async () => ({ filesystem: 'NTFS', driveType: 'Fixed', local: true }),
    probeAcl: async () => ({ private: true, ownerSid: 'S-1-5-21-test', currentSid: 'S-1-5-21-test' }),
    assertNoReparse: async () => {},
    secureRoot: async () => {},
  };
  return { root, localAppData, stableSourceRoot, sandboxSourceRoot, dependencies };
}

test('managedNamespacePath is a deterministic absolute UUID-scoped path', () => {
  const got = managedNamespacePath('C:\\Users\\alice\\AppData\\Local', uuid);
  assert.equal(got, path.win32.resolve('C:\\Users\\alice\\AppData\\Local\\my-agent\\managed', uuid));
  assert.throws(() => managedNamespacePath('relative', uuid), /absolute/i);
  assert.throws(() => managedNamespacePath('C:\\Users\\alice\\AppData\\Local', '../escape'), /UUID/i);
});

test('prepare creates only the fixed empty managed layout and immutable format-1 record', async (t) => {
  const f = await fixture(t);
  const record = await prepareManagedNamespace({ localAppData: f.localAppData, installationId: uuid, stableSourceRoot: f.stableSourceRoot, sandboxSourceRoot: f.sandboxSourceRoot }, f.dependencies);
  assert.deepEqual(Object.keys(record).sort(), [
    'data_policy', 'format', 'installation_id', 'mode', 'paths', 'root', 'source_roots', 'stage',
  ]);
  assert.equal(record.format, 1);
  assert.equal(record.mode, 'managed_installation_v1');
  assert.equal(record.stage, 'prepared_empty');
  assert.equal(record.data_policy, 'empty_no_migration');
  assert.deepEqual(record.source_roots, { stable: f.stableSourceRoot, sandbox: f.sandboxSourceRoot });
  assert.deepEqual(record.paths, {
    launcher: path.join(record.root, 'launcher'),
    state: path.join(record.root, 'state'),
    releases: path.join(record.root, 'releases'),
    host_state: path.join(record.root, 'host/state'),
    host_profile: path.join(record.root, 'host/profile'),
    host_workspace: path.join(record.root, 'host/workspace'),
    data: path.join(record.root, 'data/store'),
  });
  assert.equal(Object.isFrozen(record), true);
  assert.equal(Object.isFrozen(record.paths), true);
  assert.deepEqual(await readdir(record.paths.data), []);
  assert.deepEqual(await readManagedNamespace(record.root, { ...f.dependencies, localAppData: f.localAppData }), record);
  const secondId = '8f2e649a-588c-4fe2-a7d1-6d8ca76c0999';
  const second = await prepareManagedNamespace({ localAppData: f.localAppData, installationId: secondId, stableSourceRoot: f.stableSourceRoot, sandboxSourceRoot: f.sandboxSourceRoot }, f.dependencies);
  assert.equal(second.installation_id, secondId);
  const markerText = await readFile(path.join(record.root, 'state/managed-namespace.json'), 'utf8');
  assert.equal(JSON.stringify(JSON.parse(markerText)), markerText.trim());
});

test('preparation fails closed for unsafe UUID, source overlap, pre-existing namespace, non-NTFS, and permissive ACL', async (t) => {
  const f = await fixture(t);
  await assert.rejects(prepareManagedNamespace({ localAppData: f.localAppData, installationId: '../outside', stableSourceRoot: f.stableSourceRoot, sandboxSourceRoot: f.sandboxSourceRoot }, f.dependencies), /UUID/i);
  await assert.rejects(prepareManagedNamespace({ localAppData: f.localAppData, installationId: uuid, stableSourceRoot: f.stableSourceRoot, sandboxSourceRoot: f.localAppData }, f.dependencies), /overlap/i);
  const unsafeVolume = { ...f.dependencies, probeVolume: async () => ({ filesystem: 'exFAT', driveType: 'Fixed', local: true }) };
  await assert.rejects(prepareManagedNamespace({ localAppData: f.localAppData, installationId: uuid, stableSourceRoot: f.stableSourceRoot, sandboxSourceRoot: f.sandboxSourceRoot }, unsafeVolume), /NTFS/i);
  const unsafeAcl = { ...f.dependencies, probeAcl: async () => ({ private: false, ownerSid: 'S-1-1-0', currentSid: 'S-1-5-21-test' }) };
  await assert.rejects(prepareManagedNamespace({ localAppData: f.localAppData, installationId: uuid, stableSourceRoot: f.stableSourceRoot, sandboxSourceRoot: f.sandboxSourceRoot }, unsafeAcl), /ACL/i);
  const target = managedNamespacePath(f.localAppData, uuid);
  await mkdir(target, { recursive: true });
  await writeFile(path.join(target, 'unknown'), 'leave intact');
  await assert.rejects(prepareManagedNamespace({ localAppData: f.localAppData, installationId: uuid, stableSourceRoot: f.stableSourceRoot, sandboxSourceRoot: f.sandboxSourceRoot }, f.dependencies), /exist|unknown/i);
  assert.equal(await readFile(path.join(target, 'unknown'), 'utf8'), 'leave intact');
});

test('read rejects a non-anchored root before invoking probes', async (t) => {
  const f = await fixture(t);
  let probes = 0;
  const dependencies = {
    ...f.dependencies,
    localAppData: f.localAppData,
    probeVolume: async () => { probes += 1; throw new Error('must not probe'); },
    probeAcl: async () => { probes += 1; throw new Error('must not probe'); },
    assertNoReparse: async () => { probes += 1; throw new Error('must not inspect'); },
  };
  await assert.rejects(readManagedNamespace(path.join(f.root, uuid), dependencies), /LOCALAPPDATA anchor/i);
  assert.equal(probes, 0);
});
test('read rejects altered marker and nonempty data without removing evidence', async (t) => {
  const f = await fixture(t);
  const record = await prepareManagedNamespace({ localAppData: f.localAppData, installationId: uuid, stableSourceRoot: f.stableSourceRoot, sandboxSourceRoot: f.sandboxSourceRoot }, f.dependencies);
  const wrongAnchor = path.join(f.root, 'other-local-app-data');
  await mkdir(wrongAnchor);
  await assert.rejects(readManagedNamespace(record.root, { ...f.dependencies, localAppData: wrongAnchor }), /LOCALAPPDATA/i);
  await writeFile(path.join(record.paths.data, 'unexpected'), 'preserve');
  await assert.rejects(readManagedNamespace(record.root, { ...f.dependencies, localAppData: f.localAppData }), /empty|unknown/i);
  assert.equal(await readFile(path.join(record.paths.data, 'unexpected'), 'utf8'), 'preserve');
});












