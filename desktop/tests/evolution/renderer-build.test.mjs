import assert from "node:assert/strict";
import {createHash,randomUUID} from "node:crypto";
import {spawnSync} from "node:child_process";
import {copyFileSync,lstatSync,mkdtempSync,mkdirSync,readdirSync,readFileSync,realpathSync,rmSync,writeFileSync} from "node:fs";
import path from "node:path";
import {tmpdir} from "node:os";
import {test} from "node:test";
import {assertH1SpawnEnvironment,buildH1ElectronArgs,extractH1CdpEndpoint,buildH1ElectronMainOutputs,buildH1RuntimeEnvironment,createCandidateSourceFixture,freezeHostFixture,startFrozenHost,validateH1CdpEndpoint,verifyH1AgentPaths} from "../../scripts/verify-evolution-a.mjs";

const sandboxRoot=path.resolve(process.cwd());
const sha=value=>createHash("sha256").update(value).digest("hex");
function setup(){
  const runRoot=mkdtempSync(path.join(sandboxRoot,"artifacts","renderer-build-test-"));
  writeFileSync(path.join(runRoot,".evolution-test-run"),"evolution-a\n");
  const ownershipToken=`renderer-build-test:${randomUUID()}`;writeFileSync(path.join(runRoot,".renderer-build-test-owner"),ownershipToken);
  const createdIdentity=lstatSync(runRoot);
  const runtime=path.join(runRoot,"runtime");mkdirSync(path.join(runtime,"electron","resources"),{recursive:true});
  const python=path.join(runtime,"python.exe"),electron=path.join(runtime,"electron","electron.exe");writeFileSync(python,"python");writeFileSync(electron,"electron");writeFileSync(path.join(runtime,"electron","resources","resources.pak"),"resource");
  const desktop=path.join(sandboxRoot,"desktop");
  return {runRoot,python,electron,desktop,ownershipToken,createdIdentity,sandboxRoot};
}
function sandboxRootForFixture(f){return f.sandboxRoot??sandboxRoot;}
function removeOwnedRunRoot(f){
  const root=sandboxRootForFixture(f);
  const artifactsPath=path.resolve(root,"artifacts");
  const artifactsInfo=lstatSync(artifactsPath);
  if(!artifactsInfo.isDirectory()||artifactsInfo.isSymbolicLink())throw new Error("refusing cleanup: artifacts workspace is not a real directory");
  const artifactsRoot=realpathSync.native(artifactsPath);
  if((process.platform==="win32"?artifactsRoot.toLowerCase()!==artifactsPath.toLowerCase():artifactsRoot!==artifactsPath))throw new Error("refusing cleanup: artifacts workspace path was redirected");
  const absoluteRoot=path.resolve(f.runRoot);
  const isWithin=(parent,child)=>{const relative=path.relative(parent,child);return relative!==""&&relative!==".."&&!relative.startsWith(`..${path.sep}`)&&!path.isAbsolute(relative);};
  const assertOwned=()=>{
    const rootInfo=lstatSync(absoluteRoot);
    if(!rootInfo.isDirectory()||rootInfo.isSymbolicLink())throw new Error("refusing cleanup: run root is not the owned real directory");
    const finalRoot=realpathSync.native(absoluteRoot);
    const same=(a,b)=>process.platform==="win32"?a.toLowerCase()===b.toLowerCase():a===b;
    if(!same(finalRoot,absoluteRoot)||!isWithin(artifactsRoot,finalRoot)||!same(path.dirname(finalRoot),artifactsRoot))throw new Error("refusing cleanup: final path is outside the artifacts workspace");
    if(rootInfo.dev!==f.createdIdentity.dev||rootInfo.ino!==f.createdIdentity.ino||rootInfo.birthtimeMs!==f.createdIdentity.birthtimeMs)throw new Error("refusing cleanup: run root was replaced");
    const markerPath=path.join(finalRoot,".renderer-build-test-owner");
    const markerInfo=lstatSync(markerPath);
    if(!markerInfo.isFile()||markerInfo.isSymbolicLink()||!same(realpathSync.native(markerPath),markerPath)||readFileSync(markerPath,"utf8")!==f.ownershipToken)throw new Error("refusing cleanup: ownership marker is missing or changed");
    return finalRoot;
  };
  const finalRoot=assertOwned();
  // Revalidate immediately before recursive deletion to catch path replacement during checks.
  if(!samePathForTest(finalRoot,assertOwned()))throw new Error("refusing cleanup: run root changed during validation");
  rmSync(finalRoot,{recursive:true,force:false});
}
function samePathForTest(a,b){return process.platform==="win32"?a.toLowerCase()===b.toLowerCase():a===b;}
function createSyntheticAgentRoot(runRoot,{dataLinkTarget}={}){
  const appRoot=path.join(runRoot,"host","app");
  const source=path.join(sandboxRoot,"agent-core");
  for(const dir of [path.join(appRoot,"agent-core","tools"),path.join(appRoot,"workspace"),path.join(appRoot,"evolve")])mkdirSync(dir,{recursive:true});
  copyFileSync(path.join(source,"paths.py"),path.join(appRoot,"agent-core","paths.py"));
  copyFileSync(path.join(source,"tools","__init__.py"),path.join(appRoot,"agent-core","tools","__init__.py"));
  copyFileSync(path.join(source,"tools","schema.py"),path.join(appRoot,"agent-core","tools","schema.py"));
  if(dataLinkTarget){const link=path.join(appRoot,"data");const made=spawnSync(process.env.ComSpec||"cmd.exe",["/d","/c","mklink","/J",link,dataLinkTarget],{encoding:"utf8",windowsHide:true,shell:false});if(made.status!==0)throw new Error(`test junction creation failed: ${made.stderr||made.stdout}`);}else mkdirSync(path.join(appRoot,"data"));
  const profileRoot=path.join(runRoot,"host","profile");for(const dir of ["userprofile","appdata/Roaming","appdata/Local","home","xdg-config","xdg-data","xdg-cache","tmp","electron-userData","electron-sessionData"])mkdirSync(path.join(profileRoot,...dir.split("/")),{recursive:true});
  writeFileSync(path.join(appRoot,"evolve","_index.core.toml"),"[core]\n");
  writeFileSync(path.join(appRoot,"evolve","_index.toml"),"# synthetic legacy discovery marker\n");
  return appRoot;
}
function factories(){return {
  freezeControlRelease:async({outputRoot,pythonExecutable})=>{const root=path.join(outputRoot,"fixture-release");mkdirSync(root,{recursive:true});const manifest_path=path.join(root,"manifest.json");writeFileSync(manifest_path,"{}\n");return{root,manifest_path,release_id:"fixture-release"};},
  capturePythonEnvironment:async executable=>({executable,digest:`sha256:${sha("python-env")}`}),
};}

test("H1 Electron bundle accepts a test-injected read-only esbuild API without resolving checkout node_modules",async t=>{
  const f=setup();t.after(()=>removeOwnedRunRoot(f));
  const entry=process.env.H1_TEST_ESBUILD_ENTRY;assert.ok(path.isAbsolute(entry));
  const info=lstatSync(entry);assert.ok(info.isFile()&&!info.isSymbolicLink());assert.equal(realpathSync.native(entry),path.resolve(entry));
  const esbuild=createRequire(import.meta.url)(entry);let calls=0;
  const built=await buildH1ElectronMainOutputs({sandboxRoot,runRoot:f.runRoot},{build:async options=>{calls++;return esbuild.build(options);}});
  assert.equal(calls,2);assert.deepEqual(built.files.map(item=>item.path).sort(),["main.js","preload.js"]);
  assert.ok(built.files.every(item=>item.byte_length>0&&/^[a-f0-9]{64}$/.test(item.sha256)));
});
test("run-local renderer build emits a fresh, attested tree without touching desktop/dist",async t=>{
  const f=setup();t.after(()=>removeOwnedRunRoot(f));
  const existing=readFileSync(path.join(f.desktop,"dist","index.html"));
  const frozen=await freezeHostFixture({sandboxRoot,runRoot:f.runRoot,trustedPythonExecutable:f.python,electronExecutable:f.electron},{factories:factories(),rendererBuild:true});
  const manifest=JSON.parse(readFileSync(frozen.manifest_path,"utf8"));
  assert.match(manifest.build_provenance,/run-local-vite-renderer/);
  assert.ok(readdirSync(f.runRoot).some(name=>name.startsWith("t9-renderer-build-")));
  assert.ok(readFileSync(path.join(frozen.renderer_root,"index.html")).length>0);
  assert.deepEqual(readFileSync(path.join(f.desktop,"dist","index.html")),existing);
});

test("freezeHostFixture rejects renderer builder output outside this marked run",async t=>{
  const f=setup();t.after(()=>removeOwnedRunRoot(f));
  const outside=path.join(sandboxRoot,"desktop","dist");
  await assert.rejects(freezeHostFixture({sandboxRoot,runRoot:f.runRoot,trustedPythonExecutable:f.python,electronExecutable:f.electron},{factories:{...factories(),buildRenderer:async()=>({output_root:outside,files:[],source_digest:`sha256:${sha("fake")}`})},rendererBuild:true}),/renderer build output must stay inside/);
});


test("freezeHostFixture rejects a forged in-run renderer source attestation",async t=>{
  const f=setup();t.after(()=>removeOwnedRunRoot(f));
  const root=path.join(f.runRoot,"t9-renderer-build-forged");mkdirSync(root);writeFileSync(path.join(root,"index.html"),"<html>fake</html>");
  await assert.rejects(freezeHostFixture({sandboxRoot,runRoot:f.runRoot,trustedPythonExecutable:f.python,electronExecutable:f.electron},{factories:{...factories(),buildRenderer:async()=>({output_root:root,files:[{path:"index.html",byte_length:18,sha256:sha("<html>fake</html>")}],source_digest:`sha256:${sha("wrong source")}`})},rendererBuild:true}),/source digest does not match current sandbox sources/);
});
test("legacy freezeHostFixture call still seals existing dist for T4",async t=>{
  const f=setup();t.after(()=>removeOwnedRunRoot(f));
  const frozen=await freezeHostFixture({sandboxRoot,runRoot:f.runRoot,trustedPythonExecutable:f.python,electronExecutable:f.electron,candidateFixtureProfile:"t4-recovery-node-v1"},{factories:factories(),rendererBuild:true});
  assert.equal(readFileSync(path.join(frozen.renderer_root,"index.html"),"utf8"),readFileSync(path.join(f.desktop,"dist","index.html"),"utf8"));
});





test("renderer test cleanup refuses a missing or changed ownership marker",()=>{
  const f=setup();const marker=path.join(f.runRoot,".renderer-build-test-owner");
  writeFileSync(marker,"replaced-owner");
  try { assert.throws(()=>removeOwnedRunRoot(f),/ownership marker/); }
  finally { writeFileSync(marker,f.ownershipToken);removeOwnedRunRoot(f); }
});



test("renderer test cleanup refuses a changed captured directory identity",()=>{
  const f=setup();const identity=f.createdIdentity;f.createdIdentity={...identity,ino:identity.ino+1};
  try { assert.throws(()=>removeOwnedRunRoot(f),/run root was replaced/); }
  finally { f.createdIdentity=identity;removeOwnedRunRoot(f); }
});

test("default control release receives the marked run context without caller EVOLUTION_TEST_ROOT",async t=>{
  const f=setup();t.after(()=>removeOwnedRunRoot(f));
  const previous=process.env.EVOLUTION_TEST_ROOT;const originalCwd=process.cwd();process.env.EVOLUTION_TEST_ROOT="caller-context-sentinel";process.chdir(path.join(sandboxRoot,"desktop"));
  try {
    const asynchronousObserver=new Promise(resolve=>setImmediate(()=>resolve({cwd:process.cwd(),runRoot:process.env.EVOLUTION_TEST_ROOT})));
    const freezePromise=freezeHostFixture({sandboxRoot,runRoot:f.runRoot,trustedPythonExecutable:f.python,electronExecutable:f.electron},{factories:{capturePythonEnvironment:factories().capturePythonEnvironment},rendererBuild:true});
    const [frozen,observed]=await Promise.all([freezePromise,asynchronousObserver]);
    assert.deepEqual(observed,{cwd:path.join(sandboxRoot,"desktop"),runRoot:"caller-context-sentinel"});
    const profile=JSON.parse(readFileSync(path.join(path.dirname(frozen.profile_root),"evolution-test-profile.json"),"utf8"));
    assert.ok(profile.control_release_root.startsWith(path.join(f.runRoot,"control-releases")));
    assert.ok(readdirSync(f.runRoot).some(name=>name.startsWith("t9-renderer-build-")));
    assert.equal(process.cwd(),path.join(sandboxRoot,"desktop"));
    assert.equal(process.env.EVOLUTION_TEST_ROOT,"caller-context-sentinel");
  } finally { process.chdir(originalCwd);if(previous===undefined)delete process.env.EVOLUTION_TEST_ROOT;else process.env.EVOLUTION_TEST_ROOT=previous; }
});




test("parallel default freezes serialize cwd and run-root environment context",async t=>{
  const a=setup(),b=setup();t.after(()=>{removeOwnedRunRoot(a);removeOwnedRunRoot(b);});
  const previous=process.env.EVOLUTION_TEST_ROOT;const originalCwd=process.cwd();process.env.EVOLUTION_TEST_ROOT="parallel-caller-context";process.chdir(path.join(sandboxRoot,"desktop"));
  try {
    const options={factories:{capturePythonEnvironment:factories().capturePythonEnvironment}};
    const [left,right]=await Promise.all([
      freezeHostFixture({sandboxRoot,runRoot:a.runRoot,trustedPythonExecutable:a.python,electronExecutable:a.electron},options),
      freezeHostFixture({sandboxRoot,runRoot:b.runRoot,trustedPythonExecutable:b.python,electronExecutable:b.electron},options),
    ]);
    const profileFor=result=>JSON.parse(readFileSync(path.join(path.dirname(result.profile_root),"evolution-test-profile.json"),"utf8"));
    assert.ok(profileFor(left).control_release_root.startsWith(path.join(a.runRoot,"control-releases")));
    assert.ok(profileFor(right).control_release_root.startsWith(path.join(b.runRoot,"control-releases")));
    assert.equal(process.cwd(),path.join(sandboxRoot,"desktop"));
    assert.equal(process.env.EVOLUTION_TEST_ROOT,"parallel-caller-context");
  } finally { process.chdir(originalCwd);if(previous===undefined)delete process.env.EVOLUTION_TEST_ROOT;else process.env.EVOLUTION_TEST_ROOT=previous; }
});


test("H1 Electron bundle keeps sandbox preload free of Node URL require banner",async t=>{
  const f=setup();t.after(()=>removeOwnedRunRoot(f));
  const built=await buildH1ElectronMainOutputs({sandboxRoot,runRoot:f.runRoot});
  const main=readFileSync(built.main_path,"utf8"),preload=readFileSync(built.preload_path,"utf8");
  assert.match(main,/require\(['"]node:url['"]\)\.pathToFileURL\(__filename\)/);
  assert.doesNotMatch(preload,/require\(['"]node:url['"]\)/);
  assert.equal(preload.includes("__h1_import_meta_url"),false);
});

test("H1 freeze creates synthetic AgentPaths markers and isolated roots while preserving T4",async t=>{
  const f=setup();t.after(()=>removeOwnedRunRoot(f));
  const frozen=await freezeHostFixture({sandboxRoot,runRoot:f.runRoot,trustedPythonExecutable:f.python,electronExecutable:f.electron,candidateFixtureProfile:"t4-recovery-node-v1"},{factories:factories()});
  const root=frozen.app_root;
  for(const relative of ["evolve/_index.core.toml","evolve/_index.toml","data","workspace"]){assert.ok(lstatSync(path.join(root,...relative.split("/"))),`${relative} should be sealed`);}
  assert.equal(JSON.parse(readFileSync(path.join(path.dirname(frozen.profile_root),"evolution-test-profile.json"),"utf8")).candidate_fixture_profile,"t4-recovery-node-v1");
});

test("prelaunch AgentPaths check overrides inherited MY_AGENT_ROOT and binds every root to sealed app",t=>{
  const f=setup();t.after(()=>removeOwnedRunRoot(f));
  const appRoot=createSyntheticAgentRoot(f.runRoot);
  const python=process.env.H1_TEST_PYTHON_EXECUTABLE||path.resolve(sandboxRoot,"../..",".venv","Scripts","python.exe");
  const previous=process.env.MY_AGENT_ROOT;process.env.MY_AGENT_ROOT=sandboxRoot;
  try {
    const actual=verifyH1AgentPaths({sandboxRoot,runRoot:f.runRoot,appRoot,pythonExecutable:python,profileRoot:path.join(f.runRoot,"host","profile")});
    assert.deepEqual(actual,{agent_root:appRoot,data:path.join(appRoot,"data"),workspace:path.join(appRoot,"workspace"),evolve:path.join(appRoot,"evolve")});
  } finally {if(previous===undefined)delete process.env.MY_AGENT_ROOT;else process.env.MY_AGENT_ROOT=previous;}
});

test("isolated Python -I AgentPaths preflight does not create sealed-app bytecode",t=>{
  const f=setup();t.after(()=>removeOwnedRunRoot(f));
  const appRoot=createSyntheticAgentRoot(f.runRoot);
  const python=path.resolve(sandboxRoot,"../..",".venv","Scripts","python.exe");
  const profileRoot=path.join(f.runRoot,"host","profile");
  verifyH1AgentPaths({sandboxRoot,runRoot:f.runRoot,appRoot,pythonExecutable:python,profileRoot});
  const pycFiles=[];
  const visit=directory=>{for(const entry of readdirSync(directory,{withFileTypes:true})){const full=path.join(directory,entry.name);if(entry.isDirectory())visit(full);else if(entry.isFile()&&entry.name.endsWith(".pyc"))pycFiles.push(path.relative(appRoot,full).replace(/\\/g,"/"));}};
  visit(path.join(appRoot,"agent-core"));
  assert.deepEqual(pycFiles,[],"isolated preflight must not mutate the sealed app with bytecode");
});
test("prelaunch AgentPaths check rejects a symlinked data root on an NTFS test volume",t=>{
  const sandbox=mkdtempSync(path.join(tmpdir(),"h1-agentpaths-sandbox-"));mkdirSync(path.join(sandbox,"artifacts"));
  const runRoot=mkdtempSync(path.join(sandbox,"artifacts","renderer-build-test-"));
  writeFileSync(path.join(runRoot,".evolution-test-run"),"evolution-a\n");
  const ownershipToken=`renderer-build-test:${randomUUID()}`;writeFileSync(path.join(runRoot,".renderer-build-test-owner"),ownershipToken);
  const createdIdentity=lstatSync(runRoot);const f={sandboxRoot:sandbox,runRoot,ownershipToken,createdIdentity};t.after(()=>removeOwnedRunRoot(f));
  const outside=path.join(runRoot,"outside-data");mkdirSync(outside);
  const appRoot=createSyntheticAgentRoot(runRoot,{dataLinkTarget:outside});
  const python=path.resolve(sandboxRoot,"../..",".venv","Scripts","python.exe");
  assert.throws(()=>verifyH1AgentPaths({sandboxRoot:sandbox,runRoot,appRoot,pythonExecutable:python,profileRoot:path.join(runRoot,"host","profile")}),/data root|symlink|real directory/);
});






test("H1 native spawn env pins sealed root and profile paths despite inherited root",t=>{
  const f=setup();t.after(()=>removeOwnedRunRoot(f));const appRoot=createSyntheticAgentRoot(f.runRoot);const profileRoot=path.join(f.runRoot,"host","profile");
  const previous=process.env.MY_AGENT_ROOT;process.env.MY_AGENT_ROOT=sandboxRoot;
  try { const env=buildH1RuntimeEnvironment(profileRoot,appRoot);assert.equal(env.MY_AGENT_ROOT,appRoot);assertH1SpawnEnvironment(env,appRoot,profileRoot);assert.ok(env.USERPROFILE.startsWith(profileRoot));assert.equal(env.MY_AGENT_ROOT===sandboxRoot,false); }
  finally {if(previous===undefined)delete process.env.MY_AGENT_ROOT;else process.env.MY_AGENT_ROOT=previous;}
});

test("custom H1 spawn is refused after real AgentPaths preflight",async t=>{
  const f=setup();t.after(()=>removeOwnedRunRoot(f));
  const python=path.resolve(sandboxRoot,"../..",".venv","Scripts","python.exe");
  const frozen=await freezeHostFixture({sandboxRoot,runRoot:f.runRoot,trustedPythonExecutable:python,electronExecutable:f.electron,candidateFixtureProfile:"t4-recovery-node-v1"},{factories:factories(),rendererBuild:true});
  createCandidateSourceFixture({sandboxRoot,runRoot:f.runRoot});
  let spawned=false;
  await assert.rejects(startFrozenHost({sandboxRoot,runRoot:f.runRoot,...frozen},{capturePythonEnvironment:executable=>({executable,digest:`sha256:${sha("python-env")}`}),spawnProcess:()=>{spawned=true;throw new Error("must not spawn");}}),/custom spawnProcess is forbidden/);
  assert.equal(spawned,false);
});





test("CDP launcher accepts only fixed localhost and dynamic port arguments",()=>{
  const app=path.join(sandboxRoot,"artifacts","marked-run","host","app","desktop");
  assert.deepEqual(buildH1ElectronArgs(app),[app]);
  assert.deepEqual(buildH1ElectronArgs(app,{enabled:true}),["--remote-debugging-address=127.0.0.1","--remote-debugging-port=0",app]);
  for(const hostile of [{enabled:true,address:"0.0.0.0"},{enabled:true,host:"0.0.0.0"},{enabled:true,port:9222},{enabled:true,args:["--remote-debugging-address=0.0.0.0"]},{enabled:false}]) assert.throws(()=>buildH1ElectronArgs(app,hostile),/CDP options/);
  assert.equal(validateH1CdpEndpoint("ws://127.0.0.1:49152/devtools/browser/a_b-1"),"ws://127.0.0.1:49152/devtools/browser/a_b-1");
  assert.equal(extractH1CdpEndpoint("[debug]\\nDevTools listening on ws://127.0.0.1:49152/devtools/browser/a_b-1"),"ws://127.0.0.1:49152/devtools/browser/a_b-1");
  for(const hostile of ["ws://0.0.0.0:49152/devtools/browser/id","ws://127.0.0.1:0/devtools/browser/id","http://127.0.0.1:49152/devtools/browser/id","ws://127.0.0.1:49152/devtools/page/id"]) assert.throws(()=>validateH1CdpEndpoint(hostile),/CDP endpoint/);
});


