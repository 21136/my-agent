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

// The persisted singleton reference is a deterministic fixture for a confirmed
// unique match. This test deliberately does not call a model or use user data.
const serverCode = String.raw`
import sys,json,pathlib,uuid,os
root=pathlib.Path(sys.argv[1]).resolve(); core=pathlib.Path(sys.argv[2]).resolve()
assert pathlib.Path(os.environ['MY_AGENT_ROOT']).resolve()==root, 'MY_AGENT_ROOT must bind the isolated fixture'
sys.path.insert(0,str(core))
from paths import AgentPaths
from session import create_new
from work_records import WorkRecordStore
from work_record_cards import build_independent_work_record_card
from work_card_ledger import WorkCardLedger
from work_card_presentations import WorkCardPresentations
import server
from websockets.asyncio.server import serve

paths=AgentPaths.discover()
assert paths.agent_root==root and paths.workspace==root/'workspace' and paths.data==root/'data' and paths.evolve==root/'evolve', 'AgentPaths escaped fixture'
session=create_new(paths,conversation_id='20261030-abcdef12')
store=WorkRecordStore(session)
item=store.create_item(title='Synthetic only: Zeta-41 résumé',scope={'kind':'independent'})
req=store.add_requirement(item['item_id'],'Synthetic requirement; never real user data',event_id='synthetic-req')
for n in range(2):
 store.record_result(item['item_id'],req['requirement_id'],run_id=f'synthetic-old-run-{n}',result_id=f'synthetic-old-result-{n}',status='unknown',summary=f'synthetic result {n}',verification={'status':'not_verified','body':'PRIVATE-SYNTHETIC-MARKER'})
store.begin_requirement_run(item['item_id'],req['requirement_id'],run_id='synthetic-current-run',expected_current_run_id=None)
ledger=WorkCardLedger(session,store)
saved=ledger.save_snapshot(str(uuid.uuid4()),build_independent_work_record_card(store,item['item_id']))
assert saved.status=='saved'
card_id=saved.card_id
run_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'synthetic-singleton-confirmed-match'))
presentation_id=server._work_card_presentation_id(session.conversation_id,run_id,card_id)
presentations=WorkCardPresentations(session,store,ledger)
presentation=presentations.create(presentation_id,card_id,run_id=run_id)
assert presentation.status=='created'
session.append_sent_work_card_reference(presentation_id,card_id,run_id=run_id)

# Add a foreign-session frame through the real bridge after its valid event;
# AgentWsClient must reject it based on the session banner it received.
_original_init=server.WsBridge.__init__
def fixture_init(self,*args,**kwargs):
 original=kwargs.get('emit') if 'emit' in kwargs else args[0]
 def fixture_emit(event):
  original(event)
  if event.get('type')=='work-card.present':
   original({**event,'session_id':'foreign-synthetic-session'})
 if 'emit' in kwargs: kwargs['emit']=fixture_emit
 else: args=(fixture_emit,)+args[1:]
 _original_init(self,*args,**kwargs)
server.WsBridge.__init__=fixture_init
async def main():
 handler=server.WsSessionHandler(paths)
 async with serve(handler.handle,'127.0.0.1',0) as socket:
  print(json.dumps({'port':socket.sockets[0].getsockname()[1],'session':session.conversation_id,'card_id':card_id,'presentation_id':presentation_id,'receipt_path':str(presentations._presentations_store._path),'ledger_path':str(ledger._ledger_store._path),'fixture_root':str(root),'fixture':'synthetic-session; model-not-called'}),flush=True)
  await asyncio.to_thread(sys.stdin.buffer.readline)
import asyncio
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

test('fixture cleanup refuses a replaced root and preserves the replacement', () => {
  const tempCanonical=realpathSync.native(tmpdir());
  const parent=mkdtempSync(path.join(tempCanonical,'work-card-root-identity-'));
  const root=mkdtempSync(path.join(parent,'fixture-'));
  const moved=path.join(parent,'moved-fixture');const owner='replacement-check:'+randomUUID();
  try {
    writeFileSync(path.join(root,'.work-card-receipt-fixture'),owner+'\n',{flag:'wx'});
    const created=captureFixtureCleanupIdentity(root,owner,safeDirectoryIdentity(root));
    renameSync(root,moved);mkdirSync(root);writeFileSync(path.join(root,'.work-card-receipt-fixture'),owner+'\n',{flag:'wx'});
    assert.throws(()=>assertFixtureCleanupIdentity(root,owner,created),/identity|canonical path changed/i);
    assert.equal(existsSync(path.join(root,'.work-card-receipt-fixture')),true,'mismatched root must be preserved');
    const movedIdentity=captureFixtureCleanupIdentity(moved,owner,safeDirectoryIdentity(moved));
    rmSync(assertFixtureCleanupIdentity(moved,owner,movedIdentity),{recursive:true,force:false});
    const replacementIdentity=captureFixtureCleanupIdentity(root,owner,safeDirectoryIdentity(root));
    rmSync(assertFixtureCleanupIdentity(root,owner,replacementIdentity),{recursive:true,force:false});
    const parentIdentity=safeDirectoryIdentity(parent);
    assert.ok(isWithin(tempCanonical,parentIdentity.canonical));
    assert.deepEqual(readdirSync(parent),[]);
    rmdirSync(parentIdentity.canonical);
  } catch (error) {
    throw error;
  }
});
test('real isolated work-card receipt persists, retries after a failed send, and suppresses replay after reconnect', {timeout:60000}, async () => {
  const root=mkdtempSync(path.join(tmpdir(),'work-card-receipt-wire-'));
  const owner='synthetic-only:'+randomUUID();
  const createdRootIdentity=safeDirectoryIdentity(root);
  let cleanupIdentity=null,childEnv,cleanupCaptureError=null;
  let child=null, first=null, second=null, third=null;
  let stderr='';
  try {
    try { childEnv=makeIsolatedEnvironment(root,owner).env; cleanupIdentity=captureFixtureCleanupIdentity(root,owner,createdRootIdentity); }
    catch(error){ cleanupCaptureError=error; throw error; }
    // Refuse to spawn the WS fixture until the exact subprocess environment imports
    // the real AgentPaths resolver and resolves every root exclusively under tmpdir.
    const python=preflightPythonIsolatedRoot(root,childEnv);
    assert.equal(Object.keys(childEnv).some(key=>/(?:API.?KEY|TOKEN|SECRET|CREDENTIAL)/i.test(key)),false);
    child=spawn(python,['-I','-B','-c',serverCode,root,core],{cwd:core,env:childEnv,stdio:['pipe','pipe','pipe'],windowsHide:true,shell:false});
    const lines=createInterface({input:child.stdout});
    child.stderr.on('data',chunk=>{if(stderr.length<10000)stderr+=String(chunk).slice(0,10000-stderr.length);});
    let startupTimer;
    const startup=await Promise.race([
      new Promise((resolve,reject)=>{
        lines.once('line',line=>{try{resolve(JSON.parse(line));}catch{reject(new Error('invalid synthetic fixture startup JSON'));}});
        child.once('error',error=>reject(new Error(`fixture spawn failed: ${error.message}`)));
        child.once('exit',code=>reject(new Error(`fixture exited ${code}: ${stderr}`)));
      }),
      new Promise((_,reject)=>{startupTimer=setTimeout(()=>reject(new Error('isolated fixture startup timeout')),12000);}),
    ]).finally(()=>clearTimeout(startupTimer));
    assert.equal(startup.fixture,'synthetic-session; model-not-called');
    assert.equal(path.resolve(startup.fixture_root),path.resolve(root));
    for(const value of [startup.receipt_path,startup.ledger_path]) assert.ok(isWithin(path.resolve(root),path.resolve(value)),'receipt/ledger path must remain in the fresh temp Session');

    const NativeWebSocket=globalThis.WebSocket;
    assert.equal(typeof NativeWebSocket,'function','Node built-in WebSocket is required for this real wire test');
    let failNextReceipt=true;
    const firstRawFrames=[];
    class FailOnceWebSocket extends NativeWebSocket {
      constructor(url){super(url);this.addEventListener("message",event=>{try{firstRawFrames.push(JSON.parse(String(event.data)));}catch{}});}
      send(raw) {
        const frame=JSON.parse(String(raw));
        if(failNextReceipt&&frame?.type==='work-card.received') { failNextReceipt=false; throw new Error('synthetic outbound receipt failure'); }
        return super.send(raw);
      }
    }
    const firstModules=loadDesktop(FailOnceWebSocket);
    const Client=firstModules.AgentWsClient;
    const registry=new firstModules.WorkCardPresentationRegistry(startup.session);
    const chatReferences=new Map();
    const receiveOutcomes=[];
    const attachRealDesktopReceiver=(client)=>{
      client.onEvent(event=>{
        if(event.type!=='work-card.present') return;
        receiveOutcomes.push(firstModules.receiveWorkCardPresentation({
          registry,event,
          mergeReference:entry=>{
            const prior=chatReferences.get(entry.presentationId);
            if(prior&&prior!==entry.cardId) throw new Error('presentation collision in synthetic desktop chat');
            chatReferences.set(entry.presentationId,entry.cardId);
          },
          sendReceived:identity=>client.acknowledgeWorkCardReceived(identity),
        }));
      });
    };
    first=new Client('127.0.0.1',startup.port);
    attachRealDesktopReceiver(first);
    const firstBanner=waitFor(first,event=>event.type==='session.banner');
    const firstPresent=waitFor(first,event=>event.type==='work-card.present');
    const firstConnect=first.connect();
    assert.equal((await firstBanner).session_id,startup.session);await firstConnect;
    const replay=await firstPresent;
    assert.equal(replay.session_id,startup.session);
    assert.equal(replay.presentation_id,startup.presentation_id);
    assert.equal(replay.card_id,startup.card_id);
    assert.deepEqual(Object.keys(replay).sort(),['card','card_id','presentation_id','session_id','type'].sort());
    assert.deepEqual(Object.keys(replay.card).sort(),['card_id','saved_at','snapshot','viewed_at'].sort());
    assert.equal(replay.card.viewed_at,null);
    assert.equal(JSON.stringify(replay).includes('PRIVATE-SYNTHETIC-MARKER'),false);
    assert.equal(JSON.stringify(replay).includes('snapshot_sha256'),false);
    assert.equal(JSON.stringify(replay).includes('request_id'),false);
    assert.equal(receiveOutcomes[0].mutation.kind,'added');
    assert.equal(receiveOutcomes[0].receiptAttempt,'failed','forced send failure must not be called received');
    assert.equal(chatReferences.size,1);
    await pause(100);
    const pending=JSON.parse(readFileSync(startup.receipt_path,'utf8'));
    const pendingFact=pending.items.find(row=>row.presentation_id===startup.presentation_id);
    assert.equal(pendingFact.receipt_state,'pending');assert.equal(pendingFact.received_at,null);assert.equal(pendingFact.acknowledged_at,null);

    // Exercise the real server identity guard using malformed/unbound receipt frames.
    const badCardId=`wc-${'b'.repeat(64)}`;
    const badPresentationId='99999999-9999-4999-8999-999999999999';
    const wrongReceipts=[
      {session_id:'foreign-session',presentation_id:startup.presentation_id,card_id:startup.card_id},
      {session_id:startup.session,presentation_id:startup.presentation_id,card_id:badCardId},
      {session_id:startup.session,presentation_id:badPresentationId,card_id:startup.card_id},
    ];
    for(const identity of wrongReceipts){
      const response=waitForRawFrame(firstRawFrames,event=>event.type==='work-card.received.done'
        &&event.session_id===identity.session_id&&event.presentation_id===identity.presentation_id&&event.card_id===identity.card_id);
      first.send({type:'work-card.received',...identity});
      assert.equal((await response).status,'rejected');
    }
    const unchanged=JSON.parse(readFileSync(startup.receipt_path,'utf8')).items.find(row=>row.presentation_id===startup.presentation_id);
    assert.equal(unchanged.receipt_state,'pending');assert.equal(unchanged.received_at,null);assert.equal(unchanged.acknowledged_at,null);    first.destroy();first=null;await pause(100);

    // Reconnect: the same exact reference is merged idempotently and its receipt succeeds.
    const retryModules=loadDesktop(NativeWebSocket);second=new retryModules.AgentWsClient('127.0.0.1',startup.port);
    attachRealDesktopReceiver(second);
    const secondBanner=waitFor(second,event=>event.type==='session.banner');
    const secondPresent=waitFor(second,event=>event.type==='work-card.present');
    const secondReceipt=waitFor(second,event=>event.type==='work-card.received.done'&&event.session_id===startup.session&&event.presentation_id===startup.presentation_id&&event.card_id===startup.card_id);
    const secondConnect=second.connect();assert.equal((await secondBanner).session_id,startup.session);await secondConnect;
    const replayed=await secondPresent;assert.equal(replayed.presentation_id,startup.presentation_id);assert.equal(replayed.card_id,startup.card_id);
    const durableReceipt=await secondReceipt;assert.ok(['received','already_received'].includes(durableReceipt.status));assert.ok(durableReceipt.received_at);
    assert.equal(receiveOutcomes[1].mutation.kind,'duplicate');assert.equal(receiveOutcomes[1].receiptAttempt,'attempted');
    assert.equal(chatReferences.size,1,'replayed delivery must not create another chat card');
    let persisted=JSON.parse(readFileSync(startup.receipt_path,'utf8')).items.find(row=>row.presentation_id===startup.presentation_id);
    assert.equal(persisted.receipt_state,'received');assert.equal(persisted.received_at,durableReceipt.received_at);assert.equal(persisted.acknowledged_at,null);
    const ledger=JSON.parse(readFileSync(startup.ledger_path,'utf8'));
    assert.equal(ledger.items.find(row=>row.card_id===startup.card_id).viewed_at,null,'receipt must not mark the card viewed');
    const cardRead=waitFor(second,event=>event.type==='work-card.get.done'&&event.card_id===startup.card_id);
    second.getWorkCard(startup.card_id);assert.equal((await cardRead).card.viewed_at,null);

    // A fresh connection sees durable history but does not receive another live offer.
    second.destroy();second=null;await pause(100);
    const finalModules=loadDesktop(NativeWebSocket);third=new finalModules.AgentWsClient('127.0.0.1',startup.port);
    const finalBanner=waitFor(third,event=>event.type==='session.banner');const replayedAfterReceipt=[];
    third.onEvent(event=>{if(event.type==='work-card.present')replayedAfterReceipt.push(event);});
    const finalConnect=third.connect();assert.equal((await finalBanner).session_id,startup.session);await finalConnect;await pause(250);
    assert.equal(replayedAfterReceipt.length,0,'persisted receipt suppresses duplicate offers after reconnect');
    persisted=JSON.parse(readFileSync(startup.receipt_path,'utf8')).items.find(row=>row.presentation_id===startup.presentation_id);
    assert.equal(persisted.receipt_state,'received');assert.equal(persisted.acknowledged_at,null);
  } finally {
    first?.destroy();second?.destroy();third?.destroy();
    if(child&&child.exitCode===null){
      child.stdin.end('stop\n');
      let stopTimer;
      await Promise.race([new Promise(resolve=>child.once('exit',resolve)),new Promise(resolve=>{stopTimer=setTimeout(resolve,4000);})]).finally(()=>clearTimeout(stopTimer));
      if(child.exitCode===null){child.kill();await new Promise(resolve=>child.once('exit',resolve));}
    }
    if(!cleanupIdentity) throw new Error(`fixture cleanup identity unavailable; temporary root preserved: ${cleanupCaptureError?.message??"unknown setup failure"}`);
    const canonicalRoot=assertFixtureCleanupIdentity(root,owner,cleanupIdentity);
    rmSync(canonicalRoot,{recursive:true,force:false,maxRetries:5,retryDelay:200});
  }
});
