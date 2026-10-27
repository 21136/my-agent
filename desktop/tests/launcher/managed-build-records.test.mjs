import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {test} from 'node:test';
import {assertStoreBudget,claimBuildStore,safeRelative,writeCanonicalExclusive,readStableBytes} from '../../launcher/managed-build-records.mjs';
const operationId='3d6bd087-0a4c-4b85-a0f1-b5d486dd365c';
function fixture(t){
 const root=fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(),'managed-records-')));
 const sandboxRoot=path.join(root,'.worktrees','sandbox');fs.mkdirSync(sandboxRoot,{recursive:true});
 t.after(()=>{if(path.dirname(root)!==fs.realpathSync.native(os.tmpdir())||!path.basename(root).startsWith('managed-records-'))throw Error('cleanup boundary');fs.rmSync(root,{recursive:true,force:true});});
 return {root,sandboxRoot,store:path.join(sandboxRoot,'artifacts','managed-postmerge-builds')};
}
test('store claim refuses unknown entries and incomplete sibling operations before writing lock',t=>{
 const f=fixture(t);fs.mkdirSync(f.store,{recursive:true});
 fs.mkdirSync(path.join(f.store,operationId));
 assert.throws(()=>claimBuildStore({...f,operationId:'687343cc-894c-44bc-976b-9c3f94291ffc',kind:'materialize'}),/partial materialization/);
 assert.equal(fs.existsSync(path.join(f.store,'.coordinator.lock')),false);
 fs.writeFileSync(path.join(f.store,'unknown.txt'),'unrecognized');
 assert.throws(()=>assertStoreBudget(f),/unknown store entry/);
});
test('abandoned lock remains on disk and fences the same process',t=>{
 const f=fixture(t),claim=claimBuildStore({...f,operationId,kind:'materialize'});
 claim.abandon();
 assert.equal(fs.existsSync(path.join(f.store,'.coordinator.lock')),true);
 assert.throws(()=>assertStoreBudget(f),/maintenance or busy lock/);
 assert.throws(()=>claimBuildStore({...f,operationId,kind:'materialize'}),/maintenance or busy lock/);
});
test('reservation enforces logical and rounded allocation limits before operation writes',t=>{
 const f=fixture(t);
 assert.throws(()=>assertStoreBudget({...f,reserveLogical:512*1024*1024+1}),/budget exceeded/);
 assert.throws(()=>assertStoreBudget({...f,reserveAllocation:512*1024*1024+128*1024}),/budget exceeded/);
 assert.throws(()=>assertStoreBudget({...f,reserveAllocation:-1}),/reservation invalid/);
 assert.equal(fs.existsSync(f.store),false);
});
test('foreign canonical lock cannot be treated as owned and unsafe Windows paths are refused',t=>{
 const f=fixture(t);fs.mkdirSync(f.store,{recursive:true});
 writeCanonicalExclusive(path.join(f.store,'.coordinator.lock'),{format:1,mode:'managed_postmerge_build_lock_v1',operationId,kind:'build',token:'foreign'});
 assert.throws(()=>assertStoreBudget(f),/maintenance or busy lock/);
 for(const name of ['../escape','C:/escape','a\\b','a/CON.txt','a/foo.','a/foo ','a/x:y','a/\u0001'])assert.equal(safeRelative(name),false,name);
 assert.equal(safeRelative('desktop/dist/assets/main.js'),true);
});
test('bounded reads recheck the open descriptor size before allocating bytes',t=>{
 const f=fixture(t),file=path.join(f.root,'grew.bin');fs.writeFileSync(file,'large');
 const original=fs.lstatSync;let first=true;
 fs.lstatSync=function(target,...args){const value=original.call(fs,target,...args);if(target===file&&first){first=false;const small=Object.create(value);Object.defineProperty(small,'size',{value:0});return small;}return value;};
 try{assert.throws(()=>readStableBytes(file,1),/exceeded bound before read/);}
 finally{fs.lstatSync=original;}
});
