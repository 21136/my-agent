import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { existsSync } from 'node:fs';
import { mkdir, readFile, readdir, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { prepareProjectNamespace, projectNamespacePath } from '../../launcher/project-namespace.mjs';
import {
  prepareManagedHostProfileIntentSync,
  prepareManagedProductionContext,
  readManagedProductionContext,
  readManagedOperationalProductionContext,
} from '../../launcher/managed-production-context.mjs';
import { withManagedPhysicalRuntimeReads } from '../../electron/managed-host-context.ts';

const sandboxCheckout = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const evidenceRoot = path.join(sandboxCheckout, 'artifacts/managed-production-context-20261025/tests');

async function fixture(t, installationId = randomUUID()) {
  const root = path.join(evidenceRoot, `run-${randomUUID()}`);
  const stableRoot = path.join(root, 'project');
  const sandboxRoot = path.join(stableRoot, '.worktrees', 'sandbox');
  await mkdir(sandboxRoot, { recursive: true });
  const input = {
    stableRoot,
    sandboxRoot,
    installationId,
    operationId: randomUUID(),
    releaseId: 'a'.repeat(64),
  };
  const namespace = await prepareProjectNamespace({
    installationId: input.installationId,
    stableSourceRoot: stableRoot,
    sandboxSourceRoot: sandboxRoot,
  });
  t.diagnostic(`retained owned fixture: ${root}`);
  return { input, namespace };
}

test('production preparation refuses without a verified materialized V2 operation and writes no context intent', async (t) => {
  const { input, namespace } = await fixture(t, '85e88699-32d2-4bdb-a230-3f9e13275f6f');
  await assert.rejects(prepareManagedProductionContext(input), (error) => {
    assert.match(error.message, /managed_production_context_invalid:/);
    assert.doesNotMatch(error.message, /(?:installationId|operationId) must be/);
    return true;
  });
  assert.deepEqual(await readdir(namespace.paths.state), ['managed-namespace.json']);
  assert.deepEqual(await readdir(namespace.paths.releases), []);
  assert.deepEqual(await readdir(namespace.paths.data), []);
  assert.equal(JSON.parse(await readFile(path.join(namespace.paths.state, 'managed-namespace.json'), 'utf8')).stage, 'prepared_empty');
});

test('production context read refuses a prepared-only namespace without mutation', async (t) => {
  const { input, namespace } = await fixture(t);
  await assert.rejects(readManagedProductionContext(input), (error) => {
    assert.match(error.message, /managed_production_context_invalid:/);
    assert.doesNotMatch(error.message, /(?:installationId|operationId) must be/);
    return true;
  });
  assert.deepEqual(await readdir(namespace.paths.state), ['managed-namespace.json']);
  assert.deepEqual(await readdir(namespace.paths.releases), []);
  assert.deepEqual(await readdir(namespace.paths.data), []);
});

test('operational production reader refuses incomplete identity and leaves the owned namespace unchanged', async (t) => {
  const { input, namespace } = await fixture(t);
  await assert.rejects(readManagedOperationalProductionContext(input), /managed_production_context_invalid:/);
  assert.deepEqual(await readdir(namespace.paths.state), ['managed-namespace.json']);
  assert.deepEqual(await readdir(path.join(namespace.root, 'data')), ['store']);
});

test('synchronous Host profile preparation fails closed before publishing an intent for an unverified namespace', async (t) => {
  const { input, namespace } = await fixture(t);
  assert.throws(() => prepareManagedHostProfileIntentSync(input, {
    access: 'read_only', hostNonce: randomUUID(),
  }), /managed_production_context_invalid:/);
  assert.equal(existsSync(path.join(namespace.paths.host_state, 'managed-host-profile-intent.json')), false);
  assert.deepEqual(await readdir(namespace.paths.host_state), []);
});

test('managed physical runtime reads restore Electron noAsar after success and failure', () => {
  const original = process.noAsar;
  assert.equal(withManagedPhysicalRuntimeReads(() => process.noAsar), true);
  assert.equal(process.noAsar, original);
  assert.throws(() => withManagedPhysicalRuntimeReads(() => {
    assert.equal(process.noAsar, true);
    throw new Error('focused noAsar restore probe');
  }), /focused noAsar restore probe/);
  assert.equal(process.noAsar, original);
});

test('production context binds the runtime inventory helpers it invokes', async () => {
  const source = await readFile(new URL('../../launcher/managed-production-context.mjs', import.meta.url), 'utf8');
  assert.match(source, /import\s*\{[^}]*\bcaptureManagedRuntimeInputs\b[^}]*\}\s*from\s*['"]\.\/managed-runtime-registration\.mjs['"]/s);
  assert.match(source, /import\s*\{[^}]*\bvalidateManagedRuntimeRegistrationRecords\b[^}]*\}\s*from\s*['"]\.\/managed-runtime-registration\.mjs['"]/s);
  assert.match(source, /\bcaptureManagedRuntimeInputs\s*\(/);
  assert.match(source, /\bvalidateManagedRuntimeRegistrationRecords\s*\(/);
});

test('strict input validation rejects executable/path overrides before touching the namespace', async (t) => {
  const { input, namespace } = await fixture(t);
  await assert.rejects(prepareManagedProductionContext({ ...input, gitExecutable: 'git.exe' }), /managed_production_context_invalid:/);
  await assert.rejects(readManagedProductionContext({ ...input, toolchainRoot: path.join(input.sandboxRoot, 'desktop') }), /managed_production_context_invalid:/);
  assert.deepEqual(await readdir(namespace.paths.state), ['managed-namespace.json']);
  assert.equal((await stat(projectNamespacePath(input.sandboxRoot, input.installationId))).isDirectory(), true);
});
