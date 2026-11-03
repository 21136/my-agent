import assert from "node:assert/strict";
import test from "node:test";
import {readFileSync} from "node:fs";
import {createRequire} from "node:module";
const ts=createRequire(import.meta.url)("typescript");
function harness(){
 let nextUuid=0; const crypto={randomUUID:()=>`22222222-2222-4222-8222-${String(++nextUuid).padStart(12,"0")}`};
 const window={setTimeout:()=>1,clearTimeout:()=>{},myAgentDesktop:{}};
 class Socket {static OPEN=1;static CONNECTING=0;static instances=[];readyState=0;sent=[];handlers=new Map();constructor(){Socket.instances.push(this);queueMicrotask(()=>{this.readyState=1;this.emit("open",{});});}addEventListener(type,fn){this.handlers.set(type,[...this.handlers.get(type)??[],fn]);}emit(type,value){for(const fn of this.handlers.get(type)??[])fn(value);}message(v){this.emit("message",{data:JSON.stringify(v)});}send(v){this.sent.push(JSON.parse(v));}close(){this.readyState=3;this.emit("close",{});}}
 const cache=new Map();function load(url){if(cache.has(url.href))return cache.get(url.href);const exports={};cache.set(url.href,exports);const js=ts.transpileModule(readFileSync(url,"utf8"),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;new Function("exports","require","window","WebSocket","crypto",js)(exports,(id)=>{assert.ok(id.startsWith("."));return load(new URL(id.endsWith(".ts")?id:id+".ts",url));},window,Socket,crypto);return exports;}
 return {client:new (load(new URL("../src/api/ws.ts",import.meta.url)).AgentWsClient)("127.0.0.1",1234),Socket,crypto};
}
const REQUEST="11111111-1111-4111-8111-111111111111",CARD="wc-"+"a".repeat(64),SESSION="20261030-00000001";
const scope={session_id:SESSION,kind:"independent"};
const snapshot={kind:"independent",session_id:SESSION,project_id:null,item_id:"item-1",captured_at:"2026-10-30T01:00:00Z",card:{kind:"independent",session_id:SESSION,project_id:null,item_id:"item-1",title:"synthetic",status:"active",source_refs:[],requirements:[]},completeness:"complete",item_count:1,requirement_count:0,result_count:0,omitted_item_count:0,omitted_requirement_count:0,omitted_result_count:0,incomplete_reason:null,persisted:false,history_complete:true,missing_current_result_count:0};
const card={card_id:CARD,snapshot,saved_at:"2026-10-30T01:00:00Z",viewed_at:null};
async function ready(){const h=harness();await h.client.connect();h.socket=h.Socket.instances[0];h.socket.message({type:"session.banner",session_id:SESSION});return h;}

test("requests use exact payloads and never auto-retry or mark seen",async()=>{const{client,socket}=await ready();client.createWorkCard(REQUEST,"item-1");client.getWorkCard(CARD);client.markWorkCardSeen(CARD);const listId=client.listWorkCards({limit:20,beforeCardId:CARD});assert.deepEqual(socket.sent.slice(1),[{type:"work-card.create",request_id:REQUEST,item_id:"item-1"},{type:"work-card.get",card_id:CARD},{type:"work-card.seen",card_id:CARD},{type:"work-card.list",request_id:listId,limit:20,before_card_id:CARD}]);client.destroy();});
test("invalid identities and bounds fail before sending",async()=>{const{client,socket}=await ready();const n=socket.sent.length;for(const f of [()=>client.createWorkCard("bad"),()=>client.createWorkCard(REQUEST,"../path"),()=>client.getWorkCard("bad"),()=>client.markWorkCardSeen("bad"),()=>client.listWorkCards({limit:21}),()=>client.listWorkCards({limit:0})])assert.throws(f);assert.equal(socket.sent.length,n);client.destroy();});
test("only correlated valid results reach event channel",async()=>{const{client,socket}=await ready();const seen=[];client.onEvent(e=>seen.push(e));client.createWorkCard(REQUEST,"item-1");socket.message({type:"work-card.create.done",status:"saved",request_id:REQUEST,card_id:CARD,card:{...card,snapshot:{...snapshot,session_id:"other"}},source_scope:scope,message:"saved"});socket.message({type:"work-card.create.done",status:"saved",request_id:REQUEST,card_id:CARD,card,source_scope:scope,message:"saved"});assert.equal(seen.length,1);assert.equal(seen[0].card.viewed_at,null);client.getWorkCard(CARD);socket.message({type:"work-card.get.done",status:"found",card_id:CARD,card,source_scope:scope,message:"found"});client.markWorkCardSeen(CARD);socket.message({type:"work-card.seen.done",status:"acknowledged",card_id:CARD,viewed_at:"2026-10-30T01:01:00Z",source_scope:scope,message:"viewed"});assert.equal(seen.length,3);assert.equal(seen[2].status,"acknowledged");client.destroy();});
test("late events after switching session are suppressed and never replayed on reconnect",async()=>{const{client,socket,Socket}=await ready();const seen=[];client.onEvent(e=>seen.push(e));client.createWorkCard(REQUEST);await client.openSession("20261030-00000002");socket.message({type:"session.banner",session_id:"20261030-00000002"});socket.message({type:"work-card.create.done",status:"saved",request_id:REQUEST,card_id:CARD,card,source_scope:scope,message:"saved"});assert.equal(seen.filter(e=>e.type.startsWith("work-card.")).length,0);assert.throws(()=>client.createWorkCard(REQUEST));socket.close();await client.connect();const fresh=Socket.instances[1];fresh.message({type:"session.banner",session_id:SESSION});assert.doesNotThrow(()=>client.createWorkCard(REQUEST));assert.deepEqual(fresh.sent.filter(e=>e.type.startsWith("work-card.")),[{type:"work-card.create",request_id:REQUEST}]);client.destroy();});
test("list requires matching session scope and complete index; errors have no stale content",async()=>{const{client,socket}=await ready();const events=[];client.onEvent(e=>events.push(e));const listId=client.listWorkCards({limit:1});const entry={card_id:CARD,scope:{kind:"independent",item_id:"item-1"},saved_at:card.saved_at,viewed_at:null,title:"synthetic"};const response={type:"work-card.list.done",status:"ok",message:"ok",request_id:listId,items:[entry],has_more:true,next_cursor:CARD};socket.message(response);socket.message({...response,source_scope:{session_id:"other"}});assert.equal(events.length,0);socket.message({...response,source_scope:{session_id:SESSION}});assert.equal(events.length,1);client.getWorkCard(CARD);socket.message({type:"work-card.get.done",status:"not_found",message:"missing",card_id:CARD,card});assert.equal(events.length,1);socket.message({type:"work-card.get.done",status:"not_found",message:"missing",card_id:CARD});assert.equal(events.length,2);client.destroy();});
test("strict identifiers reject trailing newlines",async()=>{const{client}=await ready();assert.throws(()=>client.createWorkCard(REQUEST+"\n"));assert.throws(()=>client.getWorkCard(CARD+"\n"));assert.throws(()=>client.createWorkCard(REQUEST,"item-1\n"));client.destroy();});
test("wrong-session scope for get and seen is never delivered",async()=>{const{client,socket}=await ready();const events=[];client.onEvent(e=>events.push(e));client.getWorkCard(CARD);socket.message({type:"work-card.get.done",status:"found",card_id:CARD,card,source_scope:{...scope,session_id:"old"},message:"found"});client.markWorkCardSeen(CARD);socket.message({type:"work-card.seen.done",status:"acknowledged",card_id:CARD,viewed_at:card.saved_at,source_scope:{...scope,session_id:"old"},message:"seen"});assert.equal(events.length,0);socket.message({type:"work-card.get.done",status:"found",card_id:CARD,card,source_scope:scope,message:"found"});socket.message({type:"work-card.seen.done",status:"already_acknowledged",card_id:CARD,viewed_at:card.saved_at,source_scope:scope,message:"seen"});assert.equal(events.length,2);client.destroy();});
test("list errors require matching request id and permit another request",async()=>{const{client,socket}=await ready();const events=[];client.onEvent(e=>events.push(e));const id=client.listWorkCards();socket.message({type:"work-card.list.done",status:"session_unavailable",message:"old"});assert.equal(events.length,0);socket.message({type:"work-card.list.done",request_id:id,status:"session_unavailable",message:"unavailable"});assert.equal(events.length,1);assert.notEqual(client.listWorkCards(),id);client.destroy();});
test("explicit retry reuses UUID and reports already saved without marking viewed",async()=>{const{client,socket}=await ready();const events=[];client.onEvent(e=>events.push(e));client.createWorkCard(REQUEST,"item-1");socket.message({type:"work-card.create.done",status:"saved",message:"saved",request_id:REQUEST,card_id:CARD,card,source_scope:scope});client.createWorkCard(REQUEST,"item-1");socket.message({type:"work-card.create.done",status:"already_saved",message:"replayed",request_id:REQUEST,card_id:CARD,card,source_scope:scope});assert.deepEqual(events.map(e=>e.status),["saved","already_saved"]);assert.equal(events[1].card.viewed_at,null);assert.equal(socket.sent.filter(e=>e.type==="work-card.create").length,2);assert.equal(socket.sent.filter(e=>e.type==="work-card.seen").length,0);client.destroy();});
test("incomplete nested source reference is not emitted",async()=>{const{client,socket}=await ready();const events=[];client.onEvent(e=>events.push(e));client.getWorkCard(CARD);const malformed={...card,snapshot:{...snapshot,card:{...snapshot.card,source_refs:[{kind:"synthetic"}]}}};socket.message({type:"work-card.get.done",status:"found",message:"found",card_id:CARD,card:malformed,source_scope:scope});assert.equal(events.length,0);client.destroy();});

test("list pagination drops late duplicate and foreign session replies",async()=>{const{client,socket}=await ready();const events=[];client.onEvent(e=>events.push(e));const first=client.listWorkCards({limit:1});const entry={card_id:CARD,scope:{kind:"independent",item_id:"item-1"},saved_at:card.saved_at,viewed_at:null,title:"synthetic"};const response={type:"work-card.list.done",status:"ok",message:"ok",request_id:first,items:[entry],has_more:true,next_cursor:CARD,source_scope:{session_id:SESSION}};socket.message(response);const second=client.listWorkCards({limit:1,beforeCardId:CARD});assert.notEqual(first,second);socket.message(response);socket.message({...response,request_id:second,source_scope:{session_id:"other"}});assert.equal(events.length,1);socket.message({...response,request_id:second,items:[],has_more:false,next_cursor:null});assert.deepEqual(events.map(e=>e.request_id),[first,second]);socket.message({...response,request_id:second});assert.equal(events.length,2);client.destroy();});
test("list request ids isolate switch and reconnect, including errors",async()=>{const{client,socket,Socket}=await ready();const events=[];client.onEvent(e=>events.push(e));const old=client.listWorkCards();await client.openSession("other");socket.message({type:"session.banner",session_id:"other"});const current=client.listWorkCards();socket.message({type:"work-card.list.done",request_id:old,status:"unavailable",message:"old"});socket.message({type:"work-card.list.done",request_id:current,status:"unavailable",message:"current"});assert.deepEqual(events.filter(e=>e.type==="work-card.list.done").map(e=>e.request_id),[current]);socket.close();await client.connect();const fresh=Socket.instances[1];fresh.message({type:"session.banner",session_id:SESSION});const next=client.listWorkCards();assert.notEqual(next,current);socket.message({type:"work-card.list.done",request_id:next,status:"unavailable",message:"old socket"});assert.equal(events.filter(e=>e.type==="work-card.list.done").length,1);client.destroy();});

test("list forbids parallel pages and reused UUIDs even after completion",async()=>{const{client,socket,crypto}=await ready();const id=client.listWorkCards();assert.match(id,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);assert.throws(()=>client.listWorkCards());socket.message({type:"work-card.list.done",request_id:id,status:"invalid_cursor",message:"invalid"});crypto.randomUUID=()=>id;const before=socket.sent.length;assert.throws(()=>client.listWorkCards());assert.equal(socket.sent.length,before);client.destroy();});

test("linked copy index scopes validate server shapes and complete pagination", async () => {
  const {client, socket} = await ready();
  const events = [];
  client.onEvent(e => events.push(e));
  const projectScope = {kind: "linked_project_session_copy", project_id: "project-1", source_session_id: "source-session-1", mapping_claim: "not_asserted"};
  const itemScope = {kind: "linked_item_copy", project_id: "project-1", item_id: "item-1"};
  const entry = scope => ({card_id: CARD, scope, saved_at: card.saved_at, viewed_at: null, title: "synthetic copy"});
  const first = client.listWorkCards({limit: 1});
  const response = {type: "work-card.list.done", status: "ok", message: "ok", request_id: first,
    items: [entry(projectScope)], has_more: true, next_cursor: CARD, source_scope: {session_id: SESSION}};
  for (const bad of [
    {...projectScope, mapping_claim: "current"}, {...projectScope, project_id: ""},
    {...projectScope, project_id: "../escape"}, {...projectScope, source_session_id: "bad\n"},
    {...projectScope, source_session_id: ""}, {...projectScope, extra: "x"},
    {kind: projectScope.kind, project_id: projectScope.project_id, mapping_claim: "not_asserted"},
  ]) socket.message({...response, items: [entry(bad)]});
  assert.equal(events.length, 0);
  socket.message(response);
  assert.equal(events.length, 1);
  assert.deepEqual(events[0].items[0].scope, projectScope);
  const second = client.listWorkCards({limit: 1, beforeCardId: CARD});
  const secondCard = "wc-" + "b".repeat(64);
  const last = {...response, request_id: second, items: [{...entry(itemScope), card_id: secondCard}], has_more: false, next_cursor: null};
  for (const bad of [
    {...itemScope, item_id: ""}, {...itemScope, item_id: "../escape"},
    {...itemScope, project_id: "bad\n"}, {...itemScope, project_id: ""},
    {...itemScope, extra: "x"}, {kind: itemScope.kind, project_id: itemScope.project_id},
  ]) socket.message({...last, items: [{...entry(bad), card_id: secondCard}]});
  assert.equal(events.length, 1);
  socket.message({...last, source_scope: {session_id: "other"}});
  assert.equal(events.length, 1);
  socket.message(last);
  assert.deepEqual(events.map(e => e.request_id), [first, second]);
  assert.deepEqual(events[1].items[0].scope, itemScope);
  assert.equal(events[1].has_more, false);
  assert.doesNotThrow(() => client.listWorkCards());
  client.destroy();
});
