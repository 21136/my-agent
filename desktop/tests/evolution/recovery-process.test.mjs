import assert from "node:assert/strict";
import {createHash,randomUUID} from "node:crypto";
import {copyFileSync,existsSync,mkdtempSync,mkdirSync,readdirSync,readFileSync,rmSync,writeFileSync} from "node:fs";
import {EventEmitter} from "node:events";
import {PassThrough,Writable} from "node:stream";
import path from "node:path";
import {createRequire} from "node:module";
import {test} from "node:test";
import {buildH1ElectronMainOutputs,captureStableBaseline,createCandidateSourceFixture,createDrillSummary,createT9ScriptedWebSocketDriver,freezeHostFixture,launchFrozenHost,startFrozenHost,main,recordDrillEvidence,recoveryFixtureScopeCompatibility,runScriptedRecovery,verifyStableBaselineUnchanged,validateDrillAuthorization,validateDrillSummary,verifyFrozenHostFixture} from "../../scripts/verify-evolution-a.mjs";

const run=path.resolve(process.env.EVOLUTION_TEST_ROOT||"");
assert.ok(run);
assert.equal(readFileSync(path.join(run,".evolution-test-run"),"utf8").trim(),"evolution-a");
const sha=value=>createHash("sha256").update(value).digest("hex");
function makeSandbox(){
  const root=mkdtempSync(path.join(run,"t9-freeze-fixture-"));
  const runRoot=path.join(root,"artifacts","run");
  for(const dir of [path.join(root,"desktop","electron"),path.join(root,"desktop","src"),path.join(root,"desktop","tests","evolution","fixtures","candidate-node"),path.join(root,"desktop","dist-electron"),path.join(root,"desktop","dist","assets"),path.join(root,"agent-core","evolution"),path.join(root,"agent-core","tools"),path.join(root,"agent-core","tests"),path.join(root,"agent-core","data"),path.join(root,"evolve","tool-catalog"),path.join(root,"evolve","scripts"),path.join(root,"runtime","python"),path.join(root,"runtime","electron","resources"),runRoot])mkdirSync(dir,{recursive:true});
  writeFileSync(path.join(runRoot,".evolution-test-run"),"evolution-a\n");
  writeFileSync(path.join(root,"desktop","electron","main.ts"),"source main\n");
  writeFileSync(path.join(root,"desktop","electron","preload.ts"),"source preload\n");
  writeFileSync(path.join(root,"desktop","vite.config.ts"),"source config\n");
  writeFileSync(path.join(root,"desktop","index.html"),"<html>source</html>\n");
  writeFileSync(path.join(root,"desktop","dist-electron","main.js"),"export const main=1;\n");
  writeFileSync(path.join(root,"desktop","dist-electron","preload.js"),"export const preload=1;\n");
  writeFileSync(path.join(root,"desktop","dist-electron","vite.config.js"),"must-not-copy\n");
  writeFileSync(path.join(root,"desktop","dist","index.html"),"<html>sealed</html>\n");
  writeFileSync(path.join(root,"desktop","dist","assets","app.js"),"globalThis.ready=true;\n");
  writeFileSync(path.join(root,"desktop","package.json"),JSON.stringify({name:"fixture-desktop",version:"1.2.3",main:"dist-electron/main.js",scripts:{dev:"must-not-copy"}}));
  writeFileSync(path.join(root,"agent-core","server.py"),"print('fixture')\n");
  writeFileSync(path.join(root,"agent-core","evolution","client.py"),"class Client: pass\n");
  writeFileSync(path.join(root,"agent-core","tools","tool.py"),"def run(): pass\n");
  writeFileSync(path.join(root,"agent-core","tests","test_secret.py"),"must-not-copy\n");
  writeFileSync(path.join(root,"agent-core","data","state.json"),"real-looking-state\n");
  writeFileSync(path.join(root,"agent-core",".env"),"API_KEY=must-not-copy\n");
  writeFileSync(path.join(root,"evolve","tool-catalog","INDEX.md"),"# fixture catalog\n");
  writeFileSync(path.join(root,"evolve","scripts","candidate.py"),"must-not-copy\n");
  const approvedFixture=path.resolve(process.cwd(),"desktop/tests/evolution/fixtures/candidate-node");for(const name of ["main.cjs","service.cjs","manifest.json"])copyFileSync(path.join(approvedFixture,name),path.join(root,"desktop","tests","evolution","fixtures","candidate-node",name));
  const python=path.join(root,"runtime","python","python.exe");writeFileSync(python,"fake-python-runtime");
  const electron=path.join(root,"runtime","electron","electron.exe");writeFileSync(electron,"fake-electron-runtime");
  writeFileSync(path.join(root,"runtime","electron","resources","resources.pak"),"fake-resource");
  writeFileSync(path.join(root,"requirements.txt"),"httpx>=0.27\nwebsockets>=12.0\nrich>=13.7\nprompt_toolkit>=3.0.43\npython-docx>=1.1.0\npypdf>=4.0\npymupdf>=1.24\n");
  return{root,runRoot,python,electron};
}
function factories(){return{
  freezeControlRelease:async input=>{
    const releaseRoot=path.join(input.outputRoot,"release-fixture");mkdirSync(releaseRoot,{recursive:true});
    const manifest={format:1,files:[],runtimes:{node:{path:process.execPath,sha256:sha("node")},python:{path:input.pythonExecutable,sha256:sha("python")}}};
    const manifestBytes=Buffer.from(`${JSON.stringify(manifest)}\n`);writeFileSync(path.join(releaseRoot,"manifest.json"),manifestBytes);
    const digest=`sha256:${sha(manifestBytes)}`;return{release_id:digest,root:releaseRoot,node_executable:process.execPath,python_executable:input.pythonExecutable,manifest_path:path.join(releaseRoot,"manifest.json"),manifest_digest:digest};
  },
  capturePythonEnvironment:async executable=>({executable,path:[executable],packages:[{name:"websockets",version:"test",files_digest:`sha256:${sha("pkg")}`}],digest:`sha256:${sha("python-environment")}`}),
};}

function unitFakeDriver(evidence){
  const uuid=d=>`${d.repeat(8)}-${d.repeat(4)}-4${d.repeat(3)}-8${d.repeat(3)}-${d.repeat(12)}`;let instance=null;let status="not_run";
  return{evidenceMode:"unit_fake",captureBaseline:async({runRoot})=>{if(!existsSync(evidence))writeFileSync(evidence,"unit fake only\\n",{flag:"wx"});return{stable_files:[],baseline_listeners:[]}},bindSession:async()=>({conversation_id:uuid("3"),evolution_id:uuid("2")}),inspect:async binding=>({conversation_id:binding.conversation_id,current_instance_id:instance,candidate_status:status,evidence_locator:evidence}),patch:async({broken})=>({broken}),snapshot:async({patch})=>({source_snapshot_id:`sha256:${sha(patch.broken?"broken":"fixed")}`,broken:patch.broken}),build:async({snapshot})=>({candidate_id:`sha256:${sha(snapshot.source_snapshot_id)}`,source_snapshot_id:snapshot.source_snapshot_id,broken:snapshot.broken}),launch:async({build})=>{instance=build.broken?uuid("4"):uuid("5");status=build.broken?"failed":"ready";return{status,exit_code:build.broken?17:0,stderr_excerpt:build.broken?"fixture_start_failed":"",instance_id:instance,candidate_id:build.candidate_id,source_snapshot_id:build.source_snapshot_id,evidence_locator:evidence,components:build.broken?[]:[{ready:true},{ready:true}]}},deliverLateReady:async({binding,repaired_instance_id,repaired_candidate_id})=>({accepted:false,projection:{conversation_id:binding.conversation_id,current_instance_id:repaired_instance_id,current_candidate_id:repaired_candidate_id}}),captureConversationEvidence:async()=>evidence,stop:async()=>({status:"stopped",descendants_verified:false,components:[{exit_observed:true},{exit_observed:true}]}),captureFinal:async()=>({evidence_locators:[evidence],summary_evidence:evidence,control_release_unchanged:false})};
}
test("scripted summary defaults never claim an observed recovery",()=>{
  const summary=createDrillSummary("scripted");
  assert.equal(summary.mode,"scripted_recovery");
  assert.equal(summary.scripted_recovery_verified,false);
  assert.equal(summary.agent_recovery_verified,false);
  assert.equal(summary.installed_stable_upgraded,false);
  assert.deepEqual(summary.evidence,[]);
});

test("authorization requires trusted local/Host confirmation and has no agent fallback",()=>{
  assert.throws(()=>validateDrillAuthorization({mode:"agent",localConfirmed:true,hostConfirmed:true,modelConfirmed:false}),/permission_denied/);
  assert.throws(()=>validateDrillAuthorization({mode:"scripted",localConfirmed:true,hostConfirmed:false,modelConfirmed:false}),/permission_denied/);
  assert.doesNotThrow(()=>validateDrillAuthorization({mode:"scripted",localConfirmed:true,hostConfirmed:true,modelConfirmed:false}));
});

test("H1 freeze copies only explicit runtime/source files and writes the agreed profile without launching anything",async t=>{
  const fixture=makeSandbox();
  const result=await freezeHostFixture({sandboxRoot:fixture.root,runRoot:fixture.runRoot,trustedPythonExecutable:fixture.python,electronExecutable:fixture.electron},{factories:factories()});
  t.after(()=>{
    const relative=path.relative(run,fixture.root);assert.ok(relative&&!relative.startsWith("..")&&!path.isAbsolute(relative));
    rmSync(fixture.root,{recursive:true,force:false});
  });
  const app=path.join(result.app_root);
  assert.equal(result.profile_root,path.join(fixture.runRoot,"host","profile"));
  assert.equal(result.renderer_root,path.join(app,"desktop","dist"));
  assert.ok(existsSync(path.join(result.manifest_path)));
  const profile=JSON.parse(readFileSync(path.join(fixture.runRoot,"host","evolution-test-profile.json"),"utf8"));
  assert.equal(profile.profile_root,result.profile_root);assert.equal(profile.control_release_root,path.join(fixture.runRoot,"control-releases","release-fixture"));
  assert.equal(profile.ws_port,0);assert.equal(profile.renderer_port,0);assert.match(profile.fixture_scope_id,/^[0-9a-f-]{36}$/);
  assert.ok(existsSync(path.join(app,"desktop","dist-electron","main.js")));
  assert.ok(existsSync(path.join(app,"desktop","dist-electron","preload.js")));
  assert.ok(existsSync(path.join(app,"desktop","dist","assets","app.js")));
  assert.ok(existsSync(path.join(app,"agent-core","evolution","client.py")));
  assert.ok(existsSync(path.join(app,"evolve","tool-catalog","INDEX.md")));
  for(const excluded of ["desktop/dist-electron/vite.config.js","agent-core/tests/test_secret.py","agent-core/data/state.json","agent-core/.env","evolve/scripts/candidate.py"]){assert.equal(existsSync(path.join(app,...excluded.split("/"))),false,excluded);}
  assert.equal(existsSync(path.join(app,"data","sessions")),true);
  assert.deepEqual(readdirSync(path.join(app,"data","sessions")),[]);
  const manifest=JSON.parse(readFileSync(result.manifest_path,"utf8"));
  assert.ok(manifest.files.some(item=>item.path==="desktop/dist/index.html"));
  assert.ok(manifest.runtimes.electron.distribution_digest.startsWith("sha256:"));
  assert.equal(manifest.runtimes.python.environment.digest,`sha256:${sha("python-environment")}`);
});



test("T4 candidate bytes are separately registered as mutable H1 source outside the control-file digest",async t=>{
  const fixture=makeSandbox();
  t.after(()=>{const relative=path.relative(run,fixture.root);assert.ok(relative&&!relative.startsWith("..")&&!path.isAbsolute(relative));rmSync(fixture.root,{recursive:true,force:false});});
  const frozen=await freezeHostFixture({sandboxRoot:fixture.root,runRoot:fixture.runRoot,trustedPythonExecutable:fixture.python,electronExecutable:fixture.electron,candidateFixtureProfile:"t4-recovery-node-v1"},{factories:factories()});
  const candidate=createCandidateSourceFixture({sandboxRoot:fixture.root,runRoot:fixture.runRoot});
  assert.equal(candidate.broken,true);assert.equal(candidate.fixture_profile,"t4-recovery-node-v1");
  assert.equal(candidate.source_root,path.join(fixture.runRoot,"candidate-source"));
  const owned=["evolve/tool-catalog/recovery/main.cjs","evolve/tool-catalog/recovery/service.cjs","evolve/tool-catalog/recovery/manifest.json"];
  assert.deepEqual(candidate.files.map(item=>item.path).sort(),["main.cjs","manifest.json","service.cjs"]);
  for(const relative of owned)assert.equal(readFileSync(path.join(frozen.app_root,...relative.split("/")),"utf8"),readFileSync(path.join(candidate.source_root,path.basename(relative)),"utf8"));
  const manifest=JSON.parse(readFileSync(frozen.manifest_path,"utf8"));
  assert.deepEqual(manifest.mutable_candidate_sources.map(item=>item.path),owned);
  assert.equal(manifest.files.some(item=>item.path.startsWith("evolve/tool-catalog/recovery/")),false);
  assert.equal(verifyFrozenHostFixture({sandboxRoot:fixture.root,runRoot:fixture.runRoot,...frozen},{capturePythonEnvironment:()=>({executable:fixture.python,path:[fixture.python],packages:[{name:"websockets",version:"test",files_digest:`sha256:${sha("pkg")}`}],digest:`sha256:${sha("python-environment")}`})}).profile.candidate_fixture_profile,"t4-recovery-node-v1");
});
test("T9 Electron main/preload bundle is compiled from current sandbox source into a run-local sealed input",async t=>{
  const sandboxRoot=path.resolve(process.cwd());const runRoot=path.join(run,"t9-main-build-"+randomUUID());mkdirSync(runRoot);writeFileSync(path.join(runRoot,".evolution-test-run"),"evolution-a\n");
  t.after(()=>{const relative=path.relative(run,runRoot);assert.ok(relative&&!relative.startsWith("..")&&!path.isAbsolute(relative));rmSync(runRoot,{recursive:true,force:false});});
  const result=await buildH1ElectronMainOutputs({sandboxRoot,runRoot});
  assert.equal(path.dirname(result.main_path),result.output_root);assert.equal(path.dirname(result.preload_path),result.output_root);
  const main=readFileSync(result.main_path,"utf8");const preload=readFileSync(result.preload_path,"utf8");
  assert.match(main,/evolution-test-host-ready/);assert.match(main,/t9\.control\.shutdown/);assert.match(preload,/myAgentDesktop/);
  assert.deepEqual(result.files.map(item=>item.path),["main.js","preload.js"]);
});
test("H1 verification rehashes the sealed app, profile, runtimes, Python environment, and approved inputs",async t=>{
  const fixture=makeSandbox();
  const attestor=executable=>({executable,path:[executable],packages:[{name:"websockets",version:"test",files_digest:`sha256:${sha("pkg")}`}],digest:`sha256:${sha("python-environment")}`});
  const frozen=await freezeHostFixture({sandboxRoot:fixture.root,runRoot:fixture.runRoot,trustedPythonExecutable:fixture.python,electronExecutable:fixture.electron},{factories:{...factories(),capturePythonEnvironment:attestor}});
  t.after(()=>{const relative=path.relative(run,fixture.root);assert.ok(relative&&!relative.startsWith("..")&&!path.isAbsolute(relative));rmSync(fixture.root,{recursive:true,force:false});});
  const verified=verifyFrozenHostFixture({sandboxRoot:fixture.root,runRoot:fixture.runRoot,...frozen},{capturePythonEnvironment:attestor});
  assert.equal(verified.profile.ws_port,0);
  assert.ok(verified.manifest.source_inputs.some(item=>item.path==="desktop/electron/main.ts"));
  writeFileSync(path.join(frozen.app_root,"desktop","dist","index.html"),"tampered");
  assert.throws(()=>verifyFrozenHostFixture({sandboxRoot:fixture.root,runRoot:fixture.runRoot,...frozen},{capturePythonEnvironment:attestor}),/digest|changed|manifest/i);
});

test("H1 launcher uses only the frozen Electron app, visible window, sanitized environment, and re-attests on exit",async t=>{
  const fixture=makeSandbox();
  const attestor=executable=>({executable,path:[executable],packages:[{name:"websockets",version:"test",files_digest:`sha256:${sha("pkg")}`}],digest:`sha256:${sha("python-environment")}`});
  const frozen=await freezeHostFixture({sandboxRoot:fixture.root,runRoot:fixture.runRoot,trustedPythonExecutable:fixture.python,electronExecutable:fixture.electron},{factories:{...factories(),capturePythonEnvironment:attestor}});
  t.after(()=>{const relative=path.relative(run,fixture.root);assert.ok(relative&&!relative.startsWith("..")&&!path.isAbsolute(relative));rmSync(fixture.root,{recursive:true,force:false});});
  let spawnCall;
  const result=await launchFrozenHost({sandboxRoot:fixture.root,runRoot:fixture.runRoot,...frozen},{capturePythonEnvironment:attestor,spawnProcess:(exe,args,options)=>{spawnCall={exe,args,options};const child=new EventEmitter();child.stdout=new PassThrough();child.stderr=new PassThrough();child.kill=()=>true;queueMicrotask(()=>{child.stdout.end("host-ready\\n");child.stderr.end("");child.emit("close",0,null);});return child;}});
  assert.equal(spawnCall.exe,fixture.electron);assert.deepEqual(spawnCall.args,[path.join(frozen.app_root,"desktop")]);assert.equal(spawnCall.options.windowsHide,false);assert.equal(spawnCall.options.shell,false);
  assert.equal(Object.hasOwn(spawnCall.options.env,"VITE_DEV_SERVER_URL"),false);assert.equal(spawnCall.options.env.PYTHONDONTWRITEBYTECODE,"1");
  assert.equal(result.code,0);assert.equal(result.signal,null);assert.ok(existsSync(path.join(fixture.runRoot,"host","logs","electron.exit.json")));
});

test("frozen H1 launcher waits for its ready port and closes only through the authenticated private stdin channel",async t=>{
  const fixture=makeSandbox();const attestor=executable=>({executable,path:[executable],packages:[{name:"websockets",version:"test",files_digest:`sha256:${sha("pkg")}`}],digest:`sha256:${sha("python-environment")}`});
  const frozen=await freezeHostFixture({sandboxRoot:fixture.root,runRoot:fixture.runRoot,trustedPythonExecutable:fixture.python,electronExecutable:fixture.electron,candidateFixtureProfile:"t4-recovery-node-v1"},{factories:{...factories(),capturePythonEnvironment:attestor}});
  createCandidateSourceFixture({sandboxRoot:fixture.root,runRoot:fixture.runRoot});
  t.after(()=>{const relative=path.relative(run,fixture.root);assert.ok(relative&&!relative.startsWith("..")&&!path.isAbsolute(relative));rmSync(fixture.root,{recursive:true,force:false});});
  let token="";let closed=false;let spawnOptions;
  const result=await startFrozenHost({sandboxRoot:fixture.root,runRoot:fixture.runRoot,...frozen},{capturePythonEnvironment:attestor,spawnProcess:(_exe,_args,options)=>{
    spawnOptions=options;const child=new EventEmitter();child.stdout=new PassThrough();child.stderr=new PassThrough();child.kill=()=>{closed=true;child.emit("close",null,"SIGTERM");return true;};
    child.stdin=new Writable({write:(chunk,_encoding,callback)=>{const frame=JSON.parse(chunk.toString());if(frame.type==="t9.control.bootstrap")token=frame.token;if(frame.type==="t9.control.shutdown"&&frame.token===token){closed=true;queueMicrotask(()=>child.emit("close",0,null));}callback();}});
    queueMicrotask(()=>child.stdout.write(JSON.stringify({type:"evolution-test-host-ready",host:"127.0.0.1",port:43219})+"\n"));return child;
  }});
  assert.equal(result.port,43219);assert.equal(typeof token,"string");assert.equal(token.length,64);assert.equal(spawnOptions.windowsHide,false);assert.equal(spawnOptions.shell,false);
  const exit=await result.close();assert.equal(exit.code,0);assert.equal(closed,true);assert.ok(existsSync(path.join(fixture.runRoot,"host","logs","electron.exit.json")));
});
test("T9 WebSocket adapter binds only the sidecar Session identity and closes its scripted lifecycle",async t=>{
  const fixture=makeSandbox();const prior=globalThis.WebSocket;const hostConversation="33333333-3333-4333-8333-333333333333";const hostEvolution="22222222-2222-4222-8222-222222222222";const hostRun="44444444-4444-4444-8444-444444444444";
  class FakeWebSocket extends EventTarget{
    static OPEN=1;static CONNECTING=0;readyState=0;
    constructor(url){super();this.url=url;queueMicrotask(()=>{this.readyState=1;this.dispatchEvent(new Event("open"));this.deliver({type:"session.banner",session_id:hostConversation});});}
    deliver(value){this.dispatchEvent(new MessageEvent("message",{data:JSON.stringify(value)}));}
    send(raw){const request=JSON.parse(raw);let result;if(request.operation==="lifecycle.begin")result={conversation_id:hostConversation,host_run_id:hostRun};else if(request.operation==="bind")result={conversation_id:hostConversation,evolution_id:hostEvolution,host_run_id:hostRun};else if(request.operation==="action"&&request.action==="inspect")result={status:"succeeded",result:{conversation_id:hostConversation,evolution_id:hostEvolution,candidate_status:"not_run",current_instance_id:null,current_candidate_id:null}};else if(request.operation==="lifecycle.finish")result={conversation_id:hostConversation,finished:true};else throw new Error("unexpected fake T9 operation: "+request.operation);queueMicrotask(()=>this.deliver({type:"evolution.scripted.result",request_id:request.request_id,conversation_id:hostConversation,status:"succeeded",result,error:null}));}
    close(){this.readyState=3;queueMicrotask(()=>this.dispatchEvent(new Event("close")));}
  }
  globalThis.WebSocket=FakeWebSocket;
  try{
    const evidenceRoot=path.join(fixture.runRoot,"scripted-evidence");mkdirSync(evidenceRoot);const driver=createT9ScriptedWebSocketDriver({port:4321,runRoot:fixture.runRoot,sandboxRoot:fixture.root,baseline:{stable_files:[],baseline_listeners:[]},WebSocketClass:FakeWebSocket});
    await driver.captureBaseline({sandboxRoot:fixture.root,runRoot:fixture.runRoot,evidenceRoot});const binding=await driver.bindSession({runRoot:fixture.runRoot,evidenceRoot});
    assert.deepEqual(binding,{conversation_id:hostConversation,evolution_id:hostEvolution,host_run_id:hostRun});const state=await driver.inspect(binding);assert.equal(state.conversation_id,hostConversation);assert.ok(existsSync(state.evidence_locator));await driver.close();
  }finally{globalThis.WebSocket=prior;const relative=path.relative(run,fixture.root);assert.ok(relative&&!relative.startsWith("..")&&!path.isAbsolute(relative));rmSync(fixture.root,{recursive:true,force:false});}
});
test("unit-fake scripted driver never upgrades observed acceptance booleans",async t=>{
  const fixture=makeSandbox();
  const evidence=path.join(fixture.runRoot,"fake-evidence.txt");writeFileSync(evidence,"unit fake only\\n");
  const uuid=d=>`${d.repeat(8)}-${d.repeat(4)}-4${d.repeat(3)}-8${d.repeat(3)}-${d.repeat(12)}`;
  let lastInstance=null;let lastStatus="not_run";
  const driver={evidenceMode:"unit_fake",captureBaseline:async()=>({stable_hash:"baseline"}),bindSession:async()=>({conversation_id:uuid("3"),evolution_id:uuid("2")}),inspect:async binding=>({conversation_id:binding.conversation_id,current_instance_id:lastInstance,candidate_status:lastStatus,evidence_locator:evidence}),patch:async({broken})=>({broken}),snapshot:async({patch})=>({source_snapshot_id:`sha256:${sha(patch.broken?"broken":"fixed")}`,broken:patch.broken}),build:async({snapshot})=>({candidate_id:`sha256:${sha(snapshot.source_snapshot_id)}`,source_snapshot_id:snapshot.source_snapshot_id,broken:snapshot.broken}),launch:async({build})=>{lastInstance=build.broken?uuid("4"):uuid("5");lastStatus=build.broken?"failed":"ready";return{status:lastStatus,exit_code:build.broken?17:0,stderr_excerpt:build.broken?"fixture_start_failed":"",instance_id:lastInstance,candidate_id:build.candidate_id,source_snapshot_id:build.source_snapshot_id,evidence_locator:evidence,components:build.broken?[]:[{ready:true},{ready:true}]}},deliverLateReady:async({binding,repaired_instance_id,repaired_candidate_id})=>({accepted:false,projection:{conversation_id:binding.conversation_id,current_instance_id:repaired_instance_id,current_candidate_id:repaired_candidate_id}}),captureConversationEvidence:async()=>evidence,stop:async()=>({status:"stopped",descendants_verified:false,components:[{exit_observed:true},{exit_observed:true}]}),captureFinal:async()=>({evidence_locators:[evidence],summary_evidence:evidence,control_release_unchanged:false})};
  const result=await runScriptedRecovery({sandboxRoot:fixture.root,runRoot:fixture.runRoot,authorization:{mode:"scripted",localConfirmed:true,hostConfirmed:true,modelConfirmed:false},driver});
  t.after(()=>{const relative=path.relative(run,fixture.root);assert.ok(relative&&!relative.startsWith("..")&&!path.isAbsolute(relative));rmSync(fixture.root,{recursive:true,force:false});});
  assert.equal(result.summary.candidate_fail_observed,false);assert.equal(result.summary.candidate_repaired_ready,false);assert.equal(result.summary.scripted_recovery_verified,false);assert.equal(result.summary.agent_recovery_verified,false);
  assert.ok(result.summary.open_checks.includes("scripted_driver_is_not_a_trusted_host"));
});

test("true summary fields need current in-run evidence and agent identity",()=>{
  const fixture=makeSandbox();
  const summary=createDrillSummary("scripted");summary.main_conversation_preserved=true;
  assert.throws(()=>validateDrillSummary(summary,fixture.runRoot),/evidence/i);
  const evidence=path.join(fixture.runRoot,"evidence.txt");writeFileSync(evidence,"same-session inspect receipt\\n");
  recordDrillEvidence(summary,{field:"main_conversation_preserved",value:true,locator:evidence,observed_at:new Date().toISOString()},fixture.runRoot);
  assert.doesNotThrow(()=>validateDrillSummary(summary,fixture.runRoot));
  recordDrillEvidence(summary,{field:"agent_recovery_verified",value:true,locator:evidence,observed_at:new Date().toISOString()},fixture.runRoot);
  assert.throws(()=>validateDrillSummary(summary,fixture.runRoot),/provider|model/i);
  rmSync(fixture.root,{recursive:true,force:false});
});




test("T9 recovery scope accepts only the fixed T4 three-file extension",()=>{
  const fixed=recoveryFixtureScopeCompatibility(["evolve/tool-catalog/INDEX.md"]);
  assert.equal(fixed.compatible,false);
  assert.deepEqual(fixed.missing_paths,["evolve/tool-catalog/INDEX.md","evolve/tool-catalog/recovery/main.cjs","evolve/tool-catalog/recovery/service.cjs","evolve/tool-catalog/recovery/manifest.json"]);
  assert.equal(recoveryFixtureScopeCompatibility(fixed.required_paths).compatible,true);
  assert.equal(recoveryFixtureScopeCompatibility(["*",...fixed.required_paths.slice(1)]).compatible,false);
  assert.equal(recoveryFixtureScopeCompatibility([...fixed.required_paths,"unplanned.txt"]).compatible,false);
});

test("CLI prepares the exact T4 mutable source scope and keeps unit-driver evidence untrusted",async()=>{
  const runRoot=path.join(run,"t9-cli-"+randomUUID());const output=[];const exitBefore=process.exitCode;
  const fixtureProfile="t4-recovery-node-v1";
  const freezeHostFixture=async({runRoot,candidateFixtureProfile})=>{
    assert.equal(candidateFixtureProfile,fixtureProfile);
    const hostRoot=path.join(runRoot,"host");const appRoot=path.join(hostRoot,"app");const profileRoot=path.join(hostRoot,"profile");
    mkdirSync(path.join(appRoot,"evolve","tool-catalog"),{recursive:true});mkdirSync(profileRoot,{recursive:true});
    writeFileSync(path.join(appRoot,"evolve","tool-catalog","INDEX.md"),"fixed fixture index\n");
    const profile={profile_root:profileRoot,control_release_root:path.join(runRoot,"control-release"),trusted_python_executable:process.execPath,ws_port:0,renderer_port:0,fixture_scope_id:"66666666-6666-4666-8666-666666666666",candidate_fixture_profile:fixtureProfile};
    const profileBytes=Buffer.from(JSON.stringify(profile,null,2)+"\n");writeFileSync(path.join(hostRoot,"evolution-test-profile.json"),profileBytes);
    const manifest={format:1,scope:"evolution-slice-a-h1-test-host",app_root:"app",renderer_root:"app/desktop/dist",profile_root:"profile",files:[{path:"evolve/tool-catalog/INDEX.md",byte_length:18,sha256:sha(Buffer.from("fixed fixture index\n"))}],mutable_candidate_sources:[],source_inputs:[],profile_sha256:sha(profileBytes),control_release_id:"sha256:"+"a".repeat(64),runtimes:{},python_environment_recheck:"required_at_host_start_and_shutdown",build_provenance:"test-only"};
    const manifestPath=path.join(hostRoot,"manifest.json");writeFileSync(manifestPath,JSON.stringify(manifest,null,2)+"\n");
    return{app_root:appRoot,profile_root:profileRoot,renderer_root:path.join(appRoot,"desktop","dist"),manifest_path:manifestPath};
  };
  const result=await (async()=>{try{return await main(["--mode","scripted","--run-root",runRoot],{io:{confirm:async()=>true},captureBaseline:()=>({captured_at:new Date().toISOString(),stable_files:[],baseline_listeners:[]}),freezeHostFixture,verifyFrozenHostFixture:()=>true,scriptedDriver:unitFakeDriver(path.join(runRoot,"unit-evidence.txt")),output:{write:value=>output.push(value)}});}finally{process.exitCode=exitBefore;}})();
  assert.equal(result.scripted_recovery_verified,false);assert.equal(result.agent_recovery_verified,false);
  assert.equal(result.open_checks.some(item=>item.startsWith("t8_owned_scope_mismatch:")),false);
  assert.ok(result.open_checks.includes("scripted_driver_is_not_a_trusted_host"));
  assert.equal(JSON.parse(readFileSync(path.join(runRoot,"candidate-source","manifest.json"),"utf8")).broken,true);
  assert.equal(existsSync(path.join(runRoot,"host","app","evolve","tool-catalog","recovery","manifest.json")),true);
  assert.deepEqual(JSON.parse(readFileSync(path.join(runRoot,"host","manifest.json"),"utf8")).mutable_candidate_sources.map(item=>item.path),["evolve/tool-catalog/recovery/main.cjs","evolve/tool-catalog/recovery/service.cjs","evolve/tool-catalog/recovery/manifest.json"]);
  assert.equal(output.length,1);assert.equal(readFileSync(path.join(runRoot,".evolution-test-run"),"utf8").trim(),"evolution-a");
  assert.equal(existsSync(path.join(runRoot,"host","logs")),false);
  const summary=JSON.parse(readFileSync(result.summary_path,"utf8"));assert.equal(summary.scripted_recovery_verified,false);
  const relative=path.relative(run,runRoot);assert.ok(relative&&!relative.startsWith("..")&&!path.isAbsolute(relative));rmSync(runRoot,{recursive:true,force:false});
});

test("CLI refuses non-interactive self-authorization before creating a run directory",async()=>{
  const target=path.join(run,"t9-no-tty-"+randomUUID());
  await assert.rejects(()=>main(["--mode","scripted","--run-root",target],{io:{input:{isTTY:false},output:{isTTY:false}}}),/interactive confirmations|permission_denied/i);
  assert.equal(existsSync(target),false);
});


test("stable baseline captures only selected source hashes and known listener identities",()=>{
  const fixture=makeSandbox();
  const source=path.join(fixture.root,"desktop","electron","main.ts");writeFileSync(source,"stable-main-v1\n");
  const netstatResult={error:null,status:0,stdout:"  TCP    127.0.0.1:8765  0.0.0.0:0  LISTENING  1234\n  TCP    127.0.0.1:5173  0.0.0.0:0  LISTENING  2222\n"};
  const baseline=captureStableBaseline(fixture.root,fixture.runRoot,{stableRoot:fixture.root,netstatResult});
  assert.equal(baseline.stable_files.find(item=>item.path==="desktop/electron/main.ts").status,"present");
  assert.equal(baseline.baseline_listeners.length,1);assert.equal(baseline.baseline_listeners[0].pid,1234);
  assert.doesNotThrow(()=>verifyStableBaselineUnchanged(baseline,baseline));
  const changed={...baseline,baseline_listeners:[]};assert.throws(()=>verifyStableBaselineUnchanged(baseline,changed),/listener/i);
  rmSync(fixture.root,{recursive:true,force:false});
});


test("opt-in T4 leaf process observes exit 17, then a fresh repaired process readies and stops",{skip:process.env.EVOLUTION_ALLOW_T9_LEAF_PROCESS!=="1"},async t=>{
  const {spawn}=await import("node:child_process");
  const fixtureRoot=path.join(run,"t9-leaf-process-"+randomUUID());mkdirSync(fixtureRoot,{recursive:false});
  const source=path.join(fixtureRoot,"candidate-source");mkdirSync(source,{recursive:false});
  const approved=path.resolve(process.cwd(),"desktop/tests/evolution/fixtures/candidate-node");
  for(const name of ["main.cjs","service.cjs","manifest.json"])writeFileSync(path.join(source,name),readFileSync(path.join(approved,name)),{flag:"wx"});
  t.after(()=>{const relative=path.relative(run,fixtureRoot);assert.ok(relative&&!relative.startsWith("..")&&!path.isAbsolute(relative));rmSync(fixtureRoot,{recursive:true,force:false});});
  const childEnv={};for(const key of ["SYSTEMROOT","WINDIR","PATH","TEMP","TMP"])if(process.env[key])childEnv[key]=process.env[key];
  const launch=()=>spawn(process.execPath,[path.join(source,"main.cjs")],{cwd:source,env:childEnv,stdio:["pipe","pipe","pipe"],windowsHide:true,shell:false});
  const collect=child=>{const state={stdout:"",stderr:""};child.stdout.setEncoding("utf8");child.stderr.setEncoding("utf8");child.stdout.on("data",chunk=>{state.stdout+=chunk;});child.stderr.on("data",chunk=>{state.stderr+=chunk;});return state;};
  const exit=child=>new Promise((resolve,reject)=>{child.once("error",reject);child.once("close",(code,signal)=>resolve({code,signal}));});
  const broken=launch();const brokenOutput=collect(broken);const brokenExit=await exit(broken);assert.equal(brokenExit.code,17);assert.match(brokenOutput.stderr,/fixture_start_failed/);
  writeFileSync(path.join(source,"manifest.json"),JSON.stringify({broken:false}),{flag:"w"});
  const repaired=launch();const repairedOutput=collect(repaired);const ready=new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error("fixture ready timed out")),5000);repaired.stdout.on("data",chunk=>{for(const line of repairedOutput.stdout.split(/\r?\n/)){if(!line)continue;try{const message=JSON.parse(line);if(message.type==="ready"){clearTimeout(timer);resolve(message);return;}}catch{/* incomplete line */}}});repaired.once("error",reject);});
  const identity={candidate_id:"sha256:"+"a".repeat(64),instance_id:"99999999-9999-4999-8999-999999999999",boot_nonce:"b".repeat(64),component_id:"88888888-8888-4888-8888-888888888888"};
  repaired.stdin.write(JSON.stringify({type:"boot",...identity})+"\n");const message=await ready;assert.deepEqual(message,{type:"ready",...identity});
  const stopped=exit(repaired);repaired.stdin.end(JSON.stringify({type:"stop",instance_id:identity.instance_id,boot_nonce:identity.boot_nonce})+"\n");const stoppedExit=await stopped;assert.equal(stoppedExit.code,0);assert.equal(stoppedExit.signal,null);
});

