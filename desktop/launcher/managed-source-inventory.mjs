import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { constants as fsConstants, lstatSync, realpathSync, readdirSync, openSync, fstatSync, readSync, closeSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { TextDecoder } from 'node:util';

const FORMAT = 1;
const MODE = 'managed_allowed_source_inventory_v1';
const MAX_GIT_OUTPUT = 24 * 1024 * 1024;
const MAX_RECORDS = 60000;
const MAX_METADATA_BYTES = 16 * 1024 * 1024;
const MAX_WORKING_FILES = 20000;
const MAX_WORKING_BYTES = 64 * 1024 * 1024;
const MAX_SINGLE_FILE = 4 * 1024 * 1024;
const PREFIXES = ['agent-core/', 'desktop/', 'docs/', 'protocol/', 'scripts/', 'evolve/tools/', 'evolve/skills/', 'evolve/tool-catalog/'];
const ROOT_FILES = new Set(['AGENTS.md', '.gitignore', 'start-desktop.bat', 'init-sandbox.bat', 'check-stable-before-sandbox-merge.bat', 'sync-sandbox-worktree.bat']);
const NEVER = new Set(['.git', '.venv', 'venv', 'node_modules', 'data', 'artifacts', 'dist', 'dist-electron', '__pycache__', 'profile', 'profiles', 'user-data', 'workspace', 'workspaces', '.managed-runtime']);
const SECRET = /^(?:\.env(?:\..*)?|llm_secrets\.(?:json|ya?ml)|llm_models\.(?:json|ya?ml)|.*\.(?:pem|key|p12|pfx|cer|crt))$/i;
const SAFE = /^[A-Za-z0-9_@+.,= -]+$/;
const HASH = /^[a-f0-9]{64}$/i;
const oidRe = /^[a-f0-9]{40}$|^[a-f0-9]{64}$/i;
const decoder = new TextDecoder('utf-8', { fatal: true });
const sha256 = b => createHash('sha256').update(b).digest('hex');
function canonical(v) { if (Array.isArray(v)) return `[${v.map(canonical).join(',')}]`; if (v && typeof v === 'object') return `{${Object.keys(v).sort().map(k => `${JSON.stringify(k)}:${canonical(v[k])}`).join(',')}}`; return JSON.stringify(v); }
function utf8(bytes) { try { return decoder.decode(bytes); } catch { throw new Error('Git metadata contains invalid UTF-8'); } }
function sameFileIdentity(a, b) { return a.dev === b.dev && a.ino === b.ino && a.size === b.size && a.mtimeNs === b.mtimeNs && a.ctimeNs === b.ctimeNs && a.nlink === b.nlink && a.mode === b.mode; }
function readStableFile(file, initialStat, maxBytes, requireSingleLink = false) {
 if (!initialStat.isFile() || !Number.isSafeInteger(initialStat.dev) || !Number.isSafeInteger(initialStat.ino) || !Number.isSafeInteger(initialStat.size) || (initialStat.attributes & 0x400) || initialStat.size > maxBytes || (requireSingleLink && initialStat.nlink > 1)) throw new Error('file type or size is unsafe');
 const flags = fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW || 0); let fd;
 try {
  fd = openSync(file, flags);
  const before = fstatSync(fd, { bigint: true });
  if (!before.isFile() || before.size > BigInt(maxBytes) || (requireSingleLink && before.nlink > 1n) ||
      before.dev !== BigInt(initialStat.dev) || before.ino !== BigInt(initialStat.ino) || before.size !== BigInt(initialStat.size)) throw new Error('file changed before bounded read');
  const capacity = Math.min(maxBytes + 1, Number(before.size) + 1); const buffer = Buffer.alloc(capacity); let offset = 0;
  while (offset < capacity) { const count = readSync(fd, buffer, offset, capacity - offset, offset); if (!count) break; offset += count; }
  const after = fstatSync(fd, { bigint: true });
  if (!sameFileIdentity(before, after) || offset !== Number(before.size) || offset > maxBytes) throw new Error('file changed during bounded read');
  return buffer.subarray(0, offset);
 } finally { if (fd !== undefined) closeSync(fd); }
}
function rootCheck(input) {
 if (typeof input !== 'string' || !path.isAbsolute(input)) throw new Error('root must be absolute');
 const root = path.resolve(input); let cur = path.parse(root).root;
 for (const part of root.slice(cur.length).split(path.sep).filter(Boolean)) { cur = path.join(cur, part); let st; try { st=lstatSync(cur); } catch { throw new Error('root missing or inaccessible'); } if (st.isSymbolicLink() || (st.attributes & 0x400)) throw new Error('root contains a link or reparse point'); }
 if (!lstatSync(root).isDirectory() || path.resolve(realpathSync.native(root)) !== root) throw new Error('root is not a real directory');
 return root;
}
function git(root, args) {
 const env = { PATH:process.env.PATH||'', SYSTEMROOT:process.env.SYSTEMROOT||'', WINDIR:process.env.WINDIR||'', HOME:path.join(os.tmpdir(),'codex-managed-inventory-empty-home'), USERPROFILE:path.join(os.tmpdir(),'codex-managed-inventory-empty-home'), GIT_CONFIG_NOSYSTEM:'1', GIT_TERMINAL_PROMPT:'0', GIT_OPTIONAL_LOCKS:'0', LC_ALL:'C' };
 const fixed=['--no-optional-locks','--literal-pathspecs','-c','core.fsmonitor=false','-c',`safe.directory=${root}`,'-c','core.pager=cat','-c','diff.external=','-c','submodule.recurse=false','-c','diff.ignoreSubmodules=all','-c',`core.attributesFile=${process.platform==='win32'?'NUL':'/dev/null'}`,'--no-pager'];
 const r=spawnSync('git',[...fixed,...args],{cwd:root,env,encoding:'buffer',windowsHide:true,timeout:12000,maxBuffer:MAX_GIT_OUTPUT,stdio:['ignore','pipe','pipe']});
 if(r.error||r.status!==0) throw new Error(`metadata Git command failed safely (${r.error?.code||r.status})`); return r.stdout;
}
function isDevice(seg) { return /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i.test(seg); }
function safeRel(p) { if(!p||p.startsWith('/')||p.includes('\\')) return false; const parts=p.split('/'); return parts.every(s=>s&&s!=='.'&&s!=='..'&&SAFE.test(s)&&!/[. ]$/.test(s)&&!isDevice(s)); }
function protectedPath(p) { return p.split('/').some(s=>NEVER.has(s.toLowerCase())||SECRET.test(s)); }
function allowed(p) { return safeRel(p)&&!protectedPath(p)&&(ROOT_FILES.has(p)||PREFIXES.some(x=>p.startsWith(x))); }
function parseNul(bytes) { const s=utf8(bytes); if(s && !s.endsWith('\0')) throw new Error('metadata NUL framing is incomplete'); return s ? s.slice(0,-1).split('\0') : []; }
function checkPathSet(paths) {
 const seen=new Map(); for(const p of paths) { if(!safeRel(p)) throw new Error('unsafe or aliased Git path metadata'); const key=p.toLocaleLowerCase('en-US'); const prior=seen.get(key); if(prior && prior!==p) throw new Error('casefold path collision in Git metadata'); seen.set(key,p); }
}
function gitRoot(root) { const top=path.resolve(utf8(git(root,['rev-parse','--show-toplevel'])).trim()); if(top!==root) throw new Error('root is not Git worktree root'); return top; }
function repoState(root) {
 gitRoot(root);
 const head=utf8(git(root,['rev-parse','--verify','HEAD'])).trim(); if(!oidRe.test(head)) throw new Error('HEAD identity is invalid');
 const gitDir=path.resolve(root,utf8(git(root,['rev-parse','--path-format=absolute','--git-dir'])).trim());
 const gd=lstatSync(gitDir); if(!gd.isDirectory()||gd.isSymbolicLink()||(gd.attributes&0x400)) throw new Error('Git directory is linked or invalid');
 const indexPath=path.join(gitDir,'index'); let indexDigest=null; try { const st=lstatSync(indexPath); if(st.isSymbolicLink()||(st.attributes&0x400)||!st.isFile()) throw new Error('index is linked or invalid'); indexDigest=sha256(readStableFile(indexPath,st,40*1024*1024)); } catch(e) { if(e.code!=='ENOENT') throw e; }
 return {head,indexDigest};
}
function parseIndex(root) {
 const bytes=git(root,['ls-files','--stage','-z']); if(bytes.length>MAX_METADATA_BYTES) throw new Error('index metadata limit exceeded');
 const rows=parseNul(bytes); if(rows.length>MAX_RECORDS) throw new Error('index entry limit exceeded'); const map=new Map(); const names=[];
 for(const row of rows) { const tab=row.indexOf('\t'); const meta=row.slice(0,tab).split(' '); if(tab<0||meta.length!==3||!/^\d{6}$/.test(meta[0])||!oidRe.test(meta[1])||!/^[0-3]$/.test(meta[2])) throw new Error('invalid index metadata'); const p=row.slice(tab+1); names.push(p); if(meta[2]!=='0') throw new Error('index contains unresolved conflict stages'); if(!['100644','100755'].includes(meta[0])) throw new Error('unsupported index mode'); if(map.has(p)) throw new Error('duplicate index path'); map.set(p,{mode:meta[0],oid:meta[1]}); }
 checkPathSet(names); return map;
}
function parseTree(root, rev) {
 const bytes=git(root,['ls-tree','-rz','--full-tree',rev]); if(bytes.length>MAX_METADATA_BYTES) throw new Error('tree metadata limit exceeded');
 const rows=parseNul(bytes); if(rows.length>MAX_RECORDS) throw new Error('tree entry limit exceeded'); const map=new Map(),names=[];
 for(const row of rows) { const tab=row.indexOf('\t'), m=row.slice(0,tab).split(' '); if(tab<0||m.length!==3||!/^\d{6}$/.test(m[0])||!oidRe.test(m[2])) throw new Error('invalid tree metadata'); const p=row.slice(tab+1); names.push(p); if(!['100644','100755'].includes(m[0])) throw new Error('unsupported tree mode'); map.set(p,{mode:m[0],oid:m[2],type:m[1]}); }
 checkPathSet(names); return map;
}
function safeWorking(root,p, budget) {
 const parts=p.split('/'); let cur=root;
 for(let i=0;i<parts.length;i++) { cur=path.join(cur,parts[i]); let st; try{st=lstatSync(cur);}catch(e){if(e.code==='ENOENT')return {missing:true};throw new Error('working path cannot be inspected safely');}
   if(st.isSymbolicLink()||(st.attributes&0x400)) throw new Error('link/reparse point found under an allowed source path');
   if(i<parts.length-1&&!st.isDirectory()) return {missing:true};
   if(i===parts.length-1) { if(!st.isFile()) throw new Error('allowed source is not a regular file'); if(st.nlink>1) throw new Error('hardlinked allowed source file rejected'); if(st.size>MAX_SINGLE_FILE||budget.bytes+st.size>MAX_WORKING_BYTES) throw new Error('working source byte limit exceeded'); if(budget.files>=MAX_WORKING_FILES) throw new Error('working source file limit exceeded'); const bytes=readStableFile(cur,st,MAX_SINGLE_FILE,true); budget.bytes+=bytes.length; budget.files++; return {bytes,size:bytes.length,mode:(st.mode&0o111)?'100755':'100644'}; }
 }
 return {missing:true};
}
function relevantDirectory(p) { return PREFIXES.some(x => x.startsWith(p + '/') || p === x.slice(0, -1) || p.startsWith(x)); }
function enumerateAllowed(root,budget) {
 const found=new Set(); let traversed=0; const encountered=[]; const visit=(dir,rel)=>{
   let names; try{names=readdirSync(dir,{withFileTypes:true});}catch{throw new Error('allowed source directory cannot be enumerated safely');}
   for(const ent of names) { if(++traversed>MAX_RECORDS) throw new Error('directory traversal entry limit exceeded'); const p=rel?`${rel}/${ent.name}`:ent.name;
     if(!safeRel(p)) throw new Error('unsafe or aliased working path'); encountered.push(p); if(protectedPath(p)) continue;
     let childStat; try { childStat=lstatSync(path.join(dir,ent.name)); } catch { throw new Error('working path cannot be inspected safely'); }
     if(ent.isSymbolicLink()||(childStat.attributes&0x400)) { if(allowed(p)||relevantDirectory(p)) throw new Error('link/reparse point found under an allowed source path'); continue; }
     if(ent.isDirectory()) { if(relevantDirectory(p)) visit(path.join(dir,ent.name),p); continue; }
     if(ent.isFile()&&allowed(p)) found.add(p);
   }
 };
 for(const top of new Set(PREFIXES.map(prefix => prefix.split('/')[0]))) { const abs=path.join(root,top); try {const st=lstatSync(abs); if(st.isSymbolicLink()||(st.attributes&0x400)) throw new Error('allowed source root is linked'); if(st.isDirectory()) visit(abs,top); } catch(e){if(e.code!=='ENOENT') throw e;} }
 for(const p of ROOT_FILES) { try{const st=lstatSync(path.join(root,p)); if(st.isSymbolicLink()||(st.attributes&0x400)) throw new Error('allowed root file is linked'); if(st.isFile()) found.add(p);}catch(e){if(e.code!=='ENOENT')throw e;} }
 return found;
}
function blobOid(bytes, format) { const name=format==='sha256'?'sha256':'sha1'; return createHash(name).update(Buffer.from(`blob ${bytes.length}\0`)).update(bytes).digest('hex'); }
async function captureManagedSourceInventory({root:input,head,indexDigest}={}) {
 const root=rootCheck(input); if(!oidRe.test(head||'')) throw new Error('expected HEAD is invalid'); if(!HASH.test(indexDigest||'')) throw new Error('expected indexDigest must be SHA-256');
 const before=repoState(root); if(before.head!==head) throw new Error(`HEAD does not match expected HEAD: actual=${before.head} expected=${head}`); if(before.indexDigest!==indexDigest) throw new Error('index does not match expected indexDigest');
 const idx=parseIndex(root), tree=parseTree(root,head), workingNames=enumerateAllowed(root,{files:0,bytes:0});
 const allPaths=new Set([...idx.keys(),...tree.keys(),...workingNames]); checkPathSet([...allPaths]);
 const excluded=[]; for(const p of allPaths) if(!allowed(p)) excluded.push({path:p,classification:protectedPath(p)?'protected_or_out_of_scope':'invalid_or_out_of_scope'});
 const policyExcluded= [...idx.keys(),...tree.keys()].filter(p=>!allowed(p));
 const entries=[],budget={files:0,bytes:0}; const format=utf8(git(root,['rev-parse','--show-object-format'])).trim(); if(!['sha1','sha256'].includes(format)) throw new Error('unsupported Git object format');
 for(const p of [...allPaths].filter(allowed).sort()) {
   const h=tree.get(p), i=idx.get(p); const w=safeWorking(root,p,budget);
   const workingOid=w.missing?null:blobOid(w.bytes,format); let state;
   const staged=Boolean((h && (!i || i.oid!==h.oid || i.mode!==h.mode)) || (i && !h)); const rawDiverged=Boolean(!w.missing && workingOid!==(i?.oid||h?.oid)); const modeDiverged=Boolean(process.platform!=='win32'&&!w.missing&&i&&w.mode!==i.mode);
   if(w.missing) state='missing_working_file'; else if(!h&&!i) state='untracked'; else if(rawDiverged) state='raw_diverged'; else if(modeDiverged) state='mode_diverged'; else if(staged) state='staged'; else state='clean';
   entries.push({path:p,...(h?{headOid:h.oid,headMode:h.mode}:{}),...(i?{indexOid:i.oid,indexMode:i.mode}:{}),workingSha256:w.missing?null:sha256(w.bytes),workingOid,staged,rawDiverged,modeDiverged,state,...(w.missing?{}:{mode:w.mode,size:w.size})});
 }
 const after=repoState(root); if(after.head!==before.head||after.indexDigest!==before.indexDigest||after.head!==head||after.indexDigest!==indexDigest) throw new Error('HEAD or index changed during inventory capture');
 const exclusions=[...new Map([...excluded,...policyExcluded.map(p=>({path:p,classification:protectedPath(p)?'protected_or_out_of_scope':'invalid_or_out_of_scope'}))].map(x=>[x.path,x])).values()].sort((a,b)=>a.path.localeCompare(b.path));
 const report={format:FORMAT,mode:MODE,root,head,indexDigest,entries,excluded:exclusions,limitations:['Working-tree comparison uses raw bytes and Git blob identity; it is not clean-filter/EOL-normalized Git status.','Out-of-scope working data is not traversed; exclusions reflect metadata only.','Filesystem checks are logical, not an OS sandbox against a hostile same-user concurrent mutator.','On Windows, executable-mode reporting is not authoritative; modeDiverged is disabled.' ,'This inventory does not establish task ownership, merge readiness, or launch readiness.'],blockers:[{code:'review_only_not_execution',paths:[]}],execution:false,readyForMerge:false,readyForLaunch:false};
 report.inventory_digest=sha256(Buffer.from(canonical(report))); return report;
}
async function captureManagedTreeDelta({root:input,base,head}={}) {
 const root=rootCheck(input); if(!oidRe.test(base||'')||!oidRe.test(head||'')) throw new Error('base and head must be full object IDs');
 const before=repoState(root); if(before.head!==head) throw new Error('HEAD does not match expected delta head');
 const oldTree=parseTree(root,base), newTree=parseTree(root,head), keys=[...new Set([...oldTree.keys(),...newTree.keys()])]; checkPathSet(keys);
 const paths=[]; for(const p of keys.sort()) {const old=oldTree.get(p), next=newTree.get(p); if(!old) paths.push({status:'A',path:p,newOid:next.oid,newMode:next.mode}); else if(!next) paths.push({status:'D',path:p,oldOid:old.oid,oldMode:old.mode}); else if(old.oid!==next.oid||old.mode!==next.mode||old.type!==next.type) paths.push({status:'M',path:p,oldOid:old.oid,oldMode:old.mode,newOid:next.oid,newMode:next.mode});}
 const after=repoState(root); if(after.head!==before.head||after.indexDigest!==before.indexDigest||after.head!==head) throw new Error('HEAD or index changed during tree delta capture');
 return {basis:'merge_base_to_sandbox_head',base,head,paths,metadataOnly:true,execution:false,readyForMerge:false,readyForLaunch:false};
}
export { captureManagedSourceInventory, captureManagedTreeDelta };










