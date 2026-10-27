import { createHash, randomUUID } from 'node:crypto';
import path from 'node:path';
import { createManagedSourceJournal } from './managed-source-journal.mjs';

const PLAN_KEYS = ['blockers', 'deliveryPaths', 'expectedMergedTree', 'format', 'installationId', 'mode', 'namespaceDigest', 'preservationDigest', 'reviewDigest', 'sandbox', 'stable'];
const IDENTITY_KEYS = ['branch', 'head', 'indexDigest', 'root'];
const DELIVERY_KEYS = ['afterSha256', 'beforeSha256', 'path'];
const CONFIRMATION_KEYS = ['namespaceDigest', 'planDigest', 'reviewDigest'];
const ADAPTER_KEYS = ['merge', 'revalidate', 'verify'];
const HEX_256 = /^[a-f0-9]{64}$/;
const HEX_OID = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
const SAFE_PREFIXES = ['agent-core/', 'desktop/', 'docs/', 'protocol/', 'scripts/'];
const NEVER_SEGMENTS = new Set(['.git', '.venv', 'venv', 'node_modules', 'data', 'artifacts', 'dist', 'dist-electron', '__pycache__', 'profile', 'profiles', 'user-data', 'workspace', 'workspaces', 'config-store', 'llm_secrets', 'secrets', 'private', 'credentials', '.managed-runtime']);
const SECRET_SEGMENT = /^(?:\.env(?:\..*)?|llm_secrets\.(?:json|ya?ml)|llm_models\.(?:json|ya?ml)|.*\.(?:pem|key|p12|pfx|cer|crt))$/i;
const SAFE_SEGMENT = /^[A-Za-z0-9_@+.,= -]+$/;
const WINDOWS_RESERVED = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i;
const MAX_PLAN_BYTES = 1024 * 1024;
// Keep the journal receipt well below its 128 KiB line limit, including fixed fields.
const MAX_RECEIPT_PATHS_BYTES = 64 * 1024;
const MAX_PATHS = 2048;
const MAX_PATH_LENGTH = 1024;

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value) && (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null);
}
function exactKeys(value, expected, label) {
  if (!isPlainObject(value) || Object.keys(value).sort().join('\0') !== [...expected].sort().join('\0')) throw new Error(`Invalid ${label} shape`);
}
function assertJson(value, seen = new Set()) {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return;
  if (typeof value === 'number') { if (!Number.isFinite(value)) throw new Error('Plan contains a non-finite number'); return; }
  if (typeof value !== 'object' || seen.has(value)) throw new Error('Plan must contain only acyclic plain JSON values');
  seen.add(value);
  if (Array.isArray(value)) {
    const keys = Reflect.ownKeys(value);
    if (keys.length !== value.length + 1 || keys.some((key) => typeof key !== 'string' || (key !== 'length' && !/^(?:0|[1-9][0-9]*)$/.test(key)))) throw new Error('Plan arrays must be dense JSON arrays');
    for (let index = 0; index < value.length; index++) {
      const descriptor = Object.getOwnPropertyDescriptor(value, String(index));
      if (!descriptor || !descriptor.enumerable || !('value' in descriptor)) throw new Error('Plan arrays cannot contain holes or accessors');
      assertJson(descriptor.value, seen);
    }
  } else {
    if (!isPlainObject(value) || Reflect.ownKeys(value).some((key) => typeof key !== 'string')) throw new Error('Plan must contain only plain JSON objects');
    for (const key of Object.keys(value)) {
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (!descriptor?.enumerable || !('value' in descriptor)) throw new Error('Plan objects cannot contain hidden values or accessors');
      assertJson(descriptor.value, seen);
    }
  }
  seen.delete(value);
}
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (isPlainObject(value)) return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}
function digest(value) { return createHash('sha256').update(canonical(value), 'utf8').digest('hex'); }
function deepFreeze(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    for (const child of Object.values(value)) deepFreeze(child);
    Object.freeze(value);
  }
  return value;
}
function normalizedComparable(value) { return process.platform === 'win32' ? value.toLowerCase() : value; }
function normalizedRoot(root, label) {
  if (typeof root !== 'string' || !path.isAbsolute(root) || root.includes('\0')) throw new Error(`Invalid ${label} root`);
  const resolved = path.resolve(root);
  if (path.normalize(root) !== resolved) throw new Error(`${label} root must be normalized`);
  const parts = resolved.slice(path.parse(resolved).root.length).split(path.sep).filter(Boolean);
  if (parts.some((part) => !SAFE_SEGMENT.test(part) || /[. ]$/.test(part) || WINDOWS_RESERVED.test(part))) throw new Error(`${label} root contains an unsafe Windows component`);
  return resolved;
}
function validatePath(value) {
  if (typeof value !== 'string' || value.length === 0 || value.length > MAX_PATH_LENGTH || value.includes('\\') || value.startsWith('/') || value.includes('\0')) throw new Error('Invalid delivery path');
  const segments = value.split('/');
  if (segments.some((part) => !SAFE_SEGMENT.test(part) || /[. ]$/.test(part) || WINDOWS_RESERVED.test(part))) throw new Error('Unsafe delivery path segment');
  if (!SAFE_PREFIXES.some((prefix) => value.startsWith(prefix))) throw new Error('Delivery path is outside allowed source prefixes');
  if (segments.some((part) => NEVER_SEGMENTS.has(part.toLowerCase()) || SECRET_SEGMENT.test(part))) throw new Error('Protected path cannot be delivered');
  return value;
}
function validateBody(body) {
  assertJson(body);
  exactKeys(body, PLAN_KEYS, 'plan body');
  if (body.format !== 1 || body.mode !== 'managed_source_merge_plan_v1') throw new Error('Unsupported plan format or mode');
  if (typeof body.installationId !== 'string' || !UUID.test(body.installationId)) throw new Error('Invalid installation identity');
  for (const name of ['namespaceDigest', 'reviewDigest', 'expectedMergedTree', 'preservationDigest']) {
    if (typeof body[name] !== 'string' || !(name === 'expectedMergedTree' ? HEX_OID : HEX_256).test(body[name])) throw new Error(`Invalid ${name}`);
  }
  for (const key of ['stable', 'sandbox']) {
    const item = body[key];
    exactKeys(item, IDENTITY_KEYS, `${key} identity`);
    body[key] = { ...item, root: normalizedRoot(item.root, key) };
    const normalized = body[key];
    if (typeof normalized.head !== 'string' || !HEX_OID.test(normalized.head) || typeof normalized.indexDigest !== 'string' || !HEX_256.test(normalized.indexDigest)) throw new Error(`Invalid ${key} baseline`);
    if (typeof normalized.branch !== 'string' || normalized.branch.trim() === '' || normalized.branch.length > 256 || /[\0\r\n]/.test(normalized.branch)) throw new Error(`Invalid ${key} branch`);
  }
  if (body.sandbox.branch !== 'experiment/sandbox') throw new Error('Sandbox branch must be experiment/sandbox');
  const stableRoot = normalizedComparable(body.stable.root);
  const sandboxRoot = normalizedComparable(body.sandbox.root);
  const stablePrefix = `${stableRoot}${path.sep}`;
  const approvedNestedRoot = path.resolve(body.stable.root, '.worktrees', 'sandbox');
  const approvedNestedComparable = normalizedComparable(approvedNestedRoot);
  const isExactNestedSandbox = sandboxRoot === approvedNestedComparable && body.sandbox.root === approvedNestedRoot;
  const overlaps = stableRoot === sandboxRoot || stableRoot.startsWith(`${sandboxRoot}${path.sep}`) || sandboxRoot.startsWith(stablePrefix);
  if (stableRoot === sandboxRoot || (overlaps && !isExactNestedSandbox)) throw new Error('Source roots must be disjoint except for exact stable/.worktrees/sandbox nesting');
  if (!Array.isArray(body.deliveryPaths) || body.deliveryPaths.length < 1 || body.deliveryPaths.length > MAX_PATHS) throw new Error('Invalid delivery path count');
  body.deliveryPaths = body.deliveryPaths.map((entry) => {
    exactKeys(entry, DELIVERY_KEYS, 'delivery path');
    return { ...entry, path: validatePath(entry.path) };
  });
  let prior = null;
  const foldedPaths = new Set();
  for (const entry of body.deliveryPaths) {
    if (entry.beforeSha256 === null && entry.afterSha256 === null) throw new Error('Delivery path must change');
    for (const hash of [entry.beforeSha256, entry.afterSha256]) if (hash !== null && (typeof hash !== 'string' || !HEX_256.test(hash))) throw new Error('Invalid delivery path digest');
    if (prior !== null && prior >= entry.path) throw new Error('Delivery paths must be sorted and unique');
    const folded = entry.path.toLocaleLowerCase('en-US');
    if (foldedPaths.has(folded)) throw new Error('Case-aliased delivery paths are forbidden');
    foldedPaths.add(folded);
    prior = entry.path;
  }
  if (!Array.isArray(body.blockers) || body.blockers.length !== 0) throw new Error('Plans with blockers cannot execute');
  // The journal stores the complete receipt on one bounded line; leave ample space for fixed fields and framing.
  const receiptPathsBytes = Buffer.byteLength(canonical(body.deliveryPaths.map(({ path: sourcePath }) => sourcePath)), 'utf8');
  if (receiptPathsBytes > MAX_RECEIPT_PATHS_BYTES) throw new Error('Selected delivery paths exceed the bounded journal receipt path budget (64 KiB)');
  if (Buffer.byteLength(canonical(body), 'utf8') > MAX_PLAN_BYTES) throw new Error('Plan exceeds the bounded JSON size');
  return body;
}
function freezePlan(plan) { return deepFreeze(JSON.parse(canonical(plan))); }
function validatePlan(plan) {
  assertJson(plan);
  if (!isPlainObject(plan) || Object.keys(plan).length !== PLAN_KEYS.length + 1 || !Object.hasOwn(plan, 'planDigest')) throw new Error('Invalid plan keys');
  const rawBody = {};
  for (const key of PLAN_KEYS) rawBody[key] = plan[key];
  const body = JSON.parse(canonical(rawBody));
  validateBody(body);
  if (typeof plan.planDigest !== 'string' || !HEX_256.test(plan.planDigest) || digest(body) !== plan.planDigest) throw new Error('Plan digest mismatch');
  return freezePlan({ ...body, planDigest: plan.planDigest });
}
function validateConfirmation(value, plan) {
  exactKeys(value, CONFIRMATION_KEYS, 'confirmation');
  if (value.planDigest !== plan.planDigest || value.reviewDigest !== plan.reviewDigest || value.namespaceDigest !== plan.namespaceDigest) throw new Error('Confirmation does not bind the exact plan');
}
function validateAdapter(adapter) {
  if (!isPlainObject(adapter) || Object.keys(adapter).sort().join('\0') !== ADAPTER_KEYS.slice().sort().join('\0') || ADAPTER_KEYS.some((name) => typeof adapter[name] !== 'function')) throw new Error('A trusted transaction adapter with revalidate, merge, and verify methods is required');
}
function validateRevalidation(value, plan) {
  exactKeys(value, ['blockers', 'namespaceDigest', 'planDigest', 'reviewDigest'], 'revalidation receipt');
  if (!Array.isArray(value.blockers) || value.blockers.length !== 0 || value.planDigest !== plan.planDigest || value.reviewDigest !== plan.reviewDigest || value.namespaceDigest !== plan.namespaceDigest) throw new Error('Trusted revalidation did not confirm the exact unblocked plan');
}
function validateReceipt(receipt, plan) {
  exactKeys(receipt, ['deliveryPaths', 'mergeCommit', 'mergedTree', 'preservationDigest', 'sandboxParent', 'stableParent'], 'postcondition receipt');
  const expectedPaths = plan.deliveryPaths.map((item) => item.path);
  if (typeof receipt.mergeCommit !== 'string' || !HEX_OID.test(receipt.mergeCommit) || receipt.mergedTree !== plan.expectedMergedTree || receipt.stableParent !== plan.stable.head || receipt.sandboxParent !== plan.sandbox.head || receipt.preservationDigest !== plan.preservationDigest || !Array.isArray(receipt.deliveryPaths) || canonical(receipt.deliveryPaths) !== canonical(expectedPaths)) throw new Error('Independent postcondition receipt does not match the approved plan');
  return JSON.parse(canonical(receipt));
}
async function closeQuietly(journal) { try { journal?.close(); } catch {} }
async function failQuietly(journal, code) { try { journal?.fail({ code }); } catch {} }

/** Build the canonical, digest-bound plan. It does not grant authorization. */
export function createManagedSourceMergePlan(body) {
  assertJson(body);
  const checked = validateBody(JSON.parse(canonical(body)));
  return { ...checked, planDigest: digest(checked) };
}

/**
 * Execute one merge through an injected trusted in-process adapter only.
 * No production adapter is provided or wired. Revalidation cannot clear blockers
 * in an external review packet; successful source merge always returns readyForLaunch:false.
 */
export async function executeManagedSourceMerge({ directory, plan: untrustedPlan, confirmation: scopeConfirmation, adapter }) {
  const plan = validatePlan(untrustedPlan);
  validateConfirmation(scopeConfirmation, plan);
  validateAdapter(adapter);
  const operationId = randomUUID();
  const identity = { installationId: plan.installationId, namespaceDigest: plan.namespaceDigest };
  const operation = { operationId, planDigest: plan.planDigest, reviewDigest: plan.reviewDigest };

  // Stale plans stop before a journal claim or write.
  validateRevalidation(await adapter.revalidate(plan), plan);
  let journal;
  try { journal = createManagedSourceJournal({ directory, identity, operation }); }
  catch { throw new Error('Could not claim the single-use managed source transaction'); }
  try {
    try { validateRevalidation(await adapter.revalidate(plan), plan); }
    catch { await failQuietly(journal, 'revalidation_failed'); throw new Error('Managed source transaction expired during revalidation'); }
    const { writeId } = journal.beforeWrite({ step: 'source_merge' });
    let command;
    try { command = await adapter.merge(plan, { operationId, writeId }); }
    catch { await failQuietly(journal, 'interrupted'); throw new Error('Managed source merge outcome is unknown; maintenance is required'); }
    if (!isPlainObject(command) || Object.keys(command).length !== 1 || !Object.hasOwn(command, 'exitCode') || !Number.isInteger(command.exitCode)) {
      await failQuietly(journal, 'interrupted');
      throw new Error('Managed source merge returned an unknown outcome; maintenance is required');
    }
    journal.afterWrite({ writeId, exitCode: command.exitCode });
    if (command.exitCode !== 0) throw new Error('Managed source merge failed; maintenance is required');
    let receipt;
    try { receipt = validateReceipt(await adapter.verify(plan), plan); }
    catch { await failQuietly(journal, 'verification_failed'); throw new Error('Managed source merge postconditions failed; maintenance is required'); }
    journal.finish({ receipt });
    return { status: 'source_merge_verified', operationId, planDigest: plan.planDigest, receipt, readyForLaunch: false };
  } finally {
    await closeQuietly(journal);
  }
}





