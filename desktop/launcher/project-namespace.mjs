import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { lstat, mkdir, open, readdir, realpath } from 'node:fs/promises';
import path from 'node:path';

const FORMAT = 1;
const MODE = 'managed_project_local_v1';
const STAGE = 'prepared_empty';
const DATA_POLICY = 'empty_no_migration';
const SECURITY_POLICY = 'logical_separation_only';
const CREDENTIALS_POLICY = 'forbidden';
const MARKER = 'state/managed-namespace.json';
const MAX_MARKER_BYTES = 16 * 1024;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const RECORD_KEYS = ['format', 'mode', 'stage', 'data_policy', 'security_policy', 'credentials_policy', 'installation_id', 'root', 'source_roots', 'paths'];

function absolute(value, label) {
  if (typeof value !== 'string' || !path.isAbsolute(value)) throw new TypeError(`${label} must be an absolute path`);
  // Normalize traversal components lexically; realpath below rejects aliases and casing mismatches.
  return path.resolve(value);
}
function uuid(value) {
  if (typeof value !== 'string' || !UUID_RE.test(value)) throw new TypeError('installationId must be a canonical UUID');
  return value.toLowerCase();
}
function normalized(value) { return path.resolve(value).replace(/[\\/]+$/, '').toLowerCase(); }
function canonicalPath(value) { return path.resolve(value).replace(/^\\\\\?\\/, '').replace(/[\\/]+$/, ''); }
function within(parent, child) {
  const rel = path.relative(parent, child);
  return rel === '' || (rel !== '..' && !rel.startsWith(`..${path.sep}`) && !path.isAbsolute(rel));
}
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}
function pathsFor(root) {
  return Object.freeze({ launcher: path.join(root, 'launcher'), state: path.join(root, 'state'), releases: path.join(root, 'releases'), host_state: path.join(root, 'host/state'), host_profile: path.join(root, 'host/profile'), host_workspace: path.join(root, 'host/workspace'), data: path.join(root, 'data/store') });
}
export function projectNamespacePath(sandboxSourceRoot, installationId) {
  return path.join(absolute(sandboxSourceRoot, 'sandboxSourceRoot'), '.managed-runtime', uuid(installationId));
}
export function projectNamespaceDigest(record) { return createHash('sha256').update(canonical(record), 'utf8').digest('hex'); }

const PS_VOLUME = String.raw`$ErrorActionPreference='Stop';if([IO.Path]::GetFullPath([Environment]::GetFolderPath('Windows')).TrimEnd('\') -ine 'C:\Windows' -or [IO.Path]::GetFullPath([Environment]::SystemDirectory).TrimEnd('\') -ine 'C:\Windows\System32'){throw 'Unsupported native Windows system paths'};$i=[Console]::In.ReadToEnd()|ConvertFrom-Json;$p=[IO.Path]::GetFullPath([string]$i.path);$d=[IO.Path]::GetPathRoot($p).TrimEnd('\').ToUpperInvariant();if($d -notmatch '^[A-Z]:$'){throw 'Not a local drive'};$v=Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='"+$d+"'");if($null -eq $v){throw 'Volume unavailable'};[ordered]@{filesystem=[string]$v.FileSystem;driveType=$(if($v.DriveType -eq 3){'Fixed'}else{'Other'});local=($v.DriveType -eq 3)}|ConvertTo-Json -Compress`;
const PS_REPARSE = String.raw`$ErrorActionPreference='Stop';if([IO.Path]::GetFullPath([Environment]::GetFolderPath('Windows')).TrimEnd('\') -ine 'C:\Windows' -or [IO.Path]::GetFullPath([Environment]::SystemDirectory).TrimEnd('\') -ine 'C:\Windows\System32'){throw 'Unsupported native Windows system paths'};$i=[Console]::In.ReadToEnd()|ConvertFrom-Json;$p=[IO.Path]::GetFullPath([string]$i.path);$root=[IO.Path]::GetPathRoot($p);$cursor=$root;$bad=$false;foreach($part in ($p.Substring($root.Length) -split '[\\/]+'|Where-Object{$_})){$cursor=[IO.Path]::Combine($cursor,$part);$x=Get-Item -LiteralPath $cursor -Force;if(($x.Attributes -band [IO.FileAttributes]::ReparsePoint)-ne 0){$bad=$true;break}};if(!$bad -and $i.recursive){$stack=[Collections.Generic.Stack[string]]::new();$stack.Push($p);while($stack.Count){$q=$stack.Pop();$x=Get-Item -LiteralPath $q -Force;if(($x.Attributes -band [IO.FileAttributes]::ReparsePoint)-ne 0){$bad=$true;break};if($x.PSIsContainer){foreach($child in [IO.Directory]::EnumerateFileSystemEntries($q)){$stack.Push($child)}}}};[ordered]@{hasReparsePoint=$bad}|ConvertTo-Json -Compress`;
function systemPowerShell() {
  if (process.platform !== 'win32') throw new Error('project namespace preparation requires Windows');
  const executable = 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe';
  return executable;
}
function runPowerShell(script, input) {
  let executable;
  try { executable = systemPowerShell(); } catch (error) { return Promise.reject(error); }
  return new Promise((resolve, reject) => {
    const env = { SystemRoot: 'C:\\Windows', WINDIR: 'C:\\Windows', PATH: 'C:\\Windows\\System32;C:\\Windows', PSModulePath: 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\Modules' };
    const child = spawn(executable, ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script], { shell: false, windowsHide: true, env, stdio: ['pipe', 'pipe', 'pipe'] });
    let stdout = ''; let settled = false;
    const done = (error, result) => { if (settled) return; settled = true; clearTimeout(timer); error ? reject(error) : resolve(result); };
    const timer = setTimeout(() => { child.kill(); done(new Error('project namespace OS probe timed out')); }, 10000);
    child.stdout.setEncoding('utf8').on('data', (chunk) => { stdout += chunk; if (stdout.length > 32768) { child.kill(); done(new Error('project namespace OS probe output exceeded limit')); } });
    child.stderr.on('data', (chunk) => { if (chunk.length > 8192) { child.kill(); done(new Error('project namespace OS probe error output exceeded limit')); } });
    child.on('error', (error) => done(error));
    child.on('close', (code) => {
      if (settled) return;
      if (code !== 0) return done(new Error('project namespace OS probe failed'));
      try { done(null, JSON.parse(stdout.trim())); } catch { done(new Error('project namespace OS probe returned invalid JSON')); }
    });
    child.stdin.end(JSON.stringify(input));
  });
}
const DEFAULTS = Object.freeze({
  platform: process.platform,
  probeVolume: async (value) => runPowerShell(PS_VOLUME, { path: value }),
  assertNoReparse: async (value, recursive = false) => {
    const result = await runPowerShell(PS_REPARSE, { path: value, recursive });
    if (result?.hasReparsePoint !== false) throw new Error('reparse point rejected or probe invalid');
  },
});
function dependencies(value) {
  if (value === undefined) return DEFAULTS;
  if (!value || typeof value !== 'object' || Object.keys(value).some((key) => !['platform', 'probeVolume', 'assertNoReparse'].includes(key))) throw new TypeError('only platform, probeVolume, and assertNoReparse may be injected');
  return { ...DEFAULTS, ...value };
}
async function noReparse(deps, value, recursive = false) {
  await deps.assertNoReparse(value, recursive);
  let current = path.parse(value).root;
  for (const part of value.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    try { const info = await lstat(current); if (info.isSymbolicLink()) throw new Error('symlink/reparse path rejected'); }
    catch (error) { if (error?.code === 'ENOENT') break; throw error; }
  }
}
async function assertOrdinaryDirectory(value, label, deps) {
  const info = await lstat(value);
  if (!info.isDirectory() || info.isSymbolicLink()) throw new Error(`${label} must be an ordinary directory`);
  await noReparse(deps, value);
  const resolved = await realpath(value);
  // realpath supplies native case/traversal resolution; do not accept aliases or case-only spellings.
  if (canonicalPath(resolved) !== canonicalPath(value)) throw new Error(`${label} alias or casing alias rejected`);
  return value;
}
async function validateSources(input, deps) {
  const stable = absolute(input.stableSourceRoot, 'stableSourceRoot');
  const sandbox = absolute(input.sandboxSourceRoot, 'sandboxSourceRoot');
  if (normalized(stable) === normalized(sandbox)) throw new Error('stable and sandbox source roots must be distinct');
  if (within(sandbox, stable)) throw new Error('sandbox source root cannot contain stable source root');
  await assertOrdinaryDirectory(stable, 'stable source root', deps);
  await assertOrdinaryDirectory(sandbox, 'sandbox source root', deps);
  return { stable, sandbox };
}
async function validateVolume(deps, sandbox) {
  if (deps.platform !== 'win32') throw new Error('project namespace preparation requires Windows');
  const result = await deps.probeVolume(sandbox);
  if (!result || !['NTFS', 'exFAT'].includes(result.filesystem) || result.driveType !== 'Fixed' || result.local !== true) throw new Error('project namespace requires a local fixed NTFS or exFAT volume');
}
function recordFor(id, root, sourceRoots) {
  return Object.freeze({ format: FORMAT, mode: MODE, stage: STAGE, data_policy: DATA_POLICY, security_policy: SECURITY_POLICY, credentials_policy: CREDENTIALS_POLICY, installation_id: id, root, source_roots: Object.freeze({ stable: sourceRoots.stable, sandbox: sourceRoots.sandbox }), paths: pathsFor(root) });
}
function validateRecord(record, expected) {
  if (!record || typeof record !== 'object' || Array.isArray(record) || canonical(Object.keys(record).sort()) !== canonical([...RECORD_KEYS].sort())) throw new Error('project namespace marker has unexpected fields');
  if (record.format !== FORMAT || record.mode !== MODE || record.stage !== STAGE || record.data_policy !== DATA_POLICY || record.security_policy !== SECURITY_POLICY || record.credentials_policy !== CREDENTIALS_POLICY || record.installation_id !== expected.installation_id || record.root !== expected.root) throw new Error('project namespace marker binding mismatch');
  if (canonical(record.source_roots) !== canonical({ stable: expected.stable, sandbox: expected.sandbox })) throw new Error('project namespace marker source roots mismatch');
  if (canonical(record.paths) !== canonical(pathsFor(expected.root))) throw new Error('project namespace layout mismatch');
  return recordFor(expected.installation_id, expected.root, { stable: expected.stable, sandbox: expected.sandbox });
}
const LAYOUT = new Set(['launcher', 'state', MARKER, 'releases', 'host', 'host/state', 'host/profile', 'host/workspace', 'data', 'data/store']);
async function assertEmptyLayout(root, record, deps) {
  async function walk(dir, rel = '') {
    for (const name of await readdir(dir)) {
      const child = path.join(dir, name); const childRel = rel ? `${rel}/${name}` : name; const info = await lstat(child);
      if (info.isSymbolicLink() || !LAYOUT.has(childRel)) throw new Error('project namespace contains unknown or linked layout entry');
      if (info.isDirectory()) await walk(child, childRel);
      else if (childRel !== MARKER || !info.isFile() || info.nlink > 1) throw new Error('project namespace contains unexpected file');
    }
  }
  for (const item of LAYOUT) {
    try { const info = await lstat(path.join(root, item)); if (item !== MARKER && !info.isDirectory()) throw new Error('project namespace layout altered'); if (item === MARKER && !info.isFile()) throw new Error('project namespace marker altered'); }
    catch (error) { if (error?.code === 'ENOENT') throw new Error('project namespace layout is incomplete'); throw error; }
  }
  await walk(root);
  if ((await readdir(record.paths.data)).length !== 0) throw new Error('project namespace data store must remain empty');
}
function expectedFrom(input, sourceRoots) {
  const id = uuid(input.installationId);
  const root = projectNamespacePath(sourceRoots.sandbox, id);
  // The only permitted source-overlap is the fixed per-installation runtime subtree below sandbox.
  if (!within(sourceRoots.sandbox, root) || path.relative(sourceRoots.sandbox, root).split(path.sep)[0] !== '.managed-runtime') throw new Error('project namespace is outside the reserved sandbox subtree');
  return { installation_id: id, root, stable: sourceRoots.stable, sandbox: sourceRoots.sandbox };
}
async function readMarker(root, deps) {
  const markerPath = path.join(root, MARKER);
  await noReparse(deps, root, true);
  const info = await lstat(markerPath);
  if (!info.isFile() || info.isSymbolicLink() || info.nlink > 1 || info.size > MAX_MARKER_BYTES) throw new Error('project namespace marker must be a bounded regular non-link file');
  const handle = await open(markerPath, 'r'); let text;
  try {
    const opened = await handle.stat();
    if (!opened.isFile() || opened.nlink > 1 || opened.size > MAX_MARKER_BYTES || (info.ino && opened.ino && info.ino !== opened.ino)) throw new Error('project namespace marker changed during open');
    text = await handle.readFile('utf8');
  } finally { await handle.close(); }
  if (Buffer.byteLength(text, 'utf8') > MAX_MARKER_BYTES) throw new Error('project namespace marker exceeds size limit');
  let parsed;
  try { parsed = JSON.parse(text); } catch { throw new Error('project namespace marker is invalid JSON'); }
  if (`${canonical(parsed)}\n` !== text) throw new Error('project namespace marker is not canonical');
  return parsed;
}
export async function prepareProjectNamespace(input, injected) {
  const deps = dependencies(injected);
  const sources = await validateSources(input, deps);
  const expected = expectedFrom(input, sources);
  await validateVolume(deps, sources.sandbox);
  const runtimeRoot = path.dirname(expected.root);
  try { await lstat(runtimeRoot); await assertOrdinaryDirectory(runtimeRoot, 'reserved runtime directory', deps); }
  catch (error) { if (error?.code !== 'ENOENT') throw error; await mkdir(runtimeRoot); await assertOrdinaryDirectory(runtimeRoot, 'reserved runtime directory', deps); }
  try { await lstat(expected.root); throw new Error('project installation UUID already exists; refusing reuse'); }
  catch (error) { if (error?.code !== 'ENOENT') throw error; }
  await mkdir(expected.root);
  const record = recordFor(expected.installation_id, expected.root, sources);
  const dirs = [record.paths.launcher, record.paths.state, record.paths.releases, path.join(expected.root, 'host'), record.paths.host_state, record.paths.host_profile, record.paths.host_workspace, path.join(expected.root, 'data'), record.paths.data];
  for (const dir of dirs) await mkdir(dir);
  await noReparse(deps, expected.root, true);
  const markerPath = path.join(expected.root, MARKER); const marker = await open(markerPath, 'wx', 0o600);
  try { await marker.writeFile(`${canonical(record)}\n`, 'utf8'); await marker.sync(); } finally { await marker.close(); }
  await assertEmptyLayout(expected.root, record, deps);
  return record;
}
export async function readProjectNamespace(input, injected) {
  const deps = dependencies(injected);
  // Validate caller-trusted roots before inspecting the marker or namespace anchor.
  const sources = await validateSources(input, deps);
  const expected = expectedFrom(input, sources);
  await validateVolume(deps, sources.sandbox);
  const actual = await readMarker(expected.root, deps);
  const record = validateRecord(actual, expected);
  await assertEmptyLayout(expected.root, record, deps);
  return record;
}

