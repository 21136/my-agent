import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { existsSync, lstatSync, mkdirSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  assertStoreBudget, canonical, claimBuildStore, digest, readMaterializedOperation,
  readStableBytes, regularFile, safeDirectory, safeRelative, seal, verifyTree, writeBytesExclusive,
  writeCanonicalExclusive,
} from './managed-build-records.mjs';

const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
const HASH = /^[a-f0-9]{64}$/;
const MAX_FILES = 10_000;
const MAX_BYTES = 64 * 1024 * 1024;
const MAX_CAPTURE_FILES = 20_000;
const MAX_TOOLCHAIN_BYTES = 448 * 1024 * 1024;
const CLUSTER = 128 * 1024;
const BUILD_POLICY = 'configless_snapshot_v2';
export { BUILD_POLICY };
const CHILD = fileURLToPath(new URL('../scripts/managed-build-child.mjs', import.meta.url));
const RECORDS = fileURLToPath(new URL('./managed-build-records.mjs', import.meta.url));
const TOOLCHAIN_GUARD = fileURLToPath(new URL('./managed-toolchain-read-guard.mjs', import.meta.url));
const TOOLCHAIN_GUARD_HELPER = fileURLToPath(new URL('../scripts/managed-toolchain-read-guard.py', import.meta.url));
const fail = message => { throw new TypeError(`managed_postmerge_build_invalid: ${message}`); };
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const within = (root, target) => { const rel = path.relative(root, target); return rel === '' || (rel !== '..' && !rel.startsWith(`..${path.sep}`) && !path.isAbsolute(rel)); };

function hashRegularFile(file, max = 64 * 1024 * 1024) {
  regularFile(file, max);
  return sha(readStableBytes(file, max));
}

function checkCanonicalRoot(root, label) {
  if (typeof root !== 'string' || !path.isAbsolute(root) || path.resolve(root) !== root) fail(`${label} must be a normalized absolute path`);
  safeDirectory(root);
}

function installedPackageRoot(toolchainRoot, packageKey, maxLockPath) {
  if (!safeRelative(packageKey) || !packageKey.startsWith('node_modules/')) fail('lock package path invalid');
  const target = path.resolve(toolchainRoot, ...packageKey.split('/'));
  if (!within(path.join(toolchainRoot, 'node_modules'), target)) fail('lock package escaped node_modules');
  safeDirectory(target);
  const manifestPath = path.join(target, 'package.json');
  const manifest = JSON.parse(readStableBytes(regularFile(manifestPath, 1024 * 1024), 1024 * 1024).toString('utf8'));
  if (manifest.version !== maxLockPath.version || typeof manifest.name !== 'string') fail(`installed manifest differs from lock: ${packageKey}`);
  return { target, manifest };
}

function captureInstalledToolchain(toolchainRoot) {
  const packageJsonPath = path.join(toolchainRoot, 'package.json');
  const packageLockPath = path.join(toolchainRoot, 'package-lock.json');
  const packageJsonBytes = readStableBytes(regularFile(packageJsonPath, 1024 * 1024), 1024 * 1024);
  const packageLockBytes = readStableBytes(regularFile(packageLockPath, 16 * 1024 * 1024), 16 * 1024 * 1024);
  const manifest = JSON.parse(packageJsonBytes.toString('utf8'));
  const lock = JSON.parse(packageLockBytes.toString('utf8'));
  if (!manifest || typeof manifest !== 'object' || !lock || typeof lock !== 'object' || !lock.packages || typeof lock.packages !== 'object' || !lock.packages['']) fail('npm package/lock schema invalid');
  const rootLock = lock.packages[''];
  for (const group of ['dependencies', 'devDependencies', 'optionalDependencies']) {
    const declared = manifest[group] || {};
    const locked = rootLock[group] || {};
    if (canonical(declared) !== canonical(locked)) fail(`package lock root ${group} differs from package.json`);
  }

  const packages = Object.entries(lock.packages).filter(([name]) => name.startsWith('node_modules/')).sort(([a], [b]) => a.localeCompare(b));
  if (packages.length > MAX_CAPTURE_FILES) fail('toolchain lock package count exceeded');
  const records = new Map();
  let total = 0, visited = 0;
  for (const [key, lockEntry] of packages) {
    if (!lockEntry || typeof lockEntry.version !== 'string' || lockEntry.link === true) fail(`unsupported lock package entry: ${key}`);
    const candidate = path.resolve(toolchainRoot, ...key.split('/'));
    if (!existsSync(candidate) && lockEntry.optional === true) continue;
    const { target } = installedPackageRoot(toolchainRoot, key, lockEntry);
  }

  // Bind every installed file that the resolver could reach, including extraneous packages.
  // Only npm's command shims and Electron's separately bound runtime binary tree are excluded.
  const nodeModulesRoot = path.join(toolchainRoot, 'node_modules');
  safeDirectory(nodeModulesRoot);
  const walk = (directory, relative = 'node_modules', depth = 0) => {
    if (depth > 64) fail('toolchain directory depth exceeded');
    for (const name of readdirSync(directory).sort()) {
      visited++;
      if (visited > 60_000) fail('toolchain directory entry count exceeded');
      if (name === '.bin') continue;
      const rel = `${relative}/${name}`;
      if (!safeRelative(rel)) fail('toolchain path is not portable');
      if (/(?:^|\/)electron\/dist(?:\/|$)/i.test(rel)) continue;
      const full = path.join(directory, name), stat = lstatSync(full);
      if (stat.isSymbolicLink() || (process.platform === 'win32' && (stat.attributes & 0x400))) fail(`toolchain symlink/reparse point: ${rel}`);
      if (stat.isDirectory()) { walk(full, rel, depth + 1); continue; }
      if (!stat.isFile() || stat.nlink !== 1) fail(`toolchain non-regular or hard-linked file: ${rel}`);
      if (records.size >= MAX_CAPTURE_FILES) fail('toolchain inventory file count exceeded');
      const bytes = readStableBytes(regularFile(full, MAX_TOOLCHAIN_BYTES), MAX_TOOLCHAIN_BYTES);
      total += bytes.length;
      if (total > MAX_TOOLCHAIN_BYTES) fail('toolchain byte inventory bound exceeded during scan');
      records.set(rel, { path: rel, mode: (stat.mode & 0o111) ? '100755' : '100644', sha256: sha(bytes), byteLength: bytes.length });
    }
  };
  walk(nodeModulesRoot);
  const files = [...records.values()].sort((a, b) => a.path.localeCompare(b.path));
  return { files, packageJsonSha256: sha(packageJsonBytes), packageLockSha256: sha(packageLockBytes) };
}

function verifySourcePackageBinding(operation, toolchain) {
  for (const [sourcePath, digestField] of [['desktop/package.json', 'packageJsonSha256'], ['desktop/package-lock.json', 'packageLockSha256']]) {
    const sourceFile = operation.snapshot.files.find(entry => entry.path === sourcePath);
    if (!sourceFile || sourceFile.sha256 !== toolchain[digestField]) fail(`materialized source ${sourcePath} differs byte-for-byte from pinned toolchain`);
    const bytes = readStableBytes(regularFile(path.join(operation.sourceRoot, ...sourcePath.split('/')), 16 * 1024 * 1024), 16 * 1024 * 1024);
    if (sha(bytes) !== toolchain[digestField]) fail(`materialized source ${sourcePath} changed after verification`);
  }
}

function cleanChildEnvironment() {
  const env = {};
  for (const name of ['SystemRoot', 'WINDIR', 'TEMP', 'TMP']) if (process.env[name]) env[name] = process.env[name];
  env.PATH = path.dirname(process.execPath);
  env.LANG = 'C'; env.LC_ALL = 'C'; env.TZ = 'UTC';
  return env;
}

function validateChildOutputs(stdout, buildRoot, { managedEntry = false } = {}) {
  const text = stdout.toString('utf8');
  if (!Buffer.from(text).equals(stdout) || !text.endsWith('\n')) fail('child output framing invalid');
  let result;
  try { result = JSON.parse(text.slice(0, -1)); } catch { fail('child output JSON invalid'); }
  if (!result || result.status !== 'built' || !Array.isArray(result.outputs) || !result.outputs.length) fail('child did not return a build');
  const outputs = [], seen = new Set(); let total = 0;
  const electronOutputs = managedEntry
    ? new Set(['desktop/dist-electron/main.js', 'desktop/dist-electron/preload.js', 'desktop/dist-electron/managed-preload.js', 'desktop/dist-electron/managed-clean-git-metadata.mjs'])
    : new Set(['desktop/dist-electron/main.js', 'desktop/dist-electron/preload.js']);
  for (const item of result.outputs) {
    if (!item || !safeRelative(item.path) || !['desktop/dist-electron/', 'desktop/dist/'].some(prefix => item.path.startsWith(prefix)) || item.mode !== '100644'
        || !Number.isSafeInteger(item.byteLength) || item.byteLength < 0 || typeof item.base64 !== 'string' || seen.has(item.path)) fail('child output inventory invalid');
    if (item.path.startsWith('desktop/dist-electron/') && !electronOutputs.has(item.path)) fail('child declared an Electron output outside the exact build allowlist');
    const bytes = Buffer.from(item.base64, 'base64');
    if (bytes.toString('base64') !== item.base64 || bytes.length !== item.byteLength) fail('child output bytes invalid');
    seen.add(item.path); total += bytes.length;
    if (total > MAX_BYTES || seen.size > MAX_FILES) fail('build output budget exceeded');
    outputs.push({ path: item.path, mode: item.mode, sha256: sha(bytes), byteLength: bytes.length, bytes });
  }
  const required = ['desktop/dist-electron/main.js', 'desktop/dist-electron/preload.js', 'desktop/dist/index.html'];
  if (managedEntry) required.push('desktop/dist-electron/managed-preload.js', 'desktop/dist-electron/managed-clean-git-metadata.mjs');
  if (required.some(name => !seen.has(name))) fail('required output missing');
  // Refuse any unexpected file type/path before creating the build directory.
  for (const item of outputs) {
    const target = path.resolve(buildRoot, ...item.path.split('/'));
    if (!within(buildRoot, target)) fail('output escaped build root');
  }
  return outputs.sort((a, b) => a.path.localeCompare(b.path));
}
export { validateChildOutputs };

function assertNoBuildArtifacts(operationRoot) {
  for (const name of ['build', 'toolchain.json', 'build-intent.json', 'build.json', 'empty-env']) {
    if (existsSync(path.join(operationRoot, name))) fail(`existing or partial build artifact prevents reuse: ${name}`);
  }
}

/** Build only the verified materialized postmerge source with the existing, byte-bound sandbox toolchain. */
export async function buildManagedPostmerge({ sandboxRoot, operationId, toolchainRoot } = {}) {
  checkCanonicalRoot(sandboxRoot, 'sandboxRoot');
  if (typeof operationId !== 'string' || !UUID.test(operationId)) fail('operation UUID identity invalid');
  if (typeof toolchainRoot !== 'string' || !path.isAbsolute(toolchainRoot) || path.resolve(toolchainRoot) !== toolchainRoot
      || path.basename(toolchainRoot) !== 'desktop' || path.basename(path.dirname(toolchainRoot)) !== 'sandbox'
      || path.basename(path.dirname(path.dirname(toolchainRoot))) !== '.worktrees') fail('toolchainRoot must be a canonical .worktrees/sandbox/desktop path');
  safeDirectory(toolchainRoot);

  // All input reads and checks happen before claiming the global writer lock.
  const trustedHelperHashes = new Map([
    [CHILD, hashRegularFile(CHILD, 2 * 1024 * 1024)],
    [RECORDS, hashRegularFile(RECORDS, 2 * 1024 * 1024)],
    [TOOLCHAIN_GUARD, hashRegularFile(TOOLCHAIN_GUARD, 2 * 1024 * 1024)],
    [TOOLCHAIN_GUARD_HELPER, hashRegularFile(TOOLCHAIN_GUARD_HELPER, 2 * 1024 * 1024)],
  ]);
  const operation = readMaterializedOperation({ sandboxRoot, operationId });
  const operationRoot = operation.operationRoot;
  assertNoBuildArtifacts(operationRoot);
  const captured = captureInstalledToolchain(toolchainRoot);
  const nodeExecutable = path.resolve(process.execPath);
  regularFile(nodeExecutable, 256 * 1024 * 1024);
  const nodeHash = hashRegularFile(nodeExecutable, 256 * 1024 * 1024);
  const packageJsonSha256 = captured.packageJsonSha256;
  const packageLockSha256 = captured.packageLockSha256;
  const toolchainBody = {
    format: 1, mode: 'managed_postmerge_toolchain_v1', root: toolchainRoot,
    packageJsonSha256, packageLockSha256,
    node: { executable: nodeExecutable, sha256: nodeHash, version: process.version }, files: captured.files,
  };
  const toolchain = { ...toolchainBody, toolchainDigest: digest(toolchainBody) };
  verifySourcePackageBinding(operation, toolchain);
  const toolchainRecordBytes = Buffer.byteLength(`${canonical(toolchain)}\n`);
  if (toolchainRecordBytes > 16 * 1024 * 1024) fail('canonical toolchain record exceeds 16 MiB before build-store claim');
  const buildIntent = {
    format: 1, mode: 'managed_postmerge_build_intent_v1', operationId,
    planDigest: operation.snapshot.planDigest, snapshotDigest: operation.snapshot.snapshotDigest,
    materializedReceiptDigest: operation.materialized.receiptDigest, toolchainDigest: toolchain.toolchainDigest,
    buildPolicy: BUILD_POLICY, state: 'started', readyForLaunch: false,
  };
  const intentRecordBytes = Buffer.byteLength(`${canonical(buildIntent)}\n`);
  if (intentRecordBytes > 16 * 1024 * 1024) fail('canonical build intent exceeds 16 MiB before build-store claim');
  const metadataBytes = toolchainRecordBytes + intentRecordBytes;
  const metadataAllocation = Math.ceil(toolchainRecordBytes / CLUSTER) * CLUSTER + Math.ceil(intentRecordBytes / CLUSTER) * CLUSTER;

  // Acquire OS share locks before the store claim and before starting Node's build child.
  for (const [file, expected] of trustedHelperHashes) if (hashRegularFile(file, 2 * 1024 * 1024) !== expected) fail(`trusted build helper changed before guard acquisition: ${path.basename(file)}`);
  const guardModule = await import('./managed-toolchain-read-guard.mjs');
  for (const [file, expected] of trustedHelperHashes) if (hashRegularFile(file, 2 * 1024 * 1024) !== expected) fail(`trusted build helper changed while loading guard: ${path.basename(file)}`);
  const toolchainGuard = await guardModule.acquireManagedToolchainReadGuard({ toolchain });
  let guardReleaseAttempted = false, claim;
  try {
    if (!toolchainGuard?.receipt || toolchainGuard.receipt.toolchainDigest !== toolchain.toolchainDigest) fail('toolchain read guard receipt does not bind pinned toolchain');
    // Reserve a bounded worst-case output allocation before any worker-owned file write.
    claim = claimBuildStore({ sandboxRoot, operationId, kind: 'build' });
    assertStoreBudget({ sandboxRoot, reserveLogical: metadataBytes, reserveAllocation: metadataAllocation });
    writeCanonicalExclusive(path.join(operationRoot, 'build-intent.json'), buildIntent);
    writeCanonicalExclusive(path.join(operationRoot, 'toolchain.json'), toolchain);
    const emptyEnvDir = path.join(operationRoot, 'empty-env');
    mkdirSync(emptyEnvDir);
    safeDirectory(emptyEnvDir);

    for (const [file, expected] of trustedHelperHashes) if (hashRegularFile(file, 2 * 1024 * 1024) !== expected) fail(`trusted build helper changed before execution: ${path.basename(file)}`);
    const run = spawnSync(nodeExecutable, [
      CHILD, '--source-root', operation.sourceRoot, '--build-root', path.join(operationRoot, 'build'), '--toolchain-root', toolchainRoot,
      '--snapshot-digest', operation.snapshot.snapshotDigest, '--toolchain-digest', toolchain.toolchainDigest,
    ], {
      cwd: sandboxRoot, env: cleanChildEnvironment(), encoding: 'buffer', windowsHide: true,
      maxBuffer: 112 * 1024 * 1024, timeout: 180_000,
    });
    if (run.error) fail(`trusted build child could not run: ${run.error.message}`);
    if (run.status !== 0) fail(`trusted configless build failed: ${run.stderr?.toString('utf8').slice(-12000) || `status ${run.status}`}`);
    for (const [file, expected] of trustedHelperHashes) if (hashRegularFile(file, 2 * 1024 * 1024) !== expected) fail(`trusted build helper changed during execution: ${path.basename(file)}`);
    if (hashRegularFile(nodeExecutable, 256 * 1024 * 1024) !== nodeHash) fail('pinned Node executable changed during build');
    for (const [file, expected] of trustedHelperHashes) if (hashRegularFile(file, 2 * 1024 * 1024) !== expected) fail(`trusted build helper changed during execution: ${path.basename(file)}`);
    const operationAfter = readMaterializedOperation({ sandboxRoot, operationId });
    if (canonical(operationAfter.snapshot) !== canonical(operation.snapshot)
        || canonical(operationAfter.materialized) !== canonical(operation.materialized)
        || canonical(operationAfter.intent) !== canonical(operation.intent)) fail('materialized operation changed during build');
    const afterToolchain = captureInstalledToolchain(toolchainRoot);
    if (canonical(afterToolchain) !== canonical(captured)) fail('pinned build dependencies changed during build');

    const buildRoot = path.join(operationRoot, 'build');
    const outputs = validateChildOutputs(run.stdout, buildRoot, { managedEntry: operation.snapshot.files.some(file => file.path === 'desktop/electron/entry.ts') });
    let logical = 0, allocation = 0;
    for (const output of outputs) { logical += output.byteLength; allocation += Math.ceil(output.byteLength / CLUSTER) * CLUSTER; }
    const receiptBytes = Buffer.byteLength(`${canonical(seal({
      format: 1, mode: 'managed_postmerge_build_receipt_v1', operationId,
      planDigest: operation.snapshot.planDigest, snapshotDigest: operation.snapshot.snapshotDigest,
      materializedReceiptDigest: operation.materialized.receiptDigest, toolchainDigest: toolchain.toolchainDigest,
      buildPolicy: BUILD_POLICY, outputs: outputs.map(({ bytes, ...entry }) => entry), inputsDigest: digest(operation.snapshot.files),
      buildVerified: true, readyForLaunch: false,
    }))}\n`);
    assertStoreBudget({ sandboxRoot, reserveLogical: logical + receiptBytes, reserveAllocation: allocation + Math.ceil(receiptBytes / CLUSTER) * CLUSTER });
    mkdirSync(buildRoot);
    for (const output of outputs) {
      const target = path.resolve(buildRoot, ...output.path.split('/'));
      const parent = path.dirname(target);
      const missing = [];
      let cursor = parent;
      while (!existsSync(cursor) && within(buildRoot, cursor)) { missing.push(cursor); cursor = path.dirname(cursor); }
      if (!within(buildRoot, cursor)) fail('output parent escaped build root');
      for (const directory of missing.reverse()) mkdirSync(directory);
      safeDirectory(parent);
      writeBytesExclusive(target, output.bytes, MAX_BYTES);
    }
    const outputInventory = outputs.map(({ bytes, ...entry }) => entry);
    const actual = verifyTree(buildRoot, outputInventory, { maxFiles: MAX_FILES, maxBytes: MAX_BYTES });
    if (canonical(actual) !== canonical(outputInventory)) fail('exclusive build output inventory differs from in-memory build');
    const receipt = seal({
      format: 1, mode: 'managed_postmerge_build_receipt_v1', operationId,
      planDigest: operation.snapshot.planDigest, snapshotDigest: operation.snapshot.snapshotDigest,
      materializedReceiptDigest: operation.materialized.receiptDigest, toolchainDigest: toolchain.toolchainDigest,
      buildPolicy: BUILD_POLICY, outputs: outputInventory, inputsDigest: digest(operation.snapshot.files),
      buildVerified: true, readyForLaunch: false,
    });
    writeCanonicalExclusive(path.join(operationRoot, 'build.json'), receipt);
    assertStoreBudget({ sandboxRoot });
    guardReleaseAttempted = true;
    await toolchainGuard.release();
    claim.complete();
    return { operationRoot, receipt };
  } catch (error) {
    // The owned lock and every partial record deliberately remain as a maintenance fence.
    claim?.abandon();
    throw error;
  } finally {
    if (!guardReleaseAttempted) {
      guardReleaseAttempted = true;
      await toolchainGuard.release();
    }
  }
}
