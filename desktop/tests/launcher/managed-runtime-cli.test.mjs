import test from 'node:test';
import assert from 'node:assert/strict';
import {runManagedCli} from '../../launcher/managed-cli.mjs';

const id='94fd8621-e75b-4ec4-a5a3-7dbf88111eaf';
const operation='aac31180-466e-4506-93f3-19d049960701';
const valid=['F:\\managed-runtime-cli-missing','F:\\managed-runtime-cli-missing\\.worktrees\\sandbox',id,operation,'a'.repeat(64)];
test('runtime check is explicit and exposes no registration or launch command',async()=>{
  const output=[];
  await runManagedCli(['--help'],{write:x=>output.push(x)});
  assert.match(output[0],/--check-production-runtime/);
  for(const command of ['--register-runtime','--launch','--adopt'])assert.equal((await runManagedCli([command,...valid],{write:()=>{}})).code,2);
});
test('runtime check refuses path, digest, identity and input overrides',async()=>{
  const invalid=[valid.slice(0,4),[...valid,'runtime.json'],['relative',...valid.slice(1)],[...valid.slice(0,2),id.toUpperCase(),...valid.slice(3)],[...valid.slice(0,4),'fixture'],[...valid.slice(0,3),'../escape',valid[4]]];
  for(const input of invalid){
    const output=[];const result=await runManagedCli(['--check-production-runtime',...input],{write:x=>output.push(JSON.parse(x))});
    assert.equal(result.code,2);assert.equal(output.length,1);assert.equal(result.ready_for_launch,false);
  }
});
test('well-shaped missing registration returns a safe refusal without paths, stack or input contents',async()=>{
  const output=[];
  const result=await runManagedCli(['--check-production-runtime',...valid],{write:x=>output.push(JSON.parse(x))});
  assert.equal(result.code,1);assert.equal(result.ready_for_launch,false);
  assert.deepEqual(Object.keys(output[0]).sort(),['code','ready_for_launch','state']);
  assert.equal(output[0].state,'refused');
});
