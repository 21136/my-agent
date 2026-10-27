import assert from "node:assert/strict";
import path from "node:path";
import {mkdirSync,mkdtempSync,writeFileSync} from "node:fs";
import {createRequire} from "node:module";
import {test} from "node:test";
const require=createRequire(import.meta.url);let api={};try{api=require("../../../artifacts/evolution-control-build/supervisor-process.js");}catch{}
const run=process.env.EVOLUTION_TEST_ROOT;assert.ok(run);
const uuid=(digit)=>`${digit.repeat(8)}-${digit.repeat(4)}-4${digit.repeat(3)}-8${digit.repeat(3)}-${digit.repeat(12)}`;
const hash=(digit)=>`sha256:${digit.repeat(64)}`;
function config(){
  const scopeId=uuid("5");
  const runtimeRoot=mkdtempSync(path.join(run,"supervisor-runtime-"));
  writeFileSync(path.join(runtimeRoot,".evolution-test-run"),"evolution-a\n");
  const releaseRoot=path.join(runtimeRoot,"control-release-fixture");
  const sourceRoot=path.join(runtimeRoot,"source-root");mkdirSync(sourceRoot);
  return{
    protocol:"my-agent-evolution-supervisor/1.0",
    sandbox_root:process.cwd(),run_root:runtimeRoot,
    host_bootstrap:{pipe_name:"\\\\.\\pipe\\host-fixture",host_instance_id:uuid("4"),host_token:"a".repeat(64),allowed_scope_ids:[scopeId],control_release:{release_id:hash("a"),root:releaseRoot,node_executable:process.execPath,python_executable:process.env.EVOLUTION_TRUSTED_PYTHON,manifest_path:path.join(releaseRoot,"manifest.json"),manifest_digest:hash("a")}},
    scopes:[{scope_id:scopeId,source_root:sourceRoot,owned_paths:["main.cjs"],state_root:path.join(runtimeRoot,"workspace-state"),read_only_roots:[]}],
    runtime_paths:{[uuid("8")]:process.execPath},
    descriptors:[],profile_fixtures:[{id:uuid("7"),root:path.join(runtimeRoot,"profiles","candidate")}],
  };
}
function parser(){assert.equal(typeof api.parseSupervisorProcessConfig,"function","supervisor-process must export parseSupervisorProcessConfig");return api.parseSupervisorProcessConfig;}

test("supervisor child config binds every allowed scope to a sandbox-owned path set",()=>{
  const parse=parser();const input=config();const checked=parse(input,process.cwd());
  assert.deepEqual(checked.host_bootstrap.allowed_scope_ids,[input.scopes[0].scope_id]);
  assert.deepEqual(checked.scopes[0].owned_paths,["main.cjs"]);
  assert.equal(checked.run_root,input.run_root);
  assert.throws(()=>parse({...input,host_bootstrap:{...input.host_bootstrap,allowed_scope_ids:[]}},process.cwd()),error=>error.code==="permission_denied");
  assert.throws(()=>parse({...input,run_root:"F:/outside/run"},process.cwd()),error=>error.code==="permission_denied");
});

test("T4 descriptor factory is accepted only for the fixed owned paths and Node runtime",()=>{
  const parse=parser();const input=config();
  const paths=["evolve/tool-catalog/INDEX.md","evolve/tool-catalog/recovery/main.cjs","evolve/tool-catalog/recovery/service.cjs","evolve/tool-catalog/recovery/manifest.json"];
  input.scopes[0].owned_paths=paths;
  input.runtime_paths={"77777777-7777-4777-8777-777777777777":process.execPath};
  input.profile_fixtures=[{id:"88888888-8888-4888-8888-888888888888",root:path.join(input.run_root,"candidate-profile")}];
  input.descriptor_factories=[{artifact_id:"99999999-9999-4999-8999-999999999999",kind:"t4-recovery-node-v1",runtime_id:"77777777-7777-4777-8777-777777777777",profile_fixture_id:"88888888-8888-4888-8888-888888888888"}];
  const checked=parse(input,process.cwd());assert.deepEqual(checked.descriptor_factories,input.descriptor_factories);
  const extra=structuredClone(input);extra.scopes[0].owned_paths.push("desktop/renderer/index.html");
  assert.throws(()=>parse(extra,process.cwd()),error=>error.code==="permission_denied");
  const changedRuntime=structuredClone(input);changedRuntime.runtime_paths["77777777-7777-4777-8777-777777777777"]=path.join(input.run_root,".evolution-test-run");
  assert.throws(()=>parse(changedRuntime,process.cwd()),error=>error.code==="permission_denied");
});
test("supervisor composition uses only injected trusted runner/workspace and closes its journal",async()=>{
  const create=api.createSupervisorRuntime;assert.equal(typeof create,"function","supervisor-process must export createSupervisorRuntime");
  const input=config();const calls=[];
  const workspace={inspect:async(_scope,query)=>({ok:true,workspace_revision:0,files:query.query==="source_list"?[{path:"main.cjs",byte_length:3,sha256:hash("b")}]:[]}),patch:async()=>({ok:true}),snapshot:async()=>({ok:true})};
  const runner={lifecycle:"declared_components_only",prepare:async()=>{throw new Error("unused")},launch:async()=>{throw new Error("unused")},stop:async()=>{throw new Error("unused")},inspect:()=>null};
  const runtime=await create(input,{verifyRelease:async release=>calls.push(release.release_id),createWorkspace:async options=>{calls.push(options.runRoot);return workspace},createRunner:()=>runner});
  assert.deepEqual(calls,[input.host_bootstrap.control_release.release_id,input.run_root]);
  const binding=await runtime.supervisor.bindFromTrustedHost({conversation_id:uuid("3"),scope_id:input.scopes[0].scope_id,host_instance_id:input.host_bootstrap.host_instance_id,allowed_scope_ids:input.host_bootstrap.allowed_scope_ids,confirmation_id:uuid("6"),expires_at:"2099-01-01T00:00:00.000Z"});
  assert.equal(runtime.supervisor.deps.journal.getBinding(binding.evolution_id).conversation_id,uuid("3"));
  runtime.closeJournal();
  assert.equal(runtime.isClosed(),true);
});

