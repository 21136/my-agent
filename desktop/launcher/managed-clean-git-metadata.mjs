import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { lstat, readdir, open, realpath } from 'node:fs/promises';
import { constants as fsConstants } from 'node:fs';
import path from 'node:path';

const INDEX_HEADER_BYTES = 12;
const MAX_INDEX_BYTES = 64 * 1024 * 1024;
const MAX_INDEX_ENTRIES = 1_000_000;
const MAX_INDEX_PATH_BYTES = 64 * 1024;
const MAX_OBJECT_STORE_ENTRIES = 200_000;
const MAX_OBJECT_STORE_DEPTH = 5;
const MAX_REF_METADATA_ENTRIES = 100_000;
const MAX_REF_METADATA_DEPTH = 16;
const MAX_LOOSE_REF_BYTES = 4096;
const MAX_METADATA_FILE_BYTES = 64 * 1024;
const MAX_POWERSHELL_INPUT_BYTES = 16 * 1024 * 1024;
const MAX_POWERSHELL_OUTPUT_BYTES = 2 * 1024 * 1024;
const REPARSE_POINT = 0x400;
const INDEX_HASH_BYTES = Object.freeze({ sha1: 20, sha256: 32 });

function checksum(bytes, format) { return createHash(format).update(bytes).digest(); }
function isReparse(stats) {
  return stats.isSymbolicLink() || (Number.isInteger(stats.attributes) && (stats.attributes & REPARSE_POINT) !== 0);
}
function isSafeStatNumber(value) { return Number.isSafeInteger(value) && value >= 0; }
function assertSafeStatNumbers(stats) {
  for (const key of ['dev', 'ino', 'size', 'nlink']) {
    if (!isSafeStatNumber(stats[key])) throw new Error(`Git metadata ${key} identity is unavailable`);
  }
}
function compareSnapshot(a, b) {
  return a.dev === b.dev && a.ino === b.ino && a.size === b.size && a.nlink === b.nlink && a.mtimeMs === b.mtimeMs && a.ctimeMs === b.ctimeMs;
}
function indexError(message) { throw new Error(`Invalid plain Git index: ${message}`); }
function readUInt32(bytes, offset, limit, label) {
  if (offset < 0 || offset + 4 > limit) indexError(`truncated ${label}`);
  return bytes.readUInt32BE(offset);
}
function validateIndexPath(pathBytes, pathText) {
  if (pathBytes.length === 0 || pathBytes.length > MAX_INDEX_PATH_BYTES) indexError('path length is invalid');
  if (pathText.startsWith('/') || pathText.includes('\\') || pathText.includes('\0')) indexError('path is not a safe relative Git path');
  const segments = pathText.split('/');
  if (segments.some((part) => !part || part === '.' || part === '..')) indexError('path contains an empty or traversal segment');
}

function parseCacheTree(payload, objectFormat, indexEntryCount) {
  const oidBytes = INDEX_HASH_BYTES[objectFormat];
  if (!payload.length) indexError('TREE cache-tree payload is empty');
  let offset = 0;
  let records = 0;
  const seenPaths = new Set();
  function parseNode(parentPath, depth, isRoot, parentEntryCount) {
    if (depth > 128 || ++records > MAX_INDEX_ENTRIES) indexError('TREE cache-tree depth or node count exceeds the bound');
    const nul = payload.indexOf(0, offset);
    if (nul < offset) indexError('TREE cache-tree path lacks a NUL terminator');
    const componentBytes = payload.subarray(offset, nul);
    offset = nul + 1;
    let component;
    try { component = new TextDecoder('utf-8', { fatal: true }).decode(componentBytes); }
    catch { indexError('TREE cache-tree path is not valid UTF-8'); }
    if (isRoot) {
      if (componentBytes.length !== 0) indexError('TREE cache-tree first node is not the root');
    } else if (!component || componentBytes.length > 1024 || component === '.' || component === '..' || component.includes('/') || component.includes('\\') || !/^[A-Za-z0-9_@+.,= -]+$/.test(component) || /[. ]$/.test(component) || /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i.test(component) || /[\0\r\n]/.test(component)) {
      indexError('TREE cache-tree child path is not a plain component');
    }
    const fullPath = isRoot ? '' : parentPath ? `${parentPath}/${component}` : component;
    if (Buffer.byteLength(fullPath, 'utf8') > MAX_INDEX_PATH_BYTES) indexError('TREE cache-tree path exceeds the bound');
    if (seenPaths.has(fullPath)) indexError('TREE cache-tree contains a duplicate path');
    seenPaths.add(fullPath);

    const newline = payload.indexOf(0x0a, offset);
    if (newline < offset) indexError('TREE cache-tree node header is truncated');
    const headerBytes = payload.subarray(offset, newline);
    if ([...headerBytes].some((byte) => byte > 0x7f || byte === 0x00 || byte === 0x0d)) indexError('TREE cache-tree node header is not ASCII');
    const match = /^(-1|0|[1-9][0-9]*) (0|[1-9][0-9]*)$/.exec(headerBytes.toString('ascii'));
    if (!match) indexError('TREE cache-tree node counts are malformed');
    const entryCount = Number(match[1]);
    const subtreeCount = Number(match[2]);
    if (!Number.isSafeInteger(entryCount) || entryCount < -1 || entryCount > MAX_INDEX_ENTRIES || !Number.isSafeInteger(subtreeCount) || subtreeCount > MAX_INDEX_ENTRIES) {
      indexError('TREE cache-tree node count exceeds the bound');
    }
    if (isRoot && entryCount >= 0 && entryCount !== indexEntryCount) indexError('TREE cache-tree root count disagrees with index entries');
    if (!isRoot && parentEntryCount >= 0 && entryCount >= 0 && entryCount > parentEntryCount) indexError('TREE cache-tree child count exceeds its parent');
    offset = newline + 1;
    if (entryCount >= 0) {
      if (offset + oidBytes > payload.length) indexError('TREE cache-tree object ID is truncated');
      const treeOid = payload.subarray(offset, offset + oidBytes);
      if (treeOid.every((byte) => byte === 0)) indexError('TREE cache-tree object ID is null');
      offset += oidBytes;
    }
    const childNames = new Set();
    for (let child = 0; child < subtreeCount; child++) {
      const childNul = payload.indexOf(0, offset);
      if (childNul < offset) indexError('TREE cache-tree child node is truncated');
      let childName;
      try { childName = new TextDecoder('utf-8', { fatal: true }).decode(payload.subarray(offset, childNul)); }
      catch { indexError('TREE cache-tree child name is not valid UTF-8'); }
      if (childNames.has(childName)) indexError('TREE cache-tree has duplicate sibling nodes');
      childNames.add(childName);
      parseNode(fullPath, depth + 1, false, entryCount);
    }
  }
  parseNode('', 0, true, -1);
  if (offset !== payload.length) indexError('TREE cache-tree payload has trailing bytes');
}
/** Strictly validate a raw, checksummed Git index v2 without invoking Git. */
export function assertManagedPlainIndex(bytes, { objectFormat } = {}) {
  if (!Buffer.isBuffer(bytes)) indexError('input must be a Buffer');
  if (!Object.hasOwn(INDEX_HASH_BYTES, objectFormat)) indexError('objectFormat must be sha1 or sha256');
  const digestBytes = INDEX_HASH_BYTES[objectFormat];
  if (bytes.length < INDEX_HEADER_BYTES + digestBytes || bytes.length > MAX_INDEX_BYTES) indexError('file size is truncated or exceeds the bound');
  if (bytes.toString('ascii', 0, 4) !== 'DIRC') indexError('signature is not DIRC');
  const version = readUInt32(bytes, 4, bytes.length - digestBytes, 'header');
  if (version !== 2) indexError('only version 2 is supported');
  const count = readUInt32(bytes, 8, bytes.length - digestBytes, 'entry count');
  if (count > MAX_INDEX_ENTRIES) indexError('entry count exceeds the bound');

  const contentEnd = bytes.length - digestBytes;
  const expectedChecksum = checksum(bytes.subarray(0, contentEnd), objectFormat);
  if (!bytes.subarray(contentEnd).equals(expectedChecksum)) indexError('checksum mismatch');

  let offset = INDEX_HEADER_BYTES;
  const seenPaths = new Set();
  for (let index = 0; index < count; index++) {
    const entryStart = offset;
    const fixedEnd = entryStart + 40;
    if (fixedEnd + digestBytes + 2 > contentEnd) indexError('truncated entry header');
    const mode = bytes.readUInt32BE(entryStart + 24);
    if (mode !== 0o100644 && mode !== 0o100755) indexError('entry mode is not an ordinary file');

    const flagsOffset = fixedEnd + digestBytes;
    const flags = bytes.readUInt16BE(flagsOffset);
    if ((flags & 0x8000) !== 0) indexError('assume-unchanged entry flag is unsupported');
    if ((flags & 0x4000) !== 0) indexError('extended entry flags are unsupported');
    if ((flags & 0x3000) !== 0) indexError('nonzero merge stage is unsupported');

    const nameLengthFlag = flags & 0x0fff;
    const pathStart = flagsOffset + 2;
    const nulOffset = bytes.indexOf(0, pathStart);
    if (nulOffset < pathStart || nulOffset >= contentEnd) indexError('entry path lacks a bounded NUL terminator');
    const pathBytes = bytes.subarray(pathStart, nulOffset);
    if (nameLengthFlag === 0x0fff ? pathBytes.length < 0x0fff : pathBytes.length !== nameLengthFlag) indexError('entry path length disagrees with flags');
    let pathText;
    try { pathText = new TextDecoder('utf-8', { fatal: true }).decode(pathBytes); }
    catch { indexError('entry path is not valid UTF-8'); }
    validateIndexPath(pathBytes, pathText);
    if (seenPaths.has(pathText)) indexError('duplicate entry path');
    seenPaths.add(pathText);

    offset = nulOffset + 1;
    while ((offset - entryStart) % 8 !== 0) {
      if (offset >= contentEnd) indexError('truncated entry padding');
      if (bytes[offset] !== 0) indexError('entry padding is not zero-filled');
      offset++;
    }
    if (offset > contentEnd) indexError('entry padding exceeds the index body');
  }

  const extensionNames = new Set();
  const extensions = [];
  while (offset < contentEnd) {
    if (contentEnd - offset < 8) indexError('truncated extension header');
    const signatureBytes = bytes.subarray(offset, offset + 4);
    if (!/^[A-Za-z]{4}$/.test(signatureBytes.toString('ascii'))) indexError('malformed extension signature');
    const signature = signatureBytes.toString('ascii');
    const size = readUInt32(bytes, offset + 4, contentEnd, 'extension size');
    offset += 8;
    if (size > contentEnd - offset) indexError(`truncated ${signature} extension payload`);
    if (extensionNames.has(signature)) indexError(`duplicate ${signature} extension`);
    extensionNames.add(signature);
    extensions.push({ signature, payload: bytes.subarray(offset, offset + size) });
    offset += size;
  }
  if (offset !== contentEnd) indexError('extension framing does not end at checksum');
  const unsupported = extensions.find(({ signature }) => signature !== 'TREE');
  if (unsupported) indexError(`unsupported extension ${unsupported.signature}`);
  if (extensions.length > 1) indexError('duplicate TREE extension');
  if (extensions.length === 1) parseCacheTree(extensions[0].payload, objectFormat, count);
}

function normalizedAbsolute(input, label) {
  if (typeof input !== 'string' || !path.isAbsolute(input) || input.includes('\0') || /[\r\n]/.test(input)) {
    throw new Error(`${label} must be a normalized absolute path`);
  }
  const resolved = path.resolve(input);
  if (input !== resolved) throw new Error(`${label} must use its exact normalized spelling`);
  return resolved;
}
function metadataError(message) { throw new Error(`Unsafe managed Git metadata: ${message}`); }
async function assertPathChain(target, expected, checkedPaths) {
  const root = path.parse(target).root;
  if (!root) metadataError('filesystem path has no volume root');
  let current = root;
  const parts = target.slice(root.length).split(path.sep).filter(Boolean);
  for (let index = 0; index < parts.length; index++) {
    current = path.join(current, parts[index]);
    let stats;
    try { stats = await lstat(current); }
    catch { metadataError('required metadata path is missing or inaccessible'); }
    assertSafeStatNumbers(stats);
    if (isReparse(stats)) metadataError('link or reparse point in metadata path');
    const isFinal = index === parts.length - 1;
    if (!isFinal && !stats.isDirectory()) metadataError('metadata ancestor is not an ordinary directory');
    if (isFinal && expected === 'directory' && !stats.isDirectory()) metadataError('metadata path is not a directory');
    if (isFinal && expected === 'file') {
      if (!stats.isFile()) metadataError('metadata path is not a regular file');
      if (stats.nlink !== 1) metadataError('hardlinked metadata file is unsupported');
    }
    let real;
    try { real = await realpath(current); }
    catch { metadataError('metadata path cannot be resolved safely'); }
    if (real !== current) metadataError('metadata path uses an aliased spelling');
    checkedPaths.add(current);
  }
  if (parts.length === 0) checkedPaths.add(target);
  return target;
}
async function optionalPath(target, expected, checkedPaths) {
  try { await lstat(target); }
  catch (error) { if (error.code === 'ENOENT') return false; metadataError('optional metadata path cannot be inspected'); }
  await assertPathChain(target, expected, checkedPaths);
  return true;
}
async function readSmallMetadataFile(file, checkedPaths, maxBytes = MAX_METADATA_FILE_BYTES) {
  await assertPathChain(file, 'file', checkedPaths);
  let before;
  try { before = await lstat(file); }
  catch { metadataError('metadata file disappeared before bounded read'); }
  assertSafeStatNumbers(before);
  if (!before.isFile() || isReparse(before) || before.nlink !== 1 || before.size > maxBytes) metadataError('metadata file is not an ordinary bounded single-link file');
  let handle;
  try { handle = await open(file, fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW ?? 0)); }
  catch { metadataError('metadata file cannot be opened without following links'); }
  try {
    const opened = await handle.stat();
    assertSafeStatNumbers(opened);
    if (!opened.isFile() || isReparse(opened) || opened.nlink !== 1 || !compareSnapshot(before, opened)) metadataError('metadata file changed while opening');
    const bytes = Buffer.alloc(opened.size);
    let offset = 0;
    while (offset < bytes.length) {
      const { bytesRead } = await handle.read(bytes, offset, bytes.length - offset, offset);
      if (!Number.isSafeInteger(bytesRead) || bytesRead <= 0) metadataError('metadata file read was truncated');
      offset += bytesRead;
    }
    const after = await handle.stat();
    let namedAfter;
    try { namedAfter = await lstat(file); }
    catch { metadataError('metadata file path disappeared during bounded read'); }
    assertSafeStatNumbers(after);
    assertSafeStatNumbers(namedAfter);
    await assertPathChain(file, 'file', checkedPaths);
    if (!compareSnapshot(opened, after) || !compareSnapshot(after, namedAfter)) metadataError('metadata file changed during bounded read');
    return bytes;
  } finally { await handle.close(); }
}
function decodeMetadata(bytes, label) {
  try {
    const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    if (text.includes('\0')) metadataError(`${label} contains a NUL byte`);
    return text;
  } catch (error) {
    if (error.message.startsWith('Unsafe managed Git metadata:')) throw error;
    metadataError(`${label} is not valid UTF-8`);
  }
}

function assertObjectEntryName(relativeParts, name, isDirectory) {
  const depth = relativeParts.length;
  if (depth === 0) {
    if (isDirectory && (name === 'info' || name === 'pack' || /^[a-f0-9]{2}$/.test(name))) return;
    metadataError('unexpected entry in the objects directory');
  }
  if (relativeParts[0] === 'info') {
    if (depth === 1 && ((name === 'commit-graphs' && isDirectory) || (!isDirectory && ['packs', 'commit-graph'].includes(name)))) return;
    if (depth === 2 && relativeParts[1] === 'commit-graphs' && !isDirectory && (name === 'commit-graph-chain' || /^graph-[a-f0-9]{40,64}\.graph$/.test(name))) return;
    if (name === 'alternates' || name === 'http-alternates') metadataError('object alternates are unsupported');
    metadataError('unexpected entry in objects/info');
  }
  if (relativeParts[0] === 'pack') {
    if (depth === 1 && !isDirectory && (/^pack-[a-f0-9]{40,64}\.(?:pack|idx|rev|bitmap|keep)$/.test(name) || name === 'multi-pack-index' || /^multi-pack-index-[a-f0-9]{40,64}\.bitmap$/.test(name))) return;
    metadataError('unexpected entry in objects/pack');
  }
  if (/^[a-f0-9]{2}$/.test(relativeParts[0])) {
    if (depth === 1 && !isDirectory && /^[a-f0-9]{38}$|^[a-f0-9]{62}$/.test(name)) return;
    metadataError('invalid loose-object fanout entry');
  }
  metadataError('unexpected object-store nesting');
}
async function scanObjectStore(objectsDir, checkedPaths) {
  let count = 0;
  async function visit(directory, relativeParts, depth) {
    if (depth > MAX_OBJECT_STORE_DEPTH) metadataError('object-store depth exceeds the bound');
    await assertPathChain(directory, 'directory', checkedPaths);
    let names;
    try { names = await readdir(directory); }
    catch { metadataError('object-store directory cannot be enumerated'); }
    names.sort();
    for (const name of names) {
      count++;
      if (count > MAX_OBJECT_STORE_ENTRIES) metadataError('object-store entry count exceeds the bound');
      if (!name || name === '.' || name === '..' || name.includes('/') || name.includes('\\') || name.includes('\0')) metadataError('invalid object-store entry name');
      const absolute = path.join(directory, name);
      const parts = [...relativeParts, name];
      let stats;
      try { stats = await lstat(absolute); }
      catch { metadataError('object-store entry changed or cannot be inspected'); }
      assertSafeStatNumbers(stats);
      if (isReparse(stats)) metadataError('object-store link or reparse point rejected');
      const isDirectory = stats.isDirectory();
      if (!isDirectory && !stats.isFile()) metadataError('special object-store entry rejected');
      if (!isDirectory && stats.nlink !== 1) metadataError('hardlinked object-store file rejected');
      assertObjectEntryName(relativeParts, name, isDirectory);
      checkedPaths.add(absolute);
      const real = await realpath(absolute).catch(() => null);
      if (real !== absolute) metadataError('object-store entry uses an aliased spelling');
      if (isDirectory) await visit(absolute, parts, depth + 1);
    }
  }
  await visit(objectsDir, [], 0);
}

async function scanRefMetadata(refsDirectory, checkedPaths, state, relativeParts = [], depth = 0) {
  if (depth > MAX_REF_METADATA_DEPTH) metadataError('refs metadata depth exceeds the bound');
  await assertPathChain(refsDirectory, 'directory', checkedPaths);
  let names;
  try { names = await readdir(refsDirectory); }
  catch { metadataError('refs metadata directory cannot be enumerated'); }
  names.sort();
  for (const name of names) {
    if (++state.count > MAX_REF_METADATA_ENTRIES) metadataError('refs metadata entry count exceeds the bound');
    if (!name || name === '.' || name === '..' || name.includes('/') || name.includes('\\') || name.includes('\0')) metadataError('invalid refs metadata entry name');
    if (relativeParts.length === 0 && name === 'replace') metadataError('replace refs are unsupported');
    const absolute = path.join(refsDirectory, name);
    let stats;
    try { stats = await lstat(absolute); }
    catch { metadataError('refs metadata entry changed or cannot be inspected'); }
    assertSafeStatNumbers(stats);
    if (isReparse(stats)) metadataError('refs metadata link or reparse point rejected');
    if (stats.isDirectory()) {
      await assertPathChain(absolute, 'directory', checkedPaths);
      await scanRefMetadata(absolute, checkedPaths, state, [...relativeParts, name], depth + 1);
    } else if (stats.isFile()) {
      await readSmallMetadataFile(absolute, checkedPaths, MAX_LOOSE_REF_BYTES);
    } else metadataError('special refs metadata entry rejected');
  }
}
async function scanPackedRefs(directory, checkedPaths) {
  const packedRefs = path.join(directory, 'packed-refs');
  if (await optionalPath(packedRefs, 'file', checkedPaths)) {
    await readSmallMetadataFile(packedRefs, checkedPaths, MAX_METADATA_FILE_BYTES);
  }
}

const WINDOWS_REPARSE_PROBE = String.raw`
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
  $paths = ConvertFrom-Json -InputObject ([Console]::In.ReadToEnd())
  $bad = [System.Collections.Generic.List[string]]::new()
  foreach ($p in $paths) {
    $item = Get-Item -Force -LiteralPath ([string]$p) -ErrorAction Stop
    if (([int]$item.Attributes -band 1024) -ne 0) { $bad.Add([string]$p) }
  }
  $result = @{ reparsePaths = @($bad.ToArray()) }
  [Console]::Out.Write(($result | ConvertTo-Json -Compress -Depth 3))
} catch {
  [Console]::Error.Write('reparse probe failed')
  exit 2
}
`;
async function verifyWindowsReparseAttributes(paths) {
  if (process.platform !== 'win32') return;
  const systemRoot = process.env.SystemRoot ?? process.env.SYSTEMROOT ?? process.env.WINDIR;
  if (typeof systemRoot !== 'string' || !path.isAbsolute(systemRoot)) metadataError('Windows system root is unavailable for reparse verification');
  const powershell = path.join(systemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
  const input = Buffer.from(JSON.stringify(paths), 'utf8');
  if (input.length > MAX_POWERSHELL_INPUT_BYTES) metadataError('Windows reparse probe path list exceeds the bound');
  const env = {
    SYSTEMROOT: systemRoot,
    SystemRoot: systemRoot,
    WINDIR: systemRoot,
    PATH: path.join(systemRoot, 'System32'),
    TEMP: systemRoot,
    TMP: systemRoot,
    PSModulePath: '',
  };
  const result = spawnSync(powershell, ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', WINDOWS_REPARSE_PROBE], {
    cwd: systemRoot,
    env,
    input,
    encoding: 'buffer',
    windowsHide: true,
    timeout: 15_000,
    maxBuffer: MAX_POWERSHELL_OUTPUT_BYTES,
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  if (result.error || result.status !== 0 || result.signal) metadataError('Windows reparse attribute probe failed');
  let output;
  try { output = JSON.parse(result.stdout.toString('utf8')); }
  catch { metadataError('Windows reparse attribute probe returned malformed data'); }
  if (!output || typeof output !== 'object' || Array.isArray(output) || Object.keys(output).length !== 1 || !Array.isArray(output.reparsePaths)) {
    metadataError('Windows reparse attribute probe returned an invalid shape');
  }
  if (output.reparsePaths.length) metadataError('Windows reparse point found by the system attribute probe');
}

async function assertOrdinaryMetadataDirectory(directory, checkedPaths) {
  await assertPathChain(directory, 'directory', checkedPaths);
}
async function rejectIfPresent(file, checkedPaths, label) {
  if (await optionalPath(file, 'file', checkedPaths)) metadataError(`${label} is unsupported`);
}

/** Guard the shared Git object store and relevant worktree metadata before Git is invoked. */
export async function assertManagedGitObjectStore({ commonDir: commonInput, gitDir: gitInput } = {}) {
  const commonDir = normalizedAbsolute(commonInput, 'commonDir');
  const gitDir = normalizedAbsolute(gitInput, 'gitDir');
  const checkedPaths = new Set();
  await assertOrdinaryMetadataDirectory(commonDir, checkedPaths);
  await assertOrdinaryMetadataDirectory(gitDir, checkedPaths);
  const sameGitDirectory = commonDir === gitDir;
  const commondirFile = path.join(gitDir, 'commondir');
  if (!sameGitDirectory) {
    const commondir = decodeMetadata(await readSmallMetadataFile(commondirFile, checkedPaths, 4096), 'commondir').trim();
    if (!commondir || /[\r\n\0]/.test(commondir) || path.resolve(gitDir, commondir) !== commonDir) metadataError('worktree commondir does not bind the supplied common directory');
  } else if (await optionalPath(commondirFile, 'file', checkedPaths)) {
    metadataError('common Git directory unexpectedly contains commondir indirection');
  }

  const requiredFiles = [
    [path.join(gitDir, 'HEAD'), 'HEAD'],
    [path.join(gitDir, 'index'), 'index'],
    [path.join(commonDir, 'config'), 'common config'],
  ];
  for (const [file, label] of requiredFiles) {
    await assertPathChain(file, 'file', checkedPaths);
    if (label === 'HEAD') {
      const head = decodeMetadata(await readSmallMetadataFile(file, checkedPaths, 4096), label);
      if (!head.endsWith('\n') || head.length > 4096) metadataError('HEAD metadata is malformed');
    }
  }

  for (const directory of [
    path.join(commonDir, 'objects'),
    path.join(commonDir, 'objects', 'info'),
    path.join(commonDir, 'objects', 'pack'),
    path.join(commonDir, 'info'),
    path.join(commonDir, 'refs'),
  ]) await assertOrdinaryMetadataDirectory(directory, checkedPaths);

  for (const [file, label] of [
    [path.join(commonDir, 'objects', 'info', 'alternates'), 'object alternates'],
    [path.join(commonDir, 'objects', 'info', 'http-alternates'), 'HTTP object alternates'],
    [path.join(commonDir, 'info', 'grafts'), 'grafts'],
    [path.join(commonDir, 'shallow'), 'shallow repository state'],
    [path.join(gitDir, 'shallow'), 'worktree shallow state'],
    [path.join(gitDir, 'config.worktree'), 'worktree-specific config'],
  ]) await rejectIfPresent(file, checkedPaths, label);

  const indexNames = await readdir(gitDir).catch(() => metadataError('Git directory cannot be enumerated'));
  if (indexNames.some((name) => name.startsWith('sharedindex.'))) metadataError('split/shared index indirection is unsupported');
  if (!sameGitDirectory && await optionalPath(path.join(gitDir, 'objects'), 'directory', checkedPaths)) metadataError('worktree-local object directory is unsupported');
  const replaceRefs = path.join(commonDir, 'refs', 'replace');
  if (await optionalPath(replaceRefs, 'directory', checkedPaths)) metadataError('replace refs are unsupported');

  for (const optional of [path.join(commonDir, 'logs'), path.join(gitDir, 'logs'), path.join(gitDir, 'refs'), path.join(gitDir, 'info')]) {
    await optionalPath(optional, 'directory', checkedPaths);
  }
  for (const file of [path.join(commonDir, 'info', 'exclude'), path.join(gitDir, 'gitdir')]) {
    if (await optionalPath(file, 'file', checkedPaths)) {
      const bytes = await readSmallMetadataFile(file, checkedPaths, 4096);
      decodeMetadata(bytes, path.basename(file));
    }
  }

  const refState = { count: 0 };
  await scanRefMetadata(path.join(commonDir, 'refs'), checkedPaths, refState);
  if (!sameGitDirectory && await optionalPath(path.join(gitDir, 'refs'), 'directory', checkedPaths)) {
    await scanRefMetadata(path.join(gitDir, 'refs'), checkedPaths, refState);
  }
  await scanPackedRefs(commonDir, checkedPaths);
  if (!sameGitDirectory) await scanPackedRefs(gitDir, checkedPaths);
  await scanObjectStore(path.join(commonDir, 'objects'), checkedPaths);
  await verifyWindowsReparseAttributes([...checkedPaths].sort());
}