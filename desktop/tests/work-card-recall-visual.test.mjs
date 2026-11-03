import assert from 'node:assert/strict';
import test from 'node:test';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {mkdtempSync,mkdirSync,readFileSync,writeFileSync,existsSync,rmSync,realpathSync,lstatSync,readdirSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
import {randomUUID} from 'node:crypto';
const ts=createRequire(import.meta.url)('typescript');
const base=path.resolve(import.meta.dirname,'../src/shells/unified');
const source=readFileSync(path.join(base,'index.ts'),'utf8');
function section(a,b){const i=source.indexOf(a),j=source.indexOf(b,i);assert.ok(i>=0&&j>i);return source.slice(i,j);}
const night=section('  function renderConfirmGlass(): void {','  // ---- input events ----');
const handlers=section('  // ---- confirm keyboard shortcuts ----','  // ---- project sidebar events ----');
const js=ts.transpileModule(readFileSync(path.join(base,'confirm-preview.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022}}).outputText;
const script=ts.transpileModule(`
import {renderConfirmCardHtml,summarizeConfirmPreview,isWorkCardRecallPreview,isConfirmChoiceAllowed,renderWorkCardRecallReadable,renderWorkCardRecallBatch} from '/preview.js';
const system='You are a read-only semantic recognizer. Compare the question directly against EVERY candidate supplied; the list is complete and must not be searched, reordered, shortened, or prefiltered. Return exactly one JSON object with exactly keys status and candidate_ids. Use match with exactly one candidate ID, ambiguous with two or more plausible IDs, or not_found with an empty list. Copy IDs verbatim from the supplied candidates. Do not invent IDs or add explanations. Never call tools, alter records, or infer/access any other session.';
const batchSystem='You are a read-only semantic recognizer. Compare the question with EVERY candidate in this batch only. Other batches may exist; do not infer their contents. Return exactly one JSON object with exactly keys status and candidate_ids. Use match with exactly one candidate ID, ambiguous with two or more plausible IDs, or not_found with an empty list. Copy IDs verbatim from this batch. Do not invent IDs, explain, call tools, alter records, or infer/access any other session.';
const batchMode=new URL(location.href).searchParams.get('batch')==='1';
const candidates=Array.from({length:batchMode?999:24},(_,i)=>({id:'wc-'+i.toString(16).padStart(64,'0'),title:'合成卡片标题 '+i+(i===0?'长中英文'.repeat(35)+'<img src=x onerror=alert(1)>':''),excerpt:JSON.stringify({scope:'independent',items:[{title:'合成标题 '+i,requirements:['合成要求 '+i+'，仅用于隔离测试', (i===(batchMode?998:23))?'最后一条 <script>alert(1)</script>':'longEnglishWithoutSpaces'.repeat(8)]}]})}));
const body=(rows)=>({model:'gpt-6-luna',messages:[{role:'system',content:batchMode?batchSystem:system},{role:'user',content:JSON.stringify({question:'合成问题：上次事项是什么？',candidates:rows})}],temperature:0,stream:false,response_format:{type:'json_object'}});
const bodies=Array.from({length:Math.ceil(candidates.length/32)},(_,i)=>body(candidates.slice(i*32,(i+1)*32)));
const raw=JSON.stringify({model_id:'0x567-flash',vendor:'0x567',endpoint:'https://example.invalid/v1/chat/completions',session_id:'synthetic-session',...(batchMode?{provider_payloads:bodies}:{provider_payload:body(candidates)})});
const renderStart=performance.now();
let perspective=new URL(location.href).searchParams.get('mode');const chatEl=document.querySelector('#chat'),confirmGlass=document.querySelector('#glass'),input=document.querySelector('#input');let destroyed=false;
const sent=[]; const confirm={requestId:'synthetic',preview:raw,allowApproveAll:true};
const chat={model:{confirmPending:true,confirmOverlay:perspective==='night'?confirm:null,blocks:[{kind:'confirm',...confirm}]},submitConfirm(){return true;}};
const client={sendConfirm(id,choice){sent.push(choice)}};function setStatus(s){document.querySelector('#status').textContent=s;}function syncWorkingVisual(){}function renderChat(){}function isConfirmInProgressLabel(){return false;}function escapeHtml(s){return s.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');}
${night}
${handlers}
if(perspective==='night'){document.body.classList.add('night');renderConfirmGlass();}else{chatEl.innerHTML=renderConfirmCardHtml(raw,'<button type="button" class="unified-btn unified-btn-accent" data-confirm="y" data-id="synthetic" disabled>允许此次</button><button type="button" class="unified-btn unified-btn-ghost" data-confirm="n" data-id="synthetic">拒绝</button><button type="button" class="unified-btn" data-confirm="a" data-id="synthetic" disabled>本会话均允许</button>');}
window.fixture={raw,sent,renderMs:performance.now()-renderStart};
`,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022}}).outputText;
const css=readFileSync(path.join(base,'unified.css'),'utf8');
const html=`<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/real.css"><style>
:root{--ma-mono:monospace;--ma-bg:#f6f3ec;--ma-surface:#fff;--ma-chrome-surface:#fff;--ma-text:#251f1a;--ma-text-muted:#51483e;--ma-accent:#a84414;--ma-border:#b7a999}body{font-family:system-ui;background:var(--ma-bg);color:var(--ma-text);margin:0;padding:2rem}body.night{--ma-bg:#171421;--ma-surface:#292338;--ma-text:#f5efff;--ma-text-muted:#ded3ec;--ma-accent:#d79bff;--ma-border:#655678}#chat{max-width:650px}.hidden{display:none!important}#glass{position:fixed}#status{margin:1rem}</style><div id="chat"></div><div id="glass" class="unified-confirm-glass hidden"></div><input id="input" hidden><div id="status"></div><script type="module" src="/test.js"></script>`;
// Refuse cleanup if any component or child is a link/reparse-point redirect.
// The expected real path is captured immediately after this test's mkdtemp.
const samePath=(a,b)=>process.platform==='win32'?path.resolve(a).toLowerCase()===path.resolve(b).toLowerCase():path.resolve(a)===path.resolve(b);
function hasNoLinkComponents(absolute){
 const parsed=path.parse(absolute); let current=parsed.root;
 for(const part of absolute.slice(parsed.root.length).split(path.sep).filter(Boolean)){
  current=path.join(current,part);const info=lstatSync(current);
  if(info.isSymbolicLink() || !samePath(realpathSync.native(current),current))return false;
 }
 return true;
}
function safeFixtureForRemoval(root,temp,expectedReal){
 try{
  const absolute=path.resolve(root), tempAbsolute=path.resolve(temp), name=path.basename(absolute);
  if(!/^work-card-visual-[A-Za-z0-9]+$/.test(name)||!samePath(path.dirname(absolute),tempAbsolute))return false;
  if(!hasNoLinkComponents(tempAbsolute)||!hasNoLinkComponents(absolute))return false;
  const realTemp=realpathSync.native(tempAbsolute), realRoot=realpathSync.native(absolute);
  if(!samePath(realRoot,expectedReal)||!samePath(path.dirname(realRoot),realTemp))return false;
  const entries=[absolute];
  while(entries.length){
   const current=entries.pop();const st=lstatSync(current);
   if(st.isSymbolicLink()||!samePath(realpathSync.native(current),current))return false;
   if(st.isDirectory())for(const child of readdirSync(current))entries.push(path.join(current,child));
  }
  return samePath(realpathSync.native(absolute),expectedReal);
 }catch{return false;}
}
function sleep(ms){return new Promise(r=>setTimeout(r,ms));}
async function cdp(port){const list=await (await fetch(`http://127.0.0.1:${port}/json`)).json();const ws=new WebSocket(list.find(x=>x.type==='page').webSocketDebuggerUrl);await new Promise((r,j)=>{ws.addEventListener('open',r,{once:true});ws.addEventListener('error',j,{once:true});});let seq=0;const pending=new Map();ws.addEventListener('message',e=>{const v=JSON.parse(e.data);if(v.id&&pending.has(v.id)){const p=pending.get(v.id);pending.delete(v.id);v.error?p.reject(Error(v.error.message)):p.resolve(v.result);}});return {ws,call(method,params={}){return new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});}};}

test('isolated Edge renders actual confirmation module and handlers',{timeout:45000},async()=>{
 const temp=path.resolve(tmpdir());
 const root=mkdtempSync(path.join(temp,'work-card-visual-'));
 const expectedReal=realpathSync.native(root);
 const evidenceId=randomUUID();
 // Screenshots are validated in memory by default. Set WORK_CARD_VISUAL_SAVE=1
 // explicitly to persist uniquely named synthetic evidence beside this test.
 const saveShot=(suffix,data)=>{
  const png=Buffer.from(data,'base64');
  assert.ok(png.length>8 && png.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])), 'browser screenshot must be a PNG');
  if(process.env.WORK_CARD_VISUAL_SAVE!=='1')return;
  const file=path.resolve(import.meta.dirname,`work-card-recall-visual.${evidenceId}.${suffix}.png`);
  writeFileSync(file,png,{flag:'wx'});console.log('visual evidence:',file);
 };
 const profile=path.join(root,'profile'), envroot=path.join(root,'home'); mkdirSync(profile);mkdirSync(envroot);
 const http=createServer((req,res)=>{const table={'/': ['text/html',html],'/preview.js':['text/javascript',js],'/test.js':['text/javascript',script],'/real.css':['text/css',css]};const v=table[new URL(req.url,'http://localhost').pathname];res.writeHead(v?200:404,{'Content-Type':v?.[0]||'text/plain'});res.end(v?.[1]||'');});
 await new Promise(r=>http.listen(0,'127.0.0.1',r));const port=http.address().port;
 const chrome=spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--disable-extensions','--disable-background-networking','--disable-sync','--disable-component-update','--disable-default-apps','--disable-features=MediaRouter,OptimizationHints','--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1','--remote-debugging-address=127.0.0.1','--remote-debugging-port=0',`--user-data-dir=${profile}`,`http://127.0.0.1:${port}/?mode=day`],{cwd:root,env:{SYSTEMROOT:process.env.SYSTEMROOT,WINDIR:process.env.WINDIR,PATH:process.env.PATH,TEMP:root,TMP:root,HOME:envroot,USERPROFILE:envroot,APPDATA:envroot,LOCALAPPDATA:envroot},stdio:['ignore','ignore','pipe']});
 let chromeError='';chrome.stderr.on('data',chunk=>{chromeError+=String(chunk).slice(0,3000);});
 let browser;
 try{
  for(let i=0;i<120&&!existsSync(path.join(profile,'DevToolsActivePort'));i++)await sleep(100);
  assert.ok(existsSync(path.join(profile,'DevToolsActivePort')),'isolated Edge did not start: '+chromeError);const debugPort=Number(readFileSync(path.join(profile,'DevToolsActivePort'),'utf8').split('\n')[0]);browser=await cdp(debugPort);await browser.call('Page.enable');await browser.call('Runtime.enable');await browser.call('Page.addScriptToEvaluateOnNewDocument',{source:'window.__fixtureErrors=[];window.addEventListener(\"error\",e=>window.__fixtureErrors.push(e.message));window.addEventListener(\"unhandledrejection\",e=>window.__fixtureErrors.push(String(e.reason)));'});await browser.call('Emulation.setDeviceMetricsOverride',{width:1000,height:700,deviceScaleFactor:1,mobile:false});
  async function evalJS(expression){const result=await browser.call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw new Error(result.exceptionDetails.text);return result.result.value;}
  for(const [mode,batch] of [['day',false],['night',false],['day',true],['night',true]]){
   await browser.call('Page.navigate',{url:`http://127.0.0.1:${port}/?mode=${mode}&batch=${batch?1:0}`});let ready=false;for(let i=0;i<100;i++){if(await evalJS('!!window.fixture')){ready=true;break;}await sleep(100);}assert.ok(ready,mode+' page did not load: '+JSON.stringify(await evalJS('({errors:window.__fixtureErrors,href:location.href,body:document.body?.innerText})')));
   assert.deepEqual(await evalJS(`(()=>{const d=document.querySelector('[data-work-card-recall]');return {found:!!d,disabled:d.querySelector('[data-confirm="y"]').disabled,reject:d.querySelector('[data-confirm="n"]').disabled,open:d.querySelector('details').open,text:d.innerText}})()`),{found:true,disabled:true,reject:false,open:false,text:await evalJS(`document.querySelector('[data-work-card-recall]').innerText`)});
   assert.match(await evalJS(`document.querySelector('[data-work-card-recall]').innerText`),/当前会话问题、卡片标题和要求到 0x567/);
   const closedShot=await browser.call('Page.captureScreenshot',{format:'png'});saveShot(`${mode}${batch?'-batch':''}-closed`,closedShot.data);
   await evalJS(`document.dispatchEvent(new KeyboardEvent('keydown',{key:'y',bubbles:true}));document.querySelector('[data-confirm="y"]').click();`);assert.deepEqual(await evalJS('fixture.sent'),[]);
   await evalJS(`document.querySelector('[data-confirm="n"]').click()`);assert.deepEqual(await evalJS('fixture.sent'),['n']);await evalJS(`document.querySelector('#status').textContent=''`);
   await evalJS(`document.querySelector('details summary').click()`);await sleep(150);
   assert.equal(await evalJS(`document.querySelector('pre').textContent === fixture.raw`),true);assert.equal(await evalJS(`document.querySelector('[data-confirm="y"]').disabled`),false);
   if(batch){
    assert.equal(await evalJS(`document.querySelectorAll('.unified-recall-candidate').length`),0);
    assert.equal(await evalJS(`document.querySelectorAll('.unified-recall-batch').length`),32);
    const stats=await evalJS(`({renderMs:fixture.renderMs,nodes:document.querySelectorAll('*').length,bytes:new TextEncoder().encode(fixture.raw).length})`);
    console.log('synthetic 999-card batch',mode,JSON.stringify(stats));
    const started=Date.now();await evalJS(`document.querySelectorAll('.unified-recall-batch').forEach(d=>d.open=true)`);
    let loaded=false;for(let i=0;i<200;i++){if(await evalJS(`document.querySelectorAll('.unified-recall-batch[data-loaded="true"]').length===32`)){loaded=true;break;}await sleep(50);}assert.ok(loaded,'all batches should be inspectable');
    assert.equal(await evalJS(`document.querySelectorAll('.unified-recall-candidate').length`),999);
    console.log('synthetic all-batches expansion',mode,Date.now()-started,'ms');
   }
   assert.equal(await evalJS(`(()=>{const r=JSON.parse(fixture.raw),b=r.provider_payloads||[r.provider_payload],spans=[...document.querySelectorAll('.unified-recall-value')].map(e=>e.textContent);const expected=[JSON.stringify(r.endpoint)];for(const body of b){const q=JSON.parse(body.messages[1].content);expected.push(JSON.stringify(q.question));for(const c of q.candidates){const x=JSON.parse(c.excerpt);expected.push(JSON.stringify(c.title),JSON.stringify(c.id),JSON.stringify(x.scope));for(const item of x.items){expected.push(JSON.stringify(item.title));for(const t of item.requirements)expected.push(JSON.stringify(t));}}}return JSON.stringify(spans)===JSON.stringify(expected)})()`),true);
   assert.equal(await evalJS(`document.querySelector('.unified-recall-readable img, .unified-recall-readable script') === null`),true);
   assert.equal(await evalJS(`(()=>{const r=document.querySelector('.unified-recall-scroll'),v=document.querySelector('.unified-recall-value');return r.scrollHeight>r.clientHeight && getComputedStyle(r).overflowY==='auto' && getComputedStyle(v).overflowWrap==='anywhere' && r.scrollWidth<=r.clientWidth+1})()`),true);

   assert.equal(await evalJS(`(()=>{let p=document.querySelector('pre');return p.scrollWidth>p.clientWidth && getComputedStyle(p).overflowX==='auto' && getComputedStyle(p).whiteSpace==='pre'})()`),true);
   await evalJS(`(()=>{let p=document.querySelector('pre');p.scrollLeft=p.scrollWidth;return p.scrollLeft})()`);assert.ok(await evalJS('document.querySelector("pre").scrollLeft')>0);
   await evalJS(`document.querySelector('pre').scrollLeft=0`);
   const shot=await browser.call('Page.captureScreenshot',{format:'png'});saveShot(`${mode}${batch?'-batch':''}`,shot.data);
   await evalJS(`document.dispatchEvent(new KeyboardEvent('keydown',{key:'y',bubbles:true}));document.querySelector('[data-confirm="y"]').click()`);assert.deepEqual(await evalJS('fixture.sent'),['n','y','y']);
  }
 }finally{if(browser){try{await browser.call('Browser.close');}catch{}browser.ws.close();}http.closeAllConnections();http.close();if(chrome.exitCode===null&&chrome.signalCode===null){chrome.kill();await Promise.race([new Promise(r=>chrome.once('exit',r)),sleep(3000)]);}if(chrome.exitCode!==null||chrome.signalCode!==null){if(safeFixtureForRemoval(root,temp,expectedReal))rmSync(root,{recursive:true,force:true,maxRetries:10,retryDelay:200});else console.error('fixture path verification failed; profile retained:',root);}else console.error('isolated browser still running; profile retained:',root);}
});
