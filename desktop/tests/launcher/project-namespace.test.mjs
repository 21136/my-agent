import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  projectNamespacePath,
  prepareProjectNamespace,
  readProjectNamespace,
  projectNamespaceDigest,
} from '../../launcher/project-namespace.mjs';

const sandboxRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const evidenceRoot = path.join(sandboxRoot, 'artifacts/project-local-storage-20261024/project-namespace-tests');
const installationId = '2f1d8d4e-9d2a-4f90-8e11-01ed6b1a7c91';

async function fixture(t) {
  const root = path.join(evidenceRoot, `run-${randomUUID()}`);
  const stableSourceRoot = path.join(root, 'source');
  const sandboxSourceRoot = path.join(stableSourceRoot, '.worktrees', 'sandbox');
  await mkdir(sandboxSourceRoot, { recursive: true });
  t.diagnostic(`retained evidence fixture: ${root}`);
  const dependencies = {
    platform: 'win32',
    probeVolume: async () => ({ filesystem: 'exFAT', driveType: 'Fixed', local: true }),
    assertNoReparse: async () => {},
  };
  return { root, stableSourceRoot, sandboxSourceRoot, dependencies };
}

const inputFor = (f, id = installationId) => ({ installationId: id, stableSourceRoot: f.stableSourceRoot, sandboxSourceRoot: f.sandboxSourceRoot });

test('project namespace uses the fixed sandbox anchor and prepares the canonical empty project-local record', async (t) => {
  const f = await fixture(t);
  const expectedRoot = path.join(f.sandboxSourceRoot, '.managed-runtime', installationId);
  assert.equal(projectNamespacePath(f.sandboxSourceRoot, installationId), expectedRoot);
  const record = await prepareProjectNamespace(inputFor(f), f.dependencies);
  assert.equal(record.format, 1);
  assert.equal(record.mode, 'managed_project_local_v1');
  assert.equal(record.stage, 'prepared_empty');
  assert.equal(record.data_policy, 'empty_no_migration');
  assert.equal(record.security_policy, 'logical_separation_only');
  assert.equal(record.credentials_policy, 'forbidden');
  assert.equal(record.root, expectedRoot);
  assert.deepEqual(record.source_roots, { stable: f.stableSourceRoot, sandbox: f.sandboxSourceRoot });
  assert.deepEqual(Object.keys(record.paths).sort(), ['data', 'host_profile', 'host_state', 'host_workspace', 'launcher', 'releases', 'state']);
  assert.equal(projectNamespaceDigest(record), projectNamespaceDigest({ ...record }));
  assert.deepEqual(await readProjectNamespace(inputFor(f), f.dependencies), record);
  const markerText = await readFile(path.join(record.paths.state, 'managed-namespace.json'), 'utf8');
  assert.deepEqual(JSON.parse(markerText), record);
  assert.equal(markerText.endsWith(`}${String.fromCharCode(10)}`), true);
});

test('project namespace preparation requires distinct roots and refuses reuse or populated runtime', async (t) => {
  const f = await fixture(t);
  await assert.rejects(prepareProjectNamespace({ ...inputFor(f), stableSourceRoot: f.sandboxSourceRoot }, f.dependencies), /distinct|same/i);
  const record = await prepareProjectNamespace(inputFor(f), f.dependencies);
  await assert.rejects(prepareProjectNamespace(inputFor(f), f.dependencies), /exist|reuse/i);
  await writeFile(path.join(record.paths.data, 'unexpected'), 'preserve');
  await assert.rejects(readProjectNamespace(inputFor(f), f.dependencies), /empty|populated|unknown/i);
  assert.equal(await readFile(path.join(record.paths.data, 'unexpected'), 'utf8'), 'preserve');
});

test('project namespace read binds trusted source roots before reading a marker', async (t) => {
  const f = await fixture(t);
  const record = await prepareProjectNamespace(inputFor(f), f.dependencies);
  const otherSandbox = path.join(f.root, 'other-sandbox');
  await mkdir(otherSandbox);
  const probedPaths = [];
  const deps = { ...f.dependencies, assertNoReparse: async (value) => { probedPaths.push(path.resolve(value)); } };
  await assert.rejects(readProjectNamespace({ ...inputFor(f), sandboxSourceRoot: otherSandbox }, deps), /ENOENT|source|anchor|root/i);
  assert.equal(probedPaths.includes(path.resolve(record.root)), false);
  assert.equal(record.root, projectNamespacePath(f.sandboxSourceRoot, installationId));
});

test('project namespace rejects nested stable root and unsupported volume without creating a namespace', async (t) => {
  const f = await fixture(t);
  await assert.rejects(prepareProjectNamespace({ ...inputFor(f), stableSourceRoot: path.join(f.sandboxSourceRoot, 'nested-stable') }, f.dependencies), /contain|nest|overlap/i);
  const unsupported = { ...f.dependencies, probeVolume: async () => ({ filesystem: 'NTFS', driveType: 'Removable', local: false }) };
  await assert.rejects(prepareProjectNamespace(inputFor(f), unsupported), /fixed|local|volume|exfat|ntfs/i);
});

test('project namespace anchor rejects path traversal, prefix siblings, and casing aliases', async (t) => {
  const f = await fixture(t);
  assert.equal(projectNamespacePath(f.sandboxSourceRoot, installationId), path.join(f.sandboxSourceRoot, '.managed-runtime', installationId));
  assert.throws(() => projectNamespacePath(f.sandboxSourceRoot, '../escape'), /UUID/i);
  assert.notEqual(projectNamespacePath(`${f.sandboxSourceRoot}-sibling`, installationId), projectNamespacePath(f.sandboxSourceRoot, installationId));
  const differentlyCased = f.sandboxSourceRoot.replace('source', 'SOURCE');
  if (differentlyCased !== f.sandboxSourceRoot) {
    await assert.rejects(prepareProjectNamespace({ ...inputFor(f), sandboxSourceRoot: differentlyCased }, f.dependencies), /alias|canonical|source/i);
  }
});

test('project namespace rejects unknown fields and private-mode marker substitution', async (t) => {
  for (const mutation of ['unknown-field', 'private-mode']) {
    const f = await fixture(t);
    const record = await prepareProjectNamespace(inputFor(f), f.dependencies);
    const marker = path.join(record.paths.state, 'managed-namespace.json');
    const parsed = JSON.parse(await readFile(marker, 'utf8'));
    if (mutation === 'unknown-field') parsed.extra = true;
    else parsed.mode = 'managed_installation_v1';
    const ordered = (value) => Array.isArray(value) ? value.map(ordered) : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, ordered(value[key])])) : value;
    await writeFile(marker, `${JSON.stringify(ordered(parsed))}\n`);
    await assert.rejects(readProjectNamespace(inputFor(f), f.dependencies), /field|binding|mode|marker/i);
  }
});

test('project namespace read refuses populated host and release paths without deleting evidence', async (t) => {
  for (const rel of ['host/state/preserved.txt', 'releases/preserved.txt']) {
    const f = await fixture(t);
    const record = await prepareProjectNamespace(inputFor(f), f.dependencies);
    const occupied = path.join(record.root, rel);
    await writeFile(occupied, 'preserve');
    await assert.rejects(readProjectNamespace(inputFor(f), f.dependencies), /unknown|populated|unexpected|empty/i);
    assert.equal(await readFile(occupied, 'utf8'), 'preserve');
  }
});

test('project namespace rejects a symlinked parent of both source roots when the filesystem permits it', async (t) => {
  const f = await fixture(t);
  const link = path.join(f.root, 'source-parent-alias');
  try { await import('node:fs/promises').then(({ symlink }) => symlink(f.root, link, 'junction')); }
  catch (error) { t.skip(`junction unavailable; symlink-parent probe not executed: ${error.code ?? error.message}`); return; }
  await assert.rejects(prepareProjectNamespace({ ...inputFor(f), stableSourceRoot: path.join(link, 'source'), sandboxSourceRoot: path.join(link, 'source/.worktrees/sandbox') }, f.dependencies), /ordinary|link|alias|source|reparse/i);
});



test('project namespace rejects noncanonical and cross-source marker bindings', async (t) => {
  for (const mutation of ['noncanonical', 'cross-source']) {
    const f = await fixture(t);
    const record = await prepareProjectNamespace(inputFor(f), f.dependencies);
    const marker = path.join(record.paths.state, 'managed-namespace.json');
    const original = await readFile(marker, 'utf8');
    if (mutation === 'noncanonical') await writeFile(marker, ` ${original}`);
    else {
      const parsed = JSON.parse(original);
      parsed.source_roots.sandbox = f.stableSourceRoot;
      const ordered = (value) => Array.isArray(value) ? value.map(ordered) : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, ordered(value[key])])) : value;
      const canonicalText = (value) => JSON.stringify(ordered(value));
      await writeFile(marker, `${canonicalText(parsed)}\n`);
    }
    await assert.rejects(readProjectNamespace(inputFor(f), f.dependencies), /canonical|binding|source|marker/i);
    assert.notEqual(await readFile(marker, 'utf8'), '');
  }
});

test('project namespace rejects oversized markers and preserves their evidence', async (t) => {
  const f = await fixture(t);
  const record = await prepareProjectNamespace(inputFor(f), f.dependencies);
  const marker = path.join(record.paths.state, 'managed-namespace.json');
  const oversized = 'x'.repeat(16 * 1024 + 1);
  await writeFile(marker, oversized);
  await assert.rejects(readProjectNamespace(inputFor(f), f.dependencies), /bounded|size|marker/i);
  assert.equal((await readFile(marker, 'utf8')).length, oversized.length);
});

test('project namespace normalizes traversal components before recording trusted roots', async (t) => {
  const f = await fixture(t);
  const normalizedStable = path.join(f.root, 'source');
  const stableSourceRoot = path.join(f.root, 'unused', '..', 'source');
  const record = await prepareProjectNamespace({ ...inputFor(f), stableSourceRoot }, f.dependencies);
  assert.equal(record.source_roots.stable, normalizedStable);
});
