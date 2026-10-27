import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdirSync, mkdtempSync, writeFileSync, readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { createManagedSourceJournal, readManagedSourceJournal } from '../../launcher/managed-source-journal.mjs';
import { createManagedSourceMergePlan, executeManagedSourceMerge } from '../../launcher/managed-source-transaction.mjs';

// Fail before mkdir or any fixture write if this test was launched from the stable checkout.
const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const sandboxRoot = path.resolve(testDirectory, '../../..');
assert.equal(sandboxRoot.toLowerCase(), 'f:\\my-agent-main\\.worktrees\\sandbox'.toLowerCase(), 'review tests must run only in the authorized sandbox worktree');
const artifactsRoot = path.resolve(sandboxRoot, 'artifacts/managed-transaction-20261024');
const fixturesRoot = path.join(artifactsRoot, 'fixtures');
mkdirSync(fixturesRoot, { recursive: true });
const runRoot = mkdtempSync(path.join(fixturesRoot, 'review-journal-'));
let next = 0;
function fixture(label) {
  const directory = path.join(runRoot, `${String(++next).padStart(2, '0')}-${label}`);
  mkdirSync(directory, { recursive: true });
  return directory;
}
const identity = { installationId: '11111111-1111-4111-8111-111111111111', namespaceDigest: 'a'.repeat(64) };
const operation = { operationId: '22222222-2222-4222-8222-222222222222', planDigest: 'b'.repeat(64), reviewDigest: 'c'.repeat(64) };
const journalPath = (directory) => path.join(directory, 'managed-source-transaction.jsonl');
const canonical = (value) => Array.isArray(value) ? `[${value.map(canonical).join(',')}]` : value && typeof value === 'object'
  ? `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`
  : JSON.stringify(value);
const hash = (value) => createHash('sha256').update(canonical(value), 'utf8').digest('hex');
function row(sequence, type, payload, previousHash) {
  const unsigned = { version: 1, sequence, type, payload, previousHash };
  return { ...unsigned, hash: hash(unsigned) };
}

function contenderScript(moduleUrl, directory) {
  return `import {createManagedSourceJournal} from ${JSON.stringify(moduleUrl)};\nconst identity=${JSON.stringify(identity)}, operation=${JSON.stringify(operation)};\ntry { const h=createManagedSourceJournal({directory:${JSON.stringify(directory)},identity,operation}); h.close(); process.stdout.write('CLAIMED\\n'); } catch { process.stdout.write('REJECTED\\n'); }`;
}
function runNode(source) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ['--input-type=module', '-e', source], { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true, cwd: sandboxRoot });
    let stdout = '', stderr = '';
    child.stdout.setEncoding('utf8').on('data', (part) => { stdout += part; });
    child.stderr.setEncoding('utf8').on('data', (part) => { stderr += part; });
    child.once('error', reject);
    child.once('close', (code) => code === 0 ? resolve({ stdout, stderr }) : reject(new Error(`child exited ${code}: ${stderr}`)));
  });
}

test('simultaneous subprocess claims produce exactly one journal owner', async () => {
  const directory = fixture('contenders');
  const moduleUrl = new URL('../../launcher/managed-source-journal.mjs', import.meta.url).href;
  const outcomes = await Promise.all([runNode(contenderScript(moduleUrl, directory)), runNode(contenderScript(moduleUrl, directory))]);
  const claims = outcomes.flatMap(({ stdout }) => stdout.trim().split(/\r?\n/)).sort();
  assert.deepEqual(claims, ['CLAIMED', 'REJECTED']);
  assert.equal(readManagedSourceJournal({ directory, identity }).status, 'maintenance_required');
});

test('inspection rejects a self-consistent chain with a receipt but no write intent or exit', () => {
  const directory = fixture('self-consistent-invalid-state');
  const claim = row(0, 'claim', { identity, operation }, '0'.repeat(64));
  const invalidFinish = row(1, 'finish', { receipt: {
    mergeCommit: '1'.repeat(40), mergedTree: '2'.repeat(40), stableParent: '3'.repeat(40), sandboxParent: '4'.repeat(40),
    deliveryPaths: ['desktop/review.mjs'], preservationDigest: 'd'.repeat(64),
  } }, claim.hash);
  writeFileSync(journalPath(directory), `${canonical(claim)}\n${canonical(invalidFinish)}\n`, 'utf8');
  assert.throws(() => readManagedSourceJournal({ directory, identity }), /receipt|order|state|finish/i);
});

test('plan accepts the documented stable worktree containing its sandbox worktree', () => {
  const stableRoot = path.join(runRoot, 'repository');
  const nestedSandboxRoot = path.join(stableRoot, '.worktrees', 'sandbox');
  const sha = (text) => createHash('sha256').update(text).digest('hex');
  const oid = (n) => String(n).repeat(40);
  const body = {
    format: 1, mode: 'managed_source_merge_plan_v1', installationId: 'f6c2d116-3dc5-4ff1-99b1-c7feac43e2f0',
    namespaceDigest: sha('namespace'), reviewDigest: sha('review'),
    stable: { root: stableRoot, head: oid(1), branch: 'codex/stable', indexDigest: sha('stable-index') },
    sandbox: { root: nestedSandboxRoot, head: oid(2), branch: 'experiment/sandbox', indexDigest: sha('sandbox-index') },
    deliveryPaths: [{ path: 'desktop/launcher/reviewed.mjs', beforeSha256: null, afterSha256: sha('reviewed') }],
    expectedMergedTree: oid(3), preservationDigest: sha('preservation'), blockers: [],
  };
  assert.doesNotThrow(() => createManagedSourceMergePlan(body));
});

test('plan validation rejects protected environment, config, workspace, and ADS paths', () => {
  const sha = (text) => createHash('sha256').update(text).digest('hex');
  const oid = (n) => String(n).repeat(40);
  const blocked = ['desktop/.env', 'desktop/.env.production', 'desktop/config-store/keys.json', 'desktop/workspace/profile.json', 'desktop/file.mjs:secret', 'desktop/llm_secrets/key.txt'];
  const accepted = [];
  for (const candidate of blocked) {
    const body = {
      format: 1, mode: 'managed_source_merge_plan_v1', installationId: 'f6c2d116-3dc5-4ff1-99b1-c7feac43e2f0',
      namespaceDigest: sha('namespace'), reviewDigest: sha('review'),
      stable: { root: path.join(runRoot, 'stable'), head: oid(1), branch: 'main', indexDigest: sha('stable-index') },
      sandbox: { root: path.join(runRoot, 'sandbox'), head: oid(2), branch: 'experiment/sandbox', indexDigest: sha('sandbox-index') },
      deliveryPaths: [{ path: candidate, beforeSha256: null, afterSha256: sha(candidate) }],
      expectedMergedTree: oid(3), preservationDigest: sha('preservation'), blockers: [],
    };
    try { createManagedSourceMergePlan(body); accepted.push(candidate); } catch {}
  }
  assert.deepEqual(accepted, [], 'protected path classes must all be rejected');
});

test('plan construction rejects accessors without invoking attacker-controlled getters', () => {
  const sha = (text) => createHash('sha256').update(text).digest('hex');
  const oid = (n) => String(n).repeat(40);
  let getterCalls = 0;
  const body = {
    mode: 'managed_source_merge_plan_v1', installationId: 'f6c2d116-3dc5-4ff1-99b1-c7feac43e2f0',
    namespaceDigest: sha('namespace'), reviewDigest: sha('review'),
    stable: { root: path.join(runRoot, 'stable'), head: oid(1), branch: 'main', indexDigest: sha('stable-index') },
    sandbox: { root: path.join(runRoot, 'sandbox'), head: oid(2), branch: 'experiment/sandbox', indexDigest: sha('sandbox-index') },
    deliveryPaths: [{ path: 'desktop/launcher/reviewed.mjs', beforeSha256: null, afterSha256: sha('reviewed') }],
    expectedMergedTree: oid(3), preservationDigest: sha('preservation'), blockers: [],
  };
  Object.defineProperty(body, 'format', { enumerable: true, get() { getterCalls++; return 1; } });
  assert.throws(() => createManagedSourceMergePlan(body));
  assert.equal(getterCalls, 0, 'validation must reject non-JSON accessors before reading their values');
});


test('tampering with a constructed plan fails before journal claim or adapter calls', async () => {
  const sha = (text) => createHash('sha256').update(text).digest('hex');
  const oid = (n) => String(n).repeat(40);
  const directory = fixture('tampered-plan');
  const body = {
    format: 1, mode: 'managed_source_merge_plan_v1', installationId: 'f6c2d116-3dc5-4ff1-99b1-c7feac43e2f0',
    namespaceDigest: sha('namespace'), reviewDigest: sha('review'),
    stable: { root: path.join(runRoot, 'tamper-stable'), head: oid(1), branch: 'main', indexDigest: sha('stable-index') },
    sandbox: { root: path.join(runRoot, 'tamper-sandbox'), head: oid(2), branch: 'experiment/sandbox', indexDigest: sha('sandbox-index') },
    deliveryPaths: [{ path: 'desktop/launcher/reviewed.mjs', beforeSha256: null, afterSha256: sha('reviewed') }],
    expectedMergedTree: oid(3), preservationDigest: sha('preservation'), blockers: [],
  };
  const plan = createManagedSourceMergePlan(body);
  plan.deliveryPaths[0].path = 'desktop/launcher/unreviewed.mjs';
  const confirmation = { planDigest: plan.planDigest, reviewDigest: plan.reviewDigest, namespaceDigest: plan.namespaceDigest };
  let callbacks = 0;
  const adapter = { async revalidate() { callbacks++; }, async merge() { callbacks++; }, async verify() { callbacks++; } };
  await assert.rejects(executeManagedSourceMerge({ directory, plan, confirmation, adapter }), /digest|plan/i);
  assert.equal(callbacks, 0);
  assert.deepEqual(readdirSync(directory), []);
});





test('oversized selected path sets are rejected before merge by the journal-compatible bound', () => {
  const sha = (text) => createHash('sha256').update(text).digest('hex');
  const oid = (n) => String(n).repeat(40);
  const deliveryPaths = Array.from({ length: 96 }, (_, index) => ({
    path: `desktop/${String(index).padStart(3, '0')}-${'a'.repeat(800)}.mjs`,
    beforeSha256: null,
    afterSha256: sha(`path-${index}`),
  }));
  const body = {
    format: 1, mode: 'managed_source_merge_plan_v1', installationId: 'f6c2d116-3dc5-4ff1-99b1-c7feac43e2f0',
    namespaceDigest: sha('namespace'), reviewDigest: sha('review'),
    stable: { root: path.join(runRoot, 'capacity-stable'), head: oid(1), branch: 'main', indexDigest: sha('stable-index') },
    sandbox: { root: path.join(runRoot, 'capacity-sandbox'), head: oid(2), branch: 'experiment/sandbox', indexDigest: sha('sandbox-index') },
    deliveryPaths, expectedMergedTree: oid(3), preservationDigest: sha('preservation'), blockers: [],
  };
  assert.ok(Buffer.byteLength(JSON.stringify(deliveryPaths)) > 64 * 1024);
  assert.throws(() => createManagedSourceMergePlan(body), /size|bound|scope|path/i);
});

test('intent and finish fsync failures never return successful execution', async () => {
  const sha = (text) => createHash('sha256').update(text).digest('hex');
  const oid = (n) => String(n).repeat(40);
  const journalModule = new URL('../../launcher/managed-source-journal.mjs', import.meta.url).href;
  const transactionModule = new URL('../../launcher/managed-source-transaction.mjs', import.meta.url).href;

  async function runFailureCase(label, failAt) {
    const directory = fixture(`fsync-${label}`);
    const plan = createManagedSourceMergePlan({
      format: 1, mode: 'managed_source_merge_plan_v1', installationId: 'f6c2d116-3dc5-4ff1-99b1-c7feac43e2f0',
      namespaceDigest: sha('namespace'), reviewDigest: sha('review'),
      stable: { root: path.join(runRoot, `${label}-stable`), head: oid(1), branch: 'main', indexDigest: sha('stable-index') },
      sandbox: { root: path.join(runRoot, `${label}-sandbox`), head: oid(2), branch: 'experiment/sandbox', indexDigest: sha('sandbox-index') },
      deliveryPaths: [{ path: 'desktop/launcher/reviewed.mjs', beforeSha256: null, afterSha256: sha('reviewed') }],
      expectedMergedTree: oid(3), preservationDigest: sha('preservation'), blockers: [],
    });
    const confirmation = { planDigest: plan.planDigest, reviewDigest: plan.reviewDigest, namespaceDigest: plan.namespaceDigest };
    const source = `
      import fs from 'node:fs';
      import { syncBuiltinESMExports } from 'node:module';
      const originalFsync = fs.fsyncSync; let fsyncCalls = 0;
      fs.fsyncSync = (fd) => { fsyncCalls++; if (fsyncCalls === ${failAt}) { const e = new Error('injected fsync uncertainty'); e.code = 'EIO'; throw e; } return originalFsync(fd); };
      syncBuiltinESMExports();
      const { executeManagedSourceMerge } = await import(${JSON.stringify(transactionModule)});
      const plan = ${JSON.stringify(plan)};
      let merges = 0, verifications = 0, returned = false, error = null;
      const adapter = {
        async revalidate(p) { return { planDigest:p.planDigest, reviewDigest:p.reviewDigest, namespaceDigest:p.namespaceDigest, blockers:[] }; },
        async merge() { merges++; return { exitCode:0 }; },
        async verify() { verifications++; return ${JSON.stringify({ mergeCommit: oid(4), mergedTree: oid(3), stableParent: oid(1), sandboxParent: oid(2), deliveryPaths:['desktop/launcher/reviewed.mjs'], preservationDigest:sha('preservation') })}; },
      };
      try { await executeManagedSourceMerge({ directory:${JSON.stringify(directory)}, plan, confirmation:${JSON.stringify(confirmation)}, adapter }); returned = true; }
      catch (e) { error = e.message; }
      fs.fsyncSync = originalFsync; syncBuiltinESMExports();
      const { readManagedSourceJournal } = await import(${JSON.stringify(journalModule)});
      const inspected = readManagedSourceJournal({ directory:${JSON.stringify(directory)}, identity:{ installationId:plan.installationId, namespaceDigest:plan.namespaceDigest } });
      process.stdout.write(JSON.stringify({ fsyncCalls, merges, verifications, returned, error, status:inspected.status }) + '\\n');
    `;
    const child = await runNode(source);
    return JSON.parse(child.stdout.trim());
  }

  const beforeIntent = await runFailureCase('before-intent', 2);
  assert.equal(beforeIntent.returned, false);
  assert.equal(beforeIntent.merges, 0, 'merge callback must not run when intent fsync fails');
  assert.equal(beforeIntent.status, 'maintenance_required');

  const afterFinishBytes = await runFailureCase('after-finish', 4);
  assert.equal(afterFinishBytes.returned, false, 'finish fsync uncertainty cannot be returned as successful execution');
  assert.equal(afterFinishBytes.merges, 1);
  assert.equal(afterFinishBytes.verifications, 1);
  // A complete row may remain readable after a failed fsync; this is historical evidence only,
  // never recovery, activation, or permission to replay an uncertain operation.
  assert.equal(afterFinishBytes.status, 'source_merge_verified');
});



