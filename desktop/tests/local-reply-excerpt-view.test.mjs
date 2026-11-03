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

const detail = {session_id:SESSION,item_id:'item-1',requirement_id:'req-1',result_id:'result-1',label:'合成摘录 <unsafe>',escaped_excerpt_text:'&lt;img src=x onerror=alert(1)&gt; &amp;lt;script&amp;gt; &#x27;quoted&#x27;',source_availability:'unavailable',result_status:'unknown',verification_status:'not_verified'};
const FIRST='11111111-1111-4111-8111-111111111111', SECOND='22222222-2222-4222-8222-222222222222';
const event = {type:'work-card.result.view.done',status:'acknowledged',message:'ok',session_id:SESSION,presentation_id:PRESENTATION,card_id:CARD,result_id:'result-1',requirement_id:'req-1',local_assistant_reply_excerpt_detail:detail};
test('websocket accepts only valid bound optional local detail and keeps old responses',async()=>{
 const {client,Socket}=load();await client.connect();const socket=Socket.instances[0];socket.message({type:'session.banner',session_id:SESSION});const events=[];client.onEvent(e=>events.push(e));
 const id={sessionId:SESSION,presentationId:PRESENTATION,cardId:CARD,resultId:'result-1',requirementId:'req-1'};client.acknowledgeWorkCardResultView(id);
 for(const patch of [{session_id:'wrong'},{result_id:'wrong'},{card_id:'wc-'+ 'b'.repeat(64)},{presentation_id:'11111111-1111-4111-8111-111111111111'},{requirement_id:'wrong'}])socket.message({...event,...patch});
 for(const patch of [{session_id:'wrong'},{item_id:''},{requirement_id:'wrong'},{result_id:'wrong'},{verification_status:'verified'},{escaped_excerpt_text:'<img>'},{escaped_excerpt_text:'&#x3c;img>'},{extra:'bad'}])socket.message({...event,local_assistant_reply_excerpt_detail:{...detail,...patch}});
 assert.equal(events.length,0);socket.message(event);assert.equal(events.length,1);assert.deepEqual(events[0].local_assistant_reply_excerpt_detail,detail);socket.message(event);assert.equal(events.length,1);
 client.acknowledgeWorkCardResultView(id);const {local_assistant_reply_excerpt_detail,...legacy}=event;socket.message(legacy);assert.equal(events.length,2);client.destroy();
});
const moduleExports={};const moduleJs=ts.transpileModule(readFileSync(new URL('../src/shells/unified/work-card-presentation.ts',import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText;new Function('exports','require',moduleJs)(moduleExports,()=>({}));
class ElementFake {constructor(tag='details'){this.tag=tag;this.dataset={resultId:'result-1',requirementId:'req-1'};this.children=[];this.style={};this.open=true;this.isConnected=true;this.ownerDocument={createElement:t=>new ElementFake(t)};}closest(){return {dataset:{sessionId:SESSION,presentationId:PRESENTATION,cardId:CARD}}}setAttribute(k){this.attr=k}append(...v){this.children.push(...v);for(const c of v)c.parent=this}querySelector(){return this.children.find(x=>x.attr==='data-local-reply-excerpt')||null}remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this)}}
function setup(){const view=new moduleExports.WorkCardResultExcerptView(),node=new ElementFake(),entry={sessionId:SESSION,presentationId:PRESENTATION,cardId:CARD,status:'resolved',card:{snapshot:{kind:'independent',card:{item_id:'item-1',requirements:[{requirement_id:'req-1',latest_result:{result_id:'result-1'}}]}}}};const request={session_id:SESSION,presentation_id:PRESENTATION,card_id:CARD,requirement_id:'req-1',result_id:'result-1',request_id:FIRST};return {view,node,entry,request}}
test('one explicit open displays decoded once as text and close erases it, without modifying card',()=>{const {view,node,entry,request}=setup(),before=JSON.stringify(entry.card);view.opened(node,entry,request);assert.equal(view.accept({...event,request_id:FIRST},SESSION,entry),true);assert.equal(node.children[0].children[1].textContent,"<img src=x onerror=alert(1)> &lt;script&gt; 'quoted'");assert.match(node.children[0].children[0].textContent,/未核验/);assert.equal(JSON.stringify(entry.card),before);view.closed(node);assert.equal(node.children.length,0);view.opened(node,entry,{...request,request_id:SECOND});assert.equal(view.accept({...event,request_id:FIRST},SESSION,entry),false);assert.equal(view.accept({...event,request_id:SECOND},SESSION,entry),true)});
test('closed, re-rendered, stale, mismatched and out-of-order responses cannot populate detail',()=>{
 for(const scenario of ['closed','reopened','detached','session','entry','item','result','old-event']){const {view,node,entry,request}=setup();view.opened(node,entry,request);let e={...event,request_id:FIRST},session=SESSION,actualEntry=entry;
 if(scenario==='closed'){node.open=false;view.closed(node)}if(scenario==='reopened'){node.open=false;view.closed(node);node.open=true;view.opened(node,entry,{...request,request_id:SECOND})}if(scenario==='detached')node.isConnected=false;if(scenario==='session')session='other';if(scenario==='entry')actualEntry={...entry};if(scenario==='item')e={...event,request_id:FIRST,local_assistant_reply_excerpt_detail:{...detail,item_id:'foreign'}};if(scenario==='result')e={...event,request_id:FIRST,result_id:'wrong'};if(scenario==='old-event'){view.clear();view.opened(node,entry,{...request,request_id:SECOND})}
 assert.equal(view.accept(e,session,actualEntry),false,scenario);assert.equal(node.children.length,0,scenario);
 }
});

test('request IDs correlate concurrent retries, malformed and old responses never settle current request',async()=>{
 const {client,Socket}=load();await client.connect();const socket=Socket.instances[0];socket.message({type:'session.banner',session_id:SESSION});const got=[];client.onEvent(e=>got.push(e));const base={sessionId:SESSION,presentationId:PRESENTATION,cardId:CARD,resultId:'result-1',requirementId:'req-1'};
 client.acknowledgeWorkCardResultView({...base,requestId:FIRST});client.cancelWorkCardResultView(FIRST);client.acknowledgeWorkCardResultView({...base,requestId:SECOND});assert.equal(socket.sent.at(-1).request_id,SECOND);
 socket.message({...event,request_id:FIRST});socket.message(event);socket.message({...event,request_id:SECOND,requirement_id:'wrong'});assert.equal(got.length,0);
 socket.message({...event,request_id:SECOND});assert.equal(got.length,1);socket.message({...event,request_id:SECOND});assert.equal(got.length,1);assert.throws(()=>client.acknowledgeWorkCardResultView({...base,requestId:FIRST}));client.destroy();
});

test('concurrent ID-bound responses are correlated independently in either order and errors echo the ID',async()=>{
 const {client,Socket}=load();await client.connect();const socket=Socket.instances[0];socket.message({type:'session.banner',session_id:SESSION});const got=[];client.onEvent(e=>got.push(e));const base={sessionId:SESSION,presentationId:PRESENTATION,cardId:CARD,resultId:'result-1',requirementId:'req-1'};
 assert.throws(()=>client.acknowledgeWorkCardResultView({...base,requestId:'NOT-A-UUID'}));client.acknowledgeWorkCardResultView({...base,requestId:FIRST});client.acknowledgeWorkCardResultView({...base,requestId:SECOND});
 socket.message({...event,request_id:SECOND});assert.deepEqual(got.map(x=>x.request_id),[SECOND]);socket.message({...event,request_id:FIRST});assert.deepEqual(got.map(x=>x.request_id),[SECOND,FIRST]);
 const THIRD='33333333-3333-4333-8333-333333333333';client.acknowledgeWorkCardResultView({...base,requestId:THIRD});const {local_assistant_reply_excerpt_detail,...error}=event;socket.message({...error,status:'invalid_request',request_id:THIRD});assert.equal(got.at(-1).status,'invalid_request');assert.equal(got.at(-1).request_id,THIRD);client.destroy();
});

test('unified shell generates and sends a fresh canonical ID on each expansion',()=>{
 const source=readFileSync(new URL('../src/shells/unified/index.ts',import.meta.url),'utf8');const ast=ts.createSourceFile('index.ts',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TS);
 function find(pred,n=ast){if(pred(n))return n;return ts.forEachChild(n,c=>find(pred,c))}
 const call=find(n=>ts.isCallExpression(n)&&n.expression.getText(ast)==='bindWorkCardResultViewEvents');const options=call.arguments[1],property=options.properties.find(p=>p.name?.getText(ast)==='send');assert.ok(property);
 const sent=[],ids=[FIRST,SECOND],crypto={randomUUID:()=>ids.shift()},client={acknowledgeWorkCardResultView:v=>sent.push(v)};
 const code=ts.transpileModule('return ('+property.initializer.getText(ast)+');',{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
 const send=new Function('client','crypto',code)(client,crypto);for(let i=0;i<2;i++){const r={session_id:SESSION,presentation_id:PRESENTATION,card_id:CARD,requirement_id:'req-1',result_id:'result-1'};send(r);assert.equal(r.request_id,sent[i].requestId)}assert.deepEqual(sent.map(v=>v.requestId),[FIRST,SECOND]);
});
