import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { cpSync, lstatSync, mkdtempSync, readFileSync, realpathSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { runManagedSourceAnalysis } from "../electron/managed-source-analysis-worker-client.mjs";
const desktop=path.resolve(path.dirname(fileURLToPath(import.meta.url)),"..");
const fixtureBase=path.resolve(path.dirname(fileURLToPath(import.meta.url)),"../../artifacts/managed-source-analysis-worker-fixtures");mkdirSync(fixtureBase,{recursive:true});
function git(cwd,...args){return execFileSync("git",["-C",cwd,"-c","commit.gpgsign=false","-c","core.hooksPath=NUL",...args],{env:{...process.env,GIT_CONFIG_NOSYSTEM:"1",GIT_TERMINAL_PROMPT:"0"},encoding:"utf8",windowsHide:true,stdio:["ignore","pipe","pipe"]}).trim();}
function fixture(t){const root=mkdtempSync(path.join(fixtureBase,"run-"));assert.equal(path.dirname(root),fixtureBase);assert.equal(realpathSync(fixtureBase),fixtureBase);t.after(()=>{const target=path.resolve(root);assert.equal(path.dirname(target),fixtureBase);assert.equal(realpathSync(target),target);const st=lstatSync(target);assert.ok(st.isDirectory()&&!st.isSymbolicLink()&&!(st.attributes&0x400));rmSync(target,{recursive:true,force:false});});
 const launcher=path.join(root,"desktop","launcher");mkdirSync(launcher,{recursive:true});for(const file of ["managed-review.mjs","managed-source-plan.mjs","managed-source-inventory.mjs","managed-source-checkpoint.mjs","managed-source-selection.mjs","managed-selected-merge.mjs","managed-clean-git-metadata.mjs","managed-readonly-sandbox-index.mjs","managed-selected-merge-store.mjs","managed-stable-preservation.mjs"])cpSync(path.join(desktop,"launcher",file),path.join(launcher,file));
 const electron=path.join(root,"desktop","electron");mkdirSync(electron,{recursive:true});for(const file of ["managed-source-analysis-worker.mjs","managed-source-analysis-worker-client.mjs","managed-selected-merge-authorization.mjs"])cpSync(path.join(desktop,"electron",file),path.join(electron,file));
 const reviewFile=path.join(launcher,"managed-review.mjs");let source=readFileSync(reviewFile,"utf8");source=`import { appendFileSync } from "node:fs";\n`+source.replace("export async function inspectManagedReview({ stableRoot, sandboxRoot } = {}) {\n  return await buildReview({ stableRoot, sandboxRoot });",`export async function inspectManagedReview({ stableRoot, sandboxRoot } = {}) {\n  appendFileSync(${JSON.stringify(path.join(root,"review-calls.log"))}, "review\\n");\n  return await buildReview({ stableRoot, sandboxRoot });`);writeFileSync(reviewFile,source);
 writeFileSync(path.join(root,".gitignore"),".worktrees/\n");writeFileSync(path.join(root,"desktop","base.mjs"),"export const base=true;\n");git(root,"init","-b","main");git(root,"config","user.name","Analysis Worker Fixture");git(root,"config","user.email","analysis@example.invalid");git(root,"add",".gitignore","desktop");git(root,"commit","-m","baseline");
 const sandbox=path.join(root,".worktrees","sandbox");mkdirSync(path.dirname(sandbox),{recursive:true});git(root,"worktree","add","-b","experiment/sandbox",sandbox);writeFileSync(path.join(sandbox,"desktop","selected.mjs"),"export const selected=1;\n");git(sandbox,"add","desktop/selected.mjs");git(sandbox,"commit","-m","selected");return{root,sandbox};}
async function run(f,operation,extra={},allow=true){let authorizations=0;const result=await runManagedSourceAnalysis({codeRoot:f.root,operation,roots:{stableRoot:f.root,sandboxRoot:f.sandbox},...extra,isTrusted:()=>{authorizations++;return allow;}});return{result,authorizations};}
test("real Git fixture routes identity and review off-thread, then prepares twice-bound plan only for exact fixture roots",async t=>{
 const f=fixture(t);const identity=await run(f,"identity");assert.equal(identity.authorizations,1);assert.equal(identity.result.identity.stable.identity.worktreeRoot,f.root);assert.equal(identity.result.identity.sandbox.identity.worktreeRoot,f.sandbox);
 const reviewed=await run(f,"review");assert.equal(reviewed.authorizations,1);const review=reviewed.result.report;assert.equal(review.stable.identity.worktreeRoot,f.root);assert.equal(review.sandbox.identity.worktreeRoot,f.sandbox);
 const sm=await import(pathToFileURL(path.join(f.root,"desktop","launcher","managed-source-selection.mjs")).href);const cp=await import(pathToFileURL(path.join(f.root,"desktop","launcher","managed-source-checkpoint.mjs")).href);const selectedPaths=["desktop/selected.mjs"];const proposal=sm.createManagedSourceSelectionProposal({review,request:{format:1,mode:"managed_source_selection_request_v1",reviewDigest:review.review_digest,selectedPaths}});const request={format:1,mode:"managed_source_checkpoint_request_v1",reviewDigest:review.review_digest,proposalDigest:proposal.proposal_digest,selectedPaths};const checkpoint=cp.captureManagedSourceCheckpoint({review,request,stableRoot:f.root,sandboxRoot:f.sandbox});
 const prepared=await run(f,"prepare",{checkpoint});assert.equal(prepared.authorizations,1);assert.equal(prepared.result.plan.stable.worktreeRoot,f.root);assert.equal(prepared.result.plan.sandbox.worktreeRoot,f.sandbox);assert.deepEqual(prepared.result.plan.selectedPaths,selectedPaths);
 const head=git(f.sandbox,"rev-parse","HEAD");await assert.rejects(run(f,"prepare",{checkpoint},false),/worker failed/);assert.equal(git(f.sandbox,"rev-parse","HEAD"),head);
 writeFileSync(path.join(f.root,"desktop","selected.mjs"),"host collision\n");
 await assert.rejects(run(f,"prepare",{checkpoint}),error=>error.code==="selected_untracked_collision"&&JSON.stringify(error.paths)==="[\"desktop/selected.mjs\"]"&&!/private|secret|bytesBase64/.test(error.message));
});


test("stable main routes selection, checkpoint and merge hot paths through the intended workers", () => {
 const main=readFileSync(path.join(desktop,"electron","main.ts"),"utf8");
 const selection=main.slice(main.indexOf("const managedSelectionHandler"),main.indexOf("let managedCheckpointBackend"));
 const checkpoint=main.slice(main.indexOf("const managedCheckpointHandler"),main.indexOf('ipcMain.handle("shell-dev:managed-source-checkpoint"'));
 const merge=main.slice(main.indexOf("const managedSelectedMergeHandler"),main.indexOf('ipcMain.handle("shell-dev:managed-selected-merge"'));
 assert.match(selection,/resolveManagedReviewRoots/);assert.match(selection,/operation: "identity"/);assert.match(selection,/operation: "review"/);
 assert.match(checkpoint,/resolveManagedReviewRoots/);assert.match(checkpoint,/operation: "identity"/);assert.match(checkpoint,/operation: "review"/);
 assert.match(merge,/resolveManagedReviewRoots/);assert.match(merge,/operation: "identity"/);assert.match(merge,/operation: "prepare"/);
 assert.match(merge,/managed-selected-merge-worker\.mjs/);assert.match(merge,/mode: "managed_selected_merge_worker_v1"/);
});

