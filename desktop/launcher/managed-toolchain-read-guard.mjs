import { createHash, randomBytes } from 'node:crypto';
import { existsSync, lstatSync, realpathSync } from 'node:fs';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { canonical, digest, exact, readStableBytes, safeDirectory, safeRelative } from './managed-build-records.mjs';

const HASH = /^[a-f0-9]{64}$/;
const UUIDLESS_MODE = 'managed_toolchain_read_guard_v1';
const REQUEST_MODE = 'managed_toolchain_read_guard_request_v1';
const READY_MODE = 'managed_toolchain_read_guard_ready_v1';
const RELEASE_MODE = 'managed_toolchain_read_guard_release_v1';
const ACK_MODE = 'managed_toolchain_read_guard_release_ack_v1';
const PROBE_MODE = 'managed_toolchain_read_guard_probe_v1';
const MAX_FRAME_BYTES = 16 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 64 * 1024;
const MAX_FILES = 20_000;
const MAX_FILE_BYTES = 448 * 1024 * 1024;
const MAX_NODE_BYTES = 256 * 1024 * 1024;
const MAX_GUARD_MS = 5 * 60 * 1000;
const EXIT_WAIT_MS = 15_000;
const MODULE_DIR = path.dirname(fileURLToPath(import.meta.url));
const HELPER_PATH = path.resolve(MODULE_DIR, '../scripts/managed-toolchain-read-guard.py');
const CONTROL_SANDBOX = path.resolve(MODULE_DIR, '../..');
const PROJECT_ROOT = path.resolve(CONTROL_SANDBOX, '../..');
const ERROR_PREFIX = 'managed_toolchain_read_guard_invalid: ';

function guardError(message) { return new TypeError(ERROR_PREFIX + message); }
function fail(message) { throw guardError(message); }
function comparePath(a, b) { return a.localeCompare(b); }
function cleanEnvironment({ includePath = false } = {}) {
  const env = {};
  for (const key of ['SystemRoot', 'WINDIR', 'TEMP', 'TMP']) if (process.env[key]) env[key] = process.env[key];
  if (includePath && process.env.PATH) env.PATH = process.env.PATH;
  return env;
}
function canonicalRegularFile(file) {
  if (typeof file !== 'string' || !path.isAbsolute(file) || path.resolve(file) !== file) fail('Python/helper path is not canonical absolute');
  safeDirectory(path.dirname(file));
  const stat = lstatSync(file);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1 || realpathSync.native(file) !== file || (process.platform === 'win32' && (stat.attributes & 0x400))) fail('Python/helper file is linked, nonregular, or aliased');
  return file;
}
function selectPythonExecutable() {
  const virtualEnvPython = path.join(PROJECT_ROOT, '.venv', 'Scripts', 'python.exe');
  if (existsSync(virtualEnvPython)) return canonicalRegularFile(virtualEnvPython);

  // Resolve one explicit absolute fallback with the OS-native where.exe and pin that path for this call.
  const systemRoot = process.env.SystemRoot || process.env.WINDIR;
  if (typeof systemRoot !== 'string' || !path.isAbsolute(systemRoot) || path.resolve(systemRoot) !== systemRoot) fail('pinned Python runtime unavailable');
  const whereExe = canonicalRegularFile(path.join(systemRoot, 'System32', 'where.exe'));
  const resolved = spawnSync(whereExe, ['python.exe'], {
    cwd: CONTROL_SANDBOX, env: cleanEnvironment({ includePath: true }), encoding: 'buffer',
    windowsHide: true, shell: false, timeout: 10_000, maxBuffer: 16 * 1024,
  });
  if (resolved.error || resolved.status !== 0) fail('pinned Python runtime unavailable');
  const firstPath = resolved.stdout.toString('utf8').split(/\r?\n/).find(Boolean);
  return canonicalRegularFile(firstPath);
}
function expectedDirectories(toolchainRoot, files) {
  const result = new Map();
  const add = value => result.set(path.resolve(value).toLowerCase(), path.resolve(value));
  let current = path.parse(toolchainRoot).root;
  add(current);
  for (const part of toolchainRoot.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    add(current);
  }
  for (const item of [...files, { path: 'package.json' }, { path: 'package-lock.json' }]) {
    const parts = item.path.split('/');
    parts.pop();
    current = toolchainRoot;
    for (const part of parts) { current = path.join(current, part); add(current); }
  }
  let nodeDir = path.dirname(process.execPath);
  for (;;) {
    add(nodeDir);
    const parent = path.dirname(nodeDir);
    if (parent === nodeDir) break;
    nodeDir = parent;
  }
  if (result.size > MAX_FILES) fail('toolchain ancestor directory count exceeds handle bound');
  return result.size;
}
function validateToolchain(toolchain) {
  const keys = ['format', 'mode', 'root', 'packageJsonSha256', 'packageLockSha256', 'node', 'files', 'toolchainDigest'];
  if (!exact(toolchain, keys) || toolchain.format !== 1 || toolchain.mode !== 'managed_postmerge_toolchain_v1') fail('toolchain schema invalid');
  if (typeof toolchain.root !== 'string' || !path.isAbsolute(toolchain.root) || path.resolve(toolchain.root) !== toolchain.root
      || path.basename(toolchain.root) !== 'desktop' || path.basename(path.dirname(toolchain.root)) !== 'sandbox'
      || path.basename(path.dirname(path.dirname(toolchain.root))) !== '.worktrees') fail('toolchain root is not canonical sandbox desktop');
  safeDirectory(toolchain.root);
  if (!HASH.test(toolchain.packageJsonSha256 || '') || !HASH.test(toolchain.packageLockSha256 || '')) fail('toolchain package hashes invalid');
  if (!exact(toolchain.node, ['executable', 'sha256', 'version']) || typeof toolchain.node.executable !== 'string'
      || !path.isAbsolute(toolchain.node.executable) || path.resolve(toolchain.node.executable) !== toolchain.node.executable
      || !HASH.test(toolchain.node.sha256 || '') || typeof toolchain.node.version !== 'string'
      || !/^v\d+\.\d+\.\d+(?:[-+][\w.-]+)?$/.test(toolchain.node.version)) fail('toolchain Node identity invalid');
  if (toolchain.node.executable !== process.execPath) fail('toolchain Node executable differs from the running pinned Node');
  const nodeExecutable = canonicalRegularFile(process.execPath);
  const nodeBytes = readStableBytes(nodeExecutable, MAX_NODE_BYTES);
  if (createHash('sha256').update(nodeBytes).digest('hex') !== toolchain.node.sha256) fail('running pinned Node executable hash mismatch');
  if (!Array.isArray(toolchain.files) || toolchain.files.length < 1 || toolchain.files.length > MAX_FILES - 2) fail('toolchain inventory count invalid');
  let totalBytes = 0;
  const seen = new Set();
  let priorPath = null;
  for (const entry of toolchain.files) {
    if (!exact(entry, ['path', 'mode', 'sha256', 'byteLength']) || !safeRelative(entry.path)
        || !entry.path.startsWith('node_modules/') || !['100644', '100755'].includes(entry.mode)
        || !HASH.test(entry.sha256 || '') || !Number.isSafeInteger(entry.byteLength) || entry.byteLength < 0) fail('toolchain file record invalid');
    if (priorPath !== null && comparePath(priorPath, entry.path) >= 0) fail('toolchain file records are not strictly ordered');
    priorPath = entry.path;
    const key = entry.path.toLowerCase();
    if (seen.has(key)) fail('toolchain path alias');
    seen.add(key);
    totalBytes += entry.byteLength;
    if (!Number.isSafeInteger(totalBytes) || totalBytes > MAX_FILE_BYTES) fail('toolchain inventory exceeds byte bound');
  }
  const body = { ...toolchain };
  delete body.toolchainDigest;
  if (!HASH.test(toolchain.toolchainDigest || '') || digest(body) !== toolchain.toolchainDigest) fail('toolchain digest invalid');
  return { fileCount: toolchain.files.length + 2, runtimeFileCount: 1, directoryCount: expectedDirectories(toolchain.root, toolchain.files) };
}
function parseCanonicalFrame(bytes, label) {
  let text;
  try { text = new TextDecoder('utf-8', { fatal: true }).decode(bytes); } catch { fail(label + ' frame encoding invalid'); }
  let value;
  try { value = JSON.parse(text); } catch { fail(label + ' frame JSON invalid'); }
  if (canonical(value) + '\n' !== text) fail(label + ' frame is noncanonical');
  return value;
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  promise.catch(() => {});
  return { promise, resolve, reject };
}
function waitForClose(child, closePromise, milliseconds) {
  if (child.exitCode !== null || child.signalCode !== null) return closePromise;
  return Promise.race([
    closePromise,
    new Promise(resolve => setTimeout(() => resolve(null), milliseconds)),
  ]);
}

/** Acquire native Windows share locks for every file and ancestor in the pinned toolchain inventory. */
async function acquireManagedToolchainReadGuardInternal({ toolchain }) {
  if (process.platform !== 'win32') fail('native toolchain read guard is Windows-only');
  const { fileCount, runtimeFileCount, directoryCount } = validateToolchain(toolchain);
  const helper = canonicalRegularFile(HELPER_PATH);
  const python = selectPythonExecutable();
  const probe = spawnSync(python, ['-I', '-B', '-S', helper, '--probe'], {
    cwd: toolchain.root, env: cleanEnvironment(), encoding: 'buffer', windowsHide: true,
    shell: false, timeout: 15_000, maxBuffer: MAX_RESPONSE_BYTES,
  });
  if (probe.error || probe.status !== 0 || probe.stdout.length > MAX_RESPONSE_BYTES) fail('pinned Python runtime probe failed');
  const probeValue = parseCanonicalFrame(probe.stdout, 'Python probe');
  const probeKeys = ['format', 'mode', 'python', 'imports'];
  if (!exact(probeValue, probeKeys) || probeValue.format !== 1 || probeValue.mode !== PROBE_MODE || probeValue.imports !== true
      || typeof probeValue.python !== 'string' || path.resolve(probeValue.python) !== python) fail('pinned Python runtime or stdlib imports do not match');

  const nonce = randomBytes(32).toString('hex');
  const request = { format: 1, mode: REQUEST_MODE, nonce, toolchain };
  const requestBytes = Buffer.from(canonical(request) + '\n');
  if (requestBytes.length > MAX_FRAME_BYTES) fail('toolchain guard request exceeds frame bound');
  const child = spawn(python, ['-I', '-B', '-S', helper, '--guard'], {
    cwd: toolchain.root, env: cleanEnvironment(), windowsHide: true, shell: false, stdio: ['pipe', 'pipe', 'ignore'],
  });
  const ready = deferred();
  const ack = deferred();
  let stdout = Buffer.alloc(0);
  let readySeen = false;
  let releaseRequested = false;
  let ackSeen = false;
  let failure = null;
  let closeResult = null;
  const closePromise = new Promise(resolve => child.once('close', (code, signal) => { closeResult = { code, signal }; resolve(closeResult); }));
  const terminate = error => {
    if (!failure) failure = guardError('native helper process failed');
    child.kill();
    ready.reject(failure);
    ack.reject(failure);
  };
  const timer = setTimeout(() => terminate(new Error('managed toolchain read guard exceeded its five-minute bound')), MAX_GUARD_MS);
  timer.unref?.();
  child.once('error', error => terminate(error));
  child.stdin.on('error', error => terminate(error));
  child.stdout.on('data', chunk => {
    stdout = Buffer.concat([stdout, chunk]);
    if (stdout.length > MAX_RESPONSE_BYTES) return terminate(new Error('managed toolchain read guard response exceeded its bound'));
    for (;;) {
      const newline = stdout.indexOf(0x0a);
      if (newline < 0) break;
      const frame = stdout.subarray(0, newline + 1);
      stdout = stdout.subarray(newline + 1);
      try {
        const value = parseCanonicalFrame(frame, 'guard response');
        if (!readySeen) {
          const keys = ['format', 'mode', 'nonce', 'toolchainDigest', 'fileCount', 'runtimeFileCount', 'directoryCount'];
          if (!exact(value, keys) || value.format !== 1 || value.mode !== READY_MODE || value.nonce !== nonce
              || value.toolchainDigest !== toolchain.toolchainDigest || value.fileCount !== fileCount
              || value.runtimeFileCount !== runtimeFileCount || value.directoryCount !== directoryCount) fail('guard ready receipt binding mismatch');
          readySeen = true;
          ready.resolve(value);
        } else if (releaseRequested && !ackSeen) {
          const keys = ['format', 'mode', 'nonce', 'toolchainDigest'];
          if (!exact(value, keys) || value.format !== 1 || value.mode !== ACK_MODE || value.nonce !== nonce
              || value.toolchainDigest !== toolchain.toolchainDigest) fail('guard release acknowledgement mismatch');
          ackSeen = true;
          ack.resolve(value);
        } else fail('unexpected guard protocol frame');
      } catch (error) { terminate(error); return; }
    }
  });
  child.once('exit', (code, signal) => {
    if (!readySeen) terminate(new Error('native toolchain read guard exited before ready'));
    else if (!releaseRequested) terminate(new Error('native toolchain read guard exited while held'));
    else if (code !== 0 || signal) terminate(new Error('native toolchain read guard release failed'));
  });

  try {
    child.stdin.write(requestBytes);
    await ready.promise;
    if (failure || child.exitCode !== null) throw failure || new Error('native toolchain read guard exited before readiness');
  } catch {
    terminate(new Error('native toolchain read guard failed before ready'));
    await waitForClose(child, closePromise, EXIT_WAIT_MS);
    clearTimeout(timer);
    throw new Error('managed toolchain read guard failed before ready');
  }

  let releasePromise;
  const release = () => {
    if (releasePromise) return releasePromise;
    releasePromise = (async () => {
      try {
        if (failure || child.exitCode !== null || child.signalCode !== null) fail('guard was lost before release');
        releaseRequested = true;
        const frame = Buffer.from(canonical({ format: 1, mode: RELEASE_MODE, nonce, toolchainDigest: toolchain.toolchainDigest }) + '\n');
        if (frame.length > 4096) fail('release frame exceeded its bound');
        child.stdin.end(frame);
        await ack.promise;
        const closed = await waitForClose(child, closePromise, EXIT_WAIT_MS);
        if (!closed || closed.code !== 0 || closed.signal || failure || stdout.length) fail('guard did not close cleanly');
      } catch {
        terminate(guardError('release protocol failed'));
        await waitForClose(child, closePromise, EXIT_WAIT_MS);
        throw guardError('release failed');
      } finally { clearTimeout(timer); }
    })();
    return releasePromise;
  };
  return {
    release,
    receipt: { mode: UUIDLESS_MODE, toolchainDigest: toolchain.toolchainDigest, fileCount, runtimeFileCount, directoryCount },
  };
}

/** Acquire the native guard; all outward failures use the stable CLI classifier prefix. */
export async function acquireManagedToolchainReadGuard(input) {
  try {
    return await acquireManagedToolchainReadGuardInternal(input);
  } catch (error) {
    if (error instanceof Error && error.message.startsWith(ERROR_PREFIX)) throw error;
    throw guardError('acquisition failed');
  }
}
