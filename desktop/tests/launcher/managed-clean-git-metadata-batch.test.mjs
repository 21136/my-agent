import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import fsPromises from 'node:fs/promises';
import childProcess from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';
import path from 'node:path';
import { performance } from 'node:perf_hooks';
import { spawnSync } from 'node:child_process';
import * as metadata from '../../launcher/managed-clean-git-metadata.mjs';

const git = 'C:/Program Files/Git/cmd/git.exe';
function run(cwd, ...args) {
  const result = spawnSync(git, ['-c', `safe.directory=${cwd}`, '-c', 'core.hooksPath=NUL', '-c', 'commit.gpgSign=false', ...args], {
    cwd, encoding: 'utf8', windowsHide: true,
    env: { ...process.env, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: 'NUL', GIT_TERMINAL_PROMPT: '0' },
  });
  assert.equal(result.status, 0, `isolated Git fixture failed: ${result.stderr}`);
  return result.stdout.trim();
}
function fixture() {
  const base = fs.mkdtempSync('F:/managed-object-batch-');
  const stable = path.join(base, 'repo'), sandbox = path.join(stable, '.worktrees', 'sandbox');
  fs.mkdirSync(stable, { recursive: true });
  for (let i = 0; i < 80; i++) {
    const file = path.join(stable, 'desktop', `source-${String(i).padStart(3, '0')}.mjs`);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, `export const source${i} = ${i};\n`);
  }
  run(stable, 'init', '-b', 'main'); run(stable, 'config', 'user.name', 'Fixture'); run(stable, 'config', 'user.email', 'fixture@example.invalid');
  run(stable, 'add', '.'); run(stable, 'commit', '-m', 'fixture');
  fs.mkdirSync(path.dirname(sandbox), { recursive: true }); run(stable, 'worktree', 'add', '-b', 'experiment/sandbox', sandbox, 'HEAD');
  return { base, commonDir: path.join(stable, '.git'), stableGitDir: path.join(stable, '.git'), sandboxGitDir: path.join(stable, '.git', 'worktrees', 'sandbox') };
}
function validateCleanupTree(root, current = root) {
  const relative = path.relative(root, current);
  if (relative.startsWith('..') || path.isAbsolute(relative)) throw new Error('fixture cleanup escaped its root');
  const stat = fs.lstatSync(current);
  if (stat.isSymbolicLink() || (Number.isInteger(stat.attributes) && (stat.attributes & 0x400))) throw new Error('fixture cleanup found a nested link/reparse point');
  if (fs.realpathSync.native(current) !== current) throw new Error('fixture cleanup found an aliased nested path');
  if (stat.isDirectory()) {
    for (const name of fs.readdirSync(current)) validateCleanupTree(root, path.join(current, name));
  } else if (!stat.isFile() || stat.nlink !== 1) throw new Error('fixture cleanup found a non-ordinary or hardlinked file');
}
function cleanup(base) {
  const root = path.resolve(base);
  if (path.resolve(path.dirname(root)) !== path.resolve('F:/') || !path.basename(root).startsWith('managed-object-batch-') || path.resolve(fs.realpathSync.native(root)) !== root) throw new Error('refusing fixture cleanup outside its fresh F: root');
  validateCleanupTree(root);
  fs.rmSync(root, { recursive: true, force: false });
}

test('batches a shared commonDir scan and reparse probe while validating both worktree gitDirs', async t => {
  const f = fixture(); t.after(() => cleanup(f.base));
  assert.equal(typeof metadata.assertManagedGitObjectStores, 'function', 'batch guard should be available');
  await assert.rejects(() => metadata.assertManagedGitObjectStores([
    { commonDir: f.commonDir, gitDir: f.stableGitDir },
    { commonDir: path.join(f.base, 'another-common'), gitDir: f.sandboxGitDir },
  ]), /share one common directory/i);

  const originalReaddir = fsPromises.readdir, originalSpawnSync = childProcess.spawnSync;
  const metrics = { objectRootReads: 0, windowsProbes: 0, windowsProbeMs: 0, windowsProbePaths: [] };
  fsPromises.readdir = async function(directory, ...args) {
    if (path.resolve(String(directory)) === path.join(f.commonDir, 'objects')) metrics.objectRootReads++;
    return originalReaddir.call(this, directory, ...args);
  };
  childProcess.spawnSync = function(file, args, options) {
    if (path.basename(String(file)).toLowerCase() === 'powershell.exe') {
      metrics.windowsProbes++;
      if (Buffer.isBuffer(options?.input)) metrics.windowsProbePaths = JSON.parse(options.input.toString('utf8'));
      const start = performance.now();
      try { return originalSpawnSync.call(this, file, args, options); }
      finally { metrics.windowsProbeMs += performance.now() - start; }
    }
    return originalSpawnSync.call(this, file, args, options);
  };
  syncBuiltinESMExports();
  const result = { baseline: [], batched: [], nodeNoopMs: [] };
  const resetMetrics = () => { metrics.objectRootReads = 0; metrics.windowsProbes = 0; metrics.windowsProbeMs = 0; metrics.windowsProbePaths = []; };
  const roots = [
    { commonDir: f.commonDir, gitDir: f.stableGitDir },
    { commonDir: f.commonDir, gitDir: f.sandboxGitDir },
  ];
  try {
    for (let sample = 0; sample < 3; sample++) {
      resetMetrics();
      let start = performance.now();
      for (const root of roots) await metadata.assertManagedGitObjectStore(root);
      result.baseline.push({ wallMs: Math.round(performance.now() - start), objectRootReads: metrics.objectRootReads, windowsProbes: metrics.windowsProbes, windowsProbeMs: Math.round(metrics.windowsProbeMs) });
      assert.equal(metrics.objectRootReads, 2);
      assert.equal(metrics.windowsProbes, process.platform === 'win32' ? 2 : 0);

      resetMetrics(); start = performance.now();
      await metadata.assertManagedGitObjectStores(roots);
      result.batched.push({ wallMs: Math.round(performance.now() - start), objectRootReads: metrics.objectRootReads, windowsProbes: metrics.windowsProbes, windowsProbeMs: Math.round(metrics.windowsProbeMs) });
      assert.equal(metrics.objectRootReads, 1, 'the shared object store is scanned once per guard');
      assert.equal(metrics.windowsProbes, process.platform === 'win32' ? 1 : 0, 'one reparse probe covers shared and both worktree paths');
      if (process.platform === 'win32') {
        assert.ok(metrics.windowsProbePaths.includes(f.stableGitDir));
        assert.ok(metrics.windowsProbePaths.includes(f.sandboxGitDir));
        assert.ok(metrics.windowsProbePaths.includes(path.join(f.commonDir, 'objects')));
      }
    }
    for (let sample = 0; sample < 3; sample++) {
      const start = performance.now(); childProcess.spawnSync(process.execPath, ['-e', ''], { windowsHide: true });
      result.nodeNoopMs.push(Math.round(performance.now() - start));
    }
  } finally {
    fsPromises.readdir = originalReaddir; childProcess.spawnSync = originalSpawnSync; syncBuiltinESMExports();
  }
  console.log(`managed-object-store-batch ${JSON.stringify({ scope: 'isolated linked-worktree fixture', commonDirCount: 1, worktreeCount: 2, ...result })}`);

  fs.writeFileSync(path.join(f.sandboxGitDir, 'HEAD'), 'invalid without newline');
  await assert.rejects(() => metadata.assertManagedGitObjectStores([
    { commonDir: f.commonDir, gitDir: f.stableGitDir },
    { commonDir: f.commonDir, gitDir: f.sandboxGitDir },
  ]), /HEAD|metadata/i, 'the second worktree metadata is independently validated');
});

test('batch rechecks earlier worktree metadata after validating the next gitDir', async t => {
  const f = fixture(); t.after(() => cleanup(f.base));
  const stableHead = path.join(f.stableGitDir, 'HEAD');
  const originalReaddir = fsPromises.readdir;
  let injected = false;
  fsPromises.readdir = async function(directory, ...args) {
    if (!injected && path.resolve(String(directory)) === path.resolve(f.sandboxGitDir)) {
      fs.writeFileSync(stableHead, 'malformed without newline');
      injected = true;
    }
    return originalReaddir.call(this, directory, ...args);
  };
  syncBuiltinESMExports();
  try {
    await assert.rejects(() => metadata.assertManagedGitObjectStores([
      { commonDir: f.commonDir, gitDir: f.stableGitDir },
      { commonDir: f.commonDir, gitDir: f.sandboxGitDir },
    ]), /HEAD.*malformed/i);
  } finally {
    fsPromises.readdir = originalReaddir;
    syncBuiltinESMExports();
  }
  assert.equal(injected, true, 'the drift must happen after stable gitDir validation');
});

test('batch rechecks common metadata after per-worktree validation', async t => {
  const f = fixture(); t.after(() => cleanup(f.base));
  const replaceRefs = path.join(f.commonDir, 'refs', 'replace');
  const originalReaddir = fsPromises.readdir;
  let injected = false;
  fsPromises.readdir = async function(directory, ...args) {
    if (!injected && path.resolve(String(directory)) === path.resolve(f.sandboxGitDir)) {
      fs.mkdirSync(replaceRefs);
      fs.writeFileSync(path.join(replaceRefs, 'blocked'), `${'a'.repeat(40)}\n`);
      injected = true;
    }
    return originalReaddir.call(this, directory, ...args);
  };
  syncBuiltinESMExports();
  try {
    await assert.rejects(() => metadata.assertManagedGitObjectStores([
      { commonDir: f.commonDir, gitDir: f.stableGitDir },
      { commonDir: f.commonDir, gitDir: f.sandboxGitDir },
    ]), /replace refs are unsupported/i);
  } finally {
    fsPromises.readdir = originalReaddir;
    syncBuiltinESMExports();
  }
  assert.equal(injected, true, 'race must occur after initial commonDir validation and before batch completion');
});

test('batch late shared check rejects alternates created during second worktree validation', async t => {
  const f = fixture(); t.after(() => cleanup(f.base));
  const alternates = path.join(f.commonDir, 'objects', 'info', 'alternates');
  const originalReaddir = fsPromises.readdir;
  let injected = false, objectRootScans = 0;
  fsPromises.readdir = async function(directory, ...args) {
    if (path.resolve(String(directory)) === path.join(f.commonDir, 'objects')) objectRootScans++;
    if (!injected && path.resolve(String(directory)) === path.resolve(f.sandboxGitDir)) {
      fs.writeFileSync(alternates, 'outside-object-store\n');
      injected = true;
    }
    return originalReaddir.call(this, directory, ...args);
  };
  syncBuiltinESMExports();
  try {
    await assert.rejects(() => metadata.assertManagedGitObjectStores([
      { commonDir: f.commonDir, gitDir: f.stableGitDir },
      { commonDir: f.commonDir, gitDir: f.sandboxGitDir },
    ]), /object alternates (?:are|is) unsupported/i);
  } finally {
    fsPromises.readdir = originalReaddir;
    syncBuiltinESMExports();
  }
  assert.equal(injected, true);
  assert.equal(objectRootScans, 0, 'light common metadata recheck must reject before repeating the expensive object-store traversal');
});
