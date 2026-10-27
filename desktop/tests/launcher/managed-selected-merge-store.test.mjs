import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdirSync, writeFileSync, existsSync, lstatSync, realpathSync, readdirSync, unlinkSync, rmdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { test } from 'node:test';
import { claimSelectedMergeStore, readSelectedMergeEvidence } from '../../launcher/managed-selected-merge-store.mjs';
import { validateManagedSourceCheckpoint } from '../../launcher/managed-source-checkpoint.mjs';
import { createManagedSourceSelectionProposal } from '../../launcher/managed-source-selection.mjs';

const canonical = (v) => Array.isArray(v) ? `[${v.map(canonical).join(',')}]` : v && typeof v === 'object' ? `{${Object.keys(v).sort().map(k => `${JSON.stringify(k)}:${canonical(v[k])}`).join(',')}}` : JSON.stringify(v);
const sha = (v) => createHash('sha256').update(v).digest('hex');
const oid = (n) => n.toString(16).padStart(2, '0').repeat(20);
const plan = (n) => { const p = { format: 1, mode: 'managed_selected_merge_plan_v1', checkpointDigest: sha(`checkpoint-${n}`), selectedPaths: ['desktop/approved.ts'], stable: { head: oid(n + 2) }, expectedCommit: oid(n + 3), expectedMergedTree: oid(n + 4) }; p.planDigest = sha(canonical(p)); return p; };
const receiptFor = (p) => { const r = { format: 1, mode: 'managed_selected_merge_receipt_v1', status: 'source_merge_verified', planDigest: p.planDigest, checkpointDigest: p.checkpointDigest, selectedPaths: [...p.selectedPaths], stableHeadBefore: p.stable.head, stableHeadAfter: p.expectedCommit, mergedTree: p.expectedMergedTree, sourceMergeExecuted: true, sourceHistoryMerged: false, readyForLaunch: false }; r.receiptDigest = sha(canonical(r)); return r; };
const SANDBOX_ROOT = realpathSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..'));
const FIXTURE_PARENT = path.join(SANDBOX_ROOT, 'artifacts', 'managed-adoption-execution-20261025', 'store-fixtures');
const FIXTURE_NAMES = new Set(['success','same-plan','unknown-global','new-after-unknown','parallel','receipt-mutation','unsafe','trust','limit-eight','engine-allowlist','plan-seal','locked-rescan','reserve-overflow','durable-evidence','plan-preservation-invalid']);
const HASH_NAME = /^[a-f0-9]{64}$/;
function contained(base, target) {
  const rel = path.relative(base, target);
  if (rel === '..' || rel.startsWith(`..${path.sep}`) || path.isAbsolute(rel)) throw new Error('fixture path escaped sandbox workspace');
}
function ordinaryDirectory(target) {
  const s = lstatSync(target);
  if (!s.isDirectory() || s.isSymbolicLink() || (process.platform === 'win32' && (s.attributes & 0x400))) throw new Error('fixture directory is not ordinary');
  if (realpathSync(target) !== target) throw new Error('fixture directory realpath mismatch');
}
function ordinaryFile(target) {
  const s = lstatSync(target);
  if (!s.isFile() || s.isSymbolicLink() || (typeof s.nlink === 'number' && s.nlink !== 1) || (process.platform === 'win32' && (s.attributes & 0x400))) throw new Error('fixture file is not an ordinary single-link file');
}
function cleanupFixture(root) {
  if (!existsSync(root)) return;
  contained(FIXTURE_PARENT, root); ordinaryDirectory(root);
  for (const top of readdirSync(root)) {
    const item = path.join(root, top); contained(root, item);
    if (top === 'not-a-directory') { ordinaryFile(item); unlinkSync(item); continue; }
    if (top !== 'artifacts') throw new Error('unknown fixture entry retained');
    ordinaryDirectory(item);
    for (const child of readdirSync(item)) {
      if (child !== 'managed-selected-merges') throw new Error('unknown fixture artifact retained');
      const store = path.join(item, child); ordinaryDirectory(store);
      for (const entry of readdirSync(store)) {
        const entryPath = path.join(store, entry); contained(store, entryPath);
        if (entry === '.coordinator.lock' || entry === 'mystery.bin') { ordinaryFile(entryPath); unlinkSync(entryPath); continue; }
        if (entry === 'foreign-directory') { ordinaryDirectory(entryPath); if (readdirSync(entryPath).length) throw new Error('unknown foreign fixture data retained'); rmdirSync(entryPath); continue; }
        if (!HASH_NAME.test(entry)) throw new Error('unknown fixture operation retained');
        ordinaryDirectory(entryPath);
        for (const file of readdirSync(entryPath)) {
          if (!['intent.json','receipt.json','temporary.index','plan.json','checkpoint.json','stable-index.before','checkpoint.extra'].includes(file)) throw new Error('unknown operation fixture file retained');
          const filePath = path.join(entryPath, file); contained(entryPath, filePath); ordinaryFile(filePath); unlinkSync(filePath);
        }
        rmdirSync(entryPath);
      }
      rmdirSync(store);
    }
    rmdirSync(item);
  }
  rmdirSync(root);
}
function ensureWorkspaceCwd() {
  const cwd = realpathSync(process.cwd()); contained(SANDBOX_ROOT, cwd);
}
function ensureDirectory(target) {
  contained(SANDBOX_ROOT, target);
  const rel = path.relative(SANDBOX_ROOT, target);
  let current = SANDBOX_ROOT;
  for (const component of rel.split(path.sep).filter(Boolean)) {
    current = path.join(current, component); contained(SANDBOX_ROOT, current);
    if (existsSync(current)) ordinaryDirectory(current); else mkdirSync(current);
  }
}
const makeRoot = (name) => {
  ensureWorkspaceCwd();
  if (!FIXTURE_NAMES.has(name)) throw new Error('fixture name is not allowlisted');
  const root = path.join(FIXTURE_PARENT, name); contained(FIXTURE_PARENT, root);
  ensureDirectory(FIXTURE_PARENT); cleanupFixture(root); ensureDirectory(root);
  return root;
};
const trusted = () => true;
const writeCanonical = (file, value) => writeFileSync(file, `${canonical(value)}\n`, { flag: 'wx' });
function durableFixture(root, n = 60) {
  const bytes = Buffer.from('small durable checkpoint evidence');
  const sha256 = sha(bytes);
  const blobOid = createHash('sha1').update(Buffer.from(`blob ${bytes.length}\0`)).update(bytes).digest('hex');
  const selectionPath = 'desktop/evidence.mjs';
  const inventory = { format: 1, mode: 'managed_allowed_source_inventory_v1', root: path.join(root, 'sandbox'), head: oid(n + 20), indexDigest: sha('index'), entries: [{ path: selectionPath, headOid: blobOid, headMode: '100644', workingSha256: sha256, workingOid: blobOid, staged: false, rawDiverged: false, modeDiverged: false, state: 'clean', mode: '100644', size: bytes.length }], excluded: [], blockers: [], execution: false, readyForMerge: false, readyForLaunch: false };
  inventory.inventory_digest = sha(Buffer.from(canonical(inventory)));
  const side = (base, head) => ({ identity: { worktreeRoot: path.join(root, base), gitDir: path.join(root, base, '.git'), commonDir: path.join(root, '.git') }, head, branch: 'fixture', indexDigest: sha('index'), inventory });
  const review = { format: 1, mode: 'managed_source_review_v1', inspectionDigest: sha('inspection'), stable: side('stable', oid(n + 21)), sandbox: side('sandbox', oid(n + 20)), history: { mergeBase: oid(n + 22), commits: [], paths: [] }, effectiveDelta: { basis: 'merge_base_to_sandbox_head', paths: [] }, blockers: [{ code: 'review_only_not_execution', paths: [] }], execution: false, readyForMerge: false, readyForLaunch: false };
  review.stable.inventory = structuredClone(inventory); review.stable.inventory.root = review.stable.identity.worktreeRoot; review.stable.inventory.head = review.stable.head; delete review.stable.inventory.inventory_digest; review.stable.inventory.inventory_digest = sha(Buffer.from(canonical(review.stable.inventory)));
  review.sandbox.inventory = structuredClone(inventory); review.sandbox.inventory.root = review.sandbox.identity.worktreeRoot; review.sandbox.inventory.head = review.sandbox.head; delete review.sandbox.inventory.inventory_digest; review.sandbox.inventory.inventory_digest = sha(Buffer.from(canonical(review.sandbox.inventory)));
  review.review_digest = sha(Buffer.from(canonical(review)));
  const request = { format: 1, mode: 'managed_source_checkpoint_request_v1', reviewDigest: review.review_digest, proposalDigest: '', selectedPaths: [selectionPath] };
  request.proposalDigest = createManagedSourceSelectionProposal({ review, request: { format: 1, mode: 'managed_source_selection_request_v1', reviewDigest: request.reviewDigest, selectedPaths: request.selectedPaths } }).proposal_digest;
  const checkpointRequest = { ...request };
  const fileEntry = review.sandbox.inventory.entries[0];
  const checkpoint = { format: 1, mode: 'managed_selected_source_checkpoint_v1', review, request: checkpointRequest, files: [{ ...fileEntry, bytesBase64: bytes.toString('base64') }], execution: false, readyForMerge: false, readyForLaunch: false, nativeConsent: false, rawBytes: bytes.length };
  checkpoint.checkpointDigest = sha(Buffer.from(canonical(checkpoint)));
  const validated = validateManagedSourceCheckpoint(checkpoint);
  const p = plan(n); p.selectedPaths=[selectionPath];p.checkpointDigest = validated.checkpointDigest; p.stable.rawIndexSha256 = sha(Buffer.alloc(0)); delete p.planDigest; p.planDigest = sha(canonical(p));
  return { plan: p, checkpoint: validated, stableIndex: Buffer.alloc(0) };
}
const seedSuccess = (root, p) => { const dir = path.join(root, 'artifacts', 'managed-selected-merges', p.planDigest); mkdirSync(dir, { recursive: true }); const intent = { format: 1, mode: 'managed_selected_merge_intent_v1', state: 'started', planDigest: p.planDigest, checkpointDigest: p.checkpointDigest, selectedPaths: p.selectedPaths, stableHeadBefore: p.stable.head, stableHeadAfter: p.expectedCommit, mergedTree: p.expectedMergedTree }; writeCanonical(path.join(dir, 'intent.json'), intent); writeCanonical(path.join(dir, 'receipt.json'), receiptFor(p)); };

 test('claim creates durable intent, binds plan, and finish writes verified receipt', () => {
  const root = makeRoot('success'); const p = plan(1); const claim = claimSelectedMergeStore({ sandboxRoot: root, plan: p, assertTrusted: trusted });
  assert.equal(claim.directory, path.join(root, 'artifacts', 'managed-selected-merges', p.planDigest));
  assert.equal(claim.intentPath, path.join(claim.directory, 'intent.json'));
  const receipt = receiptFor(p); claim.finish(receipt);
  assert.equal(claimSelectedMergeStore({ sandboxRoot: root, plan: plan(2), assertTrusted: trusted }).directory.endsWith(plan(2).planDigest), true);
 });

test('same plan is permanently single-use after success', () => {
  const root = makeRoot('same-plan'); const p = plan(3); const c = claimSelectedMergeStore({ sandboxRoot: root, plan: p, assertTrusted: trusted }); c.finish(receiptFor(p));
  assert.throws(() => claimSelectedMergeStore({ sandboxRoot: root, plan: p, assertTrusted: trusted }), /maintenance|used|claim/i);
});

test('unknown global store entry blocks every plan before any new operation directory', () => {
  const root = makeRoot('unknown-global'); const store = path.join(root, 'artifacts', 'managed-selected-merges'); mkdirSync(store, { recursive: true }); writeFileSync(path.join(store, 'mystery.bin'), 'x');
  assert.throws(() => claimSelectedMergeStore({ sandboxRoot: root, plan: plan(4), assertTrusted: trusted }), /maintenance|unknown|store/i);
  assert.equal(existsSync(path.join(store, plan(4).planDigest)), false);
});

test('unknown old entry blocks a different new plan', () => {
  const root = makeRoot('new-after-unknown'); const store = path.join(root, 'artifacts', 'managed-selected-merges'); mkdirSync(store, { recursive: true }); mkdirSync(path.join(store, 'foreign-directory'));
  assert.throws(() => claimSelectedMergeStore({ sandboxRoot: root, plan: plan(5), assertTrusted: trusted }), /maintenance|unknown|store/i);
});

test('exclusive coordinator lock rejects parallel claim and is released only by owner finish', () => {
  const root = makeRoot('parallel'); const p = plan(6); const first = claimSelectedMergeStore({ sandboxRoot: root, plan: p, assertTrusted: trusted });
  assert.throws(() => claimSelectedMergeStore({ sandboxRoot: root, plan: plan(7), assertTrusted: trusted }), /lock|busy|maintenance/i);
  first.finish(receiptFor(p));
  const next = claimSelectedMergeStore({ sandboxRoot: root, plan: plan(7), assertTrusted: trusted }); next.fail('source_merge_failed');
  assert.throws(() => claimSelectedMergeStore({ sandboxRoot: root, plan: plan(8), assertTrusted: trusted }), /lock|maintenance/i);
});

test('receipt mutation and mismatched expected fields are rejected without releasing lock', () => {
  const root = makeRoot('receipt-mutation'); const p = plan(9); const c = claimSelectedMergeStore({ sandboxRoot: root, plan: p, assertTrusted: trusted });
  const bad = receiptFor(p); bad.mergedTree = oid(8);
  assert.throws(() => c.finish(bad), /receipt|invalid|match|maintenance/i);
  assert.throws(() => claimSelectedMergeStore({ sandboxRoot: root, plan: plan(10), assertTrusted: trusted }), /lock|maintenance/i);
});

test('rejects a non-directory sandbox root before trust-guarded store writes', () => {
  const base = makeRoot('unsafe'); const fileRoot = path.join(base, 'not-a-directory'); writeFileSync(fileRoot, 'x');
  let guards = 0;
  assert.throws(() => claimSelectedMergeStore({ sandboxRoot: fileRoot, plan: plan(11), assertTrusted: () => { guards++; return true; } }), /root|link|unsafe|sandbox/i);
  assert.equal(guards, 0);
});

test('trust guard is the final check before creating any store state', () => {
  const root = makeRoot('trust'); let calls = 0;
  assert.throws(() => claimSelectedMergeStore({ sandboxRoot: root, plan: plan(12), assertTrusted: () => { calls++; return false; } }), /trust/i);
  assert.equal(calls, 1); assert.equal(existsSync(path.join(root, 'artifacts')), false);
});



test('operation directory limit is enforced at eight', () => {
  const root = makeRoot('limit-eight');
  for (let i = 20; i < 28; i++) { const p = plan(i); const c = claimSelectedMergeStore({ sandboxRoot: root, plan: p, assertTrusted: trusted }); c.finish(receiptFor(p)); }
  assert.throws(() => claimSelectedMergeStore({ sandboxRoot: root, plan: plan(28), assertTrusted: trusted }), /maintenance|limit|store/i);
});




test('selection paths use the engine validator allowlist rather than a local prefix grammar', () => {
  const root = makeRoot('engine-allowlist'); const p = plan(30); p.selectedPaths = ['evolve/tools/tool file.mjs']; delete p.planDigest; p.planDigest = sha(canonical(p));
  const claim = claimSelectedMergeStore({ sandboxRoot: root, plan: p, assertTrusted: trusted });
  claim.finish(receiptFor(p));
});

test('plan digest must seal the canonical plan body', () => {
  const root = makeRoot('plan-seal'); const p = plan(31); p.expectedMergedTree = oid(99);
  assert.throws(() => claimSelectedMergeStore({ sandboxRoot: root, plan: p, assertTrusted: trusted }), /plan|invalid/i);
  assert.equal(existsSync(path.join(root, 'artifacts')), false);
});

test('malformed present stable preservation is rejected before store creation even when resealed', () => {
  const root = makeRoot('plan-preservation-invalid'); const p = plan(32);
  p.stablePreservation = {};
  delete p.planDigest; p.planDigest = sha(canonical(p));
  assert.throws(() => claimSelectedMergeStore({ sandboxRoot: root, plan: p, assertTrusted: trusted }), /plan|preservation|invalid/i);
  assert.equal(existsSync(path.join(root, 'artifacts')), false);
});


test('claim rescans operation count while holding its exclusive lock', () => {
  const root = makeRoot('locked-rescan');
  for (let i = 40; i < 47; i++) { const p = plan(i); const c = claimSelectedMergeStore({ sandboxRoot: root, plan: p, assertTrusted: trusted }); c.finish(receiptFor(p)); }
  const latePlan = plan(48);
  assert.throws(() => claimSelectedMergeStore({ sandboxRoot: root, plan: latePlan, assertTrusted: () => {
    const competing = plan(47); const c = claimSelectedMergeStore({ sandboxRoot: root, plan: competing, assertTrusted: trusted }); c.finish(receiptFor(competing)); return true;
  } }), /maintenance|store|lock/i);
  assert.equal(existsSync(path.join(root, 'artifacts', 'managed-selected-merges', latePlan.planDigest)), false);
});

test('finish enforces the reserved temporary-index budget and retains lock on overflow', () => {
  const root = makeRoot('reserve-overflow'); const p = plan(49);
  const c = claimSelectedMergeStore({ sandboxRoot: root, plan: p, assertTrusted: trusted, reserveBytes: 64 });
  writeFileSync(path.join(c.directory, 'temporary.index'), Buffer.alloc(65));
  assert.throws(() => c.finish(receiptFor(p)), /maintenance|store|reservation/i);
  assert.throws(() => claimSelectedMergeStore({ sandboxRoot: root, plan: plan(50), assertTrusted: trusted }), /maintenance|store|lock/i);
});

test('durable bundle is canonical, digest-bound, and present before claim returns', () => {
  const root = makeRoot('durable-evidence'); const bundle = durableFixture(root);
  bundle.plan.sandbox={worktreeRoot:root};delete bundle.plan.planDigest;bundle.plan.planDigest=sha(canonical(bundle.plan));
  const claim = claimSelectedMergeStore({ sandboxRoot: root, plan: bundle.plan, assertTrusted: trusted, durableBundle: { checkpoint: bundle.checkpoint, stableIndex: bundle.stableIndex } });
  const evidenceNames = ['checkpoint.json', 'plan.json', 'stable-index.before'];
  assert.deepEqual(readdirSync(claim.directory).sort(), ['intent.json', ...evidenceNames].sort());
  assert.equal(readFileSync(path.join(claim.directory, 'plan.json'), 'utf8'), `${canonical(bundle.plan)}\n`);
  assert.equal(readFileSync(path.join(claim.directory, 'checkpoint.json'), 'utf8'), `${canonical(bundle.checkpoint)}\n`);
  assert.deepEqual(readFileSync(path.join(claim.directory, 'stable-index.before')), bundle.stableIndex);
  const intent = JSON.parse(readFileSync(claim.intentPath, 'utf8'));
  assert.equal(intent.mode, 'managed_selected_merge_intent_v2');
  assert.equal(intent.planFileSha256, sha(Buffer.from(`${canonical(bundle.plan)}\n`)));
  assert.equal(intent.checkpointFileSha256, sha(Buffer.from(`${canonical(bundle.checkpoint)}\n`)));
  assert.equal(intent.stableIndexBeforeSha256, sha(bundle.stableIndex));
  assert.throws(()=>readSelectedMergeEvidence({sandboxRoot:root,planDigest:bundle.plan.planDigest}),/lock|maintenance/i);
  claim.finish(receiptFor(bundle.plan));
  assert.deepEqual(readSelectedMergeEvidence({sandboxRoot:root,planDigest:bundle.plan.planDigest}),{plan:bundle.plan,receipt:receiptFor(bundle.plan)});
  const foreignIntent={...intent,expectedStableHeadAfter:oid(99)};
  const foreignReceipt={...receiptFor(bundle.plan),stableHeadAfter:oid(99)};delete foreignReceipt.receiptDigest;foreignReceipt.receiptDigest=sha(canonical(foreignReceipt));
  writeFileSync(claim.intentPath,canonical(foreignIntent)+'\n');writeFileSync(path.join(claim.directory,'receipt.json'),canonical(foreignReceipt)+'\n');
  assert.throws(()=>readSelectedMergeEvidence({sandboxRoot:root,planDigest:bundle.plan.planDigest}),/maintenance|evidence|digest/i);
  writeFileSync(claim.intentPath,canonical(intent)+'\n');writeFileSync(path.join(claim.directory,'receipt.json'),canonical(receiptFor(bundle.plan))+'\n');
  const nextPlan = plan(61); const nextClaim = claimSelectedMergeStore({ sandboxRoot: root, plan: nextPlan, assertTrusted: trusted });
  assert.equal(nextClaim.directory.endsWith(nextPlan.planDigest), true); nextClaim.finish(receiptFor(nextPlan));
  assert.throws(()=>readSelectedMergeEvidence({sandboxRoot:root,planDigest:nextPlan.planDigest}),/durable_evidence_required/i);
  writeFileSync(path.join(claim.directory, 'plan.json'), '{}\n');
  assert.throws(()=>readSelectedMergeEvidence({sandboxRoot:root,planDigest:bundle.plan.planDigest}),/maintenance|evidence|digest/i);
  assert.throws(() => claimSelectedMergeStore({ sandboxRoot: root, plan: plan(62), assertTrusted: trusted }), /maintenance|evidence|digest/i);
  writeFileSync(path.join(claim.directory, 'plan.json'), `${canonical(bundle.plan)}\n`);
  unlinkSync(path.join(claim.directory, 'checkpoint.json'));
  assert.throws(() => claimSelectedMergeStore({ sandboxRoot: root, plan: plan(64), assertTrusted: trusted }), /maintenance|evidence|store/i);
  writeFileSync(path.join(claim.directory, 'checkpoint.json'), `${canonical(bundle.checkpoint)}\n`);
  writeFileSync(path.join(claim.directory, 'checkpoint.extra'), 'unexpected');
  assert.throws(() => claimSelectedMergeStore({ sandboxRoot: root, plan: plan(65), assertTrusted: trusted }), /maintenance|evidence|store/i);
});

test('durable bundle rejects checkpoint or raw stable-index mismatch before store creation', () => {
  const root = makeRoot('durable-evidence'); const bundle = durableFixture(root, 63);
  assert.throws(() => claimSelectedMergeStore({ sandboxRoot: root, plan: bundle.plan, assertTrusted: trusted, durableBundle: { checkpoint: bundle.checkpoint, stableIndex: Buffer.from('different') } }), /evidence|index|digest|invalid/i);
  assert.equal(existsSync(path.join(root, 'artifacts')), false);
});

