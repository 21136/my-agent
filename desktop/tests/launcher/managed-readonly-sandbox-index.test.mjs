import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import test from 'node:test';
import { assertManagedReadonlySandboxIndex } from '../../launcher/managed-readonly-sandbox-index.mjs';

const oidLength = { sha1: 20, sha256: 32 };
const hash = (format, bytes) => createHash(format).update(bytes).digest();
const u32 = (value) => { const out = Buffer.alloc(4); out.writeUInt32BE(value); return out; };
function makeIndex({ format = 'sha1', payload = Buffer.alloc(0), extension = 'REUC', declaredSize = payload.length } = {}) {
  const body = Buffer.concat([Buffer.from('DIRC'), u32(2), u32(0), Buffer.from(extension, 'ascii'), u32(declaredSize), payload]);
  return Buffer.concat([body, hash(format, body)]);
}
function record(path = 'src/file.txt', modes = ['100644', '0', '100755'], oids, format = 'sha1') {
  const encodedPath = Buffer.from(path, 'utf8');
  const ids = oids ?? modes.map((mode, i) => mode === '0' ? Buffer.alloc(0) : Buffer.alloc(oidLength[format], 0x31 + i));
  return Buffer.concat([encodedPath, Buffer.from([0]), ...modes.flatMap((mode) => [Buffer.from(mode, 'ascii'), Buffer.from([0])]), ...modes.flatMap((mode, i) => mode === '0' ? [] : [ids[i]])]);
}

test('accepts valid REUC in SHA-1 and SHA-256 without mutating input', () => {
  for (const format of ['sha1', 'sha256']) {
    const payload = Buffer.concat([record('src/a', ['100644', '0', '100755'], undefined, format), record('src/b', ['0', '100644', '0'], undefined, format)]);
    const bytes = makeIndex({ format, payload });
    const before = Buffer.from(bytes);
    assert.equal(assertManagedReadonlySandboxIndex(bytes, { objectFormat: format }), undefined);
    assert.deepEqual(bytes, before);
  }
});

test('accepts strict plain indexes and rejects every non-REUC extension', () => {
  assert.equal(assertManagedReadonlySandboxIndex(Buffer.concat([Buffer.from('DIRC'), u32(2), u32(0), hash('sha1', Buffer.concat([Buffer.from('DIRC'), u32(2), u32(0)]))]), { objectFormat: 'sha1' }), undefined);
  for (const ext of ['TREE', 'link', 'FSMN', 'UNTR', 'XTRA']) assert.throws(() => assertManagedReadonlySandboxIndex(makeIndex({ extension: ext }), { objectFormat: 'sha1' }), /extension|TREE/i);
});

test('rejects malformed mode triples, truncated OIDs, and all-zero OIDs', () => {
  for (const modes of [['100644', '100644'], ['100644', '0', '100644x'], ['0100644', '0', '0'], ['120000', '0', '0']]) {
    assert.throws(() => assertManagedReadonlySandboxIndex(makeIndex({ payload: record('a', modes) }), { objectFormat: 'sha1' }), /REUC|mode|truncated/i);
  }
  const truncated = record('a').subarray(0, -1);
  assert.throws(() => assertManagedReadonlySandboxIndex(makeIndex({ payload: truncated }), { objectFormat: 'sha1' }), /REUC|truncated/i);
  const zeroOid = record('a', ['100644', '0', '0'], [Buffer.alloc(20)]);
  assert.throws(() => assertManagedReadonlySandboxIndex(makeIndex({ payload: zeroOid }), { objectFormat: 'sha1' }), /REUC|OID|object/i);
});

test('rejects unsafe, aliased, invalid UTF-8, and duplicate paths', () => {
  for (const path of ['/root', '../escape', 'a/../b', 'a\\b', 'CON.txt', 'dir/name. ', 'a:b', 'a\0b']) {
    assert.throws(() => assertManagedReadonlySandboxIndex(makeIndex({ payload: record(path) }), { objectFormat: 'sha1' }), /REUC|path/i, path);
  }
  const invalidUtf8 = Buffer.concat([Buffer.from([0xff, 0]), record('z')]);
  assert.throws(() => assertManagedReadonlySandboxIndex(makeIndex({ payload: invalidUtf8 }), { objectFormat: 'sha1' }), /REUC|UTF-8|path/i);
  assert.throws(() => assertManagedReadonlySandboxIndex(makeIndex({ payload: Buffer.concat([record('A'), record('a')]) }), { objectFormat: 'sha1' }), /REUC|duplicate|alias/i);
});

test('rejects trailing bytes, framing errors, bad checksums, and invalid object formats', () => {
  assert.throws(() => assertManagedReadonlySandboxIndex(makeIndex({ payload: Buffer.concat([record('a'), Buffer.from([0x41])]) }), { objectFormat: 'sha1' }), /REUC|trailing|mode/i);
  const malformed = makeIndex({ payload: record('a'), declaredSize: 0xffff });
  assert.throws(() => assertManagedReadonlySandboxIndex(malformed, { objectFormat: 'sha1' }), /extension|truncated/i);
  const badChecksum = makeIndex({ payload: record('a') }); badChecksum[badChecksum.length - 1] ^= 1;
  assert.throws(() => assertManagedReadonlySandboxIndex(badChecksum, { objectFormat: 'sha1' }), /checksum/i);
  assert.throws(() => assertManagedReadonlySandboxIndex(makeIndex(), { objectFormat: 'md5' }), /objectFormat/i);
});




function makeStagedIndex() {
  const fixed = Buffer.alloc(40);
  fixed.writeUInt32BE(0o100644, 24);
  const name = Buffer.from('conflict.txt');
  const flags = Buffer.alloc(2);
  flags.writeUInt16BE(0x1000 | name.length);
  const entry = Buffer.concat([fixed, Buffer.alloc(20, 0x44), flags, name, Buffer.from([0])]);
  const padding = Buffer.alloc((8 - (entry.length % 8)) % 8);
  const body = Buffer.concat([Buffer.from('DIRC'), u32(2), u32(1), entry, padding, Buffer.from('REUC'), u32(0)]);
  return Buffer.concat([body, hash('sha1', body)]);
}

test('does not admit real conflict stages even when REUC is valid', () => {
  assert.throws(() => assertManagedReadonlySandboxIndex(makeStagedIndex(), { objectFormat: 'sha1' }), /stage/i);
});

