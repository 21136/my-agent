import { createHash } from 'node:crypto';
import {
  constants as fsConstants, closeSync, existsSync, fstatSync, fsyncSync, lstatSync,
  mkdirSync, openSync, readSync, readdirSync, realpathSync, writeSync,
} from 'node:fs';
import path from 'node:path';
import { createManagedSourceSelectionProposal, validateManagedSelectionRequest } from './managed-source-selection.mjs';

const FORMAT = 1;
const MODE = 'managed_selected_source_checkpoint_v1';
const REQUEST_MODE = 'managed_source_checkpoint_request_v1';
const RECEIPT_MODE = 'managed_source_checkpoint_receipt_v1';
const SHA256 = /^[a-f0-9]{64}$/;
const OID = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
const MAX_FILES = 128;
const MAX_FILE_BYTES = 4 * 1024 * 1024;
const MAX_RAW_BYTES = 8 * 1024 * 1024;
const MAX_SERIALIZED_PACK_BYTES = 16 * 1024 * 1024;
const MAX_STORED_FILES = 8;
const MAX_STORED_BYTES = 32 * 1024 * 1024;
const FLAG_KEYS = ['execution', 'readyForMerge', 'readyForLaunch', 'nativeConsent'];
const FILE_KEYS = ['path', 'headOid', 'headMode', 'indexOid', 'indexMode', 'workingSha256', 'workingOid', 'staged', 'rawDiverged', 'modeDiverged', 'state', 'mode', 'size', 'bytesBase64'];
const TOP_KEYS = ['format', 'mode', 'review', 'request', 'files', ...FLAG_KEYS, 'rawBytes', 'checkpointDigest'];
const OUTPUT_DIR = ['artifacts', 'managed-source-checkpoints'];
const canon = value => {
  if (Array.isArray(value)) return `[${value.map(canon).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canon(value[k])}`).join(',')}}`;
  return JSON.stringify(value);
};
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const fail = message => { throw new TypeError(message); };
const isRecord = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const exactKeys = (value, keys, label) => {
  if (!isRecord(value) || Object.keys(value).length !== keys.length || keys.some(key => !Object.hasOwn(value, key))) fail(`${label} keys are invalid`);
};
function serializedPackBytes(pack) {
  const bytes = Buffer.from(`${canon(pack)}\n`, 'utf8');
  if (bytes.length > MAX_SERIALIZED_PACK_BYTES) fail('checkpoint serialized pack exceeds 16 MiB');
  return bytes;
}
function assertStrictJson(value, seen = new Set()) {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return;
  if (typeof value === 'number') { if (!Number.isFinite(value)) fail('checkpoint contains a non-finite number'); return; }
  if (typeof value !== 'object') fail('checkpoint contains a non-JSON value');
  if (seen.has(value)) fail('checkpoint contains a cycle');
  seen.add(value);
  const proto = Object.getPrototypeOf(value);
  if (Array.isArray(value)) {
    if (proto !== Array.prototype) fail('checkpoint array prototype is invalid');
    const keys = Reflect.ownKeys(value);
    if (keys.some(key => typeof key !== 'string' || (key !== 'length' && !/^(?:0|[1-9]\d*)$/.test(key)))) fail('checkpoint array has extra properties');
    if (keys.length !== value.length + 1) fail('checkpoint array is sparse');
    for (let i = 0; i < value.length; i++) {
      const descriptor = Object.getOwnPropertyDescriptor(value, String(i));
      if (!descriptor || !descriptor.enumerable || !Object.hasOwn(descriptor, 'value')) fail('checkpoint array accessor or hidden field rejected');
      assertStrictJson(descriptor.value, seen);
    }
  } else {
    if (proto !== Object.prototype && proto !== null) fail('checkpoint object prototype is invalid');
    for (const key of Reflect.ownKeys(value)) {
      if (typeof key !== 'string') fail('checkpoint symbol key rejected');
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (!descriptor?.enumerable || !Object.hasOwn(descriptor, 'value')) fail('checkpoint accessor or hidden field rejected');
      assertStrictJson(descriptor.value, seen);
    }
  }
  seen.delete(value);
}
function digestSeal(value, key) {
  const content = Object.fromEntries(Object.entries(value).filter(([name]) => name !== key));
  if (typeof value[key] !== 'string' || !SHA256.test(value[key]) || sha(Buffer.from(canon(content), 'utf8')) !== value[key]) fail(`${key} is invalid`);
}
function normalizedRoot(input, expected, label) {
  if (typeof input !== 'string' || !path.isAbsolute(input)) fail(`${label} root must be absolute`);
  const resolved = path.resolve(input);
  if (resolved !== input || (expected !== undefined && expected !== resolved)) fail(`${label} root does not exactly match review`);
  let current = path.parse(resolved).root;
  for (const component of resolved.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, component);
    const stat = lstatSync(current);
    if (stat.isSymbolicLink() || (stat.attributes & 0x400)) fail(`${label} root contains link/reparse point`);
  }
  if (!lstatSync(resolved).isDirectory() || realpathSync.native(resolved) !== resolved) fail(`${label} root is not a canonical directory`);
  return resolved;
}
function identity(stat) {
  return [stat.dev, stat.ino, stat.mode, stat.size, stat.mtimeMs, stat.ctimeMs, stat.nlink, stat.isDirectory(), stat.isFile()].join(':');
}
function checkRoot(root) {
  let current = path.parse(root).root;
  const result = [];
  for (const component of root.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, component);
    const stat = lstatSync(current);
    if (stat.isSymbolicLink() || (stat.attributes & 0x400) || !stat.isDirectory()) fail('checkpoint root ancestor is unsafe');
    result.push([current, identity(stat)]);
  }
  return result;
}
function sameAncestors(before) {
  for (const [name, expected] of before) {
    const stat = lstatSync(name);
    if (stat.isSymbolicLink() || (stat.attributes & 0x400) || identity(stat) !== expected) fail('checkpoint path ancestor changed');
  }
}
function locateOrdinaryFile(root, relative) {
  const ancestors = checkRoot(root);
  const components = relative.split('/');
  let current = root;
  for (let i = 0; i < components.length; i++) {
    const component = components[i];
    const names = readdirSync(current);
    const folded = names.filter(name => name.toLocaleLowerCase('en-US') === component.toLocaleLowerCase('en-US'));
    if (folded.length !== 1 || folded[0] !== component) fail('selected file path is missing or case-aliased');
    current = path.join(current, component);
    const stat = lstatSync(current);
    if (stat.isSymbolicLink() || (stat.attributes & 0x400)) fail('selected file path contains link/reparse point');
    if (i < components.length - 1) {
      if (!stat.isDirectory()) fail('selected file parent is not a directory');
      ancestors.push([current, identity(stat)]);
    }
    if (i === components.length - 1 && (!stat.isFile() || stat.nlink !== 1)) fail('selected file is not an ordinary single-link file');
  }
  sameAncestors(ancestors);
  return { file: current, pathStat: lstatSync(current), ancestors };
}
function boundedRead(root, relative, expectedMode) {
  const { file, pathStat, ancestors } = locateOrdinaryFile(root, relative);
  const actualMode = (pathStat.mode & 0o111) ? '100755' : '100644';
  if (expectedMode !== undefined && actualMode !== expectedMode) fail('selected file executable mode differs from review');
  if (pathStat.size > MAX_FILE_BYTES) fail('selected file exceeds per-file limit');
  const fd = openSync(file, fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW || 0));
  try {
    const before = fstatSync(fd);
    if (!before.isFile() || before.nlink !== 1 || before.size > MAX_FILE_BYTES || identity(before) !== identity(pathStat)) fail('selected file changed before read');
    const output = Buffer.alloc(before.size);
    let offset = 0;
    while (offset < output.length) {
      const count = readSync(fd, output, offset, Math.min(64 * 1024, output.length - offset), offset);
      if (count <= 0) fail('selected file read was incomplete');
      offset += count;
    }
    const after = fstatSync(fd);
    const pathAfter = lstatSync(file);
    sameAncestors(ancestors);
    if (identity(before) !== identity(after) || identity(after) !== identity(pathAfter) || pathAfter.isSymbolicLink() || (pathAfter.attributes & 0x400)) fail('selected file changed during read');
    return output;
  } finally { closeSync(fd); }
}
function blobOid(raw, length) {
  const algorithm = length === 64 ? 'sha256' : length === 40 ? 'sha1' : null;
  if (!algorithm) fail('baseline Git object format is unsupported');
  return createHash(algorithm).update(Buffer.from(`blob ${raw.length}\0`, 'utf8')).update(raw).digest('hex');
}
function selectionRequest(request) {
  return validateManagedSelectionRequest({ format: 1, mode: 'managed_source_selection_request_v1', reviewDigest: request.reviewDigest, selectedPaths: request.selectedPaths });
}
export function validateManagedCheckpointRequest(value) {
  assertStrictJson(value);
  exactKeys(value, ['format', 'mode', 'reviewDigest', 'proposalDigest', 'selectedPaths'], 'checkpoint request');
  if (value.format !== FORMAT || value.mode !== REQUEST_MODE || !SHA256.test(value.reviewDigest || '') || !SHA256.test(value.proposalDigest || '')) fail('checkpoint request identity is invalid');
  if (!Array.isArray(value.selectedPaths) || value.selectedPaths.length < 1 || value.selectedPaths.length > MAX_FILES) fail('checkpoint selection count is invalid');
  const base = selectionRequest(value);
  return { format: FORMAT, mode: REQUEST_MODE, reviewDigest: value.reviewDigest, proposalDigest: value.proposalDigest, selectedPaths: [...base.selectedPaths] };
}
function validateSelectedEntry(entry, baselineOid, proposal) {
  if (!isRecord(entry) || (entry.mode !== '100644' && entry.mode !== '100755')) fail('selected source mode metadata is missing or invalid');
  if (!Number.isSafeInteger(entry.size) || entry.size < 0) fail('selected source size metadata is missing or invalid');
  if (!SHA256.test(entry.workingSha256 || '') || !OID.test(entry.workingOid || '') || entry.workingOid.length !== baselineOid.length) fail('selected source working hash/blob metadata is missing or invalid');
  if (entry.size > MAX_FILE_BYTES) fail('selected source per-file size limit exceeded');
  if (!entry.headOid && !entry.indexOid && !proposal.blockers.some(item => item.code === 'selected_sandbox_source_metadata_incomplete' && item.paths?.includes(entry.path))) fail('untracked selected source blocker is missing');
}
function buildPack(review, request, proposal, raws) {
  let rawTotal = 0;
  const files = proposal.selectedSandboxEntries.map((entry, index) => {
    const raw = raws[index]; rawTotal += raw.length;
    if (raw.length > MAX_FILE_BYTES || rawTotal > MAX_RAW_BYTES) fail('selected source byte limit exceeded');
    if (raw.length !== entry.size || sha(raw) !== entry.workingSha256 || blobOid(raw, review.sandbox.head.length) !== entry.workingOid) fail('selected working bytes do not match review inventory');
    return { ...structuredClone(entry), bytesBase64: raw.toString('base64') };
  });
  const pack = {
    format: FORMAT, mode: MODE, review: structuredClone(review), request: structuredClone(request), files, rawBytes: rawTotal,
    execution: false, readyForMerge: false, readyForLaunch: false, nativeConsent: false,
  };
  pack.checkpointDigest = sha(Buffer.from(canon(pack), 'utf8'));
  serializedPackBytes(pack);
  return deepFreeze(pack);
}
function deepFreeze(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const child of Object.values(value)) deepFreeze(child);
  }
  return value;
}
export function captureManagedSourceCheckpoint({ review, request, stableRoot, sandboxRoot } = {}) {
  assertStrictJson(review);
  const req = validateManagedCheckpointRequest(request);
  const proposal = createManagedSourceSelectionProposal({ review, request: { format: 1, mode: 'managed_source_selection_request_v1', reviewDigest: req.reviewDigest, selectedPaths: req.selectedPaths } });
  if (proposal.proposal_digest !== req.proposalDigest) fail('checkpoint proposal digest does not match regenerated proposal');
  let plannedRawBytes = 0;
  for (const entry of proposal.selectedSandboxEntries) {
    validateSelectedEntry(entry, review.sandbox.head, proposal);
    plannedRawBytes += entry.size;
    if (plannedRawBytes > MAX_RAW_BYTES) fail('selected source aggregate byte limit exceeded');
  }
  normalizedRoot(stableRoot, review.stable.identity.worktreeRoot, 'stable');
  const root = normalizedRoot(sandboxRoot, review.sandbox.identity.worktreeRoot, 'sandbox');
  const raws = [];
  let total = 0;
  for (const entry of proposal.selectedSandboxEntries) {
    const raw = boundedRead(root, entry.path, entry.mode);
    total += raw.length;
    if (total > MAX_RAW_BYTES) fail('selected source aggregate byte limit exceeded');
    raws.push(raw);
  }
  for (let i = 0; i < proposal.selectedSandboxEntries.length; i++) {
    const again = boundedRead(root, proposal.selectedSandboxEntries[i].path, proposal.selectedSandboxEntries[i].mode);
    if (!again.equals(raws[i])) fail('selected source changed during checkpoint capture');
  }
  return buildPack(review, req, proposal, raws);
}
function validateReviewAndPack(value) {
  assertStrictJson(value);
  serializedPackBytes(value);
  exactKeys(value, TOP_KEYS, 'checkpoint');
  if (value.format !== FORMAT || value.mode !== MODE || FLAG_KEYS.some(key => value[key] !== false)) fail('checkpoint authority flags are invalid');
  digestSeal(value, 'checkpointDigest');
  const req = validateManagedCheckpointRequest(value.request);
  if (req.reviewDigest !== value.review.review_digest) fail('checkpoint request review binding is invalid');
  const proposal = createManagedSourceSelectionProposal({ review: value.review, request: { format: 1, mode: 'managed_source_selection_request_v1', reviewDigest: req.reviewDigest, selectedPaths: req.selectedPaths } });
  if (proposal.proposal_digest !== req.proposalDigest) fail('checkpoint proposal binding is invalid');
  let plannedRawBytes = 0;
  for (const entry of proposal.selectedSandboxEntries) {
    validateSelectedEntry(entry, value.review.sandbox.head, proposal);
    plannedRawBytes += entry.size;
    if (plannedRawBytes > MAX_RAW_BYTES) fail('checkpoint aggregate raw size exceeded');
  }
  if (!Array.isArray(value.files) || value.files.length !== proposal.selectedSandboxEntries.length) fail('checkpoint file set is invalid');
  let total = 0;
  for (let i = 0; i < value.files.length; i++) {
    const file = value.files[i], entry = proposal.selectedSandboxEntries[i];
    exactKeys(file, FILE_KEYS.filter(key => Object.hasOwn(entry, key) || key === 'bytesBase64'), 'checkpoint file');
    assertStrictJson(file);
    const expected = { ...entry };
    if (Object.keys(file).some(key => key !== 'bytesBase64' && JSON.stringify(file[key]) !== JSON.stringify(expected[key]))) fail('checkpoint inventory entry mismatch');
    if (file.path !== entry.path) fail('checkpoint file order/path mismatch');
    if (typeof file.bytesBase64 !== 'string' || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(file.bytesBase64)) fail('checkpoint base64 is invalid');
    const raw = Buffer.from(file.bytesBase64, 'base64');
    if (raw.toString('base64') !== file.bytesBase64 || raw.length > MAX_FILE_BYTES || raw.length !== entry.size || sha(raw) !== entry.workingSha256 || blobOid(raw, value.review.sandbox.head.length) !== entry.workingOid) fail('checkpoint raw content binding is invalid');
    total += raw.length;
    if (total > MAX_RAW_BYTES) fail('checkpoint aggregate raw size exceeded');
  }
  if (!Number.isSafeInteger(value.rawBytes) || value.rawBytes !== total || total !== plannedRawBytes) fail('checkpoint aggregate raw byte count is invalid');
  return value;
}
export function validateManagedSourceCheckpoint(value) {
  validateReviewAndPack(value);
  return deepFreeze(structuredClone(value));
}
function outputDirectory(sandboxRoot) {
  const root = normalizedRoot(sandboxRoot, undefined, 'sandbox');
  let current = root;
  for (const part of OUTPUT_DIR) {
    current = path.join(current, part);
    try { mkdirSync(current); } catch (error) { if (error.code !== 'EEXIST' || !existsSync(current)) throw error; }
    const stat = lstatSync(current);
    if (!stat.isDirectory() || stat.isSymbolicLink() || (stat.attributes & 0x400)) fail('checkpoint store ancestor is unsafe');
  }
  return current;
}
function inspectStore(directory, nextBytes = 0) {
  const names = readdirSync(directory);
  if (names.length >= MAX_STORED_FILES) fail('checkpoint store retained-file limit reached');
  let bytes = nextBytes;
  for (const name of names) {
    if (!/^[a-f0-9]{64}\.json$/.test(name)) fail('checkpoint store contains an unknown entry');
    const full = path.join(directory, name), stat = lstatSync(full);
    if (!stat.isFile() || stat.isSymbolicLink() || (stat.attributes & 0x400) || stat.nlink !== 1 || stat.size > MAX_SERIALIZED_PACK_BYTES) fail('checkpoint store entry is unsafe or oversized');
    bytes += stat.size;
  }
  if (bytes > MAX_STORED_BYTES) fail('checkpoint store logical byte limit reached');
}
function directoryIdentity(stat) { return [stat.dev, stat.ino, stat.mode, stat.nlink, stat.isDirectory()].join(':'); }
function snapshotDirectories(root, finalDirectory) {
  const volumeRoot = path.parse(root).root;
  const components = [...root.slice(volumeRoot.length).split(path.sep).filter(Boolean), ...path.relative(root, finalDirectory).split(path.sep).filter(Boolean)];
  let current = volumeRoot;
  const snapshot = [];
  for (const component of components) {
    current = path.join(current, component);
    const stat = lstatSync(current);
    if (!stat.isDirectory() || stat.isSymbolicLink() || (stat.attributes & 0x400)) fail('checkpoint output ancestor is unsafe');
    snapshot.push([current, directoryIdentity(stat)]);
  }
  return snapshot;
}
function assertDirectorySnapshot(snapshot) {
  for (const [directory, expected] of snapshot) {
    const stat = lstatSync(directory);
    if (!stat.isDirectory() || stat.isSymbolicLink() || (stat.attributes & 0x400) || directoryIdentity(stat) !== expected) fail('checkpoint output ancestor identity changed');
  }
}
function verifyStoredFile(file, fd, expectedBytes, directorySnapshot) {
  assertDirectorySnapshot(directorySnapshot);
  const before = fstatSync(fd), byPath = lstatSync(file);
  if (!before.isFile() || before.nlink !== 1 || identity(before) !== identity(byPath) || before.size !== expectedBytes.length) fail('checkpoint artifact identity changed');
  const actual = Buffer.alloc(expectedBytes.length); let offset = 0;
  while (offset < actual.length) {
    const count = readSync(fd, actual, offset, Math.min(64 * 1024, actual.length - offset), offset);
    if (count <= 0) fail('checkpoint artifact reread incomplete');
    offset += count;
  }
  if (!actual.equals(expectedBytes)) fail('checkpoint artifact reread mismatch');
  validateReviewAndPack(JSON.parse(actual.toString('utf8').slice(0, -1)));
  const after = fstatSync(fd), pathAfter = lstatSync(file);
  if (identity(before) !== identity(after) || identity(after) !== identity(pathAfter)) fail('checkpoint artifact changed after reread');
  assertDirectorySnapshot(directorySnapshot);
}
export function persistManagedSourceCheckpoint({ sandboxRoot, checkpoint } = {}) {
  const pack = validateManagedSourceCheckpoint(checkpoint);
  const root = normalizedRoot(sandboxRoot, pack.review.sandbox.identity.worktreeRoot, 'sandbox');
  for (const file of pack.files) {
    const live = boundedRead(root, file.path, file.mode);
    if (live.toString('base64') !== file.bytesBase64) fail('selected source changed before persistence');
  }
  const bytes = serializedPackBytes(pack);
  const directory = outputDirectory(root);
  const directorySnapshot = snapshotDirectories(root, directory);
  inspectStore(directory, bytes.length);
  assertDirectorySnapshot(directorySnapshot);
  const target = path.join(directory, `${pack.checkpointDigest}.json`);
  let fd;
  try {
    assertDirectorySnapshot(directorySnapshot);
    fd = openSync(target, fsConstants.O_RDWR | fsConstants.O_CREAT | fsConstants.O_EXCL, 0o600);
    assertDirectorySnapshot(directorySnapshot);
    const opened = fstatSync(fd);
    if (!opened.isFile() || opened.nlink !== 1 || identity(opened) !== identity(lstatSync(target))) fail('checkpoint output file is unsafe');
    let offset = 0;
    while (offset < bytes.length) {
      const count = writeSync(fd, bytes, offset, Math.min(64 * 1024, bytes.length - offset));
      if (count <= 0) fail('checkpoint artifact write made no progress');
      offset += count;
    }
    fsyncSync(fd);
    assertDirectorySnapshot(directorySnapshot);
    verifyStoredFile(target, fd, bytes, directorySnapshot);
    closeSync(fd); fd = undefined;
  } finally { if (fd !== undefined) closeSync(fd); }
  return {
    format: FORMAT, mode: RECEIPT_MODE, reviewDigest: pack.request.reviewDigest,
    proposalDigest: pack.request.proposalDigest, checkpointDigest: pack.checkpointDigest,
    selectedPaths: [...pack.request.selectedPaths], rawBytes: pack.rawBytes,
    storedBytes: bytes.length, checkpointCreated: true,
    execution: false, readyForMerge: false, readyForLaunch: false, nativeConsent: false,
  };
}
export function readManagedSourceCheckpoint({ sandboxRoot, checkpointDigest } = {}) {
  if (typeof checkpointDigest !== 'string' || !SHA256.test(checkpointDigest)) fail('checkpoint digest is invalid');
  const root = normalizedRoot(sandboxRoot, undefined, 'sandbox');
  let directory = root;
  for (const part of OUTPUT_DIR) {
    directory = path.join(directory, part);
    const stat = lstatSync(directory);
    if (!stat.isDirectory() || stat.isSymbolicLink() || (stat.attributes & 0x400)) fail('checkpoint store is unsafe');
  }
  const ancestors = snapshotDirectories(root, directory);
  assertDirectorySnapshot(ancestors);
  const file = path.join(directory, `${checkpointDigest}.json`);
  const pathStat = lstatSync(file);
  if (!pathStat.isFile() || pathStat.isSymbolicLink() || (pathStat.attributes & 0x400) || pathStat.nlink !== 1 || pathStat.size > MAX_SERIALIZED_PACK_BYTES) fail('checkpoint artifact exceeds 16 MiB serialized-pack limit or is unsafe');
  const fd = openSync(file, fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW || 0));
  try {
    assertDirectorySnapshot(ancestors);
    const before = fstatSync(fd);
    if (!before.isFile() || before.nlink !== 1 || identity(before) !== identity(pathStat) || before.size > MAX_SERIALIZED_PACK_BYTES) fail('checkpoint artifact changed before read or exceeds 16 MiB');
    const buffer = Buffer.alloc(before.size); let offset = 0;
    while (offset < buffer.length) {
      const count = readSync(fd, buffer, offset, Math.min(64 * 1024, buffer.length - offset), offset);
      if (count <= 0) fail('checkpoint artifact read incomplete');
      offset += count;
    }
    const after = fstatSync(fd), pathAfter = lstatSync(file);
    assertDirectorySnapshot(ancestors);
    if (identity(before) !== identity(after) || identity(after) !== identity(pathAfter)) fail('checkpoint artifact changed during read');
    if (buffer.at(-1) !== 10) fail('checkpoint artifact newline is missing');
    const parsed = JSON.parse(buffer.toString('utf8').slice(0, -1));
    validateReviewAndPack(parsed);
    if (parsed.review.sandbox.identity.worktreeRoot !== root) fail('checkpoint artifact belongs to another sandbox root');
    if (parsed.checkpointDigest !== checkpointDigest || !serializedPackBytes(parsed).equals(buffer)) fail('checkpoint artifact is not canonical or digest-bound');
    return deepFreeze(structuredClone(parsed));
  } finally { closeSync(fd); }
}