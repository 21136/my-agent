import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {materializeManagedPostmergeSource} from '../../launcher/managed-postmerge-input.mjs';
import {readMaterializedOperation} from '../../launcher/managed-build-records.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../..');
const stableRoot=path.join(root,'artifacts/continuous-closeout-20261025/git-ff-local-probe'),sandboxRoot=path.join(stableRoot,'.worktrees/sandbox');
const planDigest=fs.readdirSync(path.join(sandboxRoot,'artifacts/managed-selected-merges')).find(name=>/^[a-f0-9]{64}$/.test(name));
const input={stableRoot,sandboxRoot,planDigest,gitExecutable:'C:\\Program Files\\Git\\cmd\\git.exe',operationId:'2b90e851-e08d-4c43-92b2-760941fed62b'};
test('invalid operation identity is refused without making an output store',()=>{
 assert.throws(()=>materializeManagedPostmergeSource({...input,operationId:'../escape'}),/operation|UUID|invalid/i);
});
test('materialization copies committed blobs, binds them, and refuses reuse',()=>{
 const operationRoot=path.join(sandboxRoot,'artifacts/managed-postmerge-builds',input.operationId);
 const result=fs.existsSync(operationRoot)?readMaterializedOperation(input):materializeManagedPostmergeSource(input);
 const verified=readMaterializedOperation(input);
 assert.equal(result.operationRoot,operationRoot);assert.equal(verified.materialized.readyForLaunch,false);
 const dirty='docs/dirty.md',declared=verified.snapshot.files.find(file=>file.path===dirty);assert.ok(declared);
 assert.notDeepEqual(fs.readFileSync(path.join(stableRoot,dirty)),fs.readFileSync(path.join(verified.sourceRoot,dirty)),'unrelated dirty working bytes must not enter committed build source');
 assert.throws(()=>materializeManagedPostmergeSource(input),/used|exists|reuse|operation/i);
 const selected=path.join(verified.sourceRoot,'desktop/selected.mjs'),bytes=fs.readFileSync(selected);
 try{fs.writeFileSync(selected,Buffer.from('tampered materialized bytes\n'));assert.throws(()=>readMaterializedOperation(input),/source|materialized|inventory|digest/i);}
 finally{fs.writeFileSync(selected,bytes);}
 assert.equal(readMaterializedOperation(input).materialized.receiptDigest,verified.materialized.receiptDigest);
});
