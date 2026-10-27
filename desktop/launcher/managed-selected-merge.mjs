import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { constants as fsConstants, existsSync, lstatSync, mkdirSync, openSync, readFileSync, readdirSync, closeSync, writeSync, fstatSync, readSync, fsyncSync, realpathSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { assertManagedPlainIndex } from './managed-clean-git-metadata.mjs';
import { assertManagedReadonlySandboxIndex } from './managed-readonly-sandbox-index.mjs';
import { claimSelectedMergeStore } from './managed-selected-merge-store.mjs';
import { validateManagedSourceCheckpoint } from './managed-source-checkpoint.mjs';
import { validateManagedSelectionRequest } from './managed-source-selection.mjs';

const SHA = /^[a-f0-9]{64}$/;
const MAX_RAW_INDEX_BYTES = 1024 * 1024;
const MAX_TEMP_INDEX_BYTES = 4 * 1024 * 1024;
const MAX_SOURCE_FILES=20000,MAX_SOURCE_BYTES=64*1024*1024,MAX_SOURCE_FILE_BYTES=4*1024*1024,MAX_SOURCE_ENTRIES=60000,MAX_SOURCE_DEPTH=64;
const OID = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
const ALLOWED = ['agent-core/', 'desktop/', 'docs/', 'protocol/', 'scripts/', 'evolve/tools/', 'evolve/skills/', 'evolve/tool-catalog/'];
const ROOT_ALLOWED = new Set(['AGENTS.md', '.gitignore', 'start-desktop.bat', 'init-sandbox.bat', 'check-stable-before-sandbox-merge.bat', 'sync-sandbox-worktree.bat']);
const NEVER = new Set(['.git', '.venv', 'venv', 'node_modules', 'data', 'artifacts', 'dist', 'dist-electron', '__pycache__', 'profile', 'profiles', 'user-data', 'workspace', 'workspaces', 'config-store', 'secrets', 'private', 'credentials', 'llm_secrets', '.managed-runtime']);
const SECRET = /^(?:\.env(?:\..*)?|llm_secrets\.(?:json|ya?ml)|llm_models\.(?:json|ya?ml)|.*\.(?:pem|key|p12|pfx|cer|crt))$/i;
const fail = (code, message, paths = []) => { const e = new TypeError(message); e.code = code; e.paths = paths; throw e; };
const canonical = v => Array.isArray(v) ? `[${v.map(canonical).join(',')}]` : v && typeof v === 'object' ? `{${Object.keys(v).sort().map(k => `${JSON.stringify(k)}:${canonical(v[k])}`).join(',')}}` : JSON.stringify(v);
const sha = b => createHash('sha256').update(b).digest('hex');
const record = v => v !== null && typeof v === 'object' && !Array.isArray(v);
const exact = (v, keys) => record(v) && Object.keys(v).length === keys.length && keys.every(k => Object.hasOwn(v, k));
function safeSelectedPath(p) {
  if (typeof p !== 'string' || !p || p.startsWith('/') || p.includes('\\') || p.includes(':')) return false;
  const parts = p.split('/');
  if (parts.some(s => !s || s === '.' || s === '..' || /[. ]$/.test(s) || /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i.test(s))) return false;
  if (parts.some(s => NEVER.has(s.toLowerCase()) || SECRET.test(s))) return false;
  return ROOT_ALLOWED.has(p) || ALLOWED.some(prefix => p.startsWith(prefix));
}
export function validateManagedSelectedMergeRequest(value) {
  if (!exact(value, ['format', 'mode', 'checkpointDigest']) || value.format !== 1 || value.mode !== 'managed_selected_merge_request_v1' || !SHA.test(value.checkpointDigest || '')) fail('selected_merge_request_invalid', 'managed selected merge request is invalid');
  return { format: 1, mode: 'managed_selected_merge_request_v1', checkpointDigest: value.checkpointDigest };
}
function root(input, label) {
  if (typeof input !== 'string' || !path.isAbsolute(input) || path.resolve(input) !== input) fail(`${label}_root_invalid`, `${label} root must be canonical absolute path`);
  let cur = path.parse(input).root;
  for (const part of input.slice(cur.length).split(path.sep).filter(Boolean)) {
    cur = path.join(cur, part); let st;
    try { st = lstatSync(cur); } catch { fail(`${label}_root_missing`, `${label} root is missing or inaccessible`); }
    if (st.isSymbolicLink() || (st.attributes & 0x400)) fail(`${label}_root_linked`, `${label} root contains link/reparse ancestor`);
  }
  if (!lstatSync(input).isDirectory()) fail(`${label}_root_not_directory`, `${label} root is not a directory`);
  if(realpathSync.native(input)!==input)fail(`${label}_root_alias_rejected`,`${label} root is not a canonical non-alias path`);
  return input;
}
function gitExecutable(executable) {
  if (typeof executable !== 'string' || !path.isAbsolute(executable)) fail('git_executable_invalid', 'explicit absolute native Git executable is required');
  let real; try { real = path.resolve(executable); if (process.platform === 'win32' && path.extname(real).toLowerCase() !== '.exe') fail('git_executable_wrapper_rejected','Windows Git executable must be a native .exe'); if (!lstatSync(real).isFile()) throw new Error(); } catch (e) { if(e.code)throw e; fail('git_executable_invalid', 'explicit Git executable is not a regular file'); }
  let cursor=path.parse(real).root; for(const part of real.slice(cursor.length).split(path.sep).filter(Boolean)){cursor=path.join(cursor,part);const st=lstatSync(cursor);if(st.isSymbolicLink()||(st.attributes&0x400))fail('git_executable_reparse_rejected','Git executable path contains a reparse ancestor');}
  if(realpathSync.native(real)!==real)fail('git_executable_alias_rejected','Git executable path is not canonical');
  return real;
}
function envFor(rootPath, indexFile) {
  const env = { PATH: process.env.PATH || '', GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: process.platform === 'win32' ? 'NUL' : '/dev/null', GIT_TERMINAL_PROMPT: '0', GIT_OPTIONAL_LOCKS: '0', LC_ALL: 'C', HOME: os.tmpdir(), USERPROFILE: os.tmpdir(), GIT_ATTR_NOSYSTEM: '1' };
  for (const key of ['SYSTEMROOT', 'WINDIR', 'TMP', 'TEMP']) if (process.env[key]) env[key] = process.env[key];
  for (const key of Object.keys(process.env)) if (key.startsWith('GIT_') && key !== 'GIT_EXEC_PATH') { /* deliberately not inherited */ }
  if (indexFile) env.GIT_INDEX_FILE = indexFile;
  return env;
}
function run(git, cwd, args, indexFile, input) {
  const cfg = ['-c', `safe.directory=${cwd}`, '-c', 'core.fsmonitor=false', '-c', 'core.autocrlf=false', '-c', `core.attributesFile=${process.platform === 'win32' ? 'NUL' : '/dev/null'}`, '-c', `core.hooksPath=${process.platform === 'win32' ? 'NUL' : '/dev/null'}`, '-c', 'commit.gpgSign=false', '-c', 'tag.gpgSign=false', '-c', 'submodule.recurse=false', '-c', 'diff.external=', '-c', 'merge.autoStash=false', '-c', 'protocol.allow=never', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false', '-c', 'maintenance.auto=false', '--no-pager', '--literal-pathspecs'];
  const r = spawnSync(git, [...cfg, ...args], { cwd, env: envFor(cwd, indexFile), input, encoding: 'buffer', windowsHide: true, timeout: 20000, maxBuffer: 32 * 1024 * 1024, stdio: ['pipe', 'pipe', 'pipe'] });
  if (r.error || r.status !== 0) fail('git_metadata_rejected', `safe Git operation failed (${r.error?.code || r.status})`);
  return r.stdout;
}
const text = b => b.toString('utf8').replace(/[\r\n]+$/, '');
function locateGitDirs(rootPath){
  const marker=path.join(rootPath,'.git'); let st; try{st=lstatSync(marker);}catch{fail('git_marker_missing','Git worktree marker is missing');}
  if(st.isSymbolicLink()||(st.attributes&0x400))fail('git_marker_unsafe','Git marker is linked');
  let gitDir;
  if(st.isDirectory())gitDir=marker;
  else if(st.isFile()){const raw=readFileSync(marker,'utf8');const m=/^gitdir: ([^\r\n]+)\r?\n?$/.exec(raw);if(!m)fail('git_marker_invalid','Git worktree marker is malformed');gitDir=path.resolve(rootPath,m[1]);}
  else fail('git_marker_invalid','Git marker type is unsupported');
  let commonDir=gitDir;const cd=path.join(gitDir,'commondir');if(existsSync(cd)){const cs=lstatSync(cd);if(!cs.isFile()||cs.isSymbolicLink()||cs.nlink!==1)fail('git_common_dir_unsafe','commondir metadata unsafe');commonDir=path.resolve(gitDir,readFileSync(cd,'utf8').trim());}
  return {gitDir:path.resolve(gitDir),commonDir:path.resolve(commonDir)};
}
function safeGitRef(value){return typeof value==='string'&&/^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$/.test(value)&&!value.includes('..')&&!value.includes('//')&&!value.includes('@{')&&!value.endsWith('/')&&!value.endsWith('.')&&!value.split('/').some(part=>!part||part.startsWith('.')||part.endsWith('.lock'));}
function validateLocalConfig(dirs){
  const allowed=new Map([
    ['core.repositoryformatversion',/^(?:0|1)$/],['core.filemode',/^(?:true|false)$/i],['core.bare',/^false$/i],['core.logallrefupdates',/^(?:true|false)$/i],['core.ignorecase',/^(?:true|false)$/i],['core.symlinks',/^(?:true|false)$/i],
    ['extensions.objectformat',/^(?:sha1|sha256)$/i],['extensions.worktreeconfig',/^(?:true|false)$/i],['user.name',/^[^\r\n\0]{1,256}$/],['user.email',/^[^\r\n\0]{1,256}$/],
  ]);
  const hashes=[];let worktreeConfig=false;
  for(const file of [...new Set([path.join(dirs.commonDir,'config'),path.join(dirs.gitDir,'config')])]){
    if(!existsSync(file))continue;const st=lstatSync(file);if(!st.isFile()||st.isSymbolicLink()||(st.attributes&0x400)||st.nlink!==1||st.size>65536)fail('git_config_unsafe','local Git config is unsafe');
    const bytes=readFileSync(file);const body=bytes.toString('utf8');if(!Buffer.from(body).equals(bytes))fail('git_config_encoding_invalid','local Git config encoding is invalid');
    let section='',seen=new Set();
    for(const raw of body.split(/\r?\n/)){const line=raw.trim();if(!line||line.startsWith('#')||line.startsWith(';'))continue;
      const sm=/^\[([A-Za-z0-9._ -]+)(?:\s+"([A-Za-z0-9._/-]+)")?\]$/.exec(line);if(sm){section=sm[1].toLowerCase()+(sm[2]?'.'+sm[2].toLowerCase():'');continue;}
      const kv=/^([A-Za-z][A-Za-z0-9-]*)\s*=\s*(.*)$/.exec(line);if(!kv||!section)fail('git_config_syntax_rejected','unsupported local Git config syntax');
      const key=section+'.'+kv[1].toLowerCase(),value=kv[2];let rule=allowed.get(key);
      if(key.startsWith('remote.')&&key.endsWith('.url'))rule=/^[^\r\n\0]{1,2048}$/;
      if(key.startsWith('remote.')&&key.endsWith('.fetch'))rule=/^\+?refs\/[A-Za-z0-9._/-]+:refs\/[A-Za-z0-9._/-]+$/;
      if(key.startsWith('branch.')&&key.endsWith('.remote'))rule=/^(?:\.|[A-Za-z0-9._/-]{1,128})$/;
      if(key.startsWith('branch.')&&key.endsWith('.merge'))rule=/^refs\/heads\/[A-Za-z0-9._/-]{1,256}$/;
      if(key.startsWith('branch.')&&key.endsWith('.vscode-merge-base'))rule={test:safeGitRef};
      if(!rule||!rule.test(value)||seen.has(key))fail('git_config_key_rejected',`unsafe or unknown local Git config key: ${key}`);
      if(key==='extensions.worktreeconfig'&&value.toLowerCase()==='true')worktreeConfig=true;seen.add(key);
    }
    hashes.push([file,sha(bytes)]);
  }
  if(worktreeConfig&&existsSync(path.join(dirs.gitDir,'config.worktree')))fail('git_worktree_config_rejected','worktree-specific config is not supported');
  if(existsSync(path.join(dirs.gitDir,'config.worktree')))fail('git_worktree_config_rejected','worktree-specific config is not supported');
  for(const file of new Set([path.join(dirs.gitDir,'info','attributes'),path.join(dirs.commonDir,'info','attributes')])){let st;try{st=lstatSync(file);}catch(e){if(e.code==='ENOENT')continue;throw e;}if(!st.isFile()||st.isSymbolicLink()||(st.attributes&0x400)||st.nlink!==1||st.size>64*1024)fail('git_attributes_unsafe','Git info attributes metadata is unsafe');if(st.size!==0)fail('git_attributes_rejected','Git info attributes are not supported');}
  for(const rel of ['objects/info/alternates','objects/info/http-alternates','info/grafts','shallow'])if(existsSync(path.join(dirs.commonDir,rel)))fail('git_object_indirection_rejected','Git object indirection/shallow state is unsupported');
  return {hashes,objectFormat:readConfigFormat(hashes,dirs)};
}
function readConfigFormat(_hashes,dirs){const f=path.join(dirs.commonDir,'config');if(!existsSync(f))return 'sha1';const text=readFileSync(f,'utf8');const m=/^\s*objectformat\s*=\s*(sha1|sha256)\s*$/im.exec(text);return m?m[1].toLowerCase():'sha1';}
function assertObjectStore(git,rootPairs){
  const moduleUrl=new URL('./managed-clean-git-metadata.mjs',import.meta.url).href;
  const script=`import {assertManagedGitObjectStore} from ${JSON.stringify(moduleUrl)};const input=JSON.parse((await import('node:fs')).readFileSync(0,'utf8'));for(const d of input)await assertManagedGitObjectStore(d);process.stdout.write('ok');`;
  const env={PATH:path.dirname(git),ELECTRON_RUN_AS_NODE:'1',GIT_CONFIG_NOSYSTEM:'1',GIT_CONFIG_GLOBAL:process.platform==='win32'?'NUL':'/dev/null',HOME:os.tmpdir(),USERPROFILE:os.tmpdir(),LC_ALL:'C'};for(const k of ['SYSTEMROOT','WINDIR','TEMP','TMP'])if(process.env[k])env[k]=process.env[k];
  const r=spawnSync(process.execPath,['--input-type=module','-e',script],{cwd:rootPairs[0].root,env,input:JSON.stringify(rootPairs.map(x=>x.dirs)),encoding:'utf8',windowsHide:true,timeout:120000,maxBuffer:1024*1024});
  if(r.error||r.status!==0||r.stdout!=='ok')fail('git_object_store_guard_failed','managed Git object-store guard rejected the repository');
}
function guardBeforeGit(git,roots){
  const pairs=roots.map(rootPath=>{const dirs=locateGitDirs(rootPath);const marker=path.join(rootPath,'.git');return {root:rootPath,dirs,markerSha256:existsSync(marker)&&lstatSync(marker).isFile()?sha(readFileSync(marker)):null};});
  const config=pairs.map(x=>validateLocalConfig(x.dirs));
  assertObjectStore(git,pairs);
  for(let i=0;i<pairs.length;i++){
    const indexPath=path.join(pairs[i].dirs.gitDir,'index');if(!existsSync(indexPath))fail('git_index_missing','plain Git index is missing');
    const indexStat=lstatSync(indexPath);if(indexStat.size>MAX_RAW_INDEX_BYTES)fail('selected_merge_raw_index_limit_exceeded','existing raw Git index exceeds 1 MiB');const bytes=readFileSync(indexPath);if(bytes.length!==indexStat.size||lstatSync(indexPath).size!==indexStat.size)fail('git_index_changed_during_read','Git index changed while being read');try{if(i===1)assertManagedReadonlySandboxIndex(bytes,{objectFormat:config[i].objectFormat});else assertManagedPlainIndex(bytes,{objectFormat:config[i].objectFormat});}catch{fail(i===1?'sandbox_git_index_readonly_guard_failed':'stable_git_index_plain_guard_failed',i===1?'read-only sandbox index metadata is unsupported or malformed':'stable writer index failed strict plain-index validation');}
  }
  const metadataDigest=sha(Buffer.from(canonical({roots:pairs.map(x=>({root:x.root,gitDir:x.dirs.gitDir,commonDir:x.dirs.commonDir,markerSha256:x.markerSha256})),config:config.map(x=>x.hashes)})));
  return {pairs,metadataDigest};
}
function state(git, cwd) {
  const top = path.resolve(text(run(git, cwd, ['rev-parse', '--show-toplevel'])));
  if (top !== cwd) fail('git_root_mismatch', 'supplied path is not exact Git worktree root');
  const gitDir = path.resolve(cwd, text(run(git, cwd, ['rev-parse', '--path-format=absolute', '--git-dir'])));
  const commonDir = path.resolve(cwd, text(run(git, cwd, ['rev-parse', '--path-format=absolute', '--git-common-dir'])));
  for (const d of [gitDir, commonDir]) { const st = lstatSync(d); if (!st.isDirectory() || st.isSymbolicLink() || (st.attributes & 0x400)) fail('git_directory_unsafe', 'Git metadata directory is unsafe'); }
  const head = text(run(git, cwd, ['rev-parse', '--verify', 'HEAD']));
  if (!OID.test(head)) fail('git_head_invalid', 'Git HEAD is not a full object ID');
  const branch = text(run(git, cwd, ['symbolic-ref', '--quiet', '--short', 'HEAD']));
  if (!branch || branch.startsWith('-')) fail('detached_head_rejected', 'detached or invalid branch is not supported');
  const indexPath = path.join(gitDir, 'index');
  let rawIndexSha256 = null;
  if (existsSync(indexPath)) { const st = lstatSync(indexPath); if (!st.isFile() || st.isSymbolicLink() || st.nlink !== 1) fail('git_index_unsafe', 'Git index is not an ordinary single-link file'); if(st.size>MAX_RAW_INDEX_BYTES)fail('selected_merge_raw_index_limit_exceeded','existing raw Git index exceeds 1 MiB'); const raw=readFileSync(indexPath);if(raw.length!==st.size||lstatSync(indexPath).size!==st.size)fail('git_index_changed_during_read','Git index changed while being read');rawIndexSha256 = sha(raw); }
  return { worktreeRoot: cwd, gitDir, commonDir, branch, head, rawIndexSha256 };
}
function treeMap(git, cwd, head) {
  const out = run(git, cwd, ['ls-tree', '-rz', '--full-tree', head]); const map = new Map();
  for (const row of out.toString('utf8').split('\0').filter(Boolean)) {
    const tab = row.indexOf('\t'), meta = row.slice(0, tab).split(' '), p = row.slice(tab + 1);
    if (tab < 0 || meta.length !== 3 || !/^\d{6}$/.test(meta[0]) || !OID.test(meta[2]) || !['blob', 'commit'].includes(meta[1])) fail('tree_entry_unsupported', 'stable tree contains malformed entry; refusing to omit it', [p]);
    if(p.split('/').at(-1).toLowerCase()==='.gitattributes')fail('git_attributes_rejected','Git tree contains a .gitattributes path',[p]);
    const protectedPath = p.split('/').some(seg => NEVER.has(seg.toLowerCase()) || SECRET.test(seg));
    if (!protectedPath && ((meta[1] === 'blob' && !['100644','100755'].includes(meta[0])) || (meta[1] === 'commit' && meta[0] !== '160000'))) fail('tree_entry_unsupported', 'allowed-source tree contains symlink, submodule, or unsupported mode', [p]);
    map.set(p, { mode: meta[0], oid: meta[2], type: meta[1] });
  }
  return map;
}
function indexMap(git, cwd) {
  const out = run(git, cwd, ['ls-files', '--stage', '-z']); const map = new Map();
  for (const row of out.toString('utf8').split('\0').filter(Boolean)) {
    const tab = row.indexOf('\t'), meta = row.slice(0, tab).split(' '), p = row.slice(tab + 1);
    if (tab < 0 || meta.length !== 3 || meta[2] !== '0') fail('git_index_conflict', 'index has conflicts or malformed entries', [p]);
    if(p.split('/').at(-1).toLowerCase()==='.gitattributes')fail('git_attributes_rejected','Git index contains a .gitattributes path',[p]);
    if (!/^\d{6}$/.test(meta[0]) || !OID.test(meta[1])) fail('git_index_type_unsupported', 'index contains malformed source metadata', [p]);
    map.set(p, { mode: meta[0], oid: meta[1] });
  }
  return map;
}
function objectOid(format, type, body) { const hash = createHash(format); hash.update(Buffer.from(`${type} ${body.length}\0`)); hash.update(body); return hash.digest('hex'); }
function align8(n){return Math.ceil(n/8)*8;}
function estimateTemporaryIndexUpperBound(tree,selectedPaths){
  const entries=[...tree.keys(),...selectedPaths];
  let bytes=12+32+16+80; // index header, SHA-256 checksum allowance, extension frame and root cache node
  const dirs=new Set();
  for(const p of entries){const pathBytes=Buffer.byteLength(p,'utf8');bytes+=align8(80+pathBytes+1);const parts=p.split('/');for(let i=1;i<parts.length;i++)dirs.add(parts.slice(0,i).join('/'));}
  for(const d of dirs)bytes+=80+Buffer.byteLength(d,'utf8')+1;
  return bytes;
}
function assertTemporaryIndexBound(indexPath){
  let st;try{st=lstatSync(indexPath);}catch(e){if(e.code==='ENOENT')fail('selected_merge_temp_index_missing','temporary Git index was not materialized');throw e;}
  if(!st.isFile()||st.isSymbolicLink()||(st.attributes&0x400)||st.nlink!==1)fail('selected_merge_temp_index_unsafe','temporary Git index is not an ordinary single-link file');
  if(st.size>MAX_TEMP_INDEX_BYTES)fail('selected_merge_temp_index_limit_exceeded','temporary Git index exceeded 4 MiB; maintenance required');
  return st.size;
}
function expectedTree(git, cwd, head, files) {
  const format = text(run(git, cwd, ['rev-parse', '--show-object-format'])); const map = treeMap(git, cwd, head);
  for (const f of files) map.set(f.path, { mode: f.mode, oid: f.workingOid });
  const rootNode = { files: new Map(), dirs: new Map() };
  for (const [p, value] of map) {
    const parts = p.split('/'); let n = rootNode;
    for (const part of parts.slice(0, -1)) { if (!n.dirs.has(part)) n.dirs.set(part, { files: new Map(), dirs: new Map() }); n = n.dirs.get(part); }
    n.files.set(parts.at(-1), value);
  }
  const build = n => {
    const entries = [];
    for (const [name, value] of n.files) entries.push({ name, sort: Buffer.from(`${name}\0`), mode: value.mode, oid: value.oid });
    for (const [name, child] of n.dirs) { const oid = build(child); entries.push({ name, sort: Buffer.from(`${name}/`), mode: '40000', oid }); }
    entries.sort((a,b) => Buffer.compare(a.sort,b.sort));
    const body = Buffer.concat(entries.map(e => Buffer.concat([Buffer.from(`${e.mode} ${e.name}\0`), Buffer.from(e.oid, 'hex')])));
    return objectOid(format, 'tree', body);
  };
  return build(rootNode);
}
function sourcePathAllowed(p) {
  const parts=p.split('/');
  if(parts.some(seg => NEVER.has(seg.toLowerCase()) || SECRET.test(seg))) return false;
  return ROOT_ALLOWED.has(p) || ALLOWED.some(prefix => p.startsWith(prefix));
}
function sourceIdentity(st){return [st.dev,st.ino,st.size,st.mtimeMs,st.ctimeMs,st.nlink,st.mode].join(':');}
function boundedSourceRead(file,initial,p,budget){
  if(!initial.isFile()||initial.isSymbolicLink()||(initial.attributes&0x400)||initial.nlink!==1)fail('allowed_source_file_unsafe','allowed source is not an ordinary single-link file',[p]);
  if(initial.size>MAX_SOURCE_FILE_BYTES)fail('allowed_source_file_limit_exceeded','allowed source file exceeds 4 MiB',[p]);
  if(budget.files>=MAX_SOURCE_FILES||budget.bytes+initial.size>MAX_SOURCE_BYTES)fail('allowed_source_lane_limit_exceeded','allowed source lane exceeds file/byte limits',[p]);
  const fd=openSync(file,fsConstants.O_RDONLY|(fsConstants.O_NOFOLLOW||0));try{const before=fstatSync(fd);if(!before.isFile()||before.nlink!==1||sourceIdentity(before)!==sourceIdentity(initial))fail('allowed_source_changed_before_read','allowed source changed before bounded read',[p]);const bytes=Buffer.alloc(before.size);let off=0;while(off<bytes.length){const n=readSync(fd,bytes,off,Math.min(64*1024,bytes.length-off),off);if(n<=0)fail('allowed_source_short_read','allowed source read was incomplete',[p]);off+=n;}const after=fstatSync(fd),byPath=lstatSync(file);if(sourceIdentity(before)!==sourceIdentity(after)||sourceIdentity(after)!==sourceIdentity(byPath))fail('allowed_source_changed_during_read','allowed source changed during bounded read',[p]);budget.files++;budget.bytes+=bytes.length;return bytes;}finally{closeSync(fd);}
}
function liveSources(rootPath){
 const rootAttributes=path.join(rootPath,'.gitattributes');try{lstatSync(rootAttributes);fail('git_attributes_rejected','root .gitattributes is not supported');}catch(e){if(e.code!=='ENOENT')throw e;}
 const found=[],budget={files:0,bytes:0,entries:0},topNames=new Set([...ALLOWED.map(x=>x.split('/')[0]),...[...ROOT_ALLOWED].map(x=>x.split('/')[0])]);
 const visit=(abs,rel,depth)=>{if(depth>MAX_SOURCE_DEPTH)fail('allowed_source_depth_limit_exceeded','allowed-source depth exceeds 64',[rel]);const entries=readdirSync(abs,{withFileTypes:true});budget.entries+=entries.length;if(budget.entries>MAX_SOURCE_ENTRIES)fail('allowed_source_entry_limit_exceeded','allowed-source traversal exceeds 60000 entries',[rel]);for(const ent of entries){const p=`${rel}/${ent.name}`;if(p.split('/').some(seg=>NEVER.has(seg.toLowerCase())||SECRET.test(seg)))continue;if(ent.name.toLowerCase()==='.gitattributes')fail('git_attributes_rejected','working source tree contains a .gitattributes path',[p]);const full=path.join(abs,ent.name),st=lstatSync(full),relevant=ALLOWED.some(prefix=>prefix.startsWith(`${p}/`)||p===prefix.slice(0,-1)||p.startsWith(prefix))||ROOT_ALLOWED.has(p);if(ent.isSymbolicLink()||(st.attributes&0x400)){if(relevant)fail('allowed_source_link_unsafe','link/reparse encountered under allowed-source scope',[p]);continue;}if(ent.isDirectory()){if(relevant)visit(full,p,depth+1);continue;}if(!ent.isFile()||!sourcePathAllowed(p))continue;if(p.toLowerCase()==='.gitattributes'||p.toLowerCase().endsWith('/.gitattributes'))fail('git_attributes_rejected','attributes files are not allowed in managed selected merge',[p]);const bytes=boundedSourceRead(full,st,p,budget);found.push({path:p,sha256:sha(bytes),mode:(st.mode&0o111)?'100755':'100644',size:bytes.length});}};
 for(const top of topNames){const abs=path.join(rootPath,top);if(!existsSync(abs))continue;const st=lstatSync(abs);if(st.isSymbolicLink()||(st.attributes&0x400))fail('allowed_source_link_unsafe','allowed-source root is linked',[top]);if(st.isDirectory())visit(abs,top,1);else if(st.isFile()&&sourcePathAllowed(top)){const bytes=boundedSourceRead(abs,st,top,budget);found.push({path:top,sha256:sha(bytes),mode:(st.mode&0o111)?'100755':'100644',size:bytes.length});}}
 found.sort((a,b)=>a.path.localeCompare(b.path));const folded=new Set();for(const f of found){const k=f.path.toLocaleLowerCase('en-US');if(folded.has(k))fail('allowed_source_case_alias','allowed source has case-fold collision',[f.path]);folded.add(k);}return found;
}
function fullIndexEntries(git,cwd){
  const out=run(git,cwd,['ls-files','--stage','-z']); const rows=[];
  for(const row of out.toString('utf8').split('\0').filter(Boolean)){const tab=row.indexOf('\t'),meta=row.slice(0,tab).split(' '),p=row.slice(tab+1);if(tab<0||meta.length!==3||!/^\d{6}$/.test(meta[0])||!OID.test(meta[1])||!/^[0-3]$/.test(meta[2]))fail('git_index_malformed','index contains malformed entry',[p]);if(p.split('/').at(-1).toLowerCase()==='.gitattributes')fail('git_attributes_rejected','Git index contains a .gitattributes path',[p]);if(meta[2]!=='0')fail('git_index_conflict','index contains unresolved conflicts',[p]);rows.push({path:p,mode:meta[0],oid:meta[1],stage:meta[2]});}
  return rows.sort((a,b)=>a.path.localeCompare(b.path));
}
function stashMetadata(commonDir){
 let ref=null;const loose=path.join(commonDir,'refs','stash');if(existsSync(loose)){const st=lstatSync(loose);if(!st.isFile()||st.isSymbolicLink()||(st.attributes&0x400)||st.nlink!==1||st.size>4096)fail('stash_ref_unsafe','stash ref metadata is unsafe');const v=readFileSync(loose,'utf8').trim();if(!OID.test(v))fail('stash_ref_invalid','stash ref metadata is malformed');ref=v;}
 const packed=path.join(commonDir,'packed-refs');if(!ref&&existsSync(packed)){const st=lstatSync(packed);if(!st.isFile()||st.isSymbolicLink()||(st.attributes&0x400)||st.nlink!==1||st.size>4*1024*1024)fail('packed_refs_unsafe','packed refs metadata is unsafe');for(const line of readFileSync(packed,'utf8').split(/\r?\n/)){const m=/^([a-f0-9]{40}|[a-f0-9]{64}) refs\/stash$/.exec(line);if(m)ref=m[1];}}
 const log=path.join(commonDir,'logs','refs','stash');let reflog=[];if(existsSync(log)){const st=lstatSync(log);if(!st.isFile()||st.isSymbolicLink()||(st.attributes&0x400)||st.nlink!==1||st.size>4*1024*1024)fail('stash_reflog_unsafe','stash reflog metadata is unsafe');const raw=readFileSync(log,'utf8');for(const line of raw.split(/\r?\n/).filter(Boolean)){const m=/^([a-f0-9]{40}|[a-f0-9]{64}) ([a-f0-9]{40}|[a-f0-9]{64}) /.exec(line);if(!m)fail('stash_reflog_invalid','stash reflog metadata is malformed');reflog.push([m[1],m[2]]);}}
 return {refOid:ref,reflogOids:reflog};
}
function liveSnapshot(git,cwd,head,commonDir){
  const sources=liveSources(cwd), index=fullIndexEntries(git,cwd), tree=[...treeMap(git,cwd,head)].map(([p,v])=>({path:p,...v})).sort((a,b)=>a.path.localeCompare(b.path));
  return {sources,index,tree,stash:stashMetadata(commonDir)};
}
function preserveParts(snapshot,selected){const set=new Set(selected);return {sources:snapshot.sources.filter(x=>!set.has(x.path)),index:snapshot.index.filter(x=>!set.has(x.path)),tree:snapshot.tree.filter(x=>!set.has(x.path)),stash:snapshot.stash};}
function digestSeal(obj, key) { const value = structuredClone(obj); delete value[key]; return sha(Buffer.from(canonical(value))); }
function normalizeBlockers(review) {
  const map = new Map([['review_only_not_execution','preserve'],['stable_allowed_source_changes_require_preservation_review','preserve'],['sandbox_allowed_source_changes_require_ownership_review','exclude'],['complete_worktree_inventory_unavailable','exclude'],['committed_protected_or_out_of_scope_paths','exclude'],['cross_lane_case_alias_requires_review','review_required'],['selected_sandbox_source_metadata_incomplete','review_required'],['selected_stable_counterpart_absence_unproven','review_required'],['selected_stable_source_requires_preservation_review','review_required'],['selected_stable_source_metadata_incomplete','review_required']]);
  // Selected-content transactions exclude branch history and bind the complete
  // live source/index preservation snapshot instead of requiring an ahead range.
  map.set('empty_committed_range','exclude');
  map.set('stable_dirty_requires_preservation_review','preserve');
  map.set('sandbox_dirty_requires_ownership_review','exclude');
  const result=(review.blockers || []).map(b => { const disposition=map.get(b.code); if(!disposition) fail('selected_merge_unknown_review_blocker', `unknown review blocker: ${String(b.code)}`, b.paths||[]); return {code:b.code,paths:[...(b.paths||[])].sort(),disposition}; });
  const historyPaths=[];for(const item of review.history?.paths||[]){if(typeof item==='string')historyPaths.push(item);else if(record(item)){for(const k of ['path','oldPath','newPath'])if(typeof item[k]==='string')historyPaths.push(item[k]);if(Array.isArray(item.paths))historyPaths.push(...item.paths.filter(x=>typeof x==='string'));}}
  const omitted=[...new Set(historyPaths)].sort();if(omitted.length)result.push({code:'sandbox_history_excluded',paths:omitted,disposition:'exclude'});return result;
}
export function prepareManagedSelectedMerge({ stableRoot, sandboxRoot, checkpoint, gitExecutable: executable, preserveStableWorkingChanges = false } = {}) {
  let pack; try { pack = validateManagedSourceCheckpoint(checkpoint); } catch { fail('checkpoint_invalid', 'checkpoint failed canonical validation'); }
  if (typeof preserveStableWorkingChanges !== 'boolean') fail('stable_preservation_option_invalid', 'stable preservation is an explicit trusted-host boolean option');
  const stable = root(stableRoot, 'stable'), sandbox = root(sandboxRoot, 'sandbox'), git = gitExecutable(executable);
  const review = pack.review;
  if (stable !== review.stable.identity.worktreeRoot || sandbox !== review.sandbox.identity.worktreeRoot) fail('checkpoint_root_mismatch', 'roots do not exactly match checkpoint identities');
  if (path.resolve(stable, '.worktrees', 'sandbox') !== sandbox || path.basename(sandbox) !== 'sandbox' || path.basename(path.dirname(sandbox)) !== '.worktrees') fail('selected_merge_worktree_layout_invalid', 'sandbox must be the exact stable/.worktrees/sandbox lane');
  const gitGuard=guardBeforeGit(git,[stable,sandbox]);
  const s = state(git, stable), b = state(git, sandbox);
  if (s.gitDir !== path.resolve(review.stable.identity.gitDir) || s.commonDir !== path.resolve(review.stable.identity.commonDir) || s.head !== review.stable.head || b.gitDir !== path.resolve(review.sandbox.identity.gitDir) || b.commonDir !== path.resolve(review.sandbox.identity.commonDir) || b.head !== review.sandbox.head) fail('checkpoint_git_identity_drift', 'Git roots, common directories, or HEAD differ from checkpoint');
  if (s.commonDir !== b.commonDir || s.gitDir === b.gitDir) fail('selected_merge_worktree_identity_invalid', 'stable and sandbox must be linked worktrees with shared commonDir and distinct gitDir');
  if (b.branch !== 'experiment/sandbox') fail('selected_merge_sandbox_branch_invalid', 'sandbox branch must be experiment/sandbox');
  const selectedPaths = [...pack.request.selectedPaths].sort();
  const req = validateManagedSelectionRequest({ format: 1, mode: 'managed_source_selection_request_v1', reviewDigest: review.review_digest, selectedPaths });
  const selectedSet = new Set(req.selectedPaths);
  const currentTree = treeMap(git, stable, s.head), currentIndex = indexMap(git, stable);
  const temporaryIndexUpperBoundBytes=estimateTemporaryIndexUpperBound(currentTree,req.selectedPaths);if(temporaryIndexUpperBoundBytes>MAX_TEMP_INDEX_BYTES)fail('selected_merge_temp_index_bound_exceeded','conservative temporary-index upper bound exceeds 4 MiB');
  const files = [], preservedFiles = [];
  let preservedRawBytes = 0;
  for (const f of pack.files) {
    if (!safeSelectedPath(f.path)) fail('selected_path_unsafe', 'selected path is protected or outside source allowlist', [f.path]);
    const folded=f.path.toLocaleLowerCase('en-US'); const aliases=[...currentTree.keys(),...currentIndex.keys()].filter(p=>p!==f.path&&p.toLocaleLowerCase('en-US')===folded); if(aliases.length)fail('selected_path_case_alias','selected path has a case-folded tree/index alias',[f.path,...aliases]);
    const old = currentTree.get(f.path) || null, idx = currentIndex.get(f.path) || null;
    if ((!old && idx) || (old && (old.mode !== idx?.mode || old.oid !== idx?.oid))) fail('selected_stable_staged_dirty', 'selected stable path has staged changes or an index-only addition', [f.path]);
    let current = null;
    const abs = path.join(stable, ...f.path.split('/'));
    for (let i = 1; i < f.path.split('/').length; i++) { const parent = path.join(stable, ...f.path.split('/').slice(0, i)); if (existsSync(parent)) { const ps = lstatSync(parent); if (!ps.isDirectory() || ps.isSymbolicLink() || (ps.attributes & 0x400)) fail('selected_stable_unsafe_ancestor', 'selected path ancestor is not a safe directory', [f.path]); } }
    try {
      const st = lstatSync(abs);
      if (st.isSymbolicLink() || (st.attributes & 0x400)) fail('selected_stable_unsafe_type', 'selected stable path or ancestor is linked', [f.path]);
      if (!st.isFile() || st.nlink !== 1) fail('selected_stable_unsafe_type', 'selected stable path is not an ordinary file', [f.path]);
      current = boundedSourceRead(abs, st, f.path, {files:0,bytes:0});
    } catch (e) { if (e.code !== 'ENOENT') throw e; }
    if (old && !current) fail('selected_stable_missing', 'selected stable tracked path is missing', [f.path]);
    if (!old && current) fail('selected_untracked_collision', 'new selected path already exists in stable working tree', [f.path]);
    if (old && current) {
      const headBytes = run(git, stable, ['cat-file', 'blob', old.oid]);
      const actualMode=(lstatSync(abs).mode&0o111)?'100755':'100644';
      if (actualMode !== old.mode) fail('selected_stable_working_dirty', 'selected stable mode differs from HEAD; mode reconciliation requires separate review', [f.path]);
      if (!current.equals(headBytes)) {
        if (!preserveStableWorkingChanges) fail('selected_stable_working_dirty', 'selected stable working bytes differ from HEAD; explicit native preservation is required', [f.path]);
        preservedRawBytes += current.length;
        if (current.length > MAX_SOURCE_FILE_BYTES || preservedRawBytes > 8*1024*1024) fail('stable_preservation_bytes_exceeded', 'stable preservation exceeds its 4 MiB file / 8 MiB aggregate bounds');
        const oid = objectOid(s.head.length === 64 ? 'sha256' : 'sha1', 'blob', current);
        preservedFiles.push({path:f.path,sha256:sha(current),oid,mode:actualMode,bytesBase64:current.toString('base64')});
      }
    }
    for (let i = 1; i < f.path.split('/').length; i++) { const parent = path.join(stable, ...f.path.split('/').slice(0, i)); if (existsSync(parent)) { const ps = lstatSync(parent); if (!ps.isDirectory() || ps.isSymbolicLink() || (ps.attributes & 0x400)) fail('selected_stable_unsafe_ancestor', 'selected path ancestor is not a safe directory', [f.path]); } }
    const sandboxFile = path.join(sandbox, ...f.path.split('/')); let sandboxRaw; try { const ss=lstatSync(sandboxFile); if(!ss.isFile()||ss.isSymbolicLink()||(ss.attributes&0x400)||ss.nlink!==1) fail('sandbox_selected_source_unsafe','sandbox selected source is unsafe',[f.path]); sandboxRaw=readFileSync(sandboxFile); } catch(e) { if(e.code==='ENOENT') fail('sandbox_selected_source_missing','sandbox selected source is missing',[f.path]); throw e; } const raw=Buffer.from(f.bytesBase64,'base64'); if(!sandboxRaw.equals(raw)) fail('sandbox_selected_source_drift','sandbox selected bytes differ from checkpoint',[f.path]);
    files.push({ path: f.path, action: old ? 'replace' : 'add', rawSha256: sha(raw), mode: f.mode, expectedBaseOid: old?.oid ?? null, beforeSha256: current ? sha(current) : null, expectedBaseMode: old?.mode ?? null, workingOid: f.workingOid });
  }
  if (s.commonDir !== b.commonDir || s.gitDir === b.gitDir) fail('selected_merge_worktree_identity_invalid', 'stable and sandbox must share commonDir and have distinct gitDir');
  if (b.branch !== 'experiment/sandbox') fail('selected_merge_sandbox_branch_invalid', 'sandbox branch must be experiment/sandbox');
  const sandboxIndexBefore = b.rawIndexSha256;
  const stableSnapshot=liveSnapshot(git,stable,s.head,s.commonDir), sandboxSnapshot=liveSnapshot(git,sandbox,b.head,b.commonDir);
  const preserveBasis={stable:preserveParts(stableSnapshot,selectedPaths),sandbox:sandboxSnapshot};
  const preservationDigest = sha(Buffer.from(canonical(preserveBasis)));
  const expectedMergedTree = expectedTree(git, stable, s.head, files);
  const format = text(run(git, stable, ['rev-parse', '--show-object-format']));
  const author = 'Managed Selected Merge <managed-selected-merge@invalid> 0 +0000';
  let stablePreservation;
  if (preservedFiles.length) {
    const preservationTree = expectedTree(git, stable, s.head, preservedFiles.map(f=>({...f,workingOid:f.oid})));
    const body = Buffer.from(`tree ${preservationTree}\nparent ${s.head}\nauthor ${author}\ncommitter ${author}\n\nPreserve stable working bytes for checkpoint ${pack.checkpointDigest}\n`);
    stablePreservation = {mode:'managed_stable_working_preservation_v1',files:preservedFiles,rawBytes:preservedRawBytes,expectedTree:preservationTree,expectedCommit:objectOid(format,'commit',body)};
  }
  const commitBody = Buffer.from(`tree ${expectedMergedTree}\nparent ${stablePreservation?.expectedCommit ?? s.head}\nauthor ${author}\ncommitter ${author}\n\nSelected content from checkpoint ${pack.checkpointDigest}\n`);
  const expectedCommit = objectOid(format, 'commit', commitBody);
  const blockers=normalizeBlockers(review);
  for (const blocker of blockers) {
    if (stablePreservation && blocker.code==='selected_stable_source_requires_preservation_review' && blocker.paths.length && blocker.paths.every(p=>preservedFiles.some(f=>f.path===p))) blocker.disposition='preserve';
  }
  if(blockers.some(b=>b.disposition==='review_required'))fail('selected_merge_review_required','review contains a blocker requiring a separate human review',blockers.filter(b=>b.disposition==='review_required').flatMap(b=>b.paths));
  const plan = { format: 1, mode: 'managed_selected_merge_plan_v1', checkpointDigest: pack.checkpointDigest, stable: s, sandbox: b, selectedPaths: req.selectedPaths, files: files.map(({workingOid,...f})=>f), preservationDigest, expectedMergedTree, expectedCommit, sourceHistoryMerged: false, gitMetadataDigest:gitGuard.metadataDigest, preservationBasis: preserveBasis, temporaryIndexUpperBoundBytes, blockers, execution: false, readyForLaunch: false };
  if (stablePreservation) plan.stablePreservation = stablePreservation;
  if (Buffer.byteLength(canonical(plan),'utf8') > 16*1024*1024) fail('selected_merge_plan_bytes_exceeded','canonical merge plan exceeds 16 MiB');
  plan.planDigest = digestSeal(plan, 'planDigest');
  return Object.freeze(plan);
}
function writeIntent(dir, plan) {
  mkdirSync(dir, { recursive: true }); const file = path.join(dir, 'intent.json');
  const fd = openSync(file, fsConstants.O_CREAT | fsConstants.O_EXCL | fsConstants.O_WRONLY, 0o600);
  try { const bytes = Buffer.from(`${canonical({ format: 1, mode: 'managed_selected_merge_intent_v1', planDigest: plan.planDigest, checkpointDigest: plan.checkpointDigest, state: 'started' })}\n`); writeSync(fd, bytes); fsyncSync(fd); } finally { closeSync(fd); }
  return file;
}
function samePlanState(plan, stableRoot, sandboxRoot, git, checkpoint, phase) {
  const gitGuard=guardBeforeGit(git,[stableRoot,sandboxRoot]);if(gitGuard.metadataDigest!==plan.gitMetadataDigest)fail('selected_merge_git_metadata_drift','local Git config or .git marker changed since plan');
  const s=state(git,stableRoot),b=state(git,sandboxRoot);
  const expectedHead=phase?.head ?? plan.stable.head;
  const originalStable={...s,head:plan.stable.head,rawIndexSha256:plan.stable.rawIndexSha256};
  if(canonical(originalStable)!==canonical(plan.stable)||s.head!==expectedHead||(!phase?.indexChanged&&s.rawIndexSha256!==plan.stable.rawIndexSha256)||canonical(b)!==canonical(plan.sandbox))fail('selected_merge_metadata_drift','stable/sandbox Git identity or original index changed outside the approved preservation phase');
  const ss=liveSnapshot(git,stableRoot,s.head,s.commonDir),bs=liveSnapshot(git,sandboxRoot,b.head,b.commonDir);
  if(canonical(preserveParts(ss,plan.selectedPaths))!==canonical(plan.preservationBasis.stable)||canonical(bs)!==canonical(plan.preservationBasis.sandbox))fail('selected_merge_preservation_drift','allowed-source bytes/modes, tree metadata, or complete index entries changed');
  const pack=validateManagedSourceCheckpoint(checkpoint), idx=indexMap(git,stableRoot);
  for(const f of plan.files){
    const abs=path.join(stableRoot,...f.path.split('/'));let bytes=null,mode=null;
    try{const st=lstatSync(abs);if(!st.isFile()||st.isSymbolicLink()||(st.attributes&0x400)||st.nlink!==1)fail('selected_merge_selected_source_unsafe','selected stable file became unsafe',[f.path]);bytes=readFileSync(abs);mode=(st.mode&0o111)?'100755':'100644';}catch(e){if(e.code!=='ENOENT')throw e;}
    if((bytes?sha(bytes):null)!==f.beforeSha256||mode!==f.expectedBaseMode)fail('selected_merge_selected_overlap','selected stable source changed since plan',[f.path]);
    const preserved=phase?.stagedPaths?.has(f.path) ? plan.stablePreservation?.files.find(p=>p.path===f.path) : null;
    const expectedOid=preserved?.oid ?? f.expectedBaseOid;
    const expectedMode=preserved?.mode ?? f.expectedBaseMode;
    const actual=idx.get(f.path);
    if ((actual?.oid ?? null)!==expectedOid||(actual?.mode ?? null)!==expectedMode) fail('selected_merge_selected_index_drift','selected index entry changed outside the approved preservation phase',[f.path]);
  }
  for(const f of pack.files){const abs=path.join(sandboxRoot,...f.path.split('/'));const bytes=readFileSync(abs);if(sha(bytes)!==f.workingSha256)fail('sandbox_selected_source_drift','selected sandbox bytes differ from checkpoint',[f.path]);}
  return {stable:s,sandbox:b};
}
function selectedWorkingPath(rootPath,p){return path.join(rootPath,...p.split('/'));}
function writeArtifactFile(file,bytes){const fd=openSync(file,fsConstants.O_CREAT|fsConstants.O_EXCL|fsConstants.O_WRONLY,0o600);try{writeSync(fd,bytes);fsyncSync(fd);}finally{closeSync(fd);}}
function createPlannedCommit(git,stable,indexPath,tree,parent,message,expected) {
  const env=envFor(stable,indexPath);env.GIT_AUTHOR_NAME='Managed Selected Merge';env.GIT_AUTHOR_EMAIL='managed-selected-merge@invalid';env.GIT_COMMITTER_NAME=env.GIT_AUTHOR_NAME;env.GIT_COMMITTER_EMAIL=env.GIT_AUTHOR_EMAIL;env.GIT_AUTHOR_DATE='@0 +0000';env.GIT_COMMITTER_DATE='@0 +0000';
  const commit=spawnSync(git,['-c',`safe.directory=${stable}`,'-c','protocol.allow=never','-c','gc.auto=0','-c','gc.autoDetach=false','-c','maintenance.auto=false','-c','commit.gpgSign=false','-c',`core.hooksPath=${process.platform==='win32'?'NUL':'/dev/null'}`,'commit-tree',tree,'-p',parent],{cwd:stable,env,input:Buffer.from(message),encoding:'buffer',windowsHide:true,timeout:20000,maxBuffer:1024*1024});
  if(commit.error||commit.status!==0||text(commit.stdout)!==expected)fail('selected_merge_commit_mismatch','commit identity differs from the canonical native-confirmed plan');
}
/** Read-only identity guard shared with postmerge provenance capture. */
export function inspectManagedSelectedMergeSourceIdentity({stableRoot,sandboxRoot,gitExecutable:executable}={}) {
  const git=gitExecutable(executable),stable=root(stableRoot,'stable'),sandbox=root(sandboxRoot,'sandbox');
  const guarded=guardBeforeGit(git,[stable,sandbox]);
  const stableState=state(git,stable),sandboxState=state(git,sandbox);
  if(stableState.commonDir!==sandboxState.commonDir || sandbox!==path.join(stable,'.worktrees','sandbox') || sandboxState.branch!=='experiment/sandbox') fail('selected_merge_source_identity_invalid','managed source worktrees do not share the expected identity');
  return {stable:stableState,sandbox:sandboxState,gitMetadataDigest:guarded.metadataDigest};
}

export function executeManagedSelectedMerge({ plan, checkpoint, stableRoot, sandboxRoot, gitExecutable: executable, directory, assertTrusted } = {}) {
  if (!record(plan) || plan.mode !== 'managed_selected_merge_plan_v1' || plan.planDigest !== digestSeal(plan, 'planDigest') || plan.execution !== false || plan.readyForLaunch !== false) fail('selected_merge_plan_invalid', 'plan is invalid or launchable');
  if(Array.isArray(plan.blockers)&&plan.blockers.some(b=>b?.disposition==='review_required'))fail('selected_merge_review_required','plan contains a blocker requiring separate human review');
  if (typeof assertTrusted !== 'function' || assertTrusted() !== true) fail('selected_merge_trust_denied', 'trusted native host did not authorize this operation');
  const git=gitExecutable(executable), stable=root(stableRoot,'stable'), sandbox=root(sandboxRoot,'sandbox');
  const fresh=prepareManagedSelectedMerge({stableRoot:stable,sandboxRoot:sandbox,checkpoint,gitExecutable:git,preserveStableWorkingChanges:Boolean(plan.stablePreservation)});
  if(canonical(fresh)!==canonical(plan))fail('selected_merge_plan_stale','fresh canonical plan differs; operation was not started');
  if(assertTrusted()!==true)fail('selected_merge_trust_denied','trusted host revoked authority after final preparation');
  const durableBundle={checkpoint,stableIndex:readFileSync(path.join(plan.stable.gitDir,'index'))};
  const store=claimSelectedMergeStore({sandboxRoot:sandbox,plan,assertTrusted,durableBundle});
  if(directory&&path.resolve(directory)!==store.directory){store.fail('selected_merge_intent_path_invalid');fail('selected_merge_intent_path_invalid','store returned unexpected intent path');}
  const dir=store.directory,idxPath=path.join(dir,'temporary.index');let commandStarted=false;
  const phase={head:plan.stable.head,indexChanged:false,stagedPaths:new Set()};
  const boundary=()=>{samePlanState(plan,stable,sandbox,git,checkpoint,phase);if(assertTrusted()!==true)fail('selected_merge_trust_denied','trusted native host revoked the write boundary');};
  try{
    if (plan.stablePreservation) {
      const p=plan.stablePreservation;
      boundary();run(git,stable,['read-tree',plan.stable.head],idxPath);assertTemporaryIndexBound(idxPath);
      for (const f of p.files) {
        boundary();const bytes=Buffer.from(f.bytesBase64,'base64');const oid=text(run(git,stable,['hash-object','--no-filters','-w','--stdin'],undefined,bytes));
        if(oid!==f.oid||sha(bytes)!==f.sha256)fail('stable_preservation_blob_mismatch','stable original bytes did not produce the planned preservation blob',[f.path]);
        boundary();run(git,stable,['update-index','--add','--cacheinfo',`${f.mode},${oid},${f.path}`],idxPath);assertTemporaryIndexBound(idxPath);
      }
      boundary();const tree=text(run(git,stable,['write-tree'],idxPath));assertTemporaryIndexBound(idxPath);
      if(tree!==p.expectedTree)fail('stable_preservation_tree_mismatch','stable preservation tree differs from native-confirmed plan');
      boundary();createPlannedCommit(git,stable,idxPath,tree,plan.stable.head,`Preserve stable working bytes for checkpoint ${plan.checkpointDigest}\n`,p.expectedCommit);
      // Only explicitly selected original bytes enter the real index. No broad
      // add/stash/reset/checkout or direct working-file overwrite is permitted.
      for (const f of p.files) {
        boundary();commandStarted=true;run(git,stable,['update-index','--cacheinfo',`${f.mode},${f.oid},${f.path}`]);
        phase.indexChanged=true;phase.stagedPaths.add(f.path);
      }
      boundary();run(git,stable,['merge','--ff-only','--no-edit',p.expectedCommit]);phase.head=p.expectedCommit;
      boundary();
    }
    boundary();run(git,stable,['read-tree',phase.head],idxPath);assertTemporaryIndexBound(idxPath);
    for(const f of validateManagedSourceCheckpoint(checkpoint).files){
      boundary();const bytes=Buffer.from(f.bytesBase64,'base64');const oid=text(run(git,stable,['hash-object','--no-filters','-w','--stdin'],undefined,bytes));
      boundary();run(git,stable,['update-index','--add','--cacheinfo',`${f.mode},${oid},${f.path}`],idxPath);assertTemporaryIndexBound(idxPath);
    }
    boundary();const tree=text(run(git,stable,['write-tree'],idxPath));assertTemporaryIndexBound(idxPath);if(tree!==plan.expectedMergedTree)fail('selected_merge_tree_mismatch','temporary index differs from exact planned selected-content tree');
    boundary();createPlannedCommit(git,stable,idxPath,tree,phase.head,`Selected content from checkpoint ${plan.checkpointDigest}\n`,plan.expectedCommit);
    boundary();assertTemporaryIndexBound(idxPath);if(assertTrusted()!==true)fail('selected_merge_trust_denied','trusted host revoked authority immediately before ff-only merge');commandStarted=true;
    run(git,stable,['merge','--ff-only','--no-edit',plan.expectedCommit]);
    const after=state(git,stable),afterSandbox=state(git,sandbox),stableSnap=liveSnapshot(git,stable,after.head,after.commonDir),sandboxSnap=liveSnapshot(git,sandbox,afterSandbox.head,afterSandbox.commonDir),idx=indexMap(git,stable),pack=validateManagedSourceCheckpoint(checkpoint);
    if(after.head!==plan.expectedCommit||text(run(git,stable,['rev-parse','HEAD^{tree}']))!==plan.expectedMergedTree||afterSandbox.head!==plan.sandbox.head||afterSandbox.rawIndexSha256!==plan.sandbox.rawIndexSha256||canonical(preserveParts(stableSnap,plan.selectedPaths))!==canonical(plan.preservationBasis.stable)||canonical(sandboxSnap)!==canonical(plan.preservationBasis.sandbox))fail('selected_merge_postcondition_failed','merge or full unselected/sandbox preservation verification failed');
    for(const f of pack.files){const abs=selectedWorkingPath(stable,f.path),st=lstatSync(abs),raw=readFileSync(abs),entry=idx.get(f.path);if(!st.isFile()||st.isSymbolicLink()||(st.attributes&0x400)||sha(raw)!==f.workingSha256||((st.mode&0o111)?'100755':'100644')!==f.mode||entry?.oid!==f.workingOid||entry?.mode!==f.mode)fail('selected_merge_selected_postcondition_failed','selected working bytes/mode and real index oid/mode did not verify',[f.path]);}
    const receipt={format:1,mode:'managed_selected_merge_receipt_v1',status:'source_merge_verified',planDigest:plan.planDigest,checkpointDigest:plan.checkpointDigest,selectedPaths:[...plan.selectedPaths],stableHeadBefore:plan.stable.head,stableHeadAfter:after.head,mergedTree:plan.expectedMergedTree,sourceMergeExecuted:true,sourceHistoryMerged:false,readyForLaunch:false};receipt.receiptDigest=sha(Buffer.from(canonical(receipt)));
    if(assertTrusted()!==true)fail('selected_merge_trust_denied','trusted host revoked authority before success receipt');store.finish(receipt);return receipt;
  }catch(e){const code=/^[a-z][a-z0-9_]{0,63}$/.test(e.code||'')?e.code:(commandStarted?'selected_merge_result_unknown':'selected_merge_precondition_failed');try{store.fail(code);}catch{}e.receipt={format:1,mode:'managed_selected_merge_receipt_v1',status:'maintenance_required',planDigest:plan.planDigest,checkpointDigest:plan.checkpointDigest,maintenanceRequired:true,errorCode:code,intentPath:store.intentPath,execution:commandStarted,readyForLaunch:false};throw e;}
}
