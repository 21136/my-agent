import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync,realpathSync,existsSync,lstatSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {spawn,spawnSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {createRequire} from 'node:module';
import {randomUUID} from 'node:crypto';
const require=createRequire(import.meta.url);
const esbuild=require('esbuild');
const playwright=require('C:/Users/21136/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const core=path.resolve(import.meta.dirname,'../../agent-core');
const python=path.resolve(import.meta.dirname,'../../../../.venv/Scripts/python.exe');
const chrome='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const within=(root,target)=>{const rel=path.relative(root,target);return !!rel&&rel!=='..'&&!rel.startsWith('..'+path.sep)&&!path.isAbsolute(rel)};

test('isolated browser explicit linked snapshot view records evidence, receipt alone does not', {timeout:60000,skip:process.env.RUN_ISOLATED_SNAPSHOT_BROWSER !== "1"}, async t=>{
  assert.ok(existsSync(python)&&existsSync(chrome),'trusted Python/browser runtime required');
  const tmp=realpathSync.native(tmpdir()), root=mkdtempSync(path.join(tmp,'linked-snapshot-dom-'));
  const canonical=realpathSync.native(root);assert.ok(within(tmp,canonical));assert.ok(!lstatSync(root).isSymbolicLink());
  t.diagnostic(`synthetic fixture retained: ${canonical}`);
  for(const name of ['workspace','data','evolve','temp','home','user','roaming','local','browser']) mkdirSync(path.join(root,name));
  writeFileSync(path.join(root,'evolve','_index.core.toml'),'[core]\n',{flag:'wx'});
  writeFileSync(path.join(root,'evolve','_index.toml'),'# synthetic\n',{flag:'wx'});
  writeFileSync(path.join(root,'.synthetic-test-owner'),randomUUID(),{flag:'wx'});
  const env={};for(const k of ['SYSTEMROOT','WINDIR','PATH','PATHEXT'])if(process.env[k])env[k]=process.env[k];
  Object.assign(env,{MY_AGENT_ROOT:canonical,PYTHONDONTWRITEBYTECODE:'1',PYTHONNOUSERSITE:'1',PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8',TEMP:path.join(root,'temp'),TMP:path.join(root,'temp'),HOME:path.join(root,'home'),USERPROFILE:path.join(root,'user'),APPDATA:path.join(root,'roaming'),LOCALAPPDATA:path.join(root,'local')});
  assert.ok(!Object.keys(env).some(k=>/API.?KEY|TOKEN|SECRET|CREDENTIAL/i.test(k)));
  const probe=spawnSync(python,['-I','-B','-c',`import sys,site,pathlib,os; sys.path.insert(0,sys.argv[2]); from paths import AgentPaths; import pytest, websockets, server; p=AgentPaths.discover(); r=pathlib.Path(sys.argv[1]).resolve(); assert p.agent_root==r and p.workspace==r/'workspace' and p.data==r/'data' and p.evolve==r/'evolve'; assert str(site.getusersitepackages()) not in sys.path; print(sys.executable)`,canonical,core],{cwd:core,env,encoding:'utf8',windowsHide:true,timeout:20000});
  assert.equal(probe.status,0,`preflight failed before service: ${probe.stderr}`);assert.equal(path.resolve(probe.stdout.trim()),path.resolve(python));
  let child,context,browser,edge,stderr='',edgeError='';
  try {
    child=spawn(python,['-I','-B',path.resolve(import.meta.dirname,'fixtures/linked-snapshot-browser-bridge.py'),canonical,core],{cwd:core,env,stdio:['pipe','pipe','pipe'],windowsHide:true});
    child.stderr.on('data',x=>stderr+=String(x));const lines=createInterface({input:child.stdout});
    const start=await Promise.race([new Promise((resolve,reject)=>{lines.once('line',line=>{try{resolve(JSON.parse(line))}catch{reject(new Error('invalid fixture startup'))}});child.once('exit',code=>reject(new Error(`fixture exited ${code}: ${stderr}`)));}),new Promise((_,reject)=>setTimeout(()=>reject(new Error('fixture startup timeout')),15000))]);
    assert.equal(path.resolve(start.root),canonical);for(const p of [start.receipt_path,start.evidence_path])assert.ok(within(canonical,path.resolve(p)));
    const bundle=await esbuild.build({stdin:{resolveDir:path.resolve(import.meta.dirname,'../src'),sourcefile:'synthetic-browser-entry.ts',contents:`
      import { AgentWsClient } from './api/ws.ts';
      import { WorkCardPresentationRegistry,receiveWorkCardPresentation,renderWorkCardEntry,bindWorkCardSnapshotViewEvents } from './shells/unified/work-card-presentation.ts';
      const client=new AgentWsClient('127.0.0.1',${start.port}); const registry=new WorkCardPresentationRegistry(${JSON.stringify(start.session)}); const chat=document.querySelector('#chat');
      window.testState={events:[],trusted:[]};chat.addEventListener('click',e=>{if(e.target.closest('summary')?.parentElement?.hasAttribute('data-work-card-snapshot-detail'))window.testState.trusted.push(e.isTrusted)},true);
      bindWorkCardSnapshotViewEvents(chat,{currentSessionId:()=>${JSON.stringify(start.session)},getEntry:id=>registry.get(id),send:r=>client.acknowledgeWorkCardSnapshotView({sessionId:r.session_id,presentationId:r.presentation_id,cardId:r.card_id}),onError:()=>window.testState.events.push({type:'send-error'})});
      client.onEvent(event=>{window.testState.events.push(event);if(event.type==='work-card.present'){const x=receiveWorkCardPresentation({registry,event,mergeReference:()=>{},sendReceived:id=>client.acknowledgeWorkCardReceived(id)});if(x.mutation.entry)chat.innerHTML=renderWorkCardEntry(x.mutation.entry);}});client.connect();`},bundle:true,platform:'browser',format:'iife',write:false});
    // Mirror the established Edge fixture: launch a dedicated process with its
    // own temp profile and discover only that profile's loopback DevTools port.
    const profile=realpathSync.native(path.join(root,'browser'));
    assert.ok(within(canonical,profile) && !lstatSync(profile).isSymbolicLink());
    const edgeArgs=['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--disable-extensions','--disable-background-networking','--disable-sync','--disable-component-update','--disable-default-apps','--disable-features=MediaRouter,OptimizationHints','--no-proxy-server','--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1','--remote-debugging-address=127.0.0.1','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'];
    edge=spawn(chrome,edgeArgs,{cwd:canonical,env,stdio:['ignore','ignore','pipe'],windowsHide:true,shell:false});
    edge.stderr.on('data',chunk=>{edgeError+=String(chunk).slice(0,4000)});
    const portFile=path.join(profile,'DevToolsActivePort');
    for(let i=0;i<150 && !existsSync(portFile) && edge.exitCode===null;i++) await new Promise(r=>setTimeout(r,100));
    assert.ok(edge.exitCode===null && existsSync(portFile),`isolated Edge failed: ${edgeError}`);
    assert.ok(!lstatSync(portFile).isSymbolicLink() && within(profile,realpathSync.native(portFile)),'DevTools port file must be inside owned profile');
    const [portText,wsPath]=readFileSync(portFile,'utf8').trim().split(/\r?\n/);
    const port=Number(portText);assert.ok(Number.isInteger(port)&&port>0&&port<65536&&/^\/devtools\/browser\/[a-z0-9-]+$/i.test(wsPath));
    browser=await playwright.chromium.connectOverCDP(`http://127.0.0.1:${port}`,{timeout:10000});
    t.diagnostic(`isolated Edge ${browser.version()}; profile ${profile}; loopback DevTools ${port}; backend ${start.port}`);
    context=browser.contexts()[0]; assert.ok(context,'owned Edge context required');
    await context.routeWebSocket('**/*',ws=>{if(ws.url()===`ws://127.0.0.1:${start.port}` || ws.url()===`ws://127.0.0.1:${start.port}/`)ws.connectToServer();else ws.close();});
    const page=await context.newPage(); const browserErrors=[]; page.on('pageerror',e=>browserErrors.push(e.message)); page.on('console',msg=>browserErrors.push(msg.text())); await page.route('**/*',route=>route.abort());
    await page.setContent('<!doctype html><html><body><main id="chat"></main></body></html>');await page.addScriptTag({content:bundle.outputFiles[0].text});
    await page.waitForFunction(()=>window.testState.events.some(e=>e.type==='work-card.received.done'&&e.status==='received'),null,{timeout:12000}).catch(async error=>{throw new Error('receipt timeout: browser='+JSON.stringify(browserErrors)+' state='+JSON.stringify(await page.evaluate(()=>window.testState))+' server='+stderr+' cause='+error.message)});
    const receipt=JSON.parse(readFileSync(start.receipt_path,'utf8')).items.find(x=>x.presentation_id===start.presentation_id);
    assert.ok(receipt.received_at);assert.equal(receipt.acknowledged_at,null);
    assert.equal(existsSync(start.evidence_path),false,'receipt alone must not write view evidence');
    assert.equal(await page.locator('[data-work-card-snapshot-detail]').count(),1);
    // DOM .click() is not a trusted user gesture and must not record a view.
    await page.evaluate(()=>document.querySelector('[data-work-card-snapshot-detail] > summary').click());
    await new Promise(r=>setTimeout(r,200));
    assert.equal(existsSync(start.evidence_path),false,'untrusted script click must not write evidence');
    assert.equal((await page.evaluate(()=>window.testState.events)).filter(e=>e.type==='work-card.snapshot.view.done').length,0);
    await page.evaluate(()=>{document.querySelector('[data-work-card-snapshot-detail]').open=false});
    await page.locator('[data-work-card-snapshot-detail] > summary').click();
    await page.waitForFunction(()=>window.testState.events.some(e=>e.type==='work-card.snapshot.view.done'),null,{timeout:12000});
    const state=await page.evaluate(()=>window.testState);assert.deepEqual(state.trusted,[false,true]);
    const result=state.events.find(e=>e.type==='work-card.snapshot.view.done');assert.equal(result.status,'acknowledged');
    const evidence=JSON.parse(readFileSync(start.evidence_path,'utf8'));assert.ok(evidence.items.some(x=>x.presentation_id===start.presentation_id&&x.card_id===start.card_id&&x.evidence&&x.evidence.ack_boot_id&&x.evidence.ack_wall_utc));
  } finally {
    await browser?.close();
    if(edge&&edge.exitCode===null){edge.kill();await Promise.race([new Promise(r=>edge.once('exit',r)),new Promise(r=>setTimeout(r,4000))]);}
    if(child&&child.exitCode===null){child.stdin.end('stop\n');await Promise.race([new Promise(r=>child.once('exit',r)),new Promise(r=>setTimeout(r,4000))]);if(child.exitCode===null)child.kill();}
  }
});
