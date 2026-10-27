import assert from 'node:assert/strict';
import {test} from 'node:test';
import {runManagedCli} from '../../launcher/managed-cli.mjs';

const id='94fd8621-e75b-4ec4-a5a3-7dbf88111eaf';
const operation='aac31180-466e-4506-93f3-19d049960701';
const valid=['F:\\managed-context-cli-missing','F:\\managed-context-cli-missing\\.worktrees\\sandbox',id,operation,'a'.repeat(64)];
test('production context commands are explicit and remain unavailable as launch/adopt commands',async()=>{
  const output=[];
  await runManagedCli(['--help'],{write:x=>output.push(x)});
  assert.match(output[0],/--prepare-production-context/);
  assert.match(output[0],/--check-production-context/);
  for(const command of ['--launch','--adopt'])assert.equal((await runManagedCli([command,...valid],{write:()=>{}})).code,2);
});
test('production context CLI rejects aliases, malformed identities and arbitrary runtime/output arguments',async()=>{
  for(const command of ['--prepare-production-context','--check-production-context']){
    for(const input of [valid.slice(0,4),[...valid,'F:\\arbitrary-output'],['relative',...valid.slice(1)],[valid[0],valid[1],id.toUpperCase(),operation,valid[4]],[...valid.slice(0,3),'../escape',valid[4]],[...valid.slice(0,4),'launcher_fixture_v1']]){
      const lines=[];const result=await runManagedCli([command,...input],{write:x=>lines.push(JSON.parse(x))});
      assert.equal(result.code,2);assert.equal(result.ready_for_launch,false);assert.equal(lines.length,1);
    }
  }
});
test('well-shaped missing production contexts fail safely rather than accepting unknown command or leaking an exception',async()=>{
  for(const command of ['--prepare-production-context','--check-production-context']){
    const output=[];const result=await runManagedCli([command,...valid],{write:x=>output.push(JSON.parse(x))});
    assert.equal(result.code,1);assert.equal(result.ready_for_launch,false);
    assert.equal(output[0].state,'refused');assert.equal(typeof output[0].code,'string');
    assert.equal('message' in output[0],false);assert.equal('stack' in output[0],false);
  }
});
