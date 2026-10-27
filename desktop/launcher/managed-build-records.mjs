import fs from 'node:fs';
import path from 'node:path';
import {createHash,randomUUID} from 'node:crypto';

const HASH=/^[a-f0-9]{64}$/;
const OID=/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
const LIMIT=512*1024*1024;
const CLUSTER=128*1024;
const FILES=['intent.json','source.json','materialized.json','toolchain.json','build-intent.json','build.json','seal-intent.json','sealed.json'];
const DIRS=['source','build','release','empty-env'];
const OWNED_LOCKS=new Map();
const fail=message=>{throw new TypeError(`managed_build_invalid: ${message}`);};
export function canonical(value){return Array.isArray(value)?`[${value.map(canonical).join(',')}]`:value&&typeof value==='object'?`{${Object.keys(value).sort().map(key=>`${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`:JSON.stringify(value);}
export const digest=value=>createHash('sha256').update(canonical(value)).digest('hex');
export const seal=(value,key='receiptDigest')=>({...value,[key]:digest(value)});
export function exact(value,keys){return value!==null&&typeof value==='object'&&!Array.isArray(value)&&Object.getPrototypeOf(value)===Object.prototype&&Object.keys(value).sort().join('\0')===[...keys].sort().join('\0');}
export function safeRelative(value){
 return typeof value==='string'&&value.length>0&&value.length<=1024&&!value.startsWith('/')&&!/[\\:\u0000-\u001f\u007f<>"|?*]/.test(value)
  &&value.split('/').every(part=>part&&part!=='.'&&part!=='..'&&!/[. ]$/.test(part)&&!/^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i.test(part));
}
export function safeDirectory(value){
 if(typeof value!=='string'||!path.isAbsolute(value)||path.resolve(value)!==value)fail('directory must be a canonical absolute path');
 let cursor=path.parse(value).root;
 for(const part of value.slice(cursor.length).split(path.sep).filter(Boolean)){
  cursor=path.join(cursor,part);const s=fs.lstatSync(cursor);
  if(!s.isDirectory()||s.isSymbolicLink()||(s.attributes&0x400))fail('linked or non-directory ancestor');
 }
 if(fs.realpathSync.native(value)!==value)fail('aliased directory');return value;
}
export function regularFile(value,max=64*1024*1024){
 if(typeof value!=='string'||!path.isAbsolute(value)||path.resolve(value)!==value)fail('file path must be canonical absolute');
 safeDirectory(path.dirname(value));const s=fs.lstatSync(value);
 if(!s.isFile()||s.isSymbolicLink()||(s.attributes&0x400)||s.nlink!==1||s.size>max)fail('unsafe or oversized file');
 if(fs.realpathSync.native(value)!==value)fail('aliased file');return value;
}
const identity=s=>[s.dev,s.ino,s.size,s.nlink,s.mode,s.mtimeMs,s.ctimeMs,s.attributes??0].join(':');
export function readStableBytes(file,max=64*1024*1024){
 regularFile(file,max);const initial=fs.lstatSync(file);const fd=fs.openSync(file,fs.constants.O_RDONLY|(fs.constants.O_NOFOLLOW||0));
 try{
  const before=fs.fstatSync(fd);if(!before.isFile()||before.nlink!==1||(before.attributes&0x400)||!Number.isSafeInteger(before.size)||before.size<0||before.size>max||identity(before)!==identity(initial))fail('file changed or exceeded bound before read');
  const bytes=Buffer.alloc(before.size);let offset=0;
  while(offset<bytes.length){const n=fs.readSync(fd,bytes,offset,Math.min(1024*1024,bytes.length-offset),offset);if(!n)fail('short bounded read');offset+=n;}
  if(identity(before)!==identity(fs.fstatSync(fd))||identity(before)!==identity(fs.lstatSync(file)))fail('file changed during read');
  return bytes;
 }finally{fs.closeSync(fd);}
}
function jsonDepth(value,depth=0){if(depth>64)fail('JSON depth exceeded');if(value&&typeof value==='object')for(const child of Object.values(value))jsonDepth(child,depth+1);}
export function readCanonical(file,max=16*1024*1024){
 const bytes=readStableBytes(file,max),text=bytes.toString('utf8');if(!Buffer.from(text).equals(bytes)||!text.endsWith('\n'))fail('JSON encoding invalid');
 let value;try{value=JSON.parse(text);}catch{fail('JSON parse invalid');}jsonDepth(value);
 if(canonical(value)+'\n'!==text)fail('noncanonical JSON');return value;
}
export function syncDirectory(dir){let fd;try{fd=fs.openSync(dir,fs.constants.O_RDONLY);fs.fsyncSync(fd);}catch(e){if(process.platform!=='win32'||!['EPERM','EISDIR','EINVAL'].includes(e.code))throw e;}finally{if(fd!==undefined)fs.closeSync(fd);}}
export function writeBytesExclusive(file,bytes,max=64*1024*1024){
 if(!Buffer.isBuffer(bytes)||bytes.length>max)fail('write bytes exceeded bound');safeDirectory(path.dirname(file));
 const fd=fs.openSync(file,fs.constants.O_CREAT|fs.constants.O_EXCL|fs.constants.O_WRONLY,0o600);
 try{let offset=0;while(offset<bytes.length){const n=fs.writeSync(fd,bytes,offset,bytes.length-offset);if(!n)fail('short write');offset+=n;}fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
 if(!readStableBytes(file,max).equals(bytes))fail('exclusive write verification failed');syncDirectory(path.dirname(file));
}
export function writeCanonicalExclusive(file,value){writeBytesExclusive(file,Buffer.from(canonical(value)+'\n'),16*1024*1024);}
export function captureTree(root,{maxFiles=20000,maxBytes=64*1024*1024}={}){
 safeDirectory(root);const files=[],aliases=new Set();let size=0,entries=0;
 function walk(dir,prefix='',depth=0){
  if(depth>64)fail('tree depth exceeded');
  for(const name of fs.readdirSync(dir).sort()){
   if(++entries>60000)fail('tree entry limit exceeded');const rel=prefix?`${prefix}/${name}`:name;
   if(!safeRelative(rel))fail('unsafe tree path');const folded=rel.toLowerCase();if(aliases.has(folded))fail('tree case alias');aliases.add(folded);
   const file=path.join(dir,name),s=fs.lstatSync(file);if(s.isSymbolicLink()||(s.attributes&0x400))fail('tree link');
   if(s.isDirectory()){walk(file,rel,depth+1);continue;}
   if(!s.isFile()||s.nlink!==1||files.length>=maxFiles||(size+=s.size)>maxBytes)fail('tree file/count/size limit');
   const bytes=readStableBytes(file,maxBytes);files.push({path:rel,mode:(s.mode&0o111)?'100755':'100644',sha256:createHash('sha256').update(bytes).digest('hex'),byteLength:bytes.length});
  }
 }
 walk(root);return files.sort((a,b)=>a.path.localeCompare(b.path));
}
export function verifyTree(root,expected,options={}){
 const actual=captureTree(root,options),wanted=[...expected].sort((a,b)=>a.path.localeCompare(b.path));
 if(canonical(actual)!==canonical(wanted))fail('tree inventory digest mismatch');
 const directories=new Set();for(const file of wanted){const parts=file.path.split('/');parts.pop();while(parts.length){directories.add(parts.join('/'));parts.pop();}}
 const found=[];const visit=(dir,prefix='')=>{for(const entry of fs.readdirSync(dir,{withFileTypes:true}))if(entry.isDirectory()){const rel=prefix?`${prefix}/${entry.name}`:entry.name;found.push(rel);visit(path.join(dir,entry.name),rel);}};visit(root);
 if(canonical(found.sort())!==canonical([...directories].sort()))fail('tree contains unknown or missing directories');
 return actual;
}
function verifyDigest(value,key='receiptDigest'){if(!HASH.test(value?.[key]||''))fail('record digest missing');const body={...value};delete body[key];if(digest(body)!==value[key])fail('record digest mismatch');}
export function assertBuildOperation({sandboxRoot,operationId}={}){
 if(!UUID.test(operationId||''))fail('operation UUID invalid');safeDirectory(sandboxRoot);
 if(path.basename(sandboxRoot)!=='sandbox'||path.basename(path.dirname(sandboxRoot))!=='.worktrees')fail('sandbox root must be registered conventional worktree');
 const operationRoot=path.join(sandboxRoot,'artifacts','managed-postmerge-builds',operationId);safeDirectory(operationRoot);
 for(const entry of fs.readdirSync(operationRoot)){
  const file=path.join(operationRoot,entry);if(FILES.includes(entry))regularFile(file,16*1024*1024);else if(DIRS.includes(entry))safeDirectory(file);else fail('unknown build operation entry');
 }
 const emptyEnv=path.join(operationRoot,'empty-env');
 if(fs.existsSync(emptyEnv)&&fs.readdirSync(emptyEnv).length)fail('empty build environment contains unknown entries');
 return {operationRoot,sandboxRoot};
}
export function assertStoreBudget({sandboxRoot,reserveLogical=0,reserveAllocation=0}={}){
 safeDirectory(sandboxRoot);const store=path.join(sandboxRoot,'artifacts','managed-postmerge-builds');
 if(!Number.isSafeInteger(reserveLogical)||!Number.isSafeInteger(reserveAllocation)||reserveLogical<0||reserveAllocation<0)fail('budget reservation invalid');
 let logical=0,allocation=0,count=0;
 if(fs.existsSync(store)){
  safeDirectory(store);const entries=fs.readdirSync(store),names=entries.filter(name=>name!=='.coordinator.lock');
  if(entries.includes('.coordinator.lock')){const lock=readCanonical(path.join(store,'.coordinator.lock'),4096);if(canonical(OWNED_LOCKS.get(store))!==canonical(lock))fail('build store maintenance or busy lock');}
  if(names.length>4||names.some(name=>!UUID.test(name)))fail('build operation count or unknown store entry');count=names.length;
  for(const name of names){
   const root=path.join(store,name);safeDirectory(root);
   if(OWNED_LOCKS.get(store)?.operationId===name)continue;
   assertBuildOperation({sandboxRoot,operationId:name});
   for(const required of ['intent.json','source.json','materialized.json','source'])if(!fs.existsSync(path.join(root,required)))fail('partial materialization requires maintenance');
   const buildNames=['build-intent.json','toolchain.json','build.json','build','empty-env'];
   if(buildNames.some(entry=>fs.existsSync(path.join(root,entry)))){
    if(buildNames.some(entry=>!fs.existsSync(path.join(root,entry))))fail('partial build requires maintenance');
    readVerifiedBuildOperation({sandboxRoot,operationId:name},{allowLegacy:true});
   }else readMaterializedOperation({sandboxRoot,operationId:name});
   const releaseBase=path.join(root,'release');
   if(fs.existsSync(releaseBase)){
    const releases=fs.readdirSync(releaseBase);if(releases.length!==1||!HASH.test(releases[0])||!fs.existsSync(path.join(root,'build.json')))fail('partial or unknown release requires maintenance');
    const releaseRoot=path.join(releaseBase,releases[0]);safeDirectory(releaseRoot);
    const manifest=readCanonical(path.join(releaseRoot,'manifest.json')),sealed=readCanonical(path.join(releaseRoot,'sealed.json')),intent=readCanonical(path.join(releaseRoot,'seal-intent.json'));
    if(digest(manifest)!==releases[0]||manifest.mode!=='managed_postmerge_release_v1'||manifest.readyForLaunch!==false||sealed.releaseId!==releases[0]||sealed.operationId!==name||intent.releaseId!==releases[0]||intent.operationId!==name)fail('release completion binding invalid');
    verifyDigest(sealed);verifyDigest(intent,'intentDigest');
    if(canonical(fs.readdirSync(releaseRoot).sort())!==canonical(['app','manifest.json','seal-intent.json','sealed.json']))fail('unknown release root entry');
    validateFiles(manifest.files);verifyTree(path.join(releaseRoot,'app'),manifest.files);
   }
  }
  for(const entry of captureTree(store,{maxFiles:60000,maxBytes:LIMIT})){logical+=entry.byteLength;allocation+=Math.ceil(entry.byteLength/CLUSTER)*CLUSTER;}
 }
 if(logical+reserveLogical>LIMIT||allocation+reserveAllocation>LIMIT)fail('build store logical/allocation budget exceeded');
 return {logicalBytes:logical,roundedAllocationBytes:allocation,operationCount:count};
}
export function claimBuildStore({sandboxRoot,operationId,kind}={}){
 if(!UUID.test(operationId||'')||!['materialize','build','seal'].includes(kind))fail('build claim identity invalid');safeDirectory(sandboxRoot);
 const artifacts=path.join(sandboxRoot,'artifacts');if(!fs.existsSync(artifacts))fs.mkdirSync(artifacts);safeDirectory(artifacts);
 const store=path.join(artifacts,'managed-postmerge-builds');if(!fs.existsSync(store))fs.mkdirSync(store);safeDirectory(store);
 assertStoreBudget({sandboxRoot});
 const lock={format:1,mode:'managed_postmerge_build_lock_v1',operationId,kind,token:randomUUID()};
 writeCanonicalExclusive(path.join(store,'.coordinator.lock'),lock);OWNED_LOCKS.set(store,lock);
 assertStoreBudget({sandboxRoot});let completed=false;
 return {store,abandon(){OWNED_LOCKS.delete(store);},complete(){if(completed||canonical(readCanonical(path.join(store,'.coordinator.lock'),4096))!==canonical(lock))fail('build lock ownership lost');fs.unlinkSync(path.join(store,'.coordinator.lock'));syncDirectory(store);OWNED_LOCKS.delete(store);completed=true;}};
}
function validateFiles(files,{blobs=false,maxBytes=64*1024*1024,maxFiles=20000}={}){
 if(!Array.isArray(files)||!files.length||files.length>maxFiles)fail('source inventory count');const names=new Set();let raw=0;
 for(const f of files){if(!exact(f,blobs?['path','mode','blobOid','sha256','byteLength']:['path','mode','sha256','byteLength'])||!safeRelative(f.path)||!['100644','100755'].includes(f.mode)||!HASH.test(f.sha256)||!Number.isSafeInteger(f.byteLength)||f.byteLength<0||(blobs&&!OID.test(f.blobOid))||names.has(f.path.toLowerCase()))fail('file inventory invalid');names.add(f.path.toLowerCase());raw+=f.byteLength;}
 if(raw>maxBytes)fail('inventory byte bound');return raw;
}
export function readMaterializedOperation(input){
 const {operationRoot,sandboxRoot}=assertBuildOperation(input),sourceRoot=path.join(operationRoot,'source');
 const intent=readCanonical(path.join(operationRoot,'intent.json')),snapshot=readCanonical(path.join(operationRoot,'source.json')),materialized=readCanonical(path.join(operationRoot,'materialized.json'));
 if(!exact(intent,['format','mode','operationId','planDigest','sourceSnapshotDigest','stage','readyForLaunch'])||intent.format!==1||intent.mode!=='managed_postmerge_build_intent_v1'||intent.operationId!==input.operationId||!HASH.test(intent.planDigest)||!HASH.test(intent.sourceSnapshotDigest)||intent.stage!=='materializing'||intent.readyForLaunch!==false)fail('build intent schema invalid');
 if(!exact(snapshot,['format','mode','status','planDigest','receiptDigest','roots','mergedHead','mergedTree','files','excludedProtectedPaths','readyForLaunch','buildVerified','snapshotDigest'])||snapshot.format!==1||snapshot.mode!=='managed_postmerge_source_snapshot_v1'||snapshot.status!=='source_merge_verified'||snapshot.readyForLaunch!==false||snapshot.buildVerified!==false||snapshot.planDigest!==intent.planDigest||snapshot.snapshotDigest!==intent.sourceSnapshotDigest||!HASH.test(snapshot.receiptDigest)||!OID.test(snapshot.mergedHead)||!OID.test(snapshot.mergedTree))fail('source snapshot schema invalid');
 verifyDigest(snapshot,'snapshotDigest');const raw=validateFiles(snapshot.files,{blobs:true});
 if(!exact(snapshot.roots,['stable','sandbox'])||!Array.isArray(snapshot.excludedProtectedPaths)||snapshot.excludedProtectedPaths.some(name=>!safeRelative(name)))fail('source roots or excluded path schema');
 for(const side of ['stable','sandbox']){const r=snapshot.roots[side];if(!exact(r,['worktreeRoot','gitDir','commonDir','branch'])||typeof r.branch!=='string'||!r.branch||['worktreeRoot','gitDir','commonDir'].some(key=>typeof r[key]!=='string'||!path.isAbsolute(r[key])||path.resolve(r[key])!==r[key]))fail('source root identity schema');}
 if(snapshot.roots.sandbox.worktreeRoot!==sandboxRoot||snapshot.roots.stable.worktreeRoot!==path.resolve(sandboxRoot,'../..')||snapshot.roots.sandbox.commonDir!==snapshot.roots.stable.commonDir||snapshot.roots.sandbox.branch!=='experiment/sandbox')fail('source root binding mismatch');
 if(!exact(materialized,['format','mode','operationId','planDigest','snapshotDigest','sourceInventoryDigest','files','rawBytes','readyForLaunch','receiptDigest'])||materialized.format!==1||materialized.mode!=='managed_postmerge_materialized_v1'||materialized.operationId!==input.operationId||materialized.planDigest!==snapshot.planDigest||materialized.snapshotDigest!==snapshot.snapshotDigest||materialized.sourceInventoryDigest!==digest(snapshot.files)||canonical(materialized.files)!==canonical(snapshot.files)||materialized.rawBytes!==raw||materialized.readyForLaunch!==false)fail('materialized receipt binding invalid');
 verifyDigest(materialized);
 const expected=snapshot.files.map(({blobOid,...entry})=>entry).sort((a,b)=>a.path.localeCompare(b.path));
 try{verifyTree(sourceRoot,expected);}catch{fail('materialized source inventory drift');}
 return {operationRoot,sandboxRoot,sourceRoot,snapshot,materialized,intent};
}
export function readVerifiedBuildOperation(input,{allowLegacy=false}={}){
 const op=readMaterializedOperation(input),buildRoot=path.join(op.operationRoot,'build');
 const build=readCanonical(path.join(op.operationRoot,'build.json')),toolchain=readCanonical(path.join(op.operationRoot,'toolchain.json'));
 if(!exact(build,['format','mode','operationId','planDigest','snapshotDigest','materializedReceiptDigest','toolchainDigest','buildPolicy','outputs','inputsDigest','buildVerified','readyForLaunch','receiptDigest'])||build.format!==1||build.mode!=='managed_postmerge_build_receipt_v1'||build.operationId!==input.operationId||build.planDigest!==op.snapshot.planDigest||build.snapshotDigest!==op.snapshot.snapshotDigest||build.materializedReceiptDigest!==op.materialized.receiptDigest||build.inputsDigest!==digest(op.snapshot.files)||!(build.buildPolicy==='configless_snapshot_v2'||allowLegacy&&build.buildPolicy==='configless_v1')||build.buildVerified!==true||build.readyForLaunch!==false)fail('build receipt schema or binding invalid');
 verifyDigest(build);validateFiles(build.outputs);
 const required=['desktop/dist-electron/main.js','desktop/dist-electron/preload.js','desktop/dist/index.html'];
 if(required.some(name=>!build.outputs.some(file=>file.path===name))||build.outputs.some(file=>!['desktop/dist-electron/','desktop/dist/'].some(prefix=>file.path.startsWith(prefix))))fail('build output scope invalid');
 try{verifyTree(buildRoot,build.outputs,{maxFiles:10000});}catch{fail('build output inventory drift');}
 if(!exact(toolchain,['format','mode','root','packageJsonSha256','packageLockSha256','node','files','toolchainDigest'])||toolchain.format!==1||toolchain.mode!=='managed_postmerge_toolchain_v1'||typeof toolchain.root!=='string'||!path.isAbsolute(toolchain.root)||path.resolve(toolchain.root)!==toolchain.root||path.basename(toolchain.root)!=='desktop'||path.basename(path.dirname(toolchain.root))!=='sandbox'||path.basename(path.dirname(path.dirname(toolchain.root)))!=='.worktrees'||toolchain.toolchainDigest!==build.toolchainDigest||!exact(toolchain.node,['executable','sha256','version'])||!path.isAbsolute(toolchain.node.executable)||!HASH.test(toolchain.node.sha256)||typeof toolchain.node.version!=='string'||!Array.isArray(toolchain.files))fail('toolchain receipt schema invalid');
 verifyDigest(toolchain,'toolchainDigest');
 validateFiles(toolchain.files,{maxBytes:448*1024*1024});
 if(toolchain.files.some(file=>!file.path.startsWith('node_modules/'))||!HASH.test(toolchain.packageJsonSha256)||!HASH.test(toolchain.packageLockSha256)||path.resolve(toolchain.node.executable)!==toolchain.node.executable||!/^v\d+\.\d+\.\d+(?:[-+][\w.-]+)?$/.test(toolchain.node.version))fail('toolchain inventory or executable identity invalid');
 const buildIntent=readCanonical(path.join(op.operationRoot,'build-intent.json'));
 if(!exact(buildIntent,['format','mode','operationId','planDigest','snapshotDigest','materializedReceiptDigest','toolchainDigest','buildPolicy','state','readyForLaunch'])||buildIntent.format!==1||buildIntent.mode!=='managed_postmerge_build_intent_v1'||buildIntent.operationId!==input.operationId||buildIntent.planDigest!==build.planDigest||buildIntent.snapshotDigest!==build.snapshotDigest||buildIntent.materializedReceiptDigest!==build.materializedReceiptDigest||buildIntent.toolchainDigest!==build.toolchainDigest||buildIntent.buildPolicy!==build.buildPolicy||buildIntent.state!=='started'||buildIntent.readyForLaunch!==false)fail('build intent does not bind verified build');
 for(const [name,key]of [['desktop/package.json','packageJsonSha256'],['desktop/package-lock.json','packageLockSha256']]){const file=op.snapshot.files.find(entry=>entry.path===name);if(!file||file.sha256!==toolchain[key])fail('toolchain package/lock differs from committed source');}
 return {...op,buildRoot,build,toolchain};
}
