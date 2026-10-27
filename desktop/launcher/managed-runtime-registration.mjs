import { createHash } from 'node:crypto';
import { lstatSync, opendirSync, readdirSync } from 'node:fs';
import path from 'node:path';
import {
  canonical, digest, exact, readCanonical, readStableBytes, safeDirectory, safeRelative,
  syncDirectory, writeBytesExclusive, writeCanonicalExclusive,
} from './managed-build-records.mjs';

const PREFIX = 'managed_runtime_registration_invalid: ';
const HASH = /^[a-f0-9]{64}$/;
const MAX_REGISTRATION_BYTES = 32 * 1024 * 1024;
const MAX_RUNTIME_BYTES = 4 * 1024 * 1024 * 1024;
const MAX_FILES = 60_000;
const MAX_FILE_BYTES = 512 * 1024 * 1024;
const INTENT_FILE = 'runtime-intent.json';
const REGISTRATION_FILE = 'runtime-registration.json';
const RECEIPT_FILE = 'runtime-receipt.json';
const RECORD_FILES = [INTENT_FILE, REGISTRATION_FILE, RECEIPT_FILE];
const RUNTIME_NAMES = ['node', 'electron', 'python', 'pythonBase'];
const EXPECTED_ENTRIES = { node: 'node.exe', electron: 'electron.exe', python: 'Scripts/python.exe', pythonBase: 'python.exe' };

function fail(message) { throw new TypeError(PREFIX + message); }
function sha(bytes) { return createHash('sha256').update(bytes).digest('hex'); }
function runtimeRelative(value) { return safeRelative(value) && /^[\x20-\x7e]+$/.test(value); }
function compareRuntimePath(left, right) { return left < right ? -1 : left > right ? 1 : 0; }
function readDirectoryEntriesBounded(directory, limits, entryLimit = MAX_FILES) {
  const entries = [];
  const handle = opendirSync(directory, { bufferSize: 64 });
  try {
    while (true) {
      const entry = handle.readSync();
      if (entry === null) break;
      if (limits.entries >= entryLimit) fail(`runtime aggregate entry count exceeds ${entryLimit}`);
      limits.entries += 1;
      entries.push(entry.name);
    }
  } finally { handle.closeSync(); }
  entries.sort(compareRuntimePath);
  return entries;
}
function canonicalRoot(value, label) {
  if (typeof value !== 'string' || !path.isAbsolute(value) || path.resolve(value) !== value) fail(`${label} root must be canonical and absolute`);
  try { safeDirectory(value); } catch { fail(`${label} root must be an existing ordinary canonical directory`); }
  return value;
}
function validateRuntimeInputs(runtimes) {
  if (!exact(runtimes, RUNTIME_NAMES)) fail('runtimes must contain exactly node, electron, python, and pythonBase');
  const roots = [];
  const normalized = {};
  for (const name of RUNTIME_NAMES) {
    const input = runtimes[name];
    if (!exact(input, ['root', 'entry'])) fail(`${name} runtime input must contain exactly root and entry`);
    const root = canonicalRoot(input.root, name);
    if (!runtimeRelative(input.entry)) fail(`${name} entry must be a canonical ASCII relative path`);
    if (name === 'node' && input.entry.includes('/')) fail('Node runtime entry must be a single root-level filename');
    if (process.platform === 'win32' && input.entry !== EXPECTED_ENTRIES[name]) fail(`${name} entry must be ${EXPECTED_ENTRIES[name]}`);
    if (roots.some(existing => root === existing || root.startsWith(existing + path.sep) || existing.startsWith(root + path.sep))) fail('runtime roots must be disjoint');
    roots.push(root);
    normalized[name] = { root, entry: input.entry };
  }
  return normalized;
}
function captureRuntime(name, runtime, limits) {
  const { root, entry } = runtime;
  canonicalRoot(root, name);
  if (name === 'node') {
    const target = path.join(root, entry);
    const stat = lstatSync(target);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1
        || (process.platform === 'win32' && (stat.attributes & 0x400))
        || !Number.isSafeInteger(stat.size) || stat.size < 0 || stat.size > MAX_FILE_BYTES) fail('Node pinned executable is not a bounded single-link regular file');
    if (limits.entries + 1 > MAX_FILES || limits.files + 1 > MAX_FILES || limits.bytes + stat.size > MAX_RUNTIME_BYTES) fail('runtime aggregate count or byte bound exceeded');
    const bytes = readStableBytes(target, MAX_FILE_BYTES);
    limits.entries += 1;
    limits.files += 1;
    limits.bytes += bytes.length;
    return {
      root, entry, policy: 'single_executable_v1',
      files: [{ path: entry, sha256: sha(bytes), byteLength: bytes.length }],
      directories: [],
    };
  }
  const files = [];
  const directories = [];
  const seenPaths = new Set();

  function visit(directory, prefix = '', depth = 0) {
    if (depth > 64) fail(`${name} runtime directory depth exceeded`);
    for (const component of readDirectoryEntriesBounded(directory, limits)) {
      const relative = prefix ? `${prefix}/${component}` : component;
      if (!runtimeRelative(relative)) fail(`${name} runtime contains a noncanonical ASCII path`);
      const folded = relative.toLowerCase();
      if (seenPaths.has(folded)) fail(`${name} runtime contains a case alias`);
      seenPaths.add(folded);
      const target = path.join(directory, component);
      const stat = lstatSync(target);
      if (stat.isSymbolicLink() || (process.platform === 'win32' && (stat.attributes & 0x400))) fail(`${name} runtime contains a link or reparse point`);
      if (stat.isDirectory()) {
        directories.push(relative);
        visit(target, relative, depth + 1);
        continue;
      }
      if (!stat.isFile() || stat.nlink !== 1 || !Number.isSafeInteger(stat.size) || stat.size < 0 || stat.size > MAX_FILE_BYTES) fail(`${name} runtime contains an unsafe or oversized file`);
      if (files.length >= MAX_FILES || limits.files >= MAX_FILES) fail('runtime aggregate file count exceeds 60000');
      if (limits.bytes + stat.size > MAX_RUNTIME_BYTES) fail('runtime aggregate bytes exceed 4 GiB');
      const bytes = readStableBytes(target, MAX_FILE_BYTES);
      limits.bytes += bytes.length;
      limits.files += 1;
      files.push({ path: relative, sha256: sha(bytes), byteLength: bytes.length });
    }
  }

  visit(root);
  if (!files.length || !files.some(file => file.path === entry)) fail(`${name} runtime entry is absent from the explicit root`);
  if (name === 'node' && (files.length !== 1 || directories.length !== 0 || files[0].path !== entry)) fail('Node runtime must be exactly one standalone executable');
  files.sort((a, b) => compareRuntimePath(a.path, b.path));
  directories.sort(compareRuntimePath);
  return { root, entry, policy: name === 'node' ? 'single_executable_v1' : 'complete_tree_v1', files, directories };
}
function captureRuntimes(runtimes) {
  const limits = { bytes: 0, files: 0, entries: 0 };
  const records = {};
  for (const name of RUNTIME_NAMES) records[name] = captureRuntime(name, runtimes[name], limits);
  return records;
}
function registrationBody({ binding, runtimes }) {
  return {
    format: 1,
    mode: 'managed_runtime_registration_v1',
    installationId: binding.installationId,
    releaseId: binding.release.releaseId,
    bindingDigest: binding.bindingDigest,
    runtimes,
    readyForLaunch: false,
  };
}
function boundedCanonical(value, label) {
  const bytes = Buffer.from(canonical(value) + '\n', 'utf8');
  if (bytes.length > MAX_REGISTRATION_BYTES) fail(`${label} exceeds the 32 MiB canonical record bound`);
  return bytes;
}
function validateRegistration(value, expected) {
  const keys = ['format', 'mode', 'installationId', 'releaseId', 'bindingDigest', 'runtimes', 'readyForLaunch', 'runtimeDigest'];
  if (!exact(value, keys) || value.format !== 1 || value.mode !== 'managed_runtime_registration_v1'
      || value.installationId !== expected.installationId || value.releaseId !== expected.releaseId
      || value.bindingDigest !== expected.bindingDigest || value.readyForLaunch !== false
      || !HASH.test(value.runtimeDigest || '') || !exact(value.runtimes, RUNTIME_NAMES)) fail('runtime registration schema or identity invalid');
  const body = { ...value };
  delete body.runtimeDigest;
  if (digest(body) !== value.runtimeDigest) fail('runtime registration digest invalid');
  const roots = [];
  const rootKeys = [];
  let aggregateFiles = 0;
  let aggregateEntries = 0;
  let aggregateBytes = 0;
  for (const name of RUNTIME_NAMES) {
    const runtime = value.runtimes[name];
    if (!exact(runtime, ['root', 'entry', 'policy', 'files', 'directories'])
        || typeof runtime.root !== 'string' || !path.isAbsolute(runtime.root) || path.resolve(runtime.root) !== runtime.root
        || !runtimeRelative(runtime.entry)
        || name === 'node' && runtime.entry.includes('/')
        || runtime.policy !== (name === 'node' ? 'single_executable_v1' : 'complete_tree_v1')
        || !Array.isArray(runtime.files) || !runtime.files.length || !Array.isArray(runtime.directories)) fail(`${name} runtime record schema invalid`);
    const rootKey = runtime.root.toLowerCase();
    if (roots.some((existing, index) => {
      const existingKey = rootKeys[index];
      return rootKey === existingKey || rootKey.startsWith(existingKey + path.sep) || existingKey.startsWith(rootKey + path.sep);
    })) fail('registered runtime roots overlap');
    roots.push(runtime.root);
    rootKeys.push(rootKey);
    let previous = '';
    const fileAliases = new Set();
    const expectedDirectories = new Set();
    for (const file of runtime.files) {
      if (!exact(file, ['path', 'sha256', 'byteLength']) || !runtimeRelative(file.path) || compareRuntimePath(file.path, previous) <= 0
          || !HASH.test(file.sha256 || '') || !Number.isSafeInteger(file.byteLength) || file.byteLength < 0 || file.byteLength > MAX_FILE_BYTES) fail(`${name} runtime file record invalid`);
      previous = file.path;
      const folded = file.path.toLowerCase();
      if (fileAliases.has(folded)) fail(`${name} runtime file records contain a case alias`);
      fileAliases.add(folded);
      aggregateFiles += 1;
      aggregateEntries += 1;
      aggregateBytes += file.byteLength;
      const parts = file.path.split('/');
      parts.pop();
      while (parts.length) { expectedDirectories.add(parts.join('/')); parts.pop(); }
    }
    previous = '';
    const directoryAliases = new Set();
    const directorySet = new Set();
    for (const directory of runtime.directories) {
      if (!runtimeRelative(directory) || compareRuntimePath(directory, previous) <= 0) fail(`${name} runtime directory record invalid`);
      previous = directory;
      const folded = directory.toLowerCase();
      if (directoryAliases.has(folded)) fail(`${name} runtime directory records contain a case alias`);
      directoryAliases.add(folded);
      directorySet.add(directory);
    }
    if (!runtime.files.some(file => file.path === runtime.entry)) fail(`${name} runtime entry record missing`);
    if (name === 'node' && (runtime.files.length !== 1 || runtime.directories.length || runtime.files[0].path !== runtime.entry)) fail('Node runtime record is not a standalone executable');
    if ([...expectedDirectories].some(directory => !directorySet.has(directory))) fail(`${name} runtime directory inventory omits a file parent`);
    for (const directory of runtime.directories) {
      const parts = directory.split('/');
      parts.pop();
      while (parts.length) {
        if (!directorySet.has(parts.join('/'))) fail(`${name} runtime directory inventory omits a parent`);
        parts.pop();
      }
      if (fileAliases.has(directory.toLowerCase())) fail(`${name} runtime file and directory paths overlap`);
    }
    for (const file of runtime.files) {
      const parts = file.path.split('/');
      parts.pop();
      while (parts.length) {
        if (fileAliases.has(parts.join('/').toLowerCase())) fail(`${name} runtime file is nested under a file path`);
        parts.pop();
      }
    }
    aggregateEntries += runtime.directories.length;
    if (process.platform === 'win32' && runtime.entry !== EXPECTED_ENTRIES[name]) fail(`${name} runtime entry name invalid`);
  }
  if (aggregateFiles > MAX_FILES || aggregateEntries > MAX_FILES || aggregateBytes > MAX_RUNTIME_BYTES) fail('runtime registration aggregate count or byte bound exceeded');
  boundedCanonical(value, 'runtime registration');
  return value;
}
function validateIntent(value, expected) {
  const keys = ['format', 'mode', 'installationId', 'releaseId', 'bindingDigest', 'runtimeDigest', 'state', 'readyForLaunch', 'intentDigest'];
  if (!exact(value, keys) || value.format !== 1 || value.mode !== 'managed_runtime_intent_v1'
      || value.installationId !== expected.installationId || value.releaseId !== expected.releaseId
      || value.bindingDigest !== expected.bindingDigest || value.runtimeDigest !== expected.runtimeDigest
      || value.state !== 'registration_started' || value.readyForLaunch !== false) fail('runtime intent identity or schema invalid');
  const body = { ...value };
  delete body.intentDigest;
  if (!HASH.test(value.intentDigest || '') || digest(body) !== value.intentDigest) fail('runtime intent digest invalid');
  return value;
}
function validateReceipt(value, registration) {
  const keys = ['format', 'mode', 'installationId', 'releaseId', 'bindingDigest', 'runtimeDigest', 'registrationDigest', 'readyForLaunch', 'receiptDigest'];
  if (!exact(value, keys) || value.format !== 1 || value.mode !== 'managed_runtime_receipt_v1'
      || value.installationId !== registration.installationId || value.releaseId !== registration.releaseId
      || value.bindingDigest !== registration.bindingDigest || value.runtimeDigest !== registration.runtimeDigest
      || value.registrationDigest !== digest(registration) || value.readyForLaunch !== false) fail('runtime receipt identity or schema invalid');
  const body = { ...value };
  delete body.receiptDigest;
  if (!HASH.test(value.receiptDigest || '') || digest(body) !== value.receiptDigest) fail('runtime receipt digest invalid');
  return value;
}
function expectedFromBinding(binding) {
  return { installationId: binding.installationId, releaseId: binding.release.releaseId, bindingDigest: binding.bindingDigest };
}
function launcherRoot(context) { return context.binding.paths.launcher; }
function assertEmptyLauncher(context) {
  const names = readdirSync(launcherRoot(context)).sort();
  if (names.length) fail('launcher runtime records already exist; registration is single-use');
}
function readRegistrationRecords(root, expected) {
  const names = readdirSync(root).sort();
  if (names.length !== RECORD_FILES.length || names.join('\0') !== [...RECORD_FILES].sort().join('\0')) fail('launcher runtime record set is partial or contains unknown entries');
  const registration = validateRegistration(readCanonical(path.join(root, REGISTRATION_FILE), MAX_REGISTRATION_BYTES), expected);
  validateIntent(readCanonical(path.join(root, INTENT_FILE), MAX_REGISTRATION_BYTES), { ...expected, runtimeDigest: registration.runtimeDigest });
  validateReceipt(readCanonical(path.join(root, RECEIPT_FILE), MAX_REGISTRATION_BYTES), registration);
  return registration;
}

/** Internal metadata-only validation used by production-context readers. */
export function validateManagedRuntimeRegistrationRecords(root, expected) {
  try {
    if (!exact(expected, ['installationId', 'releaseId', 'bindingDigest']) || !HASH.test(expected.bindingDigest || '')) fail('expected production identity invalid');
    return readRegistrationRecords(root, expected);
  } catch (error) { throw error instanceof Error && error.message.startsWith(PREFIX) ? error : new TypeError(PREFIX + (error instanceof Error ? error.message : 'record validation refused')); }
}

/** @internal Host-only read-only inventory primitive; accepts explicit roots and never launches runtimes. */
export function captureManagedRuntimeInputs(rawRuntimes) {
  try { return captureRuntimes(validateRuntimeInputs(rawRuntimes)); }
  catch (error) { throw error instanceof Error && error.message.startsWith(PREFIX) ? error : new TypeError(PREFIX + (error instanceof Error ? error.message : 'runtime capture refused')); }
}

/** @internal Read-only incremental directory reader for focused boundedness checks. */
export function readManagedRuntimeDirectoryEntries(directory, maxEntries = MAX_FILES) {
  try {
    if (!Number.isSafeInteger(maxEntries) || maxEntries < 0 || maxEntries > MAX_FILES) fail('directory entry limit is invalid');
    canonicalRoot(directory, 'runtime directory');
    return readDirectoryEntriesBounded(directory, { entries: 0 }, maxEntries);
  } catch (error) { throw error instanceof Error && error.message.startsWith(PREFIX) ? error : new TypeError(PREFIX + (error instanceof Error ? error.message : 'directory read refused')); }
}

/** Register exact explicitly supplied runtime bytes for an already verified inert production context. */
export async function prepareManagedRuntimeRegistration({ context: rawContext, runtimes: rawRuntimes } = {}) {
  try {
    const runtimes = validateRuntimeInputs(rawRuntimes);
    const { readManagedProductionContext } = await import('./managed-production-context.mjs');
    const verifiedContext = await readManagedProductionContext(rawContext);
    if (verifiedContext.runtimeRegistered) fail('runtime registration is already present');
    assertEmptyLauncher(verifiedContext);
    const captured = captureManagedRuntimeInputs(runtimes);
    const body = registrationBody({ binding: verifiedContext.binding, runtimes: captured });
    const registration = { ...body, runtimeDigest: digest(body) };
    const registrationBytes = boundedCanonical(registration, 'runtime registration');
    const expected = expectedFromBinding(verifiedContext.binding);
    const intentBody = {
      format: 1, mode: 'managed_runtime_intent_v1', ...expected,
      runtimeDigest: registration.runtimeDigest, state: 'registration_started', readyForLaunch: false,
    };
    const intent = { ...intentBody, intentDigest: digest(intentBody) };
    boundedCanonical(intent, 'runtime intent');
    writeCanonicalExclusive(path.join(launcherRoot(verifiedContext), INTENT_FILE), intent);

    const beforePublication = captureManagedRuntimeInputs(runtimes);
    if (canonical(beforePublication) !== canonical(captured)) fail('runtime bytes changed before exclusive registration publication');
    writeBytesExclusive(path.join(launcherRoot(verifiedContext), REGISTRATION_FILE), registrationBytes, MAX_REGISTRATION_BYTES);
    const persisted = validateRegistration(readCanonical(path.join(launcherRoot(verifiedContext), REGISTRATION_FILE), MAX_REGISTRATION_BYTES), expected);
    if (canonical(persisted) !== canonical(registration)) fail('published runtime registration differs from the prepared record');
    const afterPublication = captureManagedRuntimeInputs(runtimes);
    if (canonical(afterPublication) !== canonical(registration.runtimes)) fail('runtime bytes changed after exclusive registration publication');
    const receiptBody = {
      format: 1, mode: 'managed_runtime_receipt_v1', ...expected,
      runtimeDigest: registration.runtimeDigest,
      registrationDigest: digest(registration),
      readyForLaunch: false,
    };
    const receipt = { ...receiptBody, receiptDigest: digest(receiptBody) };
    boundedCanonical(receipt, 'runtime receipt');
    writeCanonicalExclusive(path.join(launcherRoot(verifiedContext), RECEIPT_FILE), receipt);
    syncDirectory(launcherRoot(verifiedContext));

    const { readManagedRuntimeRegistration } = await import('./managed-runtime-registration.mjs');
    const result = await readManagedRuntimeRegistration(rawContext);
    if (result.registration.runtimeDigest !== registration.runtimeDigest) fail('runtime registration failed its post-publication byte verification');
    return result;
  } catch (error) { throw error instanceof Error && error.message.startsWith(PREFIX) ? error : new TypeError(PREFIX + (error instanceof Error ? error.message : 'registration refused')); }
}

/** Read exact registration metadata and re-hash all registered bytes without executing them. */
export async function readManagedRuntimeRegistration(rawContext) {
  try {
    const { readManagedProductionContext } = await import('./managed-production-context.mjs');
    const verifiedContext = await readManagedProductionContext(rawContext);
    if (!verifiedContext.runtimeRegistered) fail('complete runtime registration is absent');
    const expected = expectedFromBinding(verifiedContext.binding);
    const registration = readRegistrationRecords(launcherRoot(verifiedContext), expected);
    if (verifiedContext.runtimeDigest !== registration.runtimeDigest) fail('production context runtime digest differs from launcher records');
    const runtimes = {};
    for (const name of RUNTIME_NAMES) runtimes[name] = { root: registration.runtimes[name].root, entry: registration.runtimes[name].entry };
    const actual = captureManagedRuntimeInputs(runtimes);
    if (canonical(actual) !== canonical(registration.runtimes)) fail('registered runtime bytes or directory layout changed');
    const descriptor = {
      format: 1, mode: 'managed_runtime_registration_descriptor_v1',
      installationId: registration.installationId, releaseId: registration.releaseId,
      bindingDigest: registration.bindingDigest, runtimeDigest: registration.runtimeDigest,
      root: verifiedContext.descriptor.root, readyForLaunch: false,
    };
    return { descriptor, registration };
  } catch (error) { throw error instanceof Error && error.message.startsWith(PREFIX) ? error : new TypeError(PREFIX + (error instanceof Error ? error.message : 'registration read refused')); }
}
