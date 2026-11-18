import { constants as C } from 'node:fs';
import { closeSync, existsSync, fsyncSync, fstatSync, lstatSync, mkdirSync, openSync, readFileSync, realpathSync, readdirSync, unlinkSync, writeSync } from 'node:fs';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import {
  validateSelfEvolutionCandidateBinding,
  validateSelfEvolutionMergeCheckpoint,
  validateSelfEvolutionPromotionPreview,
  validateSelfEvolutionNativeConfirmation,
  validateSelfEvolutionMergeIntent,
} from './self-evolution-selected-merge.mjs';

// Only a trusted synchronous host callback may return this sentinel, and only
// when it can guarantee that no one-shot authorization was consumed. It is not
// a native authorization credential and does not establish callback provenance.
export const SELF_EVOLUTION_AUTHORIZATION_NOT_CONSUMED = Symbol('self-evolution-authorization-not-consumed');

const HASH = /^[a-f0-9]{64}$/;
const STORE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const MAX_RECORD_BYTES = 20 * 1024 * 1024;
const MAX_STORE_ENTRIES = 20_000;
const canonical = value => Array.isArray(value)
  ? `[${value.map(canonical).join(',')}]`
  : value && typeof value === 'object'
    ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`
    : JSON.stringify(value);
const digest = value => createHash('sha256').update(canonical(value)).digest('hex');
function reject(code, message) { const error = new TypeError(message); error.code = code; throw error; }
function assertSafeDirectoryChain(target) {
  if (typeof target !== 'string' || !path.isAbsolute(target) || path.resolve(target) !== target) reject('self_evolution_store_root_invalid', 'Private operation store root must be canonical and absolute');
  let current = path.parse(target).root;
  for (const part of target.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    let stat; try { stat = lstatSync(current); } catch { reject('self_evolution_store_root_invalid', 'Private operation store root is missing or inaccessible'); }
    if (!stat.isDirectory() || stat.isSymbolicLink() || (stat.attributes & 0x400) || realpathSync.native(current) !== current) reject('self_evolution_store_root_unsafe', 'Private operation store root contains a link, reparse point, or alias');
  }
}
function safeId(value) { if (typeof value !== 'string' || !STORE_ID.test(value)) reject('self_evolution_operation_identity_invalid', 'Task, attempt, and operation ids must be safe single path components'); return value; }
function ensureDirectory(parent, name) {
  safeId(name);
  const target = path.join(parent, name);
  try { mkdirSync(target, { mode: 0o700 }); } catch (error) { if (error.code !== 'EEXIST') reject('self_evolution_store_write_failed', 'Could not create an operation directory'); }
  let stat; try { stat = lstatSync(target); } catch { reject('self_evolution_store_corrupt', 'Operation directory disappeared'); }
  if (!stat.isDirectory() || stat.isSymbolicLink() || (stat.attributes & 0x400) || realpathSync.native(target) !== target) reject('self_evolution_store_corrupt', 'Operation directory is not a plain canonical directory');
  return target;
}
function writeExclusive(file, value) {
  const bytes = Buffer.from(`${canonical(value)}\n`, 'utf8');
  if (bytes.length > MAX_RECORD_BYTES) reject('self_evolution_store_limit_exceeded', 'Operation record exceeds the bounded storage limit');
  let fd;
  try {
    fd = openSync(file, C.O_CREAT | C.O_EXCL | C.O_WRONLY | (C.O_NOFOLLOW || 0), 0o600);
    let offset = 0;
    while (offset < bytes.length) { const count = writeSync(fd, bytes, offset, bytes.length - offset); if (!count) reject('self_evolution_store_write_failed', 'Operation record write was incomplete'); offset += count; }
    fsyncSync(fd);
  } catch (error) {
    if (error.code === 'EEXIST') reject('self_evolution_intent_unavailable', 'Operation state already exists and is single-use');
    if (error.code?.startsWith?.('self_evolution_')) throw error;
    reject('self_evolution_store_write_failed', 'Could not durably create operation state');
  } finally { if (fd !== undefined) closeSync(fd); }
  let directoryFd;
  try { directoryFd = openSync(path.dirname(file), C.O_RDONLY); fsyncSync(directoryFd); }
  catch (error) { if (process.platform !== 'win32' || !['EPERM', 'EISDIR', 'EINVAL'].includes(error.code)) reject('self_evolution_store_sync_failed', 'Operation directory durability could not be confirmed'); }
  finally { if (directoryFd !== undefined) closeSync(directoryFd); }
  const reread = readRecord(file);
  if (!reread.bytes.equals(bytes)) reject('self_evolution_store_write_failed', 'Operation record failed byte-for-byte readback');
}
function writeBinaryExclusive(file, bytes, maxBytes = 1024 * 1024) {
  if (!Buffer.isBuffer(bytes) || bytes.length > maxBytes) reject('self_evolution_store_limit_exceeded', 'Stable index evidence exceeds the bounded storage limit');
  let fd;
  try {
    fd = openSync(file, C.O_CREAT | C.O_EXCL | C.O_WRONLY | (C.O_NOFOLLOW || 0), 0o600);
    let offset = 0;
    while (offset < bytes.length) { const count = writeSync(fd, bytes, offset, bytes.length - offset); if (!count) reject('self_evolution_store_write_failed', 'Stable index evidence write was incomplete'); offset += count; }
    fsyncSync(fd);
  } catch (error) {
    if (error.code === 'EEXIST') reject('self_evolution_intent_unavailable', 'Operation state already exists and is single-use');
    if (error.code?.startsWith?.('self_evolution_')) throw error;
    reject('self_evolution_store_write_failed', 'Could not durably persist stable index evidence');
  } finally { if (fd !== undefined) closeSync(fd); }
  const saved = readPlainFile(file, maxBytes);
  if (!saved.equals(bytes)) reject('self_evolution_store_write_failed', 'Stable index evidence failed readback');
}
function readPlainFile(file, maxBytes = MAX_RECORD_BYTES) {
  let fd;
  try {
    const before = lstatSync(file);
    if (!before.isFile() || before.isSymbolicLink() || (before.attributes & 0x400) || before.nlink !== 1 || before.size <= 0 || before.size > maxBytes || realpathSync.native(file) !== file) reject('self_evolution_store_corrupt', 'Operation evidence is not a bounded ordinary file');
    fd = openSync(file, C.O_RDONLY | (C.O_NOFOLLOW || 0));
    const opened = fstatSync(fd);
    if (!opened.isFile() || opened.nlink !== 1 || opened.dev !== before.dev || opened.ino !== before.ino || opened.size !== before.size) reject('self_evolution_store_corrupt', 'Operation evidence changed while being opened');
    const bytes = readFileSync(fd), afterFd = fstatSync(fd), afterPath = lstatSync(file);
    if (bytes.length !== before.size || afterFd.size !== before.size || afterFd.dev !== before.dev || afterFd.ino !== before.ino || afterPath.dev !== before.dev || afterPath.ino !== before.ino || afterPath.nlink !== 1 || realpathSync.native(file) !== file) reject('self_evolution_store_corrupt', 'Operation evidence changed while being read');
    return bytes;
  } catch (error) {
    if (error.code?.startsWith?.('self_evolution_')) throw error;
    reject('self_evolution_store_corrupt', 'Operation evidence is missing or malformed');
  } finally { if (fd !== undefined) closeSync(fd); }
}
function readRecord(file, maxBytes = MAX_RECORD_BYTES) {
  const bytes = readPlainFile(file, maxBytes), text = bytes.toString('utf8');
  if (!text.endsWith('\n') || text.indexOf('\n') !== text.length - 1) reject('self_evolution_store_corrupt', 'Operation record is not canonical JSON');
  let value; try { value = JSON.parse(text.slice(0, -1)); } catch { reject('self_evolution_store_corrupt', 'Operation record is malformed JSON'); }
  if (`${canonical(value)}\n` !== text) reject('self_evolution_store_corrupt', 'Operation record is not canonical JSON');
  return { value, bytes };
}function exact(value, keys) { return !!value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key)); }
function acquireCoordinator(root) {
  const lockPath = path.join(root, '.coordinator.lock'), token = randomUUID();
  let fd;
  try {
    fd = openSync(lockPath, C.O_CREAT | C.O_EXCL | C.O_WRONLY | (C.O_NOFOLLOW || 0), 0o600);
    writeSync(fd, token, 0, 'utf8'); fsyncSync(fd);
  } catch (error) {
    if (error.code === 'EEXIST') reject('self_evolution_store_lock_busy', 'Operation store has an active or abandoned coordinator lock');
    reject('self_evolution_store_lock_failed', 'Could not durably acquire the operation-store coordinator');
  } finally { if (fd !== undefined) closeSync(fd); }
  let directoryFd;
  try { directoryFd = openSync(root, C.O_RDONLY); fsyncSync(directoryFd); }
  catch (error) { if (process.platform !== 'win32' || !['EPERM','EISDIR','EINVAL'].includes(error.code)) reject('self_evolution_store_sync_failed', 'Could not durably persist the operation-store coordinator'); }
  finally { if (directoryFd !== undefined) closeSync(directoryFd); }
  if (readPlainFile(lockPath, 128).toString('utf8') !== token) reject('self_evolution_store_lock_unsafe', 'Operation-store coordinator lock failed readback');
  return { lockPath, token };
}
function releaseCoordinator(lock) {
  const current = readPlainFile(lock.lockPath, 128).toString('utf8');
  if (current !== lock.token) reject('self_evolution_store_lock_unsafe', 'Operation-store coordinator lock ownership changed');
  unlinkSync(lock.lockPath);
  let fd;
  try { fd = openSync(path.dirname(lock.lockPath), C.O_RDONLY); fsyncSync(fd); }
  catch (error) { if (process.platform !== 'win32' || !['EPERM','EISDIR','EINVAL'].includes(error.code)) reject('self_evolution_store_sync_failed', 'Could not durably release the operation-store coordinator'); }
  finally { if (fd !== undefined) closeSync(fd); }
}
function assertOperationIdentityAvailable(root, intent) {
  const wanted = [intent.taskId, intent.attemptId, intent.operationId].map(value => value.toLocaleLowerCase('en-US'));
  for (const taskName of readdirSync(root)) {
    const taskDir = path.join(root, taskName);
    if (!lstatSync(taskDir).isDirectory()) continue;
    for (const attemptName of readdirSync(taskDir)) {
      const attemptDir = path.join(taskDir, attemptName);
      if (!lstatSync(attemptDir).isDirectory()) continue;
      for (const operationName of readdirSync(attemptDir)) {
        const existingPath = path.join(attemptDir, operationName);
        if (!lstatSync(existingPath).isDirectory()) continue;
        const existing = [taskName, attemptName, operationName].map(value => value.toLocaleLowerCase('en-US'));
        if (existing[2] === wanted[2]) {
          if (operationName !== intent.operationId) reject('self_evolution_operation_identity_ambiguous', 'Operation id has a case-fold alias on a case-insensitive filesystem');
          reject('self_evolution_intent_unavailable', 'Operation identity is already used by another task or attempt');
        }
        if (existing.every((value, index) => value === wanted[index])) reject('self_evolution_operation_identity_ambiguous', 'Operation path aliases an existing identity on a case-insensitive filesystem');
      }
    }
  }
}
function operationDirectory(root, intent) {
  const task = safeId(intent.taskId), attempt = safeId(intent.attemptId), operation = safeId(intent.operationId);
  const taskDir = ensureDirectory(root, task), attemptDir = ensureDirectory(taskDir, attempt);
  return ensureDirectory(attemptDir, operation);
}
function countTree(root) {
  let count = 0, bytes = 0, operations = 0;
  const visit = (directory, depth) => {
    for (const name of readdirSync(directory)) {
      if (++count > MAX_STORE_ENTRIES) reject('self_evolution_store_limit_exceeded', 'Operation store contains too many records');
      const file = path.join(directory, name), stat = lstatSync(file);
      if (stat.isSymbolicLink() || (stat.attributes & 0x400) || realpathSync.native(file) !== file) reject('self_evolution_store_corrupt', 'Operation store contains a link or alias');
      if (stat.isDirectory()) { if (depth === 2 && ++operations > 8) reject('self_evolution_store_limit_exceeded', 'Operation store exceeds the eight-operation bound'); visit(file, depth + 1); }
      else if (!stat.isFile() || stat.nlink !== 1 || stat.size > MAX_RECORD_BYTES) reject('self_evolution_store_corrupt', 'Operation store contains an unsupported entry');
      else { bytes += stat.size; if (bytes > 32 * 1024 * 1024) reject('self_evolution_store_limit_exceeded', 'Operation store exceeds the 32 MiB bound'); }
    }
  };
  visit(root, 0);
  return { entries: count, bytes, operations };
}/**
 * Host-private append-only operation ledger. It persists contracts and one-way
 * state markers; an executing marker without a terminal receipt is always
 * maintenance-required on restart and is never automatically replayed.
 */
export function createSelfEvolutionMergeOperationStore({ root } = {}) {
  assertSafeDirectoryChain(root);
  countTree(root);

  function read(operationId, allowCoordinatorLock = false) {
    safeId(operationId);
    if (!allowCoordinatorLock && existsSync(path.join(root, '.coordinator.lock'))) return { status: 'maintenance_required', code: 'self_evolution_store_lock_busy' };

    // Validate all prepared identities before resolving the requested operation.
    // The original ledger limits (eight operations, 32 MiB total) still apply.
    countTree(root);
    const wanted = operationId.toLocaleLowerCase('en-US');
    const operations = [];
    const foldedIds = new Set();
    for (const taskName of readdirSync(root)) {
      const taskDir = path.join(root, taskName);
      if (!lstatSync(taskDir).isDirectory()) continue;
      for (const attemptName of readdirSync(taskDir)) {
        const attemptDir = path.join(taskDir, attemptName);
        if (!lstatSync(attemptDir).isDirectory()) continue;
        for (const operationName of readdirSync(attemptDir)) {
          const directory = path.join(attemptDir, operationName);
          const entry = lstatSync(directory);
          if (!entry.isDirectory()) {
            if (operationName.toLocaleLowerCase('en-US') === wanted) reject('self_evolution_store_corrupt', 'Operation identity entry is not a directory');
            continue;
          }
          if (operations.length >= 8) reject('self_evolution_store_limit_exceeded', 'Operation store exceeds the eight-operation bound');
          const foldedId = operationName.toLocaleLowerCase('en-US');
          if (foldedIds.has(foldedId)) reject('self_evolution_operation_identity_ambiguous', 'Operation id is present in more than one task or attempt directory');
          foldedIds.add(foldedId);
          operations.push({ taskName, attemptName, operationName, directory });
        }
      }
    }
    let totalPreparedBytes = 0, selected;
    for (const operation of operations) {
      const { taskName, attemptName, operationName, directory } = operation;
      const remainingBytes = 32 * 1024 * 1024 - totalPreparedBytes;
      if (remainingBytes <= 0) reject('self_evolution_store_limit_exceeded', 'Operation store exceeds the 32 MiB bound');
      const record = readRecord(path.join(directory, 'prepared.json'), Math.min(MAX_RECORD_BYTES, remainingBytes));
      totalPreparedBytes += record.bytes.length;
      if (totalPreparedBytes > 32 * 1024 * 1024) reject('self_evolution_store_limit_exceeded', 'Operation store exceeds the 32 MiB bound');
      const prepared = record.value;
      if (!exact(prepared, ['format','mode','payload','recordDigest']) || prepared.format !== 1 || prepared.mode !== 'self_evolution_persisted_merge_operation_v1' || !HASH.test(prepared.recordDigest || '')) reject('self_evolution_store_corrupt', 'Prepared operation envelope is malformed');
      const recordBody = { ...prepared }; delete recordBody.recordDigest;
      if (prepared.recordDigest !== digest(recordBody)) reject('self_evolution_store_corrupt', 'Prepared operation record digest is invalid');
      try {
        const { candidate, checkpoint, preview, nativeConfirmation, intent } = prepared.payload;
        validateSelfEvolutionCandidateBinding(candidate);
        validateSelfEvolutionMergeCheckpoint(checkpoint, candidate);
        validateSelfEvolutionPromotionPreview(preview, checkpoint);
        validateSelfEvolutionNativeConfirmation(nativeConfirmation, preview);
        validateSelfEvolutionMergeIntent(intent, checkpoint, preview, nativeConfirmation);
        if (intent.operationId !== operationName || taskName !== intent.taskId || attemptName !== intent.attemptId) reject('self_evolution_store_corrupt', 'Operation path identity differs from its durable intent');
        if (operationName.toLocaleLowerCase('en-US') === wanted) {
          if (operationName !== operationId) reject('self_evolution_operation_identity_ambiguous', 'Operation id has a case-fold alias on a case-insensitive filesystem');
          selected = { directory, checkpoint, intent };
        }
      } catch (error) {
        if (error.code === 'self_evolution_operation_identity_ambiguous' || error.code === 'self_evolution_store_corrupt') throw error;
        reject('self_evolution_store_corrupt', 'Prepared operation contract is invalid');
      }
    }
    if (!selected) return { status: 'not_found' };
    const { directory, checkpoint, intent } = selected;
    const indexPath = path.join(directory, 'stable-index.before');
    if (createHash('sha256').update(readPlainFile(indexPath, 1024 * 1024)).digest('hex') !== checkpoint.stableState.indexSha256) reject('self_evolution_store_corrupt', 'Stable index evidence is unsafe or does not match its checkpoint');
    const names = readdirSync(directory);
    if (names.some(name => !['prepared.json','stable-index.before','executing.json','unknown.json'].includes(name)) || !names.includes('prepared.json')) reject('self_evolution_store_corrupt', 'Operation directory contains an invalid state combination');
    const executing = names.includes('executing.json') ? readRecord(path.join(directory, 'executing.json')).value : null;
    if (executing && (!exact(executing, ['format','mode','operationId','intentDigest','markerDigest']) || executing.format !== 1 || executing.mode !== 'self_evolution_merge_executing_v1' || executing.operationId !== operationId || executing.intentDigest !== intent.intentDigest || executing.markerDigest !== digest(Object.fromEntries(Object.entries(executing).filter(([key]) => key !== 'markerDigest'))))) reject('self_evolution_store_corrupt', 'Execution claim is malformed or unbound');
    const unknown = names.includes('unknown.json') ? readRecord(path.join(directory, 'unknown.json')).value : null;
    if (unknown && (!exact(unknown, ['format','mode','operationId','intentDigest','code','markerDigest']) || unknown.format !== 1 || unknown.mode !== 'self_evolution_merge_unknown_v1' || unknown.operationId !== operationId || unknown.intentDigest !== intent.intentDigest || typeof unknown.code !== 'string' || !/^[a-z][a-z0-9_]{0,63}$/.test(unknown.code) || unknown.markerDigest !== digest(Object.fromEntries(Object.entries(unknown).filter(([key]) => key !== 'markerDigest'))))) reject('self_evolution_store_corrupt', 'Unknown marker is malformed or unbound');
    if (unknown && !executing) reject('self_evolution_store_corrupt', 'Unknown operation has no durable execution claim');
    if (unknown || executing) return { status: 'maintenance_required', code: unknown?.code || 'self_evolution_merge_result_unknown', intent, directory };
    return { status: 'prepared', intent, directory };
  }
  return Object.freeze({
    prepare(input) {
      if (!exact(input, ['candidate','checkpoint','preview','nativeConfirmation','intent','stableIndexBytes']) || !Buffer.isBuffer(input.stableIndexBytes) || input.stableIndexBytes.length > 1024 * 1024 || createHash('sha256').update(input.stableIndexBytes).digest('hex') !== input.checkpoint?.stableState?.indexSha256) reject('self_evolution_operation_invalid', 'Prepared operation input has an invalid field set');
      validateSelfEvolutionCandidateBinding(input.candidate);
      validateSelfEvolutionMergeCheckpoint(input.checkpoint, input.candidate);
      validateSelfEvolutionPromotionPreview(input.preview, input.checkpoint);
      validateSelfEvolutionNativeConfirmation(input.nativeConfirmation, input.preview);
      validateSelfEvolutionMergeIntent(input.intent, input.checkpoint, input.preview, input.nativeConfirmation);
      safeId(input.intent.taskId); safeId(input.intent.attemptId); safeId(input.intent.operationId);
      assertOperationIdentityAvailable(root, input.intent);
      const operationPath = path.join(root, input.intent.taskId, input.intent.attemptId, input.intent.operationId);
      if (existsSync(operationPath)) reject('self_evolution_intent_unavailable', 'Operation identity already exists and is single-use');
      const coordinator = acquireCoordinator(root);
      try {
        const usage = countTree(root);
        assertOperationIdentityAvailable(root, input.intent);
        if (usage.operations >= 8) reject('self_evolution_store_limit_exceeded', 'Operation store exceeds the eight-operation bound');
        if (existsSync(operationPath)) reject('self_evolution_intent_unavailable', 'Operation identity already exists and is single-use');
        const payload = structuredClone({ candidate: input.candidate, checkpoint: input.checkpoint, preview: input.preview, nativeConfirmation: input.nativeConfirmation, intent: input.intent });
        const body = { format: 1, mode: 'self_evolution_persisted_merge_operation_v1', payload };
        const prepared = { ...body, recordDigest: digest(body) };
        const recordBytes = Buffer.byteLength(`${canonical(prepared)}\n`, 'utf8');
        if (usage.bytes + input.stableIndexBytes.length + recordBytes > 32 * 1024 * 1024) reject('self_evolution_store_limit_exceeded', 'Operation store exceeds the 32 MiB bound');
        const directory = operationDirectory(root, input.intent);
        writeBinaryExclusive(path.join(directory, 'stable-index.before'), input.stableIndexBytes);
        writeExclusive(path.join(directory, 'prepared.json'), prepared);
        const result = read(input.intent.operationId, true);
        if (result.status !== 'prepared') reject('self_evolution_store_write_failed', 'Prepared operation failed durable readback');
        releaseCoordinator(coordinator);
        return { status: 'prepared', intent: result.intent, intentDigest: result.intent.intentDigest, directory };
      } catch (error) {
        // Keep the lock after any partial operation write; restart recovery must be explicit.
        throw error;
      }
    },
    read: operationId => read(operationId),
    claim(operationId, expectedIntentDigest, { assertAuthorized } = {}) {
      const coordinator = acquireCoordinator(root);
      let retainCoordinator = false;
      try {
        // Serialize the durable-state check, one-shot native authorization claim,
        // and execution marker publication across all store processes.
        const current = read(operationId, true);
        if (current.status !== 'prepared' || current.intent.intentDigest !== expectedIntentDigest) reject('self_evolution_intent_unavailable', 'Operation is missing, already claimed, unknown, or stale');
        if (typeof assertAuthorized !== 'function') reject('self_evolution_confirmation_untrusted', 'A trusted native one-shot claim callback is required');
        // Once the callback begins, an exception or ambiguous result might mean
        // that the native one-shot authorization was consumed before failure.
        retainCoordinator = true;
        let authorization;
        try { authorization = assertAuthorized(); }
        catch { reject('self_evolution_confirmation_unknown', 'Native confirmation outcome is unknown; maintenance is required'); }
        if (authorization === SELF_EVOLUTION_AUTHORIZATION_NOT_CONSUMED) {
          retainCoordinator = false;
          reject('self_evolution_confirmation_untrusted', 'Trusted host rejected confirmation without consuming it');
        }
        if (authorization !== true) reject('self_evolution_confirmation_unknown', 'Native confirmation outcome is unknown; maintenance is required');
        const body = { format: 1, mode: 'self_evolution_merge_executing_v1', operationId, intentDigest: expectedIntentDigest };
        writeExclusive(path.join(current.directory, 'executing.json'), { ...body, markerDigest: digest(body) });
        const claimed = read(operationId, true);
        if (claimed.status !== 'maintenance_required' || claimed.code !== 'self_evolution_merge_result_unknown'
          || claimed.intent.intentDigest !== expectedIntentDigest) reject('self_evolution_store_write_failed', 'Execution claim failed durable readback');
        releaseCoordinator(coordinator);
        return { status: 'executing', intent: claimed.intent, directory: claimed.directory };
      } catch (error) {
        // Retain the lock after authorization starts unless the trusted host
        // explicitly guarantees no consumption; marker write failures also stay
        // quarantined. Recovery never silently retries an unknown outcome.
        if (!retainCoordinator) releaseCoordinator(coordinator);
        throw error;
      }
    },
    markUnknown(operationId, code) {
      const current = read(operationId);
      if (current.status !== 'maintenance_required' || current.code !== 'self_evolution_merge_result_unknown'
        || readdirSync(current.directory).includes('unknown.json')) reject('self_evolution_intent_unavailable', 'Only a claimed operation without a terminal result can be marked unknown');
      if (typeof code !== 'string' || !/^[a-z][a-z0-9_]{0,63}$/.test(code)) reject('self_evolution_operation_invalid', 'Unknown result code is malformed');
      const body = { format: 1, mode: 'self_evolution_merge_unknown_v1', operationId, intentDigest: current.intent.intentDigest, code };
      writeExclusive(path.join(current.directory, 'unknown.json'), { ...body, markerDigest: digest(body) });
      return { status: 'maintenance_required', code, intent: current.intent, directory: current.directory };
    },
  });
}
