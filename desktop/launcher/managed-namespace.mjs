import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { lstat, mkdir, open, readFile, readdir, realpath } from 'node:fs/promises';
import path from 'node:path';

const FORMAT = 1;
const MODE = 'managed_installation_v1';
const STAGE = 'prepared_empty';
const DATA_POLICY = 'empty_no_migration';
const MARKER = 'state/managed-namespace.json';
const MAX_MARKER_BYTES = 16 * 1024;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const PRIVILEGED_SIDS = new Set(['S-1-5-18', 'S-1-5-32-544']);

const PS_ANCHOR = String.raw`$ErrorActionPreference='Stop'; $i=[Console]::In.ReadToEnd()|ConvertFrom-Json; [ordered]@{valid=$true}|ConvertTo-Json -Compress`;
const PS_VOLUME = String.raw`
$ErrorActionPreference='Stop'; $i=[Console]::In.ReadToEnd()|ConvertFrom-Json; $p=[IO.Path]::GetFullPath([string]$i.path); $d=[IO.Path]::GetPathRoot($p).TrimEnd('\').ToUpperInvariant(); if($d -notmatch '^[A-Z]:$'){throw 'Not a local drive'}; $v=Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='"+$d+"'"); if($null -eq $v){throw 'Volume unavailable'}; [ordered]@{filesystem=[string]$v.FileSystem;driveType=$(if($v.DriveType -eq 3){'Fixed'}else{'Other'});local=($v.DriveType -eq 3)}|ConvertTo-Json -Compress
`;
const PS_ACL = String.raw`
$ErrorActionPreference='Stop'; $i=[Console]::In.ReadToEnd()|ConvertFrom-Json; $p=[IO.Path]::GetFullPath([string]$i.path); $a=Get-Acl -LiteralPath $p; $c=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; $trusted=@($c,'S-1-5-18','S-1-5-32-544'); $owner=$a.GetOwner([Security.Principal.SecurityIdentifier]).Value; $badOwner=($owner -notin $trusted); $bad=@($a.Access|Where-Object{$_.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin $trusted}); [ordered]@{private=(!$badOwner -and $bad.Count -eq 0);ownerSid=$owner;currentSid=$c}|ConvertTo-Json -Compress
`;
const PS_SECURE_ROOT = String.raw`
$ErrorActionPreference='Stop'; $i=[Console]::In.ReadToEnd()|ConvertFrom-Json; $p=[IO.Path]::GetFullPath([string]$i.path); $a=Get-Acl -LiteralPath $p; $a.SetAccessRuleProtection($true,$false); $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User; $rights=[Security.AccessControl.FileSystemRights]::FullControl; $inherit=[Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'; $prop=[Security.AccessControl.PropagationFlags]::None; foreach($who in @($sid.Value,'S-1-5-18','S-1-5-32-544')){$id=New-Object Security.Principal.SecurityIdentifier($who); $rule=New-Object Security.AccessControl.FileSystemAccessRule($id,$rights,$inherit,$prop,[Security.AccessControl.AccessControlType]::Allow); $a.AddAccessRule($rule)}; Set-Acl -LiteralPath $p -AclObject $a; [ordered]@{secured=$true}|ConvertTo-Json -Compress
`;
const PS_REPARSE = String.raw`
$ErrorActionPreference='Stop'; $i=[Console]::In.ReadToEnd()|ConvertFrom-Json; $p=[IO.Path]::GetFullPath([string]$i.path); $root=[IO.Path]::GetPathRoot($p); $cursor=$root; $bad=$false; foreach($part in ($p.Substring($root.Length) -split '[\\/]+' | Where-Object {$_})){$cursor=[IO.Path]::Combine($cursor,$part);$x=Get-Item -LiteralPath $cursor -Force;if(($x.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){$bad=$true;break}};if(!$bad -and $i.recursive){$stack=[Collections.Generic.Stack[string]]::new();$stack.Push($p);while($stack.Count){$q=$stack.Pop();$x=Get-Item -LiteralPath $q -Force;if(($x.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){$bad=$true;break};if($x.PSIsContainer){foreach($child in [IO.Directory]::EnumerateFileSystemEntries($q)){$stack.Push($child)}}}};[ordered]@{hasReparsePoint=$bad}|ConvertTo-Json -Compress
`;

function absolute(value, label) {
  if (typeof value !== 'string' || !path.isAbsolute(value)) throw new TypeError(`${label} must be an absolute path`);
  return path.resolve(value);
}
function uuid(value) {
  if (typeof value !== 'string' || !UUID_RE.test(value)) throw new TypeError('installationId must be a canonical UUID');
  return value.toLowerCase();
}
function normalized(value) { return path.resolve(value).replace(/[\\/]+$/, '').toLowerCase(); }
function within(parent, child) { const rel = path.relative(parent, child); return rel === '' || (rel !== '..' && !rel.startsWith(`..${path.sep}`) && !path.isAbsolute(rel)); }
function rejectOverlap(privateRoot, sourceRoot, label) {
  if (within(normalized(privateRoot), normalized(sourceRoot)) || within(normalized(sourceRoot), normalized(privateRoot))) throw new Error(`private namespace overlaps ${label} source root`);
}
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}
function pathsFor(root) {
  return Object.freeze({ launcher: path.join(root, 'launcher'), state: path.join(root, 'state'), releases: path.join(root, 'releases'), host_state: path.join(root, 'host/state'), host_profile: path.join(root, 'host/profile'), host_workspace: path.join(root, 'host/workspace'), data: path.join(root, 'data/store') });
}
export function managedNamespacePath(localAppData, installationId) {
  return path.join(absolute(localAppData, 'localAppData'), 'my-agent', 'managed', uuid(installationId));
}
export function managedNamespaceDigest(record) { return createHash('sha256').update(canonical(record), 'utf8').digest('hex'); }

function powershellPath() {
  const expected = 'C:\\Windows';
  if (process.env.SYSTEMROOT?.toLowerCase() !== expected.toLowerCase() || process.env.WINDIR?.toLowerCase() !== expected.toLowerCase()) throw new Error('unsupported or inconsistent Windows system root');
  return 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe';
}
function runPowerShell(script, input) {
  if (process.platform !== 'win32') return Promise.reject(new Error('managed namespace preparation requires Windows'));
  let executable; try { executable = powershellPath(); } catch (error) { return Promise.reject(error); }
  const anchor = process.env.LOCALAPPDATA;
  if (!anchor || typeof input.localAppData !== 'string' || path.resolve(anchor).toLowerCase() !== path.resolve(input.localAppData).toLowerCase()) return Promise.reject(new Error('LOCALAPPDATA does not match the requested known-folder anchor'));
  const readInput = '$i=[Console]::In.ReadToEnd()|ConvertFrom-Json;';
  const verifyAnchor = "$nativeWindows=[Environment]::GetFolderPath('Windows'); if([IO.Path]::GetFullPath($nativeWindows).TrimEnd('\\') -ine 'C:\\Windows'){throw 'unsupported native Windows directory'}; $known=[Environment]::GetFolderPath('LocalApplicationData'); if([IO.Path]::GetFullPath($known).TrimEnd('\\') -ine [IO.Path]::GetFullPath([string]$i.localAppData).TrimEnd('\\') -or [IO.Path]::GetFullPath($env:LOCALAPPDATA).TrimEnd('\\') -ine [IO.Path]::GetFullPath([string]$i.localAppData).TrimEnd('\\')){throw 'LOCALAPPDATA known-folder mismatch'};";
  const checkedScript = script.replace(readInput, readInput + verifyAnchor);
  return new Promise((resolve, reject) => {
    const child = spawn(executable, ['-NoLogo','-NoProfile','-NonInteractive','-Command',checkedScript], { shell:false, windowsHide:true, env:{SystemRoot:'C:\\Windows',WINDIR:'C:\\Windows',LOCALAPPDATA:anchor,PATH:'C:\\Windows\\System32;C:\\Windows',PSModulePath:'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\Modules'}, stdio:['pipe','pipe','pipe'] });
    let stdout=''; let stderr=''; let settled=false;
    const done=(error,value)=>{if(settled)return;settled=true;clearTimeout(timer);error?reject(error):resolve(value)};
    const timer=setTimeout(()=>{child.kill();done(new Error('Windows security probe timed out'))},10000);
    child.stdout.setEncoding('utf8').on('data',chunk=>{stdout+=chunk;if(stdout.length>32768){child.kill();done(new Error('Windows security probe output exceeded limit'))}});
    child.stderr.setEncoding('utf8').on('data',chunk=>{stderr+=chunk;if(stderr.length>8192){child.kill();done(new Error('Windows security probe error output exceeded limit'))}});
    child.on('error',error=>done(error));
    child.on('close',code=>{if(settled)return;if(code!==0)return done(new Error('Windows security probe failed'));try{done(null,JSON.parse(stdout.trim()))}catch{done(new Error('Windows security probe returned invalid JSON'))}});
    child.stdin.end(JSON.stringify(input));
  });
}
const defaults = Object.freeze({
  platform: process.platform,
  assertLocalAppDataAnchor: async (value) => { if (!process.env.LOCALAPPDATA || path.resolve(process.env.LOCALAPPDATA).toLowerCase() !== path.resolve(value).toLowerCase()) throw new Error('LOCALAPPDATA environment anchor mismatch'); const result = await runPowerShell(PS_ANCHOR, { localAppData: value }); if (result.valid !== true) throw new Error('LOCALAPPDATA known-folder anchor mismatch'); },
  probeVolume: async (value) => { try { return await runPowerShell(PS_VOLUME, { path: value, localAppData: process.env.LOCALAPPDATA }); } catch (error) { throw withProbeCode(error, 'ERR_MANAGED_NTFS_PROBE', 'NTFS volume probe failed'); } },
  probeAcl: async (value) => { try { return await runPowerShell(PS_ACL, { path: value, localAppData: process.env.LOCALAPPDATA }); } catch (error) { throw withProbeCode(error, 'ERR_MANAGED_ACL_PROBE', 'private ACL probe failed'); } },
  assertNoReparse: async (value, recursive = false) => {
    const result = await runPowerShell(PS_REPARSE, { path: value, recursive, localAppData: process.env.LOCALAPPDATA });
    if (result.hasReparsePoint !== false) throw new Error('reparse point rejected or probe invalid');
  },
  secureRoot: async (value) => {
    const result = await runPowerShell(PS_SECURE_ROOT, { path: value, localAppData: process.env.LOCALAPPDATA });
    if (result.secured !== true) throw new Error('managed root ACL could not be secured');
  },
});
function depsOrDefault(value) { return value ?? defaults; }
function withProbeCode(error, code, message) { const wrapped = new Error(message, { cause: error }); wrapped.code = code; return wrapped; }
async function noReparse(deps, value, recursive = false) {
  await deps.assertNoReparse(value, recursive);
  let current = path.parse(value).root;
  for (const part of value.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    try { const info = await lstat(current); if (info.isSymbolicLink()) throw new Error('symlink/reparse path rejected'); }
    catch (error) { if (error?.code === 'ENOENT') break; throw error; }
  }
}
async function volumeCheck(deps, value) {
  if (deps.platform !== 'win32') throw new Error('managed namespace preparation requires Windows');
  const result = await deps.probeVolume(value);
  if (!result || result.filesystem !== 'NTFS' || result.driveType !== 'Fixed' || result.local !== true) throw new Error('managed namespace requires a local fixed NTFS volume');
}
async function aclCheck(deps, value) {
  const result = await deps.probeAcl(value);
  const current = result?.currentSid; const owner = result?.ownerSid;
  if (!result || result.private !== true || typeof current !== 'string' || typeof owner !== 'string' || (owner !== current && !PRIVILEGED_SIDS.has(owner))) throw new Error('managed namespace requires private ACLs and a current-user/system/admin owner');
}
async function assertSource(value, label, deps) {
  const source = absolute(value, `${label}SourceRoot`);
  const info = await lstat(source);
  if (!info.isDirectory() || info.isSymbolicLink()) throw new Error(`${label} source root must be a real directory`);
  await noReparse(deps, source);
  if (normalized(await realpath(source)) !== normalized(source)) throw new Error(`${label} source root alias rejected`);
  return source;
}
async function inspectOrdinaryDirectory(value, deps, checkAcl = false) {
  const info = await lstat(value);
  if (!info.isDirectory() || info.isSymbolicLink()) throw new Error('managed namespace parent must be an ordinary directory');
  await noReparse(deps, value);
  if (checkAcl) await aclCheck(deps, value);
}
async function createOrReuseOrdinaryDirectory(value, deps) {
  try { await mkdir(value); } catch (error) { if (error?.code !== 'EEXIST') throw error; }
  await inspectOrdinaryDirectory(value, deps, true);
}

export async function prepareManagedNamespace({ localAppData, installationId, stableSourceRoot, sandboxSourceRoot }, dependencies) {
  const deps = depsOrDefault(dependencies);
  const base = absolute(localAppData, 'localAppData'); const id = uuid(installationId);
  await deps.assertLocalAppDataAnchor(base);
  const baseInfo = await lstat(base);
  if (!baseInfo.isDirectory() || baseInfo.isSymbolicLink()) throw new Error('LOCALAPPDATA must be an existing ordinary directory');
  await deps.assertLocalAppDataAnchor(base);
  await noReparse(deps, base);
  const stable = await assertSource(stableSourceRoot, 'stable', deps); const sandbox = await assertSource(sandboxSourceRoot, 'sandbox', deps);
  const root = managedNamespacePath(base, id);
  rejectOverlap(root, stable, 'stable'); rejectOverlap(root, sandbox, 'sandbox');
  await volumeCheck(deps, root); await aclCheck(deps, base);
  try { await lstat(root); throw new Error('managed installation root already exists; refusing reuse'); }
  catch (error) { if (error?.code !== 'ENOENT') throw error; }
  const agent = path.join(base, 'my-agent'); const managed = path.join(agent, 'managed');
  try { await inspectOrdinaryDirectory(agent, deps, true); } catch (error) { if (error?.code !== 'ENOENT') throw error; await mkdir(agent); await inspectOrdinaryDirectory(agent, deps, true); }
  try { await inspectOrdinaryDirectory(managed, deps, true); } catch (error) { if (error?.code !== 'ENOENT') throw error; await mkdir(managed); await inspectOrdinaryDirectory(managed, deps, true); }
  // Existing ordinary parents are reusable; the installation UUID root is always exclusive.
  await mkdir(root);
  await deps.secureRoot(root);
  const paths = pathsFor(root);
  const record = Object.freeze({ format: FORMAT, mode: MODE, stage: STAGE, data_policy: DATA_POLICY, installation_id: id, root, source_roots: Object.freeze({ stable, sandbox }), paths });
  const dirs = [paths.launcher, paths.state, paths.releases, path.join(root, 'host'), paths.host_state, paths.host_profile, paths.host_workspace, path.join(root, 'data'), paths.data];
  for (const dir of dirs) await mkdir(dir);
  await noReparse(deps, root, true); await aclCheck(deps, root);
  for (const dir of dirs) await aclCheck(deps, dir);
  if ((await readdir(paths.data)).length !== 0) throw new Error('prepared managed data store must remain empty');
  const markerPath = path.join(root, MARKER);
  const marker = await open(markerPath, 'wx', 0o600);
  try { await marker.writeFile(`${canonical(record)}\n`, 'utf8'); await marker.sync(); } finally { await marker.close(); }
  await aclCheck(deps, markerPath);
  return record;
}

function checkKeys(value, keys, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) throw new Error(`${label} has unexpected fields`);
}
function validateRecord(record, root, localAppData) {
  checkKeys(record, ['format', 'mode', 'stage', 'data_policy', 'installation_id', 'root', 'source_roots', 'paths'], 'marker');
  if (typeof record.installation_id !== 'string' || record.installation_id !== record.installation_id.toLowerCase() || !UUID_RE.test(record.installation_id) || record.format !== FORMAT || record.mode !== MODE || record.stage !== STAGE || record.data_policy !== DATA_POLICY) throw new Error('invalid managed namespace marker');
  if (normalized(managedNamespacePath(localAppData, record.installation_id)) !== normalized(root) || normalized(record.root) !== normalized(root)) throw new Error('managed namespace root is not anchored to LOCALAPPDATA');
  checkKeys(record.source_roots, ['stable', 'sandbox'], 'source_roots');
  const stable = absolute(record.source_roots.stable, 'stable source root'); const sandbox = absolute(record.source_roots.sandbox, 'sandbox source root');
  if (stable !== record.source_roots.stable || sandbox !== record.source_roots.sandbox) throw new Error('source root alias rejected');
  rejectOverlap(root, stable, 'stable'); rejectOverlap(root, sandbox, 'sandbox');
  checkKeys(record.paths, ['launcher', 'state', 'releases', 'host_state', 'host_profile', 'host_workspace', 'data'], 'paths');
  const paths = pathsFor(root);
  if (canonical(record.paths) !== canonical(paths)) throw new Error('managed namespace layout mismatch');
  return Object.freeze({ ...record, root, source_roots: Object.freeze({ stable, sandbox }), paths });
}
async function assertExactLayout(root, deps) {
  const expected = new Set(['launcher', 'state', 'state/managed-namespace.json', 'releases', 'host', 'host/state', 'host/profile', 'host/workspace', 'data', 'data/store']);
  async function walk(dir, rel = '') {
    for (const name of await readdir(dir)) {
      const child = path.join(dir, name); const childRel = rel ? `${rel}/${name}` : name; const info = await lstat(child);
      if (info.isSymbolicLink()) throw new Error('reparse/link in managed namespace');
      if (!expected.has(childRel)) throw new Error('managed namespace contains an unknown layout entry');
      if (info.isDirectory()) await walk(child, childRel);
      else if (childRel !== MARKER || !info.isFile()) throw new Error('managed namespace contains an unexpected file');
    }
  }
  await walk(root);
  for (const entry of expected) { try { await lstat(path.join(root, entry)); } catch { throw new Error('managed namespace layout is incomplete'); } }
  await noReparse(deps, root, true);
}

export async function readManagedNamespace(root, dependencies) {
  const deps = depsOrDefault(dependencies);
  const base = absolute(deps.localAppData ?? process.env.LOCALAPPDATA, 'LOCALAPPDATA');
  const absoluteRoot = absolute(root, 'root');
  const basenameId = uuid(path.basename(absoluteRoot));
  const expectedRoot = managedNamespacePath(base, basenameId);
  if (absoluteRoot !== expectedRoot) throw new Error('managed namespace root is outside the current LOCALAPPDATA anchor');
  await deps.assertLocalAppDataAnchor(base);
  await noReparse(deps, absoluteRoot);
  const markerPath = path.join(absoluteRoot, MARKER);
  const markerInfo = await lstat(markerPath);
  if (!markerInfo.isFile() || markerInfo.isSymbolicLink() || markerInfo.nlink > 1 || markerInfo.size > MAX_MARKER_BYTES) throw new Error('managed namespace marker must be a bounded regular non-link file');
  await noReparse(deps, markerPath);
  const handle = await open(markerPath, 'r'); let markerText;
  try {
    const opened = await handle.stat();
    if (!opened.isFile() || opened.nlink > 1 || opened.size > MAX_MARKER_BYTES || (markerInfo.ino && opened.ino && markerInfo.ino !== opened.ino)) throw new Error('managed namespace marker changed during open');
    markerText = await handle.readFile('utf8');
  } finally { await handle.close(); }
  if (Buffer.byteLength(markerText, 'utf8') > MAX_MARKER_BYTES) throw new Error('managed namespace marker exceeds size limit');
  let parsed;
  try { parsed = JSON.parse(markerText); } catch { throw new Error('managed namespace marker is invalid JSON'); }
  if (`${canonical(parsed)}\n` !== markerText) throw new Error('managed namespace marker is not canonical');
  const record = validateRecord(parsed, absoluteRoot, base);
  await volumeCheck(deps, absoluteRoot); await aclCheck(deps, base); await aclCheck(deps, absoluteRoot);
  const dirs = [record.paths.launcher, record.paths.state, record.paths.releases, path.join(absoluteRoot, 'host'), record.paths.host_state, record.paths.host_profile, record.paths.host_workspace, path.join(absoluteRoot, 'data'), record.paths.data];
  for (const dir of dirs) { const info = await lstat(dir); if (!info.isDirectory() || info.isSymbolicLink()) throw new Error('managed namespace layout altered'); await aclCheck(deps, dir); }
  await aclCheck(deps, markerPath);
  if ((await readdir(record.paths.data)).length !== 0) throw new Error('managed data store is not empty');
  await assertExactLayout(absoluteRoot, deps);
  return record;
}



