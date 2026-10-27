import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {EventEmitter} from "node:events";
import {mkdtempSync,readFileSync} from "node:fs";
import {PassThrough} from "node:stream";
import {test} from "node:test";
import {createRequire} from "node:module";
import path from "node:path";
const require=createRequire(import.meta.url);let api={};try{api=require("../../../artifacts/evolution-control-build/workspace-client.js");}catch{/* RED until the trusted worker client is implemented. */}
const releaseApi=require("../../../artifacts/evolution-control-build/control-release.js");
const run=process.env.EVOLUTION_TEST_ROOT;assert.ok(run);assert.equal(readFileSync(path.join(run,".evolution-test-run"),"utf8").replace(/^\uFEFF/,"").trim(),"evolution-a");
const sha=(bytes)=>createHash("sha256").update(bytes).digest("hex");
const scope={scope_id:"22222222-2222-4222-8222-222222222222",source_root:path.join(process.cwd(),"candidate-fixture"),owned_paths:["main.cjs"],state_root:path.join(run,"workspace-state"),read_only_roots:[]};
function getClientClass(){assert.equal(typeof api.PythonWorkspaceClient,"function","workspace-client must export PythonWorkspaceClient");return api.PythonWorkspaceClient;}
async function freezeFixture(){const store=mkdtempSync(path.join(run,"workspace-client-release-"));return await releaseApi.freezeControlRelease({buildRoot:path.resolve("artifacts/evolution-control-build"),workerRoot:path.resolve("agent-core/evolution"),outputRoot:path.join(store,"releases"),nodeExecutable:process.execPath,pythonExecutable:process.env.EVOLUTION_TRUSTED_PYTHON});}
function fakeSpawn({stdout,exitCode=0,stderr=Buffer.alloc(0)},calls){return(command,args,options)=>{const child=new EventEmitter();child.stdin=new PassThrough();child.stdout=new PassThrough();child.stderr=new PassThrough();child.kill=()=>{child.emit("close",null,"SIGTERM");return true;};const input=[];child.stdin.on("data",chunk=>input.push(Buffer.from(chunk)));child.stdin.once("finish",()=>{setImmediate(()=>{calls.push({command,args,options,input:Buffer.concat(input)});if(stdout)child.stdout.end(stdout);else child.stdout.end();if(stderr?.length)child.stderr.end(stderr);else child.stderr.end();child.emit("close",exitCode,null);});});return child;};}

test("fixed Python worker receives only its frozen module, scope config digest and bounded envelope",async()=>{
  const Client=getClientClass();const release=await freezeFixture();const calls=[];const result={ok:true,query:"summary",workspace_revision:0};const client=await Client.create({release,sandboxRoot:process.cwd(),runRoot:run,spawnProcess:fakeSpawn({stdout:Buffer.from(JSON.stringify(result))},calls)});
  const response=await client.inspect(scope,{query:"summary"},"11111111-1111-4111-8111-111111111111");assert.deepEqual(response,result);assert.equal(calls.length,1);
  const call=calls[0];assert.equal(call.command,release.python_executable);assert.deepEqual(call.args,["-m","evolution.workspace_worker"]);assert.equal(call.options.shell,false);assert.equal(call.options.windowsHide,true);assert.equal(call.options.env.EVOLUTION_SANDBOX_ROOT,process.cwd());
  const configPath=call.options.env.EVOLUTION_WORKSPACE_CONFIG;assert.ok(configPath.startsWith(run));assert.equal(call.options.env.EVOLUTION_WORKSPACE_CONFIG_SHA256,sha(readFileSync(configPath)));
  const envelope=JSON.parse(call.input.toString("utf8"));assert.equal(envelope.operation_id,"11111111-1111-4111-8111-111111111111");assert.equal(envelope.action,"inspect");assert.deepEqual(envelope.payload,{query:"summary"});
});

test("worker crash becomes outcome_unknown and is never retried",async()=>{
  const Client=getClientClass();const release=await freezeFixture();const calls=[];const client=await Client.create({release,sandboxRoot:process.cwd(),runRoot:run,spawnProcess:fakeSpawn({stdout:Buffer.from("partial"),exitCode:3},calls)});
  const response=await client.patch(scope,{path:"main.cjs",expected_before_hash:"missing",content_utf8:"candidate"},"33333333-3333-4333-8333-333333333333");
  assert.equal(response.ok,false);assert.equal(response.error.code,"outcome_unknown");assert.equal(calls.length,1);
});
