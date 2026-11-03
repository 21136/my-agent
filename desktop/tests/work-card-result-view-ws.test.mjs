import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
const ts = createRequire(import.meta.url)("typescript");
const SESSION = "20261030-abcdef12", CARD = `wc-${"a".repeat(64)}`;
const PRESENTATION = "7cfe09c0-4f54-4d04-9f14-9815b4374e58";
function load() {
  const timers = new Map(); let nextTimer = 1; const window = { setTimeout: (fn, ms) => { const id=nextTimer++;timers.set(id,{fn,ms});return id; }, clearTimeout: id => timers.delete(id), myAgentDesktop: {} };
  class Socket { static OPEN=1; static instances=[]; readyState=0; handlers=new Map(); sent=[]; constructor(){Socket.instances.push(this);queueMicrotask(()=>{this.readyState=1;this.emit("open",{});});} addEventListener(t,f){this.handlers.set(t,[...(this.handlers.get(t)||[]),f]);} emit(t,v){for(const f of this.handlers.get(t)||[])f(v);} send(v){this.sent.push(JSON.parse(v));} close(){this.readyState=3;this.emit("close",{});} message(v){this.emit("message",{data:JSON.stringify(v)});} }
  const url = new URL("../src/api/ws.ts", import.meta.url), exports={};
  const js=ts.transpileModule(readFileSync(url,"utf8"),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  new Function("exports","require","window","WebSocket","crypto",js)(exports,()=>({}),window,Socket,{randomUUID:()=>"22222222-2222-4222-8222-222222222222"});
  return {client:new exports.AgentWsClient("127.0.0.1",1234),Socket,timers};
}
test("explicit result view sends exact identities, rejects stale session, and correlates truthful response",async()=>{
  const {client,Socket}=load(); await client.connect(); const socket=Socket.instances[0]; socket.message({type:"session.banner",session_id:SESSION});
  const received=[]; client.onEvent(e=>received.push(e));
  const identity={sessionId:SESSION,presentationId:PRESENTATION,cardId:CARD,resultId:"result-1",requirementId:"req-1"};
  assert.throws(()=>client.acknowledgeWorkCardResultView({...identity,sessionId:"old"}));
  client.acknowledgeWorkCardResultView(identity);
  assert.deepEqual(socket.sent.at(-1),{type:"work-card.result.view",session_id:SESSION,presentation_id:PRESENTATION,card_id:CARD,result_id:"result-1",requirement_id:"req-1"});
  socket.message({type:"work-card.result.view.done",status:"acknowledged",message:"explicit result view",session_id:SESSION,presentation_id:PRESENTATION,card_id:CARD,result_id:"result-1",requirement_id:"req-1"});
  assert.equal(received.length,1); assert.equal(received[0].status,"acknowledged");
  client.acknowledgeWorkCardResultView(identity);
  socket.message({type:"work-card.result.view.done",status:"rejected",message:"snapshot mismatch",session_id:SESSION,presentation_id:PRESENTATION,card_id:CARD,result_id:"result-1",requirement_id:"req-1"});
  assert.equal(received.length,2); assert.equal(received[1].status,"rejected");
  client.destroy();
});

test("snapshot view correlates explicit session and suppresses duplicates without treating receipt as viewed",async()=>{
  const {client,Socket}=load(); await client.connect(); const socket=Socket.instances[0]; socket.message({type:"session.banner",session_id:SESSION});
  client.linkedItemCopyIdentities.set(CARD,{projectId:"p",itemId:"i"});
  const identity={sessionId:SESSION,presentationId:PRESENTATION,cardId:CARD}; const events=[];client.onEvent(e=>events.push(e));
  assert.throws(()=>client.acknowledgeWorkCardSnapshotView({...identity,sessionId:"old"}));
  const originalSend=socket.send; socket.send=()=>{throw new Error("offline")};
  assert.throws(()=>client.acknowledgeWorkCardSnapshotView(identity)); socket.send=originalSend;
  client.acknowledgeWorkCardSnapshotView(identity);
  assert.deepEqual(socket.sent.at(-1),{type:"work-card.snapshot.view",session_id:SESSION,presentation_id:PRESENTATION,card_id:CARD});
  client.acknowledgeWorkCardSnapshotView(identity); assert.equal(socket.sent.filter(e=>e.type==="work-card.snapshot.view").length,1);
  socket.message({type:"work-card.snapshot.view.done",status:"acknowledged",message:"ok",session_id:"old",presentation_id:PRESENTATION,card_id:CARD});
  assert.equal(events.length,0);
  socket.message({type:"work-card.snapshot.view.done",status:"acknowledged",message:"ok",session_id:SESSION,presentation_id:PRESENTATION,card_id:CARD});
  assert.equal(events.length,1); client.acknowledgeWorkCardSnapshotView(identity); assert.equal(socket.sent.filter(e=>e.type==="work-card.snapshot.view").length,1);
  socket.message({type:"session.banner",session_id:"new"}); assert.throws(()=>client.acknowledgeWorkCardSnapshotView(identity));
  client.destroy();
});

test("lost snapshot view response times out without auto retry; stale error and session switch cannot misconfirm",async()=>{
 const {client,Socket,timers}=load();await client.connect();const socket=Socket.instances[0];socket.message({type:"session.banner",session_id:SESSION});
 client.linkedItemCopyIdentities.set(CARD,{projectId:"p",itemId:"i"});const events=[];client.onEvent(e=>events.push(e));
 const identity={sessionId:SESSION,presentationId:PRESENTATION,cardId:CARD};
 client.acknowledgeWorkCardSnapshotView(identity);const first=[...timers.values()].find(x=>x.ms===15000);assert.ok(first,'bounded timeout registered');
 first.fn();assert.equal(socket.sent.filter(e=>e.type==='work-card.snapshot.view').length,1,'never auto resend');
 assert.equal(events.at(-1).type,'work-card.snapshot.view.timeout');assert.equal(events.at(-1).session_id,SESSION);
 client.acknowledgeWorkCardSnapshotView(identity);assert.equal(socket.sent.filter(e=>e.type==='work-card.snapshot.view').length,2);
 socket.message({type:'work-card.snapshot.view.done',status:'unavailable',message:'old reply',session_id:SESSION,presentation_id:PRESENTATION,card_id:CARD});
 assert.equal(events.filter(e=>e.type==='work-card.snapshot.view.done').length,0,'ambiguous old error cannot settle retry');
 socket.message({type:'work-card.snapshot.view.done',status:'acknowledged',message:'persisted',session_id:SESSION,presentation_id:PRESENTATION,card_id:CARD});
 assert.equal(events.filter(e=>e.type==='work-card.snapshot.view.done').length,1);
 first.fn();assert.equal(events.filter(e=>e.type==='work-card.snapshot.view.timeout').length,1,'old timer cannot affect completed retry');
 socket.message({type:'session.banner',session_id:'new'});first.fn();assert.equal(events.filter(e=>e.type==='work-card.snapshot.view.timeout').length,1);client.destroy();
});

test("session switch cancels pending snapshot timeout and rejects late reply",async()=>{
 const {client,Socket,timers}=load();await client.connect();const socket=Socket.instances[0];socket.message({type:'session.banner',session_id:SESSION});
 client.linkedItemCopyIdentities.set(CARD,{projectId:'p',itemId:'i'});const events=[];client.onEvent(e=>events.push(e));
 client.acknowledgeWorkCardSnapshotView({sessionId:SESSION,presentationId:PRESENTATION,cardId:CARD});const old=[...timers.values()].find(x=>x.ms===15000);
 socket.message({type:'session.banner',session_id:'new'});const count=events.length;old.fn();
 socket.message({type:'work-card.snapshot.view.done',status:'acknowledged',message:'late',session_id:SESSION,presentation_id:PRESENTATION,card_id:CARD});
 assert.equal(events.length,count);client.destroy();
});

test("same-session banner during timeout does not prevent explicit retry",async()=>{
 const {client,Socket,timers}=load();await client.connect();const socket=Socket.instances[0];socket.message({type:'session.banner',session_id:SESSION});
 client.linkedItemCopyIdentities.set(CARD,{projectId:'p',itemId:'i'});const id={sessionId:SESSION,presentationId:PRESENTATION,cardId:CARD};
 client.acknowledgeWorkCardSnapshotView(id);const timer=[...timers.values()].find(x=>x.ms===15000);timer.fn();
 socket.message({type:'session.banner',session_id:SESSION});client.acknowledgeWorkCardSnapshotView(id);
 assert.equal(socket.sent.filter(e=>e.type==='work-card.snapshot.view').length,2);client.destroy();
});

test("a duplicate failed reply from a prior attempt cannot settle a newer explicit retry",async()=>{
 const {client,Socket,timers}=load();await client.connect();const socket=Socket.instances[0];socket.message({type:'session.banner',session_id:SESSION});
 client.linkedItemCopyIdentities.set(CARD,{projectId:'p',itemId:'i'});const events=[];client.onEvent(e=>events.push(e));
 const id={sessionId:SESSION,presentationId:PRESENTATION,cardId:CARD},reply={type:'work-card.snapshot.view.done',status:'unavailable',message:'storage unavailable',session_id:SESSION,presentation_id:PRESENTATION,card_id:CARD};
 client.acknowledgeWorkCardSnapshotView(id);socket.message(reply);assert.equal(events.length,1);
 client.acknowledgeWorkCardSnapshotView(id);socket.message(reply);assert.equal(events.length,1,'ambiguous duplicate must not close retry');
 assert.ok([...timers.values()].some(v=>v.ms===15000),'retry timer remains active');
 socket.message({...reply,status:'acknowledged'});assert.equal(events.at(-1).status,'acknowledged');client.destroy();
});

test("stale timeout cannot cancel a newer retry and destroy clears the active timer",async()=>{
 const {client,Socket,timers}=load();await client.connect();const socket=Socket.instances[0];socket.message({type:'session.banner',session_id:SESSION});
 client.linkedItemCopyIdentities.set(CARD,{projectId:'p',itemId:'i'});const events=[];client.onEvent(e=>events.push(e));const id={sessionId:SESSION,presentationId:PRESENTATION,cardId:CARD};
 client.acknowledgeWorkCardSnapshotView(id);const first=[...timers.entries()].find(([,t])=>t.ms===15000);first[1].fn();
 client.acknowledgeWorkCardSnapshotView(id);const active=[...timers.entries()].find(([key,t])=>key!==first[0]&&t.ms===15000);assert.ok(active);
 first[1].fn();assert.ok(timers.has(active[0]));client.acknowledgeWorkCardSnapshotView(id);
 assert.equal(socket.sent.filter(e=>e.type==='work-card.snapshot.view').length,2);
 client.destroy();assert.equal(timers.has(active[0]),false);const count=events.length;active[1].fn();assert.equal(events.length,count);
});

test("project binding change in same session clears linked identities and rejects old response",async()=>{
 const {client,Socket,timers}=load();await client.connect();const socket=Socket.instances[0];socket.message({type:'session.banner',session_id:SESSION,project_id:'a'});
 client.linkedItemCopyIdentities.set(CARD,{projectId:'p',itemId:'i'});const events=[];client.onEvent(e=>events.push(e));const id={sessionId:SESSION,presentationId:PRESENTATION,cardId:CARD};
 client.acknowledgeWorkCardSnapshotView(id);const timer=[...timers.entries()].find(([,t])=>t.ms===15000);
 socket.message({type:'session.banner',session_id:SESSION,project_id:'b'});assert.equal(timers.has(timer[0]),false);assert.throws(()=>client.acknowledgeWorkCardSnapshotView(id));
 const count=events.length;socket.message({type:'work-card.snapshot.view.done',status:'acknowledged',message:'late',session_id:SESSION,presentation_id:PRESENTATION,card_id:CARD});assert.equal(events.length,count);client.destroy();
});
