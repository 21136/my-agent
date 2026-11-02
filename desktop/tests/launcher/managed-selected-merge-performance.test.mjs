import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, readdirSync, lstatSync, readFileSync, realpathSync, rmdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { performance } from 'node:perf_hooks';

const git = 'C:/Program Files/Git/cmd/git.exe';

function assertPlainPathChain(target) {
  const volumeRoot = path.parse(target).root;
  let current = volumeRoot;
  for (const part of target.slice(volumeRoot.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    const stat = lstatSync(current);
    if (stat.isSymbolicLink() || (Number.isInteger(stat.attributes) && (stat.attributes & 0x400))) {
      throw new Error('cleanup boundary rejected a linked or reparse path');
    }
  }
}
function assertTreeHasNoLinks(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const fullPath = path.join(directory, entry.name);
    const stat = lstatSync(fullPath);
    if (stat.isSymbolicLink() || (Number.isInteger(stat.attributes) && (stat.attributes & 0x400))) {
      throw new Error('cleanup boundary rejected a linked or reparse entry');
    }
    if (stat.isDirectory()) assertTreeHasNoLinks(fullPath);
    else if (!stat.isFile()) throw new Error('cleanup boundary rejected an unsupported entry');
  }
}
function removeCreatedTempDirectory({ base, tempRoot, expectedPrefix, identity }) {
  const resolvedBase = path.resolve(base);
  const resolvedTempRoot = path.resolve(tempRoot);
  if (realpathSync.native(resolvedTempRoot) !== resolvedTempRoot) {
    throw new Error('cleanup boundary rejected a non-canonical temp root');
  }
  assertPlainPathChain(resolvedTempRoot);
  if (path.dirname(resolvedBase) !== resolvedTempRoot || !path.basename(resolvedBase).startsWith(expectedPrefix)) {
    throw new Error('cleanup boundary rejected a non-child or unexpected temp prefix');
  }
  assertPlainPathChain(resolvedBase);
  const baseStat = lstatSync(resolvedBase);
  if (!baseStat.isDirectory() || realpathSync.native(resolvedBase) !== resolvedBase ||
      baseStat.dev !== identity.dev || baseStat.ino !== identity.ino || baseStat.birthtimeMs !== identity.birthtimeMs) {
    throw new Error('cleanup boundary rejected a target that is not the newly created directory');
  }
  assertTreeHasNoLinks(resolvedBase);
  rmSync(resolvedBase, { recursive: true, force: false });
}

test('cleanup refuses a directory outside the expected temp-child naming boundary', t => {
  const tempRoot = mkdtempSync(path.join(os.tmpdir(), 'managed-merge-cleanup-test-'));
  const wrongChild = path.join(tempRoot, 'not-managed-merge-perf-owned');
  mkdirSync(wrongChild);
  const identity = lstatSync(wrongChild);
  t.after(() => { rmdirSync(wrongChild); rmdirSync(tempRoot); });
  assert.throws(() => removeCreatedTempDirectory({
    base: wrongChild, tempRoot, expectedPrefix: 'managed-merge-perf-',
    identity: { dev: identity.dev, ino: identity.ino, birthtimeMs: identity.birthtimeMs },
  }), /cleanup boundary/i);
  assert.equal(lstatSync(wrongChild).isDirectory(), true, 'a rejected target must remain untouched');
});
function runGit(cwd, ...args) {
  const result = spawnSync(git, ['-c', `safe.directory=${cwd}`, '-c', 'core.hooksPath=NUL', '-c', 'commit.gpgSign=false', ...args], {
    cwd, encoding: 'utf8', windowsHide: true,
    env: { ...process.env, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: 'NUL', GIT_TERMINAL_PROMPT: '0' },
  });
  assert.equal(result.status, 0, 'isolated fixture Git command must succeed');
  return result.stdout;
}
function scanSyntheticSources(root) {
  let files = 0, bytes = 0, digest = createHash('sha256');
  const visit = dir => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name), stat = lstatSync(full);
      if (entry.isDirectory()) visit(full);
      else if (entry.isFile()) { const content = readFileSync(full); files++; bytes += stat.size; digest.update(content); }
    }
  };
  visit(root);
  return { files, bytes, digest: digest.digest('hex') };
}

test('isolated synthetic Git inventory scan benchmark reports only aggregate timings', t => {
  const tempRoot = path.resolve(os.tmpdir());
  const base = mkdtempSync(path.join(tempRoot, 'managed-merge-perf-'));
  const created = lstatSync(base);
  const identity = { dev: created.dev, ino: created.ino, birthtimeMs: created.birthtimeMs };
  t.after(() => removeCreatedTempDirectory({ base, tempRoot, expectedPrefix: 'managed-merge-perf-', identity }));
  const repo = path.join(base, 'repo'), sourceRoot = path.join(repo, 'desktop');
  mkdirSync(sourceRoot, { recursive: true });
  for (let i = 0; i < 200; i++) {
    const dir = path.join(sourceRoot, `module-${String(i % 10).padStart(2, '0')}`);
    mkdirSync(dir, { recursive: true });
    writeFileSync(path.join(dir, `source-${String(i).padStart(3, '0')}.mjs`), `export const fixture${i} = ${JSON.stringify('x'.repeat(512))};\n`);
  }
  runGit(repo, 'init', '-b', 'main');
  runGit(repo, 'add', 'desktop');
  runGit(repo, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture');

  const scanSamples = [], gitSamples = [];
  let expected;
  for (let i = 0; i < 5; i++) {
    const scanStart = performance.now();
    const summary = scanSyntheticSources(sourceRoot);
    scanSamples.push(performance.now() - scanStart);
    expected ??= { files: summary.files, bytes: summary.bytes, digest: summary.digest };
    assert.deepEqual({ files: summary.files, bytes: summary.bytes, digest: summary.digest }, expected);

    const gitStart = performance.now();
    const listed = runGit(repo, 'ls-tree', '-r', '--name-only', 'HEAD');
    gitSamples.push(performance.now() - gitStart);
    assert.equal(listed.split(/\r?\n/).filter(Boolean).length, 200);
  }
  const median = values => Number([...values].sort((a, b) => a - b)[Math.floor(values.length / 2)].toFixed(3));
  const result = {
    scope: 'synthetic_fixture_only_no_merge', files: expected.files, bytes: expected.bytes,
    samples: 5, sourceReadHashMedianMs: median(scanSamples), gitTreeListMedianMs: median(gitSamples),
  };
  const rendered = JSON.stringify(result);
  assert.doesNotMatch(rendered, /[A-Z]:\\|source-\d|fixture@example/i);
  console.log(`managed-merge-perf ${rendered}`);
});
