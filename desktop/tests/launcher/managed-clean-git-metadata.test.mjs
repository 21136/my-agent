import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createRequire, syncBuiltinESMExports } from 'node:module';
import { link, mkdir, mkdtemp, symlink, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { assertManagedPlainIndex, assertManagedGitObjectStore } from '../../launcher/managed-clean-git-metadata.mjs';

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const expectedSandboxRoot = path.resolve('F:/my-agent-main/.worktrees/sandbox');
if (path.resolve(process.cwd()) !== repositoryRoot || repositoryRoot !== expectedSandboxRoot) {
  throw new Error('Tests must run from F:/my-agent-main/.worktrees/sandbox; no fixtures were created');
}
const evidenceRoot = path.join(repositoryRoot, 'artifacts/managed-clean-git-metadata-20261024');
const objectBytes = { sha1: 20, sha256: 32 };
const objectHashes = { sha1: 'sha1', sha256: 'sha256' };
const hash = (format, bytes) => createHash(objectHashes[format]).update(bytes).digest();
const mode = 0o100644;

function makeIndex({ objectFormat = 'sha1', version = 2, filePath = 'desktop/x.mjs', flagsOverride, paddingByte = 0, extensions = [], countOverride } = {}) {
  const oidBytes = objectBytes[objectFormat];
  const name = Buffer.from(filePath, 'utf8');
  const fixed = Buffer.alloc(40);
  fixed.writeUInt32BE(mode, 24);
  fixed.writeUInt32BE(17, 36);
  const flags = Buffer.alloc(2);
  flags.writeUInt16BE(flagsOverride ?? Math.min(name.length, 0x0fff));
  const start = Buffer.concat([Buffer.from('DIRC'), uint32(version), uint32(countOverride ?? 1), fixed, Buffer.alloc(oidBytes, 0x33), flags, name, Buffer.from([0])]);
  const padding = (8 - ((start.length - 12) % 8)) % 8;
  const pad = Buffer.alloc(padding, paddingByte);
  const framedExtensions = extensions.map(({ signature, payload = Buffer.alloc(0), declaredSize = payload.length }) => Buffer.concat([Buffer.from(signature, 'ascii'), uint32(declaredSize), payload]));
  const body = Buffer.concat([start, pad, ...framedExtensions]);
  return Buffer.concat([body, hash(objectFormat, body)]);
}
function uint32(value) { const bytes = Buffer.alloc(4); bytes.writeUInt32BE(value); return bytes; }
function treePayload({ entryCount = '1', subtreeCount = '0', treeOid = Buffer.alloc(20, 0x55), child = Buffer.alloc(0) } = {}) {
  return Buffer.concat([Buffer.from(`\0${entryCount} ${subtreeCount}\n`, 'ascii'), treeOid, child]);
}

async function storeFixture(t, { separateGitDir = false } = {}) {
  await mkdir(evidenceRoot, { recursive: true });
  const operation = await mkdtemp(path.join(evidenceRoot, 'store-'));
  const commonDir = path.join(operation, 'common');
  const gitDir = separateGitDir ? path.join(commonDir, 'worktrees', 'sandbox') : commonDir;
  await mkdir(path.join(commonDir, 'objects', 'info'), { recursive: true });
  await mkdir(path.join(commonDir, 'objects', 'pack'), { recursive: true });
  await mkdir(path.join(commonDir, 'info'), { recursive: true });
  await mkdir(path.join(commonDir, 'refs'), { recursive: true });
  await mkdir(path.join(commonDir, 'logs'), { recursive: true });
  if (separateGitDir) {
    await mkdir(gitDir, { recursive: true });
    await writeFile(path.join(gitDir, 'commondir'), path.relative(gitDir, commonDir) + '\n');
  }
  await writeFile(path.join(gitDir, 'index'), makeIndex());
  await writeFile(path.join(gitDir, 'HEAD'), 'ref: refs/heads/main\n');
  await writeFile(path.join(commonDir, 'config'), '[core]\nrepositoryformatversion = 0\nbare = false\n');
  t.diagnostic(`retained metadata fixture: ${operation}`);
  return { operation, commonDir, gitDir };
}

test('accepts checksummed v2 SHA-1 and SHA-256 plain indexes', () => {
  assert.equal(assertManagedPlainIndex(makeIndex({ objectFormat: 'sha1' }), { objectFormat: 'sha1' }), undefined);
  assert.equal(assertManagedPlainIndex(makeIndex({ objectFormat: 'sha256' }), { objectFormat: 'sha256' }), undefined);
});

test('rejects unsupported versions, object-format mismatch, bad checksums, and truncated headers', () => {
  assert.throws(() => assertManagedPlainIndex(makeIndex({ version: 3 }), { objectFormat: 'sha1' }), /version|v2/i);
  assert.throws(() => assertManagedPlainIndex(makeIndex({ objectFormat: 'sha1' }), { objectFormat: 'sha256' }), /checksum|truncated|index/i);
  const corrupt = makeIndex(); corrupt[20] ^= 0x01;
  assert.throws(() => assertManagedPlainIndex(corrupt, { objectFormat: 'sha1' }), /checksum/i);
  assert.throws(() => assertManagedPlainIndex(Buffer.from('DIRC'), { objectFormat: 'sha1' }), /truncated|header/i);
});

test('rejects impossible counts, malformed path lengths, modes, and nonzero entry padding', () => {
  assert.throws(() => assertManagedPlainIndex(makeIndex({ countOverride: 0xffffffff }), { objectFormat: 'sha1' }), /count|entry|truncated/i);
  assert.throws(() => assertManagedPlainIndex(makeIndex({ flagsOverride: 4 }), { objectFormat: 'sha1' }), /path|name|length/i);
  const badMode = makeIndex(); badMode.writeUInt32BE(0o120000, 12 + 24);
  const badModeChecksum = Buffer.concat([badMode.subarray(0, -20), hash('sha1', badMode.subarray(0, -20))]);
  assert.throws(() => assertManagedPlainIndex(badModeChecksum, { objectFormat: 'sha1' }), /mode|regular/i);
  assert.throws(() => assertManagedPlainIndex(makeIndex({ paddingByte: 0x41 }), { objectFormat: 'sha1' }), /padding/i);
});

test('rejects assume-unchanged, non-stage-zero, and extended index flags', () => {
  for (const flags of [0x8000, 0x1000, 0x4000]) {
    assert.throws(() => assertManagedPlainIndex(makeIndex({ flagsOverride: flags | Buffer.byteLength('desktop/clean.mjs') }), { objectFormat: 'sha1' }), /flag|stage|extended|assume/i);
  }
});

test('parses extension framing then rejects link, FSMN, UNTR, TREE, unknown, duplicate, and truncated extensions', () => {
  for (const signature of ['link', 'FSMN', 'UNTR', 'XTRA']) {
    assert.throws(() => assertManagedPlainIndex(makeIndex({ extensions: [{ signature, payload: Buffer.from('x') }] }), { objectFormat: 'sha1' }), /extension/i);
  }
  assert.throws(() => assertManagedPlainIndex(makeIndex({ extensions: [{ signature: 'TREE' }, { signature: 'TREE' }] }), { objectFormat: 'sha1' }), /duplicate|extension/i);
  assert.throws(() => assertManagedPlainIndex(makeIndex({ extensions: [{ signature: 'TREE' }] }), { objectFormat: 'sha1' }), /TREE|tree|cache/i);
  assert.throws(() => assertManagedPlainIndex(makeIndex({ extensions: [{ signature: 'TREE', payload: Buffer.alloc(0), declaredSize: 10 }] }), { objectFormat: 'sha1' }), /truncated|extension/i);
});

test('accepts ordinary bounded Git object storage and linked-worktree metadata', async t => {
  for (const separateGitDir of [false, true]) {
    const fixture = await storeFixture(t, { separateGitDir });
    const loose = path.join(fixture.commonDir, 'objects', 'ab');
    await mkdir(loose);
    await writeFile(path.join(loose, 'c'.repeat(38)), Buffer.from('opaque object bytes'));
    await assert.doesNotReject(() => assertManagedGitObjectStore(fixture));
  }
});

test('accepts bounded ordinary loose and packed refs in common and worktree metadata', async t => {
  const fixture = await storeFixture(t, { separateGitDir: true });
  await mkdir(path.join(fixture.commonDir, 'refs', 'heads', 'feature'), { recursive: true });
  await writeFile(path.join(fixture.commonDir, 'refs', 'heads', 'feature', 'topic'), `${'1'.repeat(40)}\n`);
  await writeFile(path.join(fixture.commonDir, 'packed-refs'), `# pack-refs with: peeled fully-peeled\n${'2'.repeat(40)} refs/tags/release\n`);
  await mkdir(path.join(fixture.gitDir, 'refs', 'bisect'), { recursive: true });
  await writeFile(path.join(fixture.gitDir, 'refs', 'bisect', 'good'), `${'3'.repeat(40)}\n`);
  await assert.doesNotReject(() => assertManagedGitObjectStore(fixture));
});

test('rejects oversized packed refs and loose refs before Git can inspect them', async t => {
  const packed = await storeFixture(t);
  await writeFile(path.join(packed.commonDir, 'packed-refs'), Buffer.alloc(64 * 1024 + 1, 0x61));
  await assert.rejects(() => assertManagedGitObjectStore(packed), /bounded|oversized|metadata/i);

  const loose = await storeFixture(t);
  await mkdir(path.join(loose.commonDir, 'refs', 'heads'));
  await writeFile(path.join(loose.commonDir, 'refs', 'heads', 'large'), Buffer.alloc(4097, 0x61));
  await assert.rejects(() => assertManagedGitObjectStore(loose), /bounded|oversized|metadata/i);
});

test('rejects linked loose-ref metadata before Git', async t => {
  const fixture = await storeFixture(t);
  const outside = path.join(fixture.operation, 'outside-ref');
  await writeFile(outside, `${'4'.repeat(40)}\n`);
  await mkdir(path.join(fixture.commonDir, 'refs', 'heads'));
  const loose = path.join(fixture.commonDir, 'refs', 'heads', 'linked');
  try { await symlink(outside, loose, 'file'); }
  catch (error) { if (['EPERM', 'ENOSYS', 'ENOTSUP', 'EOPNOTSUPP', 'EISDIR'].includes(error.code)) { t.skip(`synthetic link metadata unavailable on this filesystem: ${error.code}`); return; } throw error; }
  await assert.rejects(() => assertManagedGitObjectStore(fixture), /link|reparse|ordinary|metadata/i);
});

test('rejects hardlinked packed-ref metadata before Git', async t => {
  const fixture = await storeFixture(t);
  await writeFile(path.join(fixture.commonDir, 'packed-refs-target'), `${'5'.repeat(40)} refs/tags/release\n`);
  try { await link(path.join(fixture.commonDir, 'packed-refs-target'), path.join(fixture.commonDir, 'packed-refs')); }
  catch (error) { if (['EPERM', 'ENOSYS', 'ENOTSUP', 'EOPNOTSUPP', 'EISDIR'].includes(error.code)) { t.skip(`synthetic hardlinked metadata unavailable on this filesystem: ${error.code}`); return; } throw error; }
  await assert.rejects(() => assertManagedGitObjectStore(fixture), /hard.?link|single.?link|metadata/i);
});

test('rejects synthetic special loose-ref metadata before Git', async t => {
  const fixture = await storeFixture(t);
  const refs = path.join(fixture.commonDir, 'refs');
  const special = path.join(refs, 'synthetic-special-ref');
  const fsPromises = createRequire(import.meta.url)('node:fs/promises');
  const originalLstat = fsPromises.lstat;
  const originalReaddir = fsPromises.readdir;
  fsPromises.lstat = async (target, ...args) => {
    if (path.resolve(String(target)) === special) return {
      dev: 1, ino: 2, size: 0, nlink: 1, isSymbolicLink: () => false,
      isDirectory: () => false, isFile: () => false,
    };
    return originalLstat(target, ...args);
  };
  fsPromises.readdir = async (target, ...args) => {
    const names = await originalReaddir(target, ...args);
    return path.resolve(String(target)) === refs ? [...names, 'synthetic-special-ref'] : names;
  };
  syncBuiltinESMExports();
  try { await assert.rejects(() => assertManagedGitObjectStore(fixture), /special refs metadata/i); }
  finally {
    fsPromises.lstat = originalLstat;
    fsPromises.readdir = originalReaddir;
    syncBuiltinESMExports();
  }
});
test('rejects alternates, shallow history, grafts, and shared-index indirection', async t => {
  for (const [relative, bytes] of [
    ['objects/info/alternates', 'outside-object-store\n'],
    ['objects/info/http-alternates', 'https://invalid.example/objects\n'],
    ['shallow', `${'1'.repeat(40)}\n`],
    ['info/grafts', `${'1'.repeat(40)} ${'2'.repeat(40)}\n`],
  ]) {
    const fixture = await storeFixture(t);
    await writeFile(path.join(fixture.commonDir, ...relative.split('/')), bytes);
    await assert.rejects(() => assertManagedGitObjectStore(fixture), /alternate|shallow|graft|unsupported/i);
  }
  const fixture = await storeFixture(t);
  await writeFile(path.join(fixture.gitDir, 'sharedindex.abcdef'), Buffer.from('split index'));
  await assert.rejects(() => assertManagedGitObjectStore(fixture), /shared.?index|split.?index/i);
});

test('rejects hardlinked index metadata and loose objects', async t => {
  const fixture = await storeFixture(t);
  const index = path.join(fixture.gitDir, 'index');
  try { await link(index, path.join(fixture.operation, 'index-hardlink')); }
  catch (error) { if (['EPERM', 'ENOSYS', 'ENOTSUP', 'EOPNOTSUPP', 'EISDIR'].includes(error.code)) { t.skip(`hardlinks unavailable: ${error.code}`); return; } throw error; }
  await assert.rejects(() => assertManagedGitObjectStore(fixture), /hard.?link|single.?link/i);

  const objectFixture = await storeFixture(t);
  const fanout = path.join(objectFixture.commonDir, 'objects', 'ab');
  await mkdir(fanout);
  const objectFile = path.join(fanout, 'c'.repeat(38));
  await writeFile(objectFile, Buffer.from('loose object metadata sentinel'));
  try { await link(objectFile, path.join(objectFixture.operation, 'object-hardlink')); }
  catch (error) { if (['EPERM', 'ENOSYS', 'ENOTSUP', 'EOPNOTSUPP', 'EISDIR'].includes(error.code)) { t.skip(`hardlinks unavailable: ${error.code}`); return; } throw error; }
  await assert.rejects(() => assertManagedGitObjectStore(objectFixture), /hard.?link|single.?link/i);
});

test('rejects object-store symlink or junction descendants without following them', async t => {
  const fixture = await storeFixture(t);
  const outside = path.join(fixture.operation, 'outside');
  await mkdir(outside);
  const junction = path.join(fixture.commonDir, 'objects', 'pack', 'linked');
  try { await symlink(outside, junction, process.platform === 'win32' ? 'junction' : 'dir'); }
  catch (error) { if (['EPERM', 'ENOSYS', 'ENOTSUP', 'EOPNOTSUPP', 'EISDIR'].includes(error.code)) { t.skip(`directory links unavailable: ${error.code}`); return; } throw error; }
  await assert.rejects(() => assertManagedGitObjectStore(fixture), /link|reparse|alias|ordinary/i);
});

test('rejects malformed object fanout names and unsupported metadata entries', async t => {
  const fixture = await storeFixture(t);
  await mkdir(path.join(fixture.commonDir, 'objects', 'zz'));
  await writeFile(path.join(fixture.commonDir, 'objects', 'zz', 'not-an-object'), Buffer.from('opaque'));
  await assert.rejects(() => assertManagedGitObjectStore(fixture), /fanout|object|entry|unsupported/i);
});
test('metadata guard never invokes Git and uses at most one bounded Windows attribute probe', async t => {
  const fixture = await storeFixture(t);
  await mkdir(path.join(fixture.commonDir, 'refs', 'heads'));
  await writeFile(path.join(fixture.commonDir, 'refs', 'heads', 'probe'), `${'6'.repeat(40)}\n`);
  await writeFile(path.join(fixture.commonDir, 'packed-refs'), `${'7'.repeat(40)} refs/tags/probe\n`);
  const childProcess = createRequire(import.meta.url)('node:child_process');
  const original = childProcess.spawnSync;
  const calls = [];
  childProcess.spawnSync = (command, args, options) => {
    calls.push({ command, args, options });
    assert.notEqual(String(command).toLowerCase(), 'git');
    return { error: null, status: 0, signal: null, stdout: Buffer.from('{"reparsePaths":[]}'), stderr: Buffer.alloc(0) };
  };
  syncBuiltinESMExports();
  try { await assertManagedGitObjectStore(fixture); }
  finally { childProcess.spawnSync = original; syncBuiltinESMExports(); }
  if (process.platform === 'win32') {
    assert.equal(calls.length, 1);
    assert.match(String(calls[0].command), /powershell\.exe$/i);
    assert.ok(calls[0].options.input, 'metadata paths are supplied through stdin');
    const probedPaths = JSON.parse(calls[0].options.input.toString('utf8'));
    assert.ok(probedPaths.includes(path.join(fixture.commonDir, 'refs', 'heads', 'probe')));
    assert.ok(probedPaths.includes(path.join(fixture.commonDir, 'packed-refs')));
    assert.equal(calls[0].options.shell, undefined, 'the system PowerShell is launched without a shell');
  } else assert.equal(calls.length, 0);
});
test('fails closed when the bounded Windows attribute probe reports a reparse path', async t => {
  if (process.platform !== 'win32') { t.skip('Windows reparse attribute probe is platform-specific'); return; }
  const fixture = await storeFixture(t);
  const childProcess = createRequire(import.meta.url)('node:child_process');
  const original = childProcess.spawnSync;
  childProcess.spawnSync = () => ({ error: null, status: 0, signal: null, stdout: Buffer.from(JSON.stringify({ reparsePaths: [fixture.commonDir] })), stderr: Buffer.alloc(0) });
  syncBuiltinESMExports();
  try { await assert.rejects(() => assertManagedGitObjectStore(fixture), /reparse point found/i); }
  finally { childProcess.spawnSync = original; syncBuiltinESMExports(); }
});
test('accepts only structurally valid inert TREE cache-tree payloads', () => {
  assert.equal(assertManagedPlainIndex(makeIndex({ extensions: [{ signature: 'TREE', payload: treePayload() }] }), { objectFormat: 'sha1' }), undefined);
  assert.equal(assertManagedPlainIndex(makeIndex({ objectFormat: 'sha256', extensions: [{ signature: 'TREE', payload: treePayload({ treeOid: Buffer.alloc(32, 0x66) }) }] }), { objectFormat: 'sha256' }), undefined);
  const nested = Buffer.concat([treePayload({ subtreeCount: '1' }), Buffer.from('dir\0', 'ascii'), Buffer.from('1 0\n', 'ascii'), Buffer.alloc(20, 0x77)]);
  assert.equal(assertManagedPlainIndex(makeIndex({ extensions: [{ signature: 'TREE', payload: nested }] }), { objectFormat: 'sha1' }), undefined);
  assert.equal(assertManagedPlainIndex(makeIndex({ extensions: [{ signature: 'TREE', payload: treePayload({ entryCount: '-1', treeOid: Buffer.alloc(0) }) }] }), { objectFormat: 'sha1' }), undefined);
  const invalidTrees = [
    Buffer.from('not-nul-terminated'),
    Buffer.from('\0-2 0\n', 'ascii'),
    Buffer.from('\0 1 0\n', 'ascii'),
    treePayload({ subtreeCount: '1' }),
    treePayload({ child: Buffer.from('trailing') }),
    treePayload({ entryCount: '1x' }),
    treePayload({ entryCount: '0' }),
    Buffer.concat([treePayload({ subtreeCount: '1' }), Buffer.from('dir\0', 'ascii'), Buffer.from('2 0\n', 'ascii'), Buffer.alloc(20, 0x77)]),
    Buffer.concat([Buffer.from('\0-1 1\n', 'ascii'), Buffer.from('..\0', 'ascii'), Buffer.from('0 0\n', 'ascii'), Buffer.alloc(20, 0x55)]),
  ];
  for (const payload of invalidTrees) {
    assert.throws(() => assertManagedPlainIndex(makeIndex({ extensions: [{ signature: 'TREE', payload }] }), { objectFormat: 'sha1' }), /TREE|tree|cache|extension/i);
  }
});