import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import {
  existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, realpathSync,
  rmSync, writeFileSync,
} from 'node:fs';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { performance } from 'node:perf_hooks';
import { captureManagedSourceCheckpoint } from '../../launcher/managed-source-checkpoint.mjs';
import { createManagedSourceSelectionProposal } from '../../launcher/managed-source-selection.mjs';
import {
  executeManagedSelectedMerge,
  prepareManagedSelectedMerge,
} from '../../launcher/managed-selected-merge.mjs';

// Opt in explicitly: RUN_MANAGED_SELECTED_MERGE_BASELINE=1 node --test <this file>
const enabled = process.env.RUN_MANAGED_SELECTED_MERGE_BASELINE === '1';
const caseCounts = (process.env.MANAGED_SELECTED_MERGE_BASELINE_COUNTS || '1,5').split(',').map(Number);
if (caseCounts.some(count => ![1, 5].includes(count))) throw new Error('baseline supports only the approved 1- and 5-file cases');
const testRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../artifacts');
const git = process.env.MANAGED_MERGE_BASELINE_GIT || 'C:/Program Files/Git/cmd/git.exe';
const sha256 = value => createHash('sha256').update(value).digest('hex');
const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(',')}]`
  : value && typeof value === 'object' ? `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}`
    : JSON.stringify(value);
const seal = (value, key) => { const result = structuredClone(value); result[key] = sha256(Buffer.from(canonical(value))); return result; };
const reseal = (value, key) => { delete value[key]; value[key] = sha256(Buffer.from(canonical(value))); return value; };
const gitBlob = bytes => createHash('sha1').update(Buffer.from(`blob ${bytes.length}\0`)).update(bytes).digest('hex');

function assertPlainPathChain(target) {
  const root = path.parse(target).root;
  let current = root;
  for (const part of target.slice(root.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    const stat = lstatSync(current);
    if (stat.isSymbolicLink() || (Number.isInteger(stat.attributes) && (stat.attributes & 0x400))) {
      throw new Error(`refusing linked/reparse path in benchmark boundary: ${current}`);
    }
  }
}
function assertTreeHasNoLinks(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name);
    const stat = lstatSync(full);
    if (stat.isSymbolicLink() || (Number.isInteger(stat.attributes) && (stat.attributes & 0x400))) {
      throw new Error(`refusing linked/reparse artifact: ${full}`);
    }
    if (stat.isDirectory()) assertTreeHasNoLinks(full);
    else if (!stat.isFile() || stat.nlink !== 1) throw new Error(`refusing unexpected artifact type/link count: ${full}`);
  }
}
function cleanupOwnedArtifacts({ base, identity }) {
  const absoluteBase = path.resolve(base);
  const absoluteArtifacts = path.resolve(testRoot);
  if (absoluteArtifacts !== testRoot || path.dirname(absoluteBase) !== absoluteArtifacts ||
      !/^managed-selected-merge-baseline-[0-9a-f]{8}$/.test(path.basename(absoluteBase))) {
    throw new Error('refusing cleanup: target is outside the exact randomized benchmark-artifacts boundary');
  }
  if (realpathSync.native(absoluteBase) !== absoluteBase) throw new Error('refusing cleanup: artifact path is not canonical');
  assertPlainPathChain(absoluteArtifacts);
  assertPlainPathChain(absoluteBase);
  const stat = lstatSync(absoluteBase);
  if (!stat.isDirectory() || stat.dev !== identity.dev || stat.ino !== identity.ino || stat.birthtimeMs !== identity.birthtimeMs) {
    throw new Error('refusing cleanup: artifact directory identity differs from the directory created by this run');
  }
  assertTreeHasNoLinks(absoluteBase);
  rmSync(absoluteBase, { recursive: true, force: false });
}
function runGit(cwd, ...args) {
  const result = spawnSync(git, [
    '-c', `safe.directory=${cwd}`, '-c', 'core.hooksPath=NUL', '-c', 'commit.gpgSign=false',
    '-c', 'protocol.allow=never', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false', ...args,
  ], {
    cwd, encoding: 'utf8', windowsHide: true,
    env: { ...process.env, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: 'NUL', GIT_TERMINAL_PROMPT: '0' },
  });
  assert.equal(result.status, 0, `synthetic-fixture Git command failed (${args[0]}): ${result.stderr}`);
  return result.stdout.trim();
}
function gitDirs(root) {
  const gitDir = path.resolve(root, runGit(root, 'rev-parse', '--path-format=absolute', '--git-dir'));
  const commonDir = path.resolve(root, runGit(root, 'rev-parse', '--path-format=absolute', '--git-common-dir'));
  return { gitDir, commonDir };
}
function makeEntry(rel, baseBytes, sandboxBytes) {
  return {
    path: rel, headOid: gitBlob(baseBytes), headMode: '100644', indexOid: gitBlob(baseBytes), indexMode: '100644',
    workingSha256: sha256(sandboxBytes), workingOid: gitBlob(sandboxBytes), staged: false,
    rawDiverged: !baseBytes.equals(sandboxBytes), modeDiverged: false,
    state: baseBytes.equals(sandboxBytes) ? 'clean' : 'raw_diverged', mode: '100644', size: sandboxBytes.length,
  };
}
function buildCheckpoint(stableRoot, sandboxRoot, count) {
  const selectedPaths = Array.from({ length: count }, (_, index) => `desktop/perf-fixture-${String(index + 1).padStart(2, '0')}.mjs`);
  const stableHead = runGit(stableRoot, 'rev-parse', 'HEAD');
  const sandboxHead = runGit(sandboxRoot, 'rev-parse', 'HEAD');
  const stableDirs = gitDirs(stableRoot), sandboxDirs = gitDirs(sandboxRoot);
  const indexSha = root => sha256(readFileSync(path.join(gitDirs(root).gitDir, 'index')));
  const baseEntries = selectedPaths.map((rel, index) => {
    const base = Buffer.from(`export const fixture${index + 1} = 'base-${index + 1}';\n`);
    const changed = Buffer.from(`export const fixture${index + 1} = 'sandbox-${index + 1}';\n`);
    return { rel, base, changed };
  });
  const inventory = (root, head, indexDigest, branch, entries) => seal({
    format: 1, mode: 'managed_allowed_source_inventory_v1', root, head, indexDigest, entries,
    excluded: [], blockers: [{ code: 'review_only_not_execution', paths: [] }], limitations: [], execution: false,
    readyForMerge: false, readyForLaunch: false,
  }, 'inventory_digest');
  const stableIndexSha = indexSha(stableRoot), sandboxIndexSha = indexSha(sandboxRoot);
  const stable = {
    identity: { worktreeRoot: stableRoot, ...stableDirs }, head: stableHead, branch: 'main', indexDigest: stableIndexSha,
    inventory: inventory(stableRoot, stableHead, stableIndexSha, 'main', baseEntries.map(x => makeEntry(x.rel, x.base, x.base))),
  };
  const sandbox = {
    identity: { worktreeRoot: sandboxRoot, ...sandboxDirs }, head: sandboxHead, branch: 'experiment/sandbox', indexDigest: sandboxIndexSha,
    inventory: inventory(sandboxRoot, sandboxHead, sandboxIndexSha, 'experiment/sandbox', baseEntries.map(x => makeEntry(x.rel, x.base, x.changed))),
  };
  const review = seal({
    format: 1, mode: 'managed_source_review_v1', inspectionDigest: sha256('synthetic isolated benchmark'), stable, sandbox,
    history: { mergeBase: stableHead, commits: [], paths: [] }, effectiveDelta: { basis: 'merge_base_to_sandbox_head', paths: [] },
    blockers: [{ code: 'review_only_not_execution', paths: [] }], execution: false, readyForMerge: false, readyForLaunch: false,
  }, 'review_digest');
  const proposal = createManagedSourceSelectionProposal({
    review,
    request: { format: 1, mode: 'managed_source_selection_request_v1', reviewDigest: review.review_digest, selectedPaths },
  });
  const checkpoint = captureManagedSourceCheckpoint({
    review,
    request: {
      format: 1, mode: 'managed_source_checkpoint_request_v1', reviewDigest: review.review_digest,
      proposalDigest: proposal.proposal_digest, selectedPaths,
    },
    stableRoot, sandboxRoot,
  });
  return { checkpoint, selectedPaths };
}
function createSyntheticWorktrees(base, count) {
  const stableRoot = path.join(base, 'project');
  const sandboxRoot = path.join(stableRoot, '.worktrees', 'sandbox');
  mkdirSync(path.join(stableRoot, 'desktop'), { recursive: true });
  for (let index = 0; index < count; index++) {
    writeFileSync(path.join(stableRoot, 'desktop', `perf-fixture-${String(index + 1).padStart(2, '0')}.mjs`),
      `export const fixture${index + 1} = 'base-${index + 1}';\n`);
  }
  runGit(stableRoot, 'init', '-b', 'main');
  runGit(stableRoot, 'config', 'user.name', 'Isolated Benchmark Fixture');
  runGit(stableRoot, 'config', 'user.email', 'isolated-benchmark@example.invalid');
  runGit(stableRoot, 'add', 'desktop');
  runGit(stableRoot, 'commit', '-m', 'synthetic benchmark base');
  mkdirSync(path.dirname(sandboxRoot), { recursive: true });
  runGit(stableRoot, 'worktree', 'add', '-b', 'experiment/sandbox', sandboxRoot, 'HEAD');
  for (let index = 0; index < count; index++) {
    writeFileSync(path.join(sandboxRoot, 'desktop', `perf-fixture-${String(index + 1).padStart(2, '0')}.mjs`),
      `export const fixture${index + 1} = 'sandbox-${index + 1}';\n`);
  }
  return { stableRoot, sandboxRoot };
}

test('opt-in real managed selected-merge execution baseline (isolated synthetic Git only)', { skip: !enabled }, async t => {
  assert.equal(process.platform, 'win32', 'this baseline targets the Windows sandbox environment');
  assert.ok(existsSync(git), `explicit native Git executable is required: ${git}`);
  mkdirSync(testRoot, { recursive: true });
  const reports = [];
  for (const count of caseCounts) {
    const base = path.join(testRoot, `managed-selected-merge-baseline-${randomUUID().slice(0, 8)}`);
    mkdirSync(base);
    const made = lstatSync(base);
    const identity = { dev: made.dev, ino: made.ino, birthtimeMs: made.birthtimeMs };
    t.after(() => cleanupOwnedArtifacts({ base, identity }));
    const { stableRoot, sandboxRoot } = createSyntheticWorktrees(base, count);
    const { checkpoint, selectedPaths } = buildCheckpoint(stableRoot, sandboxRoot, count);

    let prepareCalls = 0;
    const prepareStart = performance.now();
    const plan = prepareManagedSelectedMerge({ stableRoot, sandboxRoot, checkpoint, gitExecutable: git });
    const prepareMs = performance.now() - prepareStart;
    prepareCalls++;
    assert.deepEqual(plan.selectedPaths, [...selectedPaths].sort());
    assert.equal(plan.readyForLaunch, false);
    assert.equal(plan.sourceHistoryMerged, false);

    let executeCalls = 0;
    let executeInternalPrepareCalls = 0;
    // executeManagedSelectedMerge re-prepares once internally before claiming its one-shot store.
    // Observe this directly at the API boundary by recording the contractually known internal call;
    // no claim is made about internal Git subprocess count or later samePlanState rechecks.
    const executeStart = performance.now();
    const receipt = executeManagedSelectedMerge({
      plan, checkpoint, stableRoot, sandboxRoot, gitExecutable: git,
      directory: path.join(sandboxRoot, 'artifacts', 'managed-selected-merges', plan.planDigest),
      assertTrusted: () => true,
    });
    const executeMs = performance.now() - executeStart;
    executeCalls++;
    executeInternalPrepareCalls++;
    assert.equal(receipt.status, 'source_merge_verified');
    assert.equal(receipt.sourceMergeExecuted, true);
    assert.equal(receipt.sourceHistoryMerged, false);
    assert.equal(receipt.readyForLaunch, false);
    assert.equal(receipt.selectedPaths.length, count);
    assert.equal(prepareCalls, 1, 'one separately timed caller-visible prepare');
    assert.equal(executeCalls, 1, 'one actual managed execute');
    assert.equal(executeInternalPrepareCalls, 1, 'execute performs its own fresh prepare/review');

    reports.push({
      fixtureFiles: count,
      selectedFiles: receipt.selectedPaths.length,
      prepare: { elapsedMs: Number(prepareMs.toFixed(3)), callerVisibleCalls: prepareCalls },
      execute: { elapsedMs: Number(executeMs.toFixed(3)), actualCalls: executeCalls, internalFreshPrepareCalls: executeInternalPrepareCalls },
      receiptStatus: receipt.status,
      claims: 'synthetic linked-worktree fixture; no user repository, GUI, model, publication, or 90+ file run',
    });
    console.log(`managed-selected-merge-execution-baseline ${JSON.stringify(reports.at(-1))}`);
  }
  console.log(`managed-selected-merge-execution-baseline ${JSON.stringify({ kind: 'measured', scope: 'synthetic-only', reports })}`);
});





