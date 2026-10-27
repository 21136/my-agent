import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fixture, git, put } from './launcher/fixtures.mjs';
import path from 'node:path';
import { createSandboxAdopter, sandboxAdoptionHostBlocker } from '../electron/sandbox-adopt.ts';
test('absence of development variables is not positive fixed Host identity', () => { assert.match(sandboxAdoptionHostBlocker({ mutableDevelopmentHost: false, testHost: false }) ?? '', /固定/); assert.equal(sandboxAdoptionHostBlocker({ mutableDevelopmentHost: false, testHost: false, fixedHostVerified: true }), null); });
test('adoption observer persists before Git writes, receipts and precise backup', async (t) => { const f = fixture(t, { repositories: true }), stable = path.join(f.root, 'source-stable'), sandbox = path.join(f.root, 'source-sandbox'), events: any[] = []; put(stable, 'agent-core/server.py', '# local\n'); put(sandbox, 'desktop/file.txt', 'next\n'); const service = createSandboxAdopter({ stableRoot: stable, sandboxRoot: sandbox }, { observer: { assertIdle() { }, beforeWrite(e: any) { events.push(['before', e.step]); }, afterWrite(e: any) { events.push(['after', e.step, e.code]); }, backup(sha: string) { events.push(['backup', sha]); }, finish(r: any) { events.push(['finish', r.ok]); } } }); const s = await service.getStatus(), result = await service.prepareAndMerge({ expectedSnapshotId: s.snapshotId, autoCommitSandbox: true, autoStashStable: true }); assert.equal(result.ok, true, JSON.stringify(result)); assert.ok(events.length > 3, 'missing durable observer calls'); assert.deepEqual(events[0], ['before', 'add']); assert.ok(events.some(e => e[0] === 'backup' && e[1] === result.recoveryStash)); assert.deepEqual(events.at(-1), ['finish', true]); });
test('journal failure or changed authority before the first write preserves both HEADs', async (t) => { const f = fixture(t, { repositories: true }), stable = path.join(f.root, 'source-stable'), sandbox = path.join(f.root, 'source-sandbox'); put(sandbox, 'desktop/file.txt', 'next\n'); let writes = 0; const service = createSandboxAdopter({ stableRoot: stable, sandboxRoot: sandbox }, { observer: { assertIdle() { }, beforeWrite() { writes++; throw Error('journal persistence unavailable'); }, afterWrite() { }, backup() { }, finish() { } } }); const s = await service.getStatus(), head = git(sandbox, 'rev-parse', 'HEAD'); const result = await service.prepareAndMerge({ expectedSnapshotId: s.snapshotId, autoCommitSandbox: true }); assert.equal(result.ok, false); assert.match(result.detail ?? result.message, /journal/); assert.equal(writes, 1); assert.equal(git(sandbox, 'rev-parse', 'HEAD'), head); assert.equal(git(sandbox, 'diff', '--cached'), ''); });


test('real disposable Git adoption can complete two durable cycles without reusing a pending journal',async t=>{
 const {createAdoptionJournal}=await import('../launcher/journal.mjs');
 const f=fixture(t,{repositories:true}),stable=path.join(f.root,'source-stable'),sandbox=path.join(f.root,'source-sandbox');
 const journal=createAdoptionJournal(path.join(f.root,'control-state'),{installation_id:f.run_id,release_id:'d'.repeat(64),binding_digest:'e'.repeat(64)});
 const service=createSandboxAdopter({stableRoot:stable,sandboxRoot:sandbox},{observer:journal});
 put(stable,'agent-core/server.py','# local bytes must never enter recovery journal\n');
 for(const value of ['first cycle\n','second cycle\n']){
  put(sandbox,'desktop/file.txt',value);const status=await service.getStatus();const result=await service.prepareAndMerge({expectedSnapshotId:status.snapshotId,autoCommitSandbox:true,autoStashStable:true});assert.equal(result.ok,true,JSON.stringify(result));assert.doesNotThrow(()=>journal.assertIdle());assert.match(result.recoveryStash??'',/^[0-9a-f]{40,64}$/);
 }
 const {readFileSync}=await import('node:fs');const raw=readFileSync(path.join(f.root,'control-state/adoption-journal.json'),'utf8'),state=JSON.parse(raw);
 assert.equal(state.history.length,2);assert.equal(state.status,'idle');assert.equal(raw.includes('local bytes must never enter'),false);assert.equal(state.history[0].baseline.repoRoot,stable);assert.equal(state.history[1].recovery_stash.length,40);
});

test('a failed pre-write receipt stays blocked even on the same journal handle',async t=>{
 const {createAdoptionJournal}=await import('../launcher/journal.mjs');const f=fixture(t,{repositories:true}),stable=path.join(f.root,'source-stable'),sandbox=path.join(f.root,'source-sandbox');
 const journal=createAdoptionJournal(path.join(f.root,'control-state'),{installation_id:f.run_id,release_id:'d'.repeat(64),binding_digest:'e'.repeat(64)});
 put(sandbox,'desktop/file.txt','not committed\n');let attempts=0;const observer={...journal,beforeWrite(e:any){journal.beforeWrite(e);attempts++;throw Error('simulated crash between durable intent and Git');}};
 const service=createSandboxAdopter({stableRoot:stable,sandboxRoot:sandbox},{observer}),head=git(sandbox,'rev-parse','HEAD');const status=await service.getStatus();const options={expectedSnapshotId:status.snapshotId,autoCommitSandbox:true};
 assert.equal((await service.prepareAndMerge(options)).ok,false);assert.equal((await service.prepareAndMerge(options)).ok,false);assert.equal(attempts,1);assert.equal(git(sandbox,'rev-parse','HEAD'),head);assert.throws(()=>journal.assertIdle(),/maintenance|unfinished/);
});
