import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { assertStoreBudget, canonical, digest, seal, writeCanonicalExclusive } from '../../launcher/managed-build-records.mjs';
import { sealManagedPostmergeRelease, verifyManagedPostmergeRelease } from '../../launcher/managed-postmerge-release.mjs';

// Synthetic inert input records exercise schema and tamper handling only. They do not model a real Git merge or Vite build.
const OPERATION_ID = '28c69b56-01d9-42f9-a6df-0bef5c205331';
const OID = '1234567890abcdef1234567890abcdef12345678';
const PLAN = 'a'.repeat(64);
const sha = bytes => createHash('sha256').update(bytes).digest('hex');

function put(root, rel, text) {
  const target = path.join(root, ...rel.split('/'));
  mkdirSync(path.dirname(target), { recursive: true });
  const bytes = Buffer.from(text);
  writeFileSync(target, bytes);
  chmodSync(target, 0o644);
  return { path: rel, mode: '100644', sha256: sha(bytes), byteLength: bytes.length };
}

function record(root, name, value) {
  writeCanonicalExclusive(path.join(root, name), value);
}

function replaceFixtureRecord(root, name, value) {
  writeFileSync(path.join(root, name), canonical(value) + '\n');
}

function syntheticOperation({ managedWrapper = false, managedPython = false, managedMetadataHelperSource = true, managedPreloadOutput = false, managedMetadataHelperOutput = false, extraElectronOutput = false } = {}) {
  const parent = mkdtempSync(path.join(os.tmpdir(), 'managed-postmerge-release-'));
  const sandboxRoot = path.join(parent, '.worktrees', 'sandbox');
  const desktopRoot = path.join(sandboxRoot, 'desktop');
  mkdirSync(desktopRoot, { recursive: true });
  const operationRoot = path.join(sandboxRoot, 'artifacts', 'managed-postmerge-builds', OPERATION_ID);
  const sourceRoot = path.join(operationRoot, 'source');
  const buildRoot = path.join(operationRoot, 'build');
  mkdirSync(sourceRoot, { recursive: true }); mkdirSync(buildRoot, { recursive: true }); mkdirSync(path.join(operationRoot, 'empty-env'));

  const payload = [
    ['desktop/package.json', '{"main":"dist-electron/main.js","name":"synthetic"}\n'],
    ['desktop/package-lock.json', '{"lockfileVersion":3,"name":"synthetic"}\n'],
    ['agent-core/server.py', '# inert test fixture\n'],
    ['agent-core/fixed_host_entry.py', '# inert test fixture\n'],
    ['evolve/_index.core.toml', 'format = 1\n'],
    ['desktop/launcher/a-runtime.mjs', 'export const lower = true;\n'],
    ['desktop/launcher/B-runtime.mjs', 'export const upper = true;\n'],
    ['desktop/launcher/runtime.mjs', 'export const inert = true;\n'],
    ['desktop/prompts/system.txt', 'synthetic prompt fixture\n'],
    ['agent-core/config/private.py', 'must not be copied\n'],
    ['agent-core/data/private.py', 'must not be copied\n'],
  ];
  if (managedWrapper) payload.push(
    ['desktop/electron/entry.ts', 'managed wrapper fixture\n'],
    ['desktop/electron/managed-host-entry.ts', 'managed host fixture\n'],
    ...(managedWrapper === 'missing-context' ? [] : [['desktop/electron/managed-host-context.ts', 'managed context fixture\n']]),
    ['desktop/electron/managed-preload.ts', 'managed preload fixture\n'],
    ...(managedMetadataHelperSource ? [['desktop/launcher/managed-clean-git-metadata.mjs', 'export async function assertManagedGitObjectStore() {}\n']] : []),
  );
  if (managedPython) payload.push(
    ['agent-core/managed_host_entry.py', '# fixture\n'],
    ['agent-core/managed_readonly.py', '# fixture\n'],
    ['agent-core/managed_store.py', '# fixture\n'],
    ['agent-core/managed_application.py', '# fixture\n'],
  );
  const sourceRows = payload.map(([rel, text]) => put(sourceRoot, rel, text)).sort((a, b) => a.path.localeCompare(b.path));
  put(desktopRoot, 'package.json', payload.find(([rel]) => rel === 'desktop/package.json')[1]);
  put(desktopRoot, 'package-lock.json', payload.find(([rel]) => rel === 'desktop/package-lock.json')[1]);
  const files = sourceRows.map(file => ({ ...file, blobOid: OID }));
  const snapshotBody = {
    format: 1, mode: 'managed_postmerge_source_snapshot_v1', status: 'source_merge_verified',
    planDigest: PLAN, receiptDigest: 'b'.repeat(64),
    roots: {
      stable: { worktreeRoot: path.resolve(sandboxRoot, '../..'), gitDir: path.join(sandboxRoot, '.git'), commonDir: path.join(sandboxRoot, '.git'), branch: 'main' },
      sandbox: { worktreeRoot: sandboxRoot, gitDir: path.join(sandboxRoot, '.git'), commonDir: path.join(sandboxRoot, '.git'), branch: 'experiment/sandbox' },
    },
    mergedHead: OID, mergedTree: '234567890abcdef1234567890abcdef123456789',
    files, excludedProtectedPaths: [], readyForLaunch: false, buildVerified: false,
  };
  const snapshot = { ...snapshotBody, snapshotDigest: digest(snapshotBody) };
  const intent = { format: 1, mode: 'managed_postmerge_build_intent_v1', operationId: OPERATION_ID, planDigest: PLAN, sourceSnapshotDigest: snapshot.snapshotDigest, stage: 'materializing', readyForLaunch: false };
  const materialized = seal({ format: 1, mode: 'managed_postmerge_materialized_v1', operationId: OPERATION_ID, planDigest: PLAN, snapshotDigest: snapshot.snapshotDigest, sourceInventoryDigest: digest(files), files, rawBytes: files.reduce((sum, row) => sum + row.byteLength, 0), readyForLaunch: false });
  record(operationRoot, 'intent.json', intent); record(operationRoot, 'source.json', snapshot); record(operationRoot, 'materialized.json', materialized);

  const outputs = [
    put(buildRoot, 'desktop/dist-electron/main.js', '// inert synthetic main output\n'),
    put(buildRoot, 'desktop/dist-electron/preload.js', '// inert synthetic preload output\n'),
    put(buildRoot, 'desktop/dist/index.html', '<!doctype html><title>synthetic</title>\n'),
    put(buildRoot, 'desktop/dist/assets/app.js', '// inert synthetic renderer output\n'),
  ];
  if (managedPreloadOutput) outputs.push(put(buildRoot, 'desktop/dist-electron/managed-preload.js', '// inert managed preload output\n'));
  if (managedMetadataHelperOutput) outputs.push(put(buildRoot, 'desktop/dist-electron/managed-clean-git-metadata.mjs', '// inert sealed metadata helper output\n'));
  if (extraElectronOutput) outputs.push(put(buildRoot, 'desktop/dist-electron/unexpected.js', '// must be refused\n'));
  outputs.sort((a, b) => a.path.localeCompare(b.path));
  const toolchainFile = put(desktopRoot, 'node_modules/vite/package.json', '{"name":"vite","version":"synthetic"}\n');
  const toolchainBody = {
    format: 1, mode: 'managed_postmerge_toolchain_v1', root: desktopRoot,
    packageJsonSha256: files.find(file => file.path === 'desktop/package.json').sha256,
    packageLockSha256: files.find(file => file.path === 'desktop/package-lock.json').sha256,
    node: { executable: path.join(desktopRoot, ...toolchainFile.path.split('/')), sha256: toolchainFile.sha256, version: process.version },
    files: [{ ...toolchainFile }],
  };
  const toolchain = { ...toolchainBody, toolchainDigest: digest(toolchainBody) };
  const buildIntent = {
    format: 1, mode: 'managed_postmerge_build_intent_v1', operationId: OPERATION_ID,
    planDigest: snapshot.planDigest, snapshotDigest: snapshot.snapshotDigest,
    materializedReceiptDigest: materialized.receiptDigest, toolchainDigest: toolchain.toolchainDigest,
    buildPolicy: 'configless_snapshot_v2', state: 'started', readyForLaunch: false,
  };
  record(operationRoot, 'build-intent.json', buildIntent);
  record(operationRoot, 'toolchain.json', toolchain);
  const build = seal({
    format: 1, mode: 'managed_postmerge_build_receipt_v1', operationId: OPERATION_ID,
    planDigest: snapshot.planDigest, snapshotDigest: snapshot.snapshotDigest,
    materializedReceiptDigest: materialized.receiptDigest, toolchainDigest: toolchain.toolchainDigest,
    buildPolicy: 'configless_snapshot_v2', outputs, inputsDigest: digest(snapshot.files), buildVerified: true, readyForLaunch: false,
  });
  record(operationRoot, 'build.json', build);
  return { parent, sandboxRoot, operationRoot, sourceRoot, buildRoot };
}

test('managed wrapper snapshot cannot be sealed without the complete managed Python source set', t => {
  const fixture = syntheticOperation({ managedWrapper: true }); t.after(() => rmSync(fixture.parent, { recursive: true, force: true }));
  assert.throws(
    () => sealManagedPostmergeRelease({ sandboxRoot: fixture.sandboxRoot, operationId: OPERATION_ID }),
    /managed bundle.*agent-core\/managed_host_entry\.py/i,
  );
  assert.equal(existsSync(path.join(fixture.operationRoot, 'release')), false);
});

test('managed wrapper snapshot cannot be sealed without managed-preload.js output', t => {
  const fixture = syntheticOperation({ managedWrapper: true, managedPython: true, managedMetadataHelperOutput: true });
  t.after(() => rmSync(fixture.parent, { recursive: true, force: true }));
  assert.throws(
    () => sealManagedPostmergeRelease({ sandboxRoot: fixture.sandboxRoot, operationId: OPERATION_ID }),
    /required managed Electron output missing: desktop\/dist-electron\/managed-preload\.js/i,
  );
  assert.equal(existsSync(path.join(fixture.operationRoot, 'release')), false);
});

test('managed wrapper snapshot requires its clean Git metadata helper source input', t => {
  const fixture = syntheticOperation({ managedWrapper: true, managedPython: true, managedMetadataHelperSource: false });
  t.after(() => rmSync(fixture.parent, { recursive: true, force: true }));
  assert.throws(
    () => sealManagedPostmergeRelease({ sandboxRoot: fixture.sandboxRoot, operationId: OPERATION_ID }),
    /managed bundle source missing: desktop\/launcher\/managed-clean-git-metadata\.mjs/i,
  );
  assert.equal(existsSync(path.join(fixture.operationRoot, 'release')), false);
});

test('managed wrapper snapshot cannot be sealed without the clean Git metadata helper output', t => {
  const fixture = syntheticOperation({ managedWrapper: true, managedPython: true, managedPreloadOutput: true });
  t.after(() => rmSync(fixture.parent, { recursive: true, force: true }));
  assert.throws(
    () => sealManagedPostmergeRelease({ sandboxRoot: fixture.sandboxRoot, operationId: OPERATION_ID }),
    /required managed Electron output missing: desktop\/dist-electron\/managed-clean-git-metadata\.mjs/i,
  );
  assert.equal(existsSync(path.join(fixture.operationRoot, 'release')), false);
});

test('managed wrapper release accepts and seals the exact managed runtime support outputs', t => {
  const fixture = syntheticOperation({ managedWrapper: true, managedPython: true, managedPreloadOutput: true, managedMetadataHelperOutput: true });
  t.after(() => rmSync(fixture.parent, { recursive: true, force: true }));
  const release = sealManagedPostmergeRelease({ sandboxRoot: fixture.sandboxRoot, operationId: OPERATION_ID });
  const manifest = JSON.parse(readFileSync(path.join(release.root, 'manifest.json'), 'utf8'));
  assert.ok(manifest.files.some(file => file.path === 'desktop/dist-electron/managed-preload.js'));
  assert.ok(manifest.files.some(file => file.path === 'desktop/dist-electron/managed-clean-git-metadata.mjs'));
});

test('managed wrapper release rejects any additional unlisted Electron output', t => {
  const fixture = syntheticOperation({ managedWrapper: true, managedPython: true, managedPreloadOutput: true, managedMetadataHelperOutput: true, extraElectronOutput: true });
  t.after(() => rmSync(fixture.parent, { recursive: true, force: true }));
  assert.throws(
    () => sealManagedPostmergeRelease({ sandboxRoot: fixture.sandboxRoot, operationId: OPERATION_ID }),
    /outside the exact runtime output allowlist/i,
  );
  assert.equal(existsSync(path.join(fixture.operationRoot, 'release')), false);
});

test('managed wrapper snapshot requires its context entry input', t => {
  const fixture = syntheticOperation({ managedWrapper: 'missing-context', managedPython: true });
  t.after(() => rmSync(fixture.parent, { recursive: true, force: true }));
  assert.throws(
    () => sealManagedPostmergeRelease({ sandboxRoot: fixture.sandboxRoot, operationId: OPERATION_ID }),
    /managed bundle source missing: desktop\/electron\/managed-host-context\.ts/i,
  );
  assert.equal(existsSync(path.join(fixture.operationRoot, 'release')), false);
});

test('synthetic inert fixture refuses E0 build mode and leaves the operation unmodified', async t => {
  const fixture = syntheticOperation(); t.after(() => rmSync(fixture.parent, { recursive: true, force: true }));
  const file = path.join(fixture.operationRoot, 'build.json');
  const build = JSON.parse(readFileSync(file, 'utf8'));
  const e0 = { ...build, mode: 'launcher_fixture_v1' };
  delete e0.receiptDigest;
  replaceFixtureRecord(fixture.operationRoot, 'build.json', seal(e0));
  assert.throws(
    () => verifyManagedPostmergeRelease({ sandboxRoot: fixture.sandboxRoot, operationId: OPERATION_ID, releaseId: 'c'.repeat(64) }),
    /build receipt schema|invalid/i,
  );
  assert.equal(existsSync(path.join(fixture.operationRoot, 'release')), false);
});

test('legacy unguarded policy remains budget-inspectable but cannot seal a new release',t=>{
  const fixture=syntheticOperation();t.after(()=>rmSync(fixture.parent,{recursive:true,force:true}));
  const build=JSON.parse(readFileSync(path.join(fixture.operationRoot,'build.json'),'utf8'));
  delete build.receiptDigest;build.buildPolicy='configless_v1';
  replaceFixtureRecord(fixture.operationRoot,'build.json',seal(build));
  const intent=JSON.parse(readFileSync(path.join(fixture.operationRoot,'build-intent.json'),'utf8'));
  intent.buildPolicy='configless_v1';replaceFixtureRecord(fixture.operationRoot,'build-intent.json',intent);
  assert.equal(assertStoreBudget(fixture).operationCount,1);
  assert.throws(()=>sealManagedPostmergeRelease({sandboxRoot:fixture.sandboxRoot,operationId:OPERATION_ID}),/build receipt schema or binding invalid/);
  assert.equal(existsSync(path.join(fixture.operationRoot,'release')),false);
  assert.equal(existsSync(path.join(path.dirname(fixture.operationRoot),'.coordinator.lock')),false);
});

test('synthetic inert release verification rejects payload tampering and extra files', async t => {
  const fixture = syntheticOperation(); t.after(() => rmSync(fixture.parent, { recursive: true, force: true }));
  const descriptor = sealManagedPostmergeRelease({ sandboxRoot: fixture.sandboxRoot, operationId: OPERATION_ID });
  assert.equal(descriptor.readyForLaunch, false, 'synthetic contract exercise only; no launch readiness');
  const main = path.join(descriptor.root, 'app', 'desktop', 'dist-electron', 'main.js');
  writeFileSync(main, 'tampered inert bytes\n');
  assert.throws(
    () => verifyManagedPostmergeRelease({ sandboxRoot: fixture.sandboxRoot, operationId: OPERATION_ID, releaseId: descriptor.releaseId }),
    /tree inventory digest mismatch|app contains|hashes differ|inventory differs/i,
  );
  writeFileSync(main, '// inert synthetic main output\n');
  writeFileSync(path.join(descriptor.root, 'app', 'unlisted.txt'), 'extra\n');
  assert.throws(
    () => verifyManagedPostmergeRelease({ sandboxRoot: fixture.sandboxRoot, operationId: OPERATION_ID, releaseId: descriptor.releaseId }),
    /tree inventory digest mismatch|extra files|unmanifested|inventory differs/i,
  );
});

test('synthetic inert release verification rejects a missing payload file and an unknown empty directory', async t => {
  const fixture = syntheticOperation(); t.after(() => rmSync(fixture.parent, { recursive: true, force: true }));
  const descriptor = sealManagedPostmergeRelease({ sandboxRoot: fixture.sandboxRoot, operationId: OPERATION_ID });
  const main = path.join(descriptor.root, 'app', 'desktop', 'dist-electron', 'main.js');
  const original = readFileSync(main);
  rmSync(main);
  assert.throws(
    () => verifyManagedPostmergeRelease({ sandboxRoot: fixture.sandboxRoot, operationId: OPERATION_ID, releaseId: descriptor.releaseId }),
    /inventory|missing|tree/i,
  );
  writeFileSync(main, original);
  const empty = path.join(descriptor.root, 'app', 'unexpected-empty-directory');
  mkdirSync(empty);
  assert.throws(
    () => verifyManagedPostmergeRelease({ sandboxRoot: fixture.sandboxRoot, operationId: OPERATION_ID, releaseId: descriptor.releaseId }),
    /inventory|directory|tree/i,
  );
});

test('synthetic mixed-case runtime paths use one normalized inventory ordering', async t => {
  const fixture = syntheticOperation(); t.after(() => rmSync(fixture.parent, { recursive: true, force: true }));
  const descriptor = sealManagedPostmergeRelease({ sandboxRoot: fixture.sandboxRoot, operationId: OPERATION_ID });
  const manifest = JSON.parse(readFileSync(path.join(descriptor.root, 'manifest.json'), 'utf8'));
  assert.ok(manifest.files.some(file => file.path === 'desktop/launcher/a-runtime.mjs'));
  assert.ok(manifest.files.some(file => file.path === 'desktop/launcher/B-runtime.mjs'));
  assert.equal(verifyManagedPostmergeRelease({ sandboxRoot: fixture.sandboxRoot, operationId: OPERATION_ID, releaseId: descriptor.releaseId }).releaseId, descriptor.releaseId);
});

test('repeat sealing refuses the existing release before claiming the build store', async t => {
  const fixture = syntheticOperation(); t.after(() => rmSync(fixture.parent, { recursive: true, force: true }));
  const first = sealManagedPostmergeRelease({ sandboxRoot: fixture.sandboxRoot, operationId: OPERATION_ID });
  assert.throws(
    () => sealManagedPostmergeRelease({ sandboxRoot: fixture.sandboxRoot, operationId: OPERATION_ID }),
    /existing release|release inventory|release target/i,
  );
  assert.equal(existsSync(path.join(fixture.sandboxRoot, 'artifacts', 'managed-postmerge-builds', '.coordinator.lock')), false);
  assert.equal(existsSync(path.join(first.root, 'sealed.json')), true);
});

test('unknown release siblings are refused before claiming the build store', async t => {
  const fixture = syntheticOperation(); t.after(() => rmSync(fixture.parent, { recursive: true, force: true }));
  const releaseBase = path.join(fixture.operationRoot, 'release');
  mkdirSync(path.join(releaseBase, 'unknown-partial'), { recursive: true });
  assert.throws(
    () => sealManagedPostmergeRelease({ sandboxRoot: fixture.sandboxRoot, operationId: OPERATION_ID }),
    /release.*inventory|unknown.*release|partial/i,
  );
  assert.equal(existsSync(path.join(fixture.sandboxRoot, 'artifacts', 'managed-postmerge-builds', '.coordinator.lock')), false);
});

test('readonly verification rejects a foreign store lock before inspecting release files', async t => {
  const fixture = syntheticOperation(); t.after(() => rmSync(fixture.parent, { recursive: true, force: true }));
  const descriptor = sealManagedPostmergeRelease({ sandboxRoot: fixture.sandboxRoot, operationId: OPERATION_ID });
  const lock = { format: 1, mode: 'managed_postmerge_build_lock_v1', operationId: OPERATION_ID, kind: 'seal', token: 'foreign-maintenance-token' };
  record(path.dirname(fixture.operationRoot), '.coordinator.lock', lock);
  assert.throws(
    () => verifyManagedPostmergeRelease({ sandboxRoot: fixture.sandboxRoot, operationId: OPERATION_ID, releaseId: descriptor.releaseId }),
    /maintenance|busy|lock/i,
  );
});

test('synthetic inert operation rejects unlisted build output before publishing release', async t => {
  const fixture = syntheticOperation(); t.after(() => rmSync(fixture.parent, { recursive: true, force: true }));
  const output = put(fixture.buildRoot, 'desktop/dist-electron/extra.js', '// not allowed\n');
  const file = path.join(fixture.operationRoot, 'build.json');
  const original = JSON.parse(readFileSync(file, 'utf8'));
  const next = { ...original, outputs: [...original.outputs, output].sort((a, b) => a.path.localeCompare(b.path)) };
  delete next.receiptDigest;
  replaceFixtureRecord(fixture.operationRoot, 'build.json', seal(next));
  assert.throws(
    () => sealManagedPostmergeRelease({ sandboxRoot: fixture.sandboxRoot, operationId: OPERATION_ID }),
    /build output inventory drift|output.*allowlist|build output scope|invalid/i,
  );
  assert.equal(existsSync(path.join(fixture.operationRoot, 'release', 'c'.repeat(64))), false);
});
