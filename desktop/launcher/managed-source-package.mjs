import {
  closeSync, fstatSync, fsyncSync, lstatSync, openSync, readSync, realpathSync, writeSync,
} from 'node:fs';
import { constants as fsConstants } from 'node:fs';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { validateManagedCleanSourceReview, createManagedCleanSourcePlan } from './managed-clean-source-adapter.mjs';
import { executeManagedSourceMerge } from './managed-source-transaction.mjs';

const PACKAGE_NAME = 'managed-source-package.json';
const PACKAGE_MAX_BYTES = 2 * 1024 * 1024;
const PACKAGE_KEYS = ['format', 'mode', 'plan', 'review', 'confirmation', 'packageDigest'];
const CONFIRMATION_KEYS = ['planDigest', 'reviewDigest', 'namespaceDigest'];
const SHA256 = /^[a-f0-9]{64}$/;

function plainObject(value) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}
function exactKeys(value, keys, label) {
  if (!plainObject(value) || Object.keys(value).sort().join('\0') !== [...keys].sort().join('\0')) throw new Error(`Invalid ${label} keys`);
}
function validateJson(value, seen = new Set()) {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return;
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new Error('Package contains a non-finite number');
    return;
  }
  if (typeof value !== 'object' || seen.has(value)) throw new Error('Package must contain only acyclic JSON values');
  seen.add(value);
  const keys = Reflect.ownKeys(value);
  if (Array.isArray(value)) {
    if (Object.getPrototypeOf(value) !== Array.prototype || keys.length !== value.length + 1 || keys.some(key => typeof key !== 'string' || (key !== 'length' && !/^(?:0|[1-9][0-9]*)$/.test(key)))) throw new Error('Package arrays must be dense plain JSON arrays');
    for (let index = 0; index < value.length; index++) {
      const descriptor = Object.getOwnPropertyDescriptor(value, String(index));
      if (!descriptor?.enumerable || !Object.hasOwn(descriptor, 'value')) throw new Error('Package arrays cannot contain holes or accessors');
      validateJson(descriptor.value, seen);
    }
  } else {
    if (!plainObject(value) || keys.some(key => typeof key !== 'string') || keys.some(key => ['__proto__', 'prototype', 'constructor'].includes(key))) throw new Error('Package objects must be plain and contain no prototype keys');
    for (const key of keys) {
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (!descriptor?.enumerable || !Object.hasOwn(descriptor, 'value')) throw new Error('Package objects cannot contain hidden values or accessors');
      validateJson(descriptor.value, seen);
    }
  }
  seen.delete(value);
}
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (plainObject(value)) return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}
function sha256(value) { return createHash('sha256').update(value, 'utf8').digest('hex'); }
function deepFreeze(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    for (const child of Object.values(value)) deepFreeze(child);
    Object.freeze(value);
  }
  return value;
}
function clone(value) { return JSON.parse(canonical(value)); }
function safeDirectory(directory) {
  if (typeof directory !== 'string' || !path.isAbsolute(directory) || directory.includes('\0')) throw new Error('Package directory must be an absolute path');
  const resolved = path.resolve(directory);
  if (directory !== resolved) throw new Error('Package directory must use its exact normalized absolute spelling');
  const parsed = path.parse(resolved);
  let current = parsed.root;
  for (const component of resolved.slice(parsed.root.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, component);
    const info = lstatSync(current);
    assertDirectoryIdentity(current, info);
  }
  return resolved;
}
function isReparse(stats) { return stats.isSymbolicLink() || (Number.isInteger(stats.attributes) && (stats.attributes & 0x400) !== 0); }
function assertDirectoryIdentity(directory, stats = lstatSync(directory)) {
  if (isReparse(stats) || !stats.isDirectory()) throw new Error('Package directory contains a link, reparse point, or non-directory ancestor');
  const real = realpathSync.native(directory);
  if (real !== directory) throw new Error('Package directory contains an aliased or noncanonical ancestor');
}
function assertNumericStats(stats) {
  for (const field of ['dev', 'ino', 'size', 'nlink']) if (!Number.isSafeInteger(stats[field]) || stats[field] < 0) throw new Error(`Package ${field} identity is unavailable`);
  for (const field of ['mtimeMs', 'ctimeMs']) if (!Number.isFinite(stats[field])) throw new Error(`Package ${field} timestamp is unavailable`);
}
function assertPackageFile(stats) {
  assertNumericStats(stats);
  if (!stats.isFile() || isReparse(stats) || stats.nlink !== 1) throw new Error('Package must be an ordinary single-link file');
}
function sameSnapshot(a, b) {
  return a.dev === b.dev && a.ino === b.ino && a.size === b.size && a.nlink === b.nlink && a.mtimeMs === b.mtimeMs && a.ctimeMs === b.ctimeMs;
}
function readBounded(fd, file) {
  const before = fstatSync(fd);
  assertPackageFile(before);
  const pathBefore = lstatSync(file);
  assertPackageFile(pathBefore);
  if (!sameSnapshot(before, pathBefore)) throw new Error('Package path changed before bounded read');
  if (before.size < 2 || before.size > PACKAGE_MAX_BYTES) throw new Error('Package size is invalid or exceeds 2 MiB');
  const bytes = Buffer.alloc(before.size);
  let offset = 0;
  while (offset < bytes.length) {
    const count = readSync(fd, bytes, offset, bytes.length - offset, offset);
    if (!Number.isSafeInteger(count) || count <= 0) throw new Error('Package read was truncated');
    offset += count;
  }
  const after = fstatSync(fd);
  const pathAfter = lstatSync(file);
  assertPackageFile(after);
  assertPackageFile(pathAfter);
  if (!sameSnapshot(before, after) || !sameSnapshot(after, pathAfter)) throw new Error('Package changed during bounded read');
  const text = bytes.toString('utf8');
  if (!Buffer.from(text, 'utf8').equals(bytes)) throw new Error('Package is not valid UTF-8');
  return text;
}
function checkedConfirmation(value, plan, namespaceDigest) {
  validateJson(value);
  exactKeys(value, CONFIRMATION_KEYS, 'confirmation');
  if (![value.planDigest, value.reviewDigest, value.namespaceDigest].every(item => typeof item === 'string' && SHA256.test(item))) throw new Error('Invalid package confirmation digest');
  if (value.planDigest !== plan.planDigest || value.reviewDigest !== plan.reviewDigest || value.namespaceDigest !== namespaceDigest || plan.namespaceDigest !== namespaceDigest) throw new Error('Confirmation does not bind the exact plan, review, and namespace');
  return clone(value);
}
function validatedContents({ plan: inputPlan, review: inputReview, confirmation: inputConfirmation, installationId, namespaceDigest } = {}) {
  validateJson(inputReview);
  validateJson(inputPlan);
  validateJson(inputConfirmation);
  const review = validateManagedCleanSourceReview(clone(inputReview));
  const plan = createManagedCleanSourcePlan({ review: clone(review), installationId: installationId ?? inputPlan?.installationId, namespaceDigest: namespaceDigest ?? inputPlan?.namespaceDigest });
  if (canonical(plan) !== canonical(inputPlan)) throw new Error('Stored plan does not exactly match the regenerated review plan');
  const confirmation = checkedConfirmation(inputConfirmation, plan, namespaceDigest ?? plan.namespaceDigest);
  return { review, plan, confirmation };
}
function packageFor(contents) {
  const body = { format: 1, mode: 'managed_source_package_v1', ...contents };
  return { ...body, packageDigest: sha256(canonical(body)) };
}
function validateStoredPackage(value, { installationId, namespaceDigest }) {
  validateJson(value);
  exactKeys(value, PACKAGE_KEYS, 'package');
  if (value.format !== 1 || value.mode !== 'managed_source_package_v1' || typeof value.packageDigest !== 'string' || !SHA256.test(value.packageDigest)) throw new Error('Invalid managed source package identity');
  const body = { format: value.format, mode: value.mode, plan: value.plan, review: value.review, confirmation: value.confirmation };
  if (sha256(canonical(body)) !== value.packageDigest) throw new Error('Managed source package digest mismatch');
  const contents = validatedContents({ ...body, installationId, namespaceDigest });
  const checked = packageFor(contents);
  if (canonical(checked) !== canonical(value)) throw new Error('Managed source package is not canonical or has inconsistent bindings');
  return deepFreeze(clone(checked));
}
function readPackageAt(directory) {
  const root = safeDirectory(directory);
  const file = path.join(root, PACKAGE_NAME);
  const before = lstatSync(file);
  assertPackageFile(before);
  const noFollow = fsConstants.O_NOFOLLOW ?? 0;
  const fd = openSync(file, fsConstants.O_RDONLY | noFollow);
  try {
    const opened = fstatSync(fd);
    assertPackageFile(opened);
    if (opened.dev !== before.dev || opened.ino !== before.ino) throw new Error('Package file was replaced while opening');
    const text = readBounded(fd, file);
    if (!text.endsWith('\n') || text.indexOf('\n') !== text.length - 1) throw new Error('Package must be one canonical JSON object followed by LF');
    const raw = text.slice(0, -1);
    let parsed;
    try { parsed = JSON.parse(raw); } catch { throw new Error('Managed source package JSON is corrupt or truncated'); }
    if (canonical(parsed) !== raw) throw new Error('Managed source package JSON is noncanonical');
    return { root, file, value: parsed };
  } finally { closeSync(fd); }
}

export function writeManagedSourcePackage({ directory, plan, review, confirmation } = {}) {
  const contents = validatedContents({ plan, review, confirmation });
  const checked = packageFor(contents);
  const text = `${canonical(checked)}\n`;
  const bytes = Buffer.from(text, 'utf8');
  if (bytes.length > PACKAGE_MAX_BYTES) throw new Error('Managed source package exceeds 2 MiB');
  const root = safeDirectory(directory);
  const file = path.join(root, PACKAGE_NAME);
  const noFollow = fsConstants.O_NOFOLLOW ?? 0;
  const fd = openSync(file, fsConstants.O_RDWR | fsConstants.O_CREAT | fsConstants.O_EXCL | noFollow, 0o600);
  try {
    const opened = fstatSync(fd);
    assertPackageFile(opened);
    const pathStats = lstatSync(file);
    assertPackageFile(pathStats);
    if (!sameSnapshot(opened, pathStats)) throw new Error('Package file was replaced while creating');
    let offset = 0;
    while (offset < bytes.length) {
      const count = writeSync(fd, bytes, offset, bytes.length - offset, offset);
      if (!Number.isSafeInteger(count) || count <= 0) throw new Error('Package write made no progress');
      offset += count;
    }
    fsyncSync(fd);
    const reread = readBounded(fd, file);
    if (reread !== text) throw new Error('Persisted package differs from the requested durable package');
    const value = parseCanonicalPackage(reread);
    return validateStoredPackage(value, { installationId: plan.installationId, namespaceDigest: plan.namespaceDigest });
  } finally { closeSync(fd); }
}

export function readManagedSourcePackage({ directory, installationId, namespaceDigest } = {}) {
  if (typeof installationId !== 'string' || typeof namespaceDigest !== 'string' || !SHA256.test(namespaceDigest)) throw new Error('Exact installation and namespace binding are required');
  const { value } = readPackageAt(directory);
  return validateStoredPackage(value, { installationId, namespaceDigest });
}

// Disk packages are historical inputs, never proof of consent origin or prior fsync.
// Hold a safe read/write descriptor only to establish file durability in this run;
// no bytes are written through it. Recheck the descriptor and named path at every
// await boundary and immediately before the coordinator can invoke the Git writer.
function openExecutionLease({ directory, installationId, namespaceDigest }) {
  const root = safeDirectory(directory);
  const file = path.join(root, PACKAGE_NAME);
  const before = lstatSync(file);
  assertPackageFile(before);
  const noFollow = fsConstants.O_NOFOLLOW ?? 0;
  const fd = openSync(file, fsConstants.O_RDWR | noFollow);
  try {
    const opened = fstatSync(fd);
    assertPackageFile(opened);
    if (!sameSnapshot(before, opened)) throw new Error('Package file changed while opening executor lease');
    fsyncSync(fd); // Windows may require O_RDWR for FlushFileBuffers; failure is fail-closed.
    const durable = fstatSync(fd);
    assertPackageFile(durable);
    if (!sameSnapshot(opened, durable)) throw new Error('Package file identity drifted during executor fsync');
    const text = readBounded(fd, file);
    const value = parseCanonicalPackage(text);
    const checked = validateStoredPackage(value, { installationId, namespaceDigest });
    return { root, file, fd, snapshot: durable, text, stored: checked };
  } catch (error) {
    closeSync(fd);
    throw error;
  }
}
function parseCanonicalPackage(text) {
  if (!text.endsWith('\n') || text.indexOf('\n') !== text.length - 1) throw new Error('Package must be one canonical JSON object followed by LF');
  const raw = text.slice(0, -1);
  let parsed;
  try { parsed = JSON.parse(raw); } catch { throw new Error('Managed source package JSON is corrupt or truncated'); }
  if (canonical(parsed) !== raw) throw new Error('Managed source package JSON is noncanonical');
  return parsed;
}
function assertExecutionLease(lease, binding) {
  const currentRoot = safeDirectory(lease.root);
  if (currentRoot !== lease.root) throw new Error('Package directory identity drifted during recorded execution');
  fsyncSync(lease.fd);
  const descriptor = fstatSync(lease.fd);
  assertPackageFile(descriptor);
  const pathInfo = lstatSync(lease.file);
  assertPackageFile(pathInfo);
  if (!sameSnapshot(lease.snapshot, descriptor) || !sameSnapshot(descriptor, pathInfo)) throw new Error('Package path or descriptor drifted before recorded source execution');
  const text = readBounded(lease.fd, lease.file);
  if (text !== lease.text) throw new Error('Package contents drifted before recorded source execution');
  const stored = validateStoredPackage(parseCanonicalPackage(text), binding);
  if (canonical(stored) !== canonical(lease.stored)) throw new Error('Package binding drifted during recorded source execution');
  return stored;
}

export async function executeRecordedManagedSourceMerge({ directory, installationId, namespaceDigest, gitExecutable } = {}) {
  const binding = { installationId, namespaceDigest };
  const lease = openExecutionLease({ directory, ...binding });
  try {
    let stored = assertExecutionLease(lease, binding);
    const review = validateManagedCleanSourceReview(clone(stored.review));
    const plan = createManagedCleanSourcePlan({ review: clone(review), installationId, namespaceDigest });
    if (canonical(plan) !== canonical(stored.plan)) throw new Error('Recorded plan no longer matches its strict review');
    checkedConfirmation(stored.confirmation, plan, namespaceDigest);

    const { createManagedCleanSourceAdapter } = await import('./managed-clean-source-adapter.mjs');
    if (typeof createManagedCleanSourceAdapter !== 'function') throw new Error('Restricted managed clean-source adapter is not available');
        const sourceAdapter = await createManagedCleanSourceAdapter({
      review: clone(review),
      gitExecutable,
      // Trusted in-process boundary: the adapter calls this synchronously immediately before its merge spawn.
      // Never route this callback through a renderer or caller-provided execution option.
      assertWriteBoundary: () => { assertExecutionLease(lease, binding); },
    });
    stored = assertExecutionLease(lease, binding);
    if (canonical(stored.plan) !== canonical(plan)) throw new Error('Recorded package changed while creating the restricted adapter');
    const adapter = {
      async revalidate(candidate) {
        assertExecutionLease(lease, binding);
        const result = await sourceAdapter.revalidate(candidate);
        assertExecutionLease(lease, binding);
        return result;
      },
      async merge(candidate, context) {
        // The coordinator calls this only after its final awaited revalidation.
        assertExecutionLease(lease, binding);
        return await sourceAdapter.merge(candidate, context);
      },
      async verify(candidate) {
        assertExecutionLease(lease, binding);
        const receipt = await sourceAdapter.verify(candidate);
        assertExecutionLease(lease, binding);
        return receipt;
      },
    };
    return await executeManagedSourceMerge({ directory, plan, confirmation: stored.confirmation, adapter });
  } finally {
    closeSync(lease.fd);
  }
}