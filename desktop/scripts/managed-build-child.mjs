import { createHash } from 'node:crypto';
import fs from 'node:fs';
import { Readable } from 'node:stream';
import { existsSync, lstatSync, readdirSync, realpathSync } from 'node:fs';
import path from 'node:path';
import { builtinModules, createRequire, syncBuiltinESMExports } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { readCanonical, readStableBytes, regularFile, safeDirectory, safeRelative } from '../launcher/managed-build-records.mjs';

const fail = message => { throw new Error(`managed_postmerge_build_child_invalid: ${message}`); };
const within = (root, candidate) => {
  const relative = path.relative(root, candidate);
  return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
};
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(',')}]` : value && typeof value === 'object' ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}` : JSON.stringify(value);
const HASH = /^[a-f0-9]{64}$/;
const SOURCE_EXTENSIONS = ['.mjs', '.js', '.mts', '.ts', '.jsx', '.tsx', '.json', '.css', '.scss', '.sass', '.less', '.svg', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.woff', '.woff2', '.ttf', '.otf'];
const MANAGED_GIT_METADATA_SOURCE = 'desktop/launcher/managed-clean-git-metadata.mjs';
const MANAGED_GIT_METADATA_OUTPUT = 'desktop/dist-electron/managed-clean-git-metadata.mjs';
const MAX_RECORD_BYTES = 16 * 1024 * 1024;
function assertBuildRecordSize(value) {
  const size = Buffer.byteLength(`${canonical(value)}\n`);
  if (size > MAX_RECORD_BYTES) fail('canonical build record exceeds 16 MiB');
  return size;
}
export { assertBuildRecordSize };
function encodeBuildOutput(contents) {
  if (!Buffer.isBuffer(contents) && !ArrayBuffer.isView(contents)) fail('build output must be a byte buffer');
  const bytes = Buffer.from(contents);
  return { base64: bytes.toString('base64'), byteLength: bytes.length };
}
export { encodeBuildOutput };
function electronImportMetaOptions() {
  return {
    banner: { js: 'const __managedImportMeta = { get url() { return require("node:url").pathToFileURL(__filename).href; } };' },
    define: { 'import.meta.url': '__managedImportMeta.url' },
  };
}
export { electronImportMetaOptions };
function managedViteResolveOptions() {
  // All materialized source and pinned toolchain paths are canonical and link-free.
  // Vite's default Windows resolver probes `net use` before realpath optimization;
  // preserving these already-validated paths avoids that shell-launch path entirely.
  return { preserveSymlinks: true };
}
export { managedViteResolveOptions };
function selectManagedElectronEntry({ desktopSource, sourceFiles }) {
  const entryPath = sourceFiles.has('desktop/electron/entry.ts') ? 'entry.ts' : 'main.ts';
  const relative = `desktop/electron/${entryPath}`;
  if (!sourceFiles.has(relative)) fail('required Electron entry is absent from committed inventory');
  return path.join(desktopSource, 'electron', entryPath);
}
export { selectManagedElectronEntry };
function managedElectronEntries({ desktopSource, sourceFiles }) {
  const entries = {
    main: selectManagedElectronEntry({ desktopSource, sourceFiles }),
    preload: path.join(desktopSource, 'electron', 'preload.ts'),
  };
  if (sourceFiles.has('desktop/electron/entry.ts')) {
    if (!sourceFiles.has('desktop/electron/managed-preload.ts')) fail('required managed Electron source input is absent from committed inventory: desktop/electron/managed-preload.ts');
    entries['managed-preload'] = path.join(desktopSource, 'electron', 'managed-preload.ts');
  }
  return entries;
}
export { managedElectronEntries };
function resolvePinnedImportExport(specifier, { toolchainRoot, toolchainFiles, conditions = ['node', 'import', 'default'] }) {
  const parts = specifier.split('/');
  const packageParts = parts[0]?.startsWith('@') ? parts.slice(0, 2) : parts.slice(0, 1);
  if (!packageParts.length || packageParts.length !== (parts[0].startsWith('@') ? 2 : 1)) return null;
  const packageName = packageParts.join('/');
  const subpath = parts.length > packageParts.length ? `./${parts.slice(packageParts.length).join('/')}` : '.';
  const packageRoot = path.resolve(toolchainRoot, 'node_modules', ...packageParts);
  if (!within(path.join(toolchainRoot, 'node_modules'), packageRoot)) return null;
  const manifestPath = path.join(packageRoot, 'package.json');
  const manifestRelative = path.relative(toolchainRoot, manifestPath).split(path.sep).join('/');
  if (!toolchainFiles?.has(manifestRelative) || !existsSync(manifestPath)) return null;
  let manifest;
  try { manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8')); } catch { return null; }
  if (manifest.name !== packageName || !Object.hasOwn(manifest, 'exports')) return null;
  let target = manifest.exports;
  const subpathMap = target && typeof target === 'object' && !Array.isArray(target)
    && Object.keys(target).some(key => key.startsWith('.'));
  if (subpath !== '.' && !subpathMap) return { matched: true, target: null };
  if (subpathMap) target = target[subpath];
  const selectImportTarget = value => {
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) {
      for (const candidate of value) { const selected = selectImportTarget(candidate); if (selected) return selected; }
      return null;
    }
    if (!value || typeof value !== 'object') return null;
    for (const [condition, candidate] of Object.entries(value)) {
      if (!conditions.includes(condition)) continue;
      const selected = selectImportTarget(candidate);
      if (selected) return selected;
    }
    return null;
  };
  const selected = selectImportTarget(target);
  if (typeof selected !== 'string' || !selected.startsWith('./')) return { matched: true, target: null };
  const resolved = path.resolve(packageRoot, selected);
  if (!within(packageRoot, resolved)) return { matched: true, target: null };
  return { matched: true, target: resolved };
}
export { resolvePinnedImportExport };
function parseArgs(argv) {
  if (argv.length !== 10 || argv[0] !== '--source-root' || argv[2] !== '--build-root' || argv[4] !== '--toolchain-root'
      || argv[6] !== '--snapshot-digest' || argv[8] !== '--toolchain-digest') fail('fixed child arguments required');
  const sourceRoot = path.resolve(argv[1]), buildRoot = path.resolve(argv[3]), toolchainRoot = path.resolve(argv[5]);
  const expectedSnapshotDigest = argv[7], expectedToolchainDigest = argv[9];
  if (sourceRoot !== argv[1] || buildRoot !== argv[3] || toolchainRoot !== argv[5]) fail('absolute normalized roots required');
  if (!HASH.test(expectedSnapshotDigest) || !HASH.test(expectedToolchainDigest)) fail('parent input digests must be lowercase SHA-256');
  if (path.basename(toolchainRoot) !== 'desktop' || path.basename(path.dirname(toolchainRoot)) !== 'sandbox'
      || path.basename(path.dirname(path.dirname(toolchainRoot))) !== '.worktrees') fail('toolchain root is not a conventional sandbox desktop');
  const operationRoot = path.dirname(sourceRoot);
  return { sourceRoot, buildRoot, operationRoot, toolchainRoot, expectedSnapshotDigest, expectedToolchainDigest };
}
function verifyChildInputBindings({ snapshot, toolchainRecord, expectedSnapshotDigest, expectedToolchainDigest }) {
  if (!HASH.test(expectedSnapshotDigest || '') || !HASH.test(expectedToolchainDigest || '')) fail('parent input digest arguments invalid');
  if (snapshot?.snapshotDigest !== expectedSnapshotDigest) fail('source snapshot differs from parent-verified digest');
  if (toolchainRecord?.toolchainDigest !== expectedToolchainDigest) fail('toolchain record differs from parent-verified digest');
  if (!HASH.test(snapshot.snapshotDigest || '') || !HASH.test(toolchainRecord.toolchainDigest || '')) fail('child input receipt digest invalid');
  const snapshotBody = { ...snapshot }; delete snapshotBody.snapshotDigest;
  if (sha(Buffer.from(canonical(snapshotBody))) !== snapshot.snapshotDigest) fail('source snapshot self-digest invalid');
  const toolchainBody = { ...toolchainRecord }; delete toolchainBody.toolchainDigest;
  if (sha(Buffer.from(canonical(toolchainBody))) !== toolchainRecord.toolchainDigest) fail('toolchain record self-digest invalid');
  if (toolchainRecord.format !== 1 || toolchainRecord.mode !== 'managed_postmerge_toolchain_v1'
      || !Array.isArray(toolchainRecord.files)) fail('toolchain record schema invalid');
}
export { verifyChildInputBindings };
function assertPlainDirectory(target) {
  const stat = lstatSync(target);
  if (!stat.isDirectory() || stat.isSymbolicLink() || (process.platform === 'win32' && (stat.attributes & 0x400))) fail('expected a real directory');
}
const pathKey = value => process.platform === 'win32' ? path.resolve(value).toLowerCase() : path.resolve(value);
function captureVerifiedSourceBytes({ sourceRoot, snapshot, toolchainRoot, toolchainFiles = new Set(), toolchainRecord }) {
  safeDirectory(sourceRoot);
  if (!snapshot || !Array.isArray(snapshot.files) || snapshot.files.length > 20_000) fail('source byte snapshot schema or file count invalid');
  if (!HASH.test(snapshot.snapshotDigest || '')) fail('source snapshot digest invalid before byte capture');
  const body = { ...snapshot }; delete body.snapshotDigest;
  if (sha(Buffer.from(canonical(body))) !== snapshot.snapshotDigest) fail('source snapshot digest invalid before byte capture');
  const bytesByAbsolutePath = new Map(), relativePaths = new Set(), folded = new Set(), directories = new Set(['']);
  let total = 0;
  for (const entry of snapshot.files) {
    if (!entry || !safeRelative(entry.path) || !['100644', '100755'].includes(entry.mode) || !HASH.test(entry.sha256 || '')
        || !Number.isSafeInteger(entry.byteLength) || entry.byteLength < 0 || relativePaths.has(entry.path) || folded.has(entry.path.toLowerCase())) fail('source file inventory entry invalid');
    relativePaths.add(entry.path); folded.add(entry.path.toLowerCase());
    const directoryParts = entry.path.split('/'); directoryParts.pop();
    while (directoryParts.length) { directories.add(directoryParts.join('/')); directoryParts.pop(); }
    const absolute = path.resolve(sourceRoot, ...entry.path.split('/'));
    if (!within(sourceRoot, absolute)) fail(`source file path escaped root: ${entry.path}`);
    const bytes = readStableBytes(regularFile(absolute, 64 * 1024 * 1024), 64 * 1024 * 1024);
    const stat = lstatSync(absolute);
    const mode = (stat.mode & 0o111) ? '100755' : '100644';
    if (bytes.length !== entry.byteLength || sha(bytes) !== entry.sha256 || mode !== entry.mode) fail(`source bytes differ from verified snapshot: ${entry.path}`);
    total += bytes.length;
    if (total > 64 * 1024 * 1024) fail('source byte cache exceeds 64 MiB');
    bytesByAbsolutePath.set(pathKey(absolute), Buffer.from(bytes));
  }
  const statsByAbsolutePath = new Map(), canonicalPathsByKey = new Map(), directoryEntries = new Map();
  const addMetadata = (absolute, mustBeDirectory) => {
    const stat = lstatSync(absolute);
    if (stat.isSymbolicLink() || (process.platform === 'win32' && (stat.attributes & 0x400))
        || (mustBeDirectory ? !stat.isDirectory() : !stat.isFile())) fail(`source metadata changed during snapshot capture: ${absolute}`);
    const key = pathKey(absolute);
    statsByAbsolutePath.set(key, Object.assign(Object.create(Object.getPrototypeOf(stat)), stat));
    canonicalPathsByKey.set(key, absolute);
  };
  for (const relative of [...directories].sort((a, b) => a.split('/').length - b.split('/').length || a.localeCompare(b))) {
    const absolute = relative ? path.resolve(sourceRoot, ...relative.split('/')) : sourceRoot;
    addMetadata(absolute, true);
  }
  for (const relative of relativePaths) addMetadata(path.resolve(sourceRoot, ...relative.split('/')), false);
  const addEntry = (directory, name, isDirectory) => {
    const absolute = directory ? path.join(sourceRoot, ...directory.split('/'), name) : path.join(sourceRoot, name);
    const key = pathKey(absolute), parentKey = pathKey(directory ? path.join(sourceRoot, ...directory.split('/')) : sourceRoot);
    const entries = directoryEntries.get(parentKey) || [];
    if (!entries.some(entry => entry.name === name)) entries.push({ name, isDirectory });
    directoryEntries.set(parentKey, entries);
    if (isDirectory) {
      const childrenKey = key;
      if (!directoryEntries.has(childrenKey)) directoryEntries.set(childrenKey, []);
    }
  };
  for (const relative of directories) {
    if (!relative) continue;
    const parts = relative.split('/'); addEntry(parts.slice(0, -1).join('/'), parts.at(-1), true);
  }
  for (const relative of relativePaths) {
    const parts = relative.split('/'); addEntry(parts.slice(0, -1).join('/'), parts.at(-1), false);
  }
  const trustedToolchainRecords = new Map();
  for (const relative of toolchainFiles) if (!safeRelative(relative) || !relative.startsWith('node_modules/')) fail('toolchain read allowlist path invalid');
  const addToolchainRecord = (relative, record) => {
    const absolute = path.resolve(toolchainRoot, ...relative.split('/'));
    if (!within(toolchainRoot, absolute)) fail(`toolchain content path escaped root: ${relative}`);
    const key = pathKey(absolute);
    if (trustedToolchainRecords.has(key)) fail(`duplicate toolchain content path: ${relative}`);
    trustedToolchainRecords.set(key, { path: absolute, ...record });
  };
  if (toolchainRecord) {
    if (!Array.isArray(toolchainRecord.files)) fail('toolchain content inventory missing');
    for (const entry of toolchainRecord.files) {
      if (!entry || !safeRelative(entry.path) || !entry.path.startsWith('node_modules/') || !['100644', '100755'].includes(entry.mode)
          || !HASH.test(entry.sha256 || '') || !Number.isSafeInteger(entry.byteLength) || entry.byteLength < 0) fail('toolchain content inventory entry invalid');
      addToolchainRecord(entry.path, { sha256: entry.sha256, byteLength: entry.byteLength, mode: entry.mode, maxBytes: entry.byteLength });
    }
    if (!HASH.test(toolchainRecord.packageJsonSha256 || '') || !HASH.test(toolchainRecord.packageLockSha256 || '')) fail('toolchain package metadata digests invalid');
    addToolchainRecord('package.json', { sha256: toolchainRecord.packageJsonSha256, maxBytes: 1024 * 1024 });
    addToolchainRecord('package-lock.json', { sha256: toolchainRecord.packageLockSha256, maxBytes: 16 * 1024 * 1024 });
  }
  for (const relative of toolchainFiles) if (!trustedToolchainRecords.has(pathKey(path.resolve(toolchainRoot, ...relative.split('/'))))) fail(`toolchain resolver inventory lacks bound bytes: ${relative}`);
  const trustedToolchainPaths = [...trustedToolchainRecords.keys()];
  return { bytesByAbsolutePath, relativePaths, byteLength: total, toolchainRoot, toolchainRecord, trustedToolchainPaths, trustedToolchainRecords, toolchainBytesByAbsolutePath: new Map(), statsByAbsolutePath, canonicalPathsByKey, directoryEntries };
}
export { captureVerifiedSourceBytes };

function assertStaticDiscoverySyntax({ sourceRoot, sourceFiles, sourceCache, ts }) {
  const unwrap = node => {
    while (node && (ts.isParenthesizedExpression(node) || ts.isAsExpression(node) || ts.isTypeAssertionExpression(node) || ts.isNonNullExpression(node))) node = node.expression;
    return node;
  };
  const isImportMeta = node => {
    node = unwrap(node);
    return ts.isMetaProperty(node) && node.keywordToken === ts.SyntaxKind.ImportKeyword && node.name.text === 'meta';
  };
  const isImportMetaUrl = node => {
    node = unwrap(node);
    return ts.isPropertyAccessExpression(node) && node.name.text === 'url' && isImportMeta(node.expression);
  };
  const globAccess = node => {
    node = unwrap(node);
    if (ts.isPropertyAccessExpression(node)) return ['glob', 'globEager'].includes(node.name.text) && isImportMeta(node.expression);
    if (ts.isElementAccessExpression(node)) {
      const argument = node.argumentExpression;
      return argument && (ts.isStringLiteral(argument) || ts.isNoSubstitutionTemplateLiteral(argument))
        && ['glob', 'globEager'].includes(argument.text) && isImportMeta(node.expression);
    }
    return false;
  };
  for (const relative of sourceFiles) {
    if (!/\.(?:[cm]?[jt]sx?)$/i.test(relative)) continue;
    const absolute = path.resolve(sourceRoot, ...relative.split('/'));
    const bytes = sourceBytesAt(sourceCache, absolute);
    if (!bytes) fail(`source AST input absent from verified cache: ${relative}`);
    const extension = path.extname(relative).toLowerCase();
    const scriptKind = extension === '.tsx' ? ts.ScriptKind.TSX : extension === '.jsx' ? ts.ScriptKind.JSX : extension === '.js' || extension === '.mjs' || extension === '.cjs' ? ts.ScriptKind.JS : ts.ScriptKind.TS;
    const sourceFile = ts.createSourceFile(relative, bytes.toString('utf8'), ts.ScriptTarget.Latest, true, scriptKind);
    const visit = node => {
      if (globAccess(node)) fail(`directory-discovery import.meta.glob is prohibited: ${relative}`);
      if (ts.isNewExpression(node) && ts.isIdentifier(unwrap(node.expression)) && unwrap(node.expression).text === 'URL'
          && node.arguments?.length === 2 && isImportMetaUrl(node.arguments[1])) {
        const first = unwrap(node.arguments[0]);
        if (!(ts.isStringLiteral(first) || ts.isNoSubstitutionTemplateLiteral(first))) fail(`dynamic new URL(import.meta.url) is prohibited; use a static literal: ${relative}`);
      }
      ts.forEachChild(node, visit);
    };
    visit(sourceFile);
  }
}
export { assertStaticDiscoverySyntax };
function sourceBytesAt(cache, absolute) {
  const bytes = cache.bytesByAbsolutePath.get(pathKey(absolute));
  return bytes ? Buffer.from(bytes) : undefined;
}
function expectedRollupNativeRelative(platform = process.platform, arch = process.arch) {
  if (platform === 'win32' && arch === 'x64') return 'node_modules/@rollup/rollup-win32-x64-msvc/rollup.win32-x64-msvc.node';
  return null;
}
export { expectedRollupNativeRelative };
function isPinnedRollupNativeAddon(filename, toolchainRoot, trustedToolchainRecords) {
  const relative = expectedRollupNativeRelative();
  if (!relative) return false;
  const exactPath = path.resolve(toolchainRoot, ...relative.split('/'));
  return pathKey(filename) === pathKey(exactPath) && trustedToolchainRecords.has(pathKey(exactPath));
}
export { isPinnedRollupNativeAddon };
function installVerifiedSourceReadOverlay(sourceRoot, cache) {
  const toolchainRoot = cache.toolchainRoot;
  const trustedToolchainRecords = cache.trustedToolchainRecords;
  const original = {
    readFileSync: fs.readFileSync,
    readFile: fs.readFile,
    promisesReadFile: fs.promises.readFile,
    createReadStream: fs.createReadStream,
    existsSync: fs.existsSync,
    statSync: fs.statSync, lstatSync: fs.lstatSync, stat: fs.stat, lstat: fs.lstat,
    promisesStat: fs.promises.stat, promisesLstat: fs.promises.lstat,
    readdirSync: fs.readdirSync, readdir: fs.readdir, promisesReaddir: fs.promises.readdir,
    realpathSync: fs.realpathSync, realpath: fs.realpath, promisesRealpath: fs.promises.realpath,
    openSync: fs.openSync,
    open: fs.open,
    promisesOpen: fs.promises.open,
    openAsBlob: fs.openAsBlob,
  };
  const moduleApi = createRequire(import.meta.url)('node:module');
  const originalExtensions = { js: moduleApi._extensions['.js'], json: moduleApi._extensions['.json'], node: moduleApi._extensions['.node'] };
  const childProcess = createRequire(import.meta.url)('node:child_process');
  const originalChildProcess = Object.fromEntries(['spawn', 'spawnSync', 'exec', 'execSync', 'execFile', 'execFileSync', 'fork'].map(name => [name, childProcess[name]]));
  const sourcePath = input => {
    try {
      const absolute = input instanceof URL ? fileURLToPath(input) : Buffer.isBuffer(input) ? input.toString() : String(input);
      const clean = absolute.split(/[?#]/, 1)[0];
      const resolved = path.resolve(clean);
      return within(sourceRoot, resolved) ? resolved : null;
    } catch { return null; }
  };
  const readToolchainBytes = absolute => {
    const key = pathKey(absolute), record = trustedToolchainRecords.get(key);
    if (!record) fail(`toolchain content read is outside parent-verified inventory: ${absolute}`);
    const cached = cache.toolchainBytesByAbsolutePath.get(key);
    if (cached) return Buffer.from(cached);
    const overlayOpenSync = fs.openSync, overlayRealpathSync = fs.realpathSync;
    let bytes;
    try {
      // The shared stable reader uses these two methods to open and realpath a no-follow file.
      // Restore only those synchronous primitives during this bounded read, then reinstate the overlay.
      fs.openSync = original.openSync;
      fs.realpathSync = original.realpathSync;
      regularFile(record.path, record.maxBytes);
      const stat = lstatSync(record.path);
      const mode = (stat.mode & 0o111) ? '100755' : '100644';
      if (record.mode && mode !== record.mode) fail(`pinned toolchain mode changed: ${record.path}`);
      bytes = readStableBytes(record.path, record.byteLength ?? record.maxBytes);
    } finally {
      fs.openSync = overlayOpenSync;
      fs.realpathSync = overlayRealpathSync;
    }
    if (bytes.length > record.maxBytes || (record.byteLength !== undefined && bytes.length !== record.byteLength)
        || sha(bytes) !== record.sha256) fail(`pinned toolchain bytes differ from parent inventory: ${record.path}`);
    cache.toolchainBytesByAbsolutePath.set(key, Buffer.from(bytes));
    return Buffer.from(bytes);
  };
  const classify = input => {
    const source = sourcePath(input);
    if (source) {
      const bytes = sourceBytesAt(cache, source);
      if (!bytes) fail(`attempted to read source outside verified byte snapshot: ${source}`);
      return { kind: 'source', bytes };
    }
    let absolute;
    try { absolute = pathKey(input instanceof URL ? fileURLToPath(input) : Buffer.isBuffer(input) ? input.toString() : String(input)); }
    catch { fail('unsupported filesystem read path'); }
    if (trustedToolchainRecords.has(absolute)) return { kind: 'toolchain', bytes: readToolchainBytes(absolute) };
    fail(`filesystem content read outside source cache and pinned toolchain inventory: ${absolute}`);
  };
  const readPinnedModule = filename => {
    const resolved = path.resolve(filename);
    if (!within(toolchainRoot, resolved)) fail(`module load escaped pinned toolchain: ${filename}`);
    return readToolchainBytes(resolved);
  };
  const esbuildExecutable = path.resolve(toolchainRoot, 'node_modules', '@esbuild', 'win32-x64', 'esbuild.exe');
  const authorizeEsbuildLaunch = (command, args, options) => {
    if (typeof command !== 'string' || path.resolve(command) !== command || pathKey(command) !== pathKey(esbuildExecutable)
        || !Array.isArray(args) || options?.shell) fail(`child executable is outside the pinned esbuild allowlist: ${command}`);
    readToolchainBytes(esbuildExecutable);
  };
  childProcess.spawn = (command, args, options) => { authorizeEsbuildLaunch(command, args, options); return originalChildProcess.spawn.call(childProcess, command, args, options); };
  childProcess.spawnSync = (command, args, options) => { authorizeEsbuildLaunch(command, args, options); return originalChildProcess.spawnSync.call(childProcess, command, args, options); };
  childProcess.execFile = (command, args, options, callback) => {
    if (typeof args === 'function') { callback = args; args = []; options = undefined; }
    else if (typeof options === 'function') { callback = options; options = undefined; }
    authorizeEsbuildLaunch(command, args, options);
    return originalChildProcess.execFile.call(childProcess, command, args, options, callback);
  };
  childProcess.execFileSync = (command, args, options) => { authorizeEsbuildLaunch(command, args, options); return originalChildProcess.execFileSync.call(childProcess, command, args, options); };
  childProcess.exec = () => fail('shell command execution is prohibited in managed build child');
  childProcess.execSync = () => fail('shell command execution is prohibited in managed build child');
  childProcess.fork = () => fail('forked process execution is prohibited in managed build child');
  moduleApi._extensions['.js'] = (loadedModule, filename) => loadedModule._compile(readPinnedModule(filename).toString('utf8'), filename);
  moduleApi._extensions['.json'] = (loadedModule, filename) => {
    const text = readPinnedModule(filename).toString('utf8').replace(/^\uFEFF/, '');
    loadedModule.exports = JSON.parse(text);
  };
  moduleApi._extensions['.node'] = (loadedModule, filename) => {
    if (!isPinnedRollupNativeAddon(filename, toolchainRoot, trustedToolchainRecords)) fail(`native .node module load prohibited: ${filename}`);
    readPinnedModule(filename);
    return originalExtensions.node(loadedModule, filename);
  };
  const moduleLoadHooks = moduleApi.registerHooks({
    resolve(specifier, context, nextResolve) {
      if (specifier.startsWith('node:') || builtinModules.includes(specifier)) return nextResolve(specifier, context);
      const resolved = nextResolve(specifier, context);
      if (resolved.url.startsWith('node:')) return resolved;
      let parsedUrl;
      try { parsedUrl = new URL(resolved.url); } catch { fail(`unsupported resolved module URL: ${resolved.url}`); }
      if (parsedUrl.protocol !== 'file:' || parsedUrl.search || parsedUrl.hash) fail(`unsupported resolved module URL: ${resolved.url}`);
      const filename = fileURLToPath(parsedUrl);
      if (path.extname(filename).toLowerCase() === '.node') {
        if (!isPinnedRollupNativeAddon(filename, toolchainRoot, trustedToolchainRecords)) fail(`native .node module load prohibited: ${filename}`);
        readPinnedModule(filename);
        return resolved;
      }
      if (!within(toolchainRoot, filename)) fail(`resolved module outside pinned toolchain prohibited: ${filename}`);
      readPinnedModule(filename);
      return resolved;
    },
    load(url, context, nextLoad) {
      if (url.startsWith('node:')) return nextLoad(url, context);
      let parsedUrl;
      try { parsedUrl = new URL(url); } catch { fail(`unsupported module load URL: ${url}`); }
      if (parsedUrl.protocol !== 'file:' || parsedUrl.search || parsedUrl.hash) fail(`unsupported module load URL: ${url}`);
      const filename = fileURLToPath(parsedUrl), extension = path.extname(filename).toLowerCase();
      if (extension === '.node') {
        if (!isPinnedRollupNativeAddon(filename, toolchainRoot, trustedToolchainRecords)) fail(`native .node module load prohibited: ${filename}`);
        readPinnedModule(filename);
        return nextLoad(url, context);
      }
      if (!within(toolchainRoot, filename)) fail(`module load outside pinned toolchain prohibited: ${filename}`);
      const bytes = readPinnedModule(filename);
      let format = context.format;
      if (!format) format = extension === '.mjs' ? 'module' : extension === '.cjs' ? 'commonjs' : extension === '.json' ? 'json' : 'commonjs';
      return { format, source: bytes, shortCircuit: true };
    },
  });
  syncBuiltinESMExports();
  const sourceStat = input => {
    const source = sourcePath(input);
    if (!source) return null;
    const stat = cache.statsByAbsolutePath.get(pathKey(source));
    if (!stat) fail(`source metadata path is outside verified snapshot: ${source}`);
    return Object.assign(Object.create(Object.getPrototypeOf(stat)), stat);
  };
  const sourceDirectoryEntries = (input, options) => {
    const source = sourcePath(input);
    if (!source) return null;
    const key = pathKey(source), stat = cache.statsByAbsolutePath.get(key), entries = cache.directoryEntries.get(key);
    if (!stat?.isDirectory() || !entries) fail(`source directory is outside verified directory snapshot: ${source}`);
    const encoding = typeof options === 'string' ? options : options?.encoding;
    if (options?.withFileTypes) return entries.map(item => {
      const name = encoding === 'buffer' ? Buffer.from(item.name) : item.name;
      return { name, parentPath: source, path: source, isFile: () => !item.isDirectory, isDirectory: () => item.isDirectory, isSymbolicLink: () => false, isBlockDevice: () => false, isCharacterDevice: () => false, isFIFO: () => false, isSocket: () => false };
    });
    return entries.map(item => encoding === 'buffer' ? Buffer.from(item.name) : item.name);
  };
  const statError = input => Object.assign(new Error(`source metadata path is outside verified snapshot: ${input}`), { code: 'ENOENT', path: input });
  const decode = (bytes, options) => {
    const encoding = typeof options === 'string' ? options : options?.encoding;
    return encoding ? bytes.toString(encoding) : bytes;
  };
  fs.readFileSync = function(input, options) {
    const item = classify(input);
    return item.kind === 'source' || item.kind === 'toolchain' ? decode(item.bytes, options) : original.readFileSync.call(fs, input, options);
  };
  fs.readFile = function(input, options, callback) {
    if (typeof options === 'function') { callback = options; options = undefined; }
    const item = classify(input);
    if (item.kind === 'toolchain') { process.nextTick(callback, null, decode(item.bytes, options)); return; }
    if (item.kind !== 'source') return original.readFile.call(fs, input, options, callback);
    process.nextTick(callback, null, decode(item.bytes, options));
  };
  fs.promises.readFile = async function(input, options) {
    const item = classify(input);
    return item.kind === 'source' || item.kind === 'toolchain' ? decode(item.bytes, options) : original.promisesReadFile.call(fs.promises, input, options);
  };
  fs.createReadStream = function(input, options) {
    const item = classify(input);
    if (item.kind === 'toolchain') {
      const start = Number.isSafeInteger(options?.start) ? options.start : 0;
      const end = Number.isSafeInteger(options?.end) ? options.end : item.bytes.length - 1;
      const stream = Readable.from([item.bytes.subarray(start, end + 1)]);
      if (options?.encoding) stream.setEncoding(options.encoding);
      return stream;
    }
    if (item.kind !== 'source') return original.createReadStream.call(fs, input, options);
    const start = Number.isSafeInteger(options?.start) ? options.start : 0;
    const end = Number.isSafeInteger(options?.end) ? options.end : item.bytes.length - 1;
    const stream = Readable.from([item.bytes.subarray(start, end + 1)]);
    if (options?.encoding) stream.setEncoding(options.encoding);
    return stream;
  };
  fs.existsSync = function(input) { return sourcePath(input) ? cache.statsByAbsolutePath.has(pathKey(sourcePath(input))) : original.existsSync.call(fs, input); };
  fs.statSync = function(input, ...args) { const stat = sourceStat(input); return stat || original.statSync.call(fs, input, ...args); };
  fs.lstatSync = function(input, ...args) { const stat = sourceStat(input); return stat || original.lstatSync.call(fs, input, ...args); };
  fs.stat = function(input, ...args) {
    const stat = sourceStat(input);
    if (!stat) return original.stat.call(fs, input, ...args);
    const callback = args.at(-1); process.nextTick(callback, null, stat);
  };
  fs.lstat = function(input, ...args) {
    const stat = sourceStat(input);
    if (!stat) return original.lstat.call(fs, input, ...args);
    const callback = args.at(-1); process.nextTick(callback, null, stat);
  };
  fs.promises.stat = async function(input, ...args) { const stat = sourceStat(input); return stat || original.promisesStat.call(fs.promises, input, ...args); };
  fs.promises.lstat = async function(input, ...args) { const stat = sourceStat(input); return stat || original.promisesLstat.call(fs.promises, input, ...args); };
  fs.readdirSync = function(input, options) { const entries = sourceDirectoryEntries(input, options); return entries || original.readdirSync.call(fs, input, options); };
  fs.readdir = function(input, options, callback) {
    if (typeof options === 'function') { callback = options; options = undefined; }
    const entries = sourceDirectoryEntries(input, options);
    if (!entries) return original.readdir.call(fs, input, options, callback);
    process.nextTick(callback, null, entries);
  };
  fs.promises.readdir = async function(input, options) { const entries = sourceDirectoryEntries(input, options); return entries || original.promisesReaddir.call(fs.promises, input, options); };
  const resolveSourceRealpath = input => {
    const source = sourcePath(input);
    if (!source) return null;
    const canonicalPath = cache.canonicalPathsByKey.get(pathKey(source));
    if (!canonicalPath) fail(`source realpath is outside verified snapshot: ${source}`);
    return canonicalPath;
  };
  const realpathSyncOverlay = function(input, ...args) { const value = resolveSourceRealpath(input); return value || original.realpathSync.call(fs, input, ...args); };
  realpathSyncOverlay.native = realpathSyncOverlay;
  fs.realpathSync = realpathSyncOverlay;
  fs.realpath = function(input, ...args) {
    const value = resolveSourceRealpath(input);
    if (!value) return original.realpath.call(fs, input, ...args);
    const callback = args.at(-1); process.nextTick(callback, null, value);
  };
  fs.promises.realpath = async function(input, ...args) { const value = resolveSourceRealpath(input); return value || original.promisesRealpath.call(fs.promises, input, ...args); };
  fs.openSync = function(input, ...args) {
    const item = classify(input);
    fail(`${item.kind} file descriptor reads are prohibited; use verified cached bytes: ${input}`);
  };
  fs.open = function(input, ...args) {
    const item = classify(input), callback = args.at(-1);
    process.nextTick(callback, new Error(`${item.kind} file descriptor reads are prohibited: ${input}`));
  };
  fs.promises.open = async function(input, ...args) {
    const item = classify(input);
    fail(`${item.kind} file handle reads are prohibited; use verified cached bytes: ${input}`);
  };
  if (typeof original.openAsBlob === 'function') {
    fs.openAsBlob = async function(input, ...args) {
      const item = classify(input);
      fail(`${item.kind} Blob reads are prohibited; use verified cached bytes: ${input}`);
    };
  }
  syncBuiltinESMExports();
  return () => {
    fs.readFileSync = original.readFileSync; fs.readFile = original.readFile;
    fs.promises.readFile = original.promisesReadFile; fs.createReadStream = original.createReadStream;
    fs.existsSync = original.existsSync; fs.statSync = original.statSync; fs.lstatSync = original.lstatSync;
    fs.stat = original.stat; fs.lstat = original.lstat; fs.promises.stat = original.promisesStat; fs.promises.lstat = original.promisesLstat;
    fs.readdirSync = original.readdirSync; fs.readdir = original.readdir; fs.promises.readdir = original.promisesReaddir;
    fs.realpathSync = original.realpathSync; fs.realpath = original.realpath; fs.promises.realpath = original.promisesRealpath;
    fs.openSync = original.openSync; fs.open = original.open; fs.promises.open = original.promisesOpen;
    if (typeof original.openAsBlob === 'function') fs.openAsBlob = original.openAsBlob;
    moduleLoadHooks.deregister();
    if (originalExtensions.js) moduleApi._extensions['.js'] = originalExtensions.js; else delete moduleApi._extensions['.js'];
    if (originalExtensions.json) moduleApi._extensions['.json'] = originalExtensions.json; else delete moduleApi._extensions['.json'];
    if (originalExtensions.node) moduleApi._extensions['.node'] = originalExtensions.node; else delete moduleApi._extensions['.node'];
    for (const [name, originalMethod] of Object.entries(originalChildProcess)) childProcess[name] = originalMethod;
    syncBuiltinESMExports();
  };
}
export { installVerifiedSourceReadOverlay };
function resolveBoundarySpecifier({ specifier, importer, sourceRoot, toolchainRoot, sourceFiles, toolchainFiles, packageRequire, sourceBase, extensionCandidates = SOURCE_EXTENSIONS, conditions = ['node', 'import', 'default'] }) {
  if (!specifier || specifier.startsWith('#')) fail('empty or package-import-map specifier is prohibited');
  const queryIndex = specifier.indexOf('?');
  const query = queryIndex < 0 ? '' : specifier.slice(queryIndex);
  const cleanSpecifier = queryIndex < 0 ? specifier : specifier.slice(0, queryIndex);
  const cleanImporter = importer?.split(/[?#]/, 1)[0];
  let target;
  const bare = !cleanSpecifier.startsWith('.') && !cleanSpecifier.startsWith('/') && !path.isAbsolute(cleanSpecifier);
  if (bare) {
    const importResolution = conditions.includes('browser')
      ? resolvePinnedImportExport(cleanSpecifier, { toolchainRoot, toolchainFiles, conditions }) : null;
    if (importResolution?.matched) {
      if (!importResolution.target) fail(`pinned toolchain dependency has no matching renderer export: ${cleanSpecifier}`);
      target = importResolution.target;
    } else {
      try { target = packageRequire.resolve(cleanSpecifier, { paths: [path.dirname(cleanImporter), toolchainRoot] }); }
      catch {
        const fallback = resolvePinnedImportExport(cleanSpecifier, { toolchainRoot, toolchainFiles, conditions });
        if (!fallback?.matched || !fallback.target) fail(`pinned toolchain dependency cannot resolve: ${cleanSpecifier}`);
        target = fallback.target;
      }
    }
  } else {
    const base = path.isAbsolute(cleanSpecifier) || cleanSpecifier.startsWith('/') ? sourceBase : path.dirname(cleanImporter);
    const unresolved = path.resolve(base, cleanSpecifier.replace(/^[/\\]+/, ''));
    if (!within(sourceRoot, unresolved) && !within(toolchainRoot, unresolved)) fail(`source import escaped source and pinned toolchain: ${specifier}`);
    const suffix = path.extname(unresolved).toLowerCase();
    const typedSuffixes = {
      '.js': ['.ts', '.tsx'], '.jsx': ['.tsx', '.ts'],
      '.mjs': ['.mts'], '.cjs': ['.cts'],
    }[suffix];
    const candidates = typedSuffixes
      ? [unresolved, ...typedSuffixes.map(extension => `${unresolved.slice(0, -suffix.length)}${extension}`)]
      : [unresolved, ...extensionCandidates.map(extension => `${unresolved}${extension}`), ...extensionCandidates.map(extension => path.join(unresolved, `index${extension}`))];
    const candidateIsVerified = candidate => {
      const absolute = path.resolve(candidate);
      if (within(sourceRoot, absolute)) return !sourceFiles || sourceFiles.has(path.relative(sourceRoot, absolute).split(path.sep).join('/'));
      if (within(toolchainRoot, absolute)) return !toolchainFiles || toolchainFiles.has(path.relative(toolchainRoot, absolute).split(path.sep).join('/'));
      return false;
    };
    target = candidates.find(candidate => candidateIsVerified(candidate) && existsSync(candidate) && lstatSync(candidate).isFile());
    if (!target) fail(`source import target does not exist: ${specifier}`);
  }
  let canonicalTarget;
  try { canonicalTarget = realpathSync.native(target); } catch { fail(`resolved import is missing: ${specifier}`); }
  if (canonicalTarget !== target) fail(`resolved import contains an alias or link: ${specifier}`);
  const inSource = within(sourceRoot, canonicalTarget);
  const inToolchain = within(toolchainRoot, canonicalTarget);
  const relativeSource = inSource ? path.relative(sourceRoot, canonicalTarget).split(path.sep).join('/') : '';
  const relativeToolchain = inToolchain ? path.relative(toolchainRoot, canonicalTarget).split(path.sep).join('/') : '';
  if (inSource && sourceFiles && !sourceFiles.has(relativeSource)) fail(`resolved source import is outside committed inventory: ${specifier}`);
  if (inToolchain && toolchainFiles && !toolchainFiles.has(relativeToolchain)) fail(`resolved dependency is outside pinned toolchain inventory: ${specifier}`);
  if (!inSource && !inToolchain) fail(`resolved import escaped source and pinned toolchain: ${specifier}`);
  return `${canonicalTarget}${query}`;
}
export { parseArgs, resolveBoundarySpecifier };

function validateMarkupAssetReferences({ text, importer, sourceRoot, sourceFiles }) {
  const references = [];
  for (const match of text.matchAll(/\b(?:src|href|poster|xlink:href)\s*=\s*(["'])(.*?)\1/gi)) references.push(match[2]);
  for (const match of text.matchAll(/url\(\s*(["']?)(.*?)\1\s*\)/gi)) references.push(match[2]);
  for (const match of text.matchAll(/@import\s+(?:url\(\s*)?(["'])(.*?)\1\s*\)?/gi)) references.push(match[2]);
  for (const reference of references) {
    let value = reference.trim();
    if (!value || /^(?:data:|https?:|blob:|\/\/|#)/i.test(value)) continue;
    try { value = decodeURIComponent(value); } catch { fail(`malformed local markup asset URL: ${reference}`); }
    const clean = value.split(/[?#]/, 1)[0];
    const desktopRoot = path.join(sourceRoot, 'desktop');
    const candidates = value.startsWith('/')
      ? [path.resolve(desktopRoot, 'public', clean.replace(/^[/\\]+/, '')), path.resolve(desktopRoot, clean.replace(/^[/\\]+/, ''))]
      : [path.resolve(path.dirname(importer), clean)];
    const target = candidates.find(candidate => sourceFiles?.has(path.relative(sourceRoot, candidate).split(path.sep).join('/')))
      || candidates[0];
    if (!within(sourceRoot, target)) fail(`local markup asset escaped source: ${value}`);
    const relative = path.relative(sourceRoot, target).split(path.sep).join('/');
    if (sourceFiles && !sourceFiles.has(relative)) fail(`local markup asset is outside committed inventory: ${value}`);
  }
}
export { validateMarkupAssetReferences };

function validateRendererResolution(resolution, { sourceRoot, toolchainRoot, sourceFiles, toolchainFiles }) {
  if (!resolution || resolution.external === true || typeof resolution.id !== 'string' || resolution.id.startsWith('\0')) {
    fail('renderer package resolver returned no bounded file');
  }
  const cleanId = resolution.id.split(/[?#]/, 1)[0];
  let absolute;
  try { absolute = cleanId.startsWith('file:') ? fileURLToPath(cleanId) : path.resolve(cleanId); }
  catch { fail(`renderer package resolver returned an invalid file: ${resolution.id}`); }
  if (!path.isAbsolute(absolute)) fail(`renderer package resolver returned a non-absolute file: ${resolution.id}`);
  let canonicalTarget;
  try { canonicalTarget = realpathSync.native(absolute); }
  catch { fail(`renderer package resolver returned a missing file: ${resolution.id}`); }
  if (canonicalTarget !== absolute) fail(`renderer package resolver returned an alias or link: ${resolution.id}`);
  if (within(sourceRoot, canonicalTarget)) {
    const relative = path.relative(sourceRoot, canonicalTarget).split(path.sep).join('/');
    if (!sourceFiles.has(relative)) fail(`renderer package resolver escaped committed source inventory: ${resolution.id}`);
  } else if (within(path.join(toolchainRoot, 'node_modules'), canonicalTarget)) {
    const relative = path.relative(toolchainRoot, canonicalTarget).split(path.sep).join('/');
    if (!toolchainFiles.has(relative)) fail(`renderer package resolver escaped pinned toolchain inventory: ${resolution.id}`);
  } else fail(`renderer package resolver escaped bounded roots: ${resolution.id}`);
  return resolution.id;
}
export { validateRendererResolution };

function guardedSourcePlugin({ sourceRoot, toolchainRoot, sourceFiles, toolchainFiles, sourceCache, renderer }) {
  const req = createRequire(path.join(toolchainRoot, 'package.json'));
  const native = new Set([...builtinModules, ...builtinModules.map(name => `node:${name}`), 'electron']);
  return {
    name: 'managed-postmerge-source-boundary',
    enforce: 'pre',
    async resolveId(specifier, importer) {
      if (!importer || importer.startsWith('\0') || specifier.startsWith('\0')) return null;
      if (specifier === 'vite/modulepreload-polyfill') return null;
      const resolvedImporter = path.resolve(importer.split(/[?#]/, 1)[0]);
      if (!within(sourceRoot, resolvedImporter) && !within(path.join(toolchainRoot, 'node_modules'), resolvedImporter)) fail(`module importer escaped bounded roots: ${importer}`);
      if (native.has(specifier) || native.has(specifier.replace(/^node:/, 'node:'))) {
        if (renderer) fail(`renderer native import is prohibited: ${specifier}`);
        fail(`Electron native import is not externalized by the fixed policy: ${specifier}`);
      }
      if (/^(?:data:|https?:|\/\/|#)/i.test(specifier)) return null;
      const bare = !specifier.startsWith('.') && !specifier.startsWith('/') && !path.isAbsolute(specifier);
      if (renderer && bare) {
        const resolverImporter = within(sourceRoot, resolvedImporter) ? path.join(toolchainRoot, 'package.json') : importer;
        const resolution = await this.resolve(specifier, resolverImporter, { skipSelf: true });
        if (!resolution) fail(`renderer package resolver could not resolve ${specifier} from ${importer}`);
        return validateRendererResolution(resolution, { sourceRoot, toolchainRoot, sourceFiles, toolchainFiles });
      }
      const target = resolveBoundarySpecifier({ specifier, importer: resolvedImporter, sourceRoot, toolchainRoot, sourceFiles, toolchainFiles, packageRequire: req, sourceBase: path.join(sourceRoot, 'desktop'), conditions: renderer ? ['browser', 'import', 'default'] : ['node', 'import', 'default'] });
      return target;
    },
    transform(code, id) {
      if (id.startsWith('\0')) return null;
      const resolved = path.resolve(id.split(/[?#]/, 1)[0]);
      if (within(sourceRoot, resolved) && /\.(?:html|css|scss|sass|less)$/i.test(resolved)) validateMarkupAssetReferences({ text: code, importer: resolved, sourceRoot, sourceFiles });
      return null;
    },
    load(id) {
      if (id.startsWith('\0')) return null;
      const queryIndex = id.indexOf('?');
      const query = queryIndex < 0 ? '' : id.slice(queryIndex + 1).split('#', 1)[0];
      const resolved = path.resolve(queryIndex < 0 ? id : id.slice(0, queryIndex));
      if (!within(sourceRoot, resolved) && !within(path.join(toolchainRoot, 'node_modules'), resolved)) fail(`Vite attempted to load a file outside bounded roots: ${id}`);
      const relative = within(sourceRoot, resolved) ? path.relative(sourceRoot, resolved).split(path.sep).join('/') : path.relative(toolchainRoot, resolved).split(path.sep).join('/');
      if (within(sourceRoot, resolved) ? !sourceFiles.has(relative) : !toolchainFiles.has(relative)) fail(`Vite attempted to load a file outside verified inventory: ${id}`);
      if (query.split('&').some(part => part === 'raw' || part === 'url')) return null;
      if (within(sourceRoot, resolved) && /\.(?:[cm]?[jt]sx?)$/i.test(resolved)) return sourceBytesAt(sourceCache, resolved)?.toString('utf8');
      return null;
    },
    transformIndexHtml: { order: 'pre', handler(html) { validateMarkupAssetReferences({ text: html, importer: path.join(sourceRoot, 'desktop', 'index.html'), sourceRoot, sourceFiles }); return html; } },
  };
}
function createManagedTypecheckOptions(ts, toolchainRoot) {
  return {
    noEmit: true, skipLibCheck: true, strict: false, strictNullChecks: true,
    allowImportingTsExtensions: true, target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ESNext, moduleResolution: ts.ModuleResolutionKind.Bundler,
    jsx: ts.JsxEmit.ReactJSX, allowSyntheticDefaultImports: true, esModuleInterop: true,
    lib: ['lib.es2022.d.ts', 'lib.dom.d.ts', 'lib.dom.iterable.d.ts'],
    types: ['node'], typeRoots: [path.join(toolchainRoot, 'node_modules', '@types')],
    baseUrl: toolchainRoot, paths: { '*': ['node_modules/*'] },
  };
}
export { createManagedTypecheckOptions };
function typecheck({ sourceRoot, toolchainRoot, sourceFiles, toolchainFiles, sourceCache, electronMain, electronManagedPreload }) {
  const requireToolchain = createRequire(path.join(toolchainRoot, 'package.json'));
  const ts = requireToolchain('typescript');
  assertStaticDiscoverySyntax({ sourceRoot, sourceFiles, sourceCache, ts });
  const desktop = path.join(sourceRoot, 'desktop');
  const entries = [electronMain, path.join(desktop, 'electron', 'preload.ts'), path.join(desktop, 'src', 'main.ts')];
  if (electronManagedPreload) entries.push(electronManagedPreload);
  const viteClientTypes = path.join(toolchainRoot, 'node_modules', 'vite', 'client.d.ts');
  if (!toolchainFiles.has('node_modules/vite/client.d.ts')) fail('pinned Vite client declarations are absent from toolchain inventory');
  entries.push(viteClientTypes);
  for (const entry of entries) {
    if (!existsSync(entry)) fail(`required typecheck entry missing: ${path.relative(sourceRoot, entry)}`);
    const canonicalEntry = realpathSync.native(entry);
    const inSource = within(sourceRoot, canonicalEntry);
    const inToolchain = within(toolchainRoot, canonicalEntry);
    if (!inSource && !inToolchain) fail(`required typecheck entry escaped bounded roots: ${entry}`);
    if (inSource ? !sourceFiles.has(path.relative(sourceRoot, canonicalEntry).split(path.sep).join('/'))
      : !toolchainFiles.has(path.relative(toolchainRoot, canonicalEntry).split(path.sep).join('/'))) fail(`required typecheck entry missing from verified inventory: ${entry}`);
  }
  const options = createManagedTypecheckOptions(ts, toolchainRoot);
  const host = ts.createCompilerHost(options);
  const nativeResolve = host.resolveModuleNames?.bind(host);
  const baseFileExists = host.fileExists.bind(host), baseReadFile = host.readFile.bind(host), baseGetSourceFile = host.getSourceFile.bind(host);
  const withinAllowed = file => {
    const resolved = path.resolve(file);
    if (within(sourceRoot, resolved)) return sourceCache.bytesByAbsolutePath.has(pathKey(resolved));
    if (within(toolchainRoot, resolved)) return toolchainFiles.has(path.relative(toolchainRoot, resolved).split(path.sep).join('/'));
    return false;
  };
  host.fileExists = file => {
    const absolute = path.resolve(file);
    return within(sourceRoot, absolute) ? sourceCache.bytesByAbsolutePath.has(pathKey(absolute))
      : withinAllowed(file) && baseFileExists(file);
  };
  host.readFile = file => {
    const absolute = path.resolve(file);
    if (within(sourceRoot, absolute)) return sourceBytesAt(sourceCache, absolute)?.toString('utf8');
    return withinAllowed(file) ? baseReadFile(file) : undefined;
  };
  host.getSourceFile = (file, languageVersion, onError, shouldCreateNewSourceFile) => {
    const absolute = path.resolve(file);
    if (within(sourceRoot, absolute)) {
      const bytes = sourceBytesAt(sourceCache, absolute);
      return bytes ? ts.createSourceFile(file, bytes.toString('utf8'), languageVersion) : undefined;
    }
    return withinAllowed(file) ? baseGetSourceFile(file, languageVersion, onError, shouldCreateNewSourceFile) : undefined;
  };
  host.resolveModuleNames = (names, containingFile, ...rest) => names.map(name => {
    const resolved = ts.resolveModuleName(name, containingFile, options, host).resolvedModule;
    if (!resolved) return undefined;
    const file = path.resolve(resolved.resolvedFileName);
    const relativeSource = path.relative(sourceRoot, file).split(path.sep).join('/');
    const relativeToolchain = path.relative(toolchainRoot, file).split(path.sep).join('/');
    if (within(sourceRoot, file) ? !sourceFiles.has(relativeSource)
      : within(path.join(toolchainRoot, 'node_modules'), file) ? !toolchainFiles.has(relativeToolchain)
        : true) fail(`type import escaped source or pinned toolchain inventory: ${name}`);
    if (nativeResolve) return nativeResolve([name], containingFile, ...rest)[0];
    return resolved;
  });
  const program = ts.createProgram(entries, options, host);
  const diagnostics = ts.getPreEmitDiagnostics(program);
  if (diagnostics.length) fail(ts.formatDiagnosticsWithColorAndContext(diagnostics, {
    getCurrentDirectory: () => sourceRoot,
    getCanonicalFileName: name => name,
    getNewLine: () => '\n',
  }));
}
function readStaticPublic(sourceRoot, sourceCache) {
  let count = 0, bytesTotal = 0;
  const files = [];
  const prefix = 'desktop/public/';
  for (const relative of [...sourceCache.relativePaths].filter(value => value.startsWith(prefix)).sort()) {
    const bytes = sourceBytesAt(sourceCache, path.join(sourceRoot, ...relative.split('/')));
    count++; bytesTotal += bytes.length;
    if (count > 1000 || bytesTotal > 8 * 1024 * 1024) fail('public static asset budget exceeded');
    files.push({ path: `desktop/dist/${relative.slice(prefix.length)}`, contents: bytes });
  }
  return files;
}
async function build(argv) {
  const parsed = parseArgs(argv);
  const { sourceRoot, buildRoot, operationRoot, toolchainRoot, expectedSnapshotDigest, expectedToolchainDigest } = parsed;
  const toolchainRecord = readCanonical(path.join(operationRoot, 'toolchain.json'), MAX_RECORD_BYTES);
  const snapshot = readCanonical(path.join(operationRoot, 'source.json'), MAX_RECORD_BYTES);
  verifyChildInputBindings({ snapshot, toolchainRecord, expectedSnapshotDigest, expectedToolchainDigest });
  if (toolchainRecord.root !== toolchainRoot) fail('toolchain root differs from parent-verified toolchain record');
  assertPlainDirectory(sourceRoot); assertPlainDirectory(toolchainRoot);
  if (path.resolve(operationRoot, 'source') !== sourceRoot || path.resolve(operationRoot, 'build') !== buildRoot) fail('operation layout mismatch');
  const desktopSource = path.join(sourceRoot, 'desktop');
  const sourceFiles = new Set(snapshot.files.map(file => file.path));
  const managedSnapshot = sourceFiles.has('desktop/electron/entry.ts');
  if (managedSnapshot && !sourceFiles.has(MANAGED_GIT_METADATA_SOURCE)) fail(`required managed Electron runtime input is absent from committed inventory: ${MANAGED_GIT_METADATA_SOURCE}`);
  const electronEntries = managedElectronEntries({ desktopSource, sourceFiles });
  const main = electronEntries.main, preload = electronEntries.preload, managedPreload = electronEntries['managed-preload'];
  const html = path.join(desktopSource, 'index.html');
  const emptyEnvDir = path.join(operationRoot, 'empty-env'); assertPlainDirectory(emptyEnvDir);
  const toolchainFiles = new Set(toolchainRecord.files.map(file => file.path));
  for (const name of [`desktop/electron/${path.basename(main)}`, 'desktop/electron/preload.ts', 'desktop/src/main.ts', 'desktop/index.html']) if (!sourceFiles.has(name)) fail(`required source input is absent from committed inventory: ${name}`);
  const sourceCache = captureVerifiedSourceBytes({ sourceRoot, snapshot, toolchainRoot, toolchainFiles, toolchainRecord });
  const managedGitMetadataBytes = managedSnapshot
    ? sourceBytesAt(sourceCache, path.join(sourceRoot, ...MANAGED_GIT_METADATA_SOURCE.split('/')))
    : undefined;
  if (managedSnapshot && !managedGitMetadataBytes) fail(`required managed Electron runtime input has no verified bytes: ${MANAGED_GIT_METADATA_SOURCE}`);
  const restoreSourceOverlay = installVerifiedSourceReadOverlay(sourceRoot, sourceCache);
  let encoded;
  let electronResult, rendererResult;
  try {
  typecheck({ sourceRoot, toolchainRoot, sourceFiles, toolchainFiles, sourceCache, electronMain: main, electronManagedPreload: managedPreload });

  const requireToolchain = createRequire(path.join(toolchainRoot, 'package.json'));
  const electronOut = path.join(buildRoot, 'desktop', 'dist-electron');
  const rendererOut = path.join(buildRoot, 'desktop', 'dist');
  const { build: viteBuild } = requireToolchain('vite');
  const { build: esbuild } = requireToolchain('esbuild');
  const esbuildPlugin = {
    name: 'managed-postmerge-source-boundary',
    setup(buildApi) {
      const native = new Set([...builtinModules, ...builtinModules.map(name => `node:${name}`), 'electron']);
      buildApi.onResolve({ filter: /.*/ }, async args => {
        if (native.has(args.path)) return args.path === 'electron' || args.path.startsWith('node:') || builtinModules.includes(args.path) ? { path: args.path, external: true } : undefined;
        if (args.path.includes('?') || args.path.includes('#')) fail(`Electron import query is unsupported: ${args.path}`);
        if (args.path.startsWith('#')) fail('package import maps are prohibited');
        let importer;
        if (!args.importer && args.kind === 'entry-point' && [main, preload, managedPreload].filter(Boolean).includes(path.resolve(args.path))) importer = path.resolve(args.path);
        else if (args.importer && !args.importer.startsWith('<')) importer = path.resolve(args.importer);
        else fail('unexpected virtual Electron importer');
        if (!within(sourceRoot, importer) && !within(path.join(toolchainRoot, 'node_modules'), importer)) fail(`Electron importer escaped bounded roots: ${args.importer}`);
        const resolved = resolveBoundarySpecifier({ specifier: args.path, importer, sourceRoot, toolchainRoot, sourceFiles, toolchainFiles, packageRequire: requireToolchain, sourceBase: path.join(sourceRoot, 'desktop') });
        return { path: resolved };
      });
      buildApi.onLoad({ filter: /.*/ }, args => {
        const resolved = path.resolve(args.path);
        if (!within(sourceRoot, resolved) && !within(path.join(toolchainRoot, 'node_modules'), resolved)) fail(`esbuild attempted to load outside bounded roots: ${args.path}`);
        const relative = within(sourceRoot, resolved) ? path.relative(sourceRoot, resolved).split(path.sep).join('/') : path.relative(toolchainRoot, resolved).split(path.sep).join('/');
        if (within(sourceRoot, resolved) ? !sourceFiles.has(relative) : !toolchainFiles.has(relative)) fail(`esbuild attempted to load outside verified inventory: ${args.path}`);
        if (/\.node$/i.test(resolved)) fail(`native binary module is prohibited: ${args.path}`);
        if (within(sourceRoot, resolved)) {
          const contents = sourceBytesAt(sourceCache, resolved);
          if (!contents) fail(`esbuild source input is absent from verified byte cache: ${args.path}`);
          const extension = path.extname(resolved).toLowerCase();
          const loaders = { '.ts': 'ts', '.mts': 'ts', '.cts': 'ts', '.tsx': 'tsx', '.js': 'js', '.mjs': 'js', '.cjs': 'js', '.json': 'json', '.css': 'css', '.txt': 'text', '.svg': 'file', '.png': 'file', '.jpg': 'file', '.jpeg': 'file', '.gif': 'file', '.webp': 'file' };
          const loader = loaders[extension];
          if (!loader) fail(`unsupported cached esbuild source extension: ${extension}`);
          return { contents, loader };
        }
        return undefined;
      });
    },
  };
  const electronEntries = managedSnapshot ? { main, preload, 'managed-preload': managedPreload } : { main, preload };
  electronResult = await esbuild({ entryPoints: electronEntries, outdir: electronOut, entryNames: '[name]', bundle: true, platform: 'node', format: 'cjs', target: 'node22', packages: 'bundle', tsconfigRaw: {}, ...electronImportMetaOptions(), external: ['electron', ...builtinModules, ...builtinModules.map(name => `node:${name}`)], plugins: [esbuildPlugin], sourcemap: false, legalComments: 'none', write: false, metafile: false, logLevel: 'silent' });
  rendererResult = await viteBuild({
    configFile: false, mode: 'production', root: desktopSource, envDir: emptyEnvDir, publicDir: path.join(desktopSource, 'public'),
    resolve: managedViteResolveOptions(),
    plugins: [guardedSourcePlugin({ sourceRoot, toolchainRoot, sourceFiles, toolchainFiles, sourceCache, renderer: true })],
    css: { postcss: { plugins: [] } },
    esbuild: { tsconfigRaw: '{"compilerOptions":{"jsx":"react-jsx","target":"ES2022","useDefineForClassFields":true}}' },
    base: './', logLevel: 'silent', clearScreen: false,
    build: { write: false, outDir: rendererOut, emptyOutDir: false, copyPublicDir: false, sourcemap: false, manifest: false, minify: true, rollupOptions: { input: html } },
  });
  const viteOutputs = Array.isArray(rendererResult) ? rendererResult.flatMap(result => result.output || []) : rendererResult?.output || [];
  const outputs = [];
  for (const output of electronResult.outputFiles || []) {
    const target = path.resolve(output.path), relative = path.relative(buildRoot, target).split(path.sep).join('/');
    if (!within(buildRoot, target) || !relative.startsWith('desktop/dist-electron/')) fail('Electron output escaped its fixed destination');
    outputs.push({ path: relative, contents: output.contents });
  }
  if (managedSnapshot) outputs.push({ path: MANAGED_GIT_METADATA_OUTPUT, contents: managedGitMetadataBytes });
  for (const output of viteOutputs) {
    const relative = output.fileName.replaceAll('\\', '/');
    if (!relative || relative.startsWith('/') || relative.split('/').some(part => part === '..') || relative.includes('\0')) fail('Vite emitted an unsafe output path');
    const contents = output.type === 'asset' ? Buffer.from(output.source) : Buffer.from(output.code);
    outputs.push({ path: `desktop/dist/${relative}`, contents });
  }
  outputs.push(...readStaticPublic(sourceRoot, sourceCache));
  const seen = new Set();
  encoded = outputs.map(output => {
    if (seen.has(output.path)) fail(`duplicate output path: ${output.path}`);
    seen.add(output.path);
    return { path: output.path, mode: '100644', ...encodeBuildOutput(output.contents) };
  }).sort((a, b) => a.path.localeCompare(b.path));
  const requiredOutputs = ['desktop/dist-electron/main.js', 'desktop/dist-electron/preload.js', 'desktop/dist/index.html'];
  if (managedSnapshot) requiredOutputs.push('desktop/dist-electron/managed-preload.js', MANAGED_GIT_METADATA_OUTPUT);
  for (const required of requiredOutputs) if (!seen.has(required)) fail(`required output missing: ${required}`);
  } finally {
    restoreSourceOverlay();
  }
  process.stdout.write(`${JSON.stringify({ status: 'built', sourceDigest: sha(sourceBytesAt(sourceCache, html)), outputs: encoded })}\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  build(process.argv.slice(2)).catch(error => {
    process.stderr.write(`${error?.stack || error}\n`);
    process.exitCode = 1;
  });
}
