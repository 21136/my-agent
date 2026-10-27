import { createHash } from 'node:crypto';
import { inspectManagedSources, revalidateManagedInspection } from './managed-source-plan.mjs';
import { captureManagedSourceInventory, captureManagedTreeDelta } from './managed-source-inventory.mjs';

const MODE = 'managed_source_review_v1';
const HASH = /^[a-f0-9]{64}$/i;
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}`;
  return JSON.stringify(value);
}
function digest(value) { return createHash('sha256').update(canonical(value), 'utf8').digest('hex'); }
async function buildReview({ stableRoot, sandboxRoot }) {
  const inspection = inspectManagedSources({ stableRoot, sandboxRoot, metadataOnlyHistory: true });
  const stable = {
    identity: inspection.stable.identity,
    head: inspection.stable.head,
    branch: inspection.stable.branch,
    indexDigest: inspection.stable.indexDigest,
    inventory: await captureManagedSourceInventory({ root: stableRoot, head: inspection.stable.head, indexDigest: inspection.stable.indexDigest }),
  };
  const sandbox = {
    identity: inspection.sandbox.identity,
    head: inspection.sandbox.head,
    branch: inspection.sandbox.branch,
    indexDigest: inspection.sandbox.indexDigest,
    inventory: await captureManagedSourceInventory({ root: sandboxRoot, head: inspection.sandbox.head, indexDigest: inspection.sandbox.indexDigest }),
  };
  const effectiveDelta = await captureManagedTreeDelta({ root: sandboxRoot, base: inspection.committedRange.mergeBase, head: inspection.sandbox.head });
  const stableVerification = await captureManagedSourceInventory({ root: stableRoot, head: inspection.stable.head, indexDigest: inspection.stable.indexDigest });
  const sandboxVerification = await captureManagedSourceInventory({ root: sandboxRoot, head: inspection.sandbox.head, indexDigest: inspection.sandbox.indexDigest });
  if (stableVerification.inventory_digest !== stable.inventory.inventory_digest || sandboxVerification.inventory_digest !== sandbox.inventory.inventory_digest) throw new Error('allowed-source inventory changed during review capture');
  const checked = revalidateManagedInspection(inspection);
  if (checked.snapshot_digest !== inspection.snapshot_digest) throw new Error('old source inspection drifted while building review');
  const blockers = [...inspection.blockers];
  const changedPaths = inventory => { if (!Array.isArray(inventory?.entries)) throw new Error(`inventory entries unavailable; keys=${Object.keys(inventory ?? {}).join(",")}`); return inventory.entries.filter(item => item.state !== 'clean' || item.staged || item.rawDiverged).map(item => item.path); };
  const stableChanged = changedPaths(stable.inventory);
  const sandboxChanged = changedPaths(sandbox.inventory);
  if (stableChanged.length) blockers.push({ code: 'stable_allowed_source_changes_require_preservation_review', paths: [...new Set(stableChanged)].sort() });
  if (sandboxChanged.length) blockers.push({ code: 'sandbox_allowed_source_changes_require_ownership_review', paths: [...new Set(sandboxChanged)].sort() });
  const limitations = [...new Set([...stable.inventory.limitations, ...sandbox.inventory.limitations, 'Historical paths are per-commit metadata without rename similarity; renames appear as delete/add.', 'Effective delta is merge-base to sandbox HEAD, not a prospective merge result or delivery authorization.', 'Old Git status inventory remains intentionally incomplete when protected tracked paths exist; dirty arrays are unknown, not clean.'])];
  const report = {
    format: 1,
    mode: MODE,
    inspectionDigest: inspection.snapshot_digest,
    historyInspection: inspection.historyInspection,
    stable,
    sandbox,
    history: inspection.committedRange,
    effectiveDelta,
    blockers,
    limitations,
    execution: false,
    readyForMerge: false,
    readyForLaunch: false,
  };
  report.review_digest = digest(report);
  return report;
}
export async function inspectManagedReview({ stableRoot, sandboxRoot } = {}) {
  return await buildReview({ stableRoot, sandboxRoot });
}
export async function revalidateManagedReview(packet) {
  if (!packet || typeof packet !== 'object' || Array.isArray(packet) || packet.format !== 1 || packet.mode !== MODE || !HASH.test(packet.review_digest || '')) throw new Error('managed review packet format or digest is invalid');
  const { review_digest: suppliedDigest, ...body } = packet;
  if (digest(body) !== suppliedDigest) throw new Error('managed review packet digest is invalid or tampered');
  if (packet.execution !== false || packet.readyForMerge !== false || packet.readyForLaunch !== false || !Array.isArray(packet.blockers)) throw new Error('managed review packet safety contract is invalid');
  const stableRoot = packet.stable?.identity?.worktreeRoot;
  const sandboxRoot = packet.sandbox?.identity?.worktreeRoot;
  if (typeof stableRoot !== 'string' || typeof sandboxRoot !== 'string') throw new Error('managed review packet roots are invalid');
  const current = await buildReview({ stableRoot, sandboxRoot });
  if (current.review_digest !== suppliedDigest || canonical(current) !== canonical(packet)) throw new Error('managed review packet drifted; review must restart');
  return current;
}
