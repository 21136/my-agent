import assert from "node:assert/strict";
import path from "node:path";
import {createRequire} from "node:module";
import {test} from "node:test";
const require=createRequire(import.meta.url);let api={};try{api=require("../../../artifacts/evolution-control-build/sidecar-bootstrap.js");}catch{}
const run=process.env.EVOLUTION_TEST_ROOT;assert.ok(run);
const hash=(digit)=>`sha256:${digit.repeat(64)}`;
function fixture(){return{protocol:"my-agent-evolution-sidecar/1.0",control_release:{release_id:hash("a"),root:path.join(run,"control-release"),node_executable:process.execPath,python_executable:process.env.EVOLUTION_TRUSTED_PYTHON,manifest_path:path.join(run,"control-release","manifest.json"),manifest_digest:hash("a")},pipe_name:"\\\\.\\pipe\\my-agent-evolution-12345678-1234-4234-8234-123456789012",host_instance_id:"44444444-4444-4444-8444-444444444444",host_token:"c".repeat(64),scope_id:"55555555-5555-4555-8555-555555555555",owned_paths:["main.cjs","src/index.ts"]};}
function encoder(){assert.equal(typeof api.encodeEvolutionSidecarBootstrap,"function","sidecar-bootstrap must export encodeEvolutionSidecarBootstrap");return api.encodeEvolutionSidecarBootstrap;}
test("private sidecar bootstrap is a bounded, exact one-line stdin frame",()=>{const encode=encoder();const input=fixture();const bytes=encode(input);assert.ok(Buffer.isBuffer(bytes));assert.equal(bytes.at(-1),10);assert.equal(bytes.subarray(0,-1).includes(10),false);assert.deepEqual(JSON.parse(bytes.toString("utf8")),input);});
test("private bootstrap rejects renderer-like extras, invalid token and unsafe owned paths",()=>{const encode=encoder();const base=fixture();assert.throws(()=>encode({...base,renderer_token:"not allowed"}),/fields|bootstrap/i);assert.throws(()=>encode({...base,host_token:"short"}),/token|bootstrap/i);assert.throws(()=>encode({...base,owned_paths:["../escape.cjs"]}),/path|owned/i);});

test("bootstrap registry releases the private frame only to its registered stable root",()=>{
  const {SidecarBootstrapRegistry}=require("../../../artifacts/evolution-control-build/sidecar-bootstrap.js");
  assert.equal(typeof SidecarBootstrapRegistry,"function");
  const registry=new SidecarBootstrapRegistry();const root=path.join(run,"host-root");const candidate=path.join(run,"candidate-root");const input=fixture();
  registry.register(root,input);
  assert.deepEqual(registry.forRoot(root),require("../../../artifacts/evolution-control-build/sidecar-bootstrap.js").encodeEvolutionSidecarBootstrap(input));
  assert.equal(registry.forRoot(candidate),null);
  registry.clear(root);assert.equal(registry.forRoot(root),null);
});

test("sidecar launch descriptor keeps bootstrap bytes on stdin and never in argv",()=>{
  const apiModule=require("../../../artifacts/evolution-control-build/sidecar-bootstrap.js");
  assert.equal(typeof apiModule.sidecarBootstrapLaunch,"function");
  const frame=apiModule.encodeEvolutionSidecarBootstrap(fixture());
  const launch=apiModule.sidecarBootstrapLaunch(frame);
  assert.deepEqual(launch.args,["--evolution-bootstrap-stdin"]);
  assert.equal(launch.stdin,"pipe");assert.deepEqual(launch.privateInput,frame);
  assert.equal(launch.args.includes("c".repeat(64)),false);
  assert.deepEqual(apiModule.sidecarBootstrapLaunch(null),{args:[],stdin:"ignore",privateInput:null});
});
