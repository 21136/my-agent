import { chmodSync, existsSync, lstatSync, mkdirSync, readdirSync, renameSync } from 'node:fs';
import { createHash, randomUUID } from 'node:crypto';
import path from 'node:path';
import {
  canonical, captureTree, digest, exact, readCanonical, readMaterializedOperation,
  readStableBytes, readVerifiedBuildOperation, regularFile, safeDirectory, safeRelative,
  seal, syncDirectory, verifyTree, writeBytesExclusive, writeCanonicalExclusive,
} from './managed-build-records.mjs';
import { projectNamespaceDigest, projectNamespacePath, readProjectNamespace } from './project-namespace.mjs';
import { verifyManagedPostmergeRelease } from './managed-postmerge-release.mjs';
import { captureManagedPostmergeSource } from './managed-postmerge-source.mjs';
import { readSelectedMergeEvidence } from './managed-selected-merge-store.mjs';
import { captureManagedRuntimeInputs, validateManagedRuntimeRegistrationRecords } from './managed-runtime-registration.mjs';

const PREFIX = 'managed_production_context_invalid: ';
const BINDING_MODE = 'managed_production_binding_v1';
const INTENT_MODE = 'managed_production_context_intent_v1';
const RECEIPT_MODE = 'managed_production_context_receipt_v1';
const DESCRIPTOR_MODE = 'managed_production_context_descriptor_v1';
const MAX_RECORD_BYTES = 1024 * 1024;
const MAX_RUNTIME_REGISTRATION_BYTES = 32 * 1024 * 1024;
const MAX_RELEASE_BYTES = 64 * 1024 * 1024;
const MAX_RELEASE_TREE_BYTES = MAX_RELEASE_BYTES + 4 * 1024 * 1024;
const MAX_RELEASE_FILES = 20_000;
const MAX_TREE_FILES = MAX_RELEASE_FILES + 3;
const MAX_ALLOCATED_BYTES = 512 * 1024 * 1024;
const ALLOCATION_UNIT = 128 * 1024;
const TRUSTED_GIT_EXECUTABLE = 'C:\\Program Files\\Git\\cmd\\git.exe';
const HASH = /^[a-f0-9]{64}$/;
const GIT_OID = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const SOURCE_KEYS = ['operationId', 'planDigest', 'receiptDigest', 'snapshotDigest', 'mergedHead', 'mergedTree', 'roots'];
const ROOT_KEYS = ['stable', 'sandbox'];
const SNAPSHOT_ROOT_KEYS = ['worktreeRoot', 'gitDir', 'commonDir', 'branch'];
const MANIFEST_KEYS = ['format', 'mode', 'protocol', 'source', 'build', 'data', 'files', 'readyForLaunch'];
const BASE_DIRECTORIES = [
  'launcher', 'state', 'releases', 'host', 'host/state', 'host/profile', 'host/workspace', 'data', 'data/store',
];
const MARKER_PATH = 'state/managed-namespace.json';
const INTENT_PATH = 'state/production-context-intent.json';
const BINDING_PATH = 'state/production-binding.json';
const RECEIPT_PATH = 'state/production-context-receipt.json';

function fail(message) { throw new TypeError(PREFIX + message); }
function same(left, right) { return canonical(left) === canonical(right); }
function sha(bytes) { return createHash('sha256').update(bytes).digest('hex'); }
function comparePath(left, right) { return left.localeCompare(right); }
function isPlainDirectory(target) {
  const stat = lstatSync(target);
  if (!stat.isDirectory() || stat.isSymbolicLink() || (process.platform === 'win32' && (stat.attributes & 0x400))) fail('expected an ordinary directory');
  return target;
}
function canonicalRoot(value, label) {
  if (typeof value !== 'string' || !path.isAbsolute(value) || path.resolve(value) !== value) fail(`${label} must be a canonical absolute path`);
  return value;
}
function validateInput(input) {
  const keys = ['stableRoot', 'sandboxRoot', 'installationId', 'operationId', 'releaseId'];
  if (!exact(input, keys)) fail('input must contain only the five fixed production context fields');
  const stableRoot = canonicalRoot(input.stableRoot, 'stableRoot');
  const sandboxRoot = canonicalRoot(input.sandboxRoot, 'sandboxRoot');
  if (typeof input.installationId !== 'string' || !UUID.test(input.installationId)) fail('installationId must be a lowercase canonical UUID');
  if (typeof input.operationId !== 'string' || !UUID.test(input.operationId)) fail('operationId must be a lowercase canonical UUID');
  if (typeof input.releaseId !== 'string' || !HASH.test(input.releaseId)) fail('releaseId must be a lowercase SHA-256 digest');
  if (path.resolve(stableRoot) === path.resolve(sandboxRoot)) fail('stableRoot and sandboxRoot must be distinct');
  return { stableRoot, sandboxRoot, installationId: input.installationId, operationId: input.operationId, releaseId: input.releaseId };
}
function namespaceInput(input) {
  return { installationId: input.installationId, stableSourceRoot: input.stableRoot, sandboxSourceRoot: input.sandboxRoot };
}
function namespacePaths(root) {
  return {
    launcher: path.join(root, 'launcher'), state: path.join(root, 'state'),
    hostState: path.join(root, 'host', 'state'), hostProfile: path.join(root, 'host', 'profile'),
    hostWorkspace: path.join(root, 'host', 'workspace'), data: path.join(root, 'data', 'store'),
  };
}
function contextPaths(namespace, releaseId) {
  const releaseRoot = path.join(namespace.paths.releases, releaseId);
  return { ...namespacePaths(namespace.root), code: path.join(releaseRoot, 'app') };
}
function sourceBinding(operation) {
  const snapshot = operation.snapshot;
  if (!exact(snapshot.roots, ROOT_KEYS) || ROOT_KEYS.some(side => !exact(snapshot.roots[side], SNAPSHOT_ROOT_KEYS))) fail('verified snapshot source roots are malformed');
  return {
    operationId: operation.build.operationId,
    planDigest: snapshot.planDigest,
    receiptDigest: snapshot.receiptDigest,
    snapshotDigest: snapshot.snapshotDigest,
    mergedHead: snapshot.mergedHead,
    mergedTree: snapshot.mergedTree,
    roots: snapshot.roots,
  };
}
function assertSourceNamespaceRoots(namespace, source) {
  if (namespace.source_roots.stable !== source.roots.stable.worktreeRoot
      || namespace.source_roots.sandbox !== source.roots.sandbox.worktreeRoot) fail('prepared namespace source roots differ from the verified postmerge snapshot');
}
function assertSourceRootBindings(source, input) {
  if (source.operationId !== input.operationId || source.roots.stable.worktreeRoot !== input.stableRoot
      || source.roots.sandbox.worktreeRoot !== input.sandboxRoot) fail('requested source identities differ from the verified operation roots');
}
function releaseTreeInventory(releaseRoot) {
  return captureTree(releaseRoot, { maxFiles: MAX_TREE_FILES, maxBytes: MAX_RELEASE_TREE_BYTES });
}
function assertCanonicalRecordSize(record, label) {
  if (Buffer.byteLength(canonical(record) + '\n') > MAX_RECORD_BYTES) fail(`${label} exceeds the canonical record limit`);
}
function allocationBytes(files) {
  return files.reduce((sum, file) => sum + Math.ceil(file.byteLength / ALLOCATION_UNIT) * ALLOCATION_UNIT, 0);
}
function fileBytes(files) { return files.reduce((sum, file) => sum + file.byteLength, 0); }
function makeBinding({ namespace, source, manifest, operation, input, storeId }) {
  const releaseRoot = path.join(namespace.paths.releases, input.releaseId);
  const body = {
    format: 1,
    mode: BINDING_MODE,
    installationId: input.installationId,
    namespace: {
      mode: namespace.mode,
      root: namespace.root,
      digest: projectNamespaceDigest(namespace),
      securityPolicy: 'logical_separation_only',
      credentialsPolicy: 'forbidden',
    },
    source,
    release: {
      releaseId: input.releaseId,
      manifestDigest: input.releaseId,
      root: releaseRoot,
      buildReceiptDigest: operation.build.receiptDigest,
      toolchainDigest: operation.build.toolchainDigest,
      buildPolicy: 'configless_snapshot_v2',
    },
    paths: { ...contextPaths(namespace, input.releaseId) },
    data: { storeId, schema: 'managed_empty_store_v1', revision: 0, policy: 'empty_no_migration' },
    access: 'read_only',
    runtimeRegistration: null,
    writerLease: null,
    readyForLaunch: false,
  };
  const binding = seal(body, 'bindingDigest');
  validateBinding(binding, { namespace, source, manifest, operation, input });
  return binding;
}
function validateBinding(binding, { namespace, source, manifest, operation, input }) {
  validateBindingBase(binding, namespace, input);
  if (!same(binding.source, source)) fail('production binding source provenance mismatch');
  if (binding.release.buildReceiptDigest !== operation.build.receiptDigest
      || binding.release.toolchainDigest !== operation.build.toolchainDigest) fail('production binding release receipt mismatch');
  if (manifest.mode !== 'managed_postmerge_release_v1' || manifest.readyForLaunch !== false
      || manifest.data.policy !== binding.data.policy || manifest.data.schema !== binding.data.schema || manifest.data.revisionBefore !== 0) fail('production binding cannot consume fixture or launchable release data');
  return binding;
}
function validateBindingBase(binding, namespace, input) {
  const keys = ['format', 'mode', 'installationId', 'namespace', 'source', 'release', 'paths', 'data', 'access', 'runtimeRegistration', 'writerLease', 'readyForLaunch', 'bindingDigest'];
  if (!exact(binding, keys) || binding.format !== 1 || binding.mode !== BINDING_MODE || binding.installationId !== input.installationId
      || binding.access !== 'read_only' || binding.runtimeRegistration !== null || binding.writerLease !== null || binding.readyForLaunch !== false) fail('production binding schema or readiness invalid');
  const body = { ...binding }; delete body.bindingDigest;
  if (!HASH.test(binding.bindingDigest || '') || digest(body) !== binding.bindingDigest) fail('production binding digest invalid');
  if (!exact(binding.namespace, ['mode', 'root', 'digest', 'securityPolicy', 'credentialsPolicy'])
      || binding.namespace.mode !== 'managed_project_local_v1' || binding.namespace.root !== namespace.root
      || binding.namespace.digest !== projectNamespaceDigest(namespace)
      || binding.namespace.securityPolicy !== 'logical_separation_only' || binding.namespace.credentialsPolicy !== 'forbidden') fail('production binding namespace is not the approved project-local identity');
  if (!exact(binding.source, SOURCE_KEYS) || binding.source.operationId !== input.operationId
      || !HASH.test(binding.source.planDigest || '') || !HASH.test(binding.source.receiptDigest || '')
      || !HASH.test(binding.source.snapshotDigest || '') || !GIT_OID.test(binding.source.mergedHead || '')
      || !GIT_OID.test(binding.source.mergedTree || '') || binding.source.mergedHead.length !== binding.source.mergedTree.length
      || !exact(binding.source.roots, ROOT_KEYS)
      || ROOT_KEYS.some(side => {
        const root = binding.source.roots[side];
        return !exact(root, SNAPSHOT_ROOT_KEYS) || ![root.worktreeRoot, root.gitDir, root.commonDir].every(value =>
          typeof value === 'string' && path.isAbsolute(value) && path.resolve(value) === value)
          || typeof root.branch !== 'string' || !root.branch;
      })) fail('production binding source identity schema invalid');
  if (binding.source.roots.stable.worktreeRoot !== namespace.source_roots.stable
      || binding.source.roots.sandbox.worktreeRoot !== namespace.source_roots.sandbox) fail('production binding source roots differ from namespace marker');
  if (!exact(binding.release, ['releaseId', 'manifestDigest', 'root', 'buildReceiptDigest', 'toolchainDigest', 'buildPolicy'])
      || binding.release.releaseId !== input.releaseId || binding.release.manifestDigest !== input.releaseId
      || binding.release.root !== path.join(namespace.paths.releases, input.releaseId)
      || !HASH.test(binding.release.buildReceiptDigest || '') || !HASH.test(binding.release.toolchainDigest || '')
      || binding.release.buildPolicy !== 'configless_snapshot_v2') fail('production binding release provenance mismatch');
  const expectedPaths = contextPaths(namespace, input.releaseId);
  if (!exact(binding.paths, ['launcher', 'state', 'hostState', 'hostProfile', 'hostWorkspace', 'data', 'code']) || !same(binding.paths, expectedPaths)) fail('production binding path map differs from the fixed namespace layout');
  if (!exact(binding.data, ['storeId', 'schema', 'revision', 'policy']) || !UUID.test(binding.data.storeId || '')
      || binding.data.schema !== 'managed_empty_store_v1' || binding.data.revision !== 0 || binding.data.policy !== 'empty_no_migration') fail('production binding data identity is not a new empty store');
  return binding;
}
function makeIntent({ input, namespace, binding }) {
  const body = {
    format: 1, mode: INTENT_MODE, installationId: input.installationId,
    operationId: input.operationId, releaseId: input.releaseId,
    namespaceDigest: projectNamespaceDigest(namespace), bindingDigest: binding.bindingDigest,
    dataStoreId: binding.data.storeId, state: 'copy_started', readyForLaunch: false,
  };
  const intent = seal(body, 'intentDigest');
  assertCanonicalRecordSize(intent, 'production context intent');
  return intent;
}
function makeReceipt({ input, namespace, binding, manifest, inventory, payloadBytes, allocation }) {
  const body = {
    format: 1, mode: RECEIPT_MODE, installationId: input.installationId,
    operationId: input.operationId, releaseId: input.releaseId,
    bindingDigest: binding.bindingDigest, namespaceDigest: projectNamespaceDigest(namespace),
    manifestDigest: input.releaseId, dataStoreId: binding.data.storeId,
    filesDigest: digest(inventory), payloadBytes, allocationBytes: allocation,
    readyForLaunch: false,
  };
  const receipt = seal(body);
  assertCanonicalRecordSize(receipt, 'production context receipt');
  if (!Array.isArray(manifest.files)) fail('release manifest payload inventory missing');
  return receipt;
}
function validateIntent(intent, expected) {
  const keys = ['format', 'mode', 'installationId', 'operationId', 'releaseId', 'namespaceDigest', 'bindingDigest', 'dataStoreId', 'state', 'readyForLaunch', 'intentDigest'];
  if (!exact(intent, keys) || intent.format !== 1 || intent.mode !== INTENT_MODE || intent.installationId !== expected.input.installationId
      || intent.operationId !== expected.input.operationId || intent.releaseId !== expected.input.releaseId
      || intent.namespaceDigest !== expected.namespaceDigest || intent.bindingDigest !== expected.binding.bindingDigest
      || intent.dataStoreId !== expected.binding.data.storeId || intent.state !== 'copy_started' || intent.readyForLaunch !== false) fail('production context intent binding invalid');
  const body = { ...intent }; delete body.intentDigest;
  if (!HASH.test(intent.intentDigest || '') || digest(body) !== intent.intentDigest) fail('production context intent digest invalid');
  return intent;
}
function validateReceipt(receipt, expected) {
  const keys = ['format', 'mode', 'installationId', 'operationId', 'releaseId', 'bindingDigest', 'namespaceDigest', 'manifestDigest', 'dataStoreId', 'filesDigest', 'payloadBytes', 'allocationBytes', 'readyForLaunch', 'receiptDigest'];
  if (!exact(receipt, keys) || receipt.format !== 1 || receipt.mode !== RECEIPT_MODE || receipt.installationId !== expected.input.installationId
      || receipt.operationId !== expected.input.operationId || receipt.releaseId !== expected.input.releaseId
      || receipt.bindingDigest !== expected.binding.bindingDigest || receipt.namespaceDigest !== expected.namespaceDigest
      || receipt.manifestDigest !== expected.input.releaseId || receipt.dataStoreId !== expected.binding.data.storeId
      || receipt.filesDigest !== digest(expected.inventory) || receipt.payloadBytes !== expected.payloadBytes
      || receipt.allocationBytes !== expected.allocation || receipt.readyForLaunch !== false) fail('production context receipt binding invalid');
  const body = { ...receipt }; delete body.receiptDigest;
  if (!HASH.test(receipt.receiptDigest || '') || digest(body) !== receipt.receiptDigest) fail('production context receipt digest invalid');
  return receipt;
}
function descriptorFor(input, namespace, binding) {
  return {
    format: 1, mode: DESCRIPTOR_MODE, installationId: input.installationId,
    operationId: input.operationId, releaseId: input.releaseId,
    root: namespace.root, bindingDigest: binding.bindingDigest, readyForLaunch: false,
  };
}
function validateManifest(operation, releaseDescriptor, manifest, input) {
  if (!exact(manifest, MANIFEST_KEYS) || manifest.format !== 1 || manifest.mode !== 'managed_postmerge_release_v1'
      || manifest.protocol !== 1 || manifest.readyForLaunch !== false || digest(manifest) !== input.releaseId
      || releaseDescriptor.releaseId !== input.releaseId || releaseDescriptor.manifestDigest !== input.releaseId) fail('verified V2 release manifest identity invalid');
  const source = sourceBinding(operation);
  const expectedManifestSource = {
    snapshotDigest: source.snapshotDigest, planDigest: source.planDigest,
    receiptDigest: source.receiptDigest, mergedHead: source.mergedHead, mergedTree: source.mergedTree,
  };
  if (!same(manifest.source, expectedManifestSource)
      || !same(manifest.build, { receiptDigest: operation.build.receiptDigest, toolchainDigest: operation.build.toolchainDigest, policy: 'configless_snapshot_v2' })
      || !same(manifest.data, { policy: 'empty_no_migration', schema: 'managed_empty_store_v1', revisionBefore: 0 })) fail('verified V2 release provenance differs from its source/build operation');
  if (!Array.isArray(manifest.files) || !manifest.files.length || manifest.files.length > MAX_RELEASE_FILES) fail('verified release file inventory is invalid');
  const payloadBytes = fileBytes(manifest.files);
  if (payloadBytes > MAX_RELEASE_BYTES) fail('verified release payload exceeds the production context byte bound');
  return { source, payloadBytes };
}
function readVerifiedSource(input, namespace) {
  const operationInput = { sandboxRoot: input.sandboxRoot, operationId: input.operationId };
  const materialized = readMaterializedOperation(operationInput);
  const operation = readVerifiedBuildOperation(operationInput);
  if (materialized.snapshot.snapshotDigest !== operation.snapshot.snapshotDigest
      || materialized.materialized.receiptDigest !== operation.materialized.receiptDigest) fail('materialized source changed between provenance readers');
  const evidence = readSelectedMergeEvidence({ sandboxRoot: input.sandboxRoot, planDigest: operation.snapshot.planDigest });
  const recaptured = captureManagedPostmergeSource({
    stableRoot: input.stableRoot, sandboxRoot: input.sandboxRoot,
    plan: evidence.plan, receipt: evidence.receipt, gitExecutable: TRUSTED_GIT_EXECUTABLE,
  });
  if (!same(recaptured, operation.snapshot)) fail('current stable/sandbox Git source identity or committed source snapshot differs from the verified V2 build');
  const releaseDescriptor = verifyManagedPostmergeRelease({ ...operationInput, releaseId: input.releaseId });
  const manifest = readCanonical(path.join(releaseDescriptor.root, 'manifest.json'), 4 *  1024 * 1024);
  const validated = validateManifest(operation, releaseDescriptor, manifest, input);
  assertSourceRootBindings(validated.source, input);
  if (namespace) assertSourceNamespaceRoots(namespace, validated.source);
  const inventory = releaseTreeInventory(releaseDescriptor.root);
  if (fileBytes(inventory) > MAX_RELEASE_TREE_BYTES) fail('complete sealed release exceeds the production copy bound');
  return { operation, materialized, releaseDescriptor, manifest, source: validated.source, payloadBytes: validated.payloadBytes, inventory };
}

function names(dir) { return readdirSync(dir).sort(comparePath); }
function expectNames(dir, expected, label) {
  if (!same(names(dir), [...expected].sort(comparePath))) fail(`${label} has an unknown or incomplete entry set`);
}
function regularEntry(target, maxBytes, label) {
  const value = regularFile(target, maxBytes);
  const stat = lstatSync(value);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1 || (process.platform === 'win32' && (stat.attributes & 0x400))) fail(`${label} must be a single-link regular file`);
  return stat;
}
function assertOperationalFiles(root, { maxFiles = 4096, maxBytes = 256 * 1024 * 1024 } = {}) {
  let files = 0, bytes = 0;
  const walk = (directory) => {
    for (const name of names(directory)) {
      if (!safeRelative(name)) fail('operational tree contains an unsafe path');
      const target = path.join(directory, name), info = lstatSync(target);
      if (info.isSymbolicLink() || (process.platform === 'win32' && (info.attributes & 0x400))) fail('operational tree contains a link');
      if (info.isDirectory()) { walk(target); continue; }
      if (!info.isFile() || info.nlink !== 1 || ++files > maxFiles || (bytes += info.size) > maxBytes) fail('operational tree exceeds its file or byte bound');
    }
  };
  walk(root);
  return { files, bytes };
}
function assertOperationalStore(root) {
  const store = path.join(root, 'data', 'store');
  const entries = names(store);
  const permitted = ['application', 'managed-data.sqlite3'];
  if (entries.some(entry => !permitted.includes(entry)) || entries.includes('managed-data.sqlite3') && !lstatSync(path.join(store, 'managed-data.sqlite3')).isFile()) fail('managed operational store entries rejected');
  if (entries.includes('application')) {
    const app = path.join(store, 'application');
    expectNames(app, ['events'], 'application store');
    const events = path.join(app, 'events');
    for (const name of names(events)) {
      if (!/^[0-9]{8}\.json$/.test(name)) fail('managed application event name rejected');
      regularEntry(path.join(events, name), 64 * 1024, 'managed application event');
    }
    assertOperationalFiles(app, { maxFiles: 8192, maxBytes: 32 * 1024 * 1024 });
  }
  if (entries.includes('managed-data.sqlite3')) regularEntry(path.join(store, 'managed-data.sqlite3'), 64 * 1024 * 1024, 'managed data database');
}
function validateHostProfileIntent(intent, binding, runtimeDigest) {
  const keys = ['format', 'mode', 'installationId', 'releaseId', 'bindingDigest', 'runtimeDigest', 'hostNonce', 'profileRoot', 'access', 'intentDigest'];
  if (!exact(intent, keys)) fail('managed Host profile intent fields rejected');
  const body = { ...intent }; delete body.intentDigest;
  if (intent.format !== 1 || intent.mode !== 'managed_host_profile_intent_v1'
      || intent.installationId !== binding.installationId || intent.releaseId !== binding.release.releaseId
      || intent.bindingDigest !== binding.bindingDigest || intent.runtimeDigest !== runtimeDigest
      || !UUID.test(intent.hostNonce || '') || intent.profileRoot !== binding.paths.hostProfile
      || !['read_only', 'fenced_application'].includes(intent.access) || intent.intentDigest !== digest(body)) {
    fail('managed Host profile intent binding rejected');
  }
  return intent;
}
function assertTreeMetadata(root, { input, includePublishedRecords, includeRelease, operational = false }) {
  safeDirectory(root);
  expectNames(root, ['data', 'host', 'launcher', 'releases', 'state'], 'production namespace root');
  for (const rel of BASE_DIRECTORIES) isPlainDirectory(path.join(root, ...rel.split('/')));
  expectNames(path.join(root, 'host'), ['profile', 'state', 'workspace'], 'Host root');
  const launcher = path.join(root, 'launcher');
  const launcherNames = names(launcher);
  const runtimeRecordNames = ['runtime-intent.json', 'runtime-registration.json', 'runtime-receipt.json'].sort(comparePath);
  const hasRuntimeRecords = launcherNames.length > 0;
  if (hasRuntimeRecords && (!includePublishedRecords || !same(launcherNames, runtimeRecordNames))) fail('launcher has an incomplete or unknown runtime record set');
  if (hasRuntimeRecords) {
    for (const name of runtimeRecordNames) {
      const maxBytes = name === 'runtime-registration.json' ? MAX_RUNTIME_REGISTRATION_BYTES : MAX_RECORD_BYTES;
      regularEntry(path.join(launcher, name), maxBytes, `launcher/${name}`);
    }
  }
  for (const rel of ['host/workspace']) expectNames(path.join(root, ...rel.split('/')), [], rel);
  if (!operational) expectNames(path.join(root, 'host/state'), [], 'host/state');
  else expectNames(path.join(root, 'host/state'), ['managed-host-profile-intent.json'], 'host/state');
  if (!operational) for (const rel of ['host/profile', 'data/store']) expectNames(path.join(root, ...rel.split('/')), [], rel);
  else {
    const profile = path.join(root, 'host', 'profile');
    const profileNames = names(profile);
    if (profileNames.length) expectNames(profile, ['electron-sessionData', 'electron-userData', 'tmp'], 'managed Host profile');
    for (const name of names(profile)) {
      const directory = path.join(profile, name);
      isPlainDirectory(directory);
      assertOperationalFiles(directory);
    }
    assertOperationalStore(root);
  }
  expectNames(path.join(root, 'data'), ['store'], 'data root');
  for (const rel of ['state/managed-namespace.json', INTENT_PATH]) regularEntry(path.join(root, ...rel.split('/')), MAX_RECORD_BYTES, rel);
  const baseStateNames = includePublishedRecords
    ? ['managed-namespace.json', 'production-context-intent.json', 'production-binding.json', 'production-context-receipt.json']
    : ['managed-namespace.json', 'production-context-intent.json'];
  const stateNames = operational ? [...baseStateNames, 'managed-store-control.sqlite3', 'managed-store.lock'].filter(name => existsSync(path.join(root, 'state', name))) : baseStateNames;
  if (includePublishedRecords) for (const rel of [BINDING_PATH, RECEIPT_PATH]) regularEntry(path.join(root, ...rel.split('/')), MAX_RECORD_BYTES, rel);
  if (operational) {
    for (const rel of ['state/managed-store-control.sqlite3', 'state/managed-store.lock']) {
      const target = path.join(root, ...rel.split('/'));
      if (existsSync(target)) regularEntry(target, rel.endsWith('.sqlite3') ? 16 * 1024 * 1024 : 4096, rel);
    }
  }
  expectNames(path.join(root, 'state'), stateNames, 'production state directory');
  if (!includeRelease) {
    expectNames(path.join(root, 'releases'), [], 'release directory');
    return;
  }
  expectNames(path.join(root, 'releases'), [input.releaseId], 'release directory');
  const releaseRoot = path.join(root, 'releases', input.releaseId);
  isPlainDirectory(releaseRoot);
  expectNames(releaseRoot, ['app', 'manifest.json', 'seal-intent.json', 'sealed.json'], 'installed release root');
  isPlainDirectory(path.join(releaseRoot, 'app'));
  for (const rel of ['manifest.json', 'seal-intent.json', 'sealed.json']) regularEntry(path.join(releaseRoot, rel), MAX_RECORD_BYTES, `installed release ${rel}`);
  const files = [];
  let entries = 0, logical = 0;
  const walk = (dir, prefix = '') => {
    for (const name of names(dir)) {
      if (++entries > 60_000) fail('installed release directory entry bound exceeded');
      if (!safeRelative(name)) fail('installed release contains an unsafe path component');
      const child = path.join(dir, name), relative = prefix ? `${prefix}/${name}` : name;
      const stat = lstatSync(child);
      if (stat.isSymbolicLink() || (process.platform === 'win32' && (stat.attributes & 0x400))) fail('installed release contains a link or reparse point');
      if (stat.isDirectory()) { walk(child, relative); continue; }
      if (!stat.isFile() || stat.nlink !== 1 || files.length >= MAX_RELEASE_FILES || (logical += stat.size) > MAX_RELEASE_BYTES) fail('installed application payload file/count/size bound exceeded');
      files.push({ path: relative, byteLength: stat.size });
    }
  };
  walk(path.join(releaseRoot, 'app'));
  if (!files.length) fail('installed release application payload is empty');
}
function readNamespaceMarker(root, input) {
  const marker = readCanonical(path.join(root, ...MARKER_PATH.split('/')), 16 * 1024);
  const expectedPaths = {
    launcher: path.join(root, 'launcher'), state: path.join(root, 'state'), releases: path.join(root, 'releases'),
    host_state: path.join(root, 'host', 'state'), host_profile: path.join(root, 'host', 'profile'),
    host_workspace: path.join(root, 'host', 'workspace'), data: path.join(root, 'data', 'store'),
  };
  if (!exact(marker, ['format', 'mode', 'stage', 'data_policy', 'security_policy', 'credentials_policy', 'installation_id', 'root', 'source_roots', 'paths'])
      || marker.format !== 1 || marker.mode !== 'managed_project_local_v1' || marker.stage !== 'prepared_empty'
      || marker.data_policy !== 'empty_no_migration' || marker.security_policy !== 'logical_separation_only'
      || marker.credentials_policy !== 'forbidden' || marker.installation_id !== input.installationId || marker.root !== root
      || !same(marker.source_roots, { stable: input.stableRoot, sandbox: input.sandboxRoot }) || !same(marker.paths, expectedPaths)) fail('project-local namespace marker is not the exact prepared production identity');
  return marker;
}
function assertProductionRecords({ input, namespaceMarker, source, manifest, operation, inventory, payloadBytes, allocation, binding, intent, receipt }) {
  const namespace = namespaceMarker;
  const namespaceDigest = projectNamespaceDigest(namespace);
  const expected = { input, namespaceDigest, binding, inventory, payloadBytes, allocation };
  validateBinding(binding, { namespace, source, manifest, operation, input });
  validateIntent(intent, { input, namespaceDigest, binding });
  validateReceipt(receipt, expected);
  if (receipt.payloadBytes !== payloadBytes || receipt.allocationBytes !== allocation) fail('context receipt budget values differ from the verified release');
  return descriptorFor(input, namespace, binding);
}
function assertCopiedRelease(root, releaseId, inventory) {
  const releaseRoot = path.join(root, 'releases', releaseId);
  const actual = verifyTree(releaseRoot, inventory, { maxFiles: MAX_TREE_FILES, maxBytes: MAX_RELEASE_TREE_BYTES });
  if (!same(actual, inventory)) fail('copied release inventory differs from the sealed source');
  return actual;
}
function assertMarkerUnchanged(root, input, expectedMarker) {
  const current = readNamespaceMarker(root, input);
  if (projectNamespaceDigest(current) !== projectNamespaceDigest(expectedMarker)) fail('prepared namespace marker changed during production context preparation');
}
function assertPreparationBudget({ inventory, intent, binding, receipt, payloadBytes }) {
  if (payloadBytes > MAX_RELEASE_BYTES) fail('release payload exceeds 64 MiB');
  const additional = [intent, binding, receipt].map(record => ({ byteLength: Buffer.byteLength(canonical(record) + '\n') }));
  const logical = fileBytes(inventory) + fileBytes(additional);
  const allocation = allocationBytes(inventory) + allocationBytes(additional);
  if (logical > MAX_RELEASE_TREE_BYTES + 3 * MAX_RECORD_BYTES || allocation > MAX_ALLOCATED_BYTES) fail('production context copy exceeds its logical/allocation budget');
  return allocation;
}
function finalizeReceipt({ input, namespace, binding, manifest, inventory, payloadBytes, intent }) {
  let allocation = allocationBytes(inventory) + allocationBytes([intent, binding].map(record => ({ byteLength: Buffer.byteLength(canonical(record) + '\n') })))
    + ALLOCATION_UNIT;
  for (let attempt = 0; attempt < 4; attempt++) {
    const receipt = makeReceipt({ input, namespace, binding, manifest, inventory, payloadBytes, allocation });
    const nextAllocation = assertPreparationBudget({ inventory, intent, binding, receipt, payloadBytes });
    if (nextAllocation === allocation) return { receipt, allocation };
    allocation = nextAllocation;
  }
  fail('production context allocation receipt did not converge');
}
function copyRelease(sourceRoot, targetRoot, inventory) {
  mkdirSync(targetRoot);
  safeDirectory(targetRoot);
  syncDirectory(path.dirname(targetRoot));
  for (const entry of inventory) {
    if (!safeRelative(entry.path)) fail('source release inventory contains an unsafe relative path');
    const source = path.join(sourceRoot, ...entry.path.split('/'));
    regularEntry(source, MAX_RELEASE_TREE_BYTES, `source release ${entry.path}`);
    const bytes = readStableBytes(source, MAX_RELEASE_TREE_BYTES);
    if (bytes.length !== entry.byteLength || sha(bytes) !== entry.sha256) fail(`source release changed before copy: ${entry.path}`);
    const target = path.join(targetRoot, ...entry.path.split('/'));
    if (!target.startsWith(targetRoot + path.sep)) fail('release copy target escaped its fixed root');
    const parentRelative = path.relative(targetRoot, path.dirname(target));
    if (parentRelative) {
      let current = targetRoot;
      for (const part of parentRelative.split(path.sep)) {
        current = path.join(current, part);
        if (!existsSync(current)) { mkdirSync(current); syncDirectory(path.dirname(current)); }
        isPlainDirectory(current);
      }
    }
    writeBytesExclusive(target, bytes, MAX_RELEASE_TREE_BYTES);
    chmodSync(target, entry.mode === '100755' ? 0o755 : 0o644);
    syncDirectory(path.dirname(target));
    const copiedStat = regularEntry(target, MAX_RELEASE_TREE_BYTES, `copied release ${entry.path}`);
    const mode = (copiedStat.mode & 0o111) ? '100755' : '100644';
    if (mode !== entry.mode) fail(`copied release mode differs from the sealed source: ${entry.path}`);
  }
  verifyTree(targetRoot, inventory, { maxFiles: MAX_TREE_FILES, maxBytes: MAX_RELEASE_TREE_BYTES });
  syncDirectory(targetRoot);
}
function wrap(error) {
  if (error instanceof Error && error.message.startsWith(PREFIX)) return error;
  return new TypeError(PREFIX + (error instanceof Error ? error.message : 'operation refused'));
}

/** Prepare an inert, digest-bound production context from a verified V2 postmerge release. */
export async function prepareManagedProductionContext(rawInput) {
  try {
    const input = validateInput(rawInput);
    const namespace = await readProjectNamespace(namespaceInput(input));
    const namespaceRoot = projectNamespacePath(input.sandboxRoot, input.installationId);
    if (namespace.root !== namespaceRoot || namespace.installation_id !== input.installationId || namespace.mode !== 'managed_project_local_v1') fail('prepared namespace identity differs from fixed project-local root');
    const verified = readVerifiedSource(input, namespace);
    const namespaceMarker = readNamespaceMarker(namespaceRoot, input);
    const storeId = randomUUID();
    const binding = makeBinding({ namespace, source: verified.source, manifest: verified.manifest, operation: verified.operation, input, storeId });
    const intent = makeIntent({ input, namespace, binding });
    const finalized = finalizeReceipt({ input, namespace, binding, manifest: verified.manifest, inventory: verified.inventory, payloadBytes: verified.payloadBytes, intent });
    const receipt = finalized.receipt;
    assertCanonicalRecordSize(binding, 'production binding');
    assertCanonicalRecordSize(receipt, 'production context receipt');
    const finalAllocation = assertPreparationBudget({ inventory: verified.inventory, intent, binding, receipt, payloadBytes: verified.payloadBytes });
    if (finalAllocation !== finalized.allocation) fail('production context allocation estimate changed before intent');
    const intentFile = path.join(namespace.paths.state, 'production-context-intent.json');
    writeCanonicalExclusive(intentFile, intent);
    const targetRoot = path.join(namespace.paths.releases, input.releaseId);
    copyRelease(verified.releaseDescriptor.root, targetRoot, verified.inventory);
    assertCopiedRelease(namespaceRoot, input.releaseId, verified.inventory);
    assertTreeMetadata(namespaceRoot, { input, includePublishedRecords: false, includeRelease: true });
    assertMarkerUnchanged(namespaceRoot, input, namespaceMarker);
    const fresh = readVerifiedSource(input, namespace);
    if (!same(fresh.source, verified.source)
        || !same(fresh.manifest, verified.manifest) || !same(fresh.inventory, verified.inventory)
        || fresh.releaseDescriptor.root !== verified.releaseDescriptor.root) fail('verified V2 source/release changed during the production payload copy');
    assertMarkerUnchanged(namespaceRoot, input, namespaceMarker);
    writeCanonicalExclusive(path.join(namespace.paths.state, 'production-binding.json'), binding);
    writeCanonicalExclusive(path.join(namespace.paths.state, 'production-context-receipt.json'), receipt);
    assertTreeMetadata(namespaceRoot, { input, includePublishedRecords: true, includeRelease: true });
    const persistedBinding = readCanonical(path.join(namespace.paths.state, 'production-binding.json'), MAX_RECORD_BYTES);
    const persistedIntent = readCanonical(intentFile, MAX_RECORD_BYTES);
    const persistedReceipt = readCanonical(path.join(namespace.paths.state, 'production-context-receipt.json'), MAX_RECORD_BYTES);
    assertProductionRecords({ input, namespaceMarker, source: verified.source, manifest: verified.manifest, operation: verified.operation,
      inventory: verified.inventory, payloadBytes: verified.payloadBytes, allocation: finalAllocation,
      binding: persistedBinding, intent: persistedIntent, receipt: persistedReceipt });
    assertCopiedRelease(namespaceRoot, input.releaseId, verified.inventory);
    return descriptorFor(input, namespace, persistedBinding);
  } catch (error) { throw wrap(error); }
}

/** Read and revalidate a complete inert production context without mutating it. */
function readManagedProductionContextSync(rawInput) {
  try {
    const input = validateInput(rawInput);
    const namespaceRoot = projectNamespacePath(input.sandboxRoot, input.installationId);
    // Validate the entire allowed directory shape and require Host/data to remain empty before content reads.
    assertTreeMetadata(namespaceRoot, { input, includePublishedRecords: true, includeRelease: true });
    const namespaceMarker = readNamespaceMarker(namespaceRoot, input);
    const namespace = {
      format: namespaceMarker.format, mode: namespaceMarker.mode, stage: namespaceMarker.stage,
      data_policy: namespaceMarker.data_policy, security_policy: namespaceMarker.security_policy,
      credentials_policy: namespaceMarker.credentials_policy, installation_id: namespaceMarker.installation_id,
      root: namespaceMarker.root, source_roots: namespaceMarker.source_roots, paths: namespaceMarker.paths,
    };
    const verified = readVerifiedSource(input, namespace);
    const targetRoot = path.join(namespaceRoot, 'releases', input.releaseId);
    const installedInventory = verifyTree(targetRoot, verified.inventory, { maxFiles: MAX_TREE_FILES, maxBytes: MAX_RELEASE_TREE_BYTES });
    if (!same(installedInventory, verified.inventory)) fail('installed release differs from the sealed source inventory');
    const installedManifest = readCanonical(path.join(targetRoot, 'manifest.json'), 4 * 1024 * 1024);
    if (!same(installedManifest, verified.manifest)) fail('installed release manifest differs from the verified source manifest');
    const binding = readCanonical(path.join(namespaceRoot, ...BINDING_PATH.split('/')), MAX_RECORD_BYTES);
    const intent = readCanonical(path.join(namespaceRoot, ...INTENT_PATH.split('/')), MAX_RECORD_BYTES);
    const receipt = readCanonical(path.join(namespaceRoot, ...RECEIPT_PATH.split('/')), MAX_RECORD_BYTES);
    const allocation = assertPreparationBudget({ inventory: verified.inventory, intent, binding, receipt, payloadBytes: verified.payloadBytes });
    const descriptor = assertProductionRecords({ input, namespaceMarker, source: verified.source, manifest: installedManifest,
      operation: verified.operation, inventory: installedInventory, payloadBytes: verified.payloadBytes,
      allocation, binding, intent, receipt });
    const launcherNames = names(path.join(namespaceRoot, 'launcher'));
    let runtimeRegistration = null;
    if (launcherNames.length) {
      runtimeRegistration = validateManagedRuntimeRegistrationRecords(path.join(namespaceRoot, 'launcher'), {
        installationId: binding.installationId,
        releaseId: binding.release.releaseId,
        bindingDigest: binding.bindingDigest,
      });
      const runtimeInputs = Object.fromEntries(Object.entries(runtimeRegistration.runtimes).map(([name, item]) => [name, { root: item.root, entry: item.entry }]));
      if (!same(captureManagedRuntimeInputs(runtimeInputs), runtimeRegistration.runtimes)) fail('registered runtime inventory changed');
    }
    return {
      descriptor, binding, manifest: installedManifest,
      runtimeRegistered: runtimeRegistration !== null,
      runtimeDigest: runtimeRegistration?.runtimeDigest ?? null,
      runtimeRegistration,
    };
  } catch (error) { throw wrap(error); }
}

/** Promise-compatible public wrapper; validation itself is synchronous. */
export async function readManagedProductionContext(rawInput) {
  return readManagedProductionContextSync(rawInput);
}

/** Revalidate immutable production identity after the known Host profile/store layout has been initialized. */
function readManagedOperationalProductionContextSync(rawInput) {
  try {
    const input = validateInput(rawInput);
    const namespaceRoot = projectNamespacePath(input.sandboxRoot, input.installationId);
    assertTreeMetadata(namespaceRoot, { input, includePublishedRecords: true, includeRelease: true, operational: true });
    const namespaceMarker = readNamespaceMarker(namespaceRoot, input);
    const namespace = {
      format: namespaceMarker.format, mode: namespaceMarker.mode, stage: namespaceMarker.stage,
      data_policy: namespaceMarker.data_policy, security_policy: namespaceMarker.security_policy,
      credentials_policy: namespaceMarker.credentials_policy, installation_id: namespaceMarker.installation_id,
      root: namespaceMarker.root, source_roots: namespaceMarker.source_roots, paths: namespaceMarker.paths,
    };
    const verified = readVerifiedSource(input, namespace);
    const releaseRoot = path.join(namespaceRoot, 'releases', input.releaseId);
    const inventory = verifyTree(releaseRoot, verified.inventory, { maxFiles: MAX_TREE_FILES, maxBytes: MAX_RELEASE_TREE_BYTES });
    if (!same(inventory, verified.inventory)) fail('installed release differs from sealed source');
    const manifest = readCanonical(path.join(releaseRoot, 'manifest.json'), 4 * 1024 * 1024);
    if (!same(manifest, verified.manifest)) fail('installed manifest differs from verified source');
    const binding = readCanonical(path.join(namespaceRoot, ...BINDING_PATH.split('/')), MAX_RECORD_BYTES);
    const intent = readCanonical(path.join(namespaceRoot, ...INTENT_PATH.split('/')), MAX_RECORD_BYTES);
    const receipt = readCanonical(path.join(namespaceRoot, ...RECEIPT_PATH.split('/')), MAX_RECORD_BYTES);
    const allocation = assertPreparationBudget({ inventory, intent, binding, receipt, payloadBytes: verified.payloadBytes });
    const descriptor = assertProductionRecords({ input, namespaceMarker, source: verified.source, manifest,
      operation: verified.operation, inventory, payloadBytes: verified.payloadBytes, allocation, binding, intent, receipt });
    const registration = validateManagedRuntimeRegistrationRecords(path.join(namespaceRoot, 'launcher'), {
      installationId: binding.installationId, releaseId: binding.release.releaseId, bindingDigest: binding.bindingDigest,
    });
    const runtimeInputs = Object.fromEntries(Object.entries(registration.runtimes).map(([name, item]) => [name, { root: item.root, entry: item.entry }]));
    const actualRuntimes = captureManagedRuntimeInputs(runtimeInputs);
    if (!same(actualRuntimes, registration.runtimes)) fail('registered runtime inventory changed');
    const profileIntent = validateHostProfileIntent(readCanonical(path.join(namespaceRoot, 'host/state/managed-host-profile-intent.json'), MAX_RECORD_BYTES), binding, registration.runtimeDigest);
    return { descriptor, binding, manifest, runtimeRegistered: true, runtimeDigest: registration.runtimeDigest, runtimeRegistration: registration, profileIntent };
  } catch (error) { throw wrap(error); }
}

/** Promise-compatible public wrapper; validation itself is synchronous. */
export async function readManagedOperationalProductionContext(rawInput) {
  return readManagedOperationalProductionContextSync(rawInput);
}

/** Bind this launch's explicit access mode and Host nonce to the owned profile before any profile output is created. */
export function prepareManagedHostProfileIntentSync(rawInput, request) {
  try {
    if (!exact(request, ['access', 'hostNonce']) || !['read_only', 'fenced_application'].includes(request.access)
        || typeof request.hostNonce !== 'string' || !UUID.test(request.hostNonce)) fail('Host profile intent request rejected');
    const input = validateInput(rawInput);
    const namespaceRoot = projectNamespacePath(input.sandboxRoot, input.installationId);
    const intentPath = path.join(namespaceRoot, 'host', 'state', 'managed-host-profile-intent.json');
    const current = existsSync(intentPath)
      ? readManagedOperationalProductionContextSync(input)
      : readManagedProductionContextSync(input);
    if (!current.runtimeRegistered || !current.runtimeDigest) fail('registered runtime required for Host profile intent');
    const body = {
      format: 1, mode: 'managed_host_profile_intent_v1', installationId: input.installationId,
      releaseId: input.releaseId, bindingDigest: current.binding.bindingDigest, runtimeDigest: current.runtimeDigest,
      hostNonce: request.hostNonce, profileRoot: current.binding.paths.hostProfile, access: request.access,
    };
    const intent = { ...body, intentDigest: digest(body) };
    const temporary = path.join(path.dirname(intentPath), '.managed-host-profile-intent-' + randomUUID() + '.tmp');
    writeCanonicalExclusive(temporary, intent);
    renameSync(temporary, intentPath);
    syncDirectory(path.dirname(intentPath));
    const validated = readManagedOperationalProductionContextSync(input);
    if (!same(validated.profileIntent, intent)) fail('published Host profile intent changed');
    return { ...validated, profileIntent: intent };
  } catch (error) { throw wrap(error); }
}

/** Promise-compatible public wrapper; all validation and publication completes before it returns. */
export async function prepareManagedHostProfileIntent(rawInput, request) {
  return prepareManagedHostProfileIntentSync(rawInput, request);
}

