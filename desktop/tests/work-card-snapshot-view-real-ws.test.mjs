import assert from 'node:assert/strict';
import test from 'node:test';
import {existsSync,lstatSync,mkdirSync,mkdtempSync,readFileSync,realpathSync,renameSync,readdirSync,rmSync,rmdirSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {spawn,spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {createInterface} from 'node:readline';
import {randomUUID} from 'node:crypto';

const pythonCommand = process.platform === 'win32' ? 'python.exe' : 'python3';
const core = path.resolve(import.meta.dirname, '../../agent-core');
const ts = createRequire(import.meta.url)('typescript');

const serverCode = String.raw`
import sys,json,pathlib,os,asyncio
root=pathlib.Path(sys.argv[1]).resolve(); core=pathlib.Path(sys.argv[2]).resolve()
assert pathlib.Path(os.environ['MY_AGENT_ROOT']).resolve()==root
sys.path.insert(0,str(core)); sys.path.insert(0,str(core/'tests'))
from paths import AgentPaths
paths=AgentPaths.discover()
assert paths.agent_root==root and paths.workspace==root/'workspace' and paths.data==root/'data' and paths.evolve==root/'evolve'
from test_linked_project_session_delivery import setup_copy
from work_card_ledger import WorkCardLedger
from linked_snapshot_view_evidence import LinkedSnapshotViewEvidence
from work_card_presentations import WorkCardPresentations
import server
from websockets.asyncio.server import serve
parent,ns,rows,store,events,bridge,repl=setup_copy(root)
assert parent.paths.agent_root==root and parent.paths.data==root/'data' and parent.session_dir.resolve().is_relative_to((root/'data').resolve())
parent.save()
assert server._resume_linked_project_session_copies(repl,bridge,parent,store,emit_event=True)==1
present=next(e for e in events if e.get('type')=='work-card.present')
ledger=WorkCardLedger(parent,store); view=LinkedSnapshotViewEvidence(parent,store,ledger)
assert view.elapsed(present['presentation_id'],present['card_id']).status=='not_viewed'
server.resume_desktop_or_create=lambda _paths: parent
async def main():
 handler=server.WsSessionHandler(paths)
 async with serve(handler.handle,'127.0.0.1',0) as socket:
  print(json.dumps({'port':socket.sockets[0].getsockname()[1], 'session':parent.conversation_id,
   'presentation_id':present['presentation_id'],'card_id':present['card_id'],
   'evidence_path':str(view._evidence._path),'presentations_path':str(WorkCardPresentations(parent,store,ledger)._presentations_store._path),
   'fixture_root':str(root),'fixture':'synthetic-linked-copy-no-model'}),flush=True)
  while True:
   line=await asyncio.to_thread(sys.stdin.buffer.readline)
   if line.strip()==b'inspect':
    print(json.dumps({'type':'evidence-inspection','status':view.elapsed(present['presentation_id'],present['card_id']).status}),flush=True)
   else:
    break
asyncio.run(main())
`;
function loadDesktop(WebSocketClass = globalThis.WebSocket) {
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
      {setTimeout,clearTimeout,myAgentDesktop:{}}, WebSocketClass, {randomUUID},
    );
    return exports;
  }
  return {
    AgentWsClient: load(new URL('../src/api/ws.ts', import.meta.url)).AgentWsClient,
    ...load(new URL('../src/shells/unified/work-card-presentation.ts', import.meta.url)),
  };
}

function isWithin(root, target) {
  const relative=path.relative(root,target);
  return relative!==''&&relative!== '..'&&!relative.startsWith(`..${path.sep}`)&&!path.isAbsolute(relative);
}

function samePath(left,right){
  const a=path.resolve(left),b=path.resolve(right);
  return process.platform==='win32'?a.toLowerCase()===b.toLowerCase():a===b;
}
function statIdentity(stat){return {dev:stat.dev.toString(),ino:stat.ino.toString(),birthtimeNs:stat.birthtimeNs.toString()};}
function safeDirectoryIdentity(target){
  const before=lstatSync(target,{bigint:true});
  if(!before.isDirectory()||before.isSymbolicLink()||before.dev===0n||before.ino===0n||before.birthtimeNs===0n) throw new Error('fixture root is not a real identity-bearing directory');
  const canonical=realpathSync.native(target);
  const after=lstatSync(target,{bigint:true});
  const canonicalAfter=realpathSync.native(target);
  if(!samePath(canonical,target)||!samePath(canonicalAfter,canonical)||JSON.stringify(statIdentity(before))!==JSON.stringify(statIdentity(after))) throw new Error('fixture root was linked or replaced while checking identity');
  return {canonical,...statIdentity(after)};
}
function sameIdentity(actual,expected){return actual.dev===expected.dev&&actual.ino===expected.ino&&actual.birthtimeNs===expected.birthtimeNs;}
function captureFixtureCleanupIdentity(root,owner,createdRootIdentity){
  const tempCanonical=realpathSync.native(tmpdir());
  const currentRoot=safeDirectoryIdentity(root);
  if(!isWithin(tempCanonical,createdRootIdentity.canonical)||!samePath(currentRoot.canonical,createdRootIdentity.canonical)||!sameIdentity(currentRoot,createdRootIdentity)) throw new Error('fixture root changed before cleanup guard was captured');
  const markerPath=path.join(root,'.work-card-receipt-fixture');const markerBefore=lstatSync(markerPath,{bigint:true});
  if(!markerBefore.isFile()||markerBefore.isSymbolicLink()||markerBefore.dev===0n||markerBefore.ino===0n||markerBefore.birthtimeNs===0n||readFileSync(markerPath,'utf8').trim()!==owner) throw new Error('fixture ownership marker is not a real matching file');
  const markerAfter=lstatSync(markerPath,{bigint:true});const markerIdentity=statIdentity(markerAfter);
  if(JSON.stringify(statIdentity(markerBefore))!==JSON.stringify(markerIdentity)) throw new Error('fixture marker changed while capturing cleanup identity');
  return {tempCanonical,rootIdentity:createdRootIdentity,markerIdentity};
}
function assertFixtureCleanupIdentity(root,owner,created){
  if(!created) throw new Error('fixture identity was not captured; preserving temporary directory');
  const currentTemp=realpathSync.native(tmpdir());
  if(!samePath(currentTemp,created.tempCanonical)) throw new Error('canonical OS temp root changed; preserving fixture');
  const currentRoot=safeDirectoryIdentity(root);
  if(!isWithin(created.tempCanonical,currentRoot.canonical)||!samePath(currentRoot.canonical,created.rootIdentity.canonical)||!sameIdentity(currentRoot,created.rootIdentity)) throw new Error('fixture root identity/canonical path changed; preserving temporary directory');
  const markerPath=path.join(root,'.work-card-receipt-fixture');const markerBefore=lstatSync(markerPath,{bigint:true});
  if(!markerBefore.isFile()||markerBefore.isSymbolicLink()) throw new Error('fixture marker is linked or not regular; preserving temporary directory');
  const markerText=readFileSync(markerPath,'utf8');const markerAfter=lstatSync(markerPath,{bigint:true});
  if(JSON.stringify(statIdentity(markerBefore))!==JSON.stringify(created.markerIdentity)||JSON.stringify(statIdentity(markerAfter))!==JSON.stringify(created.markerIdentity)||markerText.trim()!==owner) throw new Error('fixture marker identity changed; preserving temporary directory');
  return currentRoot.canonical;
}
function makeIsolatedEnvironment(root,owner) {
  const paths={
    workspace:path.join(root,'workspace'), data:path.join(root,'data'), evolve:path.join(root,'evolve'),
    temp:path.join(root,'temp'), home:path.join(root,'home'), userProfile:path.join(root,'userprofile'),
    appData:path.join(root,'appdata','Roaming'), localAppData:path.join(root,'appdata','Local'),
  };
  for (const dir of Object.values(paths)) mkdirSync(dir,{recursive:true});
  writeFileSync(path.join(paths.evolve,'_index.core.toml'),'[core]\n',{flag:'wx'});
  writeFileSync(path.join(paths.evolve,'_index.toml'),'# synthetic test root\n',{flag:'wx'});
  writeFileSync(path.join(root,'.work-card-receipt-fixture'),owner+'\n',{flag:'wx'});
  const env={};
  for (const key of ['SYSTEMROOT','WINDIR','PATH','PATHEXT']) if(typeof process.env[key]==='string') env[key]=process.env[key];
  Object.assign(env,{
    MY_AGENT_ROOT:root, PYTHONDONTWRITEBYTECODE:'1', PYTHONNOUSERSITE:'1', PYTHONUTF8:'1', PYTHONIOENCODING:'utf-8',
    TEMP:paths.temp,TMP:paths.temp,HOME:paths.home,USERPROFILE:paths.userProfile,
    APPDATA:paths.appData,LOCALAPPDATA:paths.localAppData,
  });
  assert.equal(Object.keys(env).some(key=>/(?:API.?KEY|TOKEN|SECRET|CREDENTIAL)/i.test(key)),false);
  return {env,paths};
}

function preflightPythonIsolatedRoot(root, env) {
  const probe=String.raw`
import json,os,pathlib,site,sys
root=pathlib.Path(sys.argv[1]).resolve(); core=pathlib.Path(sys.argv[2]).resolve()
assert pathlib.Path(os.environ.get('MY_AGENT_ROOT','')).resolve()==root
sys.path.insert(0,str(core))
from paths import AgentPaths
paths=AgentPaths.discover()
assert paths.agent_root==root and paths.workspace==root/'workspace' and paths.data==root/'data' and paths.evolve==root/'evolve'
assert str(site.getusersitepackages()) not in sys.path
import websockets
from websockets.asyncio.server import serve
import server
print(json.dumps({'executable':sys.executable,'agent_root':str(paths.agent_root),'workspace':str(paths.workspace),'data':str(paths.data),'evolve':str(paths.evolve),'websockets':websockets.__version__}))
`;
  const sandboxPython=path.resolve(import.meta.dirname,'../../.venv/Scripts/python.exe');
  const projectPython=path.resolve(import.meta.dirname,'../../../../.venv/Scripts/python.exe');
  const candidate=existsSync(sandboxPython)?sandboxPython:existsSync(projectPython)?projectPython:pythonCommand;
  const result=spawnSync(candidate,['-I','-B','-c',probe,root,core],{cwd:core,env,encoding:'utf8',windowsHide:true,timeout:20000,maxBuffer:2*1024*1024});
  if(result.error||result.status!==0) throw new Error(`isolated Python/AgentPaths preflight failed; server not started: ${result.error?.message??result.stderr}`);
  const lines=result.stdout.trim().split(/\r?\n/);let info;
  try{info=JSON.parse(lines.at(-1));}catch{throw new Error('isolated Python preflight returned no identity JSON; server not started');}
  if(!path.isAbsolute(info.executable)||path.resolve(info.agent_root)!==path.resolve(root)
    ||path.resolve(info.workspace)!==path.join(path.resolve(root),'workspace')
    ||path.resolve(info.data)!==path.join(path.resolve(root),'data')
    ||path.resolve(info.evolve)!==path.join(path.resolve(root),'evolve')) throw new Error('AgentPaths isolation mismatch; server not started');
  if(process.env.USERPROFILE&&isWithin(path.resolve(process.env.USERPROFILE),path.resolve(info.executable))) throw new Error('Python runtime is inside the user profile; server not started');
  return info.executable;
}
function waitFor(client, predicate, timeoutMs = 8000) {
  return new Promise((resolve,reject) => {
    const timer=setTimeout(()=>{off();reject(new Error('protocol timeout'));},timeoutMs);
    const off=client.onEvent(event=>{
      if (predicate(event)) { clearTimeout(timer); off(); resolve(event); }
    });
  });
}

function pause(ms) { return new Promise(resolve=>setTimeout(resolve,ms)); }

async function waitForRawFrame(queue,predicate,timeoutMs=5000) {
  const deadline=Date.now()+timeoutMs;
  while(Date.now()<deadline){const index=queue.findIndex(predicate);if(index>=0)return queue.splice(index,1)[0];await pause(10);}
  throw new Error('raw WebSocket fixture response timeout');
}
async function connect(Client, port, expectedSession) {
  const client = new Client('127.0.0.1',port);
  const banner = waitFor(client,event=>event.type==='session.banner');
  await client.connect();
  assert.equal((await banner).session_id,expectedSession);
  return client;
}


test('isolated real WebSocket snapshot view records explicit evidence and rejects wrong session', {timeout:60000}, async () => {
 const root=mkdtempSync(path.join(tmpdir(),'linked-snapshot-wire-'));
 const owner='synthetic-only:'+randomUUID(); const identity=safeDirectoryIdentity(root);
 let guard=null,child=null,client=null;let stderr='';
 try {
  const env=makeIsolatedEnvironment(root,owner).env;
  guard=captureFixtureCleanupIdentity(root,owner,identity);
  const python=preflightPythonIsolatedRoot(root,env);
  child=spawn(python,['-I','-B','-c',serverCode,root,core],{cwd:core,env,stdio:['pipe','pipe','pipe'],windowsHide:true,shell:false});
  child.stderr.on('data',chunk=>{if(stderr.length<10000)stderr+=String(chunk).slice(0,10000-stderr.length);});
  const lines=createInterface({input:child.stdout}); let timer;
  const startup=await Promise.race([
   new Promise((resolve,reject)=>{lines.once('line',line=>{try{resolve(JSON.parse(line));}catch{reject(new Error('invalid fixture startup'));}});child.once('error',reject);child.once('exit',code=>reject(new Error(`fixture exited ${code}: ${stderr}`)));}),
   new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('fixture startup timeout')),12000);})
  ]).finally(()=>clearTimeout(timer));
  assert.equal(startup.fixture,'synthetic-linked-copy-no-model');
  assert.equal(path.resolve(startup.fixture_root),path.resolve(root));
  for(const file of [startup.evidence_path,startup.presentations_path]) assert.ok(isWithin(path.resolve(root),path.resolve(file)));
  const frames=[],sent=[]; let socket;
  class InspectSocket extends WebSocket {
   constructor(url){super(url);socket=this;this.addEventListener('message',event=>{try{frames.push(JSON.parse(String(event.data)));}catch{}});}
   send(data){sent.push(JSON.parse(String(data)));return super.send(data);}
  }
  const {AgentWsClient}=loadDesktop(InspectSocket);
  client=new AgentWsClient('127.0.0.1',startup.port);
  const banner=waitFor(client,e=>e.type==='session.banner');
  const offer=waitFor(client,e=>e.type==='work-card.present');
  await client.connect(); assert.equal((await banner).session_id,startup.session);
  const present=await offer;
  assert.equal(present.presentation_id,startup.presentation_id);assert.equal(present.card_id,startup.card_id);
  assert.equal(present.card.snapshot.kind,'linked_project_session_copy');
  assert.equal(existsSync(startup.evidence_path),false,'offer is not user view');
  const id={sessionId:startup.session,presentationId:startup.presentation_id,cardId:startup.card_id};
  const received=waitFor(client,e=>e.type==='work-card.received.done');
  client.acknowledgeWorkCardReceived(id);assert.equal((await received).status,'received');
  assert.equal(existsSync(startup.evidence_path),false,'receipt is not user view');
  assert.throws(()=>client.acknowledgeWorkCardSnapshotView({...id,sessionId:'foreign-session'}));
  socket.send(JSON.stringify({type:'work-card.snapshot.view',session_id:'foreign-session',presentation_id:id.presentationId,card_id:id.cardId}));
  assert.equal((await waitForRawFrame(frames,e=>e.type==='work-card.snapshot.view.done'&&e.session_id==='foreign-session')).status,'session_changed');
  assert.equal(existsSync(startup.evidence_path),false,'wrong session is not user view');
  const done=waitFor(client,e=>e.type==='work-card.snapshot.view.done');
  client.acknowledgeWorkCardSnapshotView(id);
  const result=await done;assert.equal(result.status,'acknowledged');
  assert.equal(result.session_id,id.sessionId);assert.equal(result.presentation_id,id.presentationId);assert.equal(result.card_id,id.cardId);
  client.acknowledgeWorkCardSnapshotView(id);
  assert.equal(sent.filter(e=>e.type==='work-card.snapshot.view'&&e.session_id===id.sessionId).length,1,'client suppresses duplicate');
  const inspection=new Promise((resolve,reject)=>{const timeout=setTimeout(()=>reject(new Error('server evidence inspection timeout')),5000);lines.once('line',line=>{clearTimeout(timeout);try{resolve(JSON.parse(line));}catch{reject(new Error('invalid evidence inspection'));}});});
  child.stdin.write('inspect\n');
  assert.deepEqual(await inspection,{type:'evidence-inspection',status:'waiting'},'server reopens evidence and sees under 30 minutes');
  const persisted=JSON.parse(readFileSync(startup.evidence_path,'utf8'));
  assert.equal(persisted.conversation_id,id.sessionId);assert.equal(persisted.items.length,1);
  assert.equal(persisted.items[0].presentation_id,id.presentationId);assert.equal(persisted.items[0].card_id,id.cardId);
  assert.ok(persisted.items[0].evidence?.ack_boot_id);assert.ok(Number.isSafeInteger(persisted.items[0].evidence.ack_monotonic_ns));
  const fact=JSON.parse(readFileSync(startup.presentations_path,'utf8')).items.find(x=>x.presentation_id===id.presentationId);
  assert.ok(fact.acknowledged_at);
  socket.send(JSON.stringify({type:'work-card.snapshot.view',session_id:id.sessionId,presentation_id:id.presentationId,card_id:id.cardId}));
  assert.equal((await waitForRawFrame(frames,e=>e.type==='work-card.snapshot.view.done'&&e.status==='already_acknowledged')).status,'already_acknowledged');
  assert.deepEqual(JSON.parse(readFileSync(startup.evidence_path,'utf8')).items,persisted.items,'duplicate must not restart clock');
 } finally {
  client?.destroy();
  if(child&&child.exitCode===null){
   child.stdin.end('stop\n');let stopTimer;
   await Promise.race([new Promise(resolve=>child.once('exit',resolve)),new Promise(resolve=>{stopTimer=setTimeout(resolve,4000);})]).finally(()=>clearTimeout(stopTimer));
   if(child.exitCode===null){child.kill();await new Promise(resolve=>child.once('exit',resolve));}
  }
  if(!guard) throw new Error('fixture identity unavailable; temporary root retained');
  rmSync(assertFixtureCleanupIdentity(root,owner,guard),{recursive:true,force:false,maxRetries:5,retryDelay:200});
 }
});
