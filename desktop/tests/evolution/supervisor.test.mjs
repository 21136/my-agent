import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { test } from "node:test";
import { createRequire } from "node:module";
import path from "node:path";

const require=createRequire(import.meta.url);
const {JournalStore}=require("../../../artifacts/evolution-control-build/journal.js");
const {BindingStore}=require("../../../artifacts/evolution-control-build/binding.js");
const {GrantStore}=require("../../../artifacts/evolution-control-build/authorization.js");
const {EvolutionSupervisor}=require("../../../artifacts/evolution-control-build/supervisor.js");
const {canonicalJson,contentHash}=require("../../../artifacts/evolution-control-build/contracts.js");
const {validateProcessGraph}=require("../../../artifacts/evolution-control-build/runner.js");
const uuid=(d)=>`${d.repeat(8)}-${d.repeat(4)}-4${d.repeat(3)}-8${d.repeat(3)}-${d.repeat(12)}`;
const hash=(letter)=>`sha256:${letter.repeat(64)}`;
function setup(){
  const run=process.env.EVOLUTION_TEST_ROOT;assert.ok(run);assert.equal(readFileSync(path.join(run,".evolution-test-run"),"utf8").replace(/^\uFEFF/,"").trim(),"evolution-a");
  const root=mkdtempSync(path.join(run,"supervisor-"));writeFileSync(path.join(root,".evolution-test-run"),"evolution-a\n");const journal=new JournalStore(root);const bindings=new BindingStore(journal);
  const binding=bindings.bind({conversation_id:uuid("3"),scope_id:uuid("6"),host_instance_id:uuid("4")});const grants=new GrantStore(journal);
  return {run,root,journal,bindings,binding,grants};
}
function request(binding,authorization_id,action,payload,revision){return {protocol:"evolution/1.0",operation_id:uuid("1"),evolution_id:binding.evolution_id,conversation_id:binding.conversation_id,host_run_id:uuid("4"),expected_state_revision:revision,authorization_id,action,payload};}

test("failed candidate build stays attached to the same control conversation and records terminal evidence",async()=>{
  const ctx=setup();const snapshot=hash("a");const descriptor={descriptor_version:"1.0",source_snapshot_id:snapshot,isolation_mode:"local_managed"};const digest=contentHash({descriptor,source_snapshot_id:snapshot,isolation_mode:"local_managed"});const binding=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id);
  const auth=ctx.grants.createConfirmed({binding,kind:"candidate.build",constraints:{source_snapshot_id:snapshot,build_input_digest:digest,isolation_mode:"local_managed"},confirmation_id:uuid("5"),expires_at:"2099-01-01T00:00:00.000Z"});
  const snapshotTree=path.join(ctx.root,"snapshot-tree");require("node:fs").mkdirSync(snapshotTree);writeFileSync(path.join(snapshotTree,"main.cjs"),"fixture");
  const calls=[];const runner={lifecycle:"declared_components_only",prepare:async()=>{calls.push("prepare");throw Object.assign(new Error("fixture syntax rejected"),{code:"build_failed"});},launch:async()=>{throw new Error("must not launch")},stop:async()=>{throw new Error("not owned")},inspect:()=>null};
  const workspace={inspect:async()=>({ok:true,workspace_revision:0}),patch:async()=>({ok:true}),snapshot:async()=>({ok:true})};
  const supervisor=new EvolutionSupervisor({journal:ctx.journal,bindings:ctx.bindings,grants:ctx.grants,runner,workspace,scopes:new Map([[ctx.binding.evolution_id,{scope_id:ctx.binding.scope_id,source_root:ctx.root,state_root:ctx.root,owned_paths:["main.cjs"],read_only_roots:[]}]]),runRoot:ctx.root,runtimePaths:new Map(),snapshots:new Map([[snapshot,{tree_root:snapshotTree,manifest_path:path.join(ctx.root,"snapshot-manifest.json")}]]),descriptors:new Map([[uuid("7"),descriptor]]),profileFixtures:new Map()});
  const before=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id).state_revision;
  const result=await supervisor.dispatch(request(ctx.binding,auth,"build",{source_snapshot_id:snapshot,descriptor_artifact_id:uuid("7"),build_input_digest:digest,isolation_mode:"local_managed"},before));
  assert.equal(result.status,"failed");assert.equal(result.error.code,"build_failed",JSON.stringify(result));assert.deepEqual(calls,["prepare"]);
  const projection=supervisor.project(ctx.binding.evolution_id,ctx.binding.conversation_id);
  assert.equal(projection.conversation_id,ctx.binding.conversation_id);assert.equal(projection.candidate_status,"failed");assert.equal(projection.main_host_replaced,false);
  assert.equal(ctx.journal.get(result.operation_id).effect,"terminal");ctx.journal.close();
});

test("future scene and adoption actions fail before journal/revision or side effects",async()=>{
  const ctx=setup();let invoked=0;const runner={lifecycle:"declared_components_only",prepare:async()=>{invoked++;},launch:async()=>{invoked++;},stop:async()=>{invoked++;},inspect:()=>null};
  const workspace={inspect:async()=>{invoked++;return {ok:true}},patch:async()=>{invoked++;return {ok:true}},snapshot:async()=>{invoked++;return {ok:true}}};
  const supervisor=new EvolutionSupervisor({journal:ctx.journal,bindings:ctx.bindings,grants:ctx.grants,runner,workspace,scopes:new Map(),runRoot:ctx.root,runtimePaths:new Map(),snapshots:new Map(),descriptors:new Map(),profileFixtures:new Map()});
  const revision=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id).state_revision;
  const req=request(ctx.binding,uuid("5"),"capture_scene",{},revision);const result=await supervisor.dispatch(req);
  assert.equal(result.status,"failed");assert.equal(result.error.code,"unsupported_capability");assert.equal(invoked,0);
  assert.equal(ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id).state_revision,revision);assert.equal(ctx.journal.get(req.operation_id),null);ctx.journal.close();
});

test("inspect reads the current supervisor binding and never asks a runner to launch",async()=>{
  const ctx=setup();let launchCalls=0;const runner={lifecycle:"declared_components_only",prepare:async()=>{throw new Error("unused")},launch:async()=>{launchCalls++;},stop:async()=>null,inspect:()=>null};
  const workspace={inspect:async()=>({ok:true,workspace_revision:0,files:[]}),patch:async()=>({ok:true}),snapshot:async()=>{snapshotId=contentHash(snapshotManifest);writeFileSync(manifestPath,canonicalJson(snapshotManifest));return{ok:true,source_snapshot_id:snapshotId,tree_root:treeRoot,manifest_path:manifestPath,workspace_revision:0,manifest:snapshotManifest}}};
  const supervisor=new EvolutionSupervisor({journal:ctx.journal,bindings:ctx.bindings,grants:ctx.grants,runner,workspace,scopes:new Map([[ctx.binding.evolution_id,{scope_id:ctx.binding.scope_id,source_root:ctx.root,state_root:ctx.root,owned_paths:["main.cjs"],read_only_roots:[]}]]),runRoot:ctx.root,runtimePaths:new Map(),snapshots:new Map(),descriptors:new Map(),profileFixtures:new Map()});
  const result=await supervisor.dispatch(request(ctx.binding,uuid("5"),"inspect",{query:"summary"},0));
  assert.equal(result.status,"succeeded");assert.equal(result.result.conversation_id,ctx.binding.conversation_id);assert.equal(launchCalls,0);
  assert.equal(ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id).state_revision,0);ctx.journal.close();
});


test("untrusted or stale runtime events cannot update the current projection",()=>{
  const ctx=setup();const runner={lifecycle:"declared_components_only",prepare:async()=>{throw new Error("unused")},launch:async()=>null,stop:async()=>null,inspect:()=>null};
  const workspace={inspect:async()=>({ok:true}),patch:async()=>({ok:true}),snapshot:async()=>({ok:true})};
  const supervisor=new EvolutionSupervisor({journal:ctx.journal,bindings:ctx.bindings,grants:ctx.grants,runner,workspace,scopes:new Map(),runRoot:ctx.root,runtimePaths:new Map(),snapshots:new Map(),descriptors:new Map(),profileFixtures:new Map()});
  const accepted=supervisor.acceptRuntimeEvent({evolution_id:ctx.binding.evolution_id,conversation_id:ctx.binding.conversation_id,candidate_id:hash("a"),instance_id:uuid("7"),instance_sequence:1,kind:"runtime.ready",origin:"candidate_reported",payload:{asserted_health:true,secret:"not returned"}});
  assert.equal(accepted,false);const projection=supervisor.project(ctx.binding.evolution_id,ctx.binding.conversation_id);assert.equal(projection.current_instance_id,null);assert.equal(projection.candidate_status,"not_run");assert.equal(projection.late_event_count,1);assert.equal(Object.hasOwn(projection,"late_events"),false);ctx.journal.close();
});


test("T4 snapshot registration creates a descriptor only for the exact recovery manifest",async()=>{
  const ctx=setup();const owned=["evolve/tool-catalog/recovery/main.cjs","evolve/tool-catalog/recovery/manifest.json","evolve/tool-catalog/recovery/service.cjs"];
  const files=owned.map((file,index)=>({path:file,byte_length:1,sha256:hash(String.fromCharCode(97+index))}));
  const manifest={format:"evolution-source-snapshot/1",scope_id:ctx.binding.scope_id,workspace_revision:0,exclusion_policy:"evolution-sandbox-v1",case_policy:"windows-case-insensitive-v1",files};
  let snapshotManifest=manifest;let snapshotId=contentHash(snapshotManifest);const manifestPath=path.join(ctx.root,"snapshot-manifest.json");const treeRoot=path.join(ctx.root,"snapshot-tree");require("node:fs").mkdirSync(treeRoot);writeFileSync(manifestPath,canonicalJson(snapshotManifest));
  const scope={scope_id:ctx.binding.scope_id,source_root:ctx.root,owned_paths:["evolve/tool-catalog/INDEX.md",...owned],state_root:path.join(ctx.root,"workspace-state"),read_only_roots:[]};
  const workspace={inspect:async()=>({ok:true,workspace_revision:0,files:[]}),patch:async()=>({ok:true}),snapshot:async()=>{snapshotId=contentHash(snapshotManifest);writeFileSync(manifestPath,canonicalJson(snapshotManifest));return{ok:true,source_snapshot_id:snapshotId,tree_root:treeRoot,manifest_path:manifestPath,workspace_revision:0,manifest:snapshotManifest}}};
  const factory={artifact_id:"99999999-9999-4999-8999-999999999999",kind:"t4-recovery-node-v1",runtime_id:"77777777-7777-4777-8777-777777777777",profile_fixture_id:"88888888-8888-4888-8888-888888888888"};
  const descriptors=new Map();const supervisor=new EvolutionSupervisor({journal:ctx.journal,bindings:ctx.bindings,grants:ctx.grants,runner:{lifecycle:"declared_components_only",prepare:async()=>{throw new Error("unused")},launch:async()=>{throw new Error("unused")},stop:async()=>{throw new Error("unused")},inspect:()=>null},workspace,scopes:new Map([[ctx.binding.evolution_id,scope]]),runRoot:ctx.root,runtimePaths:new Map([[factory.runtime_id,process.execPath]]),snapshots:new Map(),descriptors,profileFixtures:new Map(),descriptorFactories:[factory]});
  const binding=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id);const grant=ctx.grants.createConfirmed({binding,kind:"source.patch",constraints:{owned_paths:scope.owned_paths,expected_before_hashes:Object.fromEntries(scope.owned_paths.map(item=>[item,"missing"]))},confirmation_id:uuid("5"),expires_at:"2099-01-01T00:00:00.000Z"});const current=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id);
  const result=await supervisor.dispatch(request(current,grant,"snapshot",{workspace_revision:0,include_paths:owned},current.state_revision));
  assert.equal(result.status,"succeeded",JSON.stringify(result));const descriptor=descriptors.get(factory.artifact_id);
  assert.ok(descriptor);assert.equal(descriptor.source_snapshot_id,snapshotId);assert.deepEqual(validateProcessGraph(descriptor).map(item=>item.entrypoint),["evolve/tool-catalog/recovery/main.cjs","evolve/tool-catalog/recovery/service.cjs"]);assert.equal(descriptor.runtimes[0].distribution.sha256,"sha256:"+createHash("sha256").update(require("node:fs").readFileSync(process.execPath)).digest("hex"));
  snapshotManifest={...manifest,files:[{path:"evolve/tool-catalog/INDEX.md",byte_length:1,sha256:hash("d")}]};
  const nextBinding=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id);const nextGrant=ctx.grants.createConfirmed({binding:nextBinding,kind:"source.patch",constraints:{owned_paths:scope.owned_paths,expected_before_hashes:Object.fromEntries(scope.owned_paths.map(item=>[item,"missing"]))},confirmation_id:uuid("6"),expires_at:"2099-01-01T00:00:00.000Z"});const latest=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id);
  const unrelated=await supervisor.dispatch({...request(latest,nextGrant,"snapshot",{workspace_revision:0,include_paths:["evolve/tool-catalog/INDEX.md"]},latest.state_revision),operation_id:uuid("7")});assert.equal(unrelated.status,"succeeded");assert.equal(descriptors.has(factory.artifact_id),false,"an unrelated snapshot must not retain a stale T4 descriptor");
  ctx.journal.close();
});
test("one conversation can observe failure, patch, rebuild and receive a distinct ready candidate",async()=>{
  const ctx=setup();let content="broken source";let workspaceRevision=0;let snapshotNumber=0;const prepareCalls=[];let launchNumber=0;
  const workspace={inspect:async()=>({ok:true,workspace_revision:workspaceRevision}),patch:async(_scope,patch)=>{content=patch.content_utf8;workspaceRevision++;return {ok:true,path:patch.path,workspace_revision:workspaceRevision,after_hash:hash(content.includes("fixed")?"d":"c")}},snapshot:async(_scope,_paths,revision)=>{assert.equal(revision,workspaceRevision);snapshotNumber++;const id=hash(snapshotNumber===1?"a":"b");return {ok:true,source_snapshot_id:id,tree_root:path.join(ctx.root,`snapshot-${snapshotNumber}`),manifest_path:path.join(ctx.root,`snapshot-${snapshotNumber}.json`),workspace_revision:revision}}};
  const runner={lifecycle:"declared_components_only",prepare:async(input)=>{prepareCalls.push(input.descriptor.source_snapshot_id);const candidate_id=hash(prepareCalls.length===1?"c":"d");return {build_id:uuid(prepareCalls.length===1?"8":"9"),source_snapshot_id:input.descriptor.source_snapshot_id,build_fingerprint:hash(prepareCalls.length===1?"e":"f"),candidate_id,output_root:path.join(ctx.root,`build-${prepareCalls.length}`),artifact_hashes:{"main.cjs":hash(content.includes("fixed")?"d":"c")},isolation_mode:"local_managed"}},launch:async(build)=>{launchNumber++;return {candidate_id:build.candidate_id,instance_id:uuid(launchNumber===1?"5":"6"),components:[{component_id:uuid("2"),pid:5000+launchNumber,ready:launchNumber===2,exit_observed:launchNumber===1}],status:launchNumber===1?"failed":"ready",stop_scope:"declared_processes",descendants_verified:false}},stop:async(instance_id)=>({candidate_id:hash("d"),instance_id,components:[],status:"stopped",stop_scope:"declared_processes",descendants_verified:false}),inspect:()=>null};
  const scope={scope_id:ctx.binding.scope_id,source_root:ctx.root,state_root:ctx.root,owned_paths:["main.cjs"],read_only_roots:[]};
  const snapshotId=hash("a");const descriptorId=uuid("7");let descriptor={descriptor_version:"1.0",source_snapshot_id:snapshotId,isolation_mode:"local_managed"};
  const snapshots=new Map();const descriptors=new Map([[descriptorId,descriptor]]);
  const supervisor=new EvolutionSupervisor({journal:ctx.journal,bindings:ctx.bindings,grants:ctx.grants,runner,workspace,scopes:new Map([[ctx.binding.evolution_id,scope]]),runRoot:ctx.root,runtimePaths:new Map(),snapshots,descriptors,profileFixtures:new Map([[uuid("4"),path.join(ctx.root,"profile")]])});
  let sequence=0;const nextRequestId=()=>{sequence++;return uuid(String(sequence))};
  const nextRequest=(authorization_id,action,payload,revision)=>({...request(ctx.binding,authorization_id,action,payload,revision),operation_id:nextRequestId()});
  async function patch(text,expectedHash){const binding=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id);const auth=ctx.grants.createConfirmed({binding,kind:"source.patch",constraints:{owned_paths:["main.cjs"],expected_before_hashes:{"main.cjs":expectedHash}},confirmation_id:uuid(String((sequence%8)+1)),expires_at:"2099-01-01T00:00:00.000Z"});return supervisor.dispatch(nextRequest(auth,"patch",{path:"main.cjs",expected_before_hash:expectedHash,content_utf8:text},ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id).state_revision));}
  async function snapshot(){const binding=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id);const auth=ctx.grants.createConfirmed({binding,kind:"source.patch",constraints:{owned_paths:["main.cjs"],expected_before_hashes:{"main.cjs":"missing"}},confirmation_id:uuid(String((sequence%8)+1)),expires_at:"2099-01-01T00:00:00.000Z"});const result=await supervisor.dispatch(nextRequest(auth,"snapshot",{workspace_revision:workspaceRevision,include_paths:["main.cjs"]},ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id).state_revision));snapshots.set(result.result.source_snapshot_id,{source_snapshot_id:result.result.source_snapshot_id,tree_root:result.result.tree_root,manifest_path:result.result.manifest_path,workspace_revision:result.result.workspace_revision});return result;}
  async function buildAndLaunch(){const snapshotRef=[...snapshots.values()].at(-1);descriptor={descriptor_version:"1.0",source_snapshot_id:snapshotRef.source_snapshot_id,isolation_mode:"local_managed"};descriptors.set(descriptorId,descriptor);const inputDigest=contentHash({descriptor,source_snapshot_id:snapshotRef.source_snapshot_id,isolation_mode:"local_managed"});let binding=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id);const buildAuth=ctx.grants.createConfirmed({binding,kind:"candidate.build",constraints:{source_snapshot_id:snapshotRef.source_snapshot_id,build_input_digest:inputDigest,isolation_mode:"local_managed"},confirmation_id:uuid(String((sequence%8)+1)),expires_at:"2099-01-01T00:00:00.000Z"});const built=await supervisor.dispatch(nextRequest(buildAuth,"build",{source_snapshot_id:snapshotRef.source_snapshot_id,descriptor_artifact_id:descriptorId,build_input_digest:inputDigest,isolation_mode:"local_managed"},ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id).state_revision));binding=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id);const launchAuth=ctx.grants.createConfirmed({binding,kind:"candidate.launch",constraints:{candidate_id:built.result.candidate_id,profile_fixture_id:uuid("4"),isolation_mode:"local_managed"},confirmation_id:uuid(String((sequence%8)+1)),expires_at:"2099-01-01T00:00:00.000Z"});return supervisor.dispatch(nextRequest(launchAuth,"launch",{candidate_id:built.result.candidate_id,profile_fixture_id:uuid("4")},ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id).state_revision));}
  const firstPatch=await patch("broken source changed","missing");assert.equal(firstPatch.status,"succeeded");const firstSnapshot=await snapshot();assert.equal(firstSnapshot.status,"succeeded");const failedLaunch=await buildAndLaunch();assert.equal(failedLaunch.status,"failed");assert.equal(failedLaunch.result.status,"failed");
  const secondPatch=await patch("fixed source","sha256:"+"c".repeat(64));assert.equal(secondPatch.status,"succeeded");const secondSnapshot=await snapshot();assert.equal(secondSnapshot.status,"succeeded");const readyLaunch=await buildAndLaunch();assert.equal(readyLaunch.status,"succeeded");assert.equal(readyLaunch.result.status,"ready");assert.notEqual(failedLaunch.result.candidate_id,readyLaunch.result.candidate_id);
  const projection=supervisor.project(ctx.binding.evolution_id,ctx.binding.conversation_id);assert.equal(projection.conversation_id,ctx.binding.conversation_id);assert.equal(projection.current_candidate_id,readyLaunch.result.candidate_id);assert.equal(projection.candidate_status,"ready");assert.equal(projection.main_host_replaced,false);assert.equal(prepareCalls.length,2);ctx.journal.close();
});

function readyStopScenario(ctx,{stopReceipt,onStop,stopError,launchStatus="ready"}={}){
  const sourceSnapshotId=hash("a");const candidateId=hash("b");const descriptorId=uuid("7");const profileId=uuid("6");const instanceId=uuid("9");const componentId=uuid("2");
  const descriptor={descriptor_version:"1.0",source_snapshot_id:sourceSnapshotId,isolation_mode:"local_managed"};
  const digest=contentHash({descriptor,source_snapshot_id:sourceSnapshotId,isolation_mode:"local_managed"});
  const launched={candidate_id:candidateId,instance_id:instanceId,components:[{component_id:componentId,pid:9001,ready:true,exit_observed:false}],status:launchStatus,stop_scope:"declared_processes",descendants_verified:false};
  let stopCalls=0;
  const runner={lifecycle:"declared_components_only",prepare:async()=>({build_id:uuid("8"),source_snapshot_id:sourceSnapshotId,build_fingerprint:hash("c"),candidate_id:candidateId,output_root:ctx.root,artifact_hashes:{"main.cjs":hash("d")},isolation_mode:"local_managed"}),launch:async()=>launched,stop:async(target)=>{stopCalls++;if(onStop)onStop({target,stopCalls});if(stopError)throw stopError;return structuredClone(stopReceipt??{...launched,components:[{...launched.components[0],exit_observed:true}],status:"stopped"});},inspect:()=>launched};
  const workspace={inspect:async()=>({ok:true}),patch:async()=>({ok:true}),snapshot:async()=>({ok:true})};
  const snapshots=new Map([[sourceSnapshotId,{source_snapshot_id:sourceSnapshotId,tree_root:path.join(ctx.root,"snapshot-tree"),manifest_path:path.join(ctx.root,"snapshot-manifest.json"),workspace_revision:0}]]);
  const descriptors=new Map([[descriptorId,descriptor]]);
  const supervisor=new EvolutionSupervisor({journal:ctx.journal,bindings:ctx.bindings,grants:ctx.grants,runner,workspace,scopes:new Map([[ctx.binding.evolution_id,{scope_id:ctx.binding.scope_id,source_root:ctx.root,state_root:ctx.root,owned_paths:["main.cjs"],read_only_roots:[]}]]),runRoot:ctx.root,runtimePaths:new Map(),snapshots,descriptors,profileFixtures:new Map([[profileId,path.join(ctx.root,"profile")]])});
  const buildBinding=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id);
  const buildAuthorizationId=ctx.grants.createConfirmed({binding:buildBinding,kind:"candidate.build",constraints:{source_snapshot_id:sourceSnapshotId,build_input_digest:digest,isolation_mode:"local_managed"},confirmation_id:uuid("5"),expires_at:"2099-01-01T00:00:00.000Z"});
  const buildRevision=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id).state_revision;
  return (async()=>{
    const built=await supervisor.dispatch({...request(ctx.binding,buildAuthorizationId,"build",{source_snapshot_id:sourceSnapshotId,descriptor_artifact_id:descriptorId,build_input_digest:digest,isolation_mode:"local_managed"},buildRevision),operation_id:uuid("1")});
    assert.equal(built.status,"succeeded",JSON.stringify(built));
    const launchBinding=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id);
    const launchAuthorizationId=ctx.grants.createConfirmed({binding:launchBinding,kind:"candidate.launch",constraints:{candidate_id:candidateId,profile_fixture_id:profileId,isolation_mode:"local_managed"},confirmation_id:uuid("3"),expires_at:"2099-01-01T00:00:00.000Z"});
    const launchRevision=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id).state_revision;
    const started=await supervisor.dispatch({...request(ctx.binding,launchAuthorizationId,"launch",{candidate_id:candidateId,profile_fixture_id:profileId},launchRevision),operation_id:uuid("4")});
    assert.equal(started.status,launchStatus==="failed"?"failed":"succeeded",JSON.stringify(started));assert.equal(started.result.status,launchStatus);
    return {supervisor,runner,instanceId,componentId,stopAuthorizationId:started.result.stop_authorization_id,stopCalls:()=>stopCalls};
  })();
}

test("host disconnect freezes a binding even before its projection was initialized",async()=>{
  const ctx=setup();let patchCalls=0;const workspace={inspect:async()=>({ok:true}),patch:async()=>{patchCalls++;return {ok:true}},snapshot:async()=>({ok:true})};
  const runner={lifecycle:"declared_components_only",prepare:async()=>null,launch:async()=>null,stop:async()=>null,inspect:()=>null};
  const supervisor=new EvolutionSupervisor({journal:ctx.journal,bindings:ctx.bindings,grants:ctx.grants,runner,workspace,scopes:new Map([[ctx.binding.evolution_id,{scope_id:ctx.binding.scope_id,source_root:ctx.root,state_root:ctx.root,owned_paths:["main.cjs"],read_only_roots:[]}]]),runRoot:ctx.root,runtimePaths:new Map(),snapshots:new Map(),descriptors:new Map(),profileFixtures:new Map()});
  const authorizationId=ctx.grants.createConfirmed({binding:ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id),kind:"source.patch",constraints:{owned_paths:["main.cjs"],expected_before_hashes:{"main.cjs":"missing"}},confirmation_id:uuid("5"),expires_at:"2099-01-01T00:00:00.000Z"});
  supervisor.disconnectHost(ctx.binding.host_instance_id);
  const current=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id);
  const req={...request(ctx.binding,authorizationId,"patch",{path:"main.cjs",expected_before_hash:"missing",content_utf8:"new"},current.state_revision),operation_id:uuid("1")};
  const result=await supervisor.dispatch(req);
  assert.equal(result.status,"failed");assert.equal(result.error.code,"permission_denied");assert.equal(patchCalls,0);assert.equal(ctx.journal.get(req.operation_id),null);ctx.journal.close();
});

test("stop revokes run capabilities before stopping only the authorized instance",async()=>{
  const ctx=setup();let stopSawRevoked=false;let stoppedTarget=null;
  const scenario=await readyStopScenario(ctx,{onStop:({target})=>{stoppedTarget=target;stopSawRevoked=ctx.journal.readEvents().some(event=>event.kind==="grant.revoked");}});
  const wrongBinding=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id);
  const wrong={...request(ctx.binding,scenario.stopAuthorizationId,"stop",{instance_id:uuid("a")},wrongBinding.state_revision),operation_id:uuid("5")};
  const denied=await scenario.supervisor.dispatch(wrong);assert.equal(denied.status,"failed");assert.equal(denied.error.code,"permission_denied");assert.equal(scenario.stopCalls(),0);
  const binding=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id);
  const stop={...request(ctx.binding,scenario.stopAuthorizationId,"stop",{instance_id:scenario.instanceId},binding.state_revision),operation_id:uuid("6")};
  const result=await scenario.supervisor.dispatch(stop);
  assert.equal(result.status,"succeeded",JSON.stringify(result));assert.equal(result.result.status,"stopped");assert.equal(stoppedTarget,scenario.instanceId);assert.equal(stopSawRevoked,true);assert.equal(scenario.stopCalls(),1);ctx.journal.close();
});

test("supervisor never reports stopped without observed component exits",async()=>{
  const ctx=setup();
  const scenario=await readyStopScenario(ctx,{stopReceipt:{candidate_id:hash("b"),instance_id:uuid("9"),components:[{component_id:uuid("2"),pid:9001,ready:true,exit_observed:false}],status:"stopped",stop_scope:"declared_processes",descendants_verified:false}});
  const binding=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id);
  const stop={...request(ctx.binding,scenario.stopAuthorizationId,"stop",{instance_id:scenario.instanceId},binding.state_revision),operation_id:uuid("6")};
  const result=await scenario.supervisor.dispatch(stop);
  assert.equal(result.status,"succeeded",JSON.stringify(result));assert.notEqual(result.result.status,"stopped");assert.equal(result.result.status,"quarantined");
  assert.equal(scenario.supervisor.project(ctx.binding.evolution_id,ctx.binding.conversation_id).candidate_status,"quarantined");ctx.journal.close();
});

test("a fresh operation id cannot bypass an unresolved side effect",async()=>{
  const ctx=setup();let patchCalls=0;const workspace={inspect:async()=>({ok:true}),patch:async()=>{patchCalls++;return {ok:true}},snapshot:async()=>({ok:true})};
  const runner={lifecycle:"declared_components_only",prepare:async()=>null,launch:async()=>null,stop:async()=>null,inspect:()=>null};
  const supervisor=new EvolutionSupervisor({journal:ctx.journal,bindings:ctx.bindings,grants:ctx.grants,runner,workspace,scopes:new Map([[ctx.binding.evolution_id,{scope_id:ctx.binding.scope_id,source_root:ctx.root,state_root:ctx.root,owned_paths:["main.cjs"],read_only_roots:[]}]]),runRoot:ctx.root,runtimePaths:new Map(),snapshots:new Map(),descriptors:new Map(),profileFixtures:new Map()});
  const authorizationId=ctx.grants.createConfirmed({binding:ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id),kind:"source.patch",constraints:{owned_paths:["main.cjs"],expected_before_hashes:{"main.cjs":"missing"}},confirmation_id:uuid("5"),expires_at:"2099-01-01T00:00:00.000Z"});
  const current=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id);
  const unresolved={...request(ctx.binding,authorizationId,"patch",{path:"main.cjs",expected_before_hash:"missing",content_utf8:"uncertain"},current.state_revision),operation_id:uuid("1")};
  ctx.journal.begin(unresolved,{...current,state_revision:current.state_revision+1});ctx.journal.markEffect(unresolved.operation_id,"started");
  const after=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id);
  const retry={...request(ctx.binding,authorizationId,"patch",{path:"main.cjs",expected_before_hash:"missing",content_utf8:"different"},after.state_revision),operation_id:uuid("2")};
  const result=await supervisor.dispatch(retry);
  assert.equal(result.status,"unknown",JSON.stringify(result));assert.equal(result.error.code,"outcome_unknown",JSON.stringify(result));assert.equal(ctx.journal.get(retry.operation_id),null);assert.equal(patchCalls,0);assert.ok(supervisor.project(ctx.binding.evolution_id,ctx.binding.conversation_id).unknown_operation_ids.includes(unresolved.operation_id));ctx.journal.close();
});

test("a frozen binding still permits stopping its own instance",async()=>{
  const ctx=setup();const scenario=await readyStopScenario(ctx);scenario.supervisor.disconnectHost(ctx.binding.host_instance_id);
  const binding=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id);const stop={...request(ctx.binding,scenario.stopAuthorizationId,"stop",{instance_id:scenario.instanceId},binding.state_revision),operation_id:uuid("a")};
  const result=await scenario.supervisor.dispatch(stop);
  assert.equal(result.status,"succeeded",JSON.stringify(result));assert.equal(result.result.status,"stopped");assert.equal(scenario.stopCalls(),1);assert.equal(ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id).status,"frozen");ctx.journal.close();
});

test("unknown stop outcome quarantines the current instance",async()=>{
  const ctx=setup();const scenario=await readyStopScenario(ctx,{stopError:Object.assign(new Error("exit could not be observed"),{code:"outcome_unknown"})});
  const binding=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id);const stop={...request(ctx.binding,scenario.stopAuthorizationId,"stop",{instance_id:scenario.instanceId},binding.state_revision),operation_id:uuid("b")};
  const result=await scenario.supervisor.dispatch(stop);
  assert.equal(result.status,"unknown",JSON.stringify(result));assert.equal(scenario.supervisor.project(ctx.binding.evolution_id,ctx.binding.conversation_id).candidate_status,"quarantined");ctx.journal.close();
});

test("trusted host binding leases only an allowlisted scope and issues a hash-bound patch grant",async()=>{
  const run=process.env.EVOLUTION_TEST_ROOT;assert.ok(run);
  const root=mkdtempSync(path.join(run,"trusted-bind-"));writeFileSync(path.join(root,".evolution-test-run"),"evolution-a\n");
  const journal=new JournalStore(root);const bindings=new BindingStore(journal);const grants=new GrantStore(journal);
  const conversationId="20261026-1a2b3c4d",scopeId=uuid("6"),hostInstanceId=uuid("4"),confirmationId=uuid("5");
  const ownedPaths=["main.cjs","new.cjs"];
  const scope={scope_id:scopeId,source_root:root,state_root:path.join(root,"state"),owned_paths:ownedPaths,read_only_roots:[]};
  const inspectCalls=[];
  const workspace={inspect:async(_scope,query,operationId)=>{inspectCalls.push({query,operationId});return {ok:true,workspace_revision:7,files:[{path:"main.cjs",byte_length:5,sha256:hash("a")}]};},patch:async()=>({ok:true}),snapshot:async()=>({ok:true})};
  const runner={lifecycle:"declared_components_only",prepare:async()=>{throw new Error("unused")},launch:async()=>{throw new Error("unused")},stop:async()=>{throw new Error("unused")},inspect:()=>null};
  const supervisor=new EvolutionSupervisor({journal,bindings,grants,runner,workspace,scopes:new Map([[scopeId,scope]]),runRoot:root,runtimePaths:new Map(),snapshots:new Map(),descriptors:new Map(),profileFixtures:new Map()});
  assert.equal(typeof supervisor.bindFromTrustedHost,"function");
  const result=await supervisor.bindFromTrustedHost({conversation_id:conversationId,scope_id:scopeId,host_instance_id:hostInstanceId,allowed_scope_ids:[scopeId],confirmation_id:confirmationId,expires_at:"2099-01-01T00:00:00.000Z"});
  assert.equal(result.scope_id,scopeId);assert.equal(result.workspace_revision,7);assert.match(result.evolution_id,/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);assert.equal(typeof result.authorization_id,"string");
  assert.equal(inspectCalls.length,1);assert.deepEqual(inspectCalls[0].query,{query:"source_list"});
  const binding=bindings.require(result.evolution_id,conversationId);
  const patch={protocol:"evolution/1.0",operation_id:uuid("1"),evolution_id:result.evolution_id,conversation_id:conversationId,host_run_id:uuid("8"),expected_state_revision:0,authorization_id:result.authorization_id,action:"patch",payload:{path:"main.cjs",expected_before_hash:hash("a"),content_utf8:"safe"}};
  assert.doesNotThrow(()=>grants.require(patch,binding));
  assert.throws(()=>grants.require({...patch,payload:{...patch.payload,path:"outside.cjs"}},binding),error=>error.code==="permission_denied");
  assert.throws(()=>grants.require({...patch,payload:{...patch.payload,expected_before_hash:hash("b")}},binding),error=>error.code==="permission_denied");
  assert.equal(Object.hasOwn(result,"files"),false);journal.close();
});

test("trusted host binding rejects a scope outside the bootstrap allowlist before reading source",async()=>{
  const run=process.env.EVOLUTION_TEST_ROOT;assert.ok(run);
  const root=mkdtempSync(path.join(run,"trusted-bind-denied-"));writeFileSync(path.join(root,".evolution-test-run"),"evolution-a\n");
  const journal=new JournalStore(root);const bindings=new BindingStore(journal);const grants=new GrantStore(journal);let inspectCalls=0;
  const scope={scope_id:uuid("6"),source_root:root,state_root:path.join(root,"state"),owned_paths:["main.cjs"],read_only_roots:[]};
  const workspace={inspect:async()=>{inspectCalls++;return {ok:true,workspace_revision:0,files:[]}},patch:async()=>({ok:true}),snapshot:async()=>({ok:true})};
  const runner={lifecycle:"declared_components_only",prepare:async()=>null,launch:async()=>null,stop:async()=>null,inspect:()=>null};
  const supervisor=new EvolutionSupervisor({journal,bindings,grants,runner,workspace,scopes:new Map([[scope.scope_id,scope]]),runRoot:root,runtimePaths:new Map(),snapshots:new Map(),descriptors:new Map(),profileFixtures:new Map()});
  await assert.rejects(()=>supervisor.bindFromTrustedHost({conversation_id:uuid("3"),scope_id:scope.scope_id,host_instance_id:uuid("4"),allowed_scope_ids:[uuid("7")],confirmation_id:uuid("5"),expires_at:"2099-01-01T00:00:00.000Z"}),error=>error.code==="permission_denied");
  assert.equal(inspectCalls,0);assert.equal(journal.readEvents().length,0);journal.close();
});


test("trusted Host resolves only the existing exact-instance stop grant after revocation",async()=>{
  const ctx=setup();const scenario=await readyStopScenario(ctx);ctx.grants.revoke(ctx.binding.evolution_id);
  assert.equal(typeof scenario.supervisor.resolveStopAuthorizationFromTrustedHost,"function","Supervisor must restore the stop-only grant reference after sidecar session recovery");
  const input={conversation_id:ctx.binding.conversation_id,evolution_id:ctx.binding.evolution_id,instance_id:scenario.instanceId,host_instance_id:ctx.binding.host_instance_id,allowed_scope_ids:[ctx.binding.scope_id]};
  const resolved=scenario.supervisor.resolveStopAuthorizationFromTrustedHost(input);
  assert.equal(resolved.authorization_id,scenario.stopAuthorizationId);
  assert.throws(()=>scenario.supervisor.resolveStopAuthorizationFromTrustedHost({...input,instance_id:uuid("a")}),/instance|owned|current/i);
  assert.throws(()=>scenario.supervisor.resolveStopAuthorizationFromTrustedHost({...input,host_instance_id:uuid("b")}),/Host|host|binding|permission/i);
  ctx.journal.close();
});

test("failed but owned launch receipts retain a stop-only grant for cleanup",async()=>{
  const ctx=setup();
  const scenario=await readyStopScenario(ctx,{launchStatus:"failed"});
  assert.match(scenario.stopAuthorizationId,/^[0-9a-f-]{36}$/);
  const binding=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id);
  const stop={...request(ctx.binding,scenario.stopAuthorizationId,"stop",{instance_id:scenario.instanceId},binding.state_revision),operation_id:uuid("5")};
  assert.doesNotThrow(()=>ctx.grants.require(stop,binding));
  ctx.journal.close();
});
