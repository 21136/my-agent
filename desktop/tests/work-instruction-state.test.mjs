import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
const ts=createRequire(import.meta.url)("typescript"), cache=new Map();
function load(url){if(cache.has(url.href))return cache.get(url.href);const exports={};cache.set(url.href,exports);const js=ts.transpileModule(readFileSync(url,"utf8"),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;new Function("exports","require",js)(exports,id=>load(new URL(id.endsWith(".ts")?id:`${id}.ts`,url)));return exports;}
const {createChatSession}=load(new URL("../src/shells/chat-state.ts",import.meta.url));
const sessionId="session-a",runId="run-a",requestId="12345678-1234-4234-8234-123456789abc";
function setup(){const chat=createChatSession({showProcess:true,confirmInBlocks:true});chat.handleEvent({type:"session.banner",session_id:sessionId,topics:[],turn_mode:"agent",turn_mode_label:"动手",phase:"S4"});chat.handleEvent({type:"execution.state",run_id:runId,state:"running",sequence:1});chat.handleEvent({type:"turn.start",run_id:runId,intent:"execute",intent_label:"后台工作"});return chat;}
test("work instruction state is authoritative and bound to request, run, and session",()=>{
 const chat=setup();const turns=chat.model.turnCounter;
 assert.equal(chat.addWorkInstructionRequest("请跑一次相关验证",requestId,sessionId,runId),true);
 const block=chat.model.blocks.find(item=>item.kind==="work-instruction");
 assert.equal(block.state,"sending");assert.equal(chat.model.turnCounter,turns);
 const undeliveredId="12345678-1234-4234-8234-000000000001";
 assert.equal(chat.addWorkInstructionRequest("另一条",undeliveredId,sessionId,runId),true);
 chat.handleEvent({type:"confirm.request",request_id:"confirm-a",preview:"需要确认",allow_approve_all:false,run_id:runId});
 const before={run:chat.model.executionRunId,state:chat.model.executionState,confirm:chat.model.confirmPending,turns:chat.model.turnCounter};
 const queued={type:"work.instruction.state",request_id:requestId,run_id:runId,session_id:sessionId,state:"queued"};
 assert.equal(chat.handleEvent({...queued,session_id:"stale-session"}),false);
 assert.equal(chat.handleEvent({...queued,run_id:"stale-run"}),false);
 assert.equal(chat.handleEvent(queued),true);assert.equal(block.state,"queued");
 assert.equal(chat.handleEvent({...queued,state:"delivered",delivery_stage:"wrong_stage"}),false);assert.equal(block.state,"queued");
 assert.equal(chat.handleEvent({...queued,state:"delivered",delivery_stage:"next_model_request_context"}),true);
 assert.equal(block.state,"delivered");assert.equal(block.deliveryStage,"next_model_request_context");
 assert.deepEqual({run:chat.model.executionRunId,state:chat.model.executionState,confirm:chat.model.confirmPending,turns:chat.model.turnCounter},before);
 assert.equal(chat.handleEvent({type:"work.instruction.state",request_id:undeliveredId,run_id:runId,session_id:sessionId,state:"undelivered",reason:"queue_full"}),true);
 const rejected=chat.model.blocks.find(item=>item.kind==="work-instruction"&&item.requestId===undeliveredId);
 assert.equal(rejected.state,"undelivered");assert.equal(rejected.reason,"queue_full");
 assert.equal(chat.model.executionState,"running");assert.equal(chat.model.confirmPending,true);
});
