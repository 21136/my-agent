import assert from "node:assert/strict";
import {mkdirSync,readFileSync,writeFileSync} from "node:fs";
import path from "node:path";
import {createRequire} from "node:module";
import {test} from "node:test";
const require=createRequire(import.meta.url);let bridge={};let profile={};try{bridge=require("../../../artifacts/evolution-control-build/main-bridge.js");}catch{}try{profile=require("../../../artifacts/evolution-control-build/test-profile.js");}catch{}
const run=process.env.EVOLUTION_TEST_ROOT;assert.ok(run);assert.equal(readFileSync(path.join(run,".evolution-test-run"),"utf8").replace(/^\uFEFF/,"").trim(),"evolution-a");
function exported(api,name){assert.equal(typeof api[name],"function",`${name} must be exported`);return api[name];}

test("main bridge accepts only current main-frame origin and current conversation",async()=>{
  const bind=exported(bridge,"bindEvolutionIpc");const handlers=new Map();const removed=[];const calls=[];const ipc={handle:(channel,fn)=>handlers.set(channel,fn),removeHandler:channel=>removed.push(channel)};
  const cleanup=bind({mainWindow:{id:77},ipc,trustedOrigin:"app://trusted",getConversationId:()=>"33333333-3333-4333-8333-333333333333",callHost:async message=>{calls.push(message);return {status:"succeeded"}}});
  await handlers.get("evolution:getState")({senderId:77,isMainFrame:true,origin:"app://trusted"},null);
  assert.deepEqual(calls,[{type:"evolution.inspect",conversation_id:"33333333-3333-4333-8333-333333333333"}]);
  await assert.rejects(()=>handlers.get("evolution:begin")({senderId:88,isMainFrame:true,origin:"app://trusted"},null),/denied|trusted|main/i);
  await assert.rejects(()=>handlers.get("evolution:stop")({senderId:77,isMainFrame:false,origin:"app://trusted"},null),/denied|trusted|main/i);
  await assert.rejects(()=>handlers.get("evolution:begin")({senderId:77,isMainFrame:true,origin:"https://evil"},{conversation_id:"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}),/denied|trusted|payload/i);
  assert.equal(calls.length,1);cleanup();assert.equal(removed.length,4);
});

test("test profile preserves dynamic port zero and stays under sandbox artifacts",()=>{
  const validate=exported(profile,"validateEvolutionTestProfile");const profileRoot=path.join(run,"host-fixture","profile");const releaseRoot=path.join(run,"host-fixture","control-release");mkdirSync(profileRoot,{recursive:true});mkdirSync(releaseRoot,{recursive:true});writeFileSync(path.join(releaseRoot,"manifest.json"),"{}\n");
  const fixture={profile_root:profileRoot,control_release_root:releaseRoot,trusted_python_executable:process.env.EVOLUTION_TRUSTED_PYTHON,ws_port:0,renderer_port:0,fixture_scope_id:"22222222-2222-4222-8222-222222222222"};
  const result=validate(fixture,process.cwd());assert.equal(result.ws_port,0);assert.equal(result.renderer_port,0);const recovery=validate({...fixture,candidate_fixture_profile:"t4-recovery-node-v1"},process.cwd());assert.equal(recovery.candidate_fixture_profile,"t4-recovery-node-v1");assert.throws(()=>validate({...fixture,candidate_fixture_profile:"arbitrary-root"},process.cwd()),/profile|invalid/i);
  assert.throws(()=>validate({...fixture,profile_root:path.resolve(process.cwd(),"data/profile")},process.cwd()),/scope|sandbox|path/i);
  assert.throws(()=>validate({...fixture,ws_port:8765},process.cwd()),/port|profile|invalid/i);
});
test("main bridge accepts active conversation updates only from the trusted main frame",async()=>{
  const bind=exported(bridge,"bindEvolutionIpc");const handlers=new Map();const removed=[];const calls=[];let active=null;
  const ipc={handle:(channel,fn)=>handlers.set(channel,fn),removeHandler:channel=>removed.push(channel)};
  const cleanup=bind({mainWindow:{id:77},ipc,trustedOrigin:"app://trusted",getConversationId:()=>active,setConversationId:id=>{active=id;},callHost:async message=>{calls.push(message);return {status:"queued"}}});
  const trusted={senderId:77,isMainFrame:true,origin:"app://trusted"};
  await handlers.get("evolution:session")(trusted,"33333333-3333-4333-8333-333333333333");
  await handlers.get("evolution:begin")(trusted,null);
  await handlers.get("evolution:session")(trusted,"20261026-1a2b3c4d");
  await handlers.get("evolution:begin")(trusted,null);
  assert.deepEqual(calls,[{type:"evolution.begin",conversation_id:"33333333-3333-4333-8333-333333333333"},{type:"evolution.begin",conversation_id:"20261026-1a2b3c4d"}]);
  await assert.rejects(()=>handlers.get("evolution:session")({senderId:78,isMainFrame:true,origin:"app://trusted"},"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),/denied|trusted|main/i);
  await assert.rejects(()=>handlers.get("evolution:session")(trusted,"not-a-uuid"),/invalid|conversation/i);
  await handlers.get("evolution:session")(trusted,null);assert.equal(active,null);
  cleanup();assert.equal(removed.length,4);
});

