import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, symlinkSync, unlinkSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { test, after } from "node:test";
const fixtures = [];
after(() => {
  for (const root of fixtures) {
    if (path.dirname(root) !== path.resolve(tmpdir()) || !path.basename(root).startsWith("ma-dependency-audit-")) throw new Error("Unsafe fixture cleanup");
    rmSync(root, {recursive:true, force:true});
  }
});
import { auditDependencies } from "../scripts/dependency-audit.mjs";

function fixture() {
  const root = mkdtempSync(path.join(tmpdir(), "ma-dependency-audit-"));
  fixtures.push(root);
  const pkg = { dependencies: { demo: "^1.0.0" }, devDependencies: { electron: "^45.0.0" } };
  const packages = {
    "": pkg,
    "node_modules/demo": { version: "1.2.0" },
    "node_modules/electron": { version: "45.0.0" },
    "node_modules/linux-only": { version: "1.0.0", optional: true, os: ["linux"] },
  };
  writeFileSync(path.join(root,"package.json"), JSON.stringify(pkg));
  writeFileSync(path.join(root,"package-lock.json"), JSON.stringify({lockfileVersion:3,packages}));
  for (const [name, version] of [["demo","1.2.0"],["electron","45.0.0"]]) {
    mkdirSync(path.join(root,"node_modules",name),{recursive:true});
    writeFileSync(path.join(root,"node_modules",name,"package.json"),JSON.stringify({name,version}));
  }
  mkdirSync(path.join(root,"node_modules/electron/dist"));
  writeFileSync(path.join(root,"node_modules/electron/dist/version"),"45.0.0");
  writeFileSync(path.join(root,"node_modules/electron/dist/electron.exe"),"fixture executable");
  return root;
}

test("aligned install passes and incompatible optional package is skipped", () => {
  assert.equal(auditDependencies(fixture(), {platform:"win32", arch:"x64"}).ok,true);
});
test("detects actual installed package drift", () => {
  const root=fixture();
  writeFileSync(path.join(root,"node_modules/electron/package.json"),JSON.stringify({version:"33.4.11"}));
  const result=auditDependencies(root,{platform:"win32",arch:"x64"});
  assert.equal(result.ok,false);
  assert.ok(result.issues.some(i=>i.code==="version_mismatch" && i.package==="electron"));
});
test("detects stale Electron binary even when npm metadata matches",()=>{
  const root=fixture();
  writeFileSync(path.join(root,"node_modules/electron/dist/version"),"33.4.11");
  assert.ok(auditDependencies(root,{platform:"win32",arch:"x64"}).issues.some(i=>i.code==="electron_binary_mismatch"));
});
test("detects manifest changes without lockfile update",()=>{
  const root=fixture();
  writeFileSync(path.join(root,"package.json"),JSON.stringify({dependencies:{demo:"^2.0.0"},devDependencies:{electron:"^45.0.0"}}));
  assert.ok(auditDependencies(root,{platform:"win32",arch:"x64"}).issues.some(i=>i.code==="manifest_lock_mismatch"));
});
test("missing installation fails without installing anything",()=>{
  const root=fixture();
  const lock=JSON.parse(readFileSync(path.join(root,"package-lock.json")));
  lock.packages["node_modules/required"]={version:"1.0.0"};
  writeFileSync(path.join(root,"package-lock.json"),JSON.stringify(lock));
  assert.ok(auditDependencies(root,{platform:"win32",arch:"x64"}).issues.some(i=>i.code==="missing_package"));
});
test("CLI exits nonzero on fixture runtime drift",()=>{
  const root=fixture();
  writeFileSync(path.join(root,"node_modules/electron/dist/version"),"33.4.11");
  const cli=fileURLToPath(new URL("../scripts/dependency-audit.mjs",import.meta.url));
  const proc=spawnSync(process.execPath,[cli,"--root",root,"--json"],{encoding:"utf8"});
  assert.equal(proc.status,1);
  assert.equal(JSON.parse(proc.stdout).ok,false);
});
test("all npm serve and build entry points enforce dependency preflight",()=>{
  const pkg=JSON.parse(readFileSync(new URL("../package.json",import.meta.url)));
  for(const name of ["dev","dev:main-only","dev:preview","build","preview"]){
    assert.ok(pkg.scripts["pre"+name]?.includes("dependency-audit.mjs"),name);
  }
});

test("rejects node_modules pointing outside this desktop root",()=>{
  const root=fixture();
  const other=fixture();
  const modules=path.join(root,"node_modules");
  assert.equal(path.dirname(modules),root);
  rmSync(modules,{recursive:true,force:true});
  symlinkSync(path.join(other,"node_modules"),modules,process.platform==="win32"?"junction":"dir");
  assert.ok(auditDependencies(root,{platform:"win32",arch:"x64"}).issues.some(i=>i.code==="external_dependency_root"));
});
test("preview resolver does not borrow stable Vite when sandbox dependency is missing",async()=>{
  const root=fixture();
  mkdirSync(path.join(root,"agent-core"));
  mkdirSync(path.join(root,"desktop"));
  writeFileSync(path.join(root,"desktop/package.json"),"{}");
  const previous=process.env.MY_AGENT_SANDBOX_ROOT;
  process.env.MY_AGENT_SANDBOX_ROOT=root;
  try {
    const {resolvePreviewLanePaths}=await import("../scripts/sandbox-paths.mjs");
    const lane=resolvePreviewLanePaths();
    assert.equal(lane.previewViteBin,path.join(root,"desktop/node_modules/vite/bin/vite.js"));
    assert.equal(lane.previewConfig,path.join(root,"desktop/vite.shell-preview.config.ts"));
  } finally {
    if(previous===undefined) delete process.env.MY_AGENT_SANDBOX_ROOT;
    else process.env.MY_AGENT_SANDBOX_ROOT=previous;
  }
});
test("raw Vite entry points run preflight before starting a server",()=>{
  for(const name of ["vite.config.ts","vite.shell-preview.config.ts"]){
    const source=readFileSync(new URL("../"+name,import.meta.url),"utf8");
    assert.ok(source.includes("assertDependencies(__dirname)"),name);
  }
});

test("malformed lock metadata returns a blocked report rather than throwing",()=>{
  for(const malformed of [null, {lockfileVersion:3,packages:{"":{},"node_modules/demo":null}}]){
    const root=fixture();
    writeFileSync(path.join(root,"package-lock.json"),JSON.stringify(malformed));
    const result=auditDependencies(root,{platform:"win32",arch:"x64"});
    assert.equal(result.ok,false);
    assert.ok(result.issues.some(i=>["unsupported_lockfile","unsupported_lock_entry"].includes(i.code)));
  }
});

test("missing Electron runtime reports explicit locked-binary install without repairing it",()=>{
  const root=fixture();
  const binary=path.join(root,"node_modules/electron/dist/electron.exe");
  unlinkSync(binary);
  const result=auditDependencies(root,{platform:"win32",arch:"x64"});
  assert.equal(result.ok,false);
  assert.ok(result.issues.some(i=>i.code==="missing_electron_binary"));
  assert.match(result.repairHint,/node \.\/node_modules\/electron\/install\.js/);
  assert.equal(existsSync(binary),false,"audit must remain read-only");
});
