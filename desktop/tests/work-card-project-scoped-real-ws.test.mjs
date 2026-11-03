import assert from 'node:assert/strict';
import test from 'node:test';
import {mkdtempSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {createInterface} from 'node:readline';
import {randomUUID} from 'node:crypto';

const python = 'F:/my-agent-main/.venv/Scripts/python.exe';
const core = path.resolve(import.meta.dirname, '../../agent-core');
const ts = createRequire(import.meta.url)('typescript');
const serverCode = String.raw`
import sys,json,pathlib,uuid,hashlib
root=pathlib.Path(sys.argv[1]).resolve(); core=pathlib.Path(sys.argv[2]).resolve()
sys.path.insert(0,str(core))
from paths import AgentPaths
from session import create_new
from work_records import WorkRecordStore
from work_card_project_scope import build_project_scoped_work_record_cards, project_scoped_snapshot_request_id
from work_card_ledger import WorkCardLedger
from work_card_presentations import WorkCardPresentations
from server import WsSessionHandler, _work_card_presentation_id
from websockets.asyncio.server import serve
paths=AgentPaths(agent_root=root,workspace=root/'workspace',data=root/'data',evolve=root/'evolve')
paths.workspace.mkdir(parents=True); paths.evolve.mkdir()
session=create_new(paths,conversation_id='20261030-abcdef12')
store=WorkRecordStore(session)
item=store.create_item(title='Synthetic project pair',scope={'kind':'independent'})
project_id='synthetic-alpha'
requirement=store.add_requirement(item['item_id'],'Alpha-only requirement',event_id='synthetic-alpha-event',source_ref={'kind':'project','id':project_id})
second=store.add_requirement(item['item_id'],'Alpha second requirement',event_id='synthetic-alpha-second',source_ref={'kind':'project','id':project_id})
running=store.add_requirement(item['item_id'],'Alpha running requirement',event_id='synthetic-alpha-running',source_ref={'kind':'project','id':project_id})
history=store.add_requirement(item['item_id'],'Alpha rerun requirement',event_id='synthetic-alpha-rerun',source_ref={'kind':'project','id':project_id})
store.add_requirement(item['item_id'],'Beta-only requirement',event_id='synthetic-beta-event',source_ref={'kind':'project','id':'synthetic-beta'})
req_id=requirement['requirement_id']; run_id='synthetic-worker-run'
task_id='task-'+hashlib.sha256('\0'.join((session.conversation_id,item['item_id'],req_id,project_id)).encode()).hexdigest()
store.begin_requirement_run(item['item_id'],req_id,run_id=run_id,expected_current_run_id=None)
receipt={'kind':'project_worker_host_lifecycle','result_status':'unknown','project_id':project_id,'task_id':task_id,
 'worker_id':'synthetic-worker','worker_run_id':run_id,'reservation_id':'synthetic-reservation',
 'parent_run_id':'c6b23429-fc35-4ded-aa0c-5873ab8f0a11','parent_epoch':3,'host_status':'model_turn_finished',
 'acceptance_status':'not_run','write_receipts_available':True,'write_receipts':[{'path':'docs/synthetic.md','status':'written',
 'verification':'content_match','characters_read_back':12,'error_code':'','readback_succeeded':True}]}
result_id='worker-result-'+hashlib.sha256('\0'.join((session.conversation_id,item['item_id'],req_id,run_id)).encode()).hexdigest()
store.record_result_if_current_run(item['item_id'],req_id,run_id=run_id,result_id=result_id,status='unknown',
 summary=json.dumps(receipt,sort_keys=True,separators=(',',':')),
 verification={'status':'not_verified','evidence_refs':[],'host_execution':{'sequence':3,'finish_reason':'completed'}},sources=[])
store.begin_requirement_run(item['item_id'],running['requirement_id'],run_id='running-without-history',expected_current_run_id=None)
history_id=history['requirement_id']; old_run='old-synthetic-run'
store.begin_requirement_run(item['item_id'],history_id,run_id=old_run,expected_current_run_id=None)
old_task='task-'+hashlib.sha256('\0'.join((session.conversation_id,item['item_id'],history_id,project_id)).encode()).hexdigest()
old_result='worker-result-'+hashlib.sha256('\0'.join((session.conversation_id,item['item_id'],history_id,old_run)).encode()).hexdigest()
store.record_result_if_current_run(item['item_id'],history_id,run_id=old_run,result_id=old_result,status='unknown',
 summary=json.dumps({**receipt,'task_id':old_task,'worker_run_id':old_run},sort_keys=True,separators=(',',':')),
 verification={'status':'not_verified','evidence_refs':[],'host_execution':{'sequence':3,'finish_reason':'completed'}},sources=[])
store.begin_requirement_run(item['item_id'],history_id,run_id='new-synthetic-run',expected_current_run_id=old_run)
ledger=WorkCardLedger(session,store)
beta=build_project_scoped_work_record_cards(store,'synthetic-beta')
assert beta.completeness=='complete'
beta_saved=ledger.save_project_scoped_snapshot(project_scoped_snapshot_request_id(beta),beta)
assert beta_saved.status=='saved'
snapshot=build_project_scoped_work_record_cards(store,project_id)
assert snapshot.completeness=='complete'
pending=next(r for card in snapshot.cards for r in card.requirements if r.requirement_id==second['requirement_id'])
assert pending.status=='pending' and pending.latest_result is None and pending.result_count==0
assert pending.current_run_id is None and pending.latest_result_is_current is None
running_snapshot=next(r for card in snapshot.cards for r in card.requirements if r.requirement_id==running['requirement_id'])
history_snapshot=next(r for card in snapshot.cards for r in card.requirements if r.requirement_id==history_id)
assert running_snapshot.status=='in_progress' and running_snapshot.latest_result is None and running_snapshot.result_count==0 and running_snapshot.latest_result_is_current is False
assert history_snapshot.status=='in_progress' and history_snapshot.latest_result is None and history_snapshot.result_count==1 and history_snapshot.omitted_result_count==1 and history_snapshot.latest_result_is_current is False
assert snapshot.history_complete is False and snapshot.omitted_result_count==1
saved=ledger.save_project_scoped_snapshot(project_scoped_snapshot_request_id(snapshot),snapshot)
assert saved.status=='saved'
parent_run_id='c6b23429-fc35-4ded-aa0c-5873ab8f0a11'
presentation_id=_work_card_presentation_id(session.conversation_id,parent_run_id,saved.card_id)
assert WorkCardPresentations(session,store,ledger).create(presentation_id,saved.card_id,run_id=parent_run_id).status=='created'
session.append_sent_work_card_reference(presentation_id,saved.card_id,run_id=parent_run_id)
async def main():
 async with serve(WsSessionHandler(paths).handle,'127.0.0.1',0) as socket:
  print(json.dumps({'port':socket.sockets[0].getsockname()[1],'session':session.conversation_id,
   'card_id':saved.card_id,'presentation_id':presentation_id,'requirement_id':req_id,'result_id':result_id,
   'fixture':'backend-generated-synthetic-project-scoped-card'}),flush=True)
  await asyncio.to_thread(sys.stdin.buffer.readline)
import asyncio
asyncio.run(main())
`;

function loadClient() {
  const cache = new Map();
  function load(url) {
    if (cache.has(url.href)) return cache.get(url.href);
    const exports = {};
    cache.set(url.href, exports);
    const js = ts.transpileModule(readFileSync(url, 'utf8'), {
      compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
    }).outputText;
    new Function('exports','require','window','WebSocket','crypto',js)(
      exports,
      id => { assert.ok(id.startsWith('.')); return load(new URL(id.endsWith('.ts') ? id : `${id}.ts`, url)); },
      {setTimeout,clearTimeout,myAgentDesktop:{}}, WebSocket, {randomUUID},
    );
    return exports;
  }
  return load(new URL('../src/api/ws.ts', import.meta.url)).AgentWsClient;
}

function waitFor(client, predicate, timeoutMs = 8000) {
  return new Promise((resolve,reject) => {
    const timer=setTimeout(()=>{off();reject(new Error('protocol timeout'));},timeoutMs);
    const off=client.onEvent(event=>{
      if (predicate(event)) { clearTimeout(timer); off(); resolve(event); }
    });
  });
}

test('backend-generated project_scoped snapshot replays through desktop WS validation and result-view protocol', {timeout:35000}, async () => {
  const root=mkdtempSync(path.join(tmpdir(),'project-scoped-present-wire-'));
  const env={SYSTEMROOT:process.env.SYSTEMROOT,WINDIR:process.env.WINDIR,PATH:process.env.PATH,TEMP:root,TMP:root,HOME:root,USERPROFILE:root,APPDATA:root,LOCALAPPDATA:root};
  const child=spawn(python,['-I','-B','-c',serverCode,root,core],{cwd:core,env,stdio:['pipe','pipe','pipe']});
  const lines=createInterface({input:child.stdout});
  let stderr=''; child.stderr.on('data',chunk=>{stderr+=String(chunk).slice(0,10000);});
  let client;
  try {
    let startupTimer;
    const startup=await Promise.race([
      new Promise((resolve,reject)=>{
        lines.once('line',line=>{try{resolve(JSON.parse(line));}catch{reject(new Error('invalid fixture startup JSON'));}});
        child.once('exit',code=>reject(new Error(`fixture exited ${code}: ${stderr}`)));
      }),
      new Promise((_,reject)=>{startupTimer=setTimeout(()=>reject(new Error('fixture startup timeout')),10000);}),
    ]).finally(()=>clearTimeout(startupTimer));
    assert.equal(startup.fixture,'backend-generated-synthetic-project-scoped-card');
    const Client=loadClient();
    client=new Client('127.0.0.1',startup.port);
    const banner=waitFor(client,event=>event.type==='session.banner');
    const present=waitFor(client,event=>event.type==='work-card.present');
    await client.connect();
    assert.equal((await banner).session_id,startup.session);
    const event=await present;
    assert.equal(event.session_id,startup.session);
    assert.equal(event.presentation_id,startup.presentation_id);
    assert.equal(event.card_id,startup.card_id);
    assert.equal(event.card.viewed_at,null,'delivery must not mark the card read');
    const snapshot=event.card.snapshot;
    assert.equal(snapshot.kind,'project_scoped');
    assert.equal(snapshot.project_id,'synthetic-alpha');
    assert.deepEqual(snapshot.cards.flatMap(item=>item.requirements.map(row=>row.text)),['Alpha-only requirement','Alpha second requirement','Alpha running requirement','Alpha rerun requirement']);
    assert.ok(snapshot.cards.every(item=>item.project_id==='synthetic-alpha'
      && item.requirements.every(row=>row.project_id==='synthetic-alpha')));
    const resultView=waitFor(client,result=>result.type==='work-card.result.view.done');
    client.acknowledgeWorkCardResultView({sessionId:startup.session,presentationId:startup.presentation_id,
      cardId:startup.card_id,resultId:startup.result_id,requirementId:startup.requirement_id});
    assert.equal((await resultView).status,'acknowledged');
  } finally {
    client?.destroy();
    if(child.exitCode===null){
      child.stdin.end('stop\n');
      let stopTimer;
      await Promise.race([new Promise(resolve=>child.once('exit',resolve)),new Promise(resolve=>{stopTimer=setTimeout(resolve,3000);})]).finally(()=>clearTimeout(stopTimer));
      if(child.exitCode===null){child.kill();throw new Error('fixture server did not stop; temporary directory retained');}
    }
    rmSync(root,{recursive:true,force:true,maxRetries:5,retryDelay:200});
  }
});


test('isolated backend project replay reaches mounted desktop chat without another project', {timeout:45000}, async () => {
  const require=createRequire(import.meta.url);
  const esbuild=require('esbuild');
  const playwright=require('C:/Users/21136/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
  const root=mkdtempSync(path.join(tmpdir(),'project-scoped-ui-wire-'));
  const env={SYSTEMROOT:process.env.SYSTEMROOT,WINDIR:process.env.WINDIR,PATH:process.env.PATH,TEMP:root,TMP:root,HOME:root,USERPROFILE:root,APPDATA:root,LOCALAPPDATA:root};
  const child=spawn(python,['-I','-B','-c',serverCode,root,core],{cwd:core,env,stdio:['pipe','pipe','pipe'],windowsHide:true});
  let stderr='',browser,context;child.stderr.on('data',v=>stderr+=String(v).slice(0,10000));
  try {
    const lines=createInterface({input:child.stdout});let timer;
    const startup=await Promise.race([
      new Promise((resolve,reject)=>{lines.once('line',line=>{try{resolve(JSON.parse(line))}catch{reject(new Error('invalid startup'))}});child.once('exit',code=>reject(new Error(`fixture exited ${code}: ${stderr}`)))}),
      new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('fixture startup timeout')),10000)})
    ]).finally(()=>clearTimeout(timer));
    const desktop=path.resolve(import.meta.dirname,'..');
    const bundle=await esbuild.build({stdin:{contents:'export {mountUnifiedShell} from "./src/shells/unified/index.ts"; export {AgentWsClient} from "./src/api/ws.ts";',resolveDir:desktop,loader:'ts'},bundle:true,write:false,platform:'browser',format:'iife',globalName:'UnderTest',define:{'import.meta.url':'"http://127.0.0.1/entry.js"'},loader:{'.png':'dataurl','.jpg':'dataurl','.svg':'dataurl','.woff2':'dataurl'},plugins:[{name:'no-css',setup(b){b.onLoad({filter:/\.css$/},()=>({contents:'',loader:'js'}))}}],logLevel:'silent'});
    browser=await playwright.chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',args:['--disable-background-networking','--disable-features=LocalNetworkAccessChecks,LocalNetworkAccessChecksWebSockets','--disable-extensions','--disable-sync','--disable-component-update','--no-proxy-server','--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1']});
    context=await browser.newContext({serviceWorkers:'block'});
    await context.routeWebSocket('**/*',ws=>{if(ws.url()===`ws://127.0.0.1:${startup.port}/`||ws.url()===`ws://127.0.0.1:${startup.port}`)ws.connectToServer();else ws.close()});
    const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/*',route=>route.request().url()==='http://127.0.0.1/' ? route.fulfill({status:200,contentType:'text/html',body:'<!doctype html><div id="root"></div>'}) : route.abort());
    await page.goto('http://127.0.0.1/');
    await page.addScriptTag({content:bundle.outputFiles[0].text});
    await page.evaluate(async port=>{
      window.myAgentDesktop={setBilibiliViewState:async()=>{},setShellDevViewState:async()=>{}};
      window.events=[];window.client=new UnderTest.AgentWsClient('127.0.0.1',port);
      client.onEvent(e=>events.push(e));
      window.disposeUnified=UnderTest.mountUnifiedShell(document.querySelector('#root'),client);
      await client.connect();
    },startup.port).catch(e=>{throw new Error(`${e.message} server: ${stderr}`)});
    await page.waitForFunction(()=>events.some(e=>e.type==='work-card.received.done'&&e.status==='received'),null,{timeout:12000}).catch(async e=>{throw new Error(`receipt timeout: ${JSON.stringify(await page.evaluate(()=>events))}; errors ${JSON.stringify(errors)}; server ${stderr}; ${e.message}`)});
    const events=await page.evaluate(()=>events);
    const offered=events.filter(e=>e.type==='work-card.present');
    assert.equal(offered.length,1);assert.equal(offered[0].card_id,startup.card_id);
    assert.equal(offered[0].card.viewed_at,null);
    const snapshot=offered[0].card.snapshot;
    assert.equal(snapshot.project_id,'synthetic-alpha');assert.equal(snapshot.completeness,'complete');
    const rows=snapshot.cards.flatMap(c=>c.requirements);
    assert.equal(rows[0].status,'unknown');assert.equal(rows[0].latest_result.verification_status,'not_verified');
    assert.equal(rows[1].status,'pending');assert.equal(rows[1].latest_result,null);assert.equal(rows[1].result_count,0);
    assert.equal(rows[1].current_run_id,null);assert.equal(rows[1].latest_result_is_current,null);
    assert.equal(rows[2].status,'in_progress');assert.equal(rows[2].latest_result,null);assert.equal(rows[2].result_count,0);assert.equal(rows[2].latest_result_is_current,false);
    assert.equal(rows[3].status,'in_progress');assert.equal(rows[3].latest_result,null);assert.equal(rows[3].result_count,1);assert.equal(rows[3].omitted_result_count,1);assert.equal(rows[3].latest_result_is_current,false);assert.equal(snapshot.history_complete,false);
    assert.deepEqual(snapshot.cards.flatMap(c=>c.requirements.map(r=>r.text)),['Alpha-only requirement','Alpha second requirement','Alpha running requirement','Alpha rerun requirement']);
    const article=page.locator('[data-work-card-snapshot]');
    assert.equal(await article.count(),1);
    const text=await article.textContent();
    assert.match(text,/Alpha-only requirement/);assert.match(text,/Alpha second requirement/);assert.match(text,/Alpha running requirement/);assert.match(text,/Alpha rerun requirement/);
    assert.match(await article.locator('.work-card-requirement').filter({hasText:'Alpha rerun requirement'}).textContent(),/进行中/);
    assert.match(text,/状态未知/);assert.doesNotMatch(text,/项目已完成|验收通过/);
    const pendingRow=article.locator('.work-card-requirement').filter({hasText:'Alpha second requirement'});
    assert.equal(await pendingRow.count(),1);assert.match(await pendingRow.textContent(),/待处理/);
    assert.doesNotMatch(text,/Beta-only requirement|synthetic-beta/);
    assert.deepEqual(errors,[]);
    await page.evaluate(()=>{disposeUnified();client.destroy()});
  } finally {
    await context?.close();await browser?.close();
    if(child.exitCode===null){child.stdin.end('stop\n');let timer;await Promise.race([new Promise(r=>child.once('exit',r)),new Promise(r=>{timer=setTimeout(r,3000)})]).finally(()=>clearTimeout(timer));if(child.exitCode===null){child.kill();throw new Error('fixture did not stop; temporary directory retained')}}
    // Preserve the synthetic fixture rather than risking deletion of an unchecked path.
  }
});
