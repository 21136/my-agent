import { createHash } from 'node:crypto';

const REQUEST_MODE = 'managed_source_selection_request_v1';
const REVIEW_MODE = 'managed_source_review_v1';
const PROPOSAL_MODE = 'managed_source_selection_proposal_v1';
const SHA256 = /^[a-f0-9]{64}$/;
const GIT_OID = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
const MAX_PATHS = 2048;
const MAX_PATH_BYTES = 512;
const MAX_REQUEST_BYTES = 256 * 1024;
const ALLOWED_ROOT_FILES = new Set(['AGENTS.md', '.gitignore', 'start-desktop.bat', 'init-sandbox.bat', 'check-stable-before-sandbox-merge.bat', 'sync-sandbox-worktree.bat']);
const ALLOWED_PREFIXES = ['agent-core/', 'desktop/', 'docs/', 'protocol/', 'scripts/', 'evolve/tools/', 'evolve/skills/', 'evolve/tool-catalog/'];
const NEVER = new Set(['.git', '.venv', 'venv', 'node_modules', 'data', 'artifacts', 'dist', 'dist-electron', '__pycache__', 'profile', 'profiles', 'user-data', 'workspace', 'workspaces', '.managed-runtime']);
const SECRET = /^(?:\.env(?:\..*)?|llm_secrets\.(?:json|ya?ml)|llm_models\.(?:json|ya?ml)|.*\.(?:pem|key|p12|pfx|cer|crt))$/i;
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}`;
  return JSON.stringify(value);
}
function digest(value) { return createHash('sha256').update(canonical(value), 'utf8').digest('hex'); }
function record(v) { return v !== null && typeof v === 'object' && !Array.isArray(v); }
function fail(message) { throw new TypeError(message); }
function safePath(p) {
  if (typeof p !== 'string' || !p || Buffer.byteLength(p, 'utf8') > MAX_PATH_BYTES || p.startsWith('/') || p.includes('\\') || p.includes(':')) return false;
  const parts = p.split('/');
  if (parts.some(s => !s || s === '.' || s === '..' || /[. ]$/.test(s) || /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i.test(s) || !/^[A-Za-z0-9_@+.,= -]+$/.test(s))) return false;
  if (parts.some(part => NEVER.has(part.toLowerCase()) || SECRET.test(part))) return false;
  return ALLOWED_ROOT_FILES.has(p) || ALLOWED_PREFIXES.some(prefix => p.startsWith(prefix));
}
function without(obj, key) { const copy = { ...obj }; delete copy[key]; return copy; }
export function validateManagedSelectionRequest(value) {
  if (!record(value) || Object.keys(value).length !== 4 || !['format', 'mode', 'reviewDigest', 'selectedPaths'].every(k => Object.hasOwn(value, k))) fail('managed selection request keys are invalid');
  if (value.format !== 1 || value.mode !== REQUEST_MODE || typeof value.reviewDigest !== 'string' || !SHA256.test(value.reviewDigest)) fail('managed selection request format or review digest is invalid');
  if (!Array.isArray(value.selectedPaths) || value.selectedPaths.length < 1 || value.selectedPaths.length > MAX_PATHS) fail('managed selection path count is invalid');
  const seen = new Set();
  for (const p of value.selectedPaths) {
    if (!safePath(p)) fail('managed selection contains unsafe, protected, or out-of-scope path');
    const key = p.toLocaleLowerCase('en-US'); if (seen.has(key)) fail('managed selection contains duplicate or case-alias paths'); seen.add(key);
  }
  if (Buffer.byteLength(canonical(value), 'utf8') > MAX_REQUEST_BYTES) fail('managed selection request exceeds size limit');
  return structuredClone(value);
}
function validDigestSeal(value, key, message) {
  if (!record(value) || typeof value[key] !== 'string' || !SHA256.test(value[key]) || digest(without(value, key)) !== value[key]) fail(message);
}
function validateEntry(entry) {
  if (!record(entry) || !safePath(entry.path)) fail('review inventory contains unsafe path');
  for (const key of ['headOid', 'indexOid', 'workingOid']) if (entry[key] != null && (typeof entry[key] !== 'string' || !GIT_OID.test(entry[key]))) fail('review inventory contains invalid object identity');
  for (const key of ['workingSha256']) if (entry[key] != null && (typeof entry[key] !== 'string' || !SHA256.test(entry[key]))) fail('review inventory contains invalid content digest');
  for (const key of ['headMode', 'indexMode', 'mode']) if (entry[key] != null && !['100644', '100755'].includes(entry[key])) fail('review inventory contains unsupported mode');
  for (const layer of ['head', 'index']) {
    if ((entry[`${layer}Oid`] != null) !== (entry[`${layer}Mode`] != null)) fail('review inventory OID/mode pair is incomplete');
  }
  if (typeof entry.state !== 'string' || typeof entry.staged !== 'boolean' || typeof entry.rawDiverged !== 'boolean' || typeof entry.modeDiverged !== 'boolean') fail('review inventory entry shape is invalid');
  return entry;
}
function validateInventory(inv, label) {
  validDigestSeal(inv, 'inventory_digest', `${label} inventory digest is invalid`);
  if (inv.format !== 1 || inv.mode !== 'managed_allowed_source_inventory_v1' || !Array.isArray(inv.entries) || !Array.isArray(inv.excluded) || !Array.isArray(inv.blockers) || inv.execution !== false || inv.readyForMerge !== false || inv.readyForLaunch !== false) fail(`${label} inventory contract is invalid`);
  const seen = new Set();
  for (const entry of inv.entries) { validateEntry(entry); const key = entry.path.toLocaleLowerCase('en-US'); if (seen.has(key)) fail(`${label} inventory has duplicate paths`); seen.add(key); }
  return inv.entries;
}
function validateReview(review) {
  validDigestSeal(review, 'review_digest', 'managed source review digest is invalid or tampered');
  if (review.format !== 1 || review.mode !== REVIEW_MODE || !SHA256.test(review.inspectionDigest || '') || review.execution !== false || review.readyForMerge !== false || review.readyForLaunch !== false || !Array.isArray(review.blockers)) fail('managed source review contract is invalid');
  for (const side of ['stable', 'sandbox']) {
    const state = review[side];
    if (!record(state) || !record(state.identity) || typeof state.identity.worktreeRoot !== 'string' || typeof state.identity.gitDir !== 'string' || typeof state.identity.commonDir !== 'string' || !GIT_OID.test(state.head || '') || typeof state.branch !== 'string' || !state.branch || !SHA256.test(state.indexDigest || '')) fail(`${side} baseline identity is invalid`);
    validateInventory(state.inventory, side);
    if (state.inventory.head !== state.head || state.inventory.indexDigest !== state.indexDigest || state.inventory.root !== state.identity.worktreeRoot) fail(`${side} inventory does not bind to baseline`);
  }
  if (!record(review.history) || !GIT_OID.test(review.history.mergeBase || '') || !Array.isArray(review.history.commits) || !Array.isArray(review.history.paths) || !record(review.effectiveDelta) || review.effectiveDelta.basis !== 'merge_base_to_sandbox_head' || !Array.isArray(review.effectiveDelta.paths)) fail('review history or delta shape is invalid');
}
function changed(entry) { return entry.staged || entry.rawDiverged || entry.modeDiverged || entry.state !== 'clean'; }
function preservedBaseline(side) { return { identity: structuredClone(side.identity), head: side.head, branch: side.branch, indexDigest: side.indexDigest, inventoryDigest: side.inventory.inventory_digest }; }
export function createManagedSourceSelectionProposal({ review, request } = {}) {
  validateReview(review);
  const req = validateManagedSelectionRequest(request);
  if (req.reviewDigest !== review.review_digest) fail('request review digest does not match current review');
  const sandboxEntries = review.sandbox.inventory.entries;
  const stableEntries = review.stable.inventory.entries;
  const sandboxByPath = new Map(sandboxEntries.map(e => [e.path, e]));
  const stableByPath = new Map(stableEntries.map(e => [e.path, e]));
  for (const p of req.selectedPaths) if (!sandboxByPath.has(p)) fail(`selected path is not present in sandbox inventory: ${p}`);
  const selected = [...req.selectedPaths].sort().map(path => structuredClone(sandboxByPath.get(path)));
  const selectedSet = new Set(req.selectedPaths);
  const counterparts = req.selectedPaths.slice().sort().map(path => stableByPath.has(path) ? { path, status: 'in_inventory', entry: structuredClone(stableByPath.get(path)) } : { path, status: 'not_in_inventory' });
  const blockers = structuredClone(review.blockers);
  const pathsByFolded = new Map();
  for (const entry of [...stableEntries, ...sandboxEntries]) {
    const folded = entry.path.toLocaleLowerCase('en-US');
    const paths = pathsByFolded.get(folded) ?? new Set();
    paths.add(entry.path);
    pathsByFolded.set(folded, paths);
  }
  for (const paths of pathsByFolded.values()) {
    if (paths.size > 1) blockers.push({ code: 'cross_lane_case_alias_requires_review', paths: [...paths].sort() });
  }
  for (const entry of selected) {
    const path = entry.path;
    if (!entry.workingSha256 || !entry.workingOid || !entry.headOid && !entry.indexOid || !entry.mode) blockers.push({ code: 'selected_sandbox_source_metadata_incomplete', paths: [path] });
    const counterpart = stableByPath.get(path);
    if (!counterpart) blockers.push({ code: 'selected_stable_counterpart_absence_unproven', paths: [path] });
    else {
      if (changed(counterpart)) blockers.push({ code: 'selected_stable_source_requires_preservation_review', paths: [path] });
      if (!counterpart.workingSha256 || !counterpart.workingOid || !counterpart.mode || (!counterpart.headOid && !counterpart.indexOid)) blockers.push({ code: 'selected_stable_source_metadata_incomplete', paths: [path] });
    }
  }
  const proposal = {
    format: 1, mode: PROPOSAL_MODE, reviewDigest: review.review_digest,
    stableBaseline: preservedBaseline(review.stable), sandboxBaseline: preservedBaseline(review.sandbox),
    selectedPaths: [...req.selectedPaths].sort(), selectedSandboxEntries: selected, stableCounterparts: counterparts,
    preservedStableEntries: stableEntries.map(e => structuredClone(e)).sort((a,b) => a.path.localeCompare(b.path)),
    excludedSandboxPaths: sandboxEntries.filter(e => !selectedSet.has(e.path)).map(e => e.path).sort(),
    excludedSandboxEntries: sandboxEntries.filter(e => !selectedSet.has(e.path)).map(e => structuredClone(e)).sort((a,b) => a.path.localeCompare(b.path)),
    inheritedBlockers: structuredClone(review.blockers), blockers,
    scopeIsDeclarationNotVerifiedOwnership: true,
    execution: false, readyForMerge: false, readyForLaunch: false, checkpointCreated: false, nativeConsent: false,
  };
  proposal.proposal_digest = digest(proposal);
  return proposal;
}






