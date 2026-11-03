import assert from 'node:assert/strict';
import test from 'node:test';
import {existsSync,lstatSync,mkdirSync,mkdtempSync,readFileSync,realpathSync,renameSync,rmdirSync,rmSync,symlinkSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {createInterface} from 'node:readline';
import {randomUUID} from 'node:crypto';
const ts=createRequire(import.meta.url)('typescript');
const core=path.resolve(import.meta.dirname,'../../agent-core');
const serverCode=String.raw`
import sys,os,json,asyncio,pathlib,uuid
root=pathlib.Path(sys.argv[1]).resolve(); core=pathlib.Path(sys.argv[2]).resolve()
read_roots=[root,core,pathlib.Path(sys.prefix).resolve(),pathlib.Path(sys.base_prefix).resolve()]
def within(p,roots):
 try:
  p=pathlib.Path(p).resolve(); return any(p==r or r in p.parents for r in roots)
 except (TypeError,ValueError,OSError): return False
def audit(event,args):
 if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
  mode,flags=args[1:]; writing=(isinstance(mode,str) and any(c in mode for c in 'wax+')) or (isinstance(flags,int) and bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)))
  if not within(args[0],[root] if writing else read_roots): raise PermissionError('fixture audit: blocked file access')
 if event in ('os.mkdir','os.remove','os.rmdir','os.rename','os.replace'):
  targets=args[:2] if event in ('os.rename','os.replace') else args[:1]
  if any(not within(p,[root]) for p in targets): raise PermissionError('fixture audit: blocked mutation')
 if event in ('socket.connect','socket.bind') and isinstance(args[1],tuple) and args[1][0] not in ('127.0.0.1','::1'): raise PermissionError('fixture audit: blocked network')
sys.addaudithook(audit); sys.path.insert(0,str(core))
from paths import AgentPaths
from session import create_new
from work_records import WorkRecordStore
from server import WsSessionHandler, _work_card_presentation_id
from websockets.asyncio.server import serve
paths=AgentPaths(agent_root=root,workspace=root/'workspace',data=root/'data',evolve=root/'evolve')
paths.workspace.mkdir(parents=True); paths.evolve.mkdir()
session=create_new(paths,conversation_id='20261030-abcdef12')
store=WorkRecordStore(session)
mode=sys.argv[3] if len(sys.argv)>3 else 'cards'
if mode=='prestart-blocked':
 import hashlib
 from agent import _record_current_dialogue_worker_lifecycle
 from project_worker_host import ProjectWorkerHostOutcome, ProjectWorkerReservation
 from work_card_project_scope import build_project_scoped_work_record_cards, project_scoped_snapshot_request_id
 from work_card_ledger import WorkCardLedger
 from work_card_presentations import WorkCardPresentations
 project_id='synthetic-alpha'
 item=store.create_item(title='Synthetic prestart blocked task',scope={'kind':'independent'})
 req=store.add_requirement(item['item_id'],'Synthetic task was blocked before worker start.',event_id='synthetic-prestart-requirement',source_ref={'kind':'project','id':project_id})
 worker_run_id=str(uuid.uuid4()); parent_run_id=str(uuid.uuid4()); task_id='task-'+hashlib.sha256('\0'.join((session.conversation_id,item['item_id'],req['requirement_id'],project_id)).encode('utf-8')).hexdigest()
 reservation=ProjectWorkerReservation(reservation_id=str(uuid.uuid4()),worker_id='synthetic-worker',worker_run_id=worker_run_id,
  parent_run_id=parent_run_id,turn_epoch=1,conversation_id=session.conversation_id,project_id=project_id,task_id=task_id)
 outcome=ProjectWorkerHostOutcome(worker_id=reservation.worker_id,run_id=parent_run_id,turn_epoch=1,
  conversation_id=session.conversation_id,project_id=project_id,task_id=task_id,status='blocked',model_turn=None,
  error_code='project_or_target_scope_invalid',parent_run_id=parent_run_id,worker_run_id=worker_run_id,reservation_id=reservation.reservation_id)
 settled=_record_current_dialogue_worker_lifecycle(store,session_id=session.conversation_id,item_id=item['item_id'],
  requirement_id=req['requirement_id'],outcome=outcome,reservation=reservation,expected_requirement_text=req['text'])
 assert settled=='blocked_recorded'
 saved_item=store.get_item(item['item_id']);saved_req=next(row for row in saved_item['requirements'] if row['requirement_id']==req['requirement_id'])
 assert saved_req['status']=='blocked' and saved_req.get('current_run_id') is None
 result=next(row for row in saved_item['runs'] if row['requirement_id']==req['requirement_id'])
 assert result['status']=='blocked' and result['verification']['status']=='not_verified'
 ledger=WorkCardLedger(session,store);snapshot=build_project_scoped_work_record_cards(store,project_id)
 assert snapshot.completeness=='complete' and snapshot.cards[0].requirements[0].status=='blocked'
 saved=ledger.save_project_scoped_snapshot(project_scoped_snapshot_request_id(snapshot),snapshot)
 assert saved.status=='saved' and saved.card_id
 presentation_id=_work_card_presentation_id(session.conversation_id,parent_run_id,saved.card_id)
 presentations=WorkCardPresentations(session,store,ledger)
 assert presentations.create(presentation_id,saved.card_id,run_id=parent_run_id).status=='created'
 session.append_sent_work_card_reference(presentation_id,saved.card_id,run_id=parent_run_id)
 startup={'fixture':'prestart-blocked-project-card-from-production-path','session':session.conversation_id,
  'project_id':project_id,'card_id':saved.card_id,'presentation_id':presentation_id,'item_id':item['item_id'],
  'requirement_id':req['requirement_id'],'result_id':result['result_id']}
else:
 items=[store.create_item(title='Synthetic item '+str(i),scope={'kind':'independent'})['item_id'] for i in range(2)]
 startup={'session':session.conversation_id,'items':items}
async def main():
 async with serve(WsSessionHandler(paths).handle,'127.0.0.1',0) as s:
  print(json.dumps({'port':s.sockets[0].getsockname()[1],**startup}),flush=True)
  await asyncio.to_thread(sys.stdin.buffer.readline)
asyncio.run(main())
`;
function loadClient(){const cache=new Map();function load(url){if(cache.has(url.href))return cache.get(url.href);const exports={};cache.set(url.href,exports);const js=ts.transpileModule(readFileSync(url,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;new Function('exports','require','window','WebSocket','crypto',js)(exports,id=>{assert.ok(id.startsWith('.'));return load(new URL(id.endsWith('.ts')?id:`${id}.ts`,url));},{setTimeout,clearTimeout,myAgentDesktop:{}},WebSocket,{randomUUID});return exports;}return load(new URL('../src/api/ws.ts',import.meta.url)).AgentWsClient;}
function eventWait(client,predicate){return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{off();reject(new Error('protocol timeout'));},8000);const off=client.onEvent(event=>{if(predicate(event)){clearTimeout(timer);off();resolve(event);}});});}
function loadWorkCardRenderer(){
 const exports={};
 const url=new URL('../src/shells/unified/work-card-presentation.ts',import.meta.url);
 const js=ts.transpileModule(readFileSync(url,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 new Function('exports',js)(exports);
 return exports;
}

// Mirrors the real-ws fixture guard: preserve rather than delete on any uncertainty.
function samePath(a,b){a=path.resolve(a);b=path.resolve(b);return process.platform==='win32'?a.toLowerCase()===b.toLowerCase():a===b;}
function statIdentity(s){return `${s.dev}:${s.ino}:${s.birthtimeNs}`;}
function directoryIdentity(root){
 const before=lstatSync(root,{bigint:true});
 if(!path.isAbsolute(root)||!before.isDirectory()||before.isSymbolicLink()||before.dev===0n||before.ino===0n||before.birthtimeNs===0n)throw new Error('unsafe fixture directory identity');
 const canonical=realpathSync.native(root),after=lstatSync(root,{bigint:true});
 if(!samePath(root,canonical)||!samePath(realpathSync.native(root),canonical)||statIdentity(before)!==statIdentity(after))throw new Error('fixture root identity changed');
 return {canonical,identity:statIdentity(after)};
}
function markerIdentity(root,owner){
 const marker=path.join(root,'.work-card-wire-fixture'),before=lstatSync(marker,{bigint:true});
 if(!before.isFile()||before.isSymbolicLink()||before.dev===0n||before.ino===0n||before.birthtimeNs===0n)throw new Error('unsafe fixture marker');
 const text=readFileSync(marker,'utf8'),after=lstatSync(marker,{bigint:true});
 if(text!==owner||statIdentity(before)!==statIdentity(after))throw new Error('fixture marker changed');
 return statIdentity(after);
}
function captureCleanupIdentity(root,owner,created=directoryIdentity(root)){
 const temp=realpathSync.native(tmpdir()),current=directoryIdentity(root);
 if(!samePath(path.dirname(current.canonical),temp)||!samePath(current.canonical,created.canonical)||current.identity!==created.identity)throw new Error('fixture must be an unchanged direct child of OS temp');
 return {temp,root:created,marker:markerIdentity(root,owner)};
}
function assertCleanupIdentity(root,owner,created,child){
 if(!child||(child.exitCode==null&&child.signalCode==null))throw new Error('fixture child has not exited; preserving directory');
 if(!created||!samePath(realpathSync.native(tmpdir()),created.temp))throw new Error('temp identity changed; preserving directory');
 const current=directoryIdentity(root);
 if(!samePath(path.dirname(current.canonical),created.temp)||!samePath(current.canonical,created.root.canonical)||current.identity!==created.root.identity)throw new Error('fixture root identity changed; preserving directory');
 if(markerIdentity(root,owner)!==created.marker)throw new Error('fixture marker identity changed; preserving directory');
 return current.canonical;
}

test('cleanup guard rejects changed marker and a running child',()=>{
 assert.equal(typeof captureCleanupIdentity,'function');
 const root=mkdtempSync(path.join(realpathSync.native(tmpdir()),'wire-guard-'));
 const owner=randomUUID();const marker=path.join(root,'.work-card-wire-fixture');
 writeFileSync(marker,owner,{flag:'wx'});const identity=captureCleanupIdentity(root,owner);
 try {
  assert.throws(()=>assertCleanupIdentity(root,owner,identity,{exitCode:null,signalCode:null}),/child/i);
  writeFileSync(marker,'changed');
  assert.throws(()=>assertCleanupIdentity(root,owner,identity,{exitCode:0,signalCode:null}),/marker/i);
  assert.equal(existsSync(marker),true);
  writeFileSync(marker,owner);
  const saved=path.join(root,'.original-marker');renameSync(marker,saved);
  try {
   writeFileSync(marker,owner,{flag:'wx'});
   assert.throws(()=>assertCleanupIdentity(root,owner,identity,{exitCode:0,signalCode:null}),/marker/i);
  } finally {rmSync(marker);renameSync(saved,marker);}
 } finally {rmSync(assertCleanupIdentity(root,owner,identity,{exitCode:0,signalCode:null}),{recursive:true});}
});

test('cleanup guard rejects replacement root and nested directory',()=>{
 assert.equal(typeof captureCleanupIdentity,'function');
 const base=realpathSync.native(tmpdir());const root=mkdtempSync(path.join(base,'wire-guard-'));
 const owner=randomUUID();writeFileSync(path.join(root,'.work-card-wire-fixture'),owner,{flag:'wx'});
 const identity=captureCleanupIdentity(root,owner);const moved=path.join(base,`wire-moved-${randomUUID()}`);
 renameSync(root,moved);mkdirSync(root);writeFileSync(path.join(root,'.work-card-wire-fixture'),owner,{flag:'wx'});
 const replacement=captureCleanupIdentity(root,owner);
 try {
  assert.throws(()=>assertCleanupIdentity(root,owner,identity,{exitCode:0,signalCode:null}),/identity|changed/i);
  const nested=path.join(root,'nested');mkdirSync(nested);writeFileSync(path.join(nested,'.work-card-wire-fixture'),owner,{flag:'wx'});
  assert.throws(()=>captureCleanupIdentity(nested,owner),/temp|direct/i);
  assert.equal(existsSync(path.join(root,'.work-card-wire-fixture')),true);
 } finally {
  rmSync(assertCleanupIdentity(root,owner,replacement,{exitCode:0,signalCode:null}),{recursive:true});
  const movedIdentity=captureCleanupIdentity(moved,owner);rmSync(assertCleanupIdentity(moved,owner,movedIdentity,{exitCode:0,signalCode:null}),{recursive:true});
 }
});

test('cleanup guard refuses a linked root',()=>{
 const base=realpathSync.native(tmpdir()),root=mkdtempSync(path.join(base,'wire-guard-'));
 const owner=randomUUID();writeFileSync(path.join(root,'.work-card-wire-fixture'),owner,{flag:'wx'});
 const identity=captureCleanupIdentity(root,owner),link=path.join(base,`wire-link-${randomUUID()}`);
 let linked=false;
 try {
  symlinkSync(root,link,process.platform==='win32'?'junction':'dir');linked=true;
  assert.throws(()=>assertCleanupIdentity(link,owner,identity,{exitCode:0,signalCode:null}),/identity|directory|linked/i);
  assert.equal(existsSync(path.join(root,'.work-card-wire-fixture')),true);
 } finally {
  if(linked)rmdirSync(link);
  rmSync(assertCleanupIdentity(root,owner,identity,{exitCode:0,signalCode:null}),{recursive:true});
 }
});

test('real desktop client to isolated real websocket handler and ledger',{timeout:35000},async()=>{
 const root=mkdtempSync(path.join(realpathSync.native(tmpdir()),'work-card-wire-'));
 const createdRoot=directoryIdentity(root),owner=randomUUID();
 writeFileSync(path.join(root,'.work-card-wire-fixture'),owner,{flag:'wx'});
 const cleanupIdentity=captureCleanupIdentity(root,owner,createdRoot);
 const env={SYSTEMROOT:process.env.SYSTEMROOT,WINDIR:process.env.WINDIR,PATH:process.env.PATH,TEMP:root,TMP:root,HOME:root,USERPROFILE:root,APPDATA:root,LOCALAPPDATA:root};
 const child=spawn('F:/my-agent-main/.venv/Scripts/python.exe',['-I','-B','-c',serverCode,root,core],{cwd:core,env,stdio:['pipe','pipe','pipe']});
 const exited=new Promise(resolve=>{child.once('exit',resolve);child.once('error',resolve);});
 let stderr='';child.stderr.on('data',v=>{stderr+=String(v).slice(0,10000);});let client;
 try{
  const lines=createInterface({input:child.stdout});let timer;
  const startup=await Promise.race([new Promise((resolve,reject)=>{lines.once('line',line=>{try{resolve(JSON.parse(line));}catch{reject(new Error('invalid server startup'));}});child.once('exit',code=>reject(new Error(`server exited ${code}: ${stderr}`)));}),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('startup timeout')),8000);})]).finally(()=>clearTimeout(timer));
  const Client=loadClient();client=new Client('127.0.0.1',startup.port);const banner=eventWait(client,e=>e.type==='session.banner');await client.connect();assert.equal((await banner).session_id,startup.session);
  const created=[];
  for(const item of startup.items){const request=randomUUID();const wait=eventWait(client,e=>e.type==='work-card.create.done'&&e.request_id===request);client.createWorkCard(request,item);const e=await wait;assert.equal(e.status,'saved');assert.equal(e.card.snapshot.item_id,item);assert.equal(e.card.viewed_at,null);created.push(e.card_id);}
  let wait=eventWait(client,e=>e.type==='work-card.list.done');const id=client.listWorkCards({limit:1});let page=await wait;assert.equal(page.request_id,id);assert.equal(page.status,'ok');assert.equal(page.source_scope.session_id,startup.session);assert.equal(page.items.length,1);assert.equal(page.has_more,true);
  const first=page.items[0].card_id;wait=eventWait(client,e=>e.type==='work-card.list.done');const id2=client.listWorkCards({limit:1,beforeCardId:page.next_cursor});page=await wait;assert.equal(page.request_id,id2);assert.equal(page.items.length,1);assert.equal(page.has_more,false);assert.deepEqual(new Set([first,page.items[0].card_id]),new Set(created));
  wait=eventWait(client,e=>e.type==='work-card.get.done');client.getWorkCard(first);let result=await wait;assert.equal(result.status,'found');assert.equal(result.card.viewed_at,null);
  wait=eventWait(client,e=>e.type==='work-card.seen.done');client.markWorkCardSeen(first);result=await wait;assert.equal(result.status,'acknowledged');assert.ok(result.viewed_at);
  wait=eventWait(client,e=>e.type==='work-card.get.done');client.getWorkCard(first);result=await wait;assert.equal(result.status,'found');assert.ok(result.card.viewed_at);
 }finally{client?.destroy();if(child.exitCode==null&&child.signalCode==null){child.stdin.end('stop\n');let timer;await Promise.race([exited,new Promise(resolve=>{timer=setTimeout(resolve,3000);})]).finally(()=>clearTimeout(timer));if(child.exitCode==null&&child.signalCode==null){child.kill();let killTimer;await Promise.race([exited,new Promise(resolve=>{killTimer=setTimeout(resolve,3000);})]).finally(()=>clearTimeout(killTimer));}}rmSync(assertCleanupIdentity(root,owner,cleanupIdentity,child),{recursive:true,force:false,maxRetries:5,retryDelay:200});}
});

test('backend-generated pre-start blocked project card reaches the real desktop WS client and renderer', {timeout:35000}, async () => {
 const root=mkdtempSync(path.join(realpathSync.native(tmpdir()),'prestart-blocked-wire-'));
 const createdRoot=directoryIdentity(root),owner=randomUUID();
 writeFileSync(path.join(root,'.work-card-wire-fixture'),owner,{flag:'wx'});
 const cleanupIdentity=captureCleanupIdentity(root,owner,createdRoot);
 const env={SYSTEMROOT:process.env.SYSTEMROOT,WINDIR:process.env.WINDIR,PATH:process.env.PATH,TEMP:root,TMP:root,HOME:root,USERPROFILE:root,APPDATA:root,LOCALAPPDATA:root};
 const child=spawn('F:/my-agent-main/.venv/Scripts/python.exe',['-I','-B','-c',serverCode,root,core,'prestart-blocked'],{cwd:core,env,stdio:['pipe','pipe','pipe']});
 const exited=new Promise(resolve=>{child.once('exit',resolve);child.once('error',resolve);});
 let client;let stderr='';child.stderr.on('data',value=>{stderr+=String(value).slice(0,10000-stderr.length);});
 try {
  const lines=createInterface({input:child.stdout});let startupTimer;
  const startup=await Promise.race([
   new Promise((resolve,reject)=>{lines.once('line',line=>{try{resolve(JSON.parse(line));}catch{reject(new Error('invalid blocked-card fixture startup'));}});child.once('exit',code=>reject(new Error(`fixture exited ${code}: ${stderr}`)));}),
   new Promise((_,reject)=>{startupTimer=setTimeout(()=>reject(new Error('blocked-card fixture startup timeout')),10000);}),
  ]).finally(()=>clearTimeout(startupTimer));
  assert.equal(startup.fixture,'prestart-blocked-project-card-from-production-path');
  const Client=loadClient();client=new Client('127.0.0.1',startup.port);
  const banner=eventWait(client,event=>event.type==='session.banner');
  const present=eventWait(client,event=>event.type==='work-card.present');
  await client.connect();
  assert.equal((await banner).session_id,startup.session);
  const event=await present;
  assert.equal(event.session_id,startup.session);
  assert.equal(event.card.snapshot.kind,'project_scoped');
  assert.equal(event.card.snapshot.project_id,startup.project_id);
  const requirements=event.card.snapshot.cards.flatMap(item=>item.requirements);
  assert.equal(requirements.length,1);
  assert.equal(requirements[0].status,'blocked');
  assert.equal(requirements[0].current_run_id,null);
  assert.equal(requirements[0].latest_result_is_current,null);
  assert.equal(requirements[0].latest_result.status,'blocked');
  assert.equal(requirements[0].latest_result.verification_status,'not_verified');
  assert.equal(event.card.viewed_at,null);
  const renderer=loadWorkCardRenderer();
  const registry=new renderer.WorkCardPresentationRegistry(startup.session);
  const accepted=registry.present(event);
  assert.equal(accepted.kind,'added');
  const html=renderer.renderWorkCardEntry(accepted.entry);
  assert.match(html,/工作器尚未启动/);
  assert.match(html,/执行前检查受阻/);
  assert.match(html,/验收未运行/);
  assert.doesNotMatch(html,/project_worker_host_lifecycle|worker_execution_started|private-worker|reservation-id/);
  assert.doesNotMatch(html,/已启动|主机回合已结束|验收通过|已完成/);
 } finally {
  client?.destroy();
  if(child.exitCode==null&&child.signalCode==null){
   child.stdin.end('stop\\n');let stopTimer;
   await Promise.race([exited,new Promise(resolve=>{stopTimer=setTimeout(resolve,3000);})]).finally(()=>clearTimeout(stopTimer));
   if(child.exitCode==null&&child.signalCode==null){child.kill();let killTimer;await Promise.race([exited,new Promise(resolve=>{killTimer=setTimeout(resolve,3000);})]).finally(()=>clearTimeout(killTimer));}
  }
  const identityChild=child??{exitCode:0,signalCode:null};
  const ownedRoot=assertCleanupIdentity(root,owner,cleanupIdentity,identityChild);
  rmSync(ownedRoot,{recursive:true,force:false,maxRetries:5,retryDelay:200});
 }
});
