import { constants as C } from 'node:fs';
import { closeSync, existsSync, fsyncSync, lstatSync, mkdirSync, openSync, readFileSync, realpathSync, readdirSync, statSync, unlinkSync, writeSync } from 'node:fs';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { validateManagedSelectionRequest } from './managed-source-selection.mjs';
import { validateManagedSourceCheckpoint } from './managed-source-checkpoint.mjs';
import { validateManagedStablePreservation } from './managed-stable-preservation.mjs';

const MAX_ENTRIES = 8, MAX_BYTES = 32 * 1024 * 1024, MAX_FILE = 1024 * 1024;
const MAX_EVIDENCE = 16 * 1024 * 1024;
// Engine contract: temporary.index must be conservatively prevalidated <= this reservation (default 4 MiB).
const DEFAULT_RESERVE_BYTES = 4 * 1024 * 1024, MAX_RESERVE_BYTES = 4 * 1024 * 1024, RECEIPT_RESERVE_BYTES = 64 * 1024;
const HASH = /^[a-f0-9]{64}$/;
const OID = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
const INTENT_KEYS = ['checkpointDigest','expectedStableHeadAfter','expectedStableHeadBefore','expectedTree','format','mode','planDigest','selectedPaths','state'];
const INTENT_V2_KEYS = [...INTENT_KEYS, 'checkpointFileSha256','planFileSha256','stableIndexBeforeSha256'];
const RECEIPT_KEYS = ['checkpointDigest','format','mergedTree','mode','planDigest','readyForLaunch','receiptDigest','selectedPaths','sourceHistoryMerged','sourceMergeExecuted','stableHeadAfter','stableHeadBefore','status'];
function canonical(v) { return Array.isArray(v) ? `[${v.map(canonical).join(',')}]` : v && typeof v === 'object' ? `{${Object.keys(v).sort().map(k => `${JSON.stringify(k)}:${canonical(v[k])}`).join(',')}}` : JSON.stringify(v); }
function digest(v) { return createHash('sha256').update(canonical(v)).digest('hex'); }
function shaBytes(bytes) { return createHash('sha256').update(bytes).digest('hex'); }
function reject(code = 'managed_selected_store_maintenance_required') { const e = new Error(code); e.code = code; throw e; }
function exactKeys(v, keys) { return v && Object.getPrototypeOf(v) === Object.prototype && Object.keys(v).sort().join('\0') === [...keys].sort().join('\0'); }
function assertJsonValue(value, seen = new Set()) {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return;
  if (typeof value === 'number') { if (Number.isFinite(value)) return; reject('managed_selected_store_evidence_invalid'); }
  if (typeof value !== 'object' || seen.has(value)) reject('managed_selected_store_evidence_invalid');
  const proto = Object.getPrototypeOf(value);
  if (Array.isArray(value)) {
    if (proto !== Array.prototype || Reflect.ownKeys(value).length !== value.length + 1) reject('managed_selected_store_evidence_invalid');
    seen.add(value);
    for (let i = 0; i < value.length; i++) { const d = Object.getOwnPropertyDescriptor(value, String(i)); if (!d?.enumerable || !Object.hasOwn(d, 'value')) reject('managed_selected_store_evidence_invalid'); assertJsonValue(d.value, seen); }
    seen.delete(value); return;
  }
  if (proto !== Object.prototype && proto !== null) reject('managed_selected_store_evidence_invalid');
  seen.add(value);
  for (const key of Reflect.ownKeys(value)) { const d = Object.getOwnPropertyDescriptor(value, key); if (typeof key !== 'string' || !d?.enumerable || !Object.hasOwn(d, 'value')) reject('managed_selected_store_evidence_invalid'); assertJsonValue(d.value, seen); }
  seen.delete(value);
}
function ordinary(file, { allowMissing = false } = {}) {
  let s; try { s = lstatSync(file); } catch (e) { if (allowMissing && e.code === 'ENOENT') return false; reject(); }
  if (s.isSymbolicLink() || !s.isDirectory() && !s.isFile() || (s.mode & 0o170000) === 0) reject();
  if (s.isFile() && typeof s.nlink === 'number' && s.nlink !== 1) reject();
  if (s.isDirectory() && process.platform === 'win32' && (s.attributes & 0x400)) reject();
  return true;
}
function checkChain(target, allowMissingLeaf = false) {
  const abs = path.resolve(target), parsed = path.parse(abs); let current = parsed.root;
  for (const part of abs.slice(parsed.root.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    if (!ordinary(current, { allowMissing: allowMissingLeaf })) return false;
  }
  if (realpathSync(abs) !== abs) reject();
  return true;
}
function syncDir(dir) { let fd; try { fd = openSync(dir, C.O_RDONLY); fsyncSync(fd); } catch (e) { if (process.platform !== 'win32' || !['EPERM','EISDIR','EINVAL'].includes(e.code)) throw e; } finally { if (fd !== undefined) closeSync(fd); } }
function writeExclusive(file, bytes, max = MAX_FILE) {
  if (bytes.length > max) reject();
  const fd = openSync(file, C.O_CREAT | C.O_EXCL | C.O_WRONLY, 0o600);
  try {
    let offset = 0;
    while (offset < bytes.length) { const n = writeSync(fd, bytes, offset, bytes.length - offset); if (!n) reject(); offset += n; }
    fsyncSync(fd);
  } finally { closeSync(fd); }
  const reread = readFileSync(file); if (!reread.equals(bytes)) reject();
  syncDir(path.dirname(file));
}
function readCanonical(file, max = MAX_FILE) {
  ordinary(file); const s = statSync(file); if (s.size <= 0 || s.size > max) reject();
  const bytes = readFileSync(file); if (bytes.length !== s.size) reject();
  const text = bytes.toString('utf8'); if (!text.endsWith('\n') || text.indexOf('\n') !== text.length - 1) reject();
  let value; try { value = JSON.parse(text.slice(0, -1)); } catch { reject(); }
  if (canonical(value) + '\n' !== text) reject(); return { value, size: bytes.length };
}
function safePlan(plan) {
  if (!plan || plan.format !== 1 || plan.mode !== 'managed_selected_merge_plan_v1' || !HASH.test(plan.planDigest || '') || !HASH.test(plan.checkpointDigest || '') || !Array.isArray(plan.selectedPaths) || !plan.stable || !OID.test(plan.stable.head || '') || !OID.test(plan.expectedCommit || '') || !OID.test(plan.expectedMergedTree || '')) reject('managed_selected_store_plan_invalid');
  const body = { ...plan }; delete body.planDigest;
  if (digest(body) !== plan.planDigest) reject('managed_selected_store_plan_invalid');
  try {
    validateManagedSelectionRequest({ format: 1, mode: 'managed_source_selection_request_v1', reviewDigest: plan.checkpointDigest, selectedPaths: plan.selectedPaths });
  } catch { reject('managed_selected_store_plan_invalid'); }
  try { validateManagedStablePreservation(plan); } catch { reject('managed_selected_store_plan_invalid'); }
}
function validateReceipt(r, intent) {
  if (!exactKeys(r, RECEIPT_KEYS) || r.format !== 1 || r.mode !== 'managed_selected_merge_receipt_v1' || r.status !== 'source_merge_verified' || r.sourceMergeExecuted !== true || r.sourceHistoryMerged !== false || r.readyForLaunch !== false || r.planDigest !== intent.planDigest || r.checkpointDigest !== intent.checkpointDigest || canonical(r.selectedPaths) !== canonical(intent.selectedPaths) || r.stableHeadBefore !== intent.expectedStableHeadBefore || r.stableHeadAfter !== intent.expectedStableHeadAfter || r.mergedTree !== intent.expectedTree || !OID.test(r.stableHeadBefore || '') || !OID.test(r.stableHeadAfter || '') || !OID.test(r.mergedTree || '')) reject();
  const body = { ...r }; delete body.receiptDigest; if (r.receiptDigest !== digest(body)) reject();
}
function preflight(store, { ownedLock, inProgressPlan } = {}) {
  if (!existsSync(store)) return { opCount: 0, aggregate: 0 };
  if (!ordinary(store) || realpathSync(store) !== store) reject();
  const names = readdirSync(store); if (names.length > MAX_ENTRIES + 1 || names.reduce((n, x) => n + Buffer.byteLength(x), 0) > 4096) reject();
  let aggregate = 0, opCount = 0;
  for (const name of names) {
    if (name === '.coordinator.lock') {
      const { value: lock, size } = readCanonical(path.join(store, name), 4096);
      if (!ownedLock || !exactKeys(lock, ['format','mode','operationToken','planDigest','reserveBytes']) || lock.format !== 1 || lock.mode !== 'managed_selected_merge_coordinator_lock_v1' || lock.operationToken !== ownedLock.operationToken || lock.planDigest !== ownedLock.planDigest || lock.reserveBytes !== ownedLock.reserveBytes) reject('managed_selected_store_lock_busy');
      aggregate += size; continue;
    }
    if (!HASH.test(name)) reject();
    opCount++; if (opCount > MAX_ENTRIES) reject();
    const dir = path.join(store, name); ordinary(dir); if (!statSync(dir).isDirectory()) reject();
    const { value: intent } = readCanonical(path.join(dir,'intent.json'), 64 * 1024);
    const v2 = intent?.format === 2 && intent?.mode === 'managed_selected_merge_intent_v2';
    const allowed = v2 ? ['intent.json','receipt.json','temporary.index','plan.json','checkpoint.json','stable-index.before'] : ['intent.json','receipt.json','temporary.index'];
    const entries = readdirSync(dir); if (entries.some(x => !allowed.includes(x)) || !entries.includes('intent.json') || entries.length > allowed.length) reject();
    for (const n of entries) { const f = path.join(dir,n); ordinary(f); const st = statSync(f); if (!st.isFile() || st.size > MAX_BYTES) reject(); aggregate += st.size; }
    const commonValid = intent?.state === 'started' && intent.planDigest === name && HASH.test(intent.checkpointDigest || '') && Array.isArray(intent.selectedPaths) && OID.test(intent.expectedStableHeadBefore || '') && OID.test(intent.expectedStableHeadAfter || '') && OID.test(intent.expectedTree || '');
    if (!commonValid || (v2 ? !exactKeys(intent, INTENT_V2_KEYS) || intent.format !== 2 || intent.mode !== 'managed_selected_merge_intent_v2' || !INTENT_V2_KEYS.slice(-3).every(k => HASH.test(intent[k] || '')) : !exactKeys(intent, INTENT_KEYS) || intent.format !== 1 || intent.mode !== 'managed_selected_merge_intent_v1')) reject();
    if (v2) validateDurableEvidence(dir, intent);
    const isPending = inProgressPlan && name === inProgressPlan.planDigest;
    if (isPending) {
      if (canonical(intent) !== canonical(inProgressPlan.intent) || entries.includes('receipt.json')) reject();
    } else {
      if (!entries.includes('receipt.json')) reject();
      const { value: receipt } = readCanonical(path.join(dir,'receipt.json'), 64 * 1024); validateReceipt(receipt, intent);
    }
    if (aggregate > MAX_BYTES) reject();
  }
  return { opCount, aggregate };
}
function validateDurableEvidence(dir, intent, bundle) {
  const planFile = path.join(dir, 'plan.json'), checkpointFile = path.join(dir, 'checkpoint.json'), indexFile = path.join(dir, 'stable-index.before');
  for (const file of [planFile, checkpointFile, indexFile]) if (!existsSync(file)) reject();
  const { value: savedPlan } = readCanonical(planFile, MAX_EVIDENCE);
  const { value: savedCheckpoint } = readCanonical(checkpointFile, MAX_EVIDENCE);
  ordinary(indexFile); const indexStat = statSync(indexFile); if (indexStat.size > MAX_FILE) reject();
  const savedIndex = readFileSync(indexFile); if (savedIndex.length !== indexStat.size) reject();
  const planBytes = Buffer.from(canonical(savedPlan) + '\n'), checkpointBytes = Buffer.from(canonical(savedCheckpoint) + '\n');
  if (shaBytes(planBytes) !== intent.planFileSha256 || shaBytes(checkpointBytes) !== intent.checkpointFileSha256 || shaBytes(savedIndex) !== intent.stableIndexBeforeSha256) reject();
  safePlan(savedPlan);
  let validated; try { validated = validateManagedSourceCheckpoint(savedCheckpoint); } catch { reject(); }
  if (savedPlan.planDigest !== intent.planDigest || savedPlan.checkpointDigest !== intent.checkpointDigest
      || validated.checkpointDigest !== intent.checkpointDigest || savedPlan.stable?.rawIndexSha256 !== intent.stableIndexBeforeSha256
      || savedPlan.stable?.head !== intent.expectedStableHeadBefore || savedPlan.expectedCommit !== intent.expectedStableHeadAfter
      || savedPlan.expectedMergedTree !== intent.expectedTree || canonical(savedPlan.selectedPaths) !== canonical(intent.selectedPaths)
      || canonical(validated.files.map(file=>file.path)) !== canonical(intent.selectedPaths)
      || (bundle && (!planBytes.equals(bundle.planBytes) || !checkpointBytes.equals(bundle.checkpointBytes) || !savedIndex.equals(bundle.stableIndex)))) reject();
}
/** Read only a fully verified v2 operation. Legacy receipts are never backfilled. */
export function readSelectedMergeEvidence({ sandboxRoot, planDigest } = {}) {
  if (typeof sandboxRoot !== 'string' || !path.isAbsolute(sandboxRoot) || path.resolve(sandboxRoot) !== sandboxRoot || !HASH.test(planDigest || '')) reject('managed_selected_store_evidence_invalid');
  checkChain(sandboxRoot);
  const store = path.join(sandboxRoot, 'artifacts', 'managed-selected-merges');
  checkChain(store); preflight(store);
  const dir = path.join(store, planDigest);
  checkChain(dir);
  const {value:intent} = readCanonical(path.join(dir,'intent.json'),64*1024);
  if (intent.format !== 2 || intent.mode !== 'managed_selected_merge_intent_v2') reject('managed_selected_store_durable_evidence_required');
  validateDurableEvidence(dir,intent);
  const {value:plan} = readCanonical(path.join(dir,'plan.json'),MAX_EVIDENCE);
  const {value:receipt} = readCanonical(path.join(dir,'receipt.json'),64*1024);
  validateReceipt(receipt,intent);
  if (plan.planDigest !== planDigest || plan.sandbox?.worktreeRoot !== sandboxRoot) reject('managed_selected_store_evidence_invalid');
  // Recheck coordinator/unknown/partial operations after bounded record reads.
  preflight(store);
  return {plan,receipt};
}

/** reserveBytes is the engine's prevalidated upper bound for temporary.index; default 4 MiB. The engine must reject plans whose conservative index bound exceeds it. finish() checks actual index bytes and the unconsumed reservation against the 32 MiB store ceiling. */
export function claimSelectedMergeStore({ sandboxRoot, plan, assertTrusted, reserveBytes = DEFAULT_RESERVE_BYTES, durableBundle } = {}) {
  safePlan(plan);
  let bundle, intent;
  if (durableBundle !== undefined) {
    if (!exactKeys(durableBundle, ['checkpoint','stableIndex']) || !Buffer.isBuffer(durableBundle.stableIndex)) reject('managed_selected_store_evidence_invalid');
    assertJsonValue(plan);
    const planBytes = Buffer.from(canonical(plan) + '\n'), checkpointBytes = Buffer.from(canonical(durableBundle.checkpoint) + '\n');
    if (planBytes.length > MAX_EVIDENCE || checkpointBytes.length > MAX_EVIDENCE || durableBundle.stableIndex.length > MAX_FILE || !HASH.test(plan.stable?.rawIndexSha256 || '') || shaBytes(durableBundle.stableIndex) !== plan.stable.rawIndexSha256) reject('managed_selected_store_evidence_invalid');
    let checkpoint; try { checkpoint = validateManagedSourceCheckpoint(durableBundle.checkpoint); } catch { reject('managed_selected_store_evidence_invalid'); }
    if (checkpoint.checkpointDigest !== plan.checkpointDigest) reject('managed_selected_store_evidence_invalid');
    bundle = { planBytes, checkpointBytes, stableIndex: Buffer.from(durableBundle.stableIndex) };
    intent = { format: 2, mode: 'managed_selected_merge_intent_v2', planDigest: plan.planDigest, checkpointDigest: plan.checkpointDigest, selectedPaths: [...plan.selectedPaths], expectedStableHeadBefore: plan.stable.head, expectedStableHeadAfter: plan.expectedCommit, expectedTree: plan.expectedMergedTree, planFileSha256: shaBytes(planBytes), checkpointFileSha256: shaBytes(checkpointBytes), stableIndexBeforeSha256: shaBytes(bundle.stableIndex), state: 'started' };
  } else {
    intent = { format: 1, mode: 'managed_selected_merge_intent_v1', planDigest: plan.planDigest, checkpointDigest: plan.checkpointDigest, selectedPaths: [...plan.selectedPaths], expectedStableHeadBefore: plan.stable.head, expectedStableHeadAfter: plan.expectedCommit, expectedTree: plan.expectedMergedTree, state: 'started' };
  }
  if (!Number.isSafeInteger(reserveBytes) || reserveBytes < 0 || reserveBytes > MAX_RESERVE_BYTES) reject('managed_selected_store_reservation_invalid');
  if (typeof assertTrusted !== 'function') reject('managed_selected_store_trust_denied');
  const root = path.resolve(sandboxRoot || '');
  if (!sandboxRoot || !existsSync(root) || !ordinary(root) || !statSync(root).isDirectory()) reject('managed_selected_store_root_invalid');
  checkChain(root);
  const artifacts = path.join(root,'artifacts'), store = path.join(artifacts,'managed-selected-merges');
  if (existsSync(artifacts)) { ordinary(artifacts); if (!statSync(artifacts).isDirectory()) reject(); }
  if (existsSync(store)) { ordinary(store); if (!statSync(store).isDirectory()) reject(); }
  const operationDir = path.join(store, plan.planDigest);
  if (existsSync(operationDir)) reject('managed_selected_store_plan_already_used');
  const firstScan = preflight(store);
  const token = randomUUID();
  const lock = { format: 1, mode: 'managed_selected_merge_coordinator_lock_v1', operationToken: token, planDigest: plan.planDigest, reserveBytes };
  const lockBytes = Buffer.from(canonical(lock)+'\n');
  const evidenceBytes = bundle ? bundle.planBytes.length + bundle.checkpointBytes.length + bundle.stableIndex.length : 0;
  const intentBytes = Buffer.from(canonical(intent)+'\n');
  if (firstScan.opCount >= MAX_ENTRIES || firstScan.aggregate + lockBytes.length + evidenceBytes + intentBytes.length + reserveBytes + RECEIPT_RESERVE_BYTES > MAX_BYTES) reject();
  // Last trust check before any mkdir, lock, or intent creation.
  let trusted = false; try { trusted = assertTrusted() === true; } catch { trusted = false; }
  if (!trusted) reject('managed_selected_store_trust_denied');
  if (!existsSync(artifacts)) mkdirSync(artifacts);
  if (!existsSync(store)) mkdirSync(store);
  checkChain(artifacts); checkChain(store);
  const lockPath = path.join(store,'.coordinator.lock');
  writeExclusive(lockPath, lockBytes, 4096);
  syncDir(store);
  // Recheck under our exclusive lock; the initial read-only count may have gone stale.
  const lockedScan = preflight(store, { ownedLock: lock });
  if (lockedScan.opCount >= MAX_ENTRIES || lockedScan.aggregate + evidenceBytes + intentBytes.length + RECEIPT_RESERVE_BYTES + reserveBytes > MAX_BYTES) reject();
  mkdirSync(operationDir);
  syncDir(store);
  const intentPath = path.join(operationDir,'intent.json');
  try { writeExclusive(intentPath, intentBytes, 64 * 1024); }
  catch { reject(); }
  if (bundle) {
    try {
      writeExclusive(path.join(operationDir, 'plan.json'), bundle.planBytes, MAX_EVIDENCE);
      writeExclusive(path.join(operationDir, 'checkpoint.json'), bundle.checkpointBytes, MAX_EVIDENCE);
      writeExclusive(path.join(operationDir, 'stable-index.before'), bundle.stableIndex, MAX_FILE);
      validateDurableEvidence(operationDir, intent, bundle);
    } catch { reject(); }
  }
  let settled = false;
  function release() {
    const current = readCanonical(lockPath,4096).value;
    if (!exactKeys(current,['format','mode','operationToken','planDigest','reserveBytes']) || current.format !== 1 || current.mode !== 'managed_selected_merge_coordinator_lock_v1' || current.operationToken !== token || current.planDigest !== plan.planDigest || current.reserveBytes !== reserveBytes) reject();
    unlinkSync(lockPath); syncDir(store);
  }
  return Object.freeze({ directory: operationDir, intentPath,
    finish(receipt) {
      if (settled) reject();
      validateReceipt(receipt,intent);
      const receiptBytes = Buffer.from(canonical(receipt)+'\n');
      if (receiptBytes.length > RECEIPT_RESERVE_BYTES) reject();
      const current = preflight(store, { ownedLock: lock, inProgressPlan: { planDigest: plan.planDigest, intent } });
      const indexPath = path.join(operationDir,'temporary.index');
      let indexBytes = 0;
      if (existsSync(indexPath)) { ordinary(indexPath); indexBytes = statSync(indexPath).size; }
      if (indexBytes > reserveBytes || current.aggregate + receiptBytes.length + (reserveBytes - indexBytes) > MAX_BYTES) reject();
      writeExclusive(path.join(operationDir,'receipt.json'), receiptBytes, RECEIPT_RESERVE_BYTES);
      const completed = preflight(store, { ownedLock: lock });
      if (completed.aggregate > MAX_BYTES) reject();
      settled = true;
      release();
      return path.join(operationDir,'receipt.json');
    },
    fail(code) {
      if (settled) reject();
      if (typeof code !== 'string' || !new Set(['selected_merge_precondition_failed','selected_merge_result_unknown','selected_merge_postcondition_failed','selected_merge_tree_mismatch','selected_merge_commit_mismatch','verification_failed','source_merge_failed']).has(code)) reject('managed_selected_store_failure_code_invalid');
      // Preserve coordinator lock: an execution failure/uncertain result is maintenance-only.
      settled = true;
      return false;
    },
  });
}

