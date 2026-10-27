import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { EventEmitter } from "node:events";
import { PassThrough, Writable } from "node:stream";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { test } from "node:test";
import { createRequire } from "node:module";
import path from "node:path";

const require=createRequire(import.meta.url);
const {chooseRunner,createT4RecoveryCandidateDescriptor,validateProcessGraph,validateReadyMessage}=require("../../../artifacts/evolution-control-build/runner.js");
const {LocalManagedRunner}=require("../../../artifacts/evolution-control-build/runners/local.js");
const {canonicalJson,contentHash}=require("../../../artifacts/evolution-control-build/contracts.js");
const uuid=(d)=>`${d.repeat(8)}-${d.repeat(4)}-4${d.repeat(3)}-8${d.repeat(3)}-${d.repeat(12)}`;
const hash=`sha256:${"a".repeat(64)}`;
const descriptor={descriptor_version:"1.0",source_snapshot_id:hash,build_recipe:{artifact_id:uuid("1"),sha256:hash,byte_length:1,media_type:"application/json",origin:"broker_observed"},launch_recipe:{processes:[
 {component_id:uuid("2"),role:"service",runtime_id:uuid("3"),entrypoint:"service.cjs",argv:[],cwd:".",environment_profile_id:uuid("4"),depends_on:[],required:true,readiness:"protocol"},
 {component_id:uuid("5"),role:"agent",runtime_id:uuid("3"),entrypoint:"main.cjs",argv:[],cwd:".",environment_profile_id:uuid("4"),depends_on:[uuid("2")],required:true,readiness:"protocol"}
]},runtimes:[{runtime_id:uuid("3"),kind:"node",version:"24.15.0",platform:"win32",arch:"x64",use:"both",distribution:{artifact_id:uuid("6"),sha256:hash,byte_length:10,media_type:"application/octet-stream",origin:"broker_observed"}}],dependency_lock:[],adapter:{artifact_id:uuid("7"),sha256:hash,byte_length:1,media_type:"application/json",origin:"broker_observed"},data_contract_digest:hash,mode:"sealed_build",isolation_mode:"local_managed",required_capabilities:[]};

test("protected mode fails closed and local execution requires authorization",()=>{
  assert.throws(()=>chooseRunner("protected_guest",true),error=>error.code==="isolation_unavailable");
  assert.throws(()=>chooseRunner("local_managed",false),error=>error.code==="permission_denied");
});
test("process graph is dependency-ordered and rejects missing, duplicate or cyclic dependencies",()=>{
  assert.deepEqual(validateProcessGraph(descriptor).map(item=>item.component_id),[uuid("2"),uuid("5")]);
  for(const mutate of [d=>d.launch_recipe.processes[0].runtime_id=uuid("8"),d=>d.launch_recipe.processes[1].component_id=uuid("2"),d=>d.launch_recipe.processes[0].depends_on=[uuid("5")]]){
    const value=structuredClone(descriptor);mutate(value);assert.throws(()=>validateProcessGraph(value),error=>error.code==="invalid_request");
  }
});
test("entrypoint and working directory traversal are rejected",()=>{
  for(const field of ["entrypoint","cwd"]){const value=structuredClone(descriptor);value.launch_recipe.processes[0][field]="../outside.cjs";assert.throws(()=>validateProcessGraph(value),error=>error.code==="permission_denied");}
});
test("candidate environment uses a fixed allowlist and excludes inherited secrets",()=>{
  const {candidateEnvironment}=require("../../../artifacts/evolution-control-build/environment.js");
  const before=process.env.EVOLUTION_TEST_SECRET;process.env.EVOLUTION_TEST_SECRET="synthetic-secret-canary";
  try{const env=candidateEnvironment({profileRoot:"F:/fixture/profile",runtimePath:"F:/fixture/runtime/node.exe"});
    assert.equal(env.EVOLUTION_TEST_SECRET,undefined);assert.equal(env.NODE_OPTIONS,undefined);assert.equal(env.PYTHONPATH,undefined);
    assert.equal(env.PATH,path.normalize(path.dirname("F:/fixture/runtime/node.exe")));assert.equal(env.USERPROFILE,path.normalize("F:/fixture/profile"));
    assert.ok(Object.keys(env).every(key=>["PATH","TEMP","TMP","USERPROFILE","HOME","APPDATA","LOCALAPPDATA","SystemRoot","WINDIR","LANG","LC_ALL","MY_AGENT_CANDIDATE_PROFILE"].includes(key)));
  }finally{if(before===undefined)delete process.env.EVOLUTION_TEST_SECRET;else process.env.EVOLUTION_TEST_SECRET=before;}
});
test("ready requires the actual candidate, instance, fresh nonce and declared component",()=>{
  const identity={candidate_id:hash,instance_id:uuid("9"),boot_nonce:"a".repeat(64)};
  const message={type:"ready",candidate_id:hash,instance_id:identity.instance_id,boot_nonce:identity.boot_nonce,component_id:uuid("2")};
  assert.equal(validateReadyMessage(message,identity,[uuid("2")]).ok,true);
  assert.equal(validateReadyMessage({...message,boot_nonce:"b".repeat(64)},identity,[uuid("2")]).ok,false);
  assert.equal(validateReadyMessage({...message,candidate_id:`sha256:${"b".repeat(64)}`},identity,[uuid("2")]).ok,false);
  assert.equal(validateReadyMessage({...message,component_id:uuid("5")},identity,[uuid("2")]).ok,false);
});
test("runner construction is inert and scopes stop to declared direct processes",()=>{
  const runner=chooseRunner("local_managed",true);
  assert.equal(runner.lifecycle,"declared_components_only");assert.equal(typeof runner.prepare,"function");assert.equal(typeof runner.launch,"function");assert.equal(typeof runner.stop,"function");
});
test("prepare seals only declared files and exact runtime bytes without launching candidate code",async()=>{
  const run=process.env.EVOLUTION_TEST_ROOT;assert.ok(run);assert.equal(readFileSync(path.join(run,".evolution-test-run"),"utf8").replace(/^\uFEFF/,"").trim(),"evolution-a");
  const root=mkdtempSync(path.join(run,"t4-build-"));writeFileSync(path.join(root,".evolution-test-run"),"evolution-a\n");const snapshot=path.join(root,"snapshot");const tree=path.join(snapshot,"tree");mkdirSync(tree,{recursive:true});
  const files={"main.cjs":"require('node:fs').writeFileSync('candidate-executed.marker','bad');\n","service.cjs":"// fixture service\n","manifest.json":JSON.stringify({broken:true})};
  const manifest={format:"evolution-source-snapshot/1",scope_id:uuid("4"),workspace_revision:1,exclusion_policy:"evolution-sandbox-v1",case_policy:"windows-case-insensitive-v1",files:Object.entries(files).map(([file,text])=>({path:file,byte_length:Buffer.byteLength(text),sha256:"sha256:"+createHash("sha256").update(text).digest("hex")}))};
  const sourceSnapshotId=contentHash(manifest);for(const [file,text] of Object.entries(files))writeFileSync(path.join(tree,file),text);writeFileSync(path.join(snapshot,"manifest.json"),canonicalJson(manifest));
  const runtime=path.join(root,"runtime.fixture");writeFileSync(runtime,"synthetic runtime bytes");
  const candidate=structuredClone(descriptor);candidate.source_snapshot_id=sourceSnapshotId;candidate.runtimes[0].distribution.sha256="sha256:"+createHash("sha256").update(readFileSync(runtime)).digest("hex");candidate.runtimes[0].distribution.byte_length=readFileSync(runtime).length;
  const receipt=await new LocalManagedRunner().prepare({descriptor:candidate,source_root:tree,run_root:root,runtime_paths:{[uuid("3")]:runtime},authorization:{authorization_id:uuid("8"),kind:"candidate.build",isolation_mode:"local_managed"}});
  assert.equal(receipt.source_snapshot_id,sourceSnapshotId);assert.match(receipt.candidate_id,/^sha256:[0-9a-f]{64}$/);assert.deepEqual(Object.keys(receipt.artifact_hashes).sort(),["main.cjs","manifest.json","runtime/"+uuid("3"),"service.cjs"]);
  assert.equal(readFileSync(path.join(receipt.output_root,"workspace","main.cjs"),"utf8"),files["main.cjs"]);assert.equal(readFileSync(receipt.runtime_paths[uuid("3")],"utf8"),"synthetic runtime bytes");
  assert.equal(existsSync(path.join(receipt.output_root,"workspace","node_modules")),false);
  assert.equal(existsSync(path.join(receipt.output_root,"workspace","candidate-executed.marker")),false,"node --check must parse but never execute candidate source");assert.equal(JSON.parse(readFileSync(path.join(receipt.output_root,"build-receipt.json"),"utf8")).candidate_id,receipt.candidate_id);
});


function fakeProcessHarness(mode="ready") {
  const starts=[];const stops=[];const bootNonces=[];let nextPid=8100;
  class FakeChild extends EventEmitter {
    pid=nextPid++;stdout=new PassThrough();stderr=new PassThrough();killed=false;
    stdin=new Writable({write:(chunk,_encoding,callback)=>{
      let message;try{message=JSON.parse(chunk.toString())}catch{callback(new Error("bad boot frame"));return;}
      if(message.type==="boot"){
        starts.push(message.component_id);bootNonces.push(message.boot_nonce);
        setImmediate(()=>{
          this.emit("spawn");
          if(mode==="failed"&&message.component_id===uuid("5")){this.stderr.write(`fixture_start_failed ${message.boot_nonce}\\n`);this.finish(17);return;}
          if(mode==="flood"&&message.component_id===uuid("2")){this.stdout.write(Array.from({length:101},()=>"noise\n").join(""));return;}
          if(mode==="oversized"&&message.component_id===uuid("2")){this.stderr.write(Buffer.alloc(10*1024*1024+1,0x61));return;}
          const bootNonce=mode==="stale"&&message.component_id===uuid("5")?"f".repeat(64):message.boot_nonce;
          this.stdout.write(`${JSON.stringify({type:"ready",candidate_id:message.candidate_id,instance_id:message.instance_id,boot_nonce:bootNonce,component_id:message.component_id})}\n`);
        });
      }else if(message.type==="stop"){
        stops.push(message.instance_id);setImmediate(()=>this.finish(0));
      }
      callback();
    }});
    finish(code){if(this.closed)return;this.closed=true;this.emit("close",code,null);this.stdout.end();this.stderr.end();}
    kill(){this.killed=true;setImmediate(()=>this.finish(137));return true;}
  }
  return {starts,stops,bootNonces,spawnProcess:(_exe,_args,options)=>{assert.equal(options.shell,false);assert.equal(options.windowsHide,true);assert.equal(options.env.EVOLUTION_TEST_SECRET,undefined);const child=new FakeChild();setImmediate(()=>child.emit("spawn"));return child;}};
}

function sealedBuildFixture(runRoot,runner){
  const root=mkdtempSync(path.join(runRoot,"t4-lifecycle-"));writeFileSync(path.join(root,".evolution-test-run"),"evolution-a\n");
  const snapshot=path.join(root,"snapshot");const tree=path.join(snapshot,"tree");mkdirSync(tree,{recursive:true});
  const files={"main.cjs":"// valid main\n","service.cjs":"// valid service\n","manifest.json":JSON.stringify({broken:false})};
  const entries=Object.entries(files).map(([file,text])=>({path:file,byte_length:Buffer.byteLength(text),sha256:"sha256:"+createHash("sha256").update(text).digest("hex")}));
  const manifest={format:"evolution-source-snapshot/1",scope_id:uuid("4"),workspace_revision:1,exclusion_policy:"evolution-sandbox-v1",case_policy:"windows-case-insensitive-v1",files:entries};
  const snapshotId=contentHash(manifest);for(const [file,text] of Object.entries(files))writeFileSync(path.join(tree,file),text);writeFileSync(path.join(snapshot,"manifest.json"),canonicalJson(manifest));
  const runtime=path.join(root,"runtime.fixture");writeFileSync(runtime,"synthetic Node runtime bytes");
  const descriptorValue=structuredClone(descriptor);descriptorValue.source_snapshot_id=snapshotId;descriptorValue.runtimes[0].distribution.sha256="sha256:"+createHash("sha256").update(readFileSync(runtime)).digest("hex");descriptorValue.runtimes[0].distribution.byte_length=readFileSync(runtime).length;
  return runner.prepare({descriptor:descriptorValue,source_root:tree,run_root:root,runtime_paths:{[uuid("3")]:runtime},authorization:{authorization_id:uuid("8"),kind:"candidate.build",isolation_mode:"local_managed"}}).then(build=>({root,build}));
}

test("T4 descriptor prepares its three owned nested source paths without requiring an unowned root manifest",async()=>{
  const run=process.env.EVOLUTION_TEST_ROOT;assert.ok(run);const root=mkdtempSync(path.join(run,"t9-nested-build-"));writeFileSync(path.join(root,".evolution-test-run"),"evolution-a\n");
  const snapshot=path.join(root,"snapshot");const tree=path.join(snapshot,"tree");const directory=path.join(tree,"evolve","tool-catalog","recovery");mkdirSync(directory,{recursive:true});
  const files={"main.cjs":"process.stdout.write(\"main\");","service.cjs":"process.stdout.write(\"service\");","manifest.json":JSON.stringify({broken:true})};
  const prefix="evolve/tool-catalog/recovery/";const entries=Object.entries(files).map(([name,content])=>({path:prefix+name,byte_length:Buffer.byteLength(content),sha256:"sha256:"+createHash("sha256").update(content).digest("hex")}));
  const manifest={format:"evolution-source-snapshot/1",scope_id:"66666666-6666-4666-8666-666666666666",workspace_revision:1,exclusion_policy:"evolution-sandbox-v1",case_policy:"windows-case-insensitive-v1",files:entries};const snapshotId=contentHash(manifest);
  for(const [name,content] of Object.entries(files))writeFileSync(path.join(directory,name),content);writeFileSync(path.join(snapshot,"manifest.json"),canonicalJson(manifest));
  const runtime=path.join(root,"node.fixture");writeFileSync(runtime,"synthetic node bytes");const runtimeId="77777777-7777-4777-8777-777777777777";
  const value=createT4RecoveryCandidateDescriptor(snapshotId,runtimeId,process.execPath);value.runtimes[0].distribution.sha256="sha256:"+createHash("sha256").update(readFileSync(runtime)).digest("hex");value.runtimes[0].distribution.byte_length=readFileSync(runtime).length;
  const build=await new LocalManagedRunner().prepare({descriptor:value,source_root:tree,run_root:root,runtime_paths:{[runtimeId]:runtime},authorization:{authorization_id:uuid("8"),kind:"candidate.build",isolation_mode:"local_managed"}});
  assert.equal(build.source_snapshot_id,snapshotId);for(const name of Object.keys(files))assert.equal(readFileSync(path.join(build.output_root,"workspace",prefix,name),"utf8"),files[name]);
  assert.equal(existsSync(path.join(build.output_root,"workspace","manifest.json")),false);
});
test("injected process driver proves dependency-order start and reverse-order owned stop without launching a real candidate",async()=>{
  const run=process.env.EVOLUTION_TEST_ROOT;assert.ok(run);const harness=fakeProcessHarness("ready");const runner=new LocalManagedRunner({spawnProcess:harness.spawnProcess,startTimeoutMs:250,stopTimeoutMs:50});
  const {root,build}=await sealedBuildFixture(run,runner);const profile=path.join(root,"profile");
  const result=await runner.launch(build,{profile_root:profile,authorization:{authorization_id:uuid("9"),kind:"candidate.launch",isolation_mode:"local_managed",candidate_id:build.candidate_id}});
  assert.equal(result.status,"ready");assert.deepEqual(harness.starts,[uuid("2"),uuid("5")]);assert.equal(result.components.every(component=>component.ready),true);assert.equal(result.descendants_verified,false);
  const logRoot=path.join(root,"candidate-logs",result.instance_id);for(const id of [uuid("2"),uuid("5")]){const stdout=readFileSync(path.join(logRoot,id+".stdout.log"),"utf8");assert.match(stdout,/"type":"ready"/);assert.equal(readFileSync(path.join(logRoot,id+".stderr.log"),"utf8"),"");assert.equal(harness.bootNonces.some(nonce=>stdout.includes(nonce)),false,"private boot nonces must not be persisted in candidate logs");}
  const stopped=await runner.stop(result.instance_id);assert.equal(stopped.status,"stopped");assert.ok(stopped.components.every(component=>component.exit_observed===true));assert.deepEqual(harness.stops,[result.instance_id,result.instance_id]);
});

test("stale nonce and required component startup failure never report ready",async()=>{
  const run=process.env.EVOLUTION_TEST_ROOT;assert.ok(run);
  for(const [mode,expected] of [["stale","failed"],["failed","failed"]]){
    const harness=fakeProcessHarness(mode);const runner=new LocalManagedRunner({spawnProcess:harness.spawnProcess,startTimeoutMs:40,stopTimeoutMs:30});
    const {root,build}=await sealedBuildFixture(run,runner);const profile=path.join(root,"profile");
    const result=await runner.launch(build,{profile_root:profile,authorization:{authorization_id:uuid("9"),kind:"candidate.launch",isolation_mode:"local_managed",candidate_id:build.candidate_id}});
    assert.equal(result.status,expected);assert.notEqual(result.status,"ready");assert.ok(result.components.some(component=>component.exit_observed||!component.ready));if(mode==="failed"){const failed=result.components.find(component=>component.component_id===uuid("5"));assert.equal(failed.exit_code,17);assert.match(failed.stderr_excerpt,/fixture_start_failed/);assert.equal(harness.bootNonces.some(nonce=>failed.stderr_excerpt.includes(nonce)),false,"private boot nonces must not enter tool receipts");}
  }
});


test("candidate log rate and byte budgets fail closed under synthetic flood",async()=>{
  const run=process.env.EVOLUTION_TEST_ROOT;assert.ok(run);
  for(const mode of ["flood","oversized"]){
    const harness=fakeProcessHarness(mode);const runner=new LocalManagedRunner({spawnProcess:harness.spawnProcess,startTimeoutMs:100,stopTimeoutMs:30});
    const {root,build}=await sealedBuildFixture(run,runner);const profile=path.join(root,"profile");
    const result=await runner.launch(build,{profile_root:profile,authorization:{authorization_id:uuid("9"),kind:"candidate.launch",isolation_mode:"local_managed",candidate_id:build.candidate_id}});
    assert.equal(result.status,"failed",`${mode} must not become ready`);assert.equal(result.components.every(component=>component.ready),false);
  }
});
