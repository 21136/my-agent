import {randomUUID} from 'node:crypto';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {assertRecord, atomicJson, readJson, realDirectory, UUID} from './paths.mjs';

const FILE = 'adoption-journal.json';
const MAX_FILE_BYTES = 1024 * 1024;
const MAX_HISTORY = 5;
const MAX_STEPS = 50;
const IDENTITY_KEYS = ['installation_id', 'release_id', 'binding_digest'];
const STATE_KEYS = ['format', 'identity', 'status', 'operation', 'history', 'updated_at'];
const STEP_KEYS = ['step', 'cwd', 'started_at', 'receipt'];
const ownedFiles = new Map();

function isOid(value) { return typeof value === 'string' && /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/.test(value); }
function validateIdentity(identity) {
  assertRecord(identity, IDENTITY_KEYS, 'adoption journal identity');
  if (!UUID.test(identity.installation_id) || !isOid(identity.release_id) || !/^[0-9a-f]{64}$/.test(identity.binding_digest)) {
    throw new Error('adoption journal identity is invalid');
  }
  return {...identity};
}
function journalPath(stateRoot) { return path.join(realDirectory(stateRoot, 'launcher state root'), FILE); }
function timestamp() { return new Date().toISOString(); }
function initial(identity) { return {format: 1, identity, status: 'idle', operation: null, history: [], updated_at: timestamp()}; }
function validateBaseline(value) {
  assertRecord(value, ['stableHead', 'sandboxHead', 'snapshotId', 'repoRoot', 'sandboxRoot'], 'adoption baseline');
  if (!isOid(value.stableHead) || !isOid(value.sandboxHead) || !/^[0-9a-f]{64}$/.test(value.snapshotId)
    || typeof value.repoRoot !== 'string' || !path.isAbsolute(value.repoRoot) || value.repoRoot.length > 1024
    || typeof value.sandboxRoot !== 'string' || !path.isAbsolute(value.sandboxRoot) || value.sandboxRoot.length > 1024) {
    throw new Error('adoption baseline is invalid');
  }
  return {stableHead: value.stableHead, sandboxHead: value.sandboxHead, snapshotId: value.snapshotId,
    repoRoot: path.resolve(value.repoRoot), sandboxRoot: path.resolve(value.sandboxRoot)};
}
function validateState(value, expectedIdentity) {
  assertRecord(value, STATE_KEYS, 'adoption journal');
  if (value.format !== 1 || !['idle', 'active', 'maintenance_required'].includes(value.status)
    || !Array.isArray(value.history) || value.history.length > MAX_HISTORY) throw new Error('adoption journal state is invalid');
  const identity = validateIdentity(value.identity);
  if (IDENTITY_KEYS.some(key => identity[key] !== expectedIdentity[key])) throw new Error('adoption journal identity mismatch');
  if (value.operation !== null) {
    const extra = ['baseline', 'recovery_stash', 'finish', 'finished_at'].filter(key => Object.hasOwn(value.operation, key));
    assertRecord(value.operation, ['operation_id', 'started_at', 'steps', 'result', ...extra], 'adoption operation');
    if (!UUID.test(value.operation.operation_id) || !Array.isArray(value.operation.steps) || value.operation.steps.length > MAX_STEPS
      || !['active', 'failed'].includes(value.operation.result)) throw new Error('adoption operation state is invalid');
    if (value.operation.baseline !== undefined) validateBaseline(value.operation.baseline);
    if (value.operation.recovery_stash !== undefined && !isOid(value.operation.recovery_stash)) throw new Error('adoption recovery stash identity is invalid');
    for (const step of value.operation.steps) {
      assertRecord(step, STEP_KEYS, 'adoption step');
      if (typeof step.step !== 'string' || typeof step.cwd !== 'string' || !path.isAbsolute(step.cwd) || step.cwd.length > 1024
        || !Array.isArray(step.receipt) || step.receipt.length > 1) throw new Error('adoption step state is invalid');
      for (const receipt of step.receipt) {
        assertRecord(receipt, ['code', 'at'], 'Git write receipt');
        if (!Number.isInteger(receipt.code) || typeof receipt.at !== 'string') throw new Error('Git write receipt is invalid');
      }
    }
  }
  if ((value.status === 'idle') !== (value.operation === null)) throw new Error('adoption journal status disagrees with operation');
  return value;
}
function readOrCreate(file, identity) {
  if (!existsSync(file)) { const state = initial(identity); atomicJson(file, state); return state; }
  return validateState(readJson(file, MAX_FILE_BYTES), identity);
}
function persist(file, state) { state.updated_at = timestamp(); atomicJson(file, state); }
function safeStep(value) {
  if (typeof value !== 'string' || !/^[A-Za-z][A-Za-z0-9_.:-]{0,79}$/.test(value)) throw new Error('adoption step label is invalid');
  return value;
}
function safeCwd(value) {
  if (typeof value !== 'string' || !path.isAbsolute(value) || value.length > 1024 || /[\r\n\0]/.test(value)) throw new Error('adoption working directory is invalid');
  return path.resolve(value);
}
function safeFinish(result) {
  if (!result || typeof result !== 'object' || Array.isArray(result) || typeof result.ok !== 'boolean') throw new Error('adoption result is invalid');
  const safe = {ok: result.ok};
  for (const key of ['source_commit', 'stable_commit', 'sandbox_commit', 'recovery_stash_sha']) {
    if (result[key] !== undefined) { if (!isOid(result[key])) throw new Error('adoption result identity is invalid'); safe[key] = result[key]; }
  }
  return safe;
}
function compactOperation(operation, safeFinishResult) {
  return {operation_id: operation.operation_id, started_at: operation.started_at, finished_at: operation.finished_at,
    ...(operation.baseline ? {baseline: operation.baseline} : {}),
    ...(operation.recovery_stash ? {recovery_stash: operation.recovery_stash} : {}),
    steps: operation.steps, finish: safeFinishResult};
}

export function readAdoptionJournal(stateRoot, identity) {
  return readOrCreate(journalPath(stateRoot), validateIdentity(identity));
}

export function createAdoptionJournal(stateRoot, identity) {
  const checked = validateIdentity(identity), file = journalPath(stateRoot), owner = Symbol('adoption-journal-owner');
  const initialState = readOrCreate(file, checked);
  if (initialState.operation) throw new Error('adoption journal has an active or unfinished operation; ownership cannot be resumed');
  let pendingBaseline = null;

  function load() { return readOrCreate(file, checked); }
  function ownsActive(state) { return ownedFiles.get(file) === owner && state.operation?.result === 'active'; }
  function assertIdle() {
    const state = load();
    if (state.status === 'idle' && state.operation === null) return state;
    throw new Error('adoption journal requires maintenance; previous operation is active or unfinished');
  }
  function begin(status) {
    const baseline = validateBaseline({stableHead: status?.stableHead, sandboxHead: status?.sandboxHead,
      snapshotId: status?.snapshotId, repoRoot: status?.repoRoot, sandboxRoot: status?.sandboxRoot});
    const state = load();
    if (state.status !== 'idle' || state.operation !== null) throw new Error('adoption journal requires idle state before begin');
    pendingBaseline = baseline;
  }
  function beforeWrite({step, cwd}) {
    const label = safeStep(step), directory = safeCwd(cwd), state = load();
    if (state.operation === null) {
      if (state.status !== 'idle' || ownedFiles.has(file)) throw new Error('adoption journal requires maintenance; Git write is blocked');
      const operation = {operation_id: randomUUID(), started_at: timestamp(), steps: [], result: 'active',
        ...(pendingBaseline ? {baseline: pendingBaseline} : {})};
      state.operation = operation; state.status = 'active'; ownedFiles.set(file, owner);
    } else if (!ownsActive(state)) throw new Error('adoption operation is owned by another or previous Launcher instance');
    const operation = state.operation;
    if (operation.result !== 'active') throw new Error('adoption journal operation is not writable');
    if (operation.steps.length >= MAX_STEPS) throw new Error('adoption journal operation step limit exceeded');
    const previous = operation.steps.at(-1);
    if (previous && previous.receipt.length === 0) throw new Error('previous Git write has no durable receipt');
    operation.steps.push({step: label, cwd: directory, started_at: timestamp(), receipt: []});
    try { persist(file, state); } catch (error) { if (operation.steps.length === 1) ownedFiles.delete(file); throw error; }
    pendingBaseline = null;
    return operation.operation_id;
  }
  function afterWrite({step, cwd, code}) {
    const label = safeStep(step), directory = safeCwd(cwd);
    if (!Number.isInteger(code)) throw new Error('Git exit code is invalid');
    const state = load(), operation = state.operation, current = operation?.steps.at(-1);
    if (!ownsActive(state) || !operation || !current || current.receipt.length || current.step !== label || current.cwd !== directory) {
      throw new Error('Git write receipt does not match the owned pending journal step');
    }
    current.receipt.push({code, at: timestamp()});
    // Keep this owner authorized to record exact backup and compensation writes; other handles remain blocked.
    persist(file, state);
  }
  function backup(oid) {
    if (!isOid(oid)) throw new Error('backup Git object identity is invalid');
    const state = load(), operation = state.operation;
    if (!ownsActive(state) || !operation) throw new Error('backup receipt has no owned active adoption operation');
    if (operation.recovery_stash !== undefined && operation.recovery_stash !== oid) throw new Error('conflicting recovery stash identity');
    operation.recovery_stash = oid;
    persist(file, state);
  }
  function finish(result) {
    const state = load(), operation = state.operation;
    if (!operation) { pendingBaseline = null; return; }
    if (!ownsActive(state)) throw new Error('adoption operation is owned by another or previous Launcher instance');
    const safe = safeFinish(result);
    if (!safe.ok || operation.steps.some(item => !item.receipt.length || item.receipt.at(-1).code !== 0)) {
      operation.result = 'failed'; operation.finish = safe; state.status = 'maintenance_required'; persist(file, state);
      ownedFiles.delete(file); return;
    }
    operation.result = 'active'; operation.finished_at = timestamp();
    state.history.push(compactOperation(operation, safe));
    if (state.history.length > MAX_HISTORY) state.history.splice(0, state.history.length - MAX_HISTORY);
    state.operation = null; state.status = 'idle'; persist(file, state);
    pendingBaseline = null; ownedFiles.delete(file);
  }
  return {assertIdle, begin, beforeWrite, afterWrite, backup, finish};
}

