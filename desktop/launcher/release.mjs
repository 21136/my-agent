import { constants, copyFileSync, existsSync, lstatSync, mkdirSync, readFileSync, renameSync, statfsSync, writeFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import path from 'node:path';
import { assertRecord, assertFixtureRun, canonical, fileEntry, HASH, inside, readJson, realDirectory, regularFile, safeRelative, samePath, sha, walk, publishNewDirectory } from './paths.mjs';
const REQUIRED = ['desktop/package.json', 'desktop/dist-electron/main.js', 'desktop/dist-electron/preload.js', 'desktop/dist/index.html', 'agent-core/server.py', 'agent-core/fixed_host_entry.py', 'evolve/_index.core.toml'];
function sourcePath(rel) { if (!safeRelative(rel) || !['desktop/', 'agent-core/', 'evolve/'].some(p => rel.startsWith(p)) || rel.split('/').some(p => /^(?:data|workspace|profile|profiles|\.git|\.venv|__pycache__)$/i.test(p) || /^\.env(?:\.|$)/i.test(p)) || /\.(?:pem|key|p12|pfx|db|sqlite3?|jsonl|log|pyc)$/i.test(rel))
    throw Error('private or unsafe source path: ' + rel); }
function validateEntries(entries) { if (!Array.isArray(entries) || !entries.length || entries.length > 50000)
    throw Error('invalid manifest inventory'); const aliases = new Set(); for (const item of entries) {
    assertRecord(item, ['path', 'sha256', 'byte_length'], 'manifest entry');
    if (!safeRelative(item.path) || !HASH.test(item.sha256) || !Number.isSafeInteger(item.byte_length) || item.byte_length < 0)
        throw Error('invalid manifest path/digest/length');
    const key = item.path.toLowerCase();
    if (aliases.has(key))
        throw Error('manifest path alias');
    aliases.add(key);
} }
function validateRuntime(item) { assertRecord(item, ['root', 'entry', 'scope', 'files', 'directories'], 'runtime'); const root = realDirectory(item.root, 'runtime root'); if (!safeRelative(item.entry) || item.scope !== 'tree' || !Array.isArray(item.directories))
    throw Error('invalid runtime manifest'); validateEntries(item.files); if (!item.files.some(f => f.path === item.entry))
    throw Error('runtime executable absent'); for (const rel of item.directories)
    if (!safeRelative(rel))
        throw Error('invalid runtime directory'); const actual = item.scope === 'tree' ? walk(root) : { files: [item.entry], directories: [] }; if (canonical(actual) !== canonical({ files: item.files.map(f => f.path).sort(), directories: item.directories }))
    throw Error('runtime inventory changed'); for (const expected of item.files)
    if (canonical(fileEntry(root, expected.path)) !== canonical(expected))
        throw Error('runtime integrity digest changed'); return item; }
function runtimeManifest(input) { assertRecord(input, ['root', 'entry', 'scope'], 'runtime input'); const root = realDirectory(input.root, 'runtime input root'); if (!safeRelative(input.entry) || input.scope !== 'tree')
    throw Error('complete runtime tree is required'); regularFile(path.join(root, input.entry), 'runtime executable'); const tree = input.scope === 'tree' ? walk(root) : { files: [input.entry], directories: [] }; return { root, entry: input.entry, scope: input.scope, files: tree.files.map(rel => fileEntry(root, rel)), directories: tree.directories }; }
function directoriesFor(files) { return [...new Set(files.flatMap(rel => { const parts = rel.split('/'), out = []; while (parts.length > 1) {
        parts.pop();
        out.push(parts.join('/'));
    } return out; }))].sort(); }
export function freezeHostRelease(input, options = {}) {
    assertRecord(input, ['runRoot', 'sourceRoot', 'files', 'runtimeInputs', 'provenance'], 'release input');
    const run = assertFixtureRun(input.runRoot), source = realDirectory(input.sourceRoot, 'source root'), output = realDirectory(path.join(run, 'releases'), 'release output');
    if (!Array.isArray(input.files) || !input.files.length || input.files.length > 20000)
        throw Error('source path count invalid');
    const names = [...input.files].sort(), aliases = new Set();
    for (const rel of names) {
        sourcePath(rel);
        const key = rel.toLowerCase();
        if (aliases.has(key))
            throw Error('source path alias');
        aliases.add(key);
    }
    if (REQUIRED.some(rel => !names.includes(rel)))
        throw Error('required source payload paths missing');
    const files = names.map(rel => fileEntry(source, rel));
    const runtimeInputs = input.runtimeInputs;
    assertRecord(runtimeInputs, ['node', 'electron', 'python', 'python_base'], 'runtime inputs');
    const runtimes = Object.fromEntries(Object.entries(runtimeInputs).map(([name, item]) => [name, runtimeManifest(item)]));
    assertRecord(input.provenance, ['kind', 'source_snapshot'], 'provenance');
    if (!['fixture', 'sealed-development-build'].includes(input.provenance.kind) || typeof input.provenance.source_snapshot !== 'string' || !input.provenance.source_snapshot || input.provenance.source_snapshot.length > 256)
        throw Error('invalid provenance identity');
    const manifest = { format: 1, scope: 'launcher_fixture_v1', protocol: 1, data_layout: 1, files, directories: directoriesFor(names), runtimes, provenance: input.provenance };
    const bytes = Buffer.from(canonical(manifest) + '\n'), id = sha(bytes), target = path.join(output, id);
    if(bytes.length>16*1024**2) throw Error("release manifest exceeds bound");
    if (existsSync(target))
        throw Error('existing release; refusing overwrite');
    const total = files.reduce((n, x) => n + x.byte_length, 0);
    const available = options.availableBytes ? options.availableBytes(output) : Number(statfsSync(output).bavail) * Number(statfsSync(output).bsize);
    if (!Number.isFinite(available) || available < total + 16 * 1024 ** 2)
        throw Error('insufficient free space capacity');
    const stage = path.join(output, '.stage-' + randomUUID());
    mkdirSync(stage);
    mkdirSync(path.join(stage, 'app'));
    // Preserve failed owned stages as evidence; no broad recursive deletion on failure.
    for (const item of files) {
        const dest = path.join(stage, 'app', ...item.path.split('/'));
        mkdirSync(path.dirname(dest), { recursive: true });
        copyFileSync(regularFile(path.join(source, item.path), 'source file'), dest, constants.COPYFILE_EXCL);
        if (canonical(fileEntry(path.join(stage, 'app'), item.path)) !== canonical(item))
            throw Error('source changed while copying; integrity mismatch');
    }
    writeFileSync(path.join(stage, 'manifest.json'), bytes, { flag: 'wx' });
    publishNewDirectory(stage, target);
    const descriptor = { release_id: id, root: target, manifest_digest: id };
    verifyHostRelease(descriptor);
    return descriptor;
}
export function verifyHostRelease(descriptor) {
    assertRecord(descriptor, ['release_id', 'root', 'manifest_digest'], 'release descriptor');
    if (!HASH.test(descriptor.release_id) || descriptor.manifest_digest !== descriptor.release_id)
        throw Error('invalid release identity');
    const root = realDirectory(descriptor.root, 'release root');
    if (path.basename(root) !== descriptor.release_id)
        throw Error('release identity path mismatch');
    const bytes = readFileSync(regularFile(path.join(root, 'manifest.json'), 'release manifest'));
    if (bytes.length > 16 * 1024 ** 2 || sha(bytes) !== descriptor.manifest_digest)
        throw Error('release manifest identity digest mismatch');
    const manifest = JSON.parse(bytes.toString('utf8'));
    assertRecord(manifest, ['format', 'scope', 'protocol', 'data_layout', 'files', 'directories', 'runtimes', 'provenance'], 'release manifest');
    if (manifest.format !== 1 || manifest.scope !== 'launcher_fixture_v1' || manifest.protocol !== 1 || manifest.data_layout !== 1)
        throw Error('unsupported release identity');
    if (!bytes.equals(Buffer.from(canonical(manifest) + '\n')))
        throw Error('noncanonical release manifest');
    validateEntries(manifest.files);
    for (const f of manifest.files)
        sourcePath(f.path);
    if (REQUIRED.some(rel => !manifest.files.some(f => f.path === rel)))
        throw Error('required release file missing');
    if (canonical(manifest.directories) !== canonical(directoriesFor(manifest.files.map(f => f.path))))
        throw Error('invalid manifest directories');
    const app = realDirectory(path.join(root, 'app'), 'fixed app root'), tree = walk(app);
    if (canonical(tree) !== canonical({ files: manifest.files.map(f => f.path).sort(), directories: manifest.directories }))
        throw Error('app inventory differs from manifest');
    for (const expected of manifest.files)
        if (canonical(fileEntry(app, expected.path)) !== canonical(expected))
            throw Error('app integrity digest/length mismatch');
    const releaseTree = walk(root);
    const expectedFiles = ['manifest.json', ...manifest.files.map(f => 'app/' + f.path)].sort(), expectedDirs = ['app', ...manifest.directories.map(d => 'app/' + d)].sort();
    if (canonical(releaseTree) !== canonical({ files: expectedFiles, directories: expectedDirs }))
        throw Error('release inventory contains unmanifested material');
    assertRecord(manifest.runtimes, ['node', 'electron', 'python', 'python_base'], 'runtime manifest');
    for (const runtime of Object.values(manifest.runtimes))
        validateRuntime(runtime);
    const metadata = readJson(path.join(app, 'desktop/package.json'));
    if (!metadata || metadata.main !== 'dist-electron/main.js' || typeof metadata.name !== 'string' || Object.keys(metadata).some(k => !['name', 'version', 'main', 'type'].includes(k)))
        throw Error('fixed package metadata may not load arbitrary code');
    return manifest;
}
