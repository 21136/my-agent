import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readFileSync, existsSync, unlinkSync, rmSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { electronImportMetaOptions } from '../../scripts/managed-build-child.mjs';
import { captureManagedSourceCheckpoint } from '../../launcher/managed-source-checkpoint.mjs';
import { createManagedSourceSelectionProposal } from '../../launcher/managed-source-selection.mjs';
import {
  validateManagedSelectedMergeRequest,
  prepareManagedSelectedMerge,
  executeManagedSelectedMerge,
} from '../../launcher/managed-selected-merge.mjs';

const digest = 'a'.repeat(64);

test('merge request accepts only a format-1 checkpoint digest binding', () => {
  assert.deepEqual(validateManagedSelectedMergeRequest({ format: 1, mode: 'managed_selected_merge_request_v1', checkpointDigest: digest }), {
    format: 1, mode: 'managed_selected_merge_request_v1', checkpointDigest: digest,
  });
  for (const bad of [
    null,
    { format: 2, mode: 'managed_selected_merge_request_v1', checkpointDigest: digest },
    { format: 1, mode: 'wrong', checkpointDigest: digest },
    { format: 1, mode: 'managed_selected_merge_request_v1', checkpointDigest: 'A'.repeat(64) },
    { format: 1, mode: 'managed_selected_merge_request_v1', checkpointDigest: digest, readyForLaunch: true },
  ]) assert.throws(() => validateManagedSelectedMergeRequest(bad));
});

test('prepare rejects invalid checkpoint before accessing roots or Git', () => {
  assert.throws(() => prepareManagedSelectedMerge({
    stableRoot: 'not-a-root', sandboxRoot: 'not-a-root', checkpoint: {}, gitExecutable: 'git',
  }), /checkpoint/i);
});

test('execute fails closed unless supplied plan is a non-launchable canonical plan', () => {
  assert.throws(() => executeManagedSelectedMerge({
    plan: {}, checkpoint: {}, stableRoot: 'not-a-root', sandboxRoot: 'not-a-root',
    gitExecutable: 'git', directory: 'artifacts/managed-adoption-execution-20261025/fixture',
    assertTrusted: () => true,
  }));
});

const fixtureRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../artifacts/managed-adoption-execution-20261025/fixture');
const helperProofRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../artifacts/managed-final-integration-20261025');
const hash = value => createHash('sha256').update(value).digest('hex');
const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(',')}]` : value && typeof value === 'object' ? `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}` : JSON.stringify(value);
const seal = (value, key) => { const copy = structuredClone(value); copy[key] = hash(Buffer.from(canonical(value))); return copy; };
const reseal = (value,key) => { delete value[key]; value[key]=hash(Buffer.from(canonical(value))); return value; };
function checkpointFor(review,roots,paths){review=structuredClone(review);review.review_digest=undefined;delete review.review_digest;reseal(review,'review_digest');const proposal=createManagedSourceSelectionProposal({review,request:{format:1,mode:'managed_source_selection_request_v1',reviewDigest:review.review_digest,selectedPaths:paths}});const request={format:1,mode:'managed_source_checkpoint_request_v1',reviewDigest:review.review_digest,proposalDigest:proposal.proposal_digest,selectedPaths:paths};return captureManagedSourceCheckpoint({review,request,stableRoot:roots.stableRoot,sandboxRoot:roots.sandboxRoot});}
const FIXTURE_GIT = 'C:\\Program Files\\Git\\cmd\\git.exe';
function git(cwd, ...args) { if(!path.resolve(cwd).startsWith(fixtureRoot+path.sep))throw new Error('fixture Git write escaped the verified synthetic root'); const env={PATH:path.dirname(FIXTURE_GIT),SYSTEMROOT:process.env.SYSTEMROOT,WINDIR:process.env.WINDIR,TEMP:process.env.TEMP,TMP:process.env.TMP,HOME:fixtureRoot,USERPROFILE:fixtureRoot,GIT_CONFIG_NOSYSTEM:'1',GIT_CONFIG_GLOBAL:'NUL',GIT_TERMINAL_PROMPT:'0',LC_ALL:'C'}; const cfg=['-c',`safe.directory=${cwd}`,'-c','core.hooksPath=NUL','-c','commit.gpgSign=false','-c','protocol.allow=never','-c','gc.auto=0','-c','maintenance.auto=false']; const r = spawnSync(FIXTURE_GIT, [...cfg,...args], { cwd,env,encoding: 'utf8', windowsHide: true }); if (r.status !== 0) throw new Error(`fixture git failed: ${r.stderr}`); return r.stdout.trim(); }
function blob(bytes) { return createHash('sha1').update(Buffer.from(`blob ${bytes.length}\0`)).update(bytes).digest('hex'); }
function dirs(root) {
  const gitDir = path.resolve(root, git(root, 'rev-parse', '--path-format=absolute', '--git-dir'));
  const commonDir = path.resolve(root, git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir'));
  const index = path.resolve(root, git(root, 'rev-parse', '--path-format=absolute', '--git-path', 'index'));
  return { gitDir, commonDir, index };
}
function makeLane(root, head, indexDigest, branch, entries) {
  const d = dirs(root);
  const inventory = seal({ format: 1, mode: 'managed_allowed_source_inventory_v1', root, head, indexDigest, entries, excluded: [], blockers: [{ code: 'review_only_not_execution', paths: [] }], limitations: [], execution: false, readyForMerge: false, readyForLaunch: false }, 'inventory_digest');
  return { identity: { worktreeRoot: root, gitDir: d.gitDir, commonDir: d.commonDir }, head, branch, indexDigest, inventory };
}
function makeEntry(p, headBytes, workBytes = headBytes) {
  return { path: p, headOid: blob(headBytes), headMode: '100644', indexOid: blob(headBytes), indexMode: '100644', workingSha256: hash(workBytes), workingOid: blob(workBytes), staged: false, rawDiverged: !headBytes.equals(workBytes), modeDiverged: false, state: headBytes.equals(workBytes) ? 'clean' : 'raw_diverged', mode: '100644', size: workBytes.length };
}
function gitExecutable() { if(!existsSync(FIXTURE_GIT))throw new Error('fixed native fixture git.exe is unavailable'); return FIXTURE_GIT; }
function createFixture() {
  const stableRoot = path.join(fixtureRoot, 'project');
  const sandboxRoot = path.join(stableRoot, '.worktrees', 'sandbox');
  if(!path.resolve(stableRoot).startsWith(path.resolve(fixtureRoot)+path.sep))throw new Error('synthetic fixture path validation failed');
  const selectedPath = 'desktop/selected.mjs', otherPath = 'docs/keep.md';
  const base = Buffer.from('export const selected = "base";\n'); let selected = Buffer.from('export const selected = "sandbox4";\n');
  const docsBase = Buffer.from('kept in stable HEAD\n'), docsDirty = Buffer.from('sandbox unselected dirty\n');
  if (!existsSync(path.join(stableRoot, '.git'))) {
    mkdirSync(path.join(stableRoot, 'desktop'), { recursive: true }); mkdirSync(path.join(stableRoot, 'docs'), { recursive: true });
    writeFileSync(path.join(stableRoot, selectedPath), base); writeFileSync(path.join(stableRoot, otherPath), docsBase);
    git(stableRoot, 'init', '-b', 'main'); git(stableRoot, 'config', 'user.name', 'Fixture'); git(stableRoot, 'config', 'user.email', 'fixture@example.invalid');
    git(stableRoot, 'add', selectedPath, otherPath); git(stableRoot, 'commit', '-m', 'fixture base');
    mkdirSync(path.dirname(sandboxRoot), { recursive: true }); git(stableRoot, 'worktree', 'add', '-b', 'experiment/sandbox', sandboxRoot, 'HEAD');
    writeFileSync(path.join(sandboxRoot, selectedPath), selected); writeFileSync(path.join(sandboxRoot, otherPath), docsDirty);
    git(stableRoot,'config','branch.codex/runaway-mode-research.vscode-merge-base','refs/remotes/origin/research-base');
  }
  git(stableRoot,'config','branch.codex/runaway-mode-research.vscode-merge-base','refs/remotes/origin/research-base');
  const stableBytes=readFileSync(path.join(stableRoot,selectedPath));
  const stableMessage=git(stableRoot,'show','-s','--format=%s','HEAD');
  if(stableMessage.startsWith('Selected content from checkpoint ')){const stableOid=git(stableRoot,'rev-parse','HEAD');selected=Buffer.concat([stableBytes,Buffer.from(`// based on ${stableOid}\n`)]);}
  writeFileSync(path.join(sandboxRoot,selectedPath),selected);
  const stableHead = git(stableRoot, 'rev-parse', 'HEAD'), sandboxHead=git(sandboxRoot,'rev-parse','HEAD');
  const sandboxHeadBytes=Buffer.from(git(sandboxRoot,'show',`${sandboxHead}:${selectedPath}`));
  const stableIndex = readFileSync(dirs(stableRoot).index), sandboxIndex = readFileSync(dirs(sandboxRoot).index);
  const stableEntries = [makeEntry(selectedPath, stableBytes), makeEntry(otherPath, docsBase)];
  const sandboxEntries = [makeEntry(selectedPath, sandboxHeadBytes, selected), makeEntry(otherPath, docsBase, docsDirty)];
  const stable = makeLane(stableRoot, stableHead, hash(stableIndex), 'main', stableEntries);
  const sandbox = makeLane(sandboxRoot, sandboxHead, hash(sandboxIndex), 'experiment/sandbox', sandboxEntries);
  const review = seal({ format: 1, mode: 'managed_source_review_v1', inspectionDigest: hash('fixture inspection'), stable, sandbox, history: { mergeBase: sandboxHead, commits: [], paths: [{ path: 'llm_secrets.yaml' }] }, effectiveDelta: { basis: 'merge_base_to_sandbox_head', paths: [] }, blockers: [{ code: 'review_only_not_execution', paths: [] }, { code: 'sandbox_allowed_source_changes_require_ownership_review', paths: [otherPath] }], execution: false, readyForMerge: false, readyForLaunch: false }, 'review_digest');
  const proposal = createManagedSourceSelectionProposal({ review, request: { format: 1, mode: 'managed_source_selection_request_v1', reviewDigest: review.review_digest, selectedPaths: [selectedPath] } });
  const request = { format: 1, mode: 'managed_source_checkpoint_request_v1', reviewDigest: review.review_digest, proposalDigest: proposal.proposal_digest, selectedPaths: [selectedPath] };
  const checkpoint = captureManagedSourceCheckpoint({ review, request, stableRoot, sandboxRoot });
  return { stableRoot, sandboxRoot, checkpoint, selectedPath, stableBytes, gitExecutable: gitExecutable() };
}

test('targeted safety refusals cover attributes, staged/untracked overlap, and review-required blockers without merging', () => {
  const f=createFixture();
  const attrsInfo=path.join(dirs(f.stableRoot).commonDir,'info','attributes');
  writeFileSync(attrsInfo,'* filter=must-not-run\n');
  assert.throws(()=>prepareManagedSelectedMerge({stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot,checkpoint:f.checkpoint,gitExecutable:f.gitExecutable}),e=>e.code==='git_attributes_rejected');
  unlinkSync(attrsInfo);
  const rootAttrs=path.join(f.stableRoot,'.gitattributes');writeFileSync(rootAttrs,'* filter=must-not-run\n');
  assert.throws(()=>prepareManagedSelectedMerge({stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot,checkpoint:f.checkpoint,gitExecutable:f.gitExecutable}),e=>e.code==='git_attributes_rejected');unlinkSync(rootAttrs);
  const evolveDir=path.join(f.stableRoot,'evolve');mkdirSync(evolveDir,{recursive:true});const nestedAttrs=path.join(evolveDir,'.gitattributes');writeFileSync(nestedAttrs,'* filter=must-not-run\n');
  assert.throws(()=>prepareManagedSelectedMerge({stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot,checkpoint:f.checkpoint,gitExecutable:f.gitExecutable}),e=>e.code==='git_attributes_rejected');unlinkSync(nestedAttrs);
  const missingWorkingAttrs=path.join(f.stableRoot,'.gitattributes');writeFileSync(missingWorkingAttrs,'');git(f.stableRoot,'add','.gitattributes');unlinkSync(missingWorkingAttrs);
  assert.throws(()=>prepareManagedSelectedMerge({stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot,checkpoint:f.checkpoint,gitExecutable:f.gitExecutable}),e=>e.code==='git_attributes_rejected');git(f.stableRoot,'reset','--','.gitattributes');

  const stableFile=path.join(f.stableRoot,...f.selectedPath.split('/')),stableOriginal=readFileSync(stableFile);
  writeFileSync(stableFile,'staged-overlap');git(f.stableRoot,'add',f.selectedPath);
  assert.throws(()=>prepareManagedSelectedMerge({stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot,checkpoint:f.checkpoint,gitExecutable:f.gitExecutable}),e=>e.code==='selected_stable_staged_dirty');
  git(f.stableRoot,'reset','--',f.selectedPath);writeFileSync(stableFile,stableOriginal);

  const reviewRequired=structuredClone(f.checkpoint.review);reviewRequired.blockers.push({code:'cross_lane_case_alias_requires_review',paths:[f.selectedPath]});
  const blocked=checkpointFor(reviewRequired,{stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot},[f.selectedPath]);
  assert.throws(()=>prepareManagedSelectedMerge({stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot,checkpoint:blocked,gitExecutable:f.gitExecutable}),e=>e.code==='selected_merge_review_required');
  const unknownReview=structuredClone(f.checkpoint.review);unknownReview.blockers.push({code:'future_unmapped_review_code',paths:[]});
  const unknownCheckpoint=checkpointFor(unknownReview,{stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot},[f.selectedPath]);
  assert.throws(()=>prepareManagedSelectedMerge({stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot,checkpoint:unknownCheckpoint,gitExecutable:f.gitExecutable}),e=>e.code==='selected_merge_unknown_review_blocker');

  const newPath='desktop/new-selected.mjs',newBytes=Buffer.from('export const added = true;\n'),sandboxFile=path.join(f.sandboxRoot,...newPath.split('/')),stableCollision=path.join(f.stableRoot,...newPath.split('/'));
  writeFileSync(sandboxFile,newBytes);git(f.sandboxRoot,'add',newPath);
  try {
    const review=structuredClone(f.checkpoint.review);const sb=review.sandbox;const idx=readFileSync(dirs(f.sandboxRoot).index);sb.indexDigest=hash(idx);sb.inventory.indexDigest=sb.indexDigest;
    sb.inventory.entries.push({path:newPath,indexOid:blob(newBytes),indexMode:'100644',workingSha256:hash(newBytes),workingOid:blob(newBytes),staged:true,rawDiverged:false,modeDiverged:false,state:'staged',mode:'100644',size:newBytes.length});reseal(sb.inventory,'inventory_digest');reseal(review,'review_digest');
    const pack=checkpointFor(review,{stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot},[newPath]);writeFileSync(stableCollision,newBytes);
    assert.throws(()=>prepareManagedSelectedMerge({stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot,checkpoint:pack,gitExecutable:f.gitExecutable}),e=>e.code==='selected_untracked_collision');
  } finally {if(existsSync(stableCollision))unlinkSync(stableCollision);git(f.sandboxRoot,'reset','--',newPath);if(existsSync(sandboxFile))unlinkSync(sandboxFile);}
});

test('tiny linked-worktree fixture plans selected content while retaining unselected sandbox dirt', () => {
  const f = createFixture();
  const selectedStable=path.join(f.stableRoot,...f.selectedPath.split('/')); const original=readFileSync(selectedStable); writeFileSync(selectedStable,'overlap');
  assert.throws(()=>prepareManagedSelectedMerge({stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot,checkpoint:f.checkpoint,gitExecutable:f.gitExecutable}),e=>e.code==='selected_stable_working_dirty'); writeFileSync(selectedStable,original);
  const plan = prepareManagedSelectedMerge({ stableRoot: f.stableRoot, sandboxRoot: f.sandboxRoot, checkpoint: f.checkpoint, gitExecutable: f.gitExecutable });
  assert.equal(plan.mode, 'managed_selected_merge_plan_v1');
  assert.equal(plan.sourceHistoryMerged, false);
  assert.equal(plan.readyForLaunch, false);
  assert.deepEqual(plan.selectedPaths, [f.selectedPath]);
  assert.equal(plan.files[0].action, 'replace');
  assert.equal(plan.files[0].beforeSha256, hash(f.stableBytes));
  assert.equal(plan.files[0].expectedBaseMode, '100644');
  assert.ok(plan.preservationDigest);
  assert.ok(plan.temporaryIndexUpperBoundBytes <= 4 * 1024 * 1024);
  assert.ok(plan.stable.rawIndexSha256 && plan.sandbox.rawIndexSha256);
  const receipt=executeManagedSelectedMerge({plan,checkpoint:f.checkpoint,stableRoot:f.stableRoot,sandboxRoot:f.sandboxRoot,gitExecutable:f.gitExecutable,directory:path.join(f.sandboxRoot,'artifacts','managed-selected-merges',plan.planDigest),assertTrusted:()=>true});
  assert.deepEqual(Object.keys(receipt).sort(),['format','mode','status','planDigest','checkpointDigest','selectedPaths','stableHeadBefore','stableHeadAfter','mergedTree','sourceMergeExecuted','sourceHistoryMerged','readyForLaunch','receiptDigest'].sort());
  assert.equal(receipt.status,'source_merge_verified'); assert.equal(receipt.sourceMergeExecuted,true); assert.equal(receipt.readyForLaunch,false);
});

test('bundled selected-merge guard resolves its fixed helper in Electron-as-Node', async t => {
  const fixture = createFixture();
  mkdirSync(helperProofRoot, { recursive: true });
  const proofRoot = path.join(helperProofRoot, `helper-runtime-${randomUUID()}`);
  const dist = path.join(proofRoot, 'dist-electron');
  mkdirSync(dist, { recursive: true });
  t.after(() => rmSync(proofRoot, { recursive: true, force: true }));

  const toolchainRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
  const requireToolchain = createRequire(path.join(toolchainRoot, 'desktop', 'package.json'));
  const esbuild = requireToolchain('esbuild');
  const electron = path.join(toolchainRoot, 'desktop', 'node_modules', 'electron', 'dist', 'electron.exe');
  assert.ok(existsSync(electron), 'pinned Electron runtime is required for the subprocess proof');
  const entry = `import { prepareManagedSelectedMerge } from "./launcher/managed-selected-merge.mjs";\nimport { readFileSync } from "node:fs";\nconst input = JSON.parse(readFileSync(0, "utf8"));\nconst plan = prepareManagedSelectedMerge(input);\nprocess.stdout.write(JSON.stringify({ status: "ok", mode: plan.mode, planDigest: plan.planDigest }) + "\\n");\n`;
  const built = await esbuild.build({
    stdin: { contents: entry, sourcefile: 'main.ts', loader: 'ts', resolveDir: path.join(toolchainRoot, 'desktop') },
    bundle: true, platform: 'node', format: 'cjs', target: 'node22',
    external: ['electron'], ...electronImportMetaOptions(), write: false, logLevel: 'silent',
  });
  const main = path.join(dist, 'main.js');
  writeFileSync(main, built.outputFiles[0].contents);
  const helper = path.join(dist, 'managed-clean-git-metadata.mjs');
  const sourceHelper = path.join(toolchainRoot, 'desktop', 'launcher', 'managed-clean-git-metadata.mjs');
  const electronEnv = {
    ...Object.fromEntries(['SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'].filter(key => process.env[key]).map(key => [key, process.env[key]])),
    PATH: path.dirname(FIXTURE_GIT), ELECTRON_RUN_AS_NODE: '1', GIT_CONFIG_NOSYSTEM: '1',
    GIT_CONFIG_GLOBAL: 'NUL', GIT_TERMINAL_PROMPT: '0', HOME: proofRoot, USERPROFILE: proofRoot, LC_ALL: 'C',
  };
  const input = JSON.stringify({ stableRoot: fixture.stableRoot, sandboxRoot: fixture.sandboxRoot,
    checkpoint: fixture.checkpoint, gitExecutable: fixture.gitExecutable });
  const run = () => spawnSync(electron, [main], { cwd: proofRoot, env: electronEnv, input, encoding: 'utf8',
    windowsHide: true, timeout: 90000, maxBuffer: 2 * 1024 * 1024 });

  const missing = run();
  assert.notEqual(missing.status, 0);
  assert.match(`${missing.stdout}\n${missing.stderr}`, /git_object_store_guard_failed/);
  writeFileSync(helper, readFileSync(sourceHelper));
  const present = run();
  assert.equal(present.status, 0, present.stderr || present.stdout);
  const result = JSON.parse(present.stdout);
  assert.equal(result.status, 'ok');
  assert.equal(result.mode, 'managed_selected_merge_plan_v1');
});
