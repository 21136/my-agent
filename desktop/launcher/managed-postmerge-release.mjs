import { chmodSync, existsSync, lstatSync, mkdirSync, opendirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import path from 'node:path';
import {
  assertStoreBudget, canonical, captureTree, claimBuildStore, digest, readCanonical,
  readStableBytes, readVerifiedBuildOperation, regularFile, safeDirectory, seal, syncDirectory, verifyTree,
  safeRelative, writeBytesExclusive, writeCanonicalExclusive,
} from './managed-build-records.mjs';

const HASH = /^[a-f0-9]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const REQUIRED_BUILD = [
  'desktop/dist-electron/main.js',
  'desktop/dist-electron/preload.js',
  'desktop/dist/index.html',
];
const MANAGED_PRELOAD_OUTPUT = 'desktop/dist-electron/managed-preload.js';
const MANAGED_GIT_METADATA_OUTPUT = 'desktop/dist-electron/managed-clean-git-metadata.mjs';
const REQUIRED_SOURCE = [
  'desktop/package.json', 'agent-core/server.py',
  'agent-core/fixed_host_entry.py', 'evolve/_index.core.toml',
];
const REQUIRED_MANAGED_PYTHON = [
  'agent-core/managed_host_entry.py', 'agent-core/managed_readonly.py',
  'agent-core/managed_store.py', 'agent-core/managed_application.py',
];
const REQUIRED_MANAGED_ELECTRON = [
  'desktop/electron/managed-host-entry.ts', 'desktop/electron/managed-host-context.ts',
  'desktop/electron/managed-preload.ts',
];
const REQUIRED_MANAGED_RUNTIME_HELPERS = ['desktop/launcher/managed-clean-git-metadata.mjs'];
const BUILD_OUTPUT_ROOTS = ['desktop/dist/', 'desktop/dist-electron/'];
const RELEASE_MODE = 'managed_postmerge_release_v1';
const INTENT_MODE = 'managed_postmerge_release_seal_intent_v1';
const RECEIPT_MODE = 'managed_postmerge_release_seal_receipt_v1';
const MAX_RELEASE_BYTES = 64 * 1024 * 1024;
const MAX_RELEASE_FILES = 20_000;
const MAX_TOOLCHAIN_BYTES = 448 * 1024 * 1024;
const BLOCKED_SEGMENT = /^(?:tests?|scripts?|data|config|secrets?|private|credentials?|.git|.venv|venv|node_modules|__pycache__|dist|dist-electron|profile|profiles|user-data|workspace|workspaces|artifacts)$/i;
const BLOCKED_FILE = /^(?:\.env(?:\..*)?|llm_secrets\.(?:json|ya?ml)|llm_models\.(?:json|ya?ml))$|\.(?:pem|key|p12|pfx|cer|crt|db|sqlite3?|jsonl|log|pyc)$/i;
const RUNTIME_TEMPLATE_EXT = /\.(?:toml|txt|j2|jinja|jinja2|tmpl)$/i;

function fail(message) { throw new TypeError('managed_postmerge_release_invalid: ' + message); }
function exact(value, keys) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    && Object.getPrototypeOf(value) === Object.prototype
    && Object.keys(value).sort().join('\0') === [...keys].sort().join('\0');
}
function same(a, b) { return canonical(a) === canonical(b); }
function comparePath(a, b) { return a.localeCompare(b); }
function normalizedFiles(files, label) {
  if (!Array.isArray(files) || files.length === 0 || files.length > MAX_RELEASE_FILES) fail(label + ' inventory invalid');
  const seen = new Set();
  let total = 0;
  for (const file of files) {
    if (!exact(file, ['path', 'mode', 'sha256', 'byteLength']) || !safeRelative(file.path)
        || !['100644', '100755'].includes(file.mode) || !HASH.test(file.sha256 || '')
        || !Number.isSafeInteger(file.byteLength) || file.byteLength < 0) fail(label + ' entry invalid');
    const folded = file.path.toLowerCase();
    if (seen.has(folded)) fail(label + ' path alias');
    seen.add(folded); total += file.byteLength;
    if (!Number.isSafeInteger(total) || total > MAX_RELEASE_BYTES) fail(label + ' exceeds byte bound');
  }
  if (!same(files.map(f => f.path), files.map(f => f.path).sort(comparePath))) fail(label + ' inventory is not sorted');
  return total;
}
function sourcePayloadPath(rel) {
  if (rel === 'desktop/package.json' || rel === 'evolve/_index.core.toml') return true;
  const parts = rel.split('/');
  if (parts.some(part => BLOCKED_SEGMENT.test(part)) || parts.some(part => BLOCKED_FILE.test(part))) return false;
  if (rel.startsWith('desktop/launcher/')) return /\.mjs$|\.d\.mts$/i.test(rel);
  if (rel.startsWith('agent-core/')) {
    if (/\.py$/i.test(rel)) return true;
    return RUNTIME_TEMPLATE_EXT.test(rel) && parts.some(part => /^(?:prompts?|templates?)$/i.test(part));
  }
  return false;
}
function requiredBuildOutputs(operation) {
  return operation.snapshot.files.some(file => file.path === 'desktop/electron/entry.ts')
    ? [...REQUIRED_BUILD.slice(0, 2), MANAGED_PRELOAD_OUTPUT, MANAGED_GIT_METADATA_OUTPUT, REQUIRED_BUILD[2]]
    : REQUIRED_BUILD;
}
function validateOutputPath(rel, allowedElectronOutputs) {
  if (!safeRelative(rel) || !BUILD_OUTPUT_ROOTS.some(root => rel.startsWith(root))) return false;
  if (rel.startsWith('desktop/dist-electron/')) return allowedElectronOutputs.includes(rel);
  return rel.startsWith('desktop/dist/') && !rel.slice('desktop/dist/'.length).split('/').some(part => BLOCKED_SEGMENT.test(part) || BLOCKED_FILE.test(part));
}
function fileMap(files) { return new Map(files.map(file => [file.path, file])); }
function verifyCurrentToolchain(operation) {
  const { toolchain } = operation;
  safeDirectory(toolchain.root);
  if (!Array.isArray(toolchain.files) || !toolchain.files.length || toolchain.files.length > MAX_RELEASE_FILES) fail('toolchain inventory invalid');
  let total = 0;
  let previous = null;
  const seen = new Set();
  for (const entry of toolchain.files) {
    if (!exact(entry, ['path', 'mode', 'sha256', 'byteLength']) || !safeRelative(entry.path)
        || !entry.path.startsWith('node_modules/') || !['100644', '100755'].includes(entry.mode)
        || !HASH.test(entry.sha256 || '') || !Number.isSafeInteger(entry.byteLength) || entry.byteLength < 0) fail('toolchain entry schema invalid');
    if (previous !== null && comparePath(previous, entry.path) >= 0) fail('toolchain inventory is not strictly ordered');
    previous = entry.path;
    const folded = entry.path.toLowerCase();
    if (seen.has(folded)) fail('toolchain path alias');
    seen.add(folded);
    total += entry.byteLength;
    if (!Number.isSafeInteger(total) || total > MAX_TOOLCHAIN_BYTES) fail('toolchain inventory exceeds byte bound');
    const file = path.join(toolchain.root, ...entry.path.split('/'));
    regularFile(file, MAX_TOOLCHAIN_BYTES);
    const stat = lstatSync(file);
    const bytes = readStableBytes(file, MAX_TOOLCHAIN_BYTES);
    const actual = { mode: (stat.mode & 0o111) ? '100755' : '100644', sha256: createHash('sha256').update(bytes).digest('hex'), byteLength: bytes.length };
    if (!same(actual, { mode: entry.mode, sha256: entry.sha256, byteLength: entry.byteLength })) fail('pinned toolchain file changed: ' + entry.path);
  }
  const nodeFile = regularFile(toolchain.node.executable, MAX_TOOLCHAIN_BYTES);
  if (createHash('sha256').update(readStableBytes(nodeFile, MAX_TOOLCHAIN_BYTES)).digest('hex') !== toolchain.node.sha256) fail('pinned Node executable changed');
  for (const [name, hash] of [['package.json', toolchain.packageJsonSha256], ['package-lock.json', toolchain.packageLockSha256]]) {
    const file = regularFile(path.join(toolchain.root, name), MAX_TOOLCHAIN_BYTES);
    if (createHash('sha256').update(readStableBytes(file, MAX_TOOLCHAIN_BYTES)).digest('hex') !== hash) fail('pinned toolchain ' + name + ' changed');
  }
}
function sourceFiles(operation) {
  const snapshotFiles = operation.snapshot.files;
  const available = fileMap(snapshotFiles);
  for (const rel of REQUIRED_SOURCE) if (!available.has(rel)) fail('required source payload missing: ' + rel);
  if (available.has('desktop/electron/entry.ts')) {
    for (const rel of [...REQUIRED_MANAGED_PYTHON, ...REQUIRED_MANAGED_ELECTRON, ...REQUIRED_MANAGED_RUNTIME_HELPERS]) {
      if (!available.has(rel)) fail('managed bundle source missing: ' + rel);
    }
  }
  const selected = snapshotFiles.filter(file => sourcePayloadPath(file.path));
  if (!selected.length || selected.length > MAX_RELEASE_FILES) fail('source payload inventory invalid');
  return selected.map(({ path: rel, mode, sha256, byteLength }) => ({ path: rel, mode, sha256, byteLength })).sort((a, b) => comparePath(a.path, b.path));
}
function outputFiles(operation) {
  const outputs = operation.build.outputs;
  normalizedFiles(outputs, 'build output');
  const required = requiredBuildOutputs(operation);
  const allowedElectronOutputs = required.filter(rel => rel.startsWith('desktop/dist-electron/'));
  if (outputs.some(file => !validateOutputPath(file.path, allowedElectronOutputs))) fail('build declares an output outside the exact runtime output allowlist');
  const byPath = fileMap(outputs);
  for (const rel of required) if (!byPath.has(rel)) fail(([MANAGED_PRELOAD_OUTPUT, MANAGED_GIT_METADATA_OUTPUT].includes(rel) ? 'required managed Electron output missing: ' : 'required built output missing: ') + rel);
  return outputs.map(file => ({ ...file })).sort((a, b) => comparePath(a.path, b.path));
}
function releaseFiles(operation) {
  const files = [...sourceFiles(operation), ...outputFiles(operation)].sort((a, b) => comparePath(a.path, b.path));
  normalizedFiles(files, 'release payload');
  const seen = new Set();
  for (const file of files) {
    if (seen.has(file.path.toLowerCase())) fail('source and build payload overlap');
    seen.add(file.path.toLowerCase());
  }
  return files;
}
function directoriesFor(files) {
  const result = new Set();
  for (const file of files) {
    const parts = file.path.split('/');
    parts.pop();
    while (parts.length) { result.add(parts.join('/')); parts.pop(); }
  }
  return [...result].sort();
}
function fileInventory(root) {
  return captureTree(root, { maxFiles: MAX_RELEASE_FILES, maxBytes: MAX_RELEASE_BYTES });
}
function canonicalFileEntry(rel, value) {
  const bytes = Buffer.from(canonical(value) + '\n');
  return { path: rel, mode: '100644', sha256: createHash('sha256').update(bytes).digest('hex'), byteLength: bytes.length };
}
function releaseChildren(releaseBase) {
  const dir = opendirSync(releaseBase);
  const entries = [];
  try {
    for (let count = 0; count < 2; count++) {
      const entry = dir.readSync();
      if (!entry) break;
      entries.push(entry.name);
    }
  } finally { dir.closeSync(); }
  return entries;
}
function assertReleaseBaseBeforeSeal(operationRoot, releaseId) {
  const releaseBase = path.join(operationRoot, 'release');
  if (!existsSync(releaseBase)) return releaseBase;
  safeDirectory(releaseBase);
  const entries = releaseChildren(releaseBase);
  if (entries.includes(releaseId)) fail('existing release target; refusing reuse or overwrite');
  if (entries.length) fail('unknown or partial release inventory exists');
  fail('pre-existing empty release directory is an unknown partial operation');
}
function assertExactReleaseBase(operationRoot, releaseId) {
  const releaseBase = path.join(operationRoot, 'release');
  safeDirectory(releaseBase);
  const entries = releaseChildren(releaseBase);
  if (entries.length !== 1 || entries[0] !== releaseId) fail('release inventory has unknown sibling or partial directory');
}
function readOperation(sandboxRoot, operationId) {
  if (!UUID.test(operationId || '')) fail('operationId invalid');
  const root = safeDirectory(path.resolve(sandboxRoot));
  const operation = readVerifiedBuildOperation({ sandboxRoot: root, operationId });
  if (!operation || !operation.snapshot || !operation.build || !operation.toolchain) fail('verified build operation unavailable');
  if (operation.snapshot.readyForLaunch !== false || operation.snapshot.buildVerified !== false
      || operation.materialized?.readyForLaunch !== false || operation.build.readyForLaunch !== false
      || operation.build.buildVerified !== true || operation.build.buildPolicy !== 'configless_snapshot_v2') fail('operation is not a verified no-launch postmerge build');
  if (operation.build.operationId !== operationId || operation.build.planDigest !== operation.snapshot.planDigest
      || operation.build.snapshotDigest !== operation.snapshot.snapshotDigest
      || operation.build.materializedReceiptDigest !== operation.materialized.receiptDigest
      || operation.build.inputsDigest !== digest(operation.snapshot.files)) fail('build provenance differs from verified source/materialization');
  if (operation.toolchain.mode !== 'managed_postmerge_toolchain_v1' || operation.toolchain.toolchainDigest !== operation.build.toolchainDigest) fail('build toolchain provenance mismatch');
  const buildIntent = readCanonical(path.join(operation.operationRoot, 'build-intent.json'), 64 * 1024);
  const result = { ...operation, sandboxRoot: root, buildIntent };
  verifyCurrentToolchain(result);
  return result;
}
function manifestFor(operation, files) {
  return {
    format: 1, mode: RELEASE_MODE, protocol: 1,
    source: {
      snapshotDigest: operation.snapshot.snapshotDigest,
      planDigest: operation.snapshot.planDigest,
      receiptDigest: operation.snapshot.receiptDigest,
      mergedHead: operation.snapshot.mergedHead,
      mergedTree: operation.snapshot.mergedTree,
    },
    build: {
      receiptDigest: operation.build.receiptDigest,
      toolchainDigest: operation.build.toolchainDigest,
      policy: 'configless_snapshot_v2',
    },
    data: { policy: 'empty_no_migration', schema: 'managed_empty_store_v1', revisionBefore: 0 },
    files, readyForLaunch: false,
  };
}
function copyPayload(operation, appRoot, files) {
  const sourceByPath = fileMap(operation.snapshot.files);
  const outputByPath = fileMap(operation.build.outputs);
  for (const entry of files) {
    const isBuild = outputByPath.has(entry.path);
    const sourceRoot = isBuild ? operation.buildRoot : operation.sourceRoot;
    const expected = isBuild ? outputByPath.get(entry.path) : sourceByPath.get(entry.path);
    if (!expected || expected.sha256 !== entry.sha256 || expected.byteLength !== entry.byteLength || expected.mode !== entry.mode) fail('payload source record mismatch: ' + entry.path);
    const from = path.join(sourceRoot, ...entry.path.split('/'));
    regularFile(from, MAX_RELEASE_BYTES);
    const target = path.join(appRoot, ...entry.path.split('/'));
    mkdirSync(path.dirname(target), { recursive: true });
    writeBytesExclusive(target, readStableBytes(from, MAX_RELEASE_BYTES), MAX_RELEASE_BYTES);
    chmodSync(target, entry.mode === '100755' ? 0o755 : 0o644);
    syncDirectory(path.dirname(target));
    const copied = fileInventory(path.dirname(target)).find(item => item.path === path.basename(target));
    if (!copied || copied.sha256 !== entry.sha256 || copied.byteLength !== entry.byteLength || copied.mode !== entry.mode) fail('copied payload hash/mode mismatch: ' + entry.path);
  }
}
function ensureCanonicalRecord(file, value) {
  const reread = readCanonical(file, 1024 * 1024);
  if (!same(reread, value)) fail('published canonical record changed: ' + path.basename(file));
}
function verifyReleaseRoot(operation, releaseId, { requireSealed = true } = {}) {
  if (!HASH.test(releaseId || '')) fail('releaseId invalid');
  const opRoot = path.join(operation.sandboxRoot, 'artifacts', 'managed-postmerge-builds', operation.build.operationId);
  const releaseRoot = path.join(opRoot, 'release', releaseId);
  const root = safeDirectory(releaseRoot);
  assertExactReleaseBase(opRoot, releaseId);
  const manifestPath = path.join(root, 'manifest.json');
  const manifest = readCanonical(manifestPath, 4 * 1024 * 1024);
  const keys = ['format', 'mode', 'protocol', 'source', 'build', 'data', 'files', 'readyForLaunch'];
  if (!exact(manifest, keys) || manifest.format !== 1 || manifest.mode !== RELEASE_MODE || manifest.protocol !== 1 || manifest.readyForLaunch !== false) fail('release manifest schema invalid or launchable');
  normalizedFiles(manifest.files, 'release manifest');
  const computedId = digest(manifest);
  if (computedId !== releaseId || computedId !== digest(manifestFor(operation, releaseFiles(operation)))) fail('release manifest identity/provenance digest mismatch');
  if (!same(manifest.source, manifestFor(operation, manifest.files).source) || !same(manifest.build, manifestFor(operation, manifest.files).build)
      || !same(manifest.data, { policy: 'empty_no_migration', schema: 'managed_empty_store_v1', revisionBefore: 0 })) fail('release source/build/data provenance mismatch');
  const expectedFiles = releaseFiles(operation);
  if (!same(manifest.files, expectedFiles)) fail('release manifest payload inventory differs from verified inputs');
  const appRoot = path.join(root, 'app');
  safeDirectory(appRoot);
  verifyTree(appRoot, expectedFiles, { maxFiles: MAX_RELEASE_FILES, maxBytes: MAX_RELEASE_BYTES });
  const actualAppFiles = fileInventory(appRoot);
  if (!same(actualAppFiles, expectedFiles)) fail('release app content/mode hashes differ from manifest');
  const intent = readCanonical(path.join(root, 'seal-intent.json'), 64 * 1024);
  if (!exact(intent, ['format', 'mode', 'operationId', 'releaseId', 'manifestDigest', 'state', 'intentDigest'])
      || intent.format !== 1 || intent.mode !== INTENT_MODE || intent.operationId !== operation.build.operationId
      || intent.releaseId !== releaseId || intent.manifestDigest !== releaseId || intent.state !== 'started'
      || intent.intentDigest !== digest(Object.fromEntries(Object.entries(intent).filter(([key]) => key !== 'intentDigest')))) fail('release seal intent invalid');
  const rootFiles = [
    ...expectedFiles.map(file => ({ ...file, path: 'app/' + file.path })),
    canonicalFileEntry('manifest.json', manifest),
    canonicalFileEntry('seal-intent.json', intent),
  ];
  if (requireSealed) {
    const receipt = readCanonical(path.join(root, 'sealed.json'), 64 * 1024);
    if (!exact(receipt, ['format', 'mode', 'operationId', 'releaseId', 'manifestDigest', 'filesDigest', 'readyForLaunch', 'receiptDigest'])
        || receipt.format !== 1 || receipt.mode !== RECEIPT_MODE || receipt.operationId !== operation.build.operationId
        || receipt.releaseId !== releaseId || receipt.manifestDigest !== releaseId || receipt.filesDigest !== digest(expectedFiles)
        || receipt.readyForLaunch !== false || receipt.receiptDigest !== digest(Object.fromEntries(Object.entries(receipt).filter(([key]) => key !== 'receiptDigest')))) fail('release seal receipt invalid');
    rootFiles.push(canonicalFileEntry('sealed.json', receipt));
  }
  verifyTree(root, rootFiles.sort((a, b) => comparePath(a.path, b.path)), { maxFiles: MAX_RELEASE_FILES + 3, maxBytes: MAX_RELEASE_BYTES + 4 * 1024 * 1024 });
  return { format: 1, mode: 'managed_postmerge_release_descriptor_v1', releaseId, manifestDigest: releaseId, root, readyForLaunch: false };
}

/** Seal a verified postmerge build as an inert, production-schema release payload. */
export function sealManagedPostmergeRelease({ sandboxRoot, operationId }) {
  const operation = readOperation(sandboxRoot, operationId);
  const files = releaseFiles(operation);
  const manifest = manifestFor(operation, files);
  const releaseId = digest(manifest);
  const operationRoot = path.join(operation.sandboxRoot, 'artifacts', 'managed-postmerge-builds', operationId);
  safeDirectory(operationRoot);
  const releaseBase = assertReleaseBaseBeforeSeal(operationRoot, releaseId);
  assertStoreBudget({ sandboxRoot: operation.sandboxRoot });
  const claim = claimBuildStore({ sandboxRoot: operation.sandboxRoot, operationId, kind: 'seal' });
  try {
  const intent = seal({ format: 1, mode: INTENT_MODE, operationId, releaseId, manifestDigest: releaseId, state: 'started' }, 'intentDigest');
  const receipt = seal({ format: 1, mode: RECEIPT_MODE, operationId, releaseId, manifestDigest: releaseId, filesDigest: digest(files), readyForLaunch: false });
  const records = [manifest, intent, receipt].map(value => Buffer.byteLength(canonical(value) + '\n'));
  const logical = files.reduce((sum, file) => sum + file.byteLength, records.reduce((sum, size) => sum + size, 0));
  const allocated = files.reduce((sum, file) => sum + Math.ceil(file.byteLength / 131072) * 131072, records.reduce((sum, size) => sum + Math.ceil(size / 131072) * 131072, 0));
  assertStoreBudget({ sandboxRoot: operation.sandboxRoot, reserveLogical: logical, reserveAllocation: allocated });
  if (!existsSync(releaseBase)) mkdirSync(releaseBase);
  safeDirectory(releaseBase);
  const targetRoot = path.join(releaseBase, releaseId);
  mkdirSync(targetRoot);
  writeCanonicalExclusive(path.join(targetRoot, 'seal-intent.json'), intent);
  mkdirSync(path.join(targetRoot, 'app'));
  writeCanonicalExclusive(path.join(targetRoot, 'manifest.json'), manifest);
  copyPayload(operation, path.join(targetRoot, 'app'), files);
  const after = readOperation(operation.sandboxRoot, operationId);
  if (!same(after.intent, operation.intent) || !same(after.snapshot, operation.snapshot)
      || !same(after.materialized, operation.materialized) || !same(after.build, operation.build)
      || !same(after.toolchain, operation.toolchain) || !same(after.buildIntent, operation.buildIntent)
      || !same(fileInventory(operation.sourceRoot), operation.snapshot.files.map(({ path: rel, mode, sha256, byteLength }) => ({ path: rel, mode, sha256, byteLength })).sort((a, b) => comparePath(a.path, b.path)))
      || !same(fileInventory(operation.buildRoot), operation.build.outputs)) fail('verified inputs changed during release copy');
  const checked = verifyReleaseRoot(operation, releaseId, { requireSealed: false });
  const receiptBody = { format: 1, mode: RECEIPT_MODE, operationId, releaseId, manifestDigest: releaseId, filesDigest: digest(files), readyForLaunch: false };
  writeCanonicalExclusive(path.join(targetRoot, 'sealed.json'), seal(receiptBody));
  ensureCanonicalRecord(path.join(targetRoot, 'manifest.json'), manifest);
  verifyReleaseRoot(operation, releaseId, { requireSealed: true });
  assertStoreBudget({ sandboxRoot: operation.sandboxRoot });
  claim.complete();
  return checked;
  } catch (error) {
    claim.abandon();
    throw error;
  }
}

/** Verify current source/build provenance and the exact published release tree. */
export function verifyManagedPostmergeRelease({ sandboxRoot, operationId, releaseId }) {
  const operation = readOperation(sandboxRoot, operationId);
  assertStoreBudget({ sandboxRoot: operation.sandboxRoot });
  return verifyReleaseRoot(operation, releaseId, { requireSealed: true });
}
