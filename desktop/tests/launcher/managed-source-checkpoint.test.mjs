import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdirSync, writeFileSync, readFileSync, lstatSync, readdirSync, existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  validateManagedCheckpointRequest,
  captureManagedSourceCheckpoint,
  validateManagedSourceCheckpoint,
  persistManagedSourceCheckpoint,
  readManagedSourceCheckpoint,
} from '../../launcher/managed-source-checkpoint.mjs';
import { createManagedSourceSelectionProposal } from '../../launcher/managed-source-selection.mjs';

const fixtureRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), `../../../artifacts/managed-selected-checkpoint-20261024/fixtures-round1`);
const sha256 = value => createHash('sha256').update(value).digest('hex');
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}
function seal(value, key) { const output = structuredClone(value); output[key] = sha256(Buffer.from(canonical(value))); return output; }
function reseal(value, key) { delete value[key]; value[key] = sha256(Buffer.from(canonical(value))); return value; }
function resealReview(review) { for (const side of ['stable', 'sandbox']) reseal(review[side].inventory, 'inventory_digest'); return reseal(review, 'review_digest'); }
function resealPack(pack) { return reseal(pack, 'checkpointDigest'); }
function packForReview(basePack, review) {
  const req = request(review, basePack.request.selectedPaths);
  const proposal = createManagedSourceSelectionProposal({ review, request: { format: 1, mode: 'managed_source_selection_request_v1', reviewDigest: req.reviewDigest, selectedPaths: req.selectedPaths } });
  const bytesByPath = new Map(basePack.files.map(file => [file.path, file.bytesBase64]));
  const files = proposal.selectedSandboxEntries.map(entry => ({ ...structuredClone(entry), bytesBase64: bytesByPath.get(entry.path) }));
  return resealPack({ format: 1, mode: 'managed_selected_source_checkpoint_v1', review, request: req, files, rawBytes: files.reduce((n, file) => n + file.size, 0), execution: false, readyForMerge: false, readyForLaunch: false, nativeConsent: false });
}
function oid(bytes, format = 'sha1') { return createHash(format).update(Buffer.from(`blob ${bytes.length}\0`)).update(bytes).digest('hex'); }
function entry(filePath, bytes, { headBytes = bytes, headMode = '100644', mode = '100644' } = {}) {
  const untracked = headBytes === null;
  return {
    path: filePath,
    ...(!untracked ? { headOid: oid(headBytes), headMode } : {}),
    workingSha256: sha256(bytes), workingOid: oid(bytes),
    staged: false, rawDiverged: untracked || !headBytes.equals(bytes), modeDiverged: false,
    state: untracked ? 'untracked' : headBytes.equals(bytes) ? 'clean' : 'raw_diverged', mode,
    size: bytes.length,
  };
}
function inventory(root, files, head) {
  const value = { format: 1, mode: 'managed_allowed_source_inventory_v1', root, head, indexDigest: sha256('index'), entries: files, excluded: [], blockers: [], execution: false, readyForMerge: false, readyForLaunch: false };
  return seal(value, 'inventory_digest');
}
function reviewFor(stableRoot, sandboxRoot, files) {
  const stableHead = 'a'.repeat(40), sandboxHead = 'b'.repeat(40);
  const side = (root, head, entries) => ({ identity: { worktreeRoot: root, gitDir: path.join(root, '.git'), commonDir: path.join(root, '.git') }, head, branch: 'fixture', indexDigest: sha256('index'), inventory: inventory(root, entries, head) });
  return seal({
    format: 1, mode: 'managed_source_review_v1', inspectionDigest: sha256('inspection'),
    stable: side(stableRoot, stableHead, files.map(file => entry(file.path, file.stableBytes))),
    sandbox: side(sandboxRoot, sandboxHead, files.map(file => entry(file.path, file.bytes, { headBytes: file.headBytes === undefined ? file.bytes : file.headBytes, mode: file.mode ?? '100644' }))),
    history: { mergeBase: 'c'.repeat(40), commits: [], paths: [] },
    effectiveDelta: { basis: 'merge_base_to_sandbox_head', paths: [] },
    blockers: [{ code: 'review_only_not_execution', paths: [] }], execution: false, readyForMerge: false, readyForLaunch: false,
  }, 'review_digest');
}
function fixture(name, files) {
  mkdirSync(fixtureRoot, { recursive: true });
  const base = path.join(fixtureRoot, name);
  const stableRoot = path.join(base, 'stable');
  const sandboxRoot = path.join(base, 'sandbox');
  mkdirSync(stableRoot, { recursive: true }); mkdirSync(sandboxRoot, { recursive: true });
  for (const file of files) {
    const target = path.join(sandboxRoot, ...file.path.split('/'));
    if (file.bytes.length > 64 * 1024) throw new Error('test source fixtures must remain at most 64 KiB');
    mkdirSync(path.dirname(target), { recursive: true }); writeFileSync(target, file.bytes);
  }
  return { base, stableRoot, sandboxRoot };
}
function request(review, paths) {
  const selection = { format: 1, mode: 'managed_source_selection_request_v1', reviewDigest: review.review_digest, selectedPaths: paths };
  const proposal = createManagedSourceSelectionProposal({ review, request: selection });
  return { format: 1, mode: 'managed_source_checkpoint_request_v1', reviewDigest: review.review_digest, proposalDigest: proposal.proposal_digest, selectedPaths: paths };
}
function capture(name, files) {
  const roots = fixture(name, files);
  const review = reviewFor(roots.stableRoot, roots.sandboxRoot, files);
  const req = request(review, files.map(file => file.path));
  return { roots, review, req, pack: captureManagedSourceCheckpoint({ review, request: req, stableRoot: roots.stableRoot, sandboxRoot: roots.sandboxRoot }) };
}

test('checkpoint request is detached, strict, bounded to 128, and reuses protected-path policy', () => {
  const reviewDigest = 'a'.repeat(64), proposalDigest = 'b'.repeat(64);
  const value = { format: 1, mode: 'managed_source_checkpoint_request_v1', reviewDigest, proposalDigest, selectedPaths: ['desktop/a.mjs'] };
  const detached = validateManagedCheckpointRequest(value);
  value.selectedPaths.push('desktop/other.mjs');
  assert.deepEqual(detached.selectedPaths, ['desktop/a.mjs']);
  for (const bad of [
    { ...value, selectedPaths: ['.env'] },
    { ...value, selectedPaths: ['desktop/a.mjs', 'desktop/A.mjs'] },
    { ...value, extra: true },
    { ...value, reviewDigest: 'A'.repeat(64) },
    { ...value, selectedPaths: Array.from({ length: 129 }, (_, i) => `desktop/${i}.mjs`) },
  ]) assert.throws(() => validateManagedCheckpointRequest(bad));
});

test('capture binds exact roots and proposal, preserves blockers, and snapshots untracked raw working bytes', () => {
  const bytes = Buffer.from([0, 255, 10, 13]);
  const { roots, review, req, pack } = capture('capture-untracked', [{ path: 'desktop/new.mjs', bytes, stableBytes: Buffer.from('stable'), headBytes: Buffer.from('head') }]);
  assert.equal(pack.mode, 'managed_selected_source_checkpoint_v1');
  assert.deepEqual(pack.files[0].bytesBase64, bytes.toString('base64'));
  assert.equal(pack.files[0].headOid, oid(Buffer.from('head')));
  assert.deepEqual(pack.review.blockers, review.blockers);
  assert.equal(pack.execution, false); assert.equal(pack.readyForMerge, false); assert.equal(pack.readyForLaunch, false); assert.equal(pack.nativeConsent, false);
  assert.equal(pack.proposal, undefined);
  assert.throws(() => captureManagedSourceCheckpoint({ review, request: { ...req, proposalDigest: 'f'.repeat(64) }, stableRoot: roots.stableRoot, sandboxRoot: roots.sandboxRoot }));
  assert.throws(() => captureManagedSourceCheckpoint({ review, request: req, stableRoot: roots.stableRoot, sandboxRoot: roots.base }));
});

test('validation detects tampering and correctly re-sealed proposal/file mismatches', () => {
  const { pack } = capture('validate-tamper', [{ path: 'desktop/a.mjs', bytes: Buffer.from('raw'), stableBytes: Buffer.from('other') }]);
  assert.deepEqual(validateManagedSourceCheckpoint(pack), pack);
  const tampered = structuredClone(pack); tampered.files[0].bytesBase64 = Buffer.from('changed').toString('base64');
  assert.throws(() => validateManagedSourceCheckpoint(tampered));
  const resignedBytesMismatch = structuredClone(pack); resignedBytesMismatch.files[0].bytesBase64 = Buffer.from('correctly resealed other bytes').toString('base64');
  assert.throws(() => validateManagedSourceCheckpoint(resealPack(resignedBytesMismatch)));
  const resignedSizeMismatch = structuredClone(pack); resignedSizeMismatch.files[0].size += 1;
  assert.throws(() => validateManagedSourceCheckpoint(resealPack(resignedSizeMismatch)));
  const extra = structuredClone(pack); extra.files[0].unused = true;
  assert.throws(() => validateManagedSourceCheckpoint(extra));
  const badFlag = structuredClone(pack); badFlag.execution = true;
  assert.throws(() => validateManagedSourceCheckpoint(badFlag));
});

test('capture rejects missing or drifted selected files and raw byte limits', () => {
  const bytes = Buffer.from('expected');
  const { roots, review, req } = capture('capture-drift', [{ path: 'desktop/a.mjs', bytes, stableBytes: bytes }]);
  writeFileSync(path.join(roots.sandboxRoot, 'desktop/a.mjs'), 'drifted');
  assert.throws(() => captureManagedSourceCheckpoint({ review, request: req, stableRoot: roots.stableRoot, sandboxRoot: roots.sandboxRoot }));
  const sizeFixture = capture('capture-size-preflight', [{ path: 'desktop/tiny.bin', bytes: Buffer.from('tiny'), stableBytes: Buffer.from('old') }]);
  const oversizedReview = structuredClone(sizeFixture.review);
  oversizedReview.sandbox.inventory.entries[0].size = 4 * 1024 * 1024 + 1;
  resealReview(oversizedReview);
  const oversizedRequest = request(oversizedReview, ['desktop/tiny.bin']);
  assert.throws(() => captureManagedSourceCheckpoint({ review: oversizedReview, request: oversizedRequest, stableRoot: sizeFixture.roots.stableRoot, sandboxRoot: sizeFixture.roots.sandboxRoot }), /limit/i);
  const invalidModeReview = structuredClone(sizeFixture.review);
  invalidModeReview.sandbox.inventory.entries[0].mode = null;
  resealReview(invalidModeReview);
  const invalidModeRequest = request(invalidModeReview, ['desktop/tiny.bin']);
  assert.throws(() => captureManagedSourceCheckpoint({ review: invalidModeReview, request: invalidModeRequest, stableRoot: sizeFixture.roots.stableRoot, sandboxRoot: sizeFixture.roots.sandboxRoot }), /mode|metadata/i);
  const invalidModePack = packForReview(sizeFixture.pack, invalidModeReview);
  assert.throws(() => validateManagedSourceCheckpoint(invalidModePack), /mode|metadata/i);
  for (const field of ['mode', 'size', 'workingSha256', 'workingOid']) {
    const invalidReview = structuredClone(sizeFixture.review);
    invalidReview.sandbox.inventory.entries[0][field] = null;
    resealReview(invalidReview);
    const invalidRequest = request(invalidReview, ['desktop/tiny.bin']);
    assert.throws(() => captureManagedSourceCheckpoint({ review: invalidReview, request: invalidRequest, stableRoot: sizeFixture.roots.stableRoot, sandboxRoot: sizeFixture.roots.sandboxRoot }), /metadata|mode|size/i);
    assert.throws(() => validateManagedSourceCheckpoint(packForReview(sizeFixture.pack, invalidReview)), /metadata|mode|size/i);
  }
  const aggregateFiles = ['a', 'b', 'c'].map(name => ({ path: `desktop/${name}.bin`, bytes: Buffer.from('x'), stableBytes: Buffer.from('y') }));
  const aggregateRoots = fixture('capture-aggregate-preflight', aggregateFiles);
  const aggregateReview = reviewFor(aggregateRoots.stableRoot, aggregateRoots.sandboxRoot, aggregateFiles);
  aggregateReview.sandbox.inventory.entries.forEach(entry => { entry.size = 3 * 1024 * 1024; });
  resealReview(aggregateReview);
  const aggregateRequest = request(aggregateReview, aggregateFiles.map(file => file.path));
  assert.throws(() => captureManagedSourceCheckpoint({ review: aggregateReview, request: aggregateRequest, stableRoot: aggregateRoots.stableRoot, sandboxRoot: aggregateRoots.sandboxRoot }), /aggregate.*limit|limit.*aggregate/i);
});


test('serialized pack limit rejects an oversized embedded review in memory', () => {
  const { roots, pack } = capture('serialized-limit', [{ path: 'desktop/a.mjs', bytes: Buffer.from('tiny'), stableBytes: Buffer.from('old') }]);
  const review = structuredClone(pack.review);
  review.testOnlyPadding = 'x'.repeat(16 * 1024 * 1024);
  resealReview(review);
  const oversized = packForReview(pack, review);
  assert.throws(() => validateManagedSourceCheckpoint(oversized), /serialized|16 MiB/i);
  assert.throws(() => persistManagedSourceCheckpoint({ sandboxRoot: roots.sandboxRoot, checkpoint: oversized }), /serialized|16 MiB/i);
  assert.throws(() => captureManagedSourceCheckpoint({ review, request: oversized.request, stableRoot: roots.stableRoot, sandboxRoot: roots.sandboxRoot }), /serialized|16 MiB/i);
  assert.equal(existsSync(path.join(roots.sandboxRoot, 'artifacts/managed-source-checkpoints')), false);
});
test('persist creates one canonical fixed artifact, rereads it, and never overwrites', () => {
  const { roots, pack } = capture('persist-once', [{ path: 'desktop/a.mjs', bytes: Buffer.from('hello'), stableBytes: Buffer.from('old') }]);
  const out = path.join(roots.sandboxRoot, 'artifacts/managed-source-checkpoints', `${pack.checkpointDigest}.json`);
  const existed = existsSync(out);
  const receipt = existed ? null : persistManagedSourceCheckpoint({ sandboxRoot: roots.sandboxRoot, checkpoint: pack });
  if (existed) assert.deepEqual(readManagedSourceCheckpoint({ sandboxRoot: roots.sandboxRoot, checkpointDigest: pack.checkpointDigest }), pack);
  if (receipt) {
    assert.deepEqual(receipt.selectedPaths, ['desktop/a.mjs']);
    assert.equal(receipt.checkpointCreated, true);
    for (const key of ['execution', 'readyForMerge', 'readyForLaunch', 'nativeConsent']) assert.equal(receipt[key], false);
    assert.equal(receipt.rawBytes, 5);
  }
  assert.equal(pack.rawBytes, 5);
  assert.equal(pack.files[0].bytesBase64, Buffer.from('hello').toString('base64'));
  assert.equal(Object.hasOwn(pack.files[0], 'rawBytes'), false);
  const disk = readFileSync(out);
  assert.equal(disk.at(-1), 10);
  assert.equal(disk.toString('utf8'), `${canonical(pack)}\n`);
  assert.deepEqual(readManagedSourceCheckpoint({ sandboxRoot: roots.sandboxRoot, checkpointDigest: pack.checkpointDigest }), pack);
  assert.throws(() => persistManagedSourceCheckpoint({ sandboxRoot: roots.sandboxRoot, checkpoint: pack }));
  assert.equal(readdirSync(path.dirname(out)).length, 1);
  assert.equal(lstatSync(out).nlink, 1);
});

test('persist rejects source drift and historical reads reject corrupt artifacts', () => {
  const { roots, pack } = capture('persist-corrupt', [{ path: 'desktop/a.mjs', bytes: Buffer.from('before'), stableBytes: Buffer.from('stable') }]);
  writeFileSync(path.join(roots.sandboxRoot, 'desktop/a.mjs'), 'after');
  assert.throws(() => persistManagedSourceCheckpoint({ sandboxRoot: roots.sandboxRoot, checkpoint: pack }));
  writeFileSync(path.join(roots.sandboxRoot, 'desktop/a.mjs'), 'before');
  const out = path.join(roots.sandboxRoot, 'artifacts/managed-source-checkpoints', `${pack.checkpointDigest}.json`);
  if (existsSync(out)) assert.throws(() => persistManagedSourceCheckpoint({ sandboxRoot: roots.sandboxRoot, checkpoint: pack }));
  else persistManagedSourceCheckpoint({ sandboxRoot: roots.sandboxRoot, checkpoint: pack });
  writeFileSync(out, '{}\n');
  assert.throws(() => readManagedSourceCheckpoint({ sandboxRoot: roots.sandboxRoot, checkpointDigest: pack.checkpointDigest }));
});
test('checkpoint store fails closed at eight retained files and on unknown entries', () => {
  const { roots, pack } = capture('store-limit', [{ path: 'desktop/a.mjs', bytes: Buffer.from('a'), stableBytes: Buffer.from('b') }]);
  const store = path.join(roots.sandboxRoot, 'artifacts/managed-source-checkpoints');
  mkdirSync(store, { recursive: true });
  for (let i = 0; i < 8; i++) writeFileSync(path.join(store, `${String(i).padStart(64, '0')}.json`), 'x');
  assert.throws(() => persistManagedSourceCheckpoint({ sandboxRoot: roots.sandboxRoot, checkpoint: pack }), /limit|unknown/i);
  const { roots: roots2, pack: pack2 } = capture('store-unknown', [{ path: 'desktop/a.mjs', bytes: Buffer.from('a'), stableBytes: Buffer.from('b') }]);
  const store2 = path.join(roots2.sandboxRoot, 'artifacts/managed-source-checkpoints');
  mkdirSync(store2, { recursive: true }); writeFileSync(path.join(store2, 'notes.txt'), 'not a checkpoint');
  assert.throws(() => persistManagedSourceCheckpoint({ sandboxRoot: roots2.sandboxRoot, checkpoint: pack2 }), /unknown/i);
});
test('capture accepts untracked files without inventing HEAD or index metadata', () => {
  const bytes = Buffer.from('untracked');
  const roots = fixture('capture-untracked-metadata', [{ path: 'desktop/untracked.mjs', bytes }]);
  const review = reviewFor(roots.stableRoot, roots.sandboxRoot, [{ path: 'desktop/untracked.mjs', bytes, stableBytes: Buffer.from('counterpart'), headBytes: null }]);
  const req = request(review, ['desktop/untracked.mjs']);
  const pack = captureManagedSourceCheckpoint({ review, request: req, stableRoot: roots.stableRoot, sandboxRoot: roots.sandboxRoot });
  assert.equal(pack.files[0].state, 'untracked');
  assert.equal(Object.hasOwn(pack.files[0], 'headOid'), false);
  const proposal = createManagedSourceSelectionProposal({ review, request: { format: 1, mode: 'managed_source_selection_request_v1', reviewDigest: req.reviewDigest, selectedPaths: req.selectedPaths } });
  assert.ok(proposal.blockers.some(blocker => blocker.code === 'selected_sandbox_source_metadata_incomplete'));
  assert.equal(validateManagedSourceCheckpoint(pack).checkpointDigest, pack.checkpointDigest);
});

test('validation rejects accessors, non-finite values, sparse arrays, symbols, and prototype tricks', () => {
  const { pack } = capture('validate-strict-json', [{ path: 'desktop/a.mjs', bytes: Buffer.alloc(0), stableBytes: Buffer.from('other') }]);
  const accessor = structuredClone(pack);
  Object.defineProperty(accessor, 'note', { get() { return 'bad'; }, enumerable: true });
  assert.throws(() => validateManagedSourceCheckpoint(accessor));
  const nonFinite = structuredClone(pack); nonFinite.review.history.extra = Infinity;
  assert.throws(() => validateManagedSourceCheckpoint(nonFinite));
  const sparse = structuredClone(pack); sparse.files[0].unused = new Array(2); sparse.files[0].unused[1] = 1;
  assert.throws(() => validateManagedSourceCheckpoint(sparse));
  const symbol = structuredClone(pack); symbol.review[Symbol('hidden')] = 1;
  assert.throws(() => validateManagedSourceCheckpoint(symbol));
  const customProto = structuredClone(pack); Object.setPrototypeOf(customProto.review, { polluted: true });
  assert.throws(() => validateManagedSourceCheckpoint(customProto));
});

test('reusable test fixture storage remains inside the milestone root and bounded', () => {
  let total = 0;
  const visit = directory => {
    for (const item of readdirSync(directory, { withFileTypes: true })) {
      const absolute = path.join(directory, item.name);
      if (item.isSymbolicLink()) throw new Error('fixture tree must not contain links');
      if (item.isDirectory()) visit(absolute);
      else {
        const size = lstatSync(absolute).size;
        assert.ok(size <= 64 * 1024, `fixture file exceeds 64 KiB: ${absolute}`);
        total += size;
      }
    }
  };
  visit(fixtureRoot);
  assert.ok(total <= 1024 * 1024, `reusable fixture tree exceeds 1 MiB: ${total}`);
});