import {
  closeSync, fstatSync, fsyncSync, lstatSync, openSync, readSync, realpathSync,
  writeSync,
} from 'node:fs';
import { constants as fsConstants } from 'node:fs';
import { createHash, randomUUID } from 'node:crypto';
import path from 'node:path';

const JOURNAL_NAME = 'managed-source-transaction.jsonl';
const MAX_BYTES = 1024 * 1024;
const MAX_LINE_BYTES = 128 * 1024;
const MAX_EVENTS = 8;
const ZERO_HASH = '0'.repeat(64);
const FAILURE_CODES = new Set(['revalidation_failed', 'write_failed', 'verification_failed', 'interrupted', 'persistence_failed']);
const RECEIPT_KEYS = ['deliveryPaths', 'mergeCommit', 'mergedTree', 'preservationDigest', 'sandboxParent', 'stableParent'];
const HEX_OID = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
const HEX_SHA256 = /^[a-f0-9]{64}$/;
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}
function digest(value) { return createHash('sha256').update(canonical(value), 'utf8').digest('hex'); }
function exactKeys(value, keys, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).sort().join('\0') !== [...keys].sort().join('\0')) {
    throw new Error(`Invalid ${label} keys`);
  }
}
function validIdentity(identity) {
  exactKeys(identity, ['installationId', 'namespaceDigest'], 'identity');
  if (typeof identity.installationId !== 'string' || !UUID.test(identity.installationId) || typeof identity.namespaceDigest !== 'string' || !HEX_SHA256.test(identity.namespaceDigest)) throw new Error('Invalid journal identity');
  return { installationId: identity.installationId, namespaceDigest: identity.namespaceDigest };
}
function validOperation(operation) {
  exactKeys(operation, ['operationId', 'planDigest', 'reviewDigest'], 'operation');
  if (typeof operation.operationId !== 'string' || !UUID.test(operation.operationId) || !HEX_SHA256.test(operation.planDigest) || !HEX_SHA256.test(operation.reviewDigest)) throw new Error('Invalid journal operation');
  return { operationId: operation.operationId, planDigest: operation.planDigest, reviewDigest: operation.reviewDigest };
}
function isReparse(stats) {
  const attributes = stats.attributes;
  const reparseAttribute = Number.isInteger(attributes) && (attributes & 0x400) !== 0;
  return stats.isSymbolicLink() || reparseAttribute;
}
function assertSafeDirectory(directory) {
  if (typeof directory !== 'string' || !path.isAbsolute(directory)) throw new Error('Journal directory must be absolute');
  const resolved = path.resolve(directory);
  const parsed = path.parse(resolved);
  let cursor = parsed.root;
  for (const part of resolved.slice(parsed.root.length).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, part);
    const info = lstatSync(cursor);
    if (isReparse(info) || !info.isDirectory()) throw new Error('Journal directory contains a link, reparse point, or non-directory ancestor');
    const real = realpathSync.native(cursor);
    const normalize = (entry) => process.platform === 'win32' ? entry.toLowerCase() : entry;
    if (normalize(real) !== normalize(cursor)) throw new Error('Journal directory is an aliased path');
  }
  return resolved;
}
function assertSafeNumericStats(stats) {
  for (const key of ['dev', 'ino', 'size', 'nlink']) {
    if (!Number.isSafeInteger(stats[key]) || stats[key] < 0) throw new Error(`Journal ${key} identity is not a safe integer`);
  }
  for (const key of ['mtimeMs', 'ctimeMs']) {
    if (!Number.isFinite(stats[key])) throw new Error(`Journal ${key} timestamp is unavailable`);
  }
}
function assertRegularSingleLink(stats) {
  assertSafeNumericStats(stats);
  if (!stats.isFile() || isReparse(stats) || stats.nlink !== 1) throw new Error('Journal must be a regular single-link file');
}
function sameFileIdentity(left, right) { return left.dev === right.dev && left.ino === right.ino; }
function sameReadSnapshot(left, right) {
  return sameFileIdentity(left, right) && left.size === right.size && left.nlink === right.nlink && left.mtimeMs === right.mtimeMs && left.ctimeMs === right.ctimeMs;
}
function openReadOnlySafe(file) {
  const pre = lstatSync(file);
  assertRegularSingleLink(pre);
  const noFollow = fsConstants.O_NOFOLLOW ?? 0;
  const fd = openSync(file, fsConstants.O_RDONLY | noFollow);
  try {
    const post = fstatSync(fd);
    assertRegularSingleLink(post);
    if (!sameFileIdentity(pre, post)) throw new Error('Journal file changed while opening');
    return fd;
  } catch (error) { closeSync(fd); throw error; }
}
function readBounded(fd, { allowEmpty = false, file = null } = {}) {
  const before = fstatSync(fd);
  assertRegularSingleLink(before);
  const pathBefore = file === null ? before : lstatSync(file);
  assertRegularSingleLink(pathBefore);
  if (!sameReadSnapshot(before, pathBefore)) throw new Error('Journal path changed before bounded read');
  if ((!allowEmpty && before.size === 0) || before.size > MAX_BYTES) throw new Error('Journal size is invalid or exceeds the read limit');
  const data = Buffer.alloc(before.size);
  let offset = 0;
  while (offset < data.length) {
    const count = readSync(fd, data, offset, data.length - offset, offset);
    if (!Number.isSafeInteger(count) || count <= 0) throw new Error('Truncated journal read');
    offset += count;
  }
  const after = fstatSync(fd);
  assertRegularSingleLink(after);
  const pathAfter = file === null ? after : lstatSync(file);
  assertRegularSingleLink(pathAfter);
  if (!sameReadSnapshot(before, after) || !sameReadSnapshot(after, pathAfter)) throw new Error('Journal changed during bounded read');
  return data.toString('utf8');
}
function validateReceipt(receipt) {
  exactKeys(receipt, RECEIPT_KEYS, 'receipt');
  for (const name of ['mergeCommit', 'mergedTree', 'stableParent', 'sandboxParent']) if (typeof receipt[name] !== 'string' || !HEX_OID.test(receipt[name])) throw new Error(`Invalid receipt ${name}`);
  if (typeof receipt.preservationDigest !== 'string' || !HEX_SHA256.test(receipt.preservationDigest)) throw new Error('Invalid receipt preservationDigest');
  if (!Array.isArray(receipt.deliveryPaths) || receipt.deliveryPaths.length === 0 || receipt.deliveryPaths.length > 2048) throw new Error('Invalid receipt deliveryPaths');
  let prior = null;
  for (const item of receipt.deliveryPaths) {
    if (typeof item !== 'string' || item.length === 0 || item.length > 1024 || item.includes('\\') || item.startsWith('/') || item.includes('\0') || item.split('/').some((part) => !part || part === '.' || part === '..')) throw new Error('Invalid receipt delivery path');
    if (prior !== null && prior >= item) throw new Error('Receipt deliveryPaths must be sorted and unique');
    prior = item;
  }
  return JSON.parse(JSON.stringify(receipt));
}
function validateRows(text) {
  if (!text.endsWith('\n')) throw new Error('Truncated journal: final line is incomplete');
  const rawLines = text.slice(0, -1).split('\n');
  if (rawLines.length < 1 || rawLines.length > MAX_EVENTS) throw new Error('Invalid journal event count');
  let previousHash = ZERO_HASH;
  let state = 'claimed';
  let identity;
  let operation;
  let receipt = null;
  let result = null;
  let writeId = null;
  const events = [];
  for (let index = 0; index < rawLines.length; index++) {
    const raw = rawLines[index];
    if (Buffer.byteLength(raw, 'utf8') > MAX_LINE_BYTES) throw new Error('Journal line exceeds read limit');
    let row;
    try { row = JSON.parse(raw); } catch { throw new Error('Corrupt journal JSON'); }
    exactKeys(row, ['version', 'sequence', 'type', 'payload', 'previousHash', 'hash'], 'event');
    if (row.version !== 1 || row.sequence !== index || typeof row.type !== 'string' || typeof row.previousHash !== 'string' || !HEX_SHA256.test(row.previousHash) || row.previousHash !== previousHash || typeof row.hash !== 'string' || !HEX_SHA256.test(row.hash)) throw new Error('Invalid journal sequence or hash chain');
    const unsigned = { version: row.version, sequence: row.sequence, type: row.type, payload: row.payload, previousHash: row.previousHash };
    if (digest(unsigned) !== row.hash || canonical(row) !== raw) throw new Error('Journal event hash or canonical encoding mismatch');
    const payload = row.payload;
    if (index === 0) {
      if (row.type !== 'claim') throw new Error('Journal does not begin with a claim');
      exactKeys(payload, ['identity', 'operation'], 'claim');
      identity = validIdentity(payload.identity);
      operation = validOperation(payload.operation);
      state = 'ready';
    } else if (row.type === 'write_intent') {
      exactKeys(payload, ['step', 'operationId', 'writeId'], 'write intent');
      if (state !== 'ready' || payload.step !== 'source_merge' || payload.operationId !== operation.operationId || typeof payload.writeId !== 'string' || !UUID.test(payload.writeId)) throw new Error('Invalid or duplicate write intent');
      writeId = payload.writeId; state = 'intent';
    } else if (row.type === 'write_exit') {
      exactKeys(payload, ['writeId', 'exitCode'], 'write exit');
      if (state !== 'intent' || payload.writeId !== writeId || !Number.isInteger(payload.exitCode)) throw new Error('Invalid write exit ordering or token');
      result = payload.exitCode; state = result === 0 ? 'exited' : 'maintenance';
    } else if (row.type === 'finish') {
      exactKeys(payload, ['receipt'], 'finish');
      if (state !== 'exited') throw new Error('Receipt is out of order');
      receipt = validateReceipt(payload.receipt); state = 'verified';
    } else if (row.type === 'failure') {
      exactKeys(payload, ['code'], 'failure');
      if (!FAILURE_CODES.has(payload.code) || state === 'verified' || state === 'maintenance') throw new Error('Invalid failure event');
      state = 'maintenance';
    } else {
      throw new Error('Unknown journal event type');
    }
    previousHash = row.hash;
    events.push(row);
  }
  return { identity, operation, events, status: state === 'verified' ? 'source_merge_verified' : 'maintenance_required', receipt: state === 'verified' ? receipt : null, state, writeId, result };
}

export function createManagedSourceJournal({ directory, identity, operation }) {
  const root = assertSafeDirectory(directory);
  const safeIdentity = validIdentity(identity);
  const safeOperation = validOperation(operation);
  const file = path.join(root, JOURNAL_NAME);
  let fd;
  try { fd = openSync(file, fsConstants.O_RDWR | fsConstants.O_CREAT | fsConstants.O_EXCL, 0o600); }
  catch (error) { throw new Error(`Journal is already claimed or cannot be created: ${error.code ?? error.message}`); }
  let state = 'ready';
  let sequence = 0;
  let previousHash = ZERO_HASH;
  let writeId = null;
  let closed = false;
  let poisoned = false;
  let expectedText = '';
  let expectedLength = 0;
  let fileIdentity;

  const assertPathAndDescriptorIdentity = () => {
    const descriptorStats = fstatSync(fd);
    assertRegularSingleLink(descriptorStats);
    const pathStats = lstatSync(file);
    assertRegularSingleLink(pathStats);
    if (!sameFileIdentity(fileIdentity, descriptorStats) || !sameFileIdentity(fileIdentity, pathStats)) throw new Error('Journal path or descriptor was replaced');
    return descriptorStats;
  };
  const assertExpectedOnDisk = (expected) => {
    assertPathAndDescriptorIdentity();
    const diskText = readBounded(fd, { allowEmpty: expected.length === 0, file });
    if (diskText !== expected || Buffer.byteLength(diskText, 'utf8') !== Buffer.byteLength(expected, 'utf8')) throw new Error('Journal contents or length drifted from the active writer');
    assertPathAndDescriptorIdentity();
  };
  const append = (type, payload) => {
    if (closed || poisoned) throw new Error('Journal handle is closed or persistence-poisoned');
    const unsigned = { version: 1, sequence, type, payload, previousHash };
    const row = { ...unsigned, hash: digest(unsigned) };
    const bytes = Buffer.from(`${canonical(row)}\n`, 'utf8');
    if (sequence >= MAX_EVENTS || bytes.length > MAX_LINE_BYTES) throw new Error('Journal event bound exceeded');
    try {
      assertExpectedOnDisk(expectedText);
      let offset = 0;
      while (offset < bytes.length) {
        const count = writeSync(fd, bytes, offset, bytes.length - offset, expectedLength + offset);
        if (!Number.isSafeInteger(count) || count <= 0) throw new Error('Journal write returned no progress');
        offset += count;
      }
      fsyncSync(fd);
      const nextText = `${expectedText}${bytes.toString('utf8')}`;
      assertExpectedOnDisk(nextText);
      expectedText = nextText;
      expectedLength += bytes.length;
      previousHash = row.hash;
      sequence += 1;
    } catch (error) {
      poisoned = true;
      throw new Error(`Journal persistence or integrity check failed; handle poisoned (${error.code ?? error.message})`);
    }
  };

  try {
    fileIdentity = fstatSync(fd);
    assertRegularSingleLink(fileIdentity);
    assertPathAndDescriptorIdentity();
    append('claim', { identity: safeIdentity, operation: safeOperation });
  } catch (error) { try { closeSync(fd); } catch {} throw error; }

  return {
    beforeWrite({ step }) {
      if (state !== 'ready' || step !== 'source_merge') throw new Error('Journal does not permit another or unknown write');
      writeId = randomUUID();
      append('write_intent', { step, operationId: safeOperation.operationId, writeId });
      state = 'intent';
      return { operationId: safeOperation.operationId, writeId };
    },
    afterWrite({ writeId: supplied, exitCode }) {
      if (state !== 'intent' || supplied !== writeId || !Number.isInteger(exitCode)) throw new Error('Invalid write token, exit code, or journal state');
      append('write_exit', { writeId, exitCode });
      state = exitCode === 0 ? 'exited' : 'maintenance';
    },
    finish({ receipt: candidate }) {
      if (state !== 'exited') throw new Error('Journal finish requires one known successful write exit');
      try { assertExpectedOnDisk(expectedText); } catch (error) { poisoned = true; throw new Error(`Journal integrity check failed before finish; handle poisoned (${error.code ?? error.message})`); }
      const checked = validateReceipt(candidate);
      append('finish', { receipt: checked });
      state = 'verified';
    },
    fail({ code }) {
      if (state === 'verified' || state === 'maintenance' || !FAILURE_CODES.has(code)) throw new Error('Invalid failure code or journal state');
      append('failure', { code });
      state = 'maintenance';
    },
    close() {
      if (closed) return;
      closed = true;
      closeSync(fd);
    },
  };
}

export function readManagedSourceJournal({ directory, identity }) {
  const root = assertSafeDirectory(directory);
  const expectedIdentity = validIdentity(identity);
  const file = path.join(root, JOURNAL_NAME);
  const fd = openReadOnlySafe(file);
  let parsed;
  try { parsed = validateRows(readBounded(fd, { file })); } finally { closeSync(fd); }
  if (canonical(parsed.identity) !== canonical(expectedIdentity)) throw new Error('Journal identity does not match requested namespace');
  return { identity: parsed.identity, operation: parsed.operation, events: parsed.events, status: parsed.status, receipt: parsed.receipt };
}



