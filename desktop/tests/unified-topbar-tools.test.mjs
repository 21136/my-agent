import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
const ts = createRequire(import.meta.url)('typescript');
const cache = new Map();
function load(url) { if (cache.has(url.href)) return cache.get(url.href); const out = {}; cache.set(url.href,out); const js=ts.transpileModule(readFileSync(url,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText; new Function('exports','require',js)(out,id=>load(new URL(id.endsWith('.ts')?id:`${id}.ts`,url))); return out; }
const {renderTopbarV2} = load(new URL('../src/shells/unified/topbar.ts',import.meta.url));
test('existing topbar menu exposes retained tools and dispatches each exact selection',()=>{
 const events=new Map();const el={innerHTML:'',classList:{add(){},remove(){}},querySelector(s){return s==='.unified-topbar-menu'?null:{addEventListener:(t,f)=>events.set(`${s}:${t}`,f)}},querySelectorAll(){return []}};
 const selected=[];const state={proposals:[],projectLabel:'',contextLabel:'',headerCtas:[],memoryCount:0,pendingMemoryCount:0,sessionCount:0};
 renderTopbarV2(el,state,{onSelectTool:t=>selected.push(t)});
 for(const [id,label] of [['now','返回对话'],['docs','文档'],['settings','设置'],['music','听歌'],['shell-dev','双车道'],['runtime','终端与服务']]) {assert.match(el.innerHTML,new RegExp(`id="unified-tool-${id}"[^>]*>${label}`));events.get(`#unified-tool-${id}:click`)();}
 assert.deepEqual(selected,['now','docs','settings','music','shell-dev','runtime']);
 assert.doesNotMatch(el.innerHTML,/unified-new-chat|unified-new-thread|unified-open-sessions|unified-new-project/);
});
