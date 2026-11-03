import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
const ts=createRequire(import.meta.url)("typescript"), cache=new Map();
function load(url) { if(cache.has(url.href)) return cache.get(url.href); const out={};cache.set(url.href,out);const js=ts.transpileModule(readFileSync(url,"utf8"),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;new Function("exports","require",js)(out,id=>load(new URL(id.endsWith('.ts')?id:`${id}.ts`,url)));return out; }
const {createChatSession}=load(new URL('../src/shells/chat-state.ts',import.meta.url));
test('post-turn result is session-scoped and does not affect a live ordinary turn',()=>{
 const chat=createChatSession({showProcess:true,confirmInBlocks:false});
 const banner=id=>({type:'session.banner',session_id:id,topics:[],turn_mode:'agent',turn_mode_label:'',phase:'S4'});
 chat.handleEvent(banner('a'));
 chat.handleEvent({type:'turn.start'});
 chat.handleEvent({type:'confirm.request',request_id:'ordinary',preview:'ordinary',allow_approve_all:false});
 const before=[chat.model.turnActive,chat.model.turnCounter,chat.model.confirmPending,chat.model.confirmOverlay?.requestId];
 const result={type:'work-record.post-turn.result',session_id:'a',request_id:'synthetic-request',outcome:'recorded'};
 assert.equal(chat.handleEvent({...result,session_id:'b'}),false);
 assert.equal(chat.handleEvent(result),true);
 assert.equal(chat.handleEvent(result),false);
 assert.deepEqual([chat.model.turnActive,chat.model.turnCounter,chat.model.confirmPending,chat.model.confirmOverlay?.requestId],before);
 assert.equal(chat.model.blocks.filter(b=>b.kind==='notice').length,1);
 chat.handleEvent(banner('b'));
 assert.equal(chat.handleEvent(result),false);
 assert.equal(chat.handleEvent({...result,session_id:'b',outcome:'fake'}),false);
});
test('socket drops results after switch starts even before the next banner',async()=>{
 const window={setTimeout:()=>1,clearTimeout(){},myAgentDesktop:{}};
 class Socket { static OPEN=1;static instances=[];readyState=0;handlers=new Map();constructor(){Socket.instances.push(this);queueMicrotask(()=>{this.readyState=1;this.emit('open',{});});} addEventListener(t,f){this.handlers.set(t,[...(this.handlers.get(t)??[]),f]);} emit(t,e){for(const f of this.handlers.get(t)??[]) f(e);} send(){} message(v){this.emit('message',{data:JSON.stringify(v)});} close(){this.readyState=3;this.emit('close',{});} }
 const modules=new Map();function loadSocket(url){if(modules.has(url.href))return modules.get(url.href);const exports={};modules.set(url.href,exports);const js=ts.transpileModule(readFileSync(url,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;new Function('exports','require','window','WebSocket',js)(exports,id=>loadSocket(new URL(id.endsWith('.ts')?id:`${id}.ts`,url)),window,Socket);return exports;}
 const Client=loadSocket(new URL('../src/api/ws.ts',import.meta.url)).AgentWsClient;
 const client=new Client('127.0.0.1',1234);await client.connect();const socket=Socket.instances[0],events=[];client.onEvent(e=>events.push(e));
 socket.message({type:'session.banner',session_id:'a'});
 const result={type:'work-record.post-turn.result',session_id:'a',request_id:'one',outcome:'recorded'};
 socket.message(result);assert.equal(events.filter(e=>e.type===result.type).length,1);
 await client.openSession('b');socket.message(result);
 socket.message({type:'session.banner',session_id:'b'});socket.message(result);
 assert.equal(events.filter(e=>e.type===result.type).length,1);client.destroy();
});
