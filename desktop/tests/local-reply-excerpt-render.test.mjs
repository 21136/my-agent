import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
const require=createRequire(import.meta.url),ts=require('typescript');
const browserPath=process.env.MY_AGENT_PLAYWRIGHT_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const playwrightPath=path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const js=ts.transpileModule(readFileSync(new URL('../src/shells/unified/work-card-presentation.ts',import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText;
test('isolated browser: trusted expansion shows only literal ephemeral text, close removes it', {skip:!existsSync(browserPath)||!existsSync(playwrightPath)},async()=>{
 const {chromium}=require(playwrightPath);const browser=await chromium.launch({executablePath:browserPath,headless:true,args:['--disable-background-networking','--no-first-run']});
 try{const page=await browser.newPage({viewport:{width:1000,height:700}});await page.route('**/*',route=>route.abort());
 await page.setContent('<main id="chat"><article data-work-card-snapshot data-session-id="s" data-presentation-id="p" data-card-id="c"><details data-work-card-result-detail data-requirement-id="r" data-result-id="x"><summary>查看结果</summary></details></article></main><textarea aria-label="输入"></textarea>');
 await page.evaluate(source=>{const exports={};new Function('exports','require',source)(exports,()=>({}));const entry={sessionId:'s',presentationId:'p',cardId:'c',status:'resolved',card:{snapshot:{kind:'independent',card:{item_id:'i',requirements:[{requirement_id:'r',latest_result:{result_id:'x'}}]}}}};const view=new exports.WorkCardResultExcerptView();const chat=document.querySelector('#chat');window.testData={entry,view,sent:[]};exports.bindWorkCardResultViewEvents(chat,{currentSessionId:()=> 's',getEntry:()=>entry,send:r=>{r.request_id=['11111111-1111-4111-8111-111111111111','22222222-2222-4222-8222-222222222222'][window.testData.sent.length];window.testData.sent.push(r)},onExpanded:(...args)=>view.opened(...args),onClosed:d=>view.closed(d)});},js);
 await page.locator('summary').click();await page.waitForFunction(()=>window.testData.sent.length===1);
 const accepted=await page.evaluate(()=>{const {view,entry}=window.testData;return view.accept({type:'work-card.result.view.done',status:'acknowledged',message:'ok',request_id:window.testData.sent[0].request_id,session_id:'s',presentation_id:'p',card_id:'c',requirement_id:'r',result_id:'x',local_assistant_reply_excerpt_detail:{session_id:'s',item_id:'i',requirement_id:'r',result_id:'x',label:'本地摘录',escaped_excerpt_text:'\n&lt;img src=x onerror=alert(1)&gt; &amp;lt;script&amp;gt;',source_availability:'unavailable',result_status:'unknown',verification_status:'not_verified'}},'s',entry)});
 assert.equal(accepted,true);assert.equal(await page.locator('[data-local-reply-excerpt] pre').textContent(),'\n<img src=x onerror=alert(1)> &lt;script&gt;');assert.equal(await page.locator('[data-local-reply-excerpt] img').count(),0);await page.locator('textarea').fill('可输入');assert.equal(await page.locator('textarea').inputValue(),'可输入');await page.locator('summary').click();assert.equal(await page.locator('[data-local-reply-excerpt]').count(),0);
 await page.locator('summary').click();await page.waitForFunction(()=>window.testData.sent.length===2);
 const retried=await page.evaluate(()=>{const {view,entry,sent}=window.testData;const ev={type:'work-card.result.view.done',status:'acknowledged',message:'ok',request_id:sent[0].request_id,session_id:'s',presentation_id:'p',card_id:'c',requirement_id:'r',result_id:'x',local_assistant_reply_excerpt_detail:{session_id:'s',item_id:'i',requirement_id:'r',result_id:'x',label:'本地摘录',escaped_excerpt_text:'再次查看 &lt;安全&gt;',source_availability:'unavailable',result_status:'unknown',verification_status:'not_verified'}};return [sent[0].request_id!==sent[1].request_id,view.accept(ev,'s',entry),view.accept({...ev,request_id:sent[1].request_id},'s',entry)]});assert.deepEqual(retried,[true,false,true]);assert.equal(await page.locator('[data-local-reply-excerpt] pre').textContent(),'再次查看 <安全>');
 }finally{await browser.close()}
});
