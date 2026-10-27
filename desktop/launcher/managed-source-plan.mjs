import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { lstatSync, realpathSync, readFileSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const FORMAT = 1;
const MODE = 'managed_source_inspection_v1';
const MAX_GIT_OUTPUT = 32 * 1024 * 1024;
const ALLOWED_PREFIXES = ['agent-core/', 'desktop/', 'docs/', 'protocol/', 'scripts/', 'evolve/tools/', 'evolve/skills/', 'evolve/tool-catalog/'];
const ALLOWED_FILES = new Set(['AGENTS.md', '.gitignore', 'start-desktop.bat', 'init-sandbox.bat', 'check-stable-before-sandbox-merge.bat', 'sync-sandbox-worktree.bat']);
const NEVER_READ_SEGMENTS = new Set(['.git', '.venv', 'venv', 'node_modules', 'data', 'artifacts', 'dist', 'dist-electron', '__pycache__', 'profile', 'profiles', 'user-data', 'workspace', 'workspaces', '.managed-runtime']);
const SECRET_FILE = /^(?:\.env(?:\..*)?|llm_secrets\.(?:json|ya?ml)|llm_models\.(?:json|ya?ml)|.*\.(?:pem|key|p12|pfx|cer|crt))$/i;
const SAFE_FILE = /^[A-Za-z0-9_@+.,= -]+$/;
const HASH_RE = /^[a-f0-9]{64}$/;

function sha256(bytes) { return createHash('sha256').update(bytes).digest('hex'); }
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}`;
  return JSON.stringify(value);
}
function normalizedRoot(input, label) {
  if (typeof input !== 'string' || !path.isAbsolute(input)) throw new Error(`${label} root must be absolute`);
  const resolved = path.resolve(input);
  let current = path.parse(resolved).root;
  for (const part of resolved.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    let stat;
    try { stat = lstatSync(current); } catch { throw new Error(`${label} root path is missing or inaccessible`); }
    if (stat.isSymbolicLink() || (stat.attributes & 0x400) !== 0) throw new Error(`${label} root contains a symlink or reparse point`);
  }
  if (!lstatSync(resolved).isDirectory()) throw new Error(`${label} root is not a directory`);
  const real = realpathSync.native(resolved);
  if (path.resolve(real) !== resolved) throw new Error(`${label} root resolves through an alias`);
  return resolved;
}
function safeRelative(p) {
  if (typeof p !== 'string' || !p || p.startsWith('/') || p.includes('\\') || p.split('/').some(x => !x || x === '.' || x === '..' || !SAFE_FILE.test(x))) return false;
  return true;
}
function protectedPath(p) {
  const parts = p.split('/');
  return parts.some(part => NEVER_READ_SEGMENTS.has(part.toLowerCase()) || SECRET_FILE.test(part));
}
function sourceAllowed(p) {
  if (!safeRelative(p) || protectedPath(p)) return false;
  return ALLOWED_FILES.has(p) || ALLOWED_PREFIXES.some(prefix => p.startsWith(prefix));
}
function git(root, args) {
  const env = { PATH: process.env.PATH || '', SYSTEMROOT: process.env.SYSTEMROOT || '', WINDIR: process.env.WINDIR || '', HOME: path.join(os.tmpdir(), 'codex-managed-source-empty-home'), USERPROFILE: path.join(os.tmpdir(), 'codex-managed-source-empty-home'), GIT_CONFIG_NOSYSTEM: '1', GIT_TERMINAL_PROMPT: '0', GIT_OPTIONAL_LOCKS: '0', LC_ALL: 'C' };
  const fixed = ['--no-optional-locks', '--literal-pathspecs', '-c', 'core.fsmonitor=false', '-c', `safe.directory=${root}`, '-c', 'core.pager=cat', '-c', 'diff.external=', '-c', 'diff.submodule=short', '-c', 'submodule.recurse=false', '-c', 'diff.ignoreSubmodules=all', '-c', `core.attributesFile=${process.platform === 'win32' ? 'NUL' : '/dev/null'}`, '--no-pager'];
  const result = spawnSync('git', [...fixed, ...args], { cwd: root, env, encoding: 'buffer', windowsHide: true, timeout: 15000, maxBuffer: MAX_GIT_OUTPUT, stdio: ['ignore', 'pipe', 'pipe'] });
  if (result.error || result.status !== 0) throw new Error(`git ${args[0]} failed safely (${result.error?.code || result.status}): ${text(result.stderr || Buffer.alloc(0)).slice(0, 300)}`);
  return result.stdout;
}
function text(bytes) { return bytes.toString('utf8'); }
function nulList(bytes) { return text(bytes).split('\0').filter(Boolean); }
function identity(root, label) {
  const top = path.resolve(text(git(root, ['rev-parse', '--show-toplevel'])).trim());
  if (top !== root) throw new Error(`${label} root is not the Git worktree root`);
  const branchValue = text(git(root, ['rev-parse', '--abbrev-ref', 'HEAD'])).trim();
  if (!branchValue || branchValue === 'HEAD') throw new Error(`${label} is detached; branch identity is required`);
  const head = text(git(root, ['rev-parse', '--verify', 'HEAD'])).trim();
  const gitDir = path.resolve(root, text(git(root, ['rev-parse', '--path-format=absolute', '--git-dir'])).trim());
  const commonDir = path.resolve(root, text(git(root, ['rev-parse', '--path-format=absolute', '--git-common-dir'])).trim());
  for (const [p, n] of [[gitDir, 'git directory'], [commonDir, 'common git directory']]) {
    let st; try { st = lstatSync(p); } catch { throw new Error(`${label} ${n} is missing`); }
    if (st.isSymbolicLink() || (st.attributes & 0x400) !== 0 || !st.isDirectory()) throw new Error(`${label} ${n} is linked or invalid`);
  }
  const indexPath = path.join(gitDir, 'index');
  let indexDigest = null;
  try { const st = lstatSync(indexPath); if (st.isSymbolicLink() || (st.attributes & 0x400) !== 0 || !st.isFile()) throw new Error('index is linked or invalid'); indexDigest = sha256(readFileSync(indexPath)); }
  catch (error) { if (error.code !== 'ENOENT') throw new Error(`${label} index cannot be safely fingerprinted`); }
  const staged = nulList(git(root, ['ls-files', '--stage', '-z'])).map(row => row);
  const protectedTrackedPaths = [...new Set(staged.map(row => {
    const separator = row.indexOf('\t');
    if (separator < 0) throw new Error('tracked path metadata is invalid');
    return row.slice(separator + 1);
  }).filter(protectedPath))].sort();
  const stashLines = text(git(root, ['for-each-ref', '--format=%(refname)%00%(objectname)', 'refs/stash'])).split(/\r?\n/).filter(Boolean);
  const stashRefs = stashLines.map(row => { const [ref, oid] = row.split('\0'); if (ref !== 'refs/stash' || !/^[a-f0-9]{40,64}$/i.test(oid || '')) throw new Error('stash metadata is ambiguous'); return { ref, oid }; });
  const stashEntries = stashRefs.length
    ? text(git(root, ['reflog', 'show', '--no-patch', '--no-show-signature', '--format=%gD%x00%H', 'refs/stash'])).split(/\r?\n/).filter(Boolean)
    : [];
  const stashes = stashEntries.map((row, index) => {
    const [selector, oid] = row.split('\0');
    if (selector !== 'refs/stash@{' + index + '}' || !/^[a-f0-9]{40,64}$/i.test(oid || '')) throw new Error('stash reflog metadata is ambiguous');
    return { ref: 'refs/stash', selector, oid };
  });
  if (stashRefs.length && (!stashes.length || stashes[0].oid !== stashRefs[0].oid)) throw new Error('stash ref and reflog identities do not agree');
  const stashDigest = sha256(Buffer.from(canonical(stashes)));
  return { identity: { worktreeRoot: root, gitDir, commonDir }, head, branch: branchValue, indexDigest, indexEntriesDigest: sha256(Buffer.from(staged.join('\0'))), protectedTrackedPaths, stashCount: stashes.length, stashes, stashDigest };
}
function parseStatus(bytes) {
  const fields = nulList(bytes); const out = [];
  for (let i = 0; i < fields.length;) {
    const record = fields[i++];
    if (record.startsWith('# ')) continue;
    if (record.startsWith('? ')) { out.push({ code: '??', path: record.slice(2), index: '?', worktree: '?' }); continue; }
    const kind = record[0];
    if (kind === '1') { const cols = record.split(' '); out.push({ code: cols[1], path: cols.slice(8).join(' '), index: cols[1][0], worktree: cols[1][1] }); }
    else if (kind === '2') { const cols = record.split(' '); const current = cols.slice(9).join(' '); const original = fields[i++] || ''; out.push({ code: cols[1], path: current, originalPath: original, index: cols[1][0], worktree: cols[1][1] }); }
    else if (kind === 'u') { const cols = record.split(' '); out.push({ code: cols[1] || 'UU', path: cols.slice(10).join(' '), conflict: true, index: 'U', worktree: 'U' }); }
    else throw new Error('Git status returned an ambiguous record');
  }
  return out;
}
function fileHash(root, rel) {
  if (!sourceAllowed(rel)) return undefined;
  const absolute = path.join(root, ...rel.split('/'));
  const parts = rel.split('/'); let current = root;
  for (let i = 0; i < parts.length; i++) {
    current = path.join(current, parts[i]);
    let st; try { st = lstatSync(current); } catch (error) { if (error.code === 'ENOENT') return null; throw new Error('source path cannot be inspected safely'); }
    if (st.isSymbolicLink() || (st.attributes & 0x400) !== 0) throw new Error('source path contains a link or reparse point');
    if (i < parts.length - 1 && !st.isDirectory()) throw new Error('source path parent is not a directory');
    if (i === parts.length - 1 && !st.isFile()) throw new Error('source path is not a regular file');
  }
  return sha256(readFileSync(absolute));
}
function snapshotRoot(root, label, { metadataOnlyHistory = false } = {}) {
  const ident = identity(root, label);
  // Git status may run clean filters or hash tracked working bytes before Node can classify them.
  // Index-only protected names therefore block the entire worktree status inspection.
  const statusInspection = metadataOnlyHistory ? 'metadata_only' : ident.protectedTrackedPaths.length ? 'blocked_protected_tracked' : 'complete';
  const status = statusInspection === 'complete'
    ? parseStatus(git(root, ['status', '--porcelain=v2', '-z', '--branch', '--untracked-files=all', '--renames', '--ignore-submodules=all', '--no-ahead-behind']))
    : [];
  const dirty = status.map(item => {
    const safe = sourceAllowed(item.path) && (!item.originalPath || sourceAllowed(item.originalPath));
    const entry = { ...item, classification: safe ? 'source_candidate' : 'protected_or_out_of_scope' };
    if (safe) { entry.sha256 = fileHash(root, item.path); if (item.originalPath) entry.originalSha256 = fileHash(root, item.originalPath); }
    return entry;
  });
  const operations = ['MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'BISECT_LOG', 'rebase-merge', 'rebase-apply', ' sequencer'];
  const operationMarkers = [];
  for (const marker of operations) {
    const p = path.join(ident.identity.gitDir, marker.trim());
    try { const st = lstatSync(p); if (st.isSymbolicLink() || (st.attributes & 0x400) !== 0) throw new Error('Git operation marker is a link'); operationMarkers.push(marker.trim()); } catch (e) { if (e.code !== 'ENOENT') throw e; }
  }
  return { ...ident, statusInspection, dirty, operationMarkers };
}
function buildReport(stableRootInput, sandboxRootInput, { metadataOnlyHistory = false } = {}) {
  const stableRoot = normalizedRoot(stableRootInput, 'stable');
  const sandboxRoot = normalizedRoot(sandboxRootInput, 'sandbox');
  const rootKey = root => process.platform === 'win32' ? root.toLowerCase() : root;
  if (rootKey(stableRoot) === rootKey(sandboxRoot)) throw new Error('source roots must be distinct real worktree roots');
  const stable = snapshotRoot(stableRoot, 'stable', { metadataOnlyHistory });
  if (stable.branch === 'experiment/sandbox') throw new Error('stable branch must not be experiment/sandbox');
  const sandbox = snapshotRoot(sandboxRoot, 'sandbox', { metadataOnlyHistory });
  if (stable.identity.commonDir.toLowerCase() !== sandbox.identity.commonDir.toLowerCase()) throw new Error('source worktrees have different common Git directories');
  if (sandbox.branch !== 'experiment/sandbox') throw new Error('sandbox branch must be experiment/sandbox');
  const base = text(git(stableRoot, ['merge-base', stable.head, sandbox.head])).trim();
  if (!/^[a-f0-9]{40,64}$/i.test(base)) throw new Error('merge base is invalid');
  const sandboxHistory = text(git(sandboxRoot, ['rev-list', '--reverse', '--topo-order', '--end-of-options', sandbox.head])).trim().split(/\r?\n/).filter(Boolean);
  const stableHistory = new Set(text(git(stableRoot, ['rev-list', '--end-of-options', stable.head])).trim().split(/\r?\n/).filter(Boolean));
  const commitIds = sandboxHistory.map(x => x.trim()).filter(oid => !stableHistory.has(oid));
  const commits = [];
  const pathMap = new Map();
  for (const commit of commitIds) {
    const meta = text(git(sandboxRoot, ['-c', 'log.showSignature=false', 'show', '-s', '--no-show-signature', '--format=%H%x00%P%x00%T%x00%s', commit])).split('\0');
    if (meta.length < 4 || meta[0] !== commit) throw new Error('commit metadata is incomplete');
    const parents = meta[1].trim().split(' ').filter(Boolean);
    commits.push({ oid: commit, parents, tree: meta[2].trim(), subject: meta[3].trim() });
    const diffArgs = ['diff-tree', '--root', '-r', '-m', '--no-commit-id', '--name-status', '-z', ...(metadataOnlyHistory ? ['--no-renames'] : ['--find-renames']), '--no-ext-diff', '--no-textconv', commit];
    const diff = nulList(git(sandboxRoot, diffArgs));
    for (let i = 0; i < diff.length;) {
      const status = diff[i++];
      const isRename = status.startsWith('R') || status.startsWith('C');
      const first = diff[i++]; const second = isRename ? diff[i++] : undefined;
      if (!first || isRename && !second) throw new Error('committed path inventory is ambiguous');
      const pair = isRename ? { kind: status[0] === 'R' ? 'rename' : 'copy', score: Number(status.slice(1)) || 0, from: first, to: second, commit } : undefined;
      for (const p of isRename ? [first, second] : [first]) {
        const prior = pathMap.get(p) || { path: p, commits: [], renamePairs: [], allowed: sourceAllowed(p), protected: protectedPath(p) || !sourceAllowed(p) };
        prior.commits.push({ oid: commit, status }); if (pair) prior.renamePairs.push(pair); pathMap.set(p, prior);
      }
    }
  }
  const committedPaths = [...pathMap.values()].sort((a, b) => a.path.localeCompare(b.path));
  const blockers = [];
  const add = (code, paths = []) => blockers.push({ code, paths: [...new Set(paths)].sort() });
  const uninspected = [stable, sandbox].filter(snap => snap.statusInspection !== 'complete');
  if (uninspected.length) add('complete_worktree_inventory_unavailable', uninspected.flatMap(snap => snap.protectedTrackedPaths));
  for (const [label, snap] of [['stable', stable], ['sandbox', sandbox]]) {
    if (snap.operationMarkers.length) add(`${label}_unfinished_git_operation`);
    const conflicts = snap.dirty.filter(x => x.conflict).map(x => x.path);
    if (conflicts.length) add(`${label}_conflict`, conflicts);
    const unsafe = snap.dirty.filter(x => x.classification !== 'source_candidate' || x.originalPath && !sourceAllowed(x.originalPath)).flatMap(x => [x.path, ...(x.originalPath ? [x.originalPath] : [])]);
    const ownedDirty = snap.dirty.filter(x => x.classification === 'source_candidate').flatMap(x => [x.path, ...(x.originalPath ? [x.originalPath] : [])]);
    if (ownedDirty.length) add(label === 'stable' ? 'stable_dirty_requires_preservation_review' : 'sandbox_dirty_requires_ownership_review', ownedDirty);
    if (unsafe.length) add(`${label}_protected_or_out_of_scope_dirty`, unsafe);
    const dirtyAmbiguous = snap.dirty.filter(x => x.classification === 'source_candidate' && (x.code.includes('R') || x.code.includes('C'))).map(x => x.path);
    if (dirtyAmbiguous.length) add(`${label}_rename_requires_review`, dirtyAmbiguous);
  }
  const blockedCommitted = committedPaths.filter(p => p.protected).map(p => p.path);
  if (blockedCommitted.length) add('committed_protected_or_out_of_scope_paths', blockedCommitted);
  if (!commits.length) add('empty_committed_range');
  const committedRange = { mergeBase: base, commits, paths: committedPaths };
  const report = { format: FORMAT, mode: MODE, ...(metadataOnlyHistory ? { historyInspection: 'metadata_only_no_renames' } : {}), stable, sandbox, committedRange, blockers, readyForScopeReview: blockers.length === 0, readyForLaunch: false };
  report.snapshot_digest = sha256(Buffer.from(canonical(report)));
  return report;
}
export function inspectManagedSources({ stableRoot, sandboxRoot, metadataOnlyHistory = false } = {}) {
  if (typeof metadataOnlyHistory !== 'boolean') throw new TypeError('metadataOnlyHistory must be a boolean');
  return buildReport(stableRoot, sandboxRoot, { metadataOnlyHistory });
}
export function revalidateManagedInspection(inspection) {
  if (!inspection || inspection.format !== FORMAT || inspection.mode !== MODE || !HASH_RE.test(inspection.snapshot_digest || '')) throw new Error('inspection format or digest is invalid');
  if (inspection.historyInspection !== undefined && inspection.historyInspection !== 'metadata_only_no_renames') throw new Error('inspection history mode is invalid');
  const metadataOnlyHistory = inspection.historyInspection === 'metadata_only_no_renames';
  const current = buildReport(inspection.stable?.identity?.worktreeRoot, inspection.sandbox?.identity?.worktreeRoot, { metadataOnlyHistory });
  if (current.snapshot_digest !== inspection.snapshot_digest) throw new Error('managed source inspection drifted; scope review must restart');
  return current;
}
export function deriveManagedScopePlan({ installationId, namespaceDigest, inspection, deliveryPaths } = {}) {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(installationId || '')) throw new Error('installationId must be a UUID');
  if (!HASH_RE.test(namespaceDigest || '')) throw new Error('namespaceDigest must be SHA-256');
  const current = revalidateManagedInspection(inspection);
  if (!current.readyForScopeReview) throw new Error('inspection is not eligible for scope review');
  if (!Array.isArray(deliveryPaths) || !deliveryPaths.length) throw new Error('deliveryPaths must be a non-empty exact path list');
  const all = new Map(current.committedRange.paths.map(item => [item.path, item]));
  const unique = [...new Set(deliveryPaths)];
  if (unique.length !== deliveryPaths.length) throw new Error('deliveryPaths contains duplicates');
  const excluded = current.committedRange.paths.filter(item => !unique.includes(item.path)).map(item => item.path);
  if (excluded.length) throw new Error('scope proposal must account for every committed range path; excluded: ' + excluded.join(', '));
  const selected = unique.map(p => {
    if (!safeRelative(p) || !sourceAllowed(p)) throw new Error(`delivery path is not eligible: ${p}`);
    const item = all.get(p);
    if (!item || !item.allowed || item.protected) throw new Error(`delivery path is outside the committed source range: ${p}`);
    const digest = fileHash(current.sandbox.identity.worktreeRoot, p);
    if (!digest) throw new Error(`delivery path has no regular source file at sandbox HEAD: ${p}`);
    return { path: p, sha256: digest, commits: item.commits.map(c => c.oid) };
  }).sort((a, b) => a.path.localeCompare(b.path));
  const unselected = current.committedRange.paths.filter(item => !unique.includes(item.path)).map(item => item.path);
  const plan = { format: 1, mode: 'managed_scope_proposal_v1', installationId, namespaceDigest, inspectionDigest: current.snapshot_digest, stableBaseline: { head: current.stable.head, branch: current.stable.branch, indexDigest: current.stable.indexDigest, dirtyDigest: sha256(Buffer.from(canonical(current.stable.dirty))) }, sandboxBaseline: { head: current.sandbox.head, branch: current.sandbox.branch, indexDigest: current.sandbox.indexDigest, dirtyDigest: sha256(Buffer.from(canonical(current.sandbox.dirty))) }, completeCommittedPaths: current.committedRange.paths.map(x => x.path), deliveryPaths: selected, excludedCommittedPaths: unselected, gitMutation: false, execution: false };
  plan.scope_digest = sha256(Buffer.from(canonical(plan)));
  return plan;
}












