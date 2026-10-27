import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { createManagedTypecheckOptions, managedElectronEntries, resolveBoundarySpecifier, selectManagedElectronEntry, validateRendererResolution } from '../../scripts/managed-build-child.mjs';
import { validateChildOutputs } from '../../launcher/managed-postmerge-build.mjs';

const output = file => ({ path: file, mode: '100644', byteLength: 1, base64: 'eA==' });
const baseOutputs = [
  output('desktop/dist-electron/main.js'), output('desktop/dist-electron/preload.js'), output('desktop/dist/index.html'),
];

test('selects the committed dispatch wrapper when it is in the verified source inventory', () => {
  const desktop = path.resolve('fixture/source/desktop');
  assert.equal(selectManagedElectronEntry({ desktopSource: desktop, sourceFiles: new Set([
    'desktop/electron/main.ts', 'desktop/electron/entry.ts',
  ]) }), path.join(desktop, 'electron', 'entry.ts'));
});

test('keeps legacy committed main.ts as the entry for older minimal fixtures', () => {
  const desktop = path.resolve('fixture/source/desktop');
  assert.equal(selectManagedElectronEntry({ desktopSource: desktop, sourceFiles: new Set([
    'desktop/electron/main.ts',
  ]) }), path.join(desktop, 'electron', 'main.ts'));
});

test('adds only the named managed-preload entry for a committed managed wrapper', () => {
  const desktop = path.resolve('fixture/source/desktop');
  const entries = managedElectronEntries({ desktopSource: desktop, sourceFiles: new Set([
    'desktop/electron/entry.ts', 'desktop/electron/main.ts', 'desktop/electron/preload.ts', 'desktop/electron/managed-preload.ts',
  ]) });
  assert.deepEqual(Object.keys(entries), ['main', 'preload', 'managed-preload']);
  assert.equal(entries['managed-preload'], path.join(desktop, 'electron', 'managed-preload.ts'));
});

test('esbuild emits the managed preload entry under its fixed runtime filename', async t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'managed-preload-output-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const desktop = path.join(root, 'desktop');
  const electron = path.join(desktop, 'electron');
  mkdirSync(electron, { recursive: true });
  for (const name of ['entry.ts', 'main.ts', 'preload.ts', 'managed-preload.ts']) writeFileSync(path.join(electron, name), `module.exports = ${JSON.stringify(name)};\n`);
  const entries = managedElectronEntries({ desktopSource: desktop, sourceFiles: new Set([
    'desktop/electron/entry.ts', 'desktop/electron/main.ts', 'desktop/electron/preload.ts', 'desktop/electron/managed-preload.ts',
  ]) });
  const requireToolchain = createRequire(path.resolve('F:/my-agent-main/.worktrees/sandbox/desktop/package.json'));
  const esbuild = requireToolchain('esbuild');
  const built = await esbuild.build({
    entryPoints: entries, outdir: path.join(root, 'dist-electron'), entryNames: '[name]',
    bundle: true, platform: 'node', format: 'cjs', write: false, logLevel: 'silent',
  });
  assert.deepEqual(built.outputFiles.map(file => path.basename(file.path)).sort(), [
    'main.js', 'managed-preload.js', 'preload.js',
  ]);
});

test('actual esbuild managed-entry graph resolves committed JavaScript specifiers to TypeScript sources', async t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'managed-entry-ts-substitution-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const sourceRoot = path.join(root, 'source'), desktop = path.join(sourceRoot, 'desktop');
  const toolchainRoot = path.resolve('F:/my-agent-main/.worktrees/sandbox/desktop');
  const sources = new Map([
    ['desktop/electron/entry.ts', 'import { ordinary } from "./main.js"; import { managed } from "./managed-host-entry.js"; export const result = ordinary + ":" + managed;'],
    ['desktop/electron/main.ts', 'export const ordinary = "main-ts";'],
    ['desktop/electron/managed-host-entry.ts', 'import { context } from "./managed-host-context.js"; import { runtime } from "../launcher/runtime.mjs"; export const managed = context + ":" + runtime;'],
    ['desktop/electron/managed-host-context.ts', 'export const context = "context-ts";'],
    ['desktop/launcher/runtime.mts', 'export const runtime = "runtime-mts";'],
  ]);
  for (const [relative, code] of sources) {
    const file = path.join(sourceRoot, ...relative.split('/'));
    mkdirSync(path.dirname(file), { recursive: true }); writeFileSync(file, code);
  }
  writeFileSync(path.join(desktop, 'electron/main.js'), 'throw new Error("unverified JavaScript candidate must not load");');
  writeFileSync(path.join(desktop, 'electron/managed-host-entry.js'), 'throw new Error("unverified JavaScript candidate must not load");');
  const sourceFiles = new Set(sources.keys());
  const req = createRequire(path.join(toolchainRoot, 'package.json'));
  const esbuild = req('esbuild');
  const bundle = await esbuild.build({
    entryPoints: { main: path.join(desktop, 'electron/entry.ts') }, bundle: true,
    platform: 'node', format: 'cjs', write: false, logLevel: 'silent',
    plugins: [{
      name: 'verified-managed-entry-fixture',
      setup(build) {
        build.onResolve({ filter: /.*/ }, args => {
          if (args.kind === 'entry-point') return { path: path.resolve(args.path) };
          return { path: resolveBoundarySpecifier({
            specifier: args.path, importer: args.importer, sourceRoot, toolchainRoot,
            sourceFiles, toolchainFiles: new Set(), packageRequire: req, sourceBase: desktop,
          }) };
        });
        build.onLoad({ filter: /\.(?:ts|mts)$/ }, args => {
          const relative = path.relative(sourceRoot, args.path).split(path.sep).join('/');
          if (!sourceFiles.has(relative)) throw new Error(`source outside fixture inventory: ${relative}`);
          return { contents: readFileSync(args.path), loader: 'ts' };
        });
      },
    }],
  });
  assert.match(bundle.outputFiles[0].text, /main-ts/);
  assert.match(bundle.outputFiles[0].text, /context-ts/);
  assert.match(bundle.outputFiles[0].text, /runtime-mts/);
});

test('runtime JavaScript substitution refuses declaration-only candidates', t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'managed-entry-declaration-refusal-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const sourceRoot = path.join(root, 'source'), desktop = path.join(sourceRoot, 'desktop');
  const importer = path.join(desktop, 'electron', 'entry.ts');
  mkdirSync(path.dirname(importer), { recursive: true });
  writeFileSync(importer, '');
  writeFileSync(path.join(desktop, 'electron', 'only-types.d.ts'), 'export declare const onlyTypes: string;');
  assert.throws(() => resolveBoundarySpecifier({
    specifier: './only-types.js', importer, sourceRoot,
    toolchainRoot: path.resolve('F:/my-agent-main/.worktrees/sandbox/desktop'),
    sourceFiles: new Set(['desktop/electron/entry.ts', 'desktop/electron/only-types.d.ts']),
    toolchainFiles: new Set(), packageRequire: createRequire(importer), sourceBase: desktop,
  }), /source import target does not exist/i);
});

test('bare import-only package exports resolve to a pinned inventory file', t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'managed-import-exports-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const sourceRoot = path.join(root, 'source'), toolchainRoot = path.join(root, '.worktrees', 'sandbox', 'desktop');
  const importer = path.join(sourceRoot, 'desktop', 'electron', 'managed-host-entry.ts');
  const packageRoot = path.join(toolchainRoot, 'node_modules', '@probe', 'import-only');
  mkdirSync(path.dirname(importer), { recursive: true }); mkdirSync(path.join(packageRoot, 'dist'), { recursive: true });
  writeFileSync(importer, '');
  writeFileSync(path.join(toolchainRoot, 'package.json'), '{}');
  writeFileSync(path.join(sourceRoot, 'index.html'), '<!doctype html><script type="module" src="/main.js"></script>');
  writeFileSync(path.join(packageRoot, 'package.json'), JSON.stringify({
    name: '@probe/import-only', type: 'module', exports: { '.': { import: './dist/index.mjs', types: './dist/index.d.ts' } },
  }));
  writeFileSync(path.join(packageRoot, 'dist', 'index.mjs'), 'export const pinned = true;');
  const resolved = resolveBoundarySpecifier({
    specifier: '@probe/import-only', importer, sourceRoot, toolchainRoot,
    sourceFiles: new Set(), toolchainFiles: new Set([
      'node_modules/@probe/import-only/package.json', 'node_modules/@probe/import-only/dist/index.mjs',
    ]), packageRequire: createRequire(path.join(toolchainRoot, 'package.json')),
  });
  assert.equal(resolved, path.join(packageRoot, 'dist', 'index.mjs'));
  assert.throws(() => resolveBoundarySpecifier({
    specifier: '@probe/import-only', importer, sourceRoot, toolchainRoot,
    sourceFiles: new Set(), toolchainFiles: new Set(['node_modules/@probe/import-only/package.json']),
    packageRequire: createRequire(path.join(toolchainRoot, 'package.json')),
  }), /outside pinned toolchain inventory/i);
});

test('renderer exports prefer browser conditions while Electron keeps the Node export', t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'managed-renderer-conditions-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const sourceRoot = path.join(root, 'source'), toolchainRoot = path.join(root, '.worktrees', 'sandbox', 'desktop');
  const importer = path.join(sourceRoot, 'desktop', 'src', 'main.ts');
  const packageRoot = path.join(toolchainRoot, 'node_modules', 'condition-probe');
  mkdirSync(path.dirname(importer), { recursive: true }); mkdirSync(path.join(packageRoot, 'dist-node'), { recursive: true });
  mkdirSync(path.join(packageRoot, 'dist-browser'), { recursive: true }); mkdirSync(path.join(packageRoot, 'dist-default'), { recursive: true });
  writeFileSync(importer, ''); writeFileSync(path.join(toolchainRoot, 'package.json'), '{}');
  writeFileSync(path.join(packageRoot, 'package.json'), JSON.stringify({ name: 'condition-probe', exports: {
    '.': { node: './dist-node/index.js', browser: './dist-browser/index.js', default: './dist-default/index.js' },
  } }));
  for (const [dir, marker] of [['dist-node', 'node'], ['dist-browser', 'browser'], ['dist-default', 'default']]) {
    writeFileSync(path.join(packageRoot, dir, 'index.js'), `export const target = ${JSON.stringify(marker)};`);
  }
  const inventory = new Set([
    'node_modules/condition-probe/package.json', 'node_modules/condition-probe/dist-node/index.js',
    'node_modules/condition-probe/dist-browser/index.js', 'node_modules/condition-probe/dist-default/index.js',
  ]);
  const resolve = conditions => resolveBoundarySpecifier({
    specifier: 'condition-probe', importer, sourceRoot, toolchainRoot,
    sourceFiles: new Set(), toolchainFiles: inventory,
    packageRequire: createRequire(path.join(toolchainRoot, 'package.json')), conditions,
  });
  assert.equal(resolve(['browser', 'import', 'default']), path.join(packageRoot, 'dist-browser', 'index.js'));
  assert.equal(resolve(['node', 'import', 'default']), path.join(packageRoot, 'dist-node', 'index.js'));
});

test('native Vite resolver handles wildcard style exports inside the pinned inventory', async t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'managed-vite-wildcard-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const toolchainRoot = path.join(root, '.worktrees', 'sandbox', 'desktop');
  const sourceRoot = path.join(toolchainRoot, 'fixture-source');
  const packageRoot = path.join(toolchainRoot, 'node_modules', 'highlight.js');
  const importer = path.join(sourceRoot, 'main.js');
  mkdirSync(sourceRoot, { recursive: true }); mkdirSync(path.join(packageRoot, 'styles'), { recursive: true });
  writeFileSync(path.join(toolchainRoot, 'package.json'), '{}');
  writeFileSync(path.join(packageRoot, 'package.json'), JSON.stringify({
    name: 'highlight.js', exports: { './styles/*': './styles/*' },
  }));
  writeFileSync(path.join(packageRoot, 'styles', 'github.css'), 'body { color: rgb(1, 2, 3); }');
  writeFileSync(importer, 'import "highlight.js/styles/github.css"; export const loaded = true;');
  const sourceFiles = new Set(['index.html', 'main.js']);
  const toolchainFiles = new Set(['node_modules/highlight.js/package.json', 'node_modules/highlight.js/styles/github.css']);
  const requireToolchain = createRequire(path.resolve('F:/my-agent-main/.worktrees/sandbox/desktop/package.json'));
  const { createServer } = requireToolchain('vite');
  const server = await createServer({
    configFile: false, root: sourceRoot, publicDir: false, logLevel: 'silent',
    plugins: [{
      name: 'pinned-vite-wildcard-probe', enforce: 'pre',
      async resolveId(specifier, from) {
        if (specifier !== 'highlight.js/styles/github.css') return null;
        const resolved = await this.resolve(specifier, from, { skipSelf: true });
        return validateRendererResolution(resolved, { sourceRoot, toolchainRoot, sourceFiles, toolchainFiles });
      },
    }],
  });
  try {
    const resolved = await server.pluginContainer.resolveId('highlight.js/styles/github.css', importer);
    assert.equal(typeof resolved, 'object');
    assert.equal(resolved.id.replaceAll('\\', '/'), path.join(packageRoot, 'styles', 'github.css').replaceAll('\\', '/'));
  } finally { await server.close(); }
});

test('refuses a managed wrapper inventory missing its managed preload TypeScript entry', () => {
  const desktop = path.resolve('fixture/source/desktop');
  assert.throws(() => managedElectronEntries({ desktopSource: desktop, sourceFiles: new Set([
    'desktop/electron/entry.ts', 'desktop/electron/main.ts', 'desktop/electron/preload.ts',
  ]) }), /required managed Electron source input is absent from committed inventory: desktop\/electron\/managed-preload\.ts/i);
});

test('parent build validation requires the third output only for managed snapshots', () => {
  const frame = outputs => Buffer.from(`${JSON.stringify({ status: 'built', outputs })}\n`);
  assert.equal(validateChildOutputs(frame(baseOutputs), path.resolve('fixture/build')).length, 3);
  const managedPreload = output('desktop/dist-electron/managed-preload.js');
  const metadataHelper = output('desktop/dist-electron/managed-clean-git-metadata.mjs');
  assert.throws(() => validateChildOutputs(frame([...baseOutputs.slice(0, 2), managedPreload, baseOutputs[2]]),
    path.resolve('fixture/build'), { managedEntry: true }), /required output missing/i);
  assert.equal(validateChildOutputs(frame([...baseOutputs.slice(0, 2), managedPreload, metadataHelper, baseOutputs[2]]),
    path.resolve('fixture/build'), { managedEntry: true }).length, 5);
});

test('parent build validation rejects every unlisted Electron output', () => {
  const frame = outputs => Buffer.from(`${JSON.stringify({ status: 'built', outputs })}\n`);
  assert.throws(() => validateChildOutputs(frame([
    ...baseOutputs, output('desktop/dist-electron/arbitrary.js'),
  ]), path.resolve('fixture/build'), { managedEntry: true }), /outside the exact build allowlist/i);
  assert.throws(() => validateChildOutputs(frame([
    ...baseOutputs, output('desktop/dist-electron/managed-preload.js'), output('desktop/dist-electron/managed-clean-git-metadata.mjs'),
  ]), path.resolve('fixture/build')), /outside the exact build allowlist/i);
  assert.throws(() => validateChildOutputs(frame([
    ...baseOutputs, output('desktop/dist-electron/managed-clean-git-metadata.mjs'),
  ]), path.resolve('fixture/build'), { managedEntry: true }), /required output missing/i);
  assert.throws(() => validateChildOutputs(frame([
    ...baseOutputs, output('desktop/dist-electron/managed-preload.js'), output('desktop/dist-electron/managed-clean-git-metadata.mjs'), output('desktop/dist-electron/extra.mjs'),
  ]), path.resolve('fixture/build'), { managedEntry: true }), /outside the exact build allowlist/i);
});

test('managed typecheck options accept discriminant narrowing and local .ts imports in a small graph', t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'managed-typecheck-options-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const main = path.join(root, 'main.ts'), sidecar = path.join(root, 'sidecar.ts');
  writeFileSync(sidecar, 'export type SidecarStartResult = { ok: true } | { ok: false; message?: string };\nexport function start(): SidecarStartResult { return { ok: false }; }\n');
  writeFileSync(main, 'import { start } from "./sidecar.ts";\nconst result = start();\nif (!result.ok) { const message: string | undefined = result.message; void message; }\n');
  const requireToolchain = createRequire(path.resolve('F:/my-agent-main/.worktrees/sandbox/desktop/package.json'));
  const ts = requireToolchain('typescript');
  const options = createManagedTypecheckOptions(ts, path.resolve('F:/my-agent-main/.worktrees/sandbox/desktop'));
  const baseline = ts.getPreEmitDiagnostics(ts.createProgram([main], {
    ...options, strictNullChecks: false, allowImportingTsExtensions: false,
  })).map(diagnostic => diagnostic.code);
  assert.ok(baseline.includes(2339), 'baseline options must reproduce the union-property diagnostic');
  assert.ok(baseline.includes(5097), 'baseline options must reproduce the .ts import diagnostic');
  const program = ts.createProgram([main], options);
  assert.deepEqual(ts.getPreEmitDiagnostics(program).map(diagnostic => diagnostic.code), []);
});
