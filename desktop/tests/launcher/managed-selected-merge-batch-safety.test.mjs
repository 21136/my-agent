import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readFileSync, lstatSync, readdirSync, realpathSync, rmSync } from 'node:fs';
import { randomUUID, createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { performance } from 'node:perf_hooks';
import { fileURLToPath } from 'node:url';
import { captureManagedSourceCheckpoint } from '../../launcher/managed-source-checkpoint.mjs';
import { createManagedSourceSelectionProposal } from '../../launcher/managed-source-selection.mjs';
import { prepareManagedSelectedMerge, executeManagedSelectedMerge } from '../../launcher/managed-selected-merge.mjs';

const gitExe = 'C:/Program Files/Git/cmd/git.exe';
const optInSlowTest = process.env.MANAGED_SELECTED_MERGE_LONG_TESTS === '1' ? test : test.skip;
const hash = value => createHash('sha256').update(value).digest('hex');
const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(',')}]` : value && typeof value === 'object' ? `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}` : JSON.stringify(value);
const seal = (value, key) => { delete value[key]; value[key] = hash(Buffer.from(canonical(value))); return value; };
const blob = (bytes, format='sha1') => createHash(format).update(Buffer.concat([Buffer.from(`blob ${bytes.length}\0`), bytes])).digest('hex');
function git(cwd, ...args) {
  const r = spawnSync(gitExe, ['-c', `safe.directory=${cwd}`, '-c', 'core.hooksPath=NUL', '-c', 'commit.gpgSign=false', '-c', 'protocol.allow=never', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false', ...args], { cwd, encoding: 'utf8', windowsHide: true, env: { ...process.env, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: 'NUL', GIT_TERMINAL_PROMPT: '0' } });
  assert.equal(r.status, 0, `fixture git failed: ${r.stderr}`); return r.stdout.trim();
}
function gitBytes(cwd, ...args) { const r=spawnSync(gitExe,['-c',`safe.directory=${cwd}`,'-c','protocol.allow=never',...args],{cwd,encoding:'buffer',windowsHide:true,env:{...process.env,GIT_CONFIG_NOSYSTEM:'1',GIT_CONFIG_GLOBAL:'NUL',GIT_TERMINAL_PROMPT:'0'}});assert.equal(r.status,0,`fixture byte Git command failed: ${r.stderr?.toString()}`);return r.stdout; }
function lanePaths(root) { const gd = path.resolve(root, git(root, 'rev-parse', '--path-format=absolute', '--git-dir')); return { gitDir: gd, commonDir: path.resolve(root, git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir')), index: path.join(gd, 'index') }; }
function fixture({ selectedCount=2, preserveStableCount=0, objectFormat='sha1' } = {}) {
  const artifactRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../artifacts'); mkdirSync(artifactRoot, { recursive: true });
  const tempRoot = path.join(artifactRoot, `m-${randomUUID().slice(0, 8)}`); mkdirSync(tempRoot);
  const rootStat = lstatSync(tempRoot); const identity = { dev: rootStat.dev, ino: rootStat.ino, birthtimeMs: rootStat.birthtimeMs };
  const stableRoot = path.join(tempRoot, 'project'), sandboxRoot = path.join(stableRoot, '.worktrees', 'sandbox');
  const selected = Array.from({length:selectedCount},(_,i)=>`desktop/batch-${String(i).padStart(2,'0')}.mjs`), stableBytes = new Map(), sandboxBytes = new Map(), preservationBytes = new Map();
  for (const [i, p] of selected.entries()) { const a = Buffer.from(`base-${i}\n`), b = Buffer.from(`selected-${i}\n`); stableBytes.set(p,a); sandboxBytes.set(p,b); preservationBytes.set(p,i<preserveStableCount?Buffer.from(`preserved-${i}\n`):a); const f=path.join(stableRoot,...p.split('/')); mkdirSync(path.dirname(f),{recursive:true}); writeFileSync(f,a); }
  const keep = 'docs/keep.md', keepBytes = Buffer.from('keep in HEAD\n'), stableKeep = Buffer.from('stable unselected local bytes\n'), sandboxKeep = Buffer.from('sandbox unselected local bytes\n'); const k=path.join(stableRoot,...keep.split('/')); mkdirSync(path.dirname(k),{recursive:true}); writeFileSync(k,keepBytes);
  git(stableRoot,'init','-b','main',...(objectFormat==='sha1'?[]:[`--object-format=${objectFormat}`])); git(stableRoot,'config','user.name','Fixture'); git(stableRoot,'config','user.email','fixture@example.invalid'); git(stableRoot,'add','.'); git(stableRoot,'commit','-m','fixture');
  mkdirSync(path.dirname(sandboxRoot),{recursive:true}); git(stableRoot,'worktree','add','-b','experiment/sandbox',sandboxRoot,'HEAD');
  for (const [p,b] of sandboxBytes) writeFileSync(path.join(sandboxRoot,...p.split('/')),b);
  for (const [p,b] of preservationBytes) writeFileSync(path.join(stableRoot,...p.split('/')),b);
  writeFileSync(k,stableKeep); const sandboxKeepPath=path.join(sandboxRoot,...keep.split('/')); writeFileSync(sandboxKeepPath,sandboxKeep);
  const headS=git(stableRoot,'rev-parse','HEAD'), headB=git(sandboxRoot,'rev-parse','HEAD');
  const entry=(p,base,work)=>({path:p,headOid:blob(base,objectFormat),headMode:'100644',indexOid:blob(base,objectFormat),indexMode:'100644',workingSha256:hash(work),workingOid:blob(work,objectFormat),staged:false,rawDiverged:!base.equals(work),modeDiverged:false,state:base.equals(work)?'clean':'raw_diverged',mode:'100644',size:work.length});
  const mkLane=(root,head,branch,entries)=>{const d=lanePaths(root),index=readFileSync(d.index);const inventory=seal({format:1,mode:'managed_allowed_source_inventory_v1',root,head,indexDigest:hash(index),entries,excluded:[],blockers:[{code:'review_only_not_execution',paths:[]}],limitations:[],execution:false,readyForMerge:false,readyForLaunch:false},'inventory_digest');return {identity:{worktreeRoot:root,gitDir:d.gitDir,commonDir:d.commonDir},head,branch,indexDigest:hash(index),inventory};};
  const stable=mkLane(stableRoot,headS,'main',[...stableBytes].map(([p,b])=>entry(p,b,preservationBytes.get(p))).concat(entry(keep,keepBytes,stableKeep)));
  const sandbox=mkLane(sandboxRoot,headB,'experiment/sandbox',[...sandboxBytes].map(([p,b])=>entry(p,stableBytes.get(p),b)).concat(entry(keep,keepBytes,sandboxKeep)));
  const review=seal({format:1,mode:'managed_source_review_v1',inspectionDigest:hash(Buffer.from('fixture')),stable,sandbox,history:{mergeBase:headB,commits:[],paths:[]},effectiveDelta:{basis:'merge_base_to_sandbox_head',paths:[]},blockers:[{code:'review_only_not_execution',paths:[]}],execution:false,readyForMerge:false,readyForLaunch:false},'review_digest');
  const request={format:1,mode:'managed_source_selection_request_v1',reviewDigest:review.review_digest,selectedPaths:selected};
  const proposal=createManagedSourceSelectionProposal({review,request});
  const checkpoint=captureManagedSourceCheckpoint({review,request:{format:1,mode:'managed_source_checkpoint_request_v1',reviewDigest:review.review_digest,proposalDigest:proposal.proposal_digest,selectedPaths:selected},stableRoot,sandboxRoot});
  return {tempRoot,artifactRoot,identity,stableRoot,sandboxRoot,selected,keep,stableBytes,preservationBytes,stableKeep,sandboxKeep,objectFormat,checkpoint,gitExecutable:gitExe,stableIndex:lanePaths(stableRoot).index};
}

function cleanupFixture(f) {
  const target=path.resolve(f.tempRoot), expectedParent=path.resolve(f.artifactRoot);
  if(path.dirname(target)!==expectedParent||!path.basename(target).startsWith('m-'))throw new Error('cleanup path escaped the dedicated sandbox artifacts directory');
  if(realpathSync.native(target)!==target)throw new Error('cleanup target is not canonical');
  const checkChain=p=>{let cur=path.parse(p).root;for(const part of p.slice(cur.length).split(path.sep).filter(Boolean)){cur=path.join(cur,part);const s=lstatSync(cur);if(s.isSymbolicLink()||(s.attributes&0x400))throw new Error('cleanup path contains a link or reparse point');}};
  checkChain(expectedParent);checkChain(target);
  const scan=dir=>{for(const e of readdirSync(dir,{withFileTypes:true})){const full=path.join(dir,e.name),s=lstatSync(full);if(s.isSymbolicLink()||(s.attributes&0x400)||(!s.isDirectory()&&!s.isFile()))throw new Error('cleanup descendant is linked or unsupported');if(s.isDirectory())scan(full);}};
  scan(target);const current=lstatSync(target);if(!current.isDirectory()||current.dev!==f.identity.dev||current.ino!==f.identity.ino||current.birthtimeMs!==f.identity.birthtimeMs||realpathSync.native(target)!==target)throw new Error('cleanup target identity changed');rmSync(target,{recursive:true,force:false});
}

test('batch temp-index preparation catches concurrent drift before any real index, HEAD, or worktree write', t => {
  const f=fixture();
  t.after(()=>cleanupFixture(f));
  const plan=prepareManagedSelectedMerge({stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot,checkpoint:f.checkpoint,gitExecutable:f.gitExecutable});
  const before={head:git(f.stableRoot,'rev-parse','HEAD'),refs:git(f.stableRoot,'show-ref'),index:hash(readFileSync(f.stableIndex)),selected:f.selected.map(p=>readFileSync(path.join(f.stableRoot,...p.split('/'))))};
  let calls=0,drifted=false;
  const assertTrusted=()=>{calls++;if(!drifted&&calls===5){drifted=true;writeFileSync(path.join(f.stableRoot,...f.keep.split('/')),'concurrent edit\n');}return true;};
  assert.throws(()=>executeManagedSelectedMerge({plan,checkpoint:f.checkpoint,stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot,gitExecutable:f.gitExecutable,assertTrusted}),e=>e.code==='selected_merge_preservation_drift');
  assert.equal(drifted,true,'the edit must land after one selected object write and during the selected batch');
  assert.equal(git(f.stableRoot,'rev-parse','HEAD'),before.head);
  assert.equal(git(f.stableRoot,'show-ref'),before.refs,'blob-only import must not update any ref');
  assert.equal(hash(readFileSync(f.stableIndex)),before.index);
  f.selected.forEach((p,i)=>assert.deepEqual(readFileSync(path.join(f.stableRoot,...p.split('/'))),before.selected[i]));
  assert.deepEqual(readFileSync(path.join(f.stableRoot,...f.keep.split('/'))),Buffer.from('concurrent edit\n'),'external concurrent edit is preserved, not overwritten');
});

test('batched temporary index produces the exact selected tree and a verified receipt', t => {
  const f=fixture();
  t.after(()=>cleanupFixture(f));
  const plan=prepareManagedSelectedMerge({stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot,checkpoint:f.checkpoint,gitExecutable:f.gitExecutable});
  const receipt=executeManagedSelectedMerge({plan,checkpoint:f.checkpoint,stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot,gitExecutable:f.gitExecutable,assertTrusted:()=>true});
  assert.equal(receipt.status,'source_merge_verified');
  assert.equal(git(f.stableRoot,'rev-parse','HEAD'),plan.expectedCommit);
  assert.equal(git(f.stableRoot,'rev-parse','HEAD^{tree}'),plan.expectedMergedTree);
  for(const file of f.checkpoint.files)assert.deepEqual(readFileSync(path.join(f.stableRoot,...file.path.split('/'))),Buffer.from(file.bytesBase64,'base64'));
  assert.equal(receipt.readyForLaunch,false);
});

optInSlowTest('19-file preservation batches only the private preparation index and retains the preservation parent and unselected edits', t => {
  const f=fixture({selectedCount:19,preserveStableCount:19});t.after(()=>cleanupFixture(f));
  const plan=prepareManagedSelectedMerge({stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot,checkpoint:f.checkpoint,gitExecutable:f.gitExecutable,preserveStableWorkingChanges:true});
  assert.equal(plan.stablePreservation.files.length,19);
  const receipt=executeManagedSelectedMerge({plan,checkpoint:f.checkpoint,stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot,gitExecutable:f.gitExecutable,assertTrusted:()=>true});
  assert.equal(receipt.status,'source_merge_verified');
  assert.equal(git(f.stableRoot,'rev-parse','HEAD^'),plan.stablePreservation.expectedCommit,'selected commit must descend from the bound stable-byte preservation parent');
  for(const [p,b] of f.preservationBytes)assert.deepEqual(gitBytes(f.stableRoot,'cat-file','blob',plan.stablePreservation.files.find(x=>x.path===p).oid),b,`preservation parent must retain original stable bytes for ${p}`);
  assert.deepEqual(readFileSync(path.join(f.stableRoot,...f.keep.split('/'))),f.stableKeep,'unselected stable local bytes must survive both fast-forwards');
  assert.deepEqual(readFileSync(path.join(f.sandboxRoot,...f.keep.split('/'))),f.sandboxKeep,'unselected sandbox local bytes must remain untouched');
  for(const p of f.selected)assert.deepEqual(readFileSync(path.join(f.stableRoot,...p.split('/'))),Buffer.from(f.checkpoint.files.find(x=>x.path===p).bytesBase64,'base64'));
});

test('authorization revoked after blob-only import blocks real index and HEAD changes', t => {
  const f=fixture();t.after(()=>cleanupFixture(f));
  const plan=prepareManagedSelectedMerge({stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot,checkpoint:f.checkpoint,gitExecutable:f.gitExecutable});
  const before={head:git(f.stableRoot,'rev-parse','HEAD'),refs:git(f.stableRoot,'show-ref'),index:hash(readFileSync(f.stableIndex)),selected:f.selected.map(p=>readFileSync(path.join(f.stableRoot,...p.split('/'))))};
  let checks=0;const assertTrusted=()=>++checks!==6;
  assert.throws(()=>executeManagedSelectedMerge({plan,checkpoint:f.checkpoint,stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot,gitExecutable:f.gitExecutable,assertTrusted}),e=>e.code==='selected_merge_trust_denied');
  assert.equal(checks,6,'revocation is injected at the post-import authorization boundary');
  assert.equal(git(f.stableRoot,'rev-parse','HEAD'),before.head);assert.equal(git(f.stableRoot,'show-ref'),before.refs);
  assert.equal(hash(readFileSync(f.stableIndex)),before.index);f.selected.forEach((p,i)=>assert.deepEqual(readFileSync(path.join(f.stableRoot,...p.split('/'))),before.selected[i]));
});

optInSlowTest('91 selected SHA-256 files prepare and execute end-to-end with blob-only import', t => {
  const f=fixture({selectedCount:91,objectFormat:'sha256'});t.after(()=>cleanupFixture(f));
  const started=performance.now();
  const plan=prepareManagedSelectedMerge({stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot,checkpoint:f.checkpoint,gitExecutable:f.gitExecutable});
  assert.equal(plan.selectedPaths.length,91);
  const receipt=executeManagedSelectedMerge({plan,checkpoint:f.checkpoint,stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot,gitExecutable:f.gitExecutable,assertTrusted:()=>true});
  const elapsedMs=Math.round(performance.now()-started);
  assert.equal(receipt.status,'source_merge_verified');assert.equal(receipt.readyForLaunch,false);
  assert.equal(git(f.stableRoot,'rev-parse','HEAD'),plan.expectedCommit);
  assert.equal(git(f.stableRoot,'rev-parse','HEAD^{tree}'),plan.expectedMergedTree);
  for(const p of f.selected)assert.deepEqual(readFileSync(path.join(f.stableRoot,...p.split('/'))),Buffer.from(f.checkpoint.files.find(x=>x.path===p).bytesBase64,'base64'));
  console.log('managed-selected-merge-e2e '+JSON.stringify({files:91,objectFormat:f.objectFormat,elapsedMs,scope:'isolated fixture; prepare+execute+postconditions'}));
});

test('one-file stable preservation keeps parent bytes and both unselected working files', t => {
  const f=fixture({selectedCount:1,preserveStableCount:1});t.after(()=>cleanupFixture(f));
  const plan=prepareManagedSelectedMerge({stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot,checkpoint:f.checkpoint,gitExecutable:f.gitExecutable,preserveStableWorkingChanges:true});
  assert.equal(plan.stablePreservation.files.length,1);
  const receipt=executeManagedSelectedMerge({plan,checkpoint:f.checkpoint,stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot,gitExecutable:f.gitExecutable,assertTrusted:()=>true});
  assert.equal(receipt.status,'source_merge_verified');
  assert.equal(git(f.stableRoot,'rev-parse','HEAD^'),plan.stablePreservation.expectedCommit);
  assert.deepEqual(gitBytes(f.stableRoot,'cat-file','blob',plan.stablePreservation.files[0].oid),f.preservationBytes.get(f.selected[0]));
  assert.deepEqual(readFileSync(path.join(f.stableRoot,...f.keep.split('/'))),f.stableKeep);
  assert.deepEqual(readFileSync(path.join(f.sandboxRoot,...f.keep.split('/'))),f.sandboxKeep);
});


optInSlowTest('91 selected files with 19 stable dirty entries preserve parent, HEAD and unselected bytes', t => {
  const f=fixture({selectedCount:91,preserveStableCount:19});t.after(()=>cleanupFixture(f));
  const prepareStarted=performance.now();
  const plan=prepareManagedSelectedMerge({stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot,checkpoint:f.checkpoint,gitExecutable:f.gitExecutable,preserveStableWorkingChanges:true});
  const preparedAt=performance.now();
  assert.equal(plan.selectedPaths.length,91);assert.equal(plan.stablePreservation.files.length,19);
  const receipt=executeManagedSelectedMerge({plan,checkpoint:f.checkpoint,stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot,gitExecutable:f.gitExecutable,assertTrusted:()=>true});
  const finishedAt=performance.now();
  assert.equal(receipt.status,'source_merge_verified');
  assert.equal(git(f.stableRoot,'rev-parse','HEAD'),plan.expectedCommit,'stable HEAD must be the selected child commit');
  assert.equal(git(f.stableRoot,'rev-parse','HEAD^'),plan.stablePreservation.expectedCommit,'selected commit must descend from the preservation parent');
  for(const file of plan.stablePreservation.files)assert.deepEqual(gitBytes(f.stableRoot,'cat-file','blob',file.oid),f.preservationBytes.get(file.path),
    'the preservation parent must contain the 19 original dirty stable byte sequences');
  for(const p of f.selected)assert.deepEqual(readFileSync(path.join(f.stableRoot,...p.split('/'))),Buffer.from(f.checkpoint.files.find(x=>x.path===p).bytesBase64,'base64'));
  assert.deepEqual(readFileSync(path.join(f.stableRoot,...f.keep.split('/'))),f.stableKeep,'unselected stable bytes must be preserved');
  assert.deepEqual(readFileSync(path.join(f.sandboxRoot,...f.keep.split('/'))),f.sandboxKeep,'unselected sandbox bytes must be preserved');
  console.log('managed-selected-merge-e2e '+JSON.stringify({selectedFiles:91,stableDirtySelected:19,stableCleanSelected:72,prepareMs:Math.round(preparedAt-prepareStarted),executeMs:Math.round(finishedAt-preparedAt),totalMs:Math.round(finishedAt-prepareStarted),scope:'isolated mixed-preservation fixture'}));
});
