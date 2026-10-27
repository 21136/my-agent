import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {mkdtempSync,readFileSync,writeFileSync} from "node:fs";
import {test} from "node:test";
import {createRequire} from "node:module";
import path from "node:path";
const require=createRequire(import.meta.url);
let api={};try{api=require("../../../artifacts/evolution-control-build/control-release.js");}catch{/* RED: the T6 module has not been implemented yet. */}
const run=process.env.EVOLUTION_TEST_ROOT;assert.ok(run);assert.equal(readFileSync(path.join(run,".evolution-test-run"),"utf8").replace(/^\uFEFF/,"").trim(),"evolution-a");
const sha=(bytes)=>createHash("sha256").update(bytes).digest("hex");
function createFixture(){
  const root=mkdtempSync(path.join(run,"release-fixture-"));
  const buildRoot=path.join(root,"build");const workerRoot=path.join(root,"worker");const outputRoot=path.join(root,"releases");
  require("node:fs").mkdirSync(buildRoot);require("node:fs").mkdirSync(workerRoot);
  const files={"control-client-entry.js":"trusted helper\n","package.json":"{\"type\":\"commonjs\"}\n","supervisor-entry.js":"trusted supervisor\n"};
  const entries=[];for(const [name,content] of Object.entries(files)){const bytes=Buffer.from(content);writeFileSync(path.join(buildRoot,name),bytes);entries.push({path:name,sha256:sha(bytes),byte_length:bytes.length});}
  writeFileSync(path.join(buildRoot,".evolution-control-build.json"),JSON.stringify({owner:"my-agent-evolution-control-build-v1",format:1,files:entries}));
  for(const name of ["__init__.py","client.py","contracts.py","windows_files.py","workspace.py","workspace_worker.py"])writeFileSync(path.join(workerRoot,name),`# trusted ${name}\n`);
  return {root,buildRoot,workerRoot,outputRoot,input:{buildRoot,workerRoot,outputRoot,nodeExecutable:process.execPath,pythonExecutable:process.env.EVOLUTION_TRUSTED_PYTHON}};
}
function exported(name){assert.equal(typeof api[name],"function",`control-release must export ${name}`);return api[name];}

test("control release freeze and verification are public operations",()=>{
  assert.equal(typeof api.freezeControlRelease,"function");assert.equal(typeof api.verifyControlRelease,"function");
});

test("frozen release is independent of source edits and rejects release tampering",async()=>{
  const freeze=exported("freezeControlRelease");const verify=exported("verifyControlRelease");const fixture=createFixture();
  const release=await freeze(fixture.input);assert.match(release.release_id,/^sha256:[0-9a-f]{64}$/);assert.equal(await verify(release),undefined);
  const frozenWorker=path.join(release.root,"python","evolution","contracts.py");const frozenBefore=readFileSync(frozenWorker,"utf8");writeFileSync(path.join(fixture.workerRoot,"contracts.py"),"# changed source after freeze\n");assert.equal(await verify(release),undefined);assert.equal(readFileSync(frozenWorker,"utf8"),frozenBefore);
  writeFileSync(frozenWorker,"# tampered frozen release\n");await assert.rejects(()=>verify(release),/digest|manifest|changed|integrity/i);
});

test("freeze refuses a release destination outside the marked run",async()=>{
  const freeze=exported("freezeControlRelease");const fixture=createFixture();fixture.input.outputRoot=path.resolve(run,"..","escaped-control-releases");
  await assert.rejects(()=>freeze(fixture.input),/run|scope|outside|path/i);
});

test("actual sandbox control build and Python modules freeze into one verified release",async()=>{
  const freeze=exported("freezeControlRelease");const verify=exported("verifyControlRelease");const store=mkdtempSync(path.join(run,"actual-release-store-"));const outputRoot=path.join(store,"releases");
  const release=await freeze({buildRoot:path.resolve("artifacts/evolution-control-build"),workerRoot:path.resolve("agent-core/evolution"),outputRoot,nodeExecutable:process.execPath,pythonExecutable:process.env.EVOLUTION_TRUSTED_PYTHON});
  await verify(release);const manifest=JSON.parse(readFileSync(release.manifest_path,"utf8"));const paths=manifest.files.map(item=>item.path);
  assert.ok(paths.includes("node/control-client-entry.js"));assert.ok(paths.includes("node/supervisor-entry.js"));assert.ok(paths.includes("python/evolution/client.py"));assert.equal(paths.some(item=>item.includes("__pycache__")||item.endsWith(".pyc")),false);
});
