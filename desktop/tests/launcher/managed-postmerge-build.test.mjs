import assert from 'node:assert/strict';
import fs, { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import childProcess from 'node:child_process';
import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { test } from 'node:test';
import { BUILD_POLICY, buildManagedPostmerge } from '../../launcher/managed-postmerge-build.mjs';
import { createHash } from 'node:crypto';
import { assertBuildRecordSize, assertStaticDiscoverySyntax, captureVerifiedSourceBytes, electronImportMetaOptions, encodeBuildOutput, expectedRollupNativeRelative, installVerifiedSourceReadOverlay, isPinnedRollupNativeAddon, managedViteResolveOptions, parseArgs, resolveBoundarySpecifier, validateMarkupAssetReferences, verifyChildInputBindings } from '../../scripts/managed-build-child.mjs';

const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(',')}]` : value && typeof value === 'object' ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}` : JSON.stringify(value);
const digest = value => createHash('sha256').update(canonical(value)).digest('hex');

test('new managed builds use the strict snapshot-v2 receipt policy', () => {
  assert.equal(BUILD_POLICY, 'configless_snapshot_v2');
});

test('rejects a toolchain outside sandbox desktop before creating the build operation', async () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'managed-build-refuse-'));
  try {
    const sandboxRoot = path.join(root, 'sandbox');
    mkdirSync(sandboxRoot);
    await assert.rejects(
      buildManagedPostmerge({ sandboxRoot, operationId: '2e07bcca-269f-4d43-8a92-8b99d534f1a2', toolchainRoot: root }),
      /toolchain.*worktrees.*sandbox.*desktop/i,
    );
    assert.equal(existsSync(path.join(sandboxRoot, 'artifacts', 'managed-postmerge-builds')), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('rejects malformed operation identity before touching the operation store', async () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'managed-build-refuse-'));
  try {
    const sandboxRoot = path.join(root, 'sandbox');
    mkdirSync(path.join(sandboxRoot, 'desktop'), { recursive: true });
    await assert.rejects(
      buildManagedPostmerge({ sandboxRoot, operationId: '../escape', toolchainRoot: path.join(sandboxRoot, 'desktop') }),
      /operation.*UUID|operation.*identity/i,
    );
    assert.equal(existsSync(path.join(sandboxRoot, 'artifacts', 'managed-postmerge-builds')), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('child arguments bind both receipt digests supplied by the verified parent', () => {
  const root = path.resolve('F:/fixture/.worktrees/sandbox/desktop');
  const toolchain = { format: 1, mode: 'managed_postmerge_toolchain_v1', root, packageJsonSha256: '1'.repeat(64), packageLockSha256: '2'.repeat(64), node: {}, files: [] };
  toolchain.toolchainDigest = digest(toolchain);
  const sourceRoot = path.resolve('F:/op/source'), buildRoot = path.resolve('F:/op/build');
  const expectedSnapshotDigest = '3'.repeat(64), args = parseArgs([
    '--source-root', sourceRoot, '--build-root', buildRoot, '--toolchain-root', root,
    '--snapshot-digest', expectedSnapshotDigest, '--toolchain-digest', toolchain.toolchainDigest,
  ]);
  assert.equal(args.toolchainRoot, root);
  assert.equal(args.expectedSnapshotDigest, expectedSnapshotDigest);
  assert.equal(args.expectedToolchainDigest, toolchain.toolchainDigest);
  assert.throws(() => parseArgs([
    '--source-root', sourceRoot, '--build-root', buildRoot, '--toolchain-root', root,
    '--snapshot-digest', expectedSnapshotDigest, '--toolchain-digest', 'invalid',
  ]), /fixed|digest/i);
});

test('self-consistent swapped child records still fail the parent digest binding', () => {
  const snapshotBody = { format: 1, mode: 'synthetic_compile_probe_v1', files: [] };
  const snapshot = { ...snapshotBody, snapshotDigest: digest(snapshotBody) };
  const toolchainBody = { format: 1, mode: 'managed_postmerge_toolchain_v1', root: 'F:/x/.worktrees/sandbox/desktop', packageJsonSha256: '1'.repeat(64), packageLockSha256: '2'.repeat(64), node: {}, files: [] };
  const toolchainRecord = { ...toolchainBody, toolchainDigest: digest(toolchainBody) };
  verifyChildInputBindings({ snapshot, toolchainRecord, expectedSnapshotDigest: snapshot.snapshotDigest, expectedToolchainDigest: toolchainRecord.toolchainDigest });
  const swappedBody = { ...toolchainBody, files: [{ path: 'node_modules/other.js', mode: '100644', sha256: '5'.repeat(64), byteLength: 1 }] };
  const swapped = { ...swappedBody, toolchainDigest: digest(swappedBody) };
  assert.throws(() => verifyChildInputBindings({ snapshot, toolchainRecord: swapped, expectedSnapshotDigest: snapshot.snapshotDigest, expectedToolchainDigest: toolchainRecord.toolchainDigest }), /differs from parent-verified digest/i);
  const swappedSnapshotBody = { ...snapshotBody, files: [{ path: 'desktop/src/entry.ts', sha256: '6'.repeat(64), byteLength: 1 }] };
  const swappedSnapshot = { ...swappedSnapshotBody, snapshotDigest: digest(swappedSnapshotBody) };
  assert.throws(() => verifyChildInputBindings({ snapshot: swappedSnapshot, toolchainRecord, expectedSnapshotDigest: snapshot.snapshotDigest, expectedToolchainDigest: toolchainRecord.toolchainDigest }), /differs from parent-verified digest/i);
});

test('child CLI rejects a self-consistent toolchain manifest that differs from the parent digest before guard or build', () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'managed-build-child-bind-'));
  try {
    const operationRoot = path.join(root, 'operation');
    const sourceRoot = path.join(operationRoot, 'source'), buildRoot = path.join(operationRoot, 'build');
    const toolchainRoot = path.resolve('F:/my-agent-main/.worktrees/sandbox/desktop');
    mkdirSync(sourceRoot, { recursive: true }); mkdirSync(path.join(operationRoot, 'empty-env'), { recursive: true });
    const snapshotBody = { format: 1, mode: 'synthetic_compile_probe_v1', files: [] };
    const snapshot = { ...snapshotBody, snapshotDigest: digest(snapshotBody) };
    const parentToolchainBody = {
      format: 1, mode: 'managed_postmerge_toolchain_v1', root: toolchainRoot,
      packageJsonSha256: '1'.repeat(64), packageLockSha256: '2'.repeat(64), node: {}, files: [],
    };
    const parentToolchainDigest = digest(parentToolchainBody);
    const changedBody = { ...parentToolchainBody, files: [{ path: 'node_modules/poison.js', mode: '100644', sha256: '3'.repeat(64), byteLength: 1 }] };
    const changedRecord = { ...changedBody, toolchainDigest: digest(changedBody) };
    writeFileSync(path.join(operationRoot, 'source.json'), `${canonical(snapshot)}\n`);
    writeFileSync(path.join(operationRoot, 'toolchain.json'), `${canonical(changedRecord)}\n`);
    const child = path.resolve('desktop/scripts/managed-build-child.mjs');
    const env = {};
    for (const name of ['SystemRoot', 'WINDIR', 'TEMP', 'TMP']) if (process.env[name]) env[name] = process.env[name];
    env.PATH = path.dirname(process.execPath);
    const run = spawnSync(process.execPath, [
      child, '--source-root', sourceRoot, '--build-root', buildRoot, '--toolchain-root', toolchainRoot,
      '--snapshot-digest', snapshot.snapshotDigest, '--toolchain-digest', parentToolchainDigest,
    ], { cwd: root, env, encoding: 'buffer', windowsHide: true, timeout: 15_000, maxBuffer: 64 * 1024 });
    assert.equal(run.status, 1);
    assert.equal(run.stdout.length, 0);
    assert.match(run.stderr.toString('utf8'), /toolchain record differs from parent-verified digest/i);
    assert.equal(existsSync(buildRoot), false);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('source-relative imports resolve extensionless modules within source and toolchain roots', () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'managed-build-resolve-'));
  try {
    const sourceRoot = path.join(root, 'op', 'source');
    const toolchainRoot = path.join(root, '.worktrees', 'sandbox', 'desktop');
    const importer = path.join(sourceRoot, 'desktop', 'src', 'main.ts');
    mkdirSync(path.dirname(importer), { recursive: true });
    mkdirSync(path.dirname(toolchainRoot), { recursive: true });
    writeFileSync(importer, '');
    writeFileSync(path.join(path.dirname(importer), 'local-view.tsx'), '');
    writeFileSync(path.join(root, 'outside.ts'), '');
    const resolved = resolveBoundarySpecifier({
      specifier: './local-view', importer, sourceRoot, toolchainRoot,
      packageRequire: createRequire(importer), extensionCandidates: ['.tsx'],
    });
    assert.equal(resolved, path.join(path.dirname(importer), 'local-view.tsx'));
    assert.throws(() => resolveBoundarySpecifier({
      specifier: '../../../../outside', importer, sourceRoot, toolchainRoot,
      packageRequire: createRequire(importer), extensionCandidates: ['.ts'],
    }), /escaped source/i);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('raw and url queries remain attached after committed-path validation', () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'managed-build-query-'));
  try {
    const sourceRoot = path.join(root, 'op', 'source'), toolchainRoot = path.join(root, '.worktrees', 'sandbox', 'desktop');
    const importer = path.join(sourceRoot, 'desktop', 'src', 'main.ts'), asset = path.join(sourceRoot, 'desktop', 'public', 'probe.txt');
    mkdirSync(path.dirname(importer), { recursive: true }); mkdirSync(path.dirname(toolchainRoot), { recursive: true }); mkdirSync(path.dirname(asset), { recursive: true });
    writeFileSync(importer, ''); writeFileSync(asset, 'pinned asset');
    const options = { importer, sourceRoot, toolchainRoot, sourceFiles: new Set(['desktop/public/probe.txt']), packageRequire: createRequire(importer) };
    assert.equal(resolveBoundarySpecifier({ ...options, specifier: '../public/probe.txt?raw' }), `${asset}?raw`);
    assert.equal(resolveBoundarySpecifier({ ...options, specifier: '../public/probe.txt?url' }), `${asset}?url`);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('verified reads use captured bytes after disk swap and restore; unknown source handles and outside reads fail closed', async () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'managed-build-cache-'));
  let restore;
  try {
    const sourceRoot = path.join(root, 'operation', 'source'), toolchainRoot = path.join(root, '.worktrees', 'sandbox', 'desktop');
    const sourceFile = path.join(sourceRoot, 'desktop', 'src', 'entry.ts'), unknownFile = path.join(sourceRoot, 'desktop', 'src', 'unknown.ts');
    mkdirSync(path.dirname(sourceFile), { recursive: true }); mkdirSync(path.dirname(toolchainRoot), { recursive: true });
    const committed = Buffer.from('export const value = "committed";\n');
    writeFileSync(sourceFile, committed); writeFileSync(unknownFile, 'unlisted');
    const entry = { path: 'desktop/src/entry.ts', mode: '100644', sha256: createHash('sha256').update(committed).digest('hex'), byteLength: committed.length };
    const body = { format: 1, mode: 'synthetic_compile_probe_v1', files: [entry] };
    const snapshot = { ...body, snapshotDigest: digest(body) };
    const cache = captureVerifiedSourceBytes({ sourceRoot, snapshot, toolchainRoot, toolchainFiles: new Set() });
    writeFileSync(sourceFile, 'export const value = "swapped";\n');
    restore = installVerifiedSourceReadOverlay(sourceRoot, cache);
    assert.deepEqual(fs.readFileSync(sourceFile), committed);
    assert.deepEqual(await fs.promises.readFile(sourceFile), committed);
    assert.deepEqual(fs.readdirSync(path.dirname(sourceFile)).sort(), ['entry.ts']);
    assert.equal(fs.existsSync(unknownFile), false);
    assert.throws(() => fs.statSync(unknownFile), /outside verified snapshot/i);
    assert.throws(() => fs.openSync(unknownFile, 'r'), /outside verified byte snapshot/i);
    assert.throws(() => fs.open(unknownFile, 'r', () => {}), /outside verified byte snapshot/i);
    await assert.rejects(fs.promises.open(unknownFile, 'r'), /outside verified byte snapshot/i);
    assert.throws(() => fs.createReadStream(unknownFile), /outside verified byte snapshot/i);
    assert.throws(() => fs.readFileSync(path.join(root, 'outside.txt')), /outside source cache and pinned toolchain inventory/i);
    await assert.rejects(fs.promises.readFile(path.join(root, 'outside.txt')), /outside source cache and pinned toolchain inventory/i);
    restore(); restore = undefined;
    writeFileSync(sourceFile, committed);
    restore = installVerifiedSourceReadOverlay(sourceRoot, cache);
    assert.deepEqual(fs.readFileSync(sourceFile), committed);
  } finally {
    restore?.();
    rmSync(root, { recursive: true, force: true });
  }
});

test('toolchain content and CJS/ESM module loads are hash-bound; addon and executable escapes are blocked', async () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'managed-build-toolchain-cache-'));
  let restore;
  try {
    const sourceRoot = path.join(root, 'operation', 'source');
    const toolchainRoot = path.join(root, '.worktrees', 'sandbox', 'desktop');
    const sourceFile = path.join(sourceRoot, 'desktop', 'src', 'entry.ts');
    const trustedFile = path.join(toolchainRoot, 'node_modules', 'probe', 'data.json');
    const staleFile = path.join(toolchainRoot, 'node_modules', 'probe', 'stale.json');
    const moduleFile = path.join(toolchainRoot, 'node_modules', 'probe', 'module.js');
    const esmFile = path.join(toolchainRoot, 'node_modules', 'probe', 'module.mjs');
    const outsideModule = path.join(root, 'outside.mjs');
    const rogueNativeFile = path.join(toolchainRoot, 'node_modules', 'probe', 'rogue.node');
    const esbuildExecutable = path.join(toolchainRoot, 'node_modules', '@esbuild', 'win32-x64', 'esbuild.exe');
    mkdirSync(path.dirname(sourceFile), { recursive: true }); mkdirSync(path.dirname(trustedFile), { recursive: true });
    mkdirSync(path.dirname(esbuildExecutable), { recursive: true });
    const sourceBytes = Buffer.from('export {}\n'), trustedBytes = Buffer.from('{"v":"pinned"}'), staleBytes = Buffer.from('{"v":"stable"}');
    const moduleBytes = Buffer.from("module.exports = 'module-pinned';\n");
    const esmBytes = Buffer.from("export const value = 'esm-pinned';\n");
    const esbuildBytes = Buffer.from('fixture executable bytes');
    writeFileSync(sourceFile, sourceBytes); writeFileSync(trustedFile, trustedBytes); writeFileSync(staleFile, staleBytes);
    writeFileSync(moduleFile, moduleBytes); writeFileSync(esmFile, esmBytes); writeFileSync(rogueNativeFile, Buffer.from('not a native module'));
    writeFileSync(esbuildExecutable, esbuildBytes); writeFileSync(outsideModule, "export const escape = true;\n");
    const files = [
      { path: 'node_modules/probe/data.json', mode: '100644', sha256: createHash('sha256').update(trustedBytes).digest('hex'), byteLength: trustedBytes.length },
      { path: 'node_modules/probe/stale.json', mode: '100644', sha256: createHash('sha256').update(staleBytes).digest('hex'), byteLength: staleBytes.length },
      { path: 'node_modules/probe/module.js', mode: '100644', sha256: createHash('sha256').update(moduleBytes).digest('hex'), byteLength: moduleBytes.length },
      { path: 'node_modules/probe/module.mjs', mode: '100644', sha256: createHash('sha256').update(esmBytes).digest('hex'), byteLength: esmBytes.length },
      { path: 'node_modules/@esbuild/win32-x64/esbuild.exe', mode: '100644', sha256: createHash('sha256').update(esbuildBytes).digest('hex'), byteLength: esbuildBytes.length },
    ];
    const snapshotFile = { path: 'desktop/src/entry.ts', mode: '100644', sha256: createHash('sha256').update(sourceBytes).digest('hex'), byteLength: sourceBytes.length };
    const snapshotBody = { format: 1, mode: 'synthetic_compile_probe_v1', files: [snapshotFile] };
    const toolchainRecord = { files, packageJsonSha256: '1'.repeat(64), packageLockSha256: '2'.repeat(64) };
    const cache = captureVerifiedSourceBytes({
      sourceRoot, snapshot: { ...snapshotBody, snapshotDigest: digest(snapshotBody) }, toolchainRoot,
      toolchainFiles: new Set(files.map(entry => entry.path)), toolchainRecord,
    });
    const requirePinned = createRequire(path.join(toolchainRoot, 'package.json'));
    writeFileSync(staleFile, Buffer.from('{"v":"broken"}'));
    restore = installVerifiedSourceReadOverlay(sourceRoot, cache);
    assert.throws(() => fs.readFileSync(staleFile), /pinned toolchain bytes differ/i);
    assert.deepEqual(fs.readFileSync(trustedFile), trustedBytes);
    assert.equal(requirePinned('./node_modules/probe/module.js'), 'module-pinned');
    assert.equal((await import(pathToFileURL(esmFile).href)).value, 'esm-pinned');
    await assert.rejects(import(pathToFileURL(outsideModule).href), /resolved module outside pinned toolchain prohibited/i);
    assert.throws(() => requirePinned('./node_modules/probe/rogue.node'), /native \.node module load prohibited/i);
    assert.throws(() => childProcess.spawnSync(process.execPath, ['--version']), /outside the pinned esbuild allowlist/i);
    assert.throws(() => childProcess.exec('node --version'), /shell command execution is prohibited/i);
    restore(); restore = undefined;
    writeFileSync(trustedFile, Buffer.from('{"v":"broken"}'));
    writeFileSync(moduleFile, Buffer.from("module.exports = 'module-tampered';\n"));
    restore = installVerifiedSourceReadOverlay(sourceRoot, cache);
    assert.deepEqual(fs.readFileSync(trustedFile), trustedBytes);
    delete requirePinned.cache[moduleFile];
    assert.equal(requirePinned('./node_modules/probe/module.js'), 'module-pinned');
    assert.throws(() => fs.openSync(trustedFile, 'r'), /toolchain file descriptor reads are prohibited/i);
  } finally { restore?.(); rmSync(root, { recursive: true, force: true }); }
});

test('only the exact active-platform Rollup native addon from the pinned inventory is allowed', () => {
  const toolchainRoot = path.resolve('F:/my-agent-main/.worktrees/sandbox/desktop');
  const allowedRelative = expectedRollupNativeRelative();
  assert.equal(allowedRelative, process.platform === 'win32' && process.arch === 'x64'
    ? 'node_modules/@rollup/rollup-win32-x64-msvc/rollup.win32-x64-msvc.node' : null);
  assert.equal(expectedRollupNativeRelative('linux', 'x64'), null);
  assert.equal(expectedRollupNativeRelative('darwin', 'arm64'), null);
  if (!allowedRelative) return;
  const exact = path.resolve(toolchainRoot, ...allowedRelative.split('/'));
  const pinned = new Map([[exact.toLowerCase(), { path: exact, sha256: 'a'.repeat(64), byteLength: 1 }]]);
  assert.equal(isPinnedRollupNativeAddon(exact, toolchainRoot, pinned), true);
  assert.equal(isPinnedRollupNativeAddon(path.join(path.dirname(exact), 'other.node'), toolchainRoot, pinned), false);
  assert.equal(isPinnedRollupNativeAddon(exact, toolchainRoot, new Map()), false);
  assert.equal(isPinnedRollupNativeAddon(path.resolve(toolchainRoot, 'node_modules/@rollup/rollup-win32-x64-gnu/rollup.win32-x64-gnu.node'), toolchainRoot, pinned), false);
});

test('Vite fixed resolver preserves canonical paths', () => {
  assert.deepEqual(managedViteResolveOptions(), { preserveSymlinks: true });
});

test('pinned TypeScript AST refuses commented directory globbing and dynamic import.meta.url templates', () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'managed-build-discovery-'));
  try {
    const sourceRoot = path.join(root, 'operation', 'source'), toolchainRoot = path.join(root, '.worktrees', 'sandbox', 'desktop');
    const file = path.join(sourceRoot, 'desktop', 'src', 'entry.ts');
    mkdirSync(path.dirname(file), { recursive: true }); mkdirSync(path.dirname(toolchainRoot), { recursive: true });
    const requireToolchain = createRequire(path.join('F:/my-agent-main/.worktrees/sandbox/desktop', 'package.json'));
    const ts = requireToolchain('typescript');
    for (const [code, expected] of [
      ['const pages = import /* comment */ . meta . globEager("./pages/*.ts");', /import\.meta\.glob.*prohibited/i],
      ['const pages = import.meta /* comment */ ["glob"]("./pages/*.ts");', /import\.meta\.glob.*prohibited/i],
      ['const asset = new URL((`./assets/${name}.png`), (import.meta.url));', /dynamic new URL.*prohibited/i],
    ]) {
      writeFileSync(file, code);
      const bytes = Buffer.from(code);
      const entry = { path: 'desktop/src/entry.ts', mode: '100644', sha256: createHash('sha256').update(bytes).digest('hex'), byteLength: bytes.length };
      const body = { format: 1, mode: 'synthetic_compile_probe_v1', files: [entry] };
      const sourceCache = captureVerifiedSourceBytes({ sourceRoot, snapshot: { ...body, snapshotDigest: digest(body) }, toolchainRoot, toolchainFiles: new Set() });
      assert.throws(() => assertStaticDiscoverySyntax({ sourceRoot, sourceFiles: new Set([entry.path]), sourceCache, ts }), expected);
    }
    const code = 'const asset = new URL(("./assets/composer-companion.png"), (import.meta.url));';
    writeFileSync(file, code);
    const bytes = Buffer.from(code);
    const entry = { path: 'desktop/src/entry.ts', mode: '100644', sha256: createHash('sha256').update(bytes).digest('hex'), byteLength: bytes.length };
    const body = { format: 1, mode: 'synthetic_compile_probe_v1', files: [entry] };
    const sourceCache = captureVerifiedSourceBytes({ sourceRoot, snapshot: { ...body, snapshotDigest: digest(body) }, toolchainRoot, toolchainFiles: new Set() });
    assert.doesNotThrow(() => assertStaticDiscoverySyntax({ sourceRoot, sourceFiles: new Set([entry.path]), sourceCache, ts }));
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('static discovery exempts fixed test roots but rejects them as runtime imports', () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'managed-build-runtime-scope-'));
  try {
    const sourceRoot = path.join(root, 'operation', 'source');
    const toolchainRoot = path.join(root, '.worktrees', 'sandbox', 'desktop');
    const importer = path.join(sourceRoot, 'desktop', 'src', 'main.ts');
    const runtimeTs = path.join(sourceRoot, 'desktop', 'src', 'runtime.ts');
    const testTs = path.join(sourceRoot, 'desktop', 'tests', 'probe.ts');
    const agentTestTs = path.join(sourceRoot, 'agent-core', 'tests', 'probe.ts');
    mkdirSync(path.dirname(importer), { recursive: true });
    mkdirSync(path.dirname(testTs), { recursive: true });
    mkdirSync(path.dirname(toolchainRoot), { recursive: true });
    writeFileSync(importer, ''); writeFileSync(runtimeTs, 'export const value = 1;');
    const testCode = 'const asset = new URL(`../src/assets/${name}.png`, import.meta.url);';
    writeFileSync(testTs, testCode);
    const requireToolchain = createRequire(path.join('F:/my-agent-main/.worktrees/sandbox/desktop', 'package.json'));
    const ts = requireToolchain('typescript');
    const makeCache = (relative, code) => {
      const file = path.join(sourceRoot, ...relative.split('/'));
      mkdirSync(path.dirname(file), { recursive: true });
      writeFileSync(file, code);
      const bytes = Buffer.from(code);
      const entry = { path: relative, mode: '100644', sha256: createHash('sha256').update(bytes).digest('hex'), byteLength: bytes.length };
      const body = { format: 1, mode: 'synthetic_compile_probe_v1', files: [entry] };
      const cache = captureVerifiedSourceBytes({ sourceRoot, snapshot: { ...body, snapshotDigest: digest(body) }, toolchainRoot, toolchainFiles: new Set() });
      return { entry, cache };
    };
    const testRecord = makeCache('desktop/tests/probe.ts', testCode);
    assert.doesNotThrow(() => assertStaticDiscoverySyntax({
      sourceRoot, sourceFiles: new Set([testRecord.entry.path]), sourceCache: testRecord.cache, ts,
    }));
    const agentTestRecord = makeCache('agent-core/tests/probe.ts', testCode);
    assert.doesNotThrow(() => assertStaticDiscoverySyntax({
      sourceRoot, sourceFiles: new Set([agentTestRecord.entry.path]), sourceCache: agentTestRecord.cache, ts,
    }));
    const runtimeCode = testCode.replace('../src/assets/', './assets/');
    const runtimeRecord = makeCache('desktop/src/runtime.ts', runtimeCode);
    assert.throws(() => assertStaticDiscoverySyntax({
      sourceRoot, sourceFiles: new Set([runtimeRecord.entry.path]), sourceCache: runtimeRecord.cache, ts,
    }), /dynamic new URL.*prohibited/i);

    const sourceFiles = new Set(['desktop/src/main.ts', 'desktop/src/runtime.ts', 'desktop/tests/probe.ts', 'agent-core/tests/probe.ts']);
    const options = { importer, sourceRoot, toolchainRoot, sourceFiles, toolchainFiles: new Set(), packageRequire: createRequire(path.join(toolchainRoot, 'package.json')) };
    assert.equal(resolveBoundarySpecifier({ ...options, specifier: './runtime.js' }), runtimeTs);
    for (const specifier of ['../tests/probe.ts', '../tests/probe.js', testTs, '../../agent-core/tests/probe.ts']) {
      assert.throws(() => resolveBoundarySpecifier({ ...options, specifier }), /fixed test root/i, `must reject ${specifier}`);
    }
    if (process.platform === 'win32') {
      const aliasedRoot = path.join(path.dirname(sourceRoot), path.basename(sourceRoot).toUpperCase());
      assert.equal(fs.realpathSync.native(aliasedRoot), sourceRoot);
      assert.throws(() => resolveBoundarySpecifier({ ...options, sourceRoot: aliasedRoot, specifier: './runtime.js' }), /source root.*canonical|alias/i);
    }
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('absolute Electron entry points resolve without an importer only inside committed source inventory', () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'managed-build-entry-'));
  try {
    const sourceRoot = path.join(root, 'op', 'source');
    const toolchainRoot = path.join(root, '.worktrees', 'sandbox', 'desktop');
    const entry = path.join(sourceRoot, 'desktop', 'electron', 'main.ts');
    mkdirSync(path.dirname(entry), { recursive: true });
    mkdirSync(path.dirname(toolchainRoot), { recursive: true });
    writeFileSync(entry, '');
    writeFileSync(path.join(root, 'outside.ts'), '');
    const resolved = resolveBoundarySpecifier({
      specifier: entry, importer: undefined, sourceRoot, toolchainRoot,
      sourceFiles: new Set(['desktop/electron/main.ts']), toolchainFiles: new Set(),
      packageRequire: createRequire(entry),
    });
    assert.equal(resolved, entry);
    assert.throws(() => resolveBoundarySpecifier({
      specifier: path.join(root, 'outside.ts'), importer: undefined, sourceRoot, toolchainRoot,
      sourceFiles: new Set(['desktop/electron/main.ts']), toolchainFiles: new Set(),
      packageRequire: createRequire(entry),
    }), /escaped source and pinned toolchain/i);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('bare modules resolve only when their final file is present in the toolchain inventory', () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'managed-build-module-'));
  try {
    const sourceRoot = path.join(root, 'op', 'source');
    const toolchainRoot = path.join(root, '.worktrees', 'sandbox', 'desktop');
    const importer = path.join(sourceRoot, 'desktop', 'src', 'main.ts');
    const moduleRoot = path.join(toolchainRoot, 'node_modules', 'probe-module');
    mkdirSync(path.dirname(importer), { recursive: true });
    mkdirSync(moduleRoot, { recursive: true });
    writeFileSync(importer, '');
    writeFileSync(path.join(toolchainRoot, 'package.json'), '{}');
    writeFileSync(path.join(moduleRoot, 'package.json'), '{"main":"index.js"}');
    writeFileSync(path.join(moduleRoot, 'index.js'), 'export default 1;');
    const packageRequire = createRequire(path.join(toolchainRoot, 'package.json'));
    assert.throws(() => resolveBoundarySpecifier({
      specifier: 'probe-module', importer, sourceRoot, toolchainRoot,
      sourceFiles: new Set(), toolchainFiles: new Set(), packageRequire,
    }), /outside pinned toolchain inventory/i);
    assert.equal(resolveBoundarySpecifier({
      specifier: 'probe-module', importer, sourceRoot, toolchainRoot,
      sourceFiles: new Set(), toolchainFiles: new Set(['node_modules/probe-module/index.js']), packageRequire,
    }), path.join(moduleRoot, 'index.js'));
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('HTML and CSS local asset URLs cannot escape materialized source', () => {
  const sourceRoot = path.resolve('F:/fixture/op/source');
  assert.doesNotThrow(() => validateMarkupAssetReferences({
    text: '<img src="/desktop/src/icon.svg"><style>.x{background:url(./image.png)}</style>',
    importer: path.join(sourceRoot, 'desktop/index.html'), sourceRoot,
  }));
  assert.throws(() => validateMarkupAssetReferences({
    text: '<link href="../../outside.css">', importer: path.join(sourceRoot, 'desktop/index.html'), sourceRoot,
  }), /asset.*escaped source/i);
  assert.throws(() => validateMarkupAssetReferences({
    text: "@import '../outside.css';", importer: path.join(sourceRoot, 'desktop/src/style.css'), sourceRoot,
    sourceFiles: new Set(['desktop/src/style.css']),
  }), /outside committed inventory/i);
});

test('canonical toolchain record limit is checked before operation writes', () => {
  assert.throws(() => assertBuildRecordSize({ files: [{ path: 'node_modules/a.js', payload: 'x'.repeat(16 * 1024 * 1024) }] }), /record.*16 MiB/i);
});

test('esbuild Uint8Array outputs become canonical base64 with exact decoded length', () => {
  const contents = Uint8Array.from([0, 127, 128, 255]);
  const result = encodeBuildOutput(contents);
  assert.deepEqual(result, { base64: 'AH+A/w==', byteLength: 4 });
  const decoded = Buffer.from(result.base64, 'base64');
  assert.equal(decoded.toString('base64'), result.base64);
  assert.equal(decoded.length, result.byteLength);
  assert.deepEqual(decoded, Buffer.from(contents));
});

test('Electron CommonJS bundle preserves fileURLToPath(import.meta.url) without starting Electron', async () => {
  const toolchainRoot = 'F:/my-agent-main/.worktrees/sandbox/desktop';
  const requireToolchain = createRequire(path.join(toolchainRoot, 'package.json'));
  const esbuild = requireToolchain('esbuild');
  const result = await esbuild.build({
    stdin: { contents: 'import {fileURLToPath} from "node:url"; module.exports=fileURLToPath(import.meta.url);', sourcefile: 'main.ts', loader: 'ts' },
    bundle: true, platform: 'node', format: 'cjs', target: 'node22', external: ['node:url'], tsconfigRaw: {},
    ...electronImportMetaOptions(), write: false, logLevel: 'silent',
  });
  const module = { exports: {} };
  const filename = 'C:\\fixture\\dist-electron\\main.js';
  const inertRequire = specifier => {
    assert.equal(specifier, 'node:url');
    return { fileURLToPath, pathToFileURL };
  };
  vm.runInNewContext(result.outputFiles[0].text, { module, exports: module.exports, require: inertRequire, __filename: filename });
  assert.equal(module.exports, filename);
  const preload = await esbuild.build({
    stdin: { contents: 'module.exports=42;', sourcefile: 'preload.ts', loader: 'ts' },
    bundle: true, platform: 'node', format: 'cjs', target: 'node22', tsconfigRaw: {},
    ...electronImportMetaOptions(), write: false, logLevel: 'silent',
  });
  const preloadModule = { exports: {} };
  vm.runInNewContext(preload.outputFiles[0].text, {
    module: preloadModule, exports: preloadModule.exports,
    require: specifier => { throw new Error(`unexpected sandboxed preload require: ${specifier}`); },
    __filename: 'C:\\fixture\\dist-electron\\preload.js',
  });
  assert.equal(preloadModule.exports, 42);
});
