import assert from "node:assert/strict";
import {mkdtempSync,readFileSync,writeFileSync} from "node:fs";
import path from "node:path";
import {createRequire} from "node:module";
import {test} from "node:test";
const require=createRequire(import.meta.url);const {EvolutionSupervisor}=require("../../../artifacts/evolution-control-build/supervisor.js");const {JournalStore}=require("../../../artifacts/evolution-control-build/journal.js");const {BindingStore}=require("../../../artifacts/evolution-control-build/binding.js");const {GrantStore}=require("../../../artifacts/evolution-control-build/authorization.js");const {contentHash}=require("../../../artifacts/evolution-control-build/contracts.js");
const uuid=d=>`${d.repeat(8)}-${d.repeat(4)}-4${d.repeat(3)}-8${d.repeat(3)}-${d.repeat(12)}`;const hash=d=>`sha256:${d.repeat(64)}`;
function setup(){const run=process.env.EVOLUTION_TEST_ROOT;assert.ok(run);assert.equal(readFileSync(path.join(run,".evolution-test-run"),"utf8").trim(),"evolution-a");const root=mkdtempSync(path.join(run,"host-auth-") );writeFileSync(path.join(root,".evolution-test-run"),"evolution-a\n");const journal=new JournalStore(root);const bindings=new BindingStore(journal);const binding=bindings.bind({conversation_id:uuid("3"),scope_id:uuid("5"),host_instance_id:uuid("4")});const grants=new GrantStore(journal);const scope={scope_id:binding.scope_id,source_root:root,state_root:root,owned_paths:["main.cjs"],read_only_roots:[]};const snapshotId=hash("a");const descriptorId=uuid("7");const descriptor={descriptor_version:"1.0",source_snapshot_id:snapshotId,isolation_mode:"local_managed"};const snapshots=new Map([[snapshotId,{source_snapshot_id:snapshotId,tree_root:path.join(root,"snapshot"),manifest_path:path.join(root,"manifest.json"),workspace_revision:0}]]);const descriptors=new Map([[descriptorId,descriptor]]);const supervisor=new EvolutionSupervisor({journal,bindings,grants,runner:{lifecycle:"declared_components_only",prepare:async()=>{throw new Error("unused")},launch:async()=>{throw new Error("unused")},stop:async()=>{throw new Error("unused")},inspect:()=>null},workspace:{inspect:async()=>({ok:true,workspace_revision:0,files:[]}),patch:async()=>({ok:true}),snapshot:async()=>({ok:true})},scopes:new Map([[binding.evolution_id,scope]]),runRoot:root,runtimePaths:new Map(),snapshots,descriptors,profileFixtures:new Map()});return{root,journal,bindings,binding,grants,scope,descriptor,snapshotId,supervisor};}

test("trusted authorization creates only a matching grant for the bound host, registered snapshot, and scope",async()=>{
  const ctx=setup();
  assert.equal(typeof ctx.supervisor.authorizeFromTrustedHost,"function","Supervisor must expose the trusted action-grant producer");
  const digest=contentHash({descriptor:ctx.descriptor,source_snapshot_id:ctx.snapshotId,isolation_mode:"local_managed"});
  const base={conversation_id:ctx.binding.conversation_id,evolution_id:ctx.binding.evolution_id,kind:"candidate.build",constraints:{source_snapshot_id:ctx.snapshotId,build_input_digest:digest,isolation_mode:"local_managed"},confirmation_id:uuid("6"),expires_at:new Date(Date.now()+300_000).toISOString(),host_instance_id:ctx.binding.host_instance_id,allowed_scope_ids:[ctx.binding.scope_id]};
  const issued=await ctx.supervisor.authorizeFromTrustedHost(base);
  assert.equal(issued.kind,"candidate.build");assert.equal(issued.evolution_id,ctx.binding.evolution_id);
  const req={protocol:"evolution/1.0",operation_id:uuid("1"),evolution_id:ctx.binding.evolution_id,conversation_id:ctx.binding.conversation_id,host_run_id:uuid("4"),expected_state_revision:0,authorization_id:issued.authorization_id,action:"build",payload:{source_snapshot_id:ctx.snapshotId,descriptor_artifact_id:uuid("7"),build_input_digest:digest,isolation_mode:"local_managed"}};
  assert.doesNotThrow(()=>ctx.grants.require(req,ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id)));
  await assert.rejects(()=>ctx.supervisor.authorizeFromTrustedHost({...base,confirmation_id:uuid("8"),host_instance_id:uuid("9")}),/host|binding|permission/i);
  await assert.rejects(()=>ctx.supervisor.authorizeFromTrustedHost({...base,confirmation_id:uuid("a"),allowed_scope_ids:[uuid("b")]}),/host|binding|permission/i);
  ctx.journal.close();
});




test("source.patch authorization is constrained to current owned paths and hashes",async()=>{
  const ctx=setup();
  const base={conversation_id:ctx.binding.conversation_id,evolution_id:ctx.binding.evolution_id,kind:"source.patch",confirmation_id:uuid("6"),expires_at:new Date(Date.now()+300_000).toISOString(),host_instance_id:ctx.binding.host_instance_id,allowed_scope_ids:[ctx.binding.scope_id]};
  const valid=await ctx.supervisor.authorizeFromTrustedHost({...base,constraints:{owned_paths:["main.cjs"],expected_before_hashes:{"main.cjs":"missing"}}});
  assert.equal(valid.kind,"source.patch");
  await assert.rejects(()=>ctx.supervisor.authorizeFromTrustedHost({...base,confirmation_id:uuid("7"),constraints:{owned_paths:["main.cjs"],expected_before_hashes:{"main.cjs":hash("f")}}}),error=>error.code==="stale_revision");
  await assert.rejects(()=>ctx.supervisor.authorizeFromTrustedHost({...base,confirmation_id:uuid("8"),constraints:{owned_paths:["../stable.ts"],expected_before_hashes:{"../stable.ts":"missing"}}}),/owned|path|permission/i);
  ctx.journal.close();
});

test("trusted patch authorization rejects a malformed or unowned source inventory",async()=>{
  const ctx=setup();ctx.supervisor.deps.workspace.inspect=async()=>({ok:true,workspace_revision:0,files:[{path:"other.cjs",byte_length:3,sha256:hash("a")}]});
  await assert.rejects(()=>ctx.supervisor.authorizeFromTrustedHost({conversation_id:ctx.binding.conversation_id,evolution_id:ctx.binding.evolution_id,kind:"source.patch",constraints:{owned_paths:["main.cjs"],expected_before_hashes:{"main.cjs":"missing"}},confirmation_id:uuid("6"),expires_at:new Date(Date.now()+300_000).toISOString(),host_instance_id:ctx.binding.host_instance_id,allowed_scope_ids:[ctx.binding.scope_id]}),error=>error.code==="scope_conflict");
  ctx.journal.close();
});

test("candidate.launch authorization binds only a supervisor-owned build and registered profile",async()=>{
  const ctx=setup();const descriptorId=uuid("7");const candidateId=hash("d");const profileId=uuid("8");const digest=contentHash({descriptor:ctx.descriptor,source_snapshot_id:ctx.snapshotId,isolation_mode:"local_managed"});
  ctx.supervisor.deps.profileFixtures.set(profileId,path.join(ctx.root,"candidate-profile"));
  ctx.supervisor.deps.runner.prepare=async()=>({build_id:uuid("9"),source_snapshot_id:ctx.snapshotId,build_fingerprint:hash("c"),candidate_id:candidateId,output_root:ctx.root,artifact_hashes:{"main.cjs":hash("a")},isolation_mode:"local_managed"});
  const binding=ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id);
  const buildGrant=ctx.grants.createConfirmed({binding,kind:"candidate.build",constraints:{source_snapshot_id:ctx.snapshotId,build_input_digest:digest,isolation_mode:"local_managed"},confirmation_id:uuid("a"),expires_at:"2099-01-01T00:00:00.000Z"});
  const built=await ctx.supervisor.dispatch({protocol:"evolution/1.0",operation_id:uuid("1"),evolution_id:ctx.binding.evolution_id,conversation_id:ctx.binding.conversation_id,host_run_id:uuid("4"),expected_state_revision:ctx.bindings.require(ctx.binding.evolution_id,ctx.binding.conversation_id).state_revision,authorization_id:buildGrant,action:"build",payload:{source_snapshot_id:ctx.snapshotId,descriptor_artifact_id:descriptorId,build_input_digest:digest,isolation_mode:"local_managed"}});
  assert.equal(built.status,"succeeded",JSON.stringify(built));
  const input={conversation_id:ctx.binding.conversation_id,evolution_id:ctx.binding.evolution_id,kind:"candidate.launch",constraints:{candidate_id:candidateId,profile_fixture_id:profileId,isolation_mode:"local_managed"},confirmation_id:uuid("b"),expires_at:new Date(Date.now()+300_000).toISOString(),host_instance_id:ctx.binding.host_instance_id,allowed_scope_ids:[ctx.binding.scope_id]};
  const grant=await ctx.supervisor.authorizeFromTrustedHost(input);assert.equal(grant.kind,"candidate.launch");
  await assert.rejects(()=>ctx.supervisor.authorizeFromTrustedHost({...input,confirmation_id:uuid("c"),constraints:{...input.constraints,candidate_id:hash("e")}}),/build|candidate|permission/i);
  ctx.journal.close();
});


