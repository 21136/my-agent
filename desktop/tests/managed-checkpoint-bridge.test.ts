import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { createManagedCheckpointHandler } from "../electron/managed-checkpoint-bridge.ts";

const hash = (char: string) => char.repeat(64);
const request = () => ({ format: 1 as const, mode: "managed_source_checkpoint_request_v1" as const, reviewDigest: hash("a"), proposalDigest: hash("b"), selectedPaths: ["desktop/one.ts"] });
const review = (digest = hash("a")) => ({ review_digest: digest, blockers: [{ code: "review_only", paths: ["desktop/one.ts"] }] });
const proposal = (r: any, req: any, digest = req.proposalDigest ?? hash("b")) => ({ mode: "managed_source_selection_proposal_v1", reviewDigest: r.review_digest, proposal_digest: digest, selectedPaths: [...req.selectedPaths], blockers: [{ code: "selection_only", paths: [...req.selectedPaths] }], execution: false, readyForMerge: false, readyForLaunch: false, checkpointCreated: false, nativeConsent: false });
const pack = (r: any, req: any, digest = hash("c")) => ({ format: 1, mode: "managed_selected_source_checkpoint_v1", review: r, request: req, files: [{ path: req.selectedPaths[0], mode: "100644", workingSha256: hash("d"), bytesBase64: "eA==", rawBytes: 1 }], rawBytes: 1, execution: false, readyForMerge: false, readyForLaunch: false, nativeConsent: false, checkpointDigest: digest });
function harness(overrides: Record<string, any> = {}) {
  const events: string[] = []; let trusted = true; let writes = 0;
  const deps = {
    isTrusted: () => trusted,
    isFixture: false,
    validateRequest: (value: any) => { if (value?.mode !== request().mode) throw Error("bad"); return structuredClone(value); },
    resolveRoots: async () => { events.push("roots"); return { stableRoot: "stable", sandboxRoot: "sandbox" }; },
    inspect: async () => { events.push("inspect"); return review(); },
    propose: (input: any) => { events.push("propose"); return proposal(input.review, input.request); },
    capture: (input: any) => { events.push("capture"); return pack(input.review, input.request); },
    confirm: async () => { events.push("confirm"); return true; },
    persist: () => { events.push("persist"); writes++; return { format: 1, mode: "managed_source_checkpoint_receipt_v1", reviewDigest: hash("a"), proposalDigest: hash("b"), checkpointDigest: hash("c"), selectedPaths: ["desktop/one.ts"], rawBytes: 1, storedBytes: 1, checkpointCreated: true, execution: false, readyForMerge: false, readyForLaunch: false, nativeConsent: false }; },
    ...overrides,
  };
  return { handler: createManagedCheckpointHandler(deps), deps, events, setTrusted: (v: boolean) => { trusted = v; }, writes: () => writes };
}

test("validates and detaches the request before roots; writes only after confirmation and final full recapture", async () => {
  const h = harness(); const original = request(); const result = await h.handler({}, original);
  original.selectedPaths[0] = "mutated";
  assert.equal(result.ok, true); assert.equal(h.writes(), 1);
  assert.deepEqual(h.events, ["roots", "inspect", "propose", "capture", "confirm", "inspect", "propose", "capture", "persist"]);
  assert.equal((result as any).receipt.checkpointDigest, hash("c"));
  assert.equal("files" in (result as any), false);
});

test("rechecks trusted caller after every bridge await and never writes when trust changes", async () => {
  for (const phase of ["backend", "roots", "inspect-initial", "capture-initial", "confirm", "inspect-final", "capture-final"]) {
    let h: ReturnType<typeof harness>; let call=0;
    const mutate = async (...args: any[]) => {
      const isRoots = phase === "roots";
      const isInspect = phase.startsWith("inspect");
      const isCapture = phase.startsWith("capture");
      const fn = isRoots ? async () => ({ stableRoot: "stable", sandboxRoot: "sandbox" }) : isInspect ? async () => review() : isCapture ? async () => pack(args[0].review,args[0].request) : async () => true;
      const value = await fn(...args); call++;
      const targetCall = phase.endsWith("final") ? 2 : 1;
      if (call === targetCall) h.setTrusted(false);
      return value;
    };
    h = harness({ ...(phase === "backend" ? { loadBackend: mutate } : {}), ...(phase === "roots" ? { resolveRoots: mutate } : {}), ...(phase.startsWith("inspect") ? { inspect: mutate } : {}), ...(phase.startsWith("capture") ? { capture: mutate } : {}), ...(phase === "confirm" ? { confirm: mutate } : {}) });
    const result = await h.handler({}, request()); assert.equal(result.ok, false, phase); assert.equal(h.writes(), 0, phase);
  }
});

test("rejects stale recaptured review, regenerated proposal, or checkpoint pack before persistence", async () => {
  const variants = [
    { inspect: (() => { let n=0; return async () => review(++n === 1 ? hash("a") : hash("e")); })() },
    { propose: (() => { let n=0; return (input:any) => proposal(input.review,input.request,++n===1?hash("b"):hash("e")); })() },
    { capture: (() => { let n=0; return (input:any) => pack(input.review,input.request,++n===1?hash("c"):hash("e")); })() },
  ];
  for (const variant of variants) { const h=harness(variant); const result=await h.handler({},request()); assert.equal(result.ok,false); assert.equal(h.writes(),0); }
});

test("cancel, busy, malformed arguments and untrusted/fixture callers never persist", async () => {
  const cancelled=harness({confirm:async()=>false}); assert.deepEqual(await cancelled.handler({},request()),{ok:false,message:"Checkpoint was not created."}); assert.equal(cancelled.writes(),0);
  let release!:()=>void; const busy=harness({confirm:()=>new Promise<boolean>(resolve=>{release=()=>resolve(false);})}); const first=busy.handler({},request()); await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); assert.equal((await busy.handler({},request())).ok,false); release(); await first;
  for (const args of [[],[request(),request()]]) { const h=harness(); assert.equal((await h.handler({},...args)).ok,false); assert.deepEqual(h.events,[]); }
  const denied=harness({isTrusted:()=>false}); assert.equal((await denied.handler({},request())).ok,false); assert.deepEqual(denied.events,[]);
  const fixture=harness({isFixture:true}); assert.equal((await fixture.handler({},request())).ok,false); assert.deepEqual(fixture.events,[]);
});

test("sanitizes failures and preserves the no-await final persistence boundary", async () => {
  const thrown=harness({resolveRoots:async()=>{throw new Error("private path secret");}}); const result=await thrown.handler({},request()); assert.equal(result.ok,false); assert.doesNotMatch(JSON.stringify(result),/private path secret/);
  const h=harness(); const writer=h.deps.persist; h.deps.persist=(input:any)=>{ h.events.push("persist-sync"); return writer(input); }; await h.handler({},request()); assert.deepEqual(h.events.slice(-2),["persist-sync","persist"]);
});






test("host and preload wire one bounded IPC channel with native-only confirmation and no pack serialization",()=>{
 const main=readFileSync(new URL("../electron/main.ts",import.meta.url),"utf8");
 const preload=readFileSync(new URL("../electron/preload.ts",import.meta.url),"utf8");
 const api=readFileSync(new URL("../src/api/ws.ts",import.meta.url),"utf8");
 const shell=readFileSync(new URL("../src/shells/unified/index.ts",import.meta.url),"utf8");
 const confirmUi=readFileSync(new URL("../electron/managed-confirm-dialog.ts",import.meta.url),"utf8");
 assert.match(main,/ipcMain\.handle\("shell-dev:managed-source-checkpoint"/);assert.match(main,/validateManagedCheckpointRequest/);assert.match(main,/showManagedConfirmDialog/);assert.match(main,/confirmLabel: "保存检查点"/);assert.match(main,/cancelLabel: "取消"/);assert.match(main,/nativeCheckpointConfirmSections/);assert.match(main,/persistManagedSourceCheckpoint/);
 assert.doesNotMatch(main,/JSON\.stringify\(pack/);assert.doesNotMatch(main,/bytesBase64/);assert.match(confirmUi,/counts\.set\(code/);assert.match(confirmUi,/Cancel as the safe default focus/);assert.doesNotMatch(main,/paths\.map\(String\)\.join/);
 assert.match(preload,/ipcRenderer\.invoke\("shell-dev:managed-source-checkpoint", request\)/);assert.match(api,/createManagedSourceCheckpoint\?/);assert.match(shell,/createCheckpoint: window\.myAgentDesktop/);
});


test("synchronous final capture persists before queued nonselected review drift can run",async()=>{
 let digest=hash("a"), captures=0, digestAtWriter="";
 const h=harness({
  inspect:async()=>review(digest),
  capture:(input:any)=>{captures++;if(captures===2)queueMicrotask(()=>{digest=hash("e");});return pack(input.review,input.request);},
  persist:()=>{digestAtWriter=digest;return {format:1,mode:"managed_source_checkpoint_receipt_v1",reviewDigest:hash("a"),proposalDigest:hash("b"),checkpointDigest:hash("c"),selectedPaths:["desktop/one.ts"],rawBytes:1,storedBytes:1,checkpointCreated:true,execution:false,readyForMerge:false,readyForLaunch:false,nativeConsent:false};},
 });
 const result=await h.handler({},request());assert.equal(result.ok,true);assert.equal(captures,2);assert.equal(digestAtWriter,hash("a"));assert.equal(digest,hash("e"));
});

test("Promise-returning capture implementation fails closed without confirmation or persistence",async()=>{
 const h=harness({capture:async(input:any)=>pack(input.review,input.request)});const result=await h.handler({},request());assert.equal(result.ok,false);assert.equal(h.events.includes("confirm"),false);assert.equal(h.writes(),0);
});
