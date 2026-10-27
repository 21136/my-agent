import {spawn as nodeSpawn} from 'node:child_process';
import {randomBytes, randomUUID} from 'node:crypto';
import {closeSync, existsSync, fsyncSync, openSync, readFileSync, unlinkSync, writeFileSync} from 'node:fs';
import path from 'node:path';
import {assertRecord, canonical, atomicJson, readJson, realDirectory, regularFile, UUID} from './paths.mjs';
import {cleanHostEnvironment, readFixtureBinding} from './binding.mjs';
import {createAdoptionJournal} from './journal.mjs';

const MAX_STREAM = 64 * 1024;
const BOOTSTRAP_FLAG = '--fixed-host-bootstrap-stdin';

function stateFile(root) { return path.join(root, 'instance.json'); }
function lockFile(root) { return path.join(root, 'instance.lock'); }
function persistState(file, state, patch) {
  const next = {...state, ...patch, updated_at: new Date().toISOString()};
  atomicJson(file, next);
  Object.assign(state, next);
}
function validatePrevious(file, installationId, releaseId, digest) {
  if (!existsSync(file)) return null;
  const prior = readJson(file, 65536);
  const allowed = ['format','installation_id','release_id','binding_digest','instance_id','state','updated_at', ...['exit_code','exit_signal','reason'].filter(key => Object.hasOwn(prior, key))];
  assertRecord(prior, allowed, 'previous Launcher instance state');
  if (prior.format !== 1 || !UUID.test(prior.instance_id) || typeof prior.updated_at !== 'string'
    || prior.installation_id !== installationId || prior.release_id !== releaseId || prior.binding_digest !== digest
    || !['stopped', 'maintenance_required'].includes(prior.state)) {
    throw new Error('previous Launcher instance state is unknown or not stopped; maintenance is required');
  }
  if (prior.state !== 'stopped') throw new Error('previous Launcher instance requires maintenance; automatic restart is blocked');
  return prior;
}
function acquireLock(file, instanceId) {
  let fd;
  try {
    fd = openSync(file, 'wx', 0o600);
    const record = {format: 1, owner: instanceId, pid: process.pid, created_at: new Date().toISOString()};
    writeFileSync(fd, canonical(record) + '\n', 'utf8');
    fsyncSync(fd);
    return {fd, record};
  } catch (error) {
    if (fd !== undefined) closeSync(fd);
    throw new Error('Launcher instance lock exists or cannot be acquired; it will not be stolen', {cause: error});
  }
}
function closeOwnedHandle(lock) {
  if (lock?.fd !== undefined) { try { closeSync(lock.fd); } catch {} lock.fd = undefined; }
}
function releaseOwnedLock(file, lock) {
  if (!lock || lock.fd === undefined || !existsSync(file)) return;
  try {
    const current = JSON.parse(readFileSync(file, 'utf8'));
    if (current?.owner === lock.record.owner) {
      closeOwnedHandle(lock);
      unlinkSync(file);
    }
  } catch { /* Ambiguous ownership means preserve the lock. */ }
}

export async function launchFixedHost({bindingPath, spawn = nodeSpawn, timeoutMs = 120000} = {}) {
  if (typeof bindingPath !== 'string' || !Number.isFinite(timeoutMs) || timeoutMs < 1 || timeoutMs > 120000 || typeof spawn !== 'function') {
    throw new Error('invalid fixed Host launch options');
  }
  const bindingFile = regularFile(bindingPath, 'fixture binding file');
  const {binding, digest, manifest} = readFixtureBinding(bindingFile);
  const stateRoot = realDirectory(binding.state_root, 'Launcher state root');
  const statePath = stateFile(stateRoot), lockPath = lockFile(stateRoot);
  const identity = {installation_id: binding.installation_id, release_id: binding.release.release_id, binding_digest: digest};
  const runtime = manifest.runtimes.electron;
  const executable = regularFile(path.join(runtime.root, ...runtime.entry.split('/')), 'fixed Electron executable');
  const app = realDirectory(path.join(binding.release.root, 'app', 'desktop'), 'fixed desktop app');
  // Validate durable adoption identity before acquiring a lock or allowing a child process.
  const adoption = createAdoptionJournal(stateRoot, identity);
  adoption.assertIdle();
  validatePrevious(statePath, identity.installation_id, identity.release_id, digest);

  const instanceId = randomUUID(), lock = acquireLock(lockPath, instanceId);
  const state = {format: 1, ...identity, instance_id: instanceId, state: 'starting', updated_at: new Date().toISOString()};
  try { persistState(statePath, state); }
  catch (error) {
    closeOwnedHandle(lock);
    try { persistState(statePath, state, {state: 'maintenance_required', reason: 'could not persist starting state'}); } catch {}
    throw error;
  }
  const args = [app, BOOTSTRAP_FLAG];
  const env = cleanHostEnvironment(process.env);
  let child;
  try { child = spawn(executable, args, {cwd: app, env, shell: false, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe']}); }
  catch (error) {
    closeOwnedHandle(lock);
    try { persistState(statePath, state, {state: 'maintenance_required', reason: 'spawn threw before lifecycle observation'}); } catch {}
    throw new Error('fixed Host spawn failed; lock retained for maintenance', {cause: error});
  }
  if (!child || !child.stdin || !child.stdout || !child.stderr || typeof child.kill !== 'function' || typeof child.on !== 'function') {
    closeOwnedHandle(lock);
    try { persistState(statePath, state, {state: 'maintenance_required', reason: 'spawn returned an invalid child handle'}); } catch {}
    throw new Error('spawn returned an invalid owned child handle; lock retained for maintenance');
  }
  const nonce = randomBytes(32).toString('hex');
  const frame = {protocol: 'my-agent-fixed-host-v1', binding_path: bindingFile, binding_digest: digest,
    release_id: identity.release_id, instance_id: instanceId, nonce};

  return await new Promise((resolve, reject) => {
    let ready = false, failed = false, settled = false, closeSeen = false, killIssued = false;
    let stdout = Buffer.alloc(0), stdoutBytes = 0, stderrBytes = 0, prefixChecked = false;
    const settleFailure = (error, kill) => {
      if (failed || closeSeen) return;
      failed = true;
      clearTimeout(timer);
      try { persistState(statePath, state, {state: 'maintenance_required', reason: 'lifecycle_failure'}); } catch {}
      closeOwnedHandle(lock);
      if (kill && !killIssued) {
        killIssued = true;
        try { child.kill(); } catch { /* Lock file remains when exit is uncertain. */ }
      }
      if (!settled) { settled = true; reject(error); }
    };
    const timer = setTimeout(() => settleFailure(new Error('fixed Host readiness timeout'), true), timeoutMs);
    child.on('error', error => settleFailure(new Error('fixed Host process error'), true));
    child.stdin.on('error', error => settleFailure(new Error('fixed Host bootstrap stdin error'), true));
    child.on('close', (code, signal) => {
      clearTimeout(timer);
      if (failed) { closeSeen = true; return; }
      if (ready && code === 0 && signal === null) {
        try {
          persistState(statePath, state, {state: 'stopped', exit_code: 0});
          closeSeen = true;
          releaseOwnedLock(lockPath, lock);
          if (!settled) { settled = true; resolve({state: 'stopped', instance_id: instanceId}); }
        } catch (error) {
          settleFailure(new Error('could not persist normal Host shutdown: ' + error.message), false);
          closeSeen = true;
        }
        return;
      }
      settleFailure(new Error(ready ? 'fixed Host exited abnormally' : 'fixed Host closed before readiness'), false);
      closeSeen = true;
    });
    child.stdout.on('data', chunk => {
      const bytes = Buffer.from(chunk);
      stdoutBytes += bytes.length;
      if (stdoutBytes > MAX_STREAM) { settleFailure(new Error('fixed Host bounded stdout exceeded limit'), true); return; }
      if (failed || bytes.length === 0) return;
      if (ready) { settleFailure(new Error('fixed Host readiness stream must contain exactly one frame'), true); return; }
      stdout = Buffer.concat([stdout, bytes]);
      if (!prefixChecked) {
        // Windows Electron may emit one startup CRLF, including across two chunks.
        if (stdout.length === 1 && stdout[0] === 0x0d) return;
        if (stdout[0] === 0x0d && stdout[1] === 0x0a) stdout = stdout.subarray(2);
        prefixChecked = true;
      }
      if (stdout.length === 0) return; // The prelude is not a readiness frame.
      if (stdout[0] !== 0x7b) { settleFailure(new Error('fixed Host readiness verification failed'), true); return; }
      const newline = stdout.indexOf(0x0a);
      if (newline < 0) return;
      if (newline !== stdout.length - 1) { settleFailure(new Error('fixed Host readiness stream must contain exactly one frame'), true); return; }
      try {
        const response = JSON.parse(stdout.subarray(0, newline).toString('utf8'));
        if (!response || typeof response !== 'object' || Array.isArray(response)
          || Object.keys(response).sort().join('\0') !== ['type','installation_id','release_id','instance_id','nonce'].sort().join('\0')
          || response.type !== 'fixed-host.ready' || response.installation_id !== identity.installation_id
          || response.release_id !== identity.release_id || response.instance_id !== instanceId || response.nonce !== nonce) {
          throw new Error('fixed Host readiness identity mismatch');
        }
        ready = true;
        clearTimeout(timer);
        persistState(statePath, state, {state: 'ready'});
      } catch { settleFailure(new Error('fixed Host readiness verification failed'), true); }
    });
    child.stderr.on('data', chunk => {
      stderrBytes += Buffer.byteLength(chunk);
      if (stderrBytes > MAX_STREAM) settleFailure(new Error('fixed Host bounded stderr exceeded limit'), true);
    });
    try { child.stdin.end(canonical(frame) + '\n', 'utf8'); }
    catch { settleFailure(new Error('fixed Host bootstrap delivery failed'), true); }
  });
}


