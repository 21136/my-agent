import fs from 'node:fs';
import path from 'node:path';
import {readManagedPostmergeSourcePayload} from './managed-postmerge-source.mjs';
import {readSelectedMergeEvidence} from './managed-selected-merge-store.mjs';
import {canonical,seal,digest,safeDirectory,safeRelative,writeCanonicalExclusive,writeBytesExclusive,readMaterializedOperation,assertStoreBudget,claimBuildStore,syncDirectory} from './managed-build-records.mjs';

const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
const HASH=/^[a-f0-9]{64}$/;
const CLUSTER=128*1024;
const fail=message=>{throw new TypeError(`managed_postmerge_input_invalid: ${message}`);};
export function materializeManagedPostmergeSource({stableRoot,sandboxRoot,planDigest,operationId,gitExecutable}={}){
 if(!UUID.test(operationId||'')||!HASH.test(planDigest||''))fail('operation UUID or plan digest invalid');
 safeDirectory(sandboxRoot);
 const store=path.join(sandboxRoot,'artifacts','managed-postmerge-builds'),operationRoot=path.join(store,operationId);
 if(fs.existsSync(operationRoot))fail('operation already used; refusing reuse');
 const firstBudget=assertStoreBudget({sandboxRoot});if(firstBudget.operationCount>=4)fail('four build operations already exist');
 const evidence=readSelectedMergeEvidence({sandboxRoot,planDigest});
 const {snapshot,files}=readManagedPostmergeSourcePayload({stableRoot,sandboxRoot,...evidence,gitExecutable});
 const intent={format:1,mode:'managed_postmerge_build_intent_v1',operationId,planDigest,sourceSnapshotDigest:snapshot.snapshotDigest,stage:'materializing',readyForLaunch:false};
 const materialized=seal({format:1,mode:'managed_postmerge_materialized_v1',operationId,planDigest,snapshotDigest:snapshot.snapshotDigest,sourceInventoryDigest:digest(snapshot.files),files:snapshot.files,rawBytes:snapshot.files.reduce((n,file)=>n+file.byteLength,0),readyForLaunch:false});
 const records=[intent,snapshot,materialized].map(value=>Buffer.byteLength(canonical(value)+'\n'));
 const logical=files.reduce((n,file)=>n+file.bytes.length,0)+records.reduce((a,b)=>a+b,0);
 const allocation=[...files.map(file=>file.bytes.length),...records].reduce((n,size)=>n+Math.ceil(size/CLUSTER)*CLUSTER,0);
 assertStoreBudget({sandboxRoot,reserveLogical:logical,reserveAllocation:allocation+CLUSTER});
 const stat=fs.statfsSync(sandboxRoot);if(Number(stat.bavail)*Number(stat.bsize)<allocation+16*1024*1024)fail('insufficient free capacity');
 const claim=claimBuildStore({sandboxRoot,operationId,kind:'materialize'});
 try{
 // Keep the claim and every partial owned file on any uncertainty. No replay or cleanup.
 if(fs.existsSync(operationRoot))fail('operation appeared during claim');
 if(assertStoreBudget({sandboxRoot,reserveLogical:logical,reserveAllocation:allocation}).operationCount>=4)fail('build count changed during claim');
 fs.mkdirSync(operationRoot);syncDirectory(store);safeDirectory(operationRoot);
 writeCanonicalExclusive(path.join(operationRoot,'intent.json'),intent);
 writeCanonicalExclusive(path.join(operationRoot,'source.json'),snapshot);
 const sourceRoot=path.join(operationRoot,'source');fs.mkdirSync(sourceRoot);syncDirectory(operationRoot);
 for(const file of files){
  if(!safeRelative(file.path))fail('unsafe materialized source path');
  const target=path.join(sourceRoot,...file.path.split('/'));
  fs.mkdirSync(path.dirname(target),{recursive:true});safeDirectory(path.dirname(target));
  writeBytesExclusive(target,file.bytes,4*1024*1024);
  fs.chmodSync(target,file.mode==='100755'?0o755:0o644);
 }
 writeCanonicalExclusive(path.join(operationRoot,'materialized.json'),materialized);
 const verified=readMaterializedOperation({sandboxRoot,operationId});
 assertStoreBudget({sandboxRoot});claim.complete();return verified;
 }catch(error){claim.abandon();throw error;}
}
