import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, writeFileSync, readFileSync, rmSync, symlinkSync, truncateSync, linkSync, existsSync, chmodSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { captureManagedSourceInventory, captureManagedTreeDelta } from '../../launcher/managed-source-inventory.mjs';
const evidenceRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../artifacts/managed-review-20261024/inventory');
const fixtureRoot = path.join(evidenceRoot, 'fixtures'); mkdirSync(fixtureRoot, { recursive: true });
const hash = bytes => execFileSync('node', ['-e', "process.stdout.write(require('crypto').createHash('sha256').update(require('fs').readFileSync(0)).digest('hex'))"], { input: bytes }).toString();
function fixture(t) {
 const root = mkdtempSync(path.join(fixtureRoot, 'repo-')); assert.ok(path.resolve(root).startsWith(path.resolve(fixtureRoot) + path.sep));
 t.after(() => { const target = path.resolve(root); assert.ok(target.startsWith(path.resolve(fixtureRoot) + path.sep)); rmSync(target, { recursive: true, force: true }); });
 const git = (...args) => execFileSync('git', args, { cwd: root, stdio: 'pipe' }).toString().trim();
 git('init','-q'); git('config','user.email','test@example.invalid'); git('config','user.name','Test');
 mkdirSync(path.join(root,'desktop','src','deep'),{recursive:true}); writeFileSync(path.join(root,'desktop/src/deep/sample.mjs'),'export const value = 1;\n'); writeFileSync(path.join(root,'.env'),'NEVER READ THIS\n');
 git('add','desktop/src/deep/sample.mjs','.env'); git('commit','-qm','fixture');
 const indexDigest=()=>hash(readFileSync(path.join(root,'.git','index')));
 return {root,git,head:git('rev-parse','HEAD'),indexDigest};
}
test('captures raw working bytes and metadata-only exclusions with deterministic digest', async t => {
 const f=fixture(t); writeFileSync(path.join(f.root,'desktop/src/deep/sample.mjs'),'export const value = 1;\r\n'); writeFileSync(path.join(f.root,'desktop/src/deep/new.ts'),'export const fresh = true;\n');
 const a=await captureManagedSourceInventory({root:f.root,head:f.head,indexDigest:f.indexDigest()}); const b=await captureManagedSourceInventory({root:f.root,head:f.head,indexDigest:f.indexDigest()});
 assert.equal(a.mode,'managed_allowed_source_inventory_v1'); assert.equal(a.inventory_digest,b.inventory_digest);
 assert.ok(a.entries.some(x=>x.path==='desktop/src/deep/sample.mjs'&&x.state==='raw_diverged')); assert.ok(a.entries.some(x=>x.path==='desktop/src/deep/new.ts'&&x.state==='untracked'));
 assert.ok(a.excluded.some(x=>x.path==='.env'&&!('sha256' in x))); assert.equal(JSON.stringify(a).includes('NEVER READ THIS'),false);
 assert.equal(a.execution,false); assert.equal(a.readyForMerge,false); assert.equal(a.readyForLaunch,false);
});
test('never invokes filters or forbidden status/blob/diff commands', async t => {
 const f=fixture(t); writeFileSync(path.join(f.root,'.gitattributes'),'*.env filter=spy\n'); f.git('add','.gitattributes'); f.git('commit','-qm','attributes');
 f.git('config','filter.spy.clean',`node -e "require('fs').writeFileSync('filter-invoked','yes')"`);
 const packet=await captureManagedSourceInventory({root:f.root,head:f.git('rev-parse','HEAD'),indexDigest:f.indexDigest()});
 assert.equal(existsSync(path.join(f.root,'filter-invoked')),false); assert.equal(JSON.stringify(packet).includes('NEVER READ THIS'),false);
 const source=readFileSync(new URL('../../launcher/managed-source-inventory.mjs',import.meta.url),'utf8');
 for (const forbidden of ["git(root, ['status'", "git(root, ['cat-file'", "git(root, ['diff-tree'", "'--textconv'", "'--no-textconv'"]) assert.equal(source.includes(forbidden), false, `forbidden command ${forbidden}`);
});
test('tree delta uses metadata and represents rename as delete plus add', async t => {
 const f=fixture(t), base=f.head; f.git('mv','desktop/src/deep/sample.mjs','desktop/src/deep/renamed.mjs'); f.git('commit','-qm','rename'); const head=f.git('rev-parse','HEAD');
 const d=await captureManagedTreeDelta({root:f.root,base,head}); assert.equal(d.basis,'merge_base_to_sandbox_head');
 assert.ok(d.paths.some(x=>x.status==='D'&&x.path==='desktop/src/deep/sample.mjs')); assert.ok(d.paths.some(x=>x.status==='A'&&x.path==='desktop/src/deep/renamed.mjs'));
 assert.ok(d.paths.every(x=>!('content'in x)&&!('similarity'in x)));
});
test('rejects a small file changed between lstat and bounded open', async t => {
 const f=fixture(t), target=path.join(f.root,'desktop/src/deep/sample.mjs'), originalOpen=fs.openSync; let mutated=false;
 fs.openSync=function(file,...args) { const fd=originalOpen.call(fs,file,...args); if(!mutated&&path.resolve(String(file))===path.resolve(target)) { mutated=true; writeFileSync(target,'small concurrent replacement'); } return fd; };
 syncBuiltinESMExports();
 try { await assert.rejects(captureManagedSourceInventory({root:f.root,head:f.head,indexDigest:f.indexDigest()}),/file changed before bounded read|file changed during bounded read/i); }
 finally { fs.openSync=originalOpen; syncBuiltinESMExports(); }
 assert.equal(mutated,true);
});test('rejects unexpected HEAD', async t => {
 const f=fixture(t); await assert.rejects(captureManagedSourceInventory({root:f.root,head:'0'.repeat(f.head.length),indexDigest:f.indexDigest()}),/HEAD/);
});
test('refuses nested symbolic links when the fixture filesystem supports them', async t => {
 const f=fixture(t); try { symlinkSync(path.join(f.root,'.env'),path.join(f.root,'desktop','src','deep','linked'),'file'); }
 catch(e) { if(['EPERM','EISDIR','ENOTSUP'].includes(e.code)) return t.skip(`fixture filesystem cannot create symlinks (${e.code})`); throw e; }
 await assert.rejects(captureManagedSourceInventory({root:f.root,head:f.head,indexDigest:f.indexDigest()}),/link|symbolic/i);
});
test('finds deeply nested untracked files under allowed prefixes', async t => {
 const f=fixture(t), p='desktop/src/deeply/nested/new.mjs'; mkdirSync(path.dirname(path.join(f.root,p)),{recursive:true}); writeFileSync(path.join(f.root,p),'new');
 const out=await captureManagedSourceInventory({root:f.root,head:f.head,indexDigest:f.indexDigest()}); assert.ok(out.entries.some(x=>x.path===p&&x.state==='untracked'));
});
test('reports staged deletion and mode-only index change independently from raw divergence', async t => {
 const f=fixture(t); f.git('rm','desktop/src/deep/sample.mjs');
 const deleted=await captureManagedSourceInventory({root:f.root,head:f.head,indexDigest:f.indexDigest()});
 assert.ok(deleted.entries.some(x=>x.path==='desktop/src/deep/sample.mjs'&&x.staged&&x.state==='missing_working_file'));
 f.git('restore','--source=HEAD','--staged','--worktree','desktop/src/deep/sample.mjs'); const baseline=await captureManagedSourceInventory({root:f.root,head:f.head,indexDigest:f.indexDigest()}); f.git('update-index','--chmod=+x','desktop/src/deep/sample.mjs');
 const changed=await captureManagedSourceInventory({root:f.root,head:f.head,indexDigest:f.indexDigest()});
 const baseEntry=baseline.entries.find(x=>x.path==='desktop/src/deep/sample.mjs'), changedEntry=changed.entries.find(x=>x.path==='desktop/src/deep/sample.mjs'); assert.equal(changedEntry.staged,true); assert.equal(changedEntry.rawDiverged,baseEntry.rawDiverged); assert.equal(changedEntry.modeDiverged,false);
});
test('rejects allowed hard links when the fixture filesystem supports them', async t => {
 const f=fixture(t), sample=path.join(f.root,'desktop/src/deep/sample.mjs'), hard=path.join(f.root,'desktop/src/deep/hard.mjs');
 try { linkSync(sample,hard); } catch(e) { if(['EPERM','EISDIR','ENOTSUP'].includes(e.code)) return t.skip(`fixture filesystem cannot create hard links (${e.code})`); throw e; }
 await assert.rejects(captureManagedSourceInventory({root:f.root,head:f.head,indexDigest:f.indexDigest()}),/hardlinked/i);
});
test('reports POSIX worktree chmod-only divergence separately from raw bytes', async t => {
 if (process.platform === 'win32') return t.skip('Windows executable mode is not authoritative');
 const f=fixture(t), sample=path.join(f.root,'desktop/src/deep/sample.mjs'); chmodSync(sample,0o755);
 const out=await captureManagedSourceInventory({root:f.root,head:f.head,indexDigest:f.indexDigest()});
 const entry=out.entries.find(x=>x.path==='desktop/src/deep/sample.mjs'); assert.equal(entry.rawDiverged,false); assert.equal(entry.modeDiverged,true); assert.equal(entry.state,'mode_diverged');
});test('rejects unresolved conflicts and unsupported symlink modes from metadata', async t => {
 const f=fixture(t), row=f.git('ls-files','--stage','--','desktop/src/deep/sample.mjs'), oid=row.split(' ')[1];
 execFileSync('git',['update-index','--index-info'],{cwd:f.root,input:`100644 ${oid} 1\tdesktop/src/deep/sample.mjs\n100644 ${oid} 2\tdesktop/src/deep/sample.mjs\n100644 ${oid} 3\tdesktop/src/deep/sample.mjs\n`});
 await assert.rejects(captureManagedSourceInventory({root:f.root,head:f.head,indexDigest:f.indexDigest()}),/conflict/i);
 const g=fixture(t), normal=g.git('ls-files','--stage','--','desktop/src/deep/sample.mjs').split(' ')[1];
 execFileSync('git',['update-index','--index-info'],{cwd:g.root,input:`120000 ${normal} 0\tdesktop/src/deep/link-entry\n`});
 await assert.rejects(captureManagedSourceInventory({root:g.root,head:g.head,indexDigest:g.indexDigest()}),/unsupported index mode/i);
});
test('rejects casefold and Windows device aliases in index metadata', async t => {
 const f=fixture(t), oid=f.git('ls-files','--stage','--','desktop/src/deep/sample.mjs').split(' ')[1];
 execFileSync('git',['update-index','--index-info'],{cwd:f.root,input:`100644 ${oid} 0\tdesktop/src/deep/Case.mjs\n100644 ${oid} 0\tdesktop/src/deep/case.mjs\n`});
 await assert.rejects(captureManagedSourceInventory({root:f.root,head:f.head,indexDigest:f.indexDigest()}),/casefold/i);
});
test('rejects Windows device aliases when Git permits metadata injection', async t => {
 const g=fixture(t), oid=g.git('ls-files','--stage','--','desktop/src/deep/sample.mjs').split(' ')[1];
 const payload = `100644 ${oid} 0\tdesktop/src/deep/CON\n`;
 try { execFileSync('git',['update-index','--index-info'],{cwd:g.root,input:payload,stdio:'pipe'}); }
 catch { return t.skip('Git rejects reserved Windows device paths before inventory parsing'); }
 const rows=g.git('ls-files','--stage'); if(!rows.includes('desktop/src/deep/CON')) return t.skip('Git ignored the reserved Windows device path');
 await assert.rejects(captureManagedSourceInventory({root:g.root,head:g.head,indexDigest:g.indexDigest()}),/unsafe|aliased/i);
});
test('rejects sparse oversized allowed files without allocating a large payload', async t => {
 const f=fixture(t), sparse=path.join(f.root,'desktop/src/deep/large.mjs'); writeFileSync(sparse,''); truncateSync(sparse,4*1024*1024+1);
 await assert.rejects(captureManagedSourceInventory({root:f.root,head:f.head,indexDigest:f.indexDigest()}),/limit exceeded/i);
});









test('source file-count limit rejects overflow before opening or reading more than 20000 files', async t => {
 const f=fixture(t), virtualDir=path.join(f.root,'desktop/src/deep'), indexDigest=f.indexDigest();
 const originals=Object.fromEntries(['lstatSync','readdirSync','openSync','fstatSync','readSync','closeSync'].map(name=>[name,fs[name]]));
 const descriptors=new Map(); let sourceOpens=0, sourceReads=0, nextFd=100000;
 const virtualIndex=file=>typeof file==='string'&&path.dirname(path.resolve(file))===path.resolve(virtualDir)&&/^overflow-\d{5}\.mjs$/.test(path.basename(file))?Number(path.basename(file).slice(9,-4)):null;
 const fakeStat=ino=>({isFile:()=>true,isDirectory:()=>false,isSymbolicLink:()=>false,attributes:0,nlink:1,size:0,mode:0o100644,dev:1,ino});
 fs.lstatSync=function(file,...args) { const i=virtualIndex(String(file)); return i===null?originals.lstatSync.call(this,file,...args):fakeStat(i+1); };
 fs.readdirSync=function(dir,...args) { const rows=originals.readdirSync.call(this,dir,...args); return path.resolve(String(dir))!==path.resolve(virtualDir)?rows:[...rows,...Array.from({length:20001},(_,i)=>({name:`overflow-${String(i).padStart(5,'0')}.mjs`,isFile:()=>true,isDirectory:()=>false,isSymbolicLink:()=>false}))]; };
 fs.openSync=function(file,...args) { const i=virtualIndex(String(file)); if(i===null) return originals.openSync.call(this,file,...args); sourceOpens++; const fd=nextFd++; descriptors.set(fd,i+1); return fd; };
 fs.fstatSync=function(fd,...args) { return descriptors.has(fd)?{isFile:()=>true,size:0n,nlink:1n,dev:1n,ino:BigInt(descriptors.get(fd)),mtimeNs:1n,ctimeNs:1n,mode:0o100644n}:originals.fstatSync.call(this,fd,...args); };
 fs.readSync=function(fd,...args) { if(descriptors.has(fd)) { sourceReads++; return 0; } return originals.readSync.call(this,fd,...args); };
 fs.closeSync=function(fd,...args) { if(descriptors.has(fd)) { descriptors.delete(fd); return; } return originals.closeSync.call(this,fd,...args); };
 syncBuiltinESMExports();
 try {
  await assert.rejects(captureManagedSourceInventory({root:f.root,head:f.head,indexDigest}),/working source file limit exceeded/);
  assert.equal(sourceOpens,20000,'overflow file must not be opened');
  assert.ok(sourceReads<=20000,'overflow file must not be read');
  assert.equal(descriptors.size,0,'all opened virtual descriptors must be closed');
 } finally { Object.assign(fs,originals); syncBuiltinESMExports(); }
});
