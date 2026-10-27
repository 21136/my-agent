import assert from "node:assert/strict";
import {createHash,randomUUID} from "node:crypto";
import {mkdirSync,writeFileSync} from "node:fs";
import path from "node:path";
import {createRequire} from "node:module";
import {test} from "node:test";
const require=createRequire(import.meta.url);let api={};let contracts={};try{api=require("../../../artifacts/evolution-control-build/test-host-config.js");contracts=require("../../../artifacts/evolution-control-build/contracts.js");}catch{/* RED: test-host config resolver has not been implemented. */}
const run=process.env.EVOLUTION_TEST_ROOT;assert.ok(run);
const hash=(digit)=>`sha256:${digit.repeat(64)}`;
function fixture(){
  const runRoot=path.join(run,`supervisor-host-config-${randomUUID()}`);mkdirSync(runRoot);writeFileSync(path.join(runRoot,".evolution-test-run"),"evolution-a\n");
  const hostRoot=path.join(runRoot,"host");const appRoot=path.join(hostRoot,"app");const profileRoot=path.join(hostRoot,"profile");const releaseRoot=path.join(runRoot,"control-release");
  mkdirSync(path.join(appRoot,"evolve","tool-catalog"),{recursive:true});mkdirSync(profileRoot,{recursive:true});mkdirSync(releaseRoot,{recursive:true});
  writeFileSync(path.join(appRoot,"evolve","tool-catalog","INDEX.md"),"fixture index\n");
  const manifest={format:1,files:[],runtimes:{node:{path:process.execPath,sha256:"a".repeat(64)},python:{path:process.execPath,sha256:"b".repeat(64)}}};
  writeFileSync(path.join(releaseRoot,"manifest.json"),`${contracts.canonicalJson(manifest)}\n`);
  const profile={profile_root:profileRoot,control_release_root:releaseRoot,trusted_python_executable:process.execPath,ws_port:0,renderer_port:0,fixture_scope_id:"66666666-6666-4666-8666-666666666666"};
  writeFileSync(path.join(hostRoot,"evolution-test-profile.json"),`${contracts.canonicalJson(profile)}\n`);
  return{runRoot,hostRoot,appRoot,profileRoot,releaseRoot,profile};
}
function exported(name){assert.equal(typeof api[name],"function",`test-host-config must export ${name}`);return api[name];}

test("H1 test profile resolves one run-bound fixed release and the narrow owned fixture scope",async()=>{
  const load=exported("loadEvolutionTestHostProfile");const build=exported("buildEvolutionTestSupervisorConfig");const f=fixture();
  const context=load(f.appRoot);assert.equal(context.run_root,f.runRoot);assert.equal(context.app_root,f.appRoot);assert.equal(context.profile.profile_root,f.profileRoot);
  const verified=[];const config=await build(context,{verifyControlRelease:async release=>verified.push(release)});
  assert.deepEqual(verified.map(release=>release.root),[f.releaseRoot]);assert.equal(config.sidecar_root,f.appRoot);assert.equal(config.sandbox_root,process.cwd());assert.equal(config.run_root,f.runRoot);
  assert.deepEqual(config.scopes,[{scope_id:f.profile.fixture_scope_id,source_root:f.appRoot,owned_paths:["evolve/tool-catalog/INDEX.md"],state_root:path.join(f.runRoot,"workspace-state"),read_only_roots:[]}]);
  assert.equal(config.host_bootstrap,undefined);const expectedDigest=createHash("sha256").update(require("node:fs").readFileSync(path.join(f.releaseRoot,"manifest.json"))).digest("hex");assert.equal(config.control_release.release_id,`sha256:${expectedDigest}`);
});

test("the explicit T4 recovery profile owns only its three candidate files and registers fixed runtime/profile identities",async()=>{
  const load=exported("loadEvolutionTestHostProfile");const build=exported("buildEvolutionTestSupervisorConfig");const f=fixture();
  const profilePath=path.join(f.hostRoot,"evolution-test-profile.json");
  const profile={...f.profile,candidate_fixture_profile:"t4-recovery-node-v1"};
  writeFileSync(profilePath,contracts.canonicalJson(profile)+"\n");
  for(const name of ["main.cjs","service.cjs","manifest.json"]){const target=path.join(f.appRoot,"evolve","tool-catalog","recovery",name);mkdirSync(path.dirname(target),{recursive:true});writeFileSync(target,name==="manifest.json"?'{"broken":true}\n':"fixture\n");}
  const context=load(f.appRoot);const config=await build(context,{verifyControlRelease:async()=>{}});
  assert.deepEqual(config.scopes[0].owned_paths,["evolve/tool-catalog/INDEX.md","evolve/tool-catalog/recovery/main.cjs","evolve/tool-catalog/recovery/service.cjs","evolve/tool-catalog/recovery/manifest.json"]);
  assert.deepEqual(config.runtime_paths,{"77777777-7777-4777-8777-777777777777":process.execPath});
  assert.deepEqual(config.profile_fixtures,[{id:"88888888-8888-4888-8888-888888888888",root:path.join(f.runRoot,"candidate-profile")}]);
  assert.deepEqual(config.descriptor_factories,[{artifact_id:"99999999-9999-4999-8999-999999999999",kind:"t4-recovery-node-v1",runtime_id:"77777777-7777-4777-8777-777777777777",profile_fixture_id:"88888888-8888-4888-8888-888888888888"}]);
  assert.deepEqual(config.descriptors,[]);
});

test("the T4 recovery profile rejects unrecognized fixture profile values",()=>{
  const load=exported("loadEvolutionTestHostProfile");const f=fixture();const profilePath=path.join(f.hostRoot,"evolution-test-profile.json");
  writeFileSync(profilePath,contracts.canonicalJson({...f.profile,candidate_fixture_profile:"arbitrary-root"})+"\n");
  assert.throws(()=>load(f.appRoot),/fixture profile|candidate fixture|profile/i);
});
test("a marked run cannot enable Host startup from a non-H1 app root",()=>{
  const load=exported("loadEvolutionTestHostProfile");const f=fixture();const candidateRoot=path.join(f.hostRoot,"candidate-app");mkdirSync(candidateRoot);
  assert.throws(()=>load(candidateRoot),/H1|host app|profile|root/i);
});

test("test Host profile rejects unknown fields",()=>{
  const load=exported("loadEvolutionTestHostProfile");const f=fixture();const profilePath=path.join(f.hostRoot,"evolution-test-profile.json");
  writeFileSync(profilePath,`${contracts.canonicalJson({...f.profile,owned_paths:["*"]})}\n`);
  assert.throws(()=>load(f.appRoot),/fields|profile/i);
});



test("profile and fixed control release must belong to the same marked run",()=>{
  const load=exported("loadEvolutionTestHostProfile");const f=fixture();const other=fixture();const profilePath=path.join(f.hostRoot,"evolution-test-profile.json");
  writeFileSync(profilePath,`${contracts.canonicalJson({...f.profile,control_release_root:other.releaseRoot})}\n`);
  assert.throws(()=>load(f.appRoot),/same marked|share.*run|scope/i);
});
