import assert from 'node:assert/strict';
import {test} from 'node:test';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
import {existsSync,readFileSync,writeFileSync} from 'node:fs';
import path from 'node:path';
import {fixture,put,bindingFor} from './fixtures.mjs';

let lifecycle={},journal={},release={};
for (const [name, target] of [['lifecycle','../../launcher/lifecycle.mjs'],['journal','../../launcher/journal.mjs'],['release','../../launcher/release.mjs']]) {
  try { Object.assign(name==='lifecycle'?lifecycle:name==='journal'?journal:release, await import(target)); }
  catch (error) { if (error.code!=='ERR_MODULE_NOT_FOUND') throw error; }
}
function fixedFixture(t) {
  assert.equal(typeof release.freezeHostRelease,'function','fixed release API missing');
  const f=fixture(t,{repositories:true});f.release=release.freezeHostRelease(f.input);f.binding=bindingFor(f,f.release);
  f.bindingPath=put(f.root,'control-state/binding.json',JSON.stringify(f.binding));return f;
}
function fakeChild() {
  const child=new EventEmitter();child.stdin=new PassThrough();child.stdout=new PassThrough();child.stderr=new PassThrough();
  child.killCalls=[];child.kill=signal=>{child.killCalls.push(signal);return true;};child.bootstrap='';
  child.stdin.on('data',chunk=>{child.bootstrap+=chunk.toString();});
  child.stdin.on('end',()=>child.onBootstrapEnd?.());
  child.sendReady=()=>{
    assert.ok(child.bootstrap.endsWith('\n'),'bootstrap frame must end with a real LF byte');
    assert.equal(child.bootstrap.split('\n').length,2,'bootstrap must contain exactly one line');
    const frame=JSON.parse(child.bootstrap.slice(0,-1));
    child.parsedBootstrap=frame;
    child.stdout.write(JSON.stringify({type:'fixed-host.ready',installation_id:child.expected.installation_id,
      release_id:child.expected.release_id,instance_id:frame.instance_id,nonce:frame.nonce})+'\n');
  };
  return child;
}
function currentState(f) { return JSON.parse(readFileSync(path.join(f.binding.state_root,'instance.json'),'utf8')); }
async function waitForReady(f) {
  const deadline=Date.now()+5000;
  while(Date.now()<deadline) { if(currentState(f).state==='ready') return; await new Promise(resolve=>setTimeout(resolve,5)); }
  throw new Error('test timed out waiting for persisted ready state');
}
function tempJournal(t) { const f=fixture(t);return path.join(f.root,'control-state'); }

const identity=(n)=>({installation_id:`${n}${n}${n}${n}${n}${n}${n}${n}-${n}${n}${n}${n}-4${n}${n}${n}-8${n}${n}${n}-${n}${n}${n}${n}${n}${n}${n}${n}${n}${n}${n}${n}`,release_id:'a'.repeat(64),binding_digest:'b'.repeat(64)});

test('launch verifies binding and sends one canonical private stdin frame, then awaits normal close',async t=>{
  const f=fixedFixture(t);let child,options;
  const running=lifecycle.launchFixedHost({bindingPath:f.bindingPath,spawn:(exe,args,opts)=>{
    options={exe,args,opts};child=fakeChild();child.expected={installation_id:f.run_id,release_id:f.release.release_id};
    child.onBootstrapEnd=()=>child.sendReady();return child;
  }});
  await waitForReady(f);
  assert.equal(options.opts.shell,false);assert.deepEqual(options.opts.stdio,['pipe','pipe','pipe']);
  assert.equal(options.args.some(value=>String(value).includes('nonce')),false);
  const frame=child.parsedBootstrap;assert.deepEqual(Object.keys(frame).sort(),['binding_digest','binding_path','instance_id','nonce','protocol','release_id'].sort());
  assert.match(frame.nonce,/^[a-f0-9]{64}$/);assert.equal(frame.protocol,'my-agent-fixed-host-v1');
  assert.equal(options.opts.env.NODE_OPTIONS,undefined);assert.equal(options.opts.env.NODE_PATH,undefined);assert.equal(options.opts.env.PYTHONDONTWRITEBYTECODE,'1');
  for(const key of Object.keys(options.opts.env)) if(!['PYTHONDONTWRITEBYTECODE','PYTHONUTF8','PYTHONIOENCODING','PYTHONNOUSERSITE'].includes(key)) assert.doesNotMatch(key,/^(?:NODE_|PYTHON|GIT_|VITE_|EVOLUTION_|.*(?:TOKEN|KEY|SECRET|API))/i);
  assert.equal(JSON.stringify(currentState(f)).includes(frame.nonce),false);
  child.emit('close',0,null);assert.deepEqual(await running,{state:'stopped',instance_id:frame.instance_id});
  assert.equal(existsSync(path.join(f.binding.state_root,'instance.lock')),false);
});

test('existing unknown lock is neither stolen nor followed by a process spawn',async t=>{
  const f=fixedFixture(t);const lock=path.join(f.binding.state_root,'instance.lock');writeFileSync(lock,JSON.stringify({owner:'unknown'}));let calls=0;
  await assert.rejects(lifecycle.launchFixedHost({bindingPath:f.bindingPath,spawn:()=>{calls++;throw Error('must not spawn');}}));
  assert.equal(calls,0);assert.deepEqual(JSON.parse(readFileSync(lock,'utf8')),{owner:'unknown'});
});

test('invalid readiness identity kills only the owned child and keeps maintenance lock',async t=>{
  const f=fixedFixture(t);let child;
  await assert.rejects(lifecycle.launchFixedHost({bindingPath:f.bindingPath,timeoutMs:300,spawn:()=>{
    child=fakeChild();setImmediate(()=>child.stdout.write(JSON.stringify({type:'fixed-host.ready',installation_id:'wrong',release_id:'wrong',instance_id:'wrong',nonce:'0'.repeat(64)})+'\n'));return child;
  }}));
  assert.equal(child.killCalls.length,1);assert.equal(existsSync(path.join(f.binding.state_root,'instance.lock')),true);
  assert.equal(currentState(f).state,'maintenance_required');
});

test('bounded readiness timeout kills once but retains lock when child close is unknown',async t=>{
  const f=fixedFixture(t);let child;
  await assert.rejects(lifecycle.launchFixedHost({bindingPath:f.bindingPath,timeoutMs:25,spawn:()=>child=fakeChild()}),/timeout/i);
  assert.equal(child.killCalls.length,1);assert.equal(existsSync(path.join(f.binding.state_root,'instance.lock')),true);
  assert.equal(currentState(f).state,'maintenance_required');
});

test('ready followed by process error and close code zero remains failed and never unlocks',async t=>{
  const f=fixedFixture(t);let child;
  const running=lifecycle.launchFixedHost({bindingPath:f.bindingPath,timeoutMs:1000,spawn:()=>{
    child=fakeChild();child.expected={installation_id:f.run_id,release_id:f.release.release_id};child.onBootstrapEnd=()=>child.sendReady();return child;
  }});
  await waitForReady(f);child.emit('error',new Error('injected post-ready failure'));
  await assert.rejects(running,/process error/i);child.emit('close',0,null);
  assert.equal(currentState(f).state,'maintenance_required');assert.equal(existsSync(path.join(f.binding.state_root,'instance.lock')),true);
});

test('ready followed by bounded stdout overflow and close zero remains maintenance-required',async t=>{
  const f=fixedFixture(t);let child;
  const running=lifecycle.launchFixedHost({bindingPath:f.bindingPath,timeoutMs:1000,spawn:()=>{
    child=fakeChild();child.expected={installation_id:f.run_id,release_id:f.release.release_id};child.onBootstrapEnd=()=>child.sendReady();return child;
  }});
  await waitForReady(f);child.stdout.write(Buffer.alloc(64*1024,0x78));await assert.rejects(running,/stdout exceeded/i);
  child.emit('close',0,null);assert.equal(currentState(f).state,'maintenance_required');
  assert.equal(existsSync(path.join(f.binding.state_root,'instance.lock')),true);assert.equal(child.killCalls.length,1);
});
test('abnormal close after ready rejects; ready plus exit zero resolves stopped and releases owned lock',async t=>{
  const f=fixedFixture(t);let abnormal;
  const failed=lifecycle.launchFixedHost({bindingPath:f.bindingPath,spawn:()=>{abnormal=fakeChild();abnormal.expected={installation_id:f.run_id,release_id:f.release.release_id};abnormal.onBootstrapEnd=()=>abnormal.sendReady();return abnormal;}});
  await waitForReady(f);abnormal.emit('close',2,null);await assert.rejects(failed,/abnormally/i);
  assert.equal(existsSync(path.join(f.binding.state_root,'instance.lock')),true);assert.equal(currentState(f).state,'maintenance_required');
  const g=fixedFixture(t);let normal;
  const succeeded=lifecycle.launchFixedHost({bindingPath:g.bindingPath,spawn:()=>{normal=fakeChild();normal.expected={installation_id:g.run_id,release_id:g.release.release_id};normal.onBootstrapEnd=()=>normal.sendReady();return normal;}});
  await waitForReady(g);normal.emit('close',0,null);const result=await succeeded;
  assert.equal(result.state,'stopped');assert.equal(existsSync(path.join(g.binding.state_root,'instance.lock')),false);assert.equal(currentState(g).state,'stopped');
});

test('synchronous spawn throw leaves a maintenance record and retained lock without leaking the lock handle',async t=>{
  const f=fixedFixture(t);await assert.rejects(lifecycle.launchFixedHost({bindingPath:f.bindingPath,spawn:()=>{throw new Error('spawn denied');}}),/spawn failed/i);
  assert.equal(currentState(f).state,'maintenance_required');assert.equal(existsSync(path.join(f.binding.state_root,'instance.lock')),true);
});

test('journal persists before mutation, exact backup receipt and step receipts without arguments or file content',t=>{
  const root=tempJournal(t);assert.equal(typeof journal.createAdoptionJournal,'function','adoption journal API missing');
  const j=journal.createAdoptionJournal(root,identity('1'));assert.doesNotThrow(()=>j.assertIdle());
  j.beforeWrite({step:'stash',cwd:root});assert.throws(()=>j.assertIdle(),/active|unfinished|maintenance/i,'an active operation must block a new adoption');
  let durable=readFileSync(path.join(root,'adoption-journal.json'),'utf8');assert.match(durable,/stash/);assert.doesNotMatch(durable,/--secret|private file body/);
  j.afterWrite({step:'stash',cwd:root,code:0});j.backup('c'.repeat(40));
  j.beforeWrite({step:'merge',cwd:root});j.afterWrite({step:'merge',cwd:root,code:0});j.finish({ok:true,source_commit:'d'.repeat(40)});
  assert.doesNotThrow(()=>j.assertIdle());
});

test('failed result and unfinished receipt permanently block the next adoption',t=>{
  for(const mode of ['failed-finish','unfinished']) {
    const root=tempJournal(t),j=journal.createAdoptionJournal(root,identity(mode==='unfinished'?'2':'3'));
    j.beforeWrite({step:'merge',cwd:root});if(mode==='failed-finish')j.afterWrite({step:'merge',cwd:root,code:0});
    j.finish({ok:false,reason:'conflict'});assert.throws(()=>j.assertIdle(),/maintenance|unfinished|failed|active|blocked/i,mode);
  }
});

test('backup accepts a 40-hex Git object id after the stash receipt and records recovery_stash',t=>{
  const root=tempJournal(t),j=journal.createAdoptionJournal(root,identity('4'));
  j.beforeWrite({step:'stash',cwd:root});j.afterWrite({step:'stash',cwd:root,code:0});assert.doesNotThrow(()=>j.backup('1'.repeat(40)));
  const persisted=JSON.parse(readFileSync(path.join(root,'adoption-journal.json'),'utf8'));
  assert.equal(persisted.operation.recovery_stash,'1'.repeat(40));assert.equal(Object.hasOwn(persisted.operation,'backup_sha256'),false);
});

test('nonzero stash receipt still permits exact backup capture and authorized recovery write before failed finish',t=>{
  const root=tempJournal(t),j=journal.createAdoptionJournal(root,identity('5'));
  j.beforeWrite({step:'stash',cwd:root});j.afterWrite({step:'stash',cwd:root,code:1});j.backup('3'.repeat(40));assert.throws(()=>j.assertIdle(),/active|unfinished|maintenance/i);
  j.beforeWrite({step:'read-tree',cwd:root});j.afterWrite({step:'read-tree',cwd:root,code:0});j.finish({ok:false,reason:'recovery incomplete'});
  const persisted=JSON.parse(readFileSync(path.join(root,'adoption-journal.json'),'utf8'));
  assert.equal(persisted.status,'maintenance_required');assert.equal(persisted.operation.recovery_stash,'3'.repeat(40));
  assert.equal(persisted.operation.steps[0].receipt[0].code,1);assert.equal(persisted.operation.steps[1].receipt[0].code,0);
});

test('begin keeps source baseline ephemeral until first write; no-op finish and repeated success are safe',t=>{
  const root=tempJournal(t),j=journal.createAdoptionJournal(root,identity('6')),file=path.join(root,'adoption-journal.json');
  j.finish({ok:false,message:'no transaction'});
  const first={stableHead:'a'.repeat(40),sandboxHead:'b'.repeat(40),snapshotId:'c'.repeat(64),repoRoot:path.join(root,'stable'),sandboxRoot:path.join(root,'sandbox')};
  j.begin(first);j.finish({ok:false,message:'declined'});
  let persisted=JSON.parse(readFileSync(file,'utf8'));assert.equal(persisted.operation,null);assert.equal(persisted.status,'idle');
  j.begin(first);j.beforeWrite({step:'merge',cwd:first.repoRoot});persisted=JSON.parse(readFileSync(file,'utf8'));assert.deepEqual(persisted.operation.baseline,first);
  j.afterWrite({step:'merge',cwd:first.repoRoot,code:0});j.finish({ok:true,source_commit:'d'.repeat(40)});
  const second={...first,stableHead:'e'.repeat(40),snapshotId:'6'.repeat(64)};
  j.begin(second);j.beforeWrite({step:'merge',cwd:second.repoRoot});j.afterWrite({step:'merge',cwd:second.repoRoot,code:0});j.finish({ok:true});
  for(let i=0;i<6;i++){const baseline={...second,stableHead:String(i+1).repeat(40),snapshotId:String(i+1).repeat(64)};j.begin(baseline);j.beforeWrite({step:'merge',cwd:baseline.repoRoot});j.afterWrite({step:'merge',cwd:baseline.repoRoot,code:0});j.finish({ok:true});}
  persisted=JSON.parse(readFileSync(file,'utf8'));assert.equal(persisted.status,'idle');assert.equal(persisted.history.length,5);assert.ok(readFileSync(file).length<1024*1024);
});

test('new journal instance cannot take ownership of an unfinished operation',t=>{
  const root=tempJournal(t),id=identity('7'),owner=journal.createAdoptionJournal(root,id);owner.beforeWrite({step:'merge',cwd:root});
  assert.throws(()=>journal.createAdoptionJournal(root,id),/active|unfinished|owner|maintenance/i);
});







test('private instance nonce never escapes through child error messages or persisted diagnostics',async t=>{
 const f=fixedFixture(t);let child;
 const running=lifecycle.launchFixedHost({bindingPath:f.bindingPath,spawn:()=>{
  child=fakeChild();child.expected={installation_id:f.run_id,release_id:f.release.release_id};
  child.onBootstrapEnd=()=>{child.parsedBootstrap=JSON.parse(child.bootstrap.trim());child.emit('error',new Error('private bootstrap '+child.parsedBootstrap.nonce));};return child;
 }});
 let rejected;try{await running;}catch(error){rejected=error;}
 assert.ok(rejected);assert.equal(rejected.message,'fixed Host process error');
 assert.equal(rejected.message.includes(child.parsedBootstrap.nonce),false);
 assert.equal(JSON.stringify(currentState(f)).includes(child.parsedBootstrap.nonce),false);
});


test('synchronous stdin.end failure never exposes the private nonce in rejection or persisted diagnostics',async t=>{
 const f=fixedFixture(t);let child;
 const running=lifecycle.launchFixedHost({bindingPath:f.bindingPath,spawn:()=>{
  child=fakeChild();
  child.stdin.end=(frame,encoding)=>{
   assert.equal(encoding,'utf8');
   child.bootstrap=String(frame);
   child.parsedBootstrap=JSON.parse(child.bootstrap.trim());
   throw new Error('synchronous private bootstrap '+child.parsedBootstrap.nonce);
  };
  return child;
 }});
 let rejected;try{await running;}catch(error){rejected=error;}
 assert.ok(rejected);assert.ok(child.parsedBootstrap,'fake stdin.end must receive the JSON frame');
 assert.equal(rejected.message,'fixed Host bootstrap delivery failed');
 assert.equal(rejected.message.includes(child.parsedBootstrap.nonce),false);
 assert.equal(JSON.stringify(currentState(f)).includes(child.parsedBootstrap.nonce),false);
});

function launchReadinessCase(t, output, timeoutMs=1000) {
  const f=fixedFixture(t);let child;
  const running=lifecycle.launchFixedHost({bindingPath:f.bindingPath,timeoutMs,spawn:()=>{
    child=fakeChild();
    child.onBootstrapEnd=()=>{
      const frame=JSON.parse(child.bootstrap);
      const response={type:'fixed-host.ready',installation_id:f.run_id,release_id:f.release.release_id,
        instance_id:frame.instance_id,nonce:frame.nonce};
      output(child,JSON.stringify(response)+'\n',response,f);
    };
    return child;
  }});
  running.catch(()=>{}); // Failed red assertions must not create an unhandled launch rejection.
  return {f,child,running};
}

for(const mode of ['combined','separate','split']) {
  test(`readiness CRLF accepts exactly one leading prefix (${mode})`,async t=>{
    const beforeReady=[];
    const {f,child,running}=launchReadinessCase(t,(child,ready,response,f)=>{
      if(mode==='combined') child.stdout.write('\r\n'+ready);
      else {
        if(mode==='split') {
          child.stdout.write('\r');beforeReady.push(currentState(f).state);
          child.stdout.write('\n');
        } else child.stdout.write('\r\n');
        beforeReady.push(currentState(f).state);
        child.stdout.write(ready.slice(0,10));beforeReady.push(currentState(f).state);
        child.stdout.write(ready.slice(10));
      }
    });
    await new Promise(resolve=>setImmediate(resolve));
    assert.ok(beforeReady.every(state=>state==='starting'),'prefix/incomplete JSON must not be ready');
    assert.equal(currentState(f).state,'ready');assert.equal(child.killCalls.length,0);
    child.emit('close',0,null);assert.equal((await running).state,'stopped');
    assert.equal(existsSync(path.join(f.binding.state_root,'instance.lock')),false);
  });
}

test('readiness CRLF prefix alone remains starting and times out',async t=>{
  const {f,child,running}=launchReadinessCase(t,child=>child.stdout.write('\r\n'),100);
  await new Promise(resolve=>setImmediate(resolve));assert.equal(currentState(f).state,'starting');
  await assert.rejects(running,/timeout/);assert.equal(child.killCalls.length,1);
  assert.equal(currentState(f).state,'maintenance_required');
});

test('readiness CRLF prefix alone cannot turn exit zero into a ready shutdown',async t=>{
  const {f,child,running}=launchReadinessCase(t,child=>{child.stdout.write('\r\n');child.emit('close',0,null);});
  await assert.rejects(running,/before readiness/);assert.equal(currentState(f).state,'maintenance_required');
  assert.equal(existsSync(path.join(f.binding.state_root,'instance.lock')),true);
});

for(const [name,output] of [
  ['single LF',(child,ready)=>child.stdout.write('\n'+ready)],
  ['repeated CRLF',(child,ready)=>{child.stdout.write('\r\n');child.stdout.write('\r\n'+ready);}],
  ['nonempty log',(child,ready)=>child.stdout.write('startup log\n'+ready)],
  ['space',(child,ready)=>child.stdout.write(' '+ready)],
  ['tab',(child,ready)=>child.stdout.write('\t'+ready)],
  ['bare CR',(child,ready)=>child.stdout.write('\r'+ready)],
  ['multiple frames in one chunk',(child,ready)=>child.stdout.write('\r\n'+ready+ready)],
  ['second frame in later chunk',(child,ready)=>{child.stdout.write('\r\n'+ready);child.stdout.write(ready);}],
  ['wrong installation',(child,ready,response)=>child.stdout.write('\r\n'+JSON.stringify({...response,installation_id:'wrong'})+'\n')],
  ['wrong release',(child,ready,response)=>child.stdout.write('\r\n'+JSON.stringify({...response,release_id:'wrong'})+'\n')],
  ['wrong instance',(child,ready,response)=>child.stdout.write('\r\n'+JSON.stringify({...response,instance_id:'wrong'})+'\n')],
  ['wrong nonce',(child,ready,response)=>child.stdout.write('\r\n'+JSON.stringify({...response,nonce:'wrong'})+'\n')],
]) {
  test(`readiness CRLF still rejects ${name}`,async t=>{
    const {f,child,running}=launchReadinessCase(t,(child,...args)=>{output(child,...args);child.emit('close',0,null);});
    await assert.rejects(running,/verification|exactly one frame/);
    assert.equal(child.killCalls.length,1);assert.equal(currentState(f).state,'maintenance_required');
    assert.equal(existsSync(path.join(f.binding.state_root,'instance.lock')),true);
  });
}

test('readiness CRLF bytes count toward the entire 64 KiB stdout limit',async t=>{
  const {child,running}=launchReadinessCase(t,(child,ready)=>{
    child.stdout.write('\r\n');
    const frame=ready.slice(0,-1)+' '.repeat(65535-Buffer.byteLength(ready))+'\n';
    assert.equal(Buffer.byteLength(frame),65535);
    child.stdout.write(frame);
  });
  await assert.rejects(running,/stdout exceeded/);assert.equal(child.killCalls.length,1);
});
