import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { randomUUID } from "node:crypto";
import { existsSync, lstatSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import http from "node:http";
import path from "node:path";
import { test } from "node:test";

const require=createRequire(import.meta.url);
let startRendererServer;
try {
  ({startEvolutionTestRendererServer:startRendererServer}=require("../../../artifacts/evolution-control-build/sealed-renderer-server.js"));
} catch {
  // Expected in RED until the isolated renderer service is implemented and built.
}

function createFixtureRoot() {
  const sandboxRoot=path.resolve(process.cwd());
  const runRoot=path.resolve(process.env.EVOLUTION_TEST_ROOT || "");
  const artifactsRoot=path.join(sandboxRoot,"artifacts");
  assert.ok(runRoot && runRoot.startsWith(`${artifactsRoot}${path.sep}`),"EVOLUTION_TEST_ROOT must stay under sandbox/artifacts");
  assert.equal(readFileSync(path.join(runRoot,".evolution-test-run"),"utf8").trim(),"evolution-a");
  const fixtureRoot=path.join(runRoot,`sealed-renderer-test-${randomUUID()}`);
  mkdirSync(path.join(fixtureRoot,"dist","assets"),{recursive:true});
  writeFileSync(path.join(fixtureRoot,"dist","index.html"),"<!doctype html><script type=\"module\" src=\"/assets/app.js\"></script>");
  writeFileSync(path.join(fixtureRoot,"dist","assets","app.js"),"globalThis.sealedRenderer=true;");
  writeFileSync(path.join(fixtureRoot,"outside.txt"),"must-not-escape-renderer-root");
  return {fixtureRoot,distRoot:path.join(fixtureRoot,"dist")};
}

function get(url,pathName) {
  return new Promise((resolve,reject)=>http.get(new URL(pathName,url),response=>{
    const chunks=[];response.on("data",chunk=>chunks.push(chunk));response.on("end",()=>resolve({status:response.statusCode,body:Buffer.concat(chunks).toString("utf8")}));
  }).on("error",reject));
}

test("H1 renderer serves only sealed dist on an ephemeral loopback port",async t=>{
  assert.equal(typeof startRendererServer,"function","H1 needs an isolated sealed renderer server");
  const {fixtureRoot,distRoot}=createFixtureRoot();
  const fixtureInfo=lstatSync(fixtureRoot);
  assert.ok(fixtureInfo.isDirectory()&&!fixtureInfo.isSymbolicLink());
  const service=await startRendererServer(distRoot);
  t.after(async()=>{
    await service.close();
    const parent=path.dirname(fixtureRoot);
    assert.equal(path.resolve(parent),path.resolve(process.env.EVOLUTION_TEST_ROOT));
    assert.ok(existsSync(fixtureRoot));
    rmSync(fixtureRoot,{recursive:true,force:false});
  });

  assert.ok(service.port>0&&service.port<65536);
  assert.equal(service.origin,`http://127.0.0.1:${service.port}`);
  assert.equal(service.indexUrl,`${service.origin}/`);
  const index=await get(service.origin,"/");
  assert.equal(index.status,200);
  assert.match(index.body,/assets\/app\.js/);
  const asset=await get(service.origin,"/assets/app.js");
  assert.equal(asset.status,200);
  assert.match(asset.body,/sealedRenderer=true/);
  const traversal=await get(service.origin,"/%2e%2e/outside.txt");
  assert.ok([400,403,404].includes(traversal.status));
  assert.doesNotMatch(traversal.body,/must-not-escape-renderer-root/);
});
