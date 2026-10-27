import assert from 'node:assert/strict';
import {test} from 'node:test';
import {runManagedCli} from '../../launcher/managed-cli.mjs';
test('postmerge CLI refuses malformed identities, arbitrary toolchain args and Git executables',async()=>{
 const out=[];
 const cases=[
  ['--materialize-selected-postmerge','F:\\repo','F:\\repo\\.worktrees\\sandbox','a'.repeat(64),'3d6bd087-0a4c-4b85-a0f1-b5d486dd365c','F:\\fake\\git.exe'],
  ['--build-selected-postmerge','F:\\repo\\.worktrees\\sandbox','../escape'],
  ['--build-selected-postmerge','F:\\repo\\.worktrees\\sandbox','3d6bd087-0a4c-4b85-a0f1-b5d486dd365c','F:\\arbitrary-toolchain'],
  ['--seal-selected-postmerge','relative','3d6bd087-0a4c-4b85-a0f1-b5d486dd365c'],
  ['--verify-selected-release','F:\\repo\\.worktrees\\sandbox','3d6bd087-0a4c-4b85-a0f1-b5d486dd365c','launcher_fixture_v1'],
 ];
 for(const args of cases){const result=await runManagedCli(args,{write:value=>out.push(value)});assert.equal(result.code,2);assert.equal(result.ready_for_launch,false);}
 assert.equal(out.length,cases.length);
});
