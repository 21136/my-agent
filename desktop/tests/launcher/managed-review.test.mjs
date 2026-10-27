import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { createRequire, syncBuiltinESMExports } from 'node:module';
import { writeFileSync, renameSync } from 'node:fs';
import { inspectManagedReview, revalidateManagedReview } from '../../launcher/managed-review.mjs';

const evidenceRoot = path.resolve('artifacts/managed-review-20261024/review');
const uuid = 'd7b28c91-0fa2-4ec3-8fc2-734fc827c0f2';
const env = { ...process.env, GIT_CONFIG_NOSYSTEM: '1', GIT_TERMINAL_PROMPT: '0' };
function git(cwd, args) {
  const r = spawnSync('git', args, { cwd, env, encoding: 'utf8', windowsHide: true });
  if (r.status !== 0) throw new Error(`fixture git ${args[0]} failed: ${r.stderr}`);
  return r.stdout.trim();
}
async function fixture(t) {
  await mkdir(evidenceRoot, { recursive: true });
  const temp = path.resolve(await mkdtemp(path.join(evidenceRoot, 'packet-')));
  const evidencePrefix = path.resolve(evidenceRoot) + path.sep;
  if (!temp.startsWith(evidencePrefix) || path.basename(temp).startsWith('packet-') !== true) throw new Error('refusing cleanup outside this test packet fixture');
  t.after(async () => { if (path.dirname(temp) !== path.resolve(evidenceRoot) || !path.basename(temp).startsWith('packet-')) throw new Error('fixture cleanup target drifted'); await rm(temp, { recursive: true, force: true }); });
  t.diagnostic(`owned disposable repository: ${temp}`);
  const stableRoot = path.join(temp, 'repo');
  const sandboxRoot = path.join(stableRoot, '.worktrees', 'sandbox');
  await mkdir(stableRoot, { recursive: true });
  git(stableRoot, ['init', '-b', 'main']);
  git(stableRoot, ['config', 'user.name', 'Review Fixture']);
  git(stableRoot, ['config', 'user.email', 'review@example.invalid']);
  await writeFile(path.join(stableRoot, '.gitignore'), '.worktrees/\n');
  await writeFile(path.join(stableRoot, 'desktop-source.mjs'), 'export const base = true;\n');
  await mkdir(path.join(stableRoot, 'desktop'), { recursive: true });
  await writeFile(path.join(stableRoot, 'desktop/source.mjs'), 'export const base = true;\n');
  await mkdir(path.join(stableRoot, 'workspace/private'), { recursive: true });
  await writeFile(path.join(stableRoot, 'workspace/private/key.bin'), 'fixture only\n');
  git(stableRoot, ['add', '.gitignore', 'desktop', '-f', 'workspace/private/key.bin']);
  git(stableRoot, ['commit', '-m', 'base']);
  const base = git(stableRoot, ['rev-parse', 'HEAD']);
  await mkdir(path.dirname(sandboxRoot), { recursive: true });
  git(stableRoot, ['worktree', 'add', '-b', 'experiment/sandbox', sandboxRoot, 'HEAD']);
  await writeFile(path.join(sandboxRoot, 'desktop/source.mjs'), 'export const sandbox = true;\n');
  git(sandboxRoot, ['add', 'desktop/source.mjs']);
  git(sandboxRoot, ['commit', '-m', 'sandbox change']);
  return { stableRoot, sandboxRoot, base };
}

test('packet preserves old protected-file blocker while inventory reports allowed working changes', async t => {
  const f = await fixture(t);
  await writeFile(path.join(f.stableRoot, 'desktop/source.mjs'), 'raw stable edit\r\n');
  await writeFile(path.join(f.sandboxRoot, 'desktop/source.mjs'), 'raw sandbox edit\r\n');
  const report = await inspectManagedReview(f);
  assert.equal(report.format, 1);
  assert.equal(report.mode, 'managed_source_review_v1');
  assert.match(report.review_digest, /^[a-f0-9]{64}$/);
  assert.equal(report.execution, false);
  assert.equal(report.readyForMerge, false);
  assert.equal(report.readyForLaunch, false);
  assert.ok(report.blockers.some(x => x.code === 'complete_worktree_inventory_unavailable'));
  assert.ok(report.blockers.some(x => x.code === 'stable_allowed_source_changes_require_preservation_review'));
  assert.ok(report.blockers.some(x => x.code === 'sandbox_allowed_source_changes_require_ownership_review'));
  assert.ok(report.limitations.some(x => x.includes('raw bytes')));
  assert.ok(report.stable.inventory.entries.some(x => x.path === 'desktop/source.mjs' && x.workingSha256));
  assert.ok(report.sandbox.inventory.entries.some(x => x.path === 'desktop/source.mjs' && x.workingSha256));
  assert.equal(report.stable.inventory.entries.some(x => x.path.includes('key.bin')), false);
  assert.ok(report.effectiveDelta.paths.some(x => x.path === 'desktop/source.mjs'));
  assert.ok(report.history.paths.some(x => x.path === 'desktop/source.mjs'));
  assert.deepEqual(await revalidateManagedReview(report), report);
});

test('revalidation rejects a modified packet even when its digest is not repaired', async t => {
  const f = await fixture(t);
  const report = await inspectManagedReview(f);
  const tampered = structuredClone(report);
  tampered.history.paths.push({ path: 'invented.txt' });
  await assert.rejects(() => revalidateManagedReview(tampered), /digest|packet/i);
});

test('revalidation detects raw working-byte drift, index drift, and newly untracked source', async t => {
  for (const drift of ['raw', 'index', 'untracked']) {
    const f = await fixture(t);
    const report = await inspectManagedReview(f);
    if (drift === 'raw') await writeFile(path.join(f.sandboxRoot, 'desktop/source.mjs'), 'different raw bytes\r\n');
    if (drift === 'index') {
      await writeFile(path.join(f.sandboxRoot, 'desktop/source.mjs'), 'staged bytes\n');
      git(f.sandboxRoot, ['add', 'desktop/source.mjs']);
    }
    if (drift === 'untracked') await writeFile(path.join(f.sandboxRoot, 'desktop/new-source.js'), 'export {}\n');
    await assert.rejects(() => revalidateManagedReview(report), /drift|changed|digest/i, drift);
  }
});


test('packet capture detects allowed stable bytes changing between initial scan and final recapture', async t => {
  const f = await fixture(t);
  const fs = createRequire(import.meta.url)('node:fs');
  const originalOpen = fs.openSync;
  const target = path.join(f.stableRoot, 'desktop/source.mjs');
  let mutated = false;
  fs.openSync = function(file, ...args) {
    const fd = originalOpen.call(this, file, ...args);
    if (!mutated && path.resolve(String(file)).toLowerCase() === target.toLowerCase()) {
      mutated = true;
      renameSync(target, `${target}.held`);
      writeFileSync(target, 'changed during packet capture\n');
    }
    return fd;
  };
  syncBuiltinESMExports();
  try { await assert.rejects(() => inspectManagedReview(f), /drift|changed|inventory/i); }
  finally { fs.openSync = originalOpen; syncBuiltinESMExports(); }
  assert.equal(mutated, true, 'test hook must mutate after the initial stable inventory read');
});
