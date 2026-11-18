import { createHash } from 'node:crypto';
import { safeSelectedPath as isManagedSelectedMergePathSafe } from './managed-selected-merge.mjs';

const fail = (code, message) => {
  const error = new TypeError(message);
  error.code = code;
  throw error;
};
const HASH = /^[a-f0-9]{64}$/;
const OID = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/;
const MAX_SELECTED_FILES = 20_000;
const MAX_FILE_BYTES = 16 * 1024 * 1024;
const MAX_SELECTED_BYTES = 64 * 1024 * 1024;
const MAX_CONTRACT_BYTES = 16 * 1024 * 1024;
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const exact = (value, keys) => record(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const canonical = value => Array.isArray(value)
  ? `[${value.map(canonical).join(',')}]`
  : value && typeof value === 'object'
    ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`
    : JSON.stringify(value);
const sha256 = value => createHash('sha256').update(typeof value === 'string' ? value : canonical(value)).digest('hex');
const sealed = (value, field) => sha256(Object.fromEntries(Object.entries(value).filter(([key]) => key !== field)));
const keys = (...value) => value;
const failIf = (condition, code, message) => { if (condition) fail(code, message); };

function safeSelectedPath(value) {
  if (!isManagedSelectedMergePathSafe(value) || typeof value !== 'string' || !value || value.startsWith('/') || value.includes('\\') || value.includes(':') || /[\u0000-\u001f\u007f<>"|?*]/.test(value)) return false;
  const parts = value.split('/');
  if (parts.some(part => !part || part === '.' || part === '..' || /[. ]$/.test(part) || /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i.test(part))) return false;
  const denied = new Set(['.git', '.venv', 'venv', 'node_modules', 'data', 'artifacts', 'dist', 'dist-electron', '__pycache__', 'profile', 'profiles', 'user-data', 'workspace', 'workspaces', 'config', 'configs', 'settings', '.config', '.aws', '.ssh', 'key', 'keys', '.keys', 'signing', 'keystore', 'keystores', 'key-store', 'key-stores', 'key_store', 'key_stores', 'keyring', 'keyrings', 'cert', 'certs', 'certificate', 'certificates', 'pki', '.pki', 'tls', 'ssl', 'gpg', '.gpg', 'gnupg', '.gnupg', 'secrets', 'private', 'credentials', 'llm_secrets', '.managed-runtime']);
  const sensitivePathPart = /(?:^|[._-])(?:secret|secrets|credential|credentials|private|token|password|passwd|apikey|api-key|keyring)(?:$|[._-])/i;
  const sensitiveFilename = /^(?:\.env(?:\..*)?|\.npmrc|\.pypirc|\.netrc|credentials(?:\.json)?|secrets(?:\.json)?|id_rsa|id_ed25519|known_hosts|llm_(?:secrets|models)\.(?:json|ya?ml)|.*\.(?:env|pem|key|p12|pfx|p7b|p7c|cer|crt))$/i;
  if (parts.some(part => denied.has(part.toLowerCase()) || sensitivePathPart.test(part) || sensitiveFilename.test(part))) return false;
  return ['agent-core/', 'desktop/', 'docs/', 'protocol/', 'scripts/', 'evolve/tools/', 'evolve/skills/', 'evolve/tool-catalog/'].some(prefix => value.startsWith(prefix));
}
function digestField(value, field, code) {
  failIf(!HASH.test(value[field] || '') || value[field] !== sealed(value, field), code, `${field} does not match canonical payload`);
}
function validateStableState(value) {
  failIf(!exact(value, keys('head', 'indexSha256', 'worktreeDigest', 'gitMetadataDigest', 'gitCommonId', 'worktreeId'))
    || !OID.test(value.head || '') || !HASH.test(value.indexSha256 || '') || !HASH.test(value.worktreeDigest || '')
    || !HASH.test(value.gitMetadataDigest || '') || !SAFE_ID.test(value.gitCommonId || '') || !SAFE_ID.test(value.worktreeId || ''),
  'self_evolution_stable_state_invalid', 'Stable state snapshot is malformed');
  return value;
}
function validateFiles(files) {
  failIf(!Array.isArray(files) || files.length === 0 || files.length > MAX_SELECTED_FILES, 'self_evolution_files_invalid', 'Selected change manifest is empty, oversized, or malformed');
  let previous = '';
  let totalBytes = 0;
  const foldedPaths = new Set();
  for (const file of files) {
    failIf(!exact(file, keys('path', 'action', 'beforeSha256', 'afterSha256', 'beforeMode', 'afterMode', 'beforeOid', 'afterOid', 'beforeSize', 'afterSize', 'contentRef'))
      || !safeSelectedPath(file.path) || file.path <= previous || foldedPaths.has(file.path.toLocaleLowerCase("en-US")) || (previous && file.path.startsWith(`${previous}/`))
      || !['add', 'modify', 'delete'].includes(file.action)
      || !(file.beforeSha256 === null || HASH.test(file.beforeSha256))
      || !(file.afterSha256 === null || HASH.test(file.afterSha256))
      || !(file.beforeMode === null || /^(?:100644|100755)$/.test(file.beforeMode))
      || !(file.afterMode === null || /^(?:100644|100755)$/.test(file.afterMode))
      || !(file.beforeOid === null || OID.test(file.beforeOid))
      || !(file.afterOid === null || OID.test(file.afterOid))
      || !(file.beforeSize === null || (Number.isSafeInteger(file.beforeSize) && file.beforeSize >= 0 && file.beforeSize <= MAX_FILE_BYTES))
      || !(file.afterSize === null || (Number.isSafeInteger(file.afterSize) && file.afterSize >= 0 && file.afterSize <= MAX_FILE_BYTES))
      || !(file.contentRef === null || SAFE_ID.test(file.contentRef)),
    'self_evolution_files_invalid', 'Selected change manifest has unsafe, unsorted, duplicate, or malformed entries');
    if (file.action === 'add') failIf(file.beforeSha256 !== null || file.beforeMode !== null || file.beforeOid !== null || file.beforeSize !== null || !HASH.test(file.afterSha256 || '') || !file.afterMode || !file.afterOid || !file.contentRef || file.afterSize === null, 'self_evolution_files_invalid', 'Added file must have only a bound after-image');
    if (file.action === 'modify') failIf(!HASH.test(file.beforeSha256 || '') || !HASH.test(file.afterSha256 || '') || !file.beforeMode || !file.afterMode || !file.beforeOid || !file.afterOid || file.beforeSize === null || file.afterSize === null || !file.contentRef, 'self_evolution_files_invalid', 'Modified file must bind before and after images');
    if (file.action === 'delete') failIf(!HASH.test(file.beforeSha256 || '') || !file.beforeMode || !file.beforeOid || file.afterSha256 !== null || file.afterMode !== null || file.afterOid !== null || file.contentRef !== null, 'self_evolution_files_invalid', 'Deleted file must have only a bound before-image');
    if (file.action === 'add') failIf(file.beforeSize !== null || file.afterSize === null, 'self_evolution_files_invalid', 'Added file size binding is invalid');
    if (file.action === 'modify') failIf(file.beforeSize === null || file.afterSize === null, 'self_evolution_files_invalid', 'Modified file sizes are required');
    if (file.action === 'delete') failIf(file.beforeSize === null || file.afterSize !== null, 'self_evolution_files_invalid', 'Deleted file size binding is invalid');
    totalBytes += file.afterSize ?? 0;
    failIf(totalBytes > MAX_SELECTED_BYTES, 'self_evolution_files_invalid', 'Selected after-image exceeds aggregate byte limit');
    foldedPaths.add(file.path.toLocaleLowerCase("en-US"));
    previous = file.path;
  }
  return files;
}
function validateEvidence(refs, candidateDigest) {
  failIf(!Array.isArray(refs) || refs.length === 0 || refs.length > 256, 'self_evolution_verification_unbound', 'At least one registered verification evidence reference is required');
  for (const item of refs) failIf(!exact(item, keys('checkId', 'candidateDigest', 'evidenceRef', 'status'))
    || !SAFE_ID.test(item.checkId || '') || !SAFE_ID.test(item.evidenceRef || '')
    || item.candidateDigest !== candidateDigest || item.status !== 'passed',
  'self_evolution_verification_unbound', 'Verification evidence is malformed, failed, or belongs to a different candidate digest');
  return refs;
}

export function validateSelfEvolutionCandidateBinding(value) {
  failIf(!exact(value, keys('format', 'mode', 'taskId', 'attemptId', 'candidateId', 'candidateDigest', 'baselineManifestDigest', 'gitCommonId', 'stableWorktreeId', 'candidateWorktreeId', 'baseHead'))
    || value.format !== 1 || value.mode !== 'self_evolution_candidate_binding_v1'
    || !SAFE_ID.test(value.taskId || '') || !SAFE_ID.test(value.attemptId || '') || !SAFE_ID.test(value.candidateId || '')
    || !HASH.test(value.candidateDigest || '') || !HASH.test(value.baselineManifestDigest || '')
    || !SAFE_ID.test(value.gitCommonId || '') || !SAFE_ID.test(value.stableWorktreeId || '')
    || !SAFE_ID.test(value.candidateWorktreeId || '') || value.stableWorktreeId === value.candidateWorktreeId
    || !OID.test(value.baseHead || ''),
  'self_evolution_candidate_binding_invalid', 'Host candidate binding is malformed');
  return value;
}

export function validateSelfEvolutionMergeCheckpoint(value, candidate) {
  validateSelfEvolutionCandidateBinding(candidate);
  failIf(!exact(value, keys('format', 'mode', 'taskId', 'attemptId', 'candidateId', 'candidateDigest', 'baselineManifestDigest', 'stableState', 'candidateHead', 'candidateWorktreeDigest', 'selectedFiles', 'verificationRefs', 'privateBlobSetRef', 'checkpointDigest'))
    || value.format !== 1 || value.mode !== 'self_evolution_merge_checkpoint_v1',
  'self_evolution_checkpoint_invalid', 'Checkpoint envelope is malformed');
  failIf(value.taskId !== candidate.taskId || value.attemptId !== candidate.attemptId || value.candidateId !== candidate.candidateId
    || value.candidateDigest !== candidate.candidateDigest || value.baselineManifestDigest !== candidate.baselineManifestDigest,
  'self_evolution_candidate_mismatch', 'Checkpoint is not bound to the Host candidate identity');
  validateStableState(value.stableState);
  failIf(value.stableState.gitCommonId !== candidate.gitCommonId || value.stableState.worktreeId !== candidate.stableWorktreeId
    || value.stableState.head !== candidate.baseHead || !OID.test(value.candidateHead || '') || !HASH.test(value.candidateWorktreeDigest || '')
    || !SAFE_ID.test(value.privateBlobSetRef || ''),
  'self_evolution_checkpoint_identity_mismatch', 'Checkpoint worktree or candidate state differs from Host binding');
  validateFiles(value.selectedFiles);
  validateEvidence(value.verificationRefs, candidate.candidateDigest);
  failIf(Buffer.byteLength(canonical(value), 'utf8') > MAX_CONTRACT_BYTES, 'self_evolution_checkpoint_invalid', 'Checkpoint exceeds canonical size limit');
  digestField(value, 'checkpointDigest', 'self_evolution_checkpoint_digest_mismatch');
  return value;
}

export function validateSelfEvolutionPromotionPreview(value, checkpoint) {
  failIf(!exact(value, keys('format', 'mode', 'taskId', 'attemptId', 'candidateId', 'candidateDigest', 'checkpointDigest', 'stableState', 'stableStateDigest', 'selectedFiles', 'verificationRefs', 'blockers', 'previewDigest'))
    || value.format !== 1 || value.mode !== 'self_evolution_promotion_preview_v1',
  'self_evolution_preview_invalid', 'Promotion preview envelope is malformed');
  failIf(value.taskId !== checkpoint.taskId || value.attemptId !== checkpoint.attemptId || value.candidateId !== checkpoint.candidateId
    || value.candidateDigest !== checkpoint.candidateDigest || value.checkpointDigest !== checkpoint.checkpointDigest,
  'self_evolution_candidate_mismatch', 'Promotion preview is not bound to this checkpoint');
  validateStableState(value.stableState);
  failIf(value.stableStateDigest !== sha256(value.stableState) || canonical(value.stableState) !== canonical(checkpoint.stableState),
    'self_evolution_stable_identity_mismatch', 'Preview stable state differs from checkpoint stable state');
  failIf(canonical(value.selectedFiles) !== canonical(checkpoint.selectedFiles), 'self_evolution_preview_files_mismatch', 'Preview changed the checkpoint file manifest');
  validateEvidence(value.verificationRefs, value.candidateDigest);
  failIf(canonical(value.verificationRefs) !== canonical(checkpoint.verificationRefs), 'self_evolution_verification_unbound', 'Preview changed registered verification references');
  failIf(!Array.isArray(value.blockers) || value.blockers.length !== 0, 'self_evolution_preview_blocked', 'Promotion preview contains unresolved blockers');
  failIf(Buffer.byteLength(canonical(value), 'utf8') > MAX_CONTRACT_BYTES, 'self_evolution_preview_invalid', 'Promotion preview exceeds canonical size limit');
  digestField(value, 'previewDigest', 'self_evolution_preview_digest_mismatch');
  return value;
}

const CONFIRMATION_KEYS = keys('format', 'mode', 'decision', 'taskId', 'attemptId', 'candidateId', 'candidateDigest', 'checkpointDigest', 'stableStateDigest', 'previewDigest', 'selectedPaths', 'operationId', 'nonce', 'issuer', 'issuedAt');
export function validateSelfEvolutionNativeConfirmation(value, preview) {
  failIf(!exact(value, CONFIRMATION_KEYS) || value.format !== 1 || value.mode !== 'self_evolution_native_confirmation_v1'
    || value.decision !== 'accepted' || value.issuer !== 'electron-main'
    || !SAFE_ID.test(value.operationId || '') || !SAFE_ID.test(value.nonce || '')
    || typeof value.issuedAt !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(value.issuedAt)
    || Number.isNaN(Date.parse(value.issuedAt)) || new Date(value.issuedAt).toISOString() !== value.issuedAt,
  'self_evolution_confirmation_invalid', 'Native confirmation receipt is malformed');
  failIf(value.taskId !== preview.taskId || value.attemptId !== preview.attemptId || value.candidateId !== preview.candidateId
    || value.candidateDigest !== preview.candidateDigest || value.checkpointDigest !== preview.checkpointDigest
    || value.stableStateDigest !== preview.stableStateDigest || value.previewDigest !== preview.previewDigest
    || canonical(value.selectedPaths) !== canonical(preview.selectedFiles.map(file => file.path)),
  'self_evolution_confirmation_mismatch', 'Native confirmation is not bound to the exact candidate, preview, and paths');
  return value;
}

export function validateSelfEvolutionMergeIntent(value, checkpoint, preview, confirmation) {
  const intentKeys = keys('format', 'mode', 'state', 'operationId', 'taskId', 'attemptId', 'candidateId', 'candidateDigest', 'checkpointDigest', 'stableStateDigest', 'previewDigest', 'confirmationNonce', 'intentDigest');
  failIf(!exact(value, intentKeys) || value.format !== 1 || value.mode !== 'self_evolution_merge_intent_v1'
    || value.state !== 'prepared' || !SAFE_ID.test(value.operationId || '') || !SAFE_ID.test(value.confirmationNonce || ''),
  'self_evolution_intent_unavailable', 'Intent must be a fresh prepared single-use operation');
  failIf(value.operationId !== confirmation.operationId || value.confirmationNonce !== confirmation.nonce
    || value.taskId !== checkpoint.taskId || value.attemptId !== checkpoint.attemptId || value.candidateId !== checkpoint.candidateId
    || value.candidateDigest !== checkpoint.candidateDigest || value.checkpointDigest !== checkpoint.checkpointDigest
    || value.stableStateDigest !== preview.stableStateDigest || value.previewDigest !== preview.previewDigest,
  'self_evolution_intent_mismatch', 'Durable intent is not bound to this confirmation and promotion preview');
  digestField(value, 'intentDigest', 'self_evolution_intent_digest_mismatch');
  return value;
}

/**
 * Strict read-only contract gate, not an execution adapter. The verifier callback
 * must only be supplied by the trusted Electron main-process bridge; renderer
 * code and ordinary callers cannot establish native-confirmation provenance.
 */
export function preflightSelfEvolutionSelectedMerge({ candidate, checkpoint, preview, nativeConfirmation, intent } = {}, { verifyNativeConfirmation } = {}) {
  validateSelfEvolutionCandidateBinding(candidate);
  validateSelfEvolutionMergeCheckpoint(checkpoint, candidate);
  validateSelfEvolutionPromotionPreview(preview, checkpoint);
  validateSelfEvolutionNativeConfirmation(nativeConfirmation, preview);
  let nativeConfirmationVerified = false;
  if (typeof verifyNativeConfirmation === 'function') {
    try { nativeConfirmationVerified = verifyNativeConfirmation(nativeConfirmation) === true; }
    catch { nativeConfirmationVerified = false; }
  }
  failIf(!nativeConfirmationVerified,
    'self_evolution_confirmation_untrusted', 'Confirmation was not authenticated by trusted native host state');
  validateSelfEvolutionMergeIntent(intent, checkpoint, preview, nativeConfirmation);
  // Validation cannot re-read filesystem/Git or atomically consume durable intent.
  // Until the trusted executor implements those operations, never proceed to writes.
  return Object.freeze({
    format: 1, mode: 'self_evolution_selected_merge_preflight_v1', status: 'blocked',
    code: 'self_evolution_merge_integration_required', taskId: candidate.taskId,
    attemptId: candidate.attemptId, candidateId: candidate.candidateId,
    candidateDigest: candidate.candidateDigest, checkpointDigest: checkpoint.checkpointDigest,
    previewDigest: preview.previewDigest, operationId: intent.operationId,
    selectedPaths: Object.freeze(preview.selectedFiles.map(file => file.path)),
    sourceMergeExecuted: false, readyForLaunch: false,
    executionGates: Object.freeze({ trustedNativeConfirmationConnected: false, completeManifestRevalidated: false, transactionalWritePathConnected: false }),
    reason: 'Shape validation and an injected verifier callback do not authenticate native confirmation or re-read the full manifest. No trusted Git re-read, complete manifest, durable intent CAS, transaction recovery, or self-evolution write executor is connected.',
  });
}

export function selfEvolutionPromotionPreviewDigest(preview) {
  if (!record(preview)) fail('self_evolution_preview_invalid', 'Promotion preview must be an object');
  return sealed(preview, 'previewDigest');
}
