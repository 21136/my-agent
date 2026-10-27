import assert from "node:assert/strict";
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync} from "node:fs";
import {test} from "node:test";
import {createRequire} from "node:module";
import path from "node:path";
const require=createRequire(import.meta.url);const {freezeControlRelease}=require("../../../artifacts/evolution-control-build/control-release.js");const {PythonWorkspaceClient}=require("../../../artifacts/evolution-control-build/workspace-client.js");
const run=process.env.EVOLUTION_TEST_ROOT;assert.ok(run);assert.equal(readFileSync(path.join(run,".evolution-test-run"),"utf8").replace(/^\uFEFF/,"").trim(),"evolution-a");

test("frozen trusted Python worker inspects only the configured fixture scope",{skip:process.env.EVOLUTION_RUN_TRUSTED_WORKER!=="1"},async()=>{
  const releaseStore=mkdtempSync(path.join(run,"real-workspace-release-"));const release=await freezeControlRelease({buildRoot:path.resolve("artifacts/evolution-control-build"),workerRoot:path.resolve("agent-core/evolution"),outputRoot:path.join(releaseStore,"releases"),nodeExecutable:process.execPath,pythonExecutable:process.env.EVOLUTION_TRUSTED_PYTHON});
  const fixture=mkdtempSync(path.join(run,"trusted-workspace-fixture-"));const source=path.join(fixture,"source");mkdirSync(source);writeFileSync(path.join(source,"main.cjs"),"module.exports = 1;\n");const state=path.join(fixture,"state");
  const client=await PythonWorkspaceClient.create({release,sandboxRoot:process.cwd(),runRoot:run});const scope={scope_id:"22222222-2222-4222-8222-222222222222",source_root:source,owned_paths:["main.cjs"],state_root:state,read_only_roots:[]};
  const result=await client.inspect(scope,{query:"summary"},"11111111-1111-4111-8111-111111111111");assert.equal(result.ok,true);assert.equal(result.scope_id,scope.scope_id);assert.equal(result.owned_file_count,1);assert.equal(result.workspace_revision,0);
});
