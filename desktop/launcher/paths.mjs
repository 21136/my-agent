import { createHash, randomUUID } from 'node:crypto';
import * as nodeFs from 'node:fs';
import { createRequire } from 'node:module';
// Integrity always checks physical bytes and directories, never Electron's virtual ASAR view.
// original-fs load errors deliberately propagate rather than falling back to patched fs.
const { closeSync, existsSync, fsyncSync, lstatSync, mkdirSync, openSync, readFileSync, readdirSync, realpathSync, renameSync, unlinkSync, writeFileSync } =
    process.versions.electron ? createRequire(process.execPath)('original-fs') : nodeFs;
import path from 'node:path';
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
export const HASH = /^[0-9a-f]{64}$/;
export function sha(bytes) { return createHash('sha256').update(bytes).digest('hex'); }
export function canonical(value) { if (value === null || typeof value === 'string' || typeof value === 'boolean')
    return JSON.stringify(value); if (typeof value === 'number' && Number.isFinite(value))
    return JSON.stringify(value); if (Array.isArray(value))
    return '[' + value.map(canonical).join(',') + ']'; if (value && typeof value === 'object' && [Object.prototype, null].includes(Object.getPrototypeOf(value)))
    return '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + canonical(value[k])).join(',') + '}'; throw Error('non-JSON canonical value'); }
export function assertRecord(value, keys, label = 'record') { if (!value || typeof value !== 'object' || Array.isArray(value) || ![Object.prototype, null].includes(Object.getPrototypeOf(value)) || Object.keys(value).sort().join('\0') !== [...keys].sort().join('\0'))
    throw Error(label + ' fields are invalid'); return value; }
export function samePath(a, b) { const x = path.resolve(a), y = path.resolve(b); return process.platform === 'win32' ? x.toLowerCase() === y.toLowerCase() : x === y; }
export function inside(parent, child) { const rel = path.relative(parent, child); return !!rel && rel !== '..' && !rel.startsWith('..' + path.sep) && !path.isAbsolute(rel); }
export function safeRelative(value) { if (typeof value !== 'string' || !value || path.isAbsolute(value) || /[\\:\x00-\x1f\x7f<>"|?*]/.test(value))
    return false; return value.split('/').every(p => p && p !== '.' && p !== '..' && !/[. ]$/.test(p) && !/^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(p)); }
function realPath(value, label, kind) { if (typeof value !== 'string' || !path.isAbsolute(value) || /[\\/](?:\.|\.\.)(?:[\\/]|$)/.test(value) || !samePath(value, path.normalize(value)))
    throw Error(label + ' must be an absolute canonical path'); const p = path.resolve(value); let current = p; for (;;) {
    const info = lstatSync(current);
    if (info.isSymbolicLink())
        throw Error(label + ' resolves through a link');
    const next = path.dirname(current);
    if (next === current)
        break;
    current = next;
} const info = lstatSync(p); if (kind === 'directory' ? !info.isDirectory() : !info.isFile())
    throw Error(label + ' must be a regular ' + kind); const real = realpathSync.native(p); if (!samePath(p, real))
    throw Error(label + ' is not canonical'); return real; }
export function realDirectory(value, label = 'directory') { return realPath(value, label, 'directory'); }
export function regularFile(value, label = 'file') { return realPath(value, label, 'file'); }
export function walk(root) { root = realDirectory(root, 'tree root'); const files = [], directories = []; let total = 0; const visit = (dir, prefix = '') => { for (const name of readdirSync(dir).sort()) {
    const rel = prefix ? prefix + '/' + name : name;
    if (!safeRelative(rel))
        throw Error('unsafe tree path: ' + rel);
    const file = path.join(dir, name), info = lstatSync(file);
    if (info.isSymbolicLink())
        throw Error('tree contains link: ' + rel);
    if (info.isDirectory()) {
        directories.push(rel);
        visit(file, rel);
    }
    else if (info.isFile()) {
        files.push(rel);
        total += info.size;
    }
    else
        throw Error('tree contains non-regular file');
    if (files.length + directories.length > 50000 || total > 2 * 1024 ** 3)
        throw Error('tree inventory bound exceeded');
} }; visit(root); const aliases = new Set(); for (const rel of [...files, ...directories]) {
    const key = rel.toLowerCase();
    if (aliases.has(key))
        throw Error('Windows path alias in tree');
    aliases.add(key);
} return { files: files.sort(), directories: directories.sort() }; }
export function fileEntry(root, rel) { if (!safeRelative(rel))
    throw Error('unsafe file path'); const p = regularFile(path.join(root, ...rel.split('/')), 'manifest file'), bytes = readFileSync(p); if (bytes.length > 512 * 1024 ** 2)
    throw Error('manifest file length exceeds bound'); return { path: rel, sha256: sha(bytes), byte_length: bytes.length }; }
export function readJson(file, maxBytes = 65536) { file = regularFile(file, 'JSON file'); if (lstatSync(file).size > maxBytes)
    throw Error('JSON frame exceeds bound'); return JSON.parse(readFileSync(file, 'utf8')); }
export function atomicJson(file, value) { realDirectory(path.dirname(file), 'state parent'); const temp = file + '.' + randomUUID() + '.tmp'; let fd; try {
    fd = openSync(temp, 'wx', 0o600);
    writeFileSync(fd, canonical(value) + '\n', 'utf8');
    fsyncSync(fd);
    closeSync(fd);
    fd = undefined;
    renameSync(temp, file);
}
catch (e) {
    if (fd !== undefined)
        closeSync(fd);
    if (existsSync(temp))
        unlinkSync(temp);
    throw e;
} }
export function assertFixtureRun(value) { const root = realDirectory(value, 'fixture run root'); const parent = path.dirname(root); if (!path.basename(root).startsWith('run-') || path.basename(parent) !== 'fixed-control-e0' || path.basename(path.dirname(parent)) !== 'artifacts' || path.basename(path.dirname(path.dirname(parent))) !== 'sandbox' || path.basename(path.dirname(path.dirname(path.dirname(parent)))) !== '.worktrees')
    throw Error('fixture run root must be sandbox/artifacts/fixed-control-e0/run-*'); const marker = readJson(path.join(root, '.fixed-control-fixture.json')); assertRecord(marker, ['format', 'owner', 'run_id'], 'fixture marker'); if (marker.format !== 1 || marker.owner !== 'my-agent-fixed-control-e0' || !UUID.test(marker.run_id))
    throw Error('invalid fixture run marker'); return root; }

/** Publish only a new owned fixture directory. Transient scanner locks never trigger copy/overwrite fallbacks. */
export function publishNewDirectory(stage,target,{rename=renameSync,wait=(ms)=>Atomics.wait(new Int32Array(new SharedArrayBuffer(4)),0,0,ms)}={}){
 const parent=realDirectory(path.dirname(stage),'publication parent');
 const run=assertFixtureRun(path.dirname(parent));
 if(path.basename(parent)!=='releases'||!samePath(path.dirname(target),parent)||!path.basename(stage).startsWith('.stage-')||!HASH.test(path.basename(target)))throw Error('invalid fixture publication paths');
 realDirectory(stage,'owned publication stage');
 if(existsSync(target))throw Error('existing release; refusing overwrite');
 const lockPath=target+'.publish.lock',token=randomUUID();let fd;
 try{
  fd=openSync(lockPath,'wx',0o600);writeFileSync(fd,token);fsyncSync(fd);
  for(let attempt=0;;attempt++){
   realDirectory(stage,'owned publication stage');
   if(existsSync(target))throw Error('existing release; refusing overwrite');
   try{rename(stage,target);realDirectory(target,'published release');return target;}
   catch(error){if(attempt>=9||!['EPERM','EBUSY','EACCES'].includes(error.code)||existsSync(target))throw error;wait(50);}
  }
 }finally{if(fd!==undefined){closeSync(fd);if(existsSync(lockPath)&&readFileSync(lockPath,'utf8')===token)unlinkSync(lockPath);}}
}
