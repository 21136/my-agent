import { constants as C } from 'node:fs';
import { closeSync, existsSync, fstatSync, lstatSync, openSync, readFileSync, readSync, realpathSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { TextDecoder } from 'node:util';
import { validateManagedSelectionRequest } from './managed-source-selection.mjs';
import { validateManagedStablePreservation } from './managed-stable-preservation.mjs';
import { readSelectedMergeEvidence } from './managed-selected-merge-store.mjs';
import { inspectManagedSelectedMergeSourceIdentity } from './managed-selected-merge.mjs';

const HASH = /^[a-f0-9]{64}$/;
const OID = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
const MAX_FILE_BYTES = 4 * 1024 * 1024;
const MAX_TOTAL_BYTES = 64 * 1024 * 1024;
const MAX_FILES = 20_000;
const MAX_TREE_BYTES = 32 * 1024 * 1024;
const MAX_BATCH_BYTES = MAX_TOTAL_BYTES + 4 * 1024 * 1024;
const PLAN_KEYS = ['blockers','checkpointDigest','execution','expectedCommit','expectedMergedTree','files','format','gitMetadataDigest','mode','planDigest','preservationBasis','preservationDigest','readyForLaunch','sandbox','selectedPaths','sourceHistoryMerged','stable','stablePreservation','temporaryIndexUpperBoundBytes'];
const RECEIPT_KEYS = ['checkpointDigest','format','mergedTree','mode','planDigest','readyForLaunch','receiptDigest','selectedPaths','sourceHistoryMerged','sourceMergeExecuted','stableHeadAfter','stableHeadBefore','status'];
const ROOT_KEYS = ['branch','commonDir','gitDir','head','rawIndexSha256','worktreeRoot'];
const FILE_KEYS = ['action','beforeSha256','expectedBaseMode','expectedBaseOid','mode','path','rawSha256'];
const NEVER = new Set(['.git','.venv','venv','node_modules','data','artifacts','dist','dist-electron','__pycache__','profile','profiles','user-data','workspace','workspaces','config-store','secrets','private','credentials','llm_secrets','.managed-runtime']);
const SECRET = /^(?:\.env(?:\..*)?|llm_secrets\.(?:json|ya?ml)|llm_models\.(?:json|ya?ml)|.*\.(?:pem|key|p12|pfx|cer|crt))$/i;
const utf8 = new TextDecoder('utf-8', { fatal: true });

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}
function sha(bytes) { return createHash('sha256').update(bytes).digest('hex'); }
function exact(value, keys) { return value !== null && typeof value === 'object' && !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype && Object.keys(value).sort().join('\0') === [...keys].sort().join('\0'); }
function fail(message) { throw new TypeError(`managed_postmerge_source_invalid: ${message}`); }
function assertStrictJson(value, seen = new Set()) {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return;
  if (typeof value === 'number') { if (Number.isFinite(value)) return; fail('non-finite JSON number'); }
  if (typeof value !== 'object' || seen.has(value)) fail('non-JSON value or cycle');
  const proto = Object.getPrototypeOf(value);
  if (Array.isArray(value)) {
    const keys = Reflect.ownKeys(value);
    if (proto !== Array.prototype || keys.length !== value.length + 1) fail('non-canonical JSON array');
    seen.add(value);
    for (let index = 0; index < value.length; index++) { const descriptor = Object.getOwnPropertyDescriptor(value, String(index)); if (!descriptor?.enumerable || !Object.hasOwn(descriptor, 'value')) fail('array accessor or sparse field'); assertStrictJson(descriptor.value, seen); }
    seen.delete(value); return;
  }
  if (proto !== Object.prototype && proto !== null) fail('non-plain JSON object');
  seen.add(value);
  for (const key of Reflect.ownKeys(value)) { const descriptor = Object.getOwnPropertyDescriptor(value, key); if (typeof key !== 'string' || !descriptor?.enumerable || !Object.hasOwn(descriptor, 'value')) fail('object accessor or hidden field'); assertStrictJson(descriptor.value, seen); }
  seen.delete(value);
}
function oidFor(bytes, oidLength) {
  const algorithm = oidLength === 64 ? 'sha256' : oidLength === 40 ? 'sha1' : null;
  if (!algorithm) fail('unsupported Git object format');
  return createHash(algorithm).update(Buffer.from(`blob ${bytes.length}\0`)).update(bytes).digest('hex');
}
function gitObjectOid(type, bytes, oidLength) {
  const algorithm = oidLength === 64 ? 'sha256' : oidLength === 40 ? 'sha1' : null;
  if (!algorithm) fail('unsupported Git object format');
  return createHash(algorithm).update(Buffer.from(`${type} ${bytes.length}\0`)).update(bytes).digest('hex');
}
function validateIdentity(identity, label) {
  if (!exact(identity, ROOT_KEYS) || typeof identity.worktreeRoot !== 'string' || typeof identity.gitDir !== 'string' || typeof identity.commonDir !== 'string' || typeof identity.branch !== 'string' || !identity.branch || !OID.test(identity.head || '') || !(identity.rawIndexSha256 === null || HASH.test(identity.rawIndexSha256 || ''))) fail(`${label} identity schema invalid`);
  for (const value of [identity.worktreeRoot, identity.gitDir, identity.commonDir]) if (!path.isAbsolute(value) || path.resolve(value) !== value || /[\r\n\0]/.test(value)) fail(`${label} identity path is not canonical absolute`);
}
function validatePlan(plan) {
  assertStrictJson(plan);
  if (!exact(plan, PLAN_KEYS.filter(key => key !== 'stablePreservation').concat(Object.hasOwn(plan ?? {}, 'stablePreservation') ? ['stablePreservation'] : []))
      || plan.format !== 1 || plan.mode !== 'managed_selected_merge_plan_v1' || plan.execution !== false || plan.readyForLaunch !== false || plan.sourceHistoryMerged !== false
      || !HASH.test(plan.planDigest || '') || !HASH.test(plan.checkpointDigest || '') || !HASH.test(plan.preservationDigest || '') || !HASH.test(plan.gitMetadataDigest || '')
      || !OID.test(plan.expectedMergedTree || '') || !OID.test(plan.expectedCommit || '') || !Number.isSafeInteger(plan.temporaryIndexUpperBoundBytes) || plan.temporaryIndexUpperBoundBytes < 0) fail('plan schema invalid');
  validateIdentity(plan.stable, 'stable'); validateIdentity(plan.sandbox, 'sandbox');
  if (plan.expectedMergedTree.length !== plan.stable.head.length || plan.expectedCommit.length !== plan.stable.head.length || plan.sandbox.head.length !== plan.stable.head.length) fail('plan object formats differ');
  const body = { ...plan }; delete body.planDigest;
  if (sha(Buffer.from(canonical(body))) !== plan.planDigest) fail('plan digest invalid');
  if (!Array.isArray(plan.selectedPaths) || !Array.isArray(plan.files) || !plan.selectedPaths.length || plan.files.length !== plan.selectedPaths.length || plan.files.length > 2048) fail('plan selection invalid');
  try { validateManagedSelectionRequest({ format: 1, mode: 'managed_source_selection_request_v1', reviewDigest: plan.checkpointDigest, selectedPaths: plan.selectedPaths }); } catch { fail('plan selected paths invalid'); }
  const seen = new Set();
  for (let index = 0; index < plan.files.length; index++) {
    const file = plan.files[index];
    if (!exact(file, FILE_KEYS) || file.path !== plan.selectedPaths[index] || seen.has(file.path) || !HASH.test(file.rawSha256 || '') || !['100644','100755'].includes(file.mode)
        || !['add','replace'].includes(file.action) || !(file.expectedBaseOid === null || OID.test(file.expectedBaseOid || ''))
        || !(file.beforeSha256 === null || HASH.test(file.beforeSha256 || '')) || !(file.expectedBaseMode === null || ['100644','100755'].includes(file.expectedBaseMode))) fail('plan selected file schema invalid');
    seen.add(file.path);
    if (file.action === 'add' && (file.expectedBaseOid !== null || file.beforeSha256 !== null || file.expectedBaseMode !== null)) fail('plan add base fields invalid');
    if (file.action === 'replace' && (!file.expectedBaseOid || !file.beforeSha256 || !file.expectedBaseMode)) fail('plan replace base fields invalid');
  }
  if (!Array.isArray(plan.blockers) || plan.blockers.some(item => !exact(item, ['code','disposition','paths']) || typeof item.code !== 'string' || !['preserve','exclude','review_required'].includes(item.disposition) || !Array.isArray(item.paths) || item.paths.some(value => typeof value !== 'string'))) fail('plan blockers schema invalid');
  if (!exact(plan.preservationBasis, ['sandbox','stable']) || ['stable','sandbox'].some(side => {
    const basis = plan.preservationBasis[side];
    return !exact(basis, ['index','sources','stash','tree']) || !Array.isArray(basis.index) || !Array.isArray(basis.sources) || !Array.isArray(basis.tree) || !exact(basis.stash, ['reflogOids','refOid']) || !(basis.stash.refOid === null || OID.test(basis.stash.refOid || '')) || !Array.isArray(basis.stash.reflogOids) || basis.stash.reflogOids.some(row => !Array.isArray(row) || row.some(oid => !OID.test(oid)));
  })) fail('plan preservation basis schema invalid');
  try { validateManagedStablePreservation(plan); } catch { fail('stable preservation invalid'); }
}
function validateReceipt(receipt, plan) {
  assertStrictJson(receipt);
  if (!exact(receipt, RECEIPT_KEYS) || receipt.format !== 1 || receipt.mode !== 'managed_selected_merge_receipt_v1' || receipt.status !== 'source_merge_verified'
      || receipt.sourceMergeExecuted !== true || receipt.sourceHistoryMerged !== false || receipt.readyForLaunch !== false
      || receipt.planDigest !== plan.planDigest || receipt.checkpointDigest !== plan.checkpointDigest || canonical(receipt.selectedPaths) !== canonical(plan.selectedPaths)
      || receipt.stableHeadBefore !== plan.stable.head || receipt.stableHeadAfter !== plan.expectedCommit || receipt.mergedTree !== plan.expectedMergedTree
      || !HASH.test(receipt.receiptDigest || '')) fail('success receipt schema or binding invalid');
  const body = { ...receipt }; delete body.receiptDigest;
  if (sha(Buffer.from(canonical(body))) !== receipt.receiptDigest) fail('receipt digest invalid');
}
function gitEnv() {
  const env = {};
  for (const key of ['PATH','SystemRoot','WINDIR','TEMP','TMP']) if (process.env[key]) env[key] = process.env[key];
  env.GIT_CONFIG_NOSYSTEM = '1'; env.GIT_CONFIG_GLOBAL = process.platform === 'win32' ? 'NUL' : '/dev/null';
  env.GIT_ATTR_NOSYSTEM = '1'; env.GIT_OPTIONAL_LOCKS = '0'; env.GIT_TERMINAL_PROMPT = '0'; env.GIT_PROTOCOL_FROM_USER = '0';
  env.GIT_NO_REPLACE_OBJECTS = '1';
  return env;
}
function git(gitExecutable, root, args, { input, maxBuffer = 4 * 1024 * 1024 } = {}) {
  const nullDevice = process.platform === 'win32' ? 'NUL' : '/dev/null';
  const result = spawnSync(gitExecutable, ['-c', `safe.directory=${root}`, '-c', `core.hooksPath=${nullDevice}`, '-c', `core.attributesFile=${nullDevice}`, '-c', 'core.autocrlf=false', '-c', 'core.fsmonitor=false', '-c', 'protocol.allow=never', '--no-optional-locks', ...args], {
    cwd: root, env: gitEnv(), input, encoding: 'buffer', windowsHide: true, shell: false, timeout: 30_000, maxBuffer,
  });
  if (result.error || result.status !== 0) fail(`read-only Git command failed (${args[0]})`);
  return result.stdout;
}
function gitText(gitExecutable, root, args) { return git(gitExecutable, root, args).toString('utf8').trim(); }
function samePath(a, b) {
  const left = path.resolve(a), right = path.resolve(b);
  return process.platform === 'win32' ? left.toLocaleLowerCase('en-US') === right.toLocaleLowerCase('en-US') : left === right;
}
function readCommit(gitExecutable, root, oid) {
  const raw = git(gitExecutable, root, ['cat-file', '-p', oid], { maxBuffer: 1024 * 1024 });
  if (gitObjectOid('commit', raw, oid.length) !== oid) fail('raw commit bytes do not match object ID');
  const sep = raw.indexOf(Buffer.from('\n\n'));
  if (sep < 0) fail('commit object header malformed');
  const headers = raw.subarray(0, sep).toString('ascii').split('\n');
  const trees = headers.filter(line => line.startsWith('tree '));
  const parents = headers.filter(line => line.startsWith('parent ')).map(line => line.slice(7));
  if (trees.length !== 1 || !OID.test(trees[0].slice(5)) || parents.some(parent => !OID.test(parent))) fail('commit object identity malformed');
  return { tree: trees[0].slice(5), parents };
}
function parseTree(raw, oidLength) {
  if (raw.length > MAX_TREE_BYTES) fail('committed tree listing exceeds bound');
  const entries = [], aliases = new Map(), seenNames = new Set();
  let offset = 0;
  while (offset < raw.length) {
    const end = raw.indexOf(0, offset); if (end < 0) fail('tree listing framing invalid');
    const row = raw.subarray(offset, end), tab = row.indexOf(9); if (tab < 0) fail('tree entry framing invalid');
    const header = row.subarray(0, tab).toString('ascii').split(' ');
    if (header.length !== 3 || !['100644','100755'].includes(header[0]) || header[1] !== 'blob' || !new RegExp(`^[a-f0-9]{${oidLength}}$`).test(header[2])) fail('tree contains a link, submodule, or unsupported entry');
    let name; try { name = utf8.decode(row.subarray(tab + 1)); } catch { fail('tree path is not valid UTF-8'); }
    if (seenNames.has(name)) fail('committed tree contains duplicate path entries');
    seenNames.add(name);
    const parts = name.split('/');
    if (!name || Buffer.byteLength(name, 'utf8') > 512 || name.startsWith('/') || name.includes('\\') || name.includes(':')
        || parts.some(part => !part || part === '.' || part === '..' || /[. ]$/.test(part) || /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i.test(part) || !/^[A-Za-z0-9_@+.,= -]+$/.test(part))) fail('tree path is unsafe or aliases a platform path');
    let prefix = '';
    for (const part of parts) {
      prefix = prefix ? `${prefix}/${part}` : part;
      const folded = prefix.toLocaleLowerCase('en-US'), prior = aliases.get(folded);
      if (prior !== undefined && prior !== prefix) fail('committed tree contains case-folded path aliases');
      aliases.set(folded, prefix);
    }
    if (name.toLowerCase() === '.gitattributes' || name.toLowerCase().endsWith('/.gitattributes')) fail('committed attributes path is forbidden');
    entries.push({ path: name, mode: header[0], oid: header[2] });
    if (entries.length > MAX_FILES) fail('committed tree file-count limit exceeded');
    offset = end + 1;
  }
  return entries;
}
function protectedPath(name) { return name.split('/').some(segment => NEVER.has(segment.toLowerCase()) || SECRET.test(segment)); }
function sourceAllowed(name, plan) {
  // Runtime index is read from the committed merged tree only. This exact-file
  // exception does not broaden the renderer's selectable source/merge scope.
  if (name === 'evolve/_index.core.toml') return true;
  try { validateManagedSelectionRequest({ format: 1, mode: 'managed_source_selection_request_v1', reviewDigest: plan.checkpointDigest, selectedPaths: [name] }); return true; }
  catch { return false; }
}
function catFileBatch(gitExecutable, root, entries, oidLength) {
  if (!entries.length) return [];
  const input = Buffer.from(entries.map(entry => entry.oid).join('\n') + '\n');
  const output = git(gitExecutable, root, ['cat-file', '--batch'], { input, maxBuffer: MAX_BATCH_BYTES });
  const result = []; let offset = 0, total = 0;
  for (const entry of entries) {
    const lineEnd = output.indexOf(10, offset); if (lineEnd < 0) fail('cat-file batch header truncated');
    const header = output.subarray(offset, lineEnd).toString('ascii').split(' ');
    if (header.length !== 3 || header[0] !== entry.oid || header[1] !== 'blob' || !/^\d+$/.test(header[2])) fail('cat-file returned a non-blob or mismatched object');
    const size = Number(header[2]);
    if (!Number.isSafeInteger(size) || size > MAX_FILE_BYTES || lineEnd + 1 + size >= output.length || output[lineEnd + 1 + size] !== 10) fail('committed source file size or framing invalid');
    total += size; if (total > MAX_TOTAL_BYTES) fail('committed source aggregate byte limit exceeded');
    const bytes = output.subarray(lineEnd + 1, lineEnd + 1 + size);
    if (oidFor(bytes, oidLength) !== entry.oid) fail('raw blob bytes do not match object ID');
    result.push(bytes);
    offset = lineEnd + 1 + size + 1;
  }
  if (offset !== output.length) fail('cat-file batch returned trailing data');
  return result;
}
function treeEntry(gitExecutable, root, tree, filePath, oidLength) {
  const raw = git(gitExecutable, root, ['ls-tree', '-r', '-z', '--full-tree', tree, '--', filePath], { maxBuffer: 1024 * 1024 });
  const rows = parseTree(raw, oidLength);
  if (rows.length !== 1 || rows[0].path !== filePath) fail('preservation path missing or ambiguous in parent tree');
  return rows[0];
}
function preserveBytes(gitExecutable, root, plan) {
  const preservation = plan.stablePreservation;
  if (!preservation) return null;
  const commit = readCommit(gitExecutable, root, preservation.expectedCommit);
  if (commit.tree !== preservation.expectedTree || commit.parents.length !== 1 || commit.parents[0] !== plan.stable.head) fail('preservation parent commit ancestry mismatch');
  const parentEntries = preservation.files.map(file => {
    const entry = treeEntry(gitExecutable, root, preservation.expectedTree, file.path, plan.stable.head.length);
    if (entry.mode !== file.mode || entry.oid !== file.oid) fail('preserved original blob is absent from preservation parent');
    return entry;
  });
  const bytesList = catFileBatch(gitExecutable, root, parentEntries, plan.stable.head.length);
  for (let index = 0; index < bytesList.length; index++) {
    const file = preservation.files[index], bytes = bytesList[index];
    if (sha(bytes) !== file.sha256 || oidFor(bytes, plan.stable.head.length) !== file.oid || bytes.toString('base64') !== file.bytesBase64) fail('preservation parent bytes do not match reviewed original');
  }
  return preservation.expectedCommit;
}
function selectedWorkingBytes(stableRoot, file) {
  let current = stableRoot, initial;
  const parts = file.path.split('/');
  for (let index = 0; index < parts.length; index++) {
    current = path.join(current, parts[index]);
    let st; try { st = lstatSync(current); } catch { fail('selected working source is missing'); }
    if (st.isSymbolicLink() || (process.platform === 'win32' && (st.attributes & 0x400)) || (index < parts.length - 1 ? !st.isDirectory() : !st.isFile())) fail('selected working source contains a link or wrong type');
    if (index === parts.length - 1) { if (st.nlink !== 1 || st.size > MAX_FILE_BYTES) fail('selected working source is linked or oversized'); initial = st; }
  }
  const fd = openSync(current, C.O_RDONLY | (C.O_NOFOLLOW || 0));
  try {
    const before = fstatSync(fd), byPath = lstatSync(current);
    const identity = value => [value.dev,value.ino,value.size,value.nlink,value.mtimeMs,value.ctimeMs,value.mode,value.attributes ?? 0].join(':');
    if (!before.isFile() || before.nlink !== 1 || before.size > MAX_FILE_BYTES || identity(before) !== identity(initial) || identity(before) !== identity(byPath)) fail('selected working source changed before read');
    const bytes = Buffer.alloc(before.size); let offset = 0;
    while (offset < bytes.length) { const count = readSync(fd, bytes, offset, Math.min(64 * 1024, bytes.length - offset), offset); if (count <= 0) fail('selected working source short read'); offset += count; }
    const after = fstatSync(fd), final = lstatSync(current);
    if (identity(before) !== identity(after) || identity(after) !== identity(final) || bytes.length !== after.size) fail('selected working source changed during read');
    return { bytes, mode: (final.mode & 0o111) ? '100755' : '100644' };
  } finally { closeSync(fd); }
}
function selectedIndex(gitExecutable, root, selectedPaths, oidLength) {
  const raw = git(gitExecutable, root, ['ls-files', '--stage', '-z', '--', ...selectedPaths], { maxBuffer: 2 * 1024 * 1024 });
  const map = new Map();
  for (const row of raw.toString('utf8').split('\0').filter(Boolean)) {
    const tab = row.indexOf('\t'); if (tab < 0) fail('selected index entry framing invalid');
    const [mode, oid, stage] = row.slice(0, tab).split(' '), name = row.slice(tab + 1);
    if (!['100644','100755'].includes(mode) || !new RegExp(`^[a-f0-9]{${oidLength}}$`).test(oid || '') || stage !== '0' || map.has(name)) fail('selected index entry unsupported or conflicted');
    map.set(name, { mode, oid });
  }
  return map;
}
function checkDirectoryChain(target) {
  if (!path.isAbsolute(target) || path.resolve(target) !== target) fail('bound Git metadata path is not normalized absolute');
  const root = path.parse(target).root; let current = root;
  for (const part of target.slice(root.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    const stat = lstatSync(current);
    if (!stat.isDirectory() || stat.isSymbolicLink() || (process.platform === 'win32' && (stat.attributes & 0x400))) fail('bound Git metadata path contains a link or non-directory');
  }
  if (realpathSync.native(target) !== target) fail('bound Git metadata path has an aliased spelling');
}
function assertPlanBoundMetadata(root, identity, label) {
  if (typeof root !== 'string' || !path.isAbsolute(root) || path.resolve(root) !== root || !samePath(root, identity.worktreeRoot)) fail(`${label} root is not the plan-bound canonical path`);
  checkDirectoryChain(root);
  const marker = path.join(root, '.git'), markerStat = lstatSync(marker);
  if (markerStat.isSymbolicLink() || (process.platform === 'win32' && (markerStat.attributes & 0x400))) fail(`${label} Git marker is linked`);
  let gitDir;
  if (markerStat.isDirectory()) gitDir = marker;
  else if (markerStat.isFile() && markerStat.nlink === 1 && markerStat.size <= 4096) {
    const bytes = readFileSync(marker), text = bytes.toString('utf8'), match = /^gitdir: ([^\r\n]+)\r?\n?$/.exec(text);
    if (!Buffer.from(text).equals(bytes) || !match) fail(`${label} Git marker is malformed`);
    gitDir = path.resolve(root, match[1]);
  } else fail(`${label} Git marker type or identity is invalid`);
  checkDirectoryChain(gitDir);
  let commonDir = gitDir;
  const commondirFile = path.join(gitDir, 'commondir');
  if (existsSync(commondirFile)) {
    const stat = lstatSync(commondirFile);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1 || stat.size <= 0 || stat.size > 4096 || (process.platform === 'win32' && (stat.attributes & 0x400))) fail(`${label} commondir metadata is unsafe`);
    const bytes = readFileSync(commondirFile), text = bytes.toString('utf8');
    if (!Buffer.from(text).equals(bytes) || /[\r\n\0]/.test(text.trim())) fail(`${label} commondir metadata is malformed`);
    commonDir = path.resolve(gitDir, text.trim());
  }
  checkDirectoryChain(commonDir);
  if (!samePath(gitDir, identity.gitDir) || !samePath(commonDir, identity.commonDir)) fail(`${label} Git dir binding differs from plan before Git access`);
  const index = path.join(gitDir, 'index'), indexStat = lstatSync(index);
  if (!indexStat.isFile() || indexStat.isSymbolicLink() || indexStat.nlink !== 1 || indexStat.size > 1024 * 1024 || (process.platform === 'win32' && (indexStat.attributes & 0x400))) fail(`${label} Git index is unsafe before Git access`);
  const indexBytes = readFileSync(index);
  if (indexBytes.length !== indexStat.size || (label === 'sandbox' && sha(indexBytes) !== identity.rawIndexSha256)) fail(`${label} Git index differs from plan before Git access`);
}
function deepFreeze(value) { if (value && typeof value === 'object' && !Object.isFrozen(value)) { for (const child of Object.values(value)) deepFreeze(child); Object.freeze(value); } return value; }

/** Capture source provenance from the verified committed merge tree. No build or launch authority is produced. */
export function captureManagedPostmergeSource({ stableRoot, sandboxRoot, plan, receipt, gitExecutable } = {}) {
  validatePlan(plan); validateReceipt(receipt, plan);
  if (typeof gitExecutable !== 'string' || !gitExecutable || gitExecutable.includes('\0') || !path.isAbsolute(gitExecutable)) fail('absolute native Git executable required');
  const planCanonicalAtStart = canonical(plan), receiptCanonicalAtStart = canonical(receipt);
  const durable = readSelectedMergeEvidence({ sandboxRoot, planDigest: plan.planDigest });
  if (canonical(durable.plan) !== planCanonicalAtStart || canonical(durable.receipt) !== receiptCanonicalAtStart) fail('passed plan or receipt differs from durable v2 evidence');
  assertPlanBoundMetadata(stableRoot, plan.stable, 'stable');
  assertPlanBoundMetadata(sandboxRoot, plan.sandbox, 'sandbox');
  // This shared engine guard validates the native executable, roots, .git markers,
  // local config/attributes, object store, indexes, and initial worktree identities.
  const identityAtStart = inspectManagedSelectedMergeSourceIdentity({ stableRoot, sandboxRoot, gitExecutable });
  if (!samePath(identityAtStart.stable.worktreeRoot, plan.stable.worktreeRoot) || !samePath(identityAtStart.sandbox.worktreeRoot, plan.sandbox.worktreeRoot)
      || !samePath(identityAtStart.stable.gitDir, plan.stable.gitDir) || !samePath(identityAtStart.sandbox.gitDir, plan.sandbox.gitDir)
      || !samePath(identityAtStart.stable.commonDir, plan.stable.commonDir) || !samePath(identityAtStart.sandbox.commonDir, plan.sandbox.commonDir)
      || identityAtStart.stable.branch !== plan.stable.branch || identityAtStart.sandbox.branch !== plan.sandbox.branch
      || identityAtStart.gitMetadataDigest !== plan.gitMetadataDigest || !samePath(identityAtStart.stable.commonDir, identityAtStart.sandbox.commonDir)
      || samePath(identityAtStart.stable.gitDir, identityAtStart.sandbox.gitDir)) fail('worktree or Git metadata identity differs from durable plan');
  const stable = identityAtStart.stable.worktreeRoot, sandbox = identityAtStart.sandbox.worktreeRoot;
  if (identityAtStart.stable.head !== receipt.stableHeadAfter || identityAtStart.stable.head !== plan.expectedCommit) fail('actual stable HEAD differs from verified merge receipt');
  if (identityAtStart.sandbox.head !== plan.sandbox.head || identityAtStart.sandbox.rawIndexSha256 !== plan.sandbox.rawIndexSha256) fail('sandbox HEAD or index changed after selected merge');
  const stableTreeAtStart = gitText(gitExecutable, stable, ['rev-parse', '--verify', 'HEAD^{tree}']);
  const sandboxTreeAtStart = gitText(gitExecutable, sandbox, ['rev-parse', '--verify', 'HEAD^{tree}']);
  const mergedTree = stableTreeAtStart;
  if (mergedTree !== receipt.mergedTree || mergedTree !== plan.expectedMergedTree) fail('actual stable tree differs from verified merge receipt');
  const child = readCommit(gitExecutable, stable, identityAtStart.stable.head);
  const expectedParent = preserveBytes(gitExecutable, stable, plan) ?? plan.stable.head;
  if (child.tree !== mergedTree || child.parents.length !== 1 || child.parents[0] !== expectedParent) fail('merged commit parent ancestry mismatch');

  const allEntries = parseTree(git(gitExecutable, stable, ['ls-tree', '-r', '-z', '--full-tree', mergedTree], { maxBuffer: MAX_TREE_BYTES }), identityAtStart.stable.head.length);
  const sourceEntries = [], excludedProtectedPaths = [], folded = new Set();
  for (const entry of allEntries) {
    if (protectedPath(entry.path)) { excludedProtectedPaths.push(entry.path); continue; }
    if (!sourceAllowed(entry.path, plan)) continue;
    if (entry.path.toLowerCase() === '.gitattributes' || entry.path.toLowerCase().endsWith('/.gitattributes')) fail('attributes file entered allowed source snapshot');
    const key = entry.path.toLocaleLowerCase('en-US'); if (folded.has(key)) fail('allowed source tree contains case aliases'); folded.add(key);
    sourceEntries.push(entry);
  }
  if (sourceEntries.length > MAX_FILES) fail('allowed source file-count limit exceeded');
  const sourceBytes = catFileBatch(gitExecutable, stable, sourceEntries, identityAtStart.stable.head.length);
  let total = 0;
  const files = sourceEntries.map((entry, index) => {
    const bytes = sourceBytes[index]; total += bytes.length; if (total > MAX_TOTAL_BYTES) fail('allowed source aggregate byte limit exceeded');
    return { path: entry.path, mode: entry.mode, blobOid: entry.oid, sha256: sha(bytes), byteLength: bytes.length };
  }).sort((left, right) => left.path.localeCompare(right.path));

  const indexed = selectedIndex(gitExecutable, stable, plan.selectedPaths, identityAtStart.stable.head.length);
  const fileByPath = new Map(files.map(file => [file.path, file]));
  const checkSelectedWorking = () => {
    for (const planned of plan.files) {
      const committed = fileByPath.get(planned.path), working = selectedWorkingBytes(stable, planned), index = indexed.get(planned.path);
      const expectedOid = oidFor(working.bytes, identityAtStart.stable.head.length);
      if (!committed || !index || sha(working.bytes) !== planned.rawSha256 || committed.sha256 !== planned.rawSha256 || committed.mode !== planned.mode
          || working.mode !== planned.mode || committed.blobOid !== expectedOid || index.oid !== expectedOid || index.mode !== planned.mode) fail('selected committed, working, or index content differs from plan');
    }
  };
  checkSelectedWorking();
  const snapshot = {
    format: 1, mode: 'managed_postmerge_source_snapshot_v1', status: 'source_merge_verified',
    planDigest: plan.planDigest, receiptDigest: receipt.receiptDigest,
    roots: {
      stable: { worktreeRoot: identityAtStart.stable.worktreeRoot, gitDir: identityAtStart.stable.gitDir, commonDir: identityAtStart.stable.commonDir, branch: identityAtStart.stable.branch },
      sandbox: { worktreeRoot: identityAtStart.sandbox.worktreeRoot, gitDir: identityAtStart.sandbox.gitDir, commonDir: identityAtStart.sandbox.commonDir, branch: identityAtStart.sandbox.branch },
    },
    mergedHead: identityAtStart.stable.head, mergedTree, files, excludedProtectedPaths: [...new Set(excludedProtectedPaths)].sort(),
    readyForLaunch: false, buildVerified: false,
  };
  checkSelectedWorking();
  if (gitText(gitExecutable, stable, ['rev-parse', '--verify', 'HEAD^{tree}']) !== stableTreeAtStart
      || gitText(gitExecutable, sandbox, ['rev-parse', '--verify', 'HEAD^{tree}']) !== sandboxTreeAtStart) fail('stable or sandbox tree changed during postmerge capture');
  const durableAtEnd = readSelectedMergeEvidence({ sandboxRoot, planDigest: plan.planDigest });
  if (canonical(durableAtEnd.plan) !== planCanonicalAtStart || canonical(durableAtEnd.receipt) !== receiptCanonicalAtStart) fail('durable plan or receipt changed during postmerge capture');
  const identityAtEnd = inspectManagedSelectedMergeSourceIdentity({ stableRoot, sandboxRoot, gitExecutable });
  if (canonical(identityAtEnd) !== canonical(identityAtStart)) fail('roots, branches, HEAD, indexes, or Git metadata changed during postmerge capture');
  snapshot.snapshotDigest = sha(Buffer.from(canonical(snapshot)));
  return deepFreeze(snapshot);
}

/** Host-private raw committed build inputs; never a renderer response or launch credential. */
export function readManagedPostmergeSourcePayload(input) {
  const snapshot=captureManagedPostmergeSource(input);
  const entries=snapshot.files.map(file=>({path:file.path,mode:file.mode,oid:file.blobOid}));
  const payload=catFileBatch(input.gitExecutable,input.stableRoot,entries,snapshot.mergedHead.length);
  const files=snapshot.files.map((file,index)=>{
    const bytes=payload[index];
    if(bytes.length!==file.byteLength||sha(bytes)!==file.sha256)fail('committed payload differs from source snapshot');
    return {path:file.path,mode:file.mode,bytes:Buffer.from(bytes)};
  });
  const after=captureManagedPostmergeSource(input);
  if(canonical(after)!==canonical(snapshot))fail('source changed during payload collection');
  return {snapshot,files};
}
