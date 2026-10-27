import { createHash } from 'node:crypto';
import { assertManagedPlainIndex } from './managed-clean-git-metadata.mjs';

const HASH_BYTES = Object.freeze({ sha1: 20, sha256: 32 });
const MAX_RECORDS = 1_000_000;
const MAX_PATH_BYTES = 64 * 1024;
const fail = (message) => { throw new Error(`Invalid managed read-only sandbox index: ${message}`); };
const digest = (bytes, format) => createHash(format).update(bytes).digest();

function validateReuc(payload, objectFormat) {
  const oidLength = HASH_BYTES[objectFormat];
  let offset = 0;
  let records = 0;
  const seen = new Set();
  while (offset < payload.length) {
    if (++records > MAX_RECORDS) fail('REUC record count exceeds the bound');
    const pathEnd = payload.indexOf(0, offset);
    if (pathEnd < offset) fail('REUC path is missing its NUL terminator');
    const pathBytes = payload.subarray(offset, pathEnd);
    if (pathBytes.length === 0 || pathBytes.length > MAX_PATH_BYTES) fail('REUC path length is invalid');
    let name;
    try { name = new TextDecoder('utf-8', { fatal: true }).decode(pathBytes); }
    catch { fail('REUC path is not valid UTF-8'); }
    if (name.startsWith('/') || name.includes('\\') || /[\x00-\x1f\x7f]/.test(name)) fail('REUC path is not a safe relative path');
    const components = name.split('/');
    if (components.some((part) => !part || part === '.' || part === '..' || /[<>:"|?*]/.test(part) || /[. ]$/.test(part) || /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i.test(part))) fail('REUC path contains traversal or a Windows alias');
    const key = name.normalize('NFC').toLocaleLowerCase('en-US');
    if (seen.has(key)) fail('REUC contains duplicate or aliased paths');
    seen.add(key);
    offset = pathEnd + 1;
    const modes = [];
    for (let stage = 0; stage < 3; stage++) {
      const modeEnd = payload.indexOf(0, offset);
      if (modeEnd < offset) fail('REUC mode is missing its NUL terminator');
      const modeBytes = payload.subarray(offset, modeEnd);
      const modeText = modeBytes.toString('ascii');
      if ([...modeBytes].some((byte) => byte > 0x7f) || !/^(?:0|100644|100755)$/.test(modeText)) fail('REUC mode is unsupported or malformed');
      modes.push(modeText);
      offset = modeEnd + 1;
    }
    for (const mode of modes) {
      if (mode !== '0') {
        if (offset + oidLength > payload.length) fail('REUC object ID is truncated');
        const oid = payload.subarray(offset, offset + oidLength);
        if (oid.every((byte) => byte === 0)) fail('REUC object ID is all zero');
        offset += oidLength;
      }
    }
  }
}

/** Strict plain indexes remain owned by assertManagedPlainIndex; this narrowly permits a validated REUC block in the read-only sandbox lane. */
export function assertManagedReadonlySandboxIndex(bytes, { objectFormat } = {}) {
  try {
    assertManagedPlainIndex(bytes, { objectFormat });
    return;
  } catch (error) {
    if (!(error instanceof Error) || error.message !== 'Invalid plain Git index: unsupported extension REUC') throw error;
  }
  if (!Buffer.isBuffer(bytes) || !Object.hasOwn(HASH_BYTES, objectFormat)) fail('invalid Buffer or object format');
  const digestLength = HASH_BYTES[objectFormat];
  const contentEnd = bytes.length - digestLength;
  let offset = 12;
  const entryCount = bytes.readUInt32BE(8);
  for (let entry = 0; entry < entryCount; entry++) {
    const entryStart = offset;
    const nul = bytes.indexOf(0, entryStart + 42 + digestLength);
    if (nul < 0 || nul >= contentEnd) fail('entry framing is invalid');
    offset = nul + 1;
    while ((offset - entryStart) % 8 !== 0) offset++;
  }
  const extensions = [];
  while (offset < contentEnd) {
    if (contentEnd - offset < 8) fail('extension framing is truncated');
    const start = offset;
    const signature = bytes.toString('ascii', offset, offset + 4);
    const size = bytes.readUInt32BE(offset + 4);
    offset += 8;
    if (size > contentEnd - offset) fail('extension payload is truncated');
    extensions.push({ start, end: offset + size, signature, payload: bytes.subarray(offset, offset + size) });
    offset += size;
  }
  const reuc = extensions.filter((extension) => extension.signature === 'REUC');
  if (reuc.length !== 1) fail('expected exactly one REUC extension');
  validateReuc(reuc[0].payload, objectFormat);
  const body = Buffer.concat([bytes.subarray(0, reuc[0].start), bytes.subarray(reuc[0].end, contentEnd)]);
  const normalized = Buffer.concat([body, digest(body, objectFormat)]);
  assertManagedPlainIndex(normalized, { objectFormat });
}

