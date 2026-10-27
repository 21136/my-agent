import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createManagedSourceSelectionProposal, validateManagedSelectionRequest } from '../../launcher/managed-source-selection.mjs';

const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(',')}]` : value && typeof value === 'object' ? `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}` : JSON.stringify(value);
const hash = value => createHash('sha256').update(canonical(value)).digest('hex');
const H = c => c.repeat(64);
const without = (value, key) => Object.fromEntries(Object.entries(value).filter(([name]) => name !== key));
function sealed(body, key) { return { ...body, [key]: hash(body) }; }
function resealInventory(inv) { return { ...without(inv, 'inventory_digest'), inventory_digest: hash(without(inv, 'inventory_digest')) }; }
function resealReview(review) {
  review.stable.inventory = resealInventory(review.stable.inventory);
  review.sandbox.inventory = resealInventory(review.sandbox.inventory);
  return { ...without(review, 'review_digest'), review_digest: hash(without(review, 'review_digest')) };
}
function inventory(root, entries) { return sealed({ format: 1, mode: 'managed_allowed_source_inventory_v1', root, head: H('a'), indexDigest: H('b'), entries, excluded: [], limitations: [], blockers: [{ code: 'review_only_not_execution', paths: [] }], execution: false, readyForMerge: false, readyForLaunch: false }, 'inventory_digest'); }
const entry = (path, oid = H('c')) => ({ path, headOid: oid, headMode: '100644', indexOid: oid, indexMode: '100644', workingSha256: H('d'), workingOid: oid, staged: false, rawDiverged: false, modeDiverged: false, state: 'clean', mode: '100644', size: 3 });
function report() {
 const stableInv = inventory('/stable', [entry('desktop/a.mjs')]);
 const sandboxInv = inventory('/sandbox', [entry('desktop/a.mjs'), entry('desktop/b.mjs')]);
 const body = { format: 1, mode: 'managed_source_review_v1', inspectionDigest: H('e'), historyInspection: 'metadata_only_no_renames', stable: { identity: { worktreeRoot: '/stable', gitDir: '/stable/.git', commonDir: '/stable/.git' }, head: H('a'), branch: 'main', indexDigest: H('b'), inventory: stableInv }, sandbox: { identity: { worktreeRoot: '/sandbox', gitDir: '/stable/.git/worktrees/sandbox', commonDir: '/stable/.git' }, head: H('a'), branch: 'experiment/sandbox', indexDigest: H('b'), inventory: sandboxInv }, history: { mergeBase: H('a'), commits: [], paths: [] }, effectiveDelta: { basis: 'merge_base_to_sandbox_head', base: H('a'), head: H('a'), paths: [] }, blockers: [{ code: 'complete_worktree_inventory_unavailable', paths: [] }], limitations: [], execution: false, readyForMerge: false, readyForLaunch: false };
 return sealed(body, 'review_digest');
}
const request = (review, selectedPaths = ['desktop/b.mjs']) => ({ format: 1, mode: 'managed_source_selection_request_v1', reviewDigest: review.review_digest, selectedPaths });

test('validator accepts exact bounded request and returns a detached canonical request', () => {
 const r = report(); const input = request(r);
 assert.deepEqual(validateManagedSelectionRequest(input), input);
 assert.notEqual(validateManagedSelectionRequest(input), input);
});
test('validator rejects extras, duplicate/case aliases, traversal, protected paths, empty and path/count overflow', () => {
 const r = report();
 for (const bad of [ { ...request(r), roots: [] }, request(r, []), request(r, ['desktop/b.mjs', 'desktop/b.mjs']), request(r, ['desktop/B.mjs', 'desktop/b.mjs']), request(r, ['../secret']), request(r, ['workspace/private/key']), request(r, ['a'.repeat(513)]) ]) assert.throws(() => validateManagedSelectionRequest(bad));
 assert.throws(() => validateManagedSelectionRequest(request(r, Array.from({ length: 2049 }, (_, i) => `desktop/${i}.mjs`))));
});
test('proposal binds review, exact selection, exclusions, preservation, inherited blockers, and safety flags', () => {
 const review = report(); const proposal = createManagedSourceSelectionProposal({ review, request: request(review) });
 assert.equal(proposal.mode, 'managed_source_selection_proposal_v1');
 assert.equal(proposal.reviewDigest, review.review_digest);
 assert.deepEqual(proposal.selectedSandboxEntries.map(x => x.path), ['desktop/b.mjs']);
 assert.deepEqual(proposal.preservedStableEntries.map(x => x.path), ['desktop/a.mjs']);
 assert.deepEqual(proposal.excludedSandboxPaths, ['desktop/a.mjs']);
 assert.deepEqual(proposal.blockers[0], review.blockers[0]);
 for (const key of ['execution', 'readyForMerge', 'readyForLaunch', 'checkpointCreated', 'nativeConsent']) assert.equal(proposal[key], false);
 const { proposal_digest, ...body } = proposal; assert.equal(proposal_digest, hash(body));
});
test('planner rejects tampered review, request binding, unknown selections, and malformed inventory', () => {
 const review = report();
 assert.throws(() => createManagedSourceSelectionProposal({ review: { ...review, blockers: [] }, request: request(review) }));
 const badRequest = request(review); badRequest.reviewDigest = H('f'); assert.throws(() => createManagedSourceSelectionProposal({ review, request: badRequest }));
 assert.throws(() => createManagedSourceSelectionProposal({ review, request: request(review, ['desktop/unknown.mjs']) }));
 const malformed = structuredClone(review); malformed.sandbox.inventory.entries[0].workingSha256 = null;
 const sealedOuterOnly = { ...without(malformed, 'review_digest'), review_digest: hash(without(malformed, 'review_digest')) };
 assert.throws(() => createManagedSourceSelectionProposal({ review: sealedOuterOnly, request: request(sealedOuterOnly) }), /inventory digest/i);
});
test('stable raw changes remain blockers and missing counterparts are not treated as absence proof', () => {
 const review = report(); review.stable.inventory.entries[0].rawDiverged = true; review.stable.inventory.entries[0].state = 'raw_diverged';
 const sealedReview = resealReview(review);
 const proposal = createManagedSourceSelectionProposal({ review: sealedReview, request: request(sealedReview, ['desktop/a.mjs', 'desktop/b.mjs']) });
 assert.ok(proposal.blockers.some(x => x.code === 'selected_stable_source_requires_preservation_review' && x.paths[0] === 'desktop/a.mjs'));
 assert.ok(proposal.blockers.some(x => x.code === 'selected_stable_counterpart_absence_unproven' && x.paths[0] === 'desktop/b.mjs'));
});
test('combined stable and sandbox paths with casefold aliases add a blocker with both exact paths', () => {
 const review = report();
 review.stable.inventory.entries = [entry('desktop/A.mjs')];
 review.sandbox.inventory.entries = [entry('desktop/a.mjs')];
 const sealedReview = resealReview(review);
 const proposal = createManagedSourceSelectionProposal({ review: sealedReview, request: request(sealedReview, ['desktop/a.mjs']) });
 assert.ok(proposal.blockers.some(x => x.code === 'cross_lane_case_alias_requires_review' && JSON.stringify(x.paths) === JSON.stringify(['desktop/A.mjs', 'desktop/a.mjs'])));
});
test('validator enforces the 256 KiB total request cap independently of per-path and count limits', () => {
 const r = report();
 const paths = Array.from({ length: 700 }, (_, i) => `desktop/${String(i).padStart(4, '0')}${'x'.repeat(400)}.mjs`);
 assert.ok(paths.every(path => Buffer.byteLength(path) <= 512));
 assert.throws(() => validateManagedSelectionRequest(request(r, paths)), /size/i);
});
test('selection order does not alter canonical proposal content or digest', () => {
 const review = report();
 const forward = createManagedSourceSelectionProposal({ review, request: request(review, ['desktop/a.mjs', 'desktop/b.mjs']) });
 const reverse = createManagedSourceSelectionProposal({ review, request: request(review, ['desktop/b.mjs', 'desktop/a.mjs']) });
 assert.deepEqual(reverse, forward);
});
test('re-sealed malformed inventory entries are rejected by inner schema validation', () => {
 const mutations = [
  ['unsafe path', e => { e.path = 'desktop/../escape.mjs'; }],
  ...['headOid', 'indexOid', 'workingOid'].map(key => [`invalid ${key}`, e => { e[key] = 'not-an-oid'; }]),
  ...['headMode', 'indexMode', 'mode'].map(key => [`unsupported ${key}`, e => { e[key] = '120000'; }]),
  ...['staged', 'rawDiverged', 'modeDiverged'].map(key => [`invalid ${key} flag`, e => { e[key] = 'false'; }]),
 ];
 for (const [label, mutate] of mutations) {
  const review = report(); mutate(review.sandbox.inventory.entries[0]);
  const sealedReview = resealReview(review);
  assert.throws(() => createManagedSourceSelectionProposal({ review: sealedReview, request: request(sealedReview) }), /inventory|path|mode|flags|oid/i, label);
 }
});
test('re-sealed inventory cannot disagree with baseline root, head, or index identity', () => {
 for (const field of ['root', 'head', 'indexDigest']) {
  const review = report();
  review.sandbox.inventory[field] = field === 'root' ? '/other' : H('f');
  const sealedReview = resealReview(review);
  assert.throws(() => createManagedSourceSelectionProposal({ review: sealedReview, request: request(sealedReview) }), /baseline|bind/i, field);
 }
});
test('inventory digest tampering and unsafe review or inventory flags are rejected', () => {
 {
  const review = report(); review.sandbox.inventory.entries[0].size = 99;
  const sealedOuterOnly = { ...without(review, 'review_digest'), review_digest: hash(without(review, 'review_digest')) };
  assert.throws(() => createManagedSourceSelectionProposal({ review: sealedOuterOnly, request: request(sealedOuterOnly) }), /inventory digest/i);
 }
 for (const [location, field] of [['review', 'execution'], ['review', 'readyForMerge'], ['review', 'readyForLaunch'], ['inventory', 'execution'], ['inventory', 'readyForMerge'], ['inventory', 'readyForLaunch']]) {
  const review = report();
  if (location === 'review') review[field] = true;
  else review.sandbox.inventory[field] = true;
  const sealedReview = location === 'review' ? { ...without(review, 'review_digest'), review_digest: hash(without(review, 'review_digest')) } : resealReview(review);
  assert.throws(() => createManagedSourceSelectionProposal({ review: sealedReview, request: request(sealedReview) }), /contract|safety|invalid/i, `${location}.${field}`);
 }
});
test('selected missing files and unknown hash or mode metadata remain blocked', () => {
 for (const mutate of [
  e => { e.state = 'missing_working_file'; e.workingSha256 = null; e.workingOid = null; delete e.mode; },
  e => { e.workingSha256 = null; },
  e => { e.workingOid = null; },
  e => { e.mode = null; },
 ]) {
  const review = report(); mutate(review.sandbox.inventory.entries.find(e => e.path === 'desktop/b.mjs'));
  const sealedReview = resealReview(review);
  const proposal = createManagedSourceSelectionProposal({ review: sealedReview, request: request(sealedReview) });
  assert.ok(proposal.blockers.some(b => b.code === 'selected_sandbox_source_metadata_incomplete' && b.paths.includes('desktop/b.mjs')));
 }
});
test('stable staged, mode-diverged, and missing entries block selection even when raw hashes equal sandbox', () => {
 const variants = [
  e => { e.staged = true; e.state = 'staged'; },
  e => { e.modeDiverged = true; e.state = 'mode_diverged'; },
  e => { e.state = 'missing_working_file'; e.mode = null; },
 ];
 for (const mutate of variants) {
  const review = report(); const stableEntry = review.stable.inventory.entries[0];
  mutate(stableEntry);
  assert.equal(stableEntry.workingSha256, review.sandbox.inventory.entries[0].workingSha256);
  const sealedReview = resealReview(review);
  const proposal = createManagedSourceSelectionProposal({ review: sealedReview, request: request(sealedReview, ['desktop/a.mjs']) });
  assert.ok(proposal.blockers.some(b => b.code === 'selected_stable_source_requires_preservation_review' && b.paths.includes('desktop/a.mjs')));
 }
});


test('re-sealed selected sandbox and stable counterpart reject orphan OID or mode metadata per layer', () => {
 for (const side of ['sandbox', 'stable']) {
  for (const layer of ['head', 'index']) {
   for (const field of [`${layer}Oid`, `${layer}Mode`]) {
    for (const missing of ['omitted', 'null']) {
     const review = report();
     const selectedEntry = review[side].inventory.entries.find(e => e.path === 'desktop/a.mjs');
     if (missing === 'omitted') delete selectedEntry[field];
     else selectedEntry[field] = null;
     const sealedReview = resealReview(review);
     assert.throws(() => createManagedSourceSelectionProposal({ review: sealedReview, request: request(sealedReview, ['desktop/a.mjs']) }), /oid.*mode|pair/i, `${side}.${field} ${missing}`);
    }
   }
  }
 }
});
test('genuinely absent or null HEAD and index pairs remain valid inventory semantics', () => {
 for (const side of ['sandbox', 'stable']) {
  for (const layer of ['head', 'index']) {
   for (const missing of ['omitted', 'null']) {
    const review = report();
    const selectedEntry = review[side].inventory.entries.find(e => e.path === 'desktop/a.mjs');
    for (const field of [`${layer}Oid`, `${layer}Mode`]) {
     if (missing === 'omitted') delete selectedEntry[field];
     else selectedEntry[field] = null;
    }
    const sealedReview = resealReview(review);
    const proposal = createManagedSourceSelectionProposal({ review: sealedReview, request: request(sealedReview, ['desktop/a.mjs']) });
    assert.equal(proposal.execution, false);
   }
  }
 }
});
