import assert from "node:assert/strict";
import path from "node:path";
import {createRequire} from "node:module";
import {test} from "node:test";
const require=createRequire(import.meta.url);let api={};let bootstrapApi={};try{api=require("../../../artifacts/evolution-control-build/supervisor-host.js");bootstrapApi=require("../../../artifacts/evolution-control-build/sidecar-bootstrap.js");}catch{/* RED: producer has not been implemented yet. */}
const run=process.env.EVOLUTION_TEST_ROOT;assert.ok(run);
const uuid=(digit)=>`${digit.repeat(8)}-${digit.repeat(4)}-4${digit.repeat(3)}-8${digit.repeat(3)}-${digit.repeat(12)}`;
const hash=(digit)=>`sha256:${digit.repeat(64)}`;
function config(){const scopeId=uuid("5");const release={release_id:hash("a"),root:path.join(run,"fixed-release"),node_executable:process.execPath,python_executable:path.join(run,"trusted-python.exe"),manifest_path:path.join(run,"fixed-release","manifest.json"),manifest_digest:hash("a")};return{protocol:"my-agent-evolution-supervisor/1.0",sandbox_root:process.cwd(),sidecar_root:path.join(process.cwd(),"host-app"),run_root:path.join(run,"host-run"),control_release:release,scopes:[{scope_id:scopeId,source_root:path.join(process.cwd(),"host-app"),owned_paths:["desktop/electron/main.ts"],state_root:path.join(run,"host-run","workspace-state"),read_only_roots:[]}],runtime_paths:{},descriptors:[],profile_fixtures:[]};}
function factory(){const calls=[];const runtime={config:null,supervisor:{disconnectHost:id=>calls.push(["disconnect",id])},closeJournal:()=>calls.push(["journal.close"]),isClosed:()=>false};return{calls,runtime,createRuntime:async value=>{calls.push(["runtime.create",value]);runtime.config=value;return runtime;},serveSupervisor:async value=>{calls.push(["serve.start",value]);return{close:async()=>calls.push(["serve.close"])};},verifyControlRelease:async release=>calls.push(["release.verify",release.release_id]),createHostIdentity:()=>({pipe_name:"\\\\.\\pipe\\my-agent-evolution-test",host_instance_id:uuid("4"),host_token:"c".repeat(64)})};}
function producer(){assert.equal(typeof api.createEvolutionSupervisorHost,"function","supervisor-host must export createEvolutionSupervisorHost");return api.createEvolutionSupervisorHost;}

test("fixed release and one trusted scope compose a host bootstrap only for the registered sidecar root",async()=>{
  const create=producer();const input=config();const f=factory();const root=input.sidecar_root;const registry=new bootstrapApi.SidecarBootstrapRegistry();const serve=f.serveSupervisor;f.serveSupervisor=async value=>{assert.equal(registry.forRoot(root),null);const transport=await serve(value);return{close:async()=>{assert.equal(registry.forRoot(root),null,"private bootstrap registry must clear before host shutdown");await transport.close();}};};const host=await create(input,{factories:f,bootstrapRegistry:registry});
  assert.deepEqual(f.calls.slice(0,3).map(call=>call[0]),["release.verify","runtime.create","serve.start"]);
  const runtimeConfig=f.calls.find(call=>call[0]==="runtime.create")[1];assert.deepEqual(runtimeConfig.host_bootstrap.control_release,input.control_release);assert.deepEqual(runtimeConfig.host_bootstrap.allowed_scope_ids,[input.scopes[0].scope_id]);
  const served=f.calls.find(call=>call[0]==="serve.start")[1];assert.equal(served.supervisor,f.runtime.supervisor);assert.equal(served.bootstrap.host_token,"c".repeat(64));
  assert.ok(registry.forRoot(root));const bootstrap=host.getSidecarBootstrap(root);assert.equal(bootstrap.scope_id,input.scopes[0].scope_id);assert.deepEqual(bootstrap.owned_paths,input.scopes[0].owned_paths);assert.equal(bootstrap.control_release.release_id,input.control_release.release_id);
  assert.equal(registry.forRoot(process.cwd()),null);assert.equal(host.getSidecarBootstrap(process.cwd()),null);assert.equal(host.getSidecarBootstrap(path.join(root,"candidate")),null);
  await host.close();assert.equal(host.getSidecarBootstrap(root),null);assert.deepEqual(f.calls.slice(-3).map(call=>call[0]),["serve.close","disconnect","journal.close"]);
  await host.close();assert.equal(f.calls.filter(call=>call[0]==="serve.close").length,1);
});

test("a release verification failure starts neither the supervisor nor its host transport",async()=>{
  const create=producer();const f=factory();f.verifyControlRelease=async()=>{f.calls.push(["release.reject"]);throw new Error("fixed release rejected");};
  await assert.rejects(()=>create(config(),{factories:f}),/fixed release rejected/);
  assert.deepEqual(f.calls.map(call=>call[0]),["release.reject"]);
});

test("a host transport startup failure closes the runtime and publishes no sidecar bootstrap",async()=>{
  const create=producer();const f=factory();f.serveSupervisor=async()=>{f.calls.push(["serve.reject"]);throw new Error("host listener unavailable");};
  await assert.rejects(()=>create(config(),{factories:f}),/host listener unavailable/);
  assert.deepEqual(f.calls.slice(-2).map(call=>call[0]),["disconnect","journal.close"]);
});

test("sidecar bootstrap production rejects ambiguous multi-scope host configs",async()=>{
  const create=producer();const input=config();input.scopes.push({...input.scopes[0],scope_id:uuid("6")});const f=factory();
  await assert.rejects(()=>create(input,{factories:f}),/exactly one|single.*scope/i);
  assert.equal(f.calls.length,0);
});


test("runtime cannot substitute another fixed release path or source scope",async()=>{
  const create=producer();const input=config();const f=factory();f.createRuntime=async value=>{f.calls.push(["runtime.create",value]);f.runtime.config={...value,host_bootstrap:{...value.host_bootstrap,control_release:{...value.host_bootstrap.control_release,root:path.join(run,"substituted-release")}},scopes:[{...value.scopes[0],source_root:path.join(run,"substituted-source")} ]};return f.runtime;};
  await assert.rejects(()=>create(input,{factories:f}),/fixed release|trusted scope|host roots/i);
  assert.equal(f.calls.some(call=>call[0]==="serve.start"),false);assert.deepEqual(f.calls.slice(-2).map(call=>call[0]),["disconnect","journal.close"]);
});





test("sidecar root cannot diverge from its fixed source scope",async()=>{
  const create=producer();const input=config();input.sidecar_root=process.cwd();const f=factory();
  await assert.rejects(()=>create(input,{factories:f}),/sidecar root.*scope|source scope/i);assert.equal(f.calls.length,0);
});
