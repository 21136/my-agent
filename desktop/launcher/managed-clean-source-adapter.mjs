import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { lstat, readdir, open, realpath } from 'node:fs/promises';
import { constants as fsConstants } from 'node:fs';
import path from 'node:path';
import { createManagedSourceMergePlan } from './managed-source-transaction.mjs';
import { assertManagedGitObjectStore, assertManagedPlainIndex } from './managed-clean-git-metadata.mjs';
const MODE = 'managed_clean_source_review_v1', FORMAT = 1;
const HASH = /^[a-f0-9]{64}$/, OID = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i;
const TOP = ['blockers', 'deliveryPaths', 'expectedMergedTree', 'format', 'history', 'mode', 'preservationDigest', 'reviewDigest', 'sandbox', 'stable'];
const SRC = ['branch', 'commonDir', 'files', 'gitDir', 'head', 'indexDigest', 'metadataDigest', 'root', 'tree'];
const FIELDS = ['mode', 'oid', 'path', 'sha256'], DEL = ['afterSha256', 'beforeSha256', 'path'], HIST = ['commit', 'parents', 'paths'];
const PREFIX = ['agent-core/', 'desktop/', 'docs/', 'protocol/', 'scripts/'];
const BLOCK = new Set(['.git', '.venv', 'venv', 'node_modules', 'data', 'artifacts', 'dist', 'dist-electron', '__pycache__', 'profile', 'profiles', 'user-data', 'workspace', 'workspaces', 'config-store', 'llm_secrets', 'secrets', 'private', 'credentials', '.managed-runtime']);
const SEG = /^[A-Za-z0-9_@+.,= -]+$/, SECRET = /^(?:\.env(?:\..*)?|llm_secrets\.(?:json|ya?ml)|llm_models\.(?:json|ya?ml)|.*\.(?:pem|key|p12|pfx|cer|crt))$/i;
const cmp = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const isObj = x => x !== null && typeof x === 'object' && !Array.isArray(x) && (Object.getPrototypeOf(x) === Object.prototype || Object.getPrototypeOf(x) === null);
function json(v, seen = new Set()) {
    if (v === null || ['string', 'boolean'].includes(typeof v))
        return;
    if (typeof v === 'number') {
        if (!Number.isFinite(v))
            throw Error('non-finite JSON');
        return;
    }
    if (typeof v !== 'object' || seen.has(v))
        throw Error('Review must be acyclic JSON');
    seen.add(v);
    if (Array.isArray(v)) {
        if (Reflect.ownKeys(v).length !== v.length + 1)
            throw Error('Sparse or hidden array values');
        for (let i = 0; i < v.length; i++) {
            let d = Object.getOwnPropertyDescriptor(v, String(i));
            if (!d?.enumerable || !('value' in d))
                throw Error('Array accessor');
            json(d.value, seen);
        }
    }
    else {
        if (!isObj(v) || Reflect.ownKeys(v).some(k => typeof k !== 'string'))
            throw Error('Review must use plain objects');
        for (let k of Reflect.ownKeys(v)) {
            let d = Object.getOwnPropertyDescriptor(v, k);
            if (!d.enumerable || !('value' in d))
                throw Error('Hidden or accessor property');
            json(d.value, seen);
        }
    }
    seen.delete(v);
}
function canon(v) {
    if (Array.isArray(v))
        return `[${v.map(canon).join(',')}]`;
    if (isObj(v))
        return `{${Object.keys(v).sort(cmp).map(k => `${JSON.stringify(k)}:${canon(v[k])}`).join(',')}}`;
    return JSON.stringify(v);
}
const hash = v => createHash('sha256').update(Buffer.isBuffer(v) || typeof v === 'string' ? v : canon(v)).digest('hex');
function freeze(v) {
    if (v && typeof v === 'object' && !Object.isFrozen(v)) {
        for (const x of Object.values(v))
            freeze(x);
        Object.freeze(v);
    }
    return v;
}
const clone = v => freeze(JSON.parse(canon(v)));
function keys(v, expected, label) {
    if (!isObj(v) || Object.keys(v).sort(cmp).join('\0') !== [...expected].sort(cmp).join('\0'))
        throw Error(`Invalid ${label} keys`);
}
function allowed(p) {
    if (typeof p !== 'string' || !p || p.length > 1024 || p.startsWith('/') || p.includes('\\') || p.includes('\0'))
        return false;
    let ss = p.split('/');
    return ss.every(s => s && SEG.test(s) && !/[. ]$/.test(s) && !/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i.test(s) && !BLOCK.has(s.toLowerCase()) && !SECRET.test(s)) && PREFIX.some(x => p.startsWith(x));
}
function abs(p, label) {
    if (typeof p !== 'string' || !path.isAbsolute(p) || p.includes('\0') || path.resolve(p) !== path.normalize(p))
        throw Error(`Invalid ${label} path`);
    return path.resolve(p);
}
async function ordinary(p, dir = false, allowHardLink = false) {
    const a = abs(p, 'filesystem'), root = path.parse(a).root;
    let cur = root;
    for (const part of a.slice(root.length).split(path.sep).filter(Boolean)) {
        cur = path.join(cur, part);
        const st = await lstat(cur);
        if (st.isSymbolicLink())
            throw Error(`Symlink/reparse path rejected: ${cur}`);
        if (cur === a) {
            if (dir ? !st.isDirectory() : !st.isFile())
                throw Error(`Non-ordinary filesystem entry: ${cur}`);
            if (!dir && !allowHardLink && st.nlink !== 1)
                throw Error(`Hard link rejected: ${cur}`);
        }
        else if (!st.isDirectory())
            throw Error(`Non-directory ancestor: ${cur}`);
    }
    const actual = path.resolve(await realpath(a)), same = process.platform === 'win32' ? actual.toLowerCase() === a.toLowerCase() : actual === a;
    if (!same)
        throw Error(`Filesystem alias/reparse path rejected: ${a}`);
    return a;
}
async function exists(p) {
    try {
        return await lstat(p);
    }
    catch (e) {
        if (e.code === 'ENOENT')
            return null;
        throw e;
    }
}
async function readBounded(file, limit) {
    const absolute = await ordinary(file);
    const before = await lstat(absolute);
    if (!before.isFile() || before.isSymbolicLink() || before.nlink !== 1 || before.size > limit)
        throw Error(`Unsafe, linked, or oversized file: ${absolute}`);
    const handle = await open(absolute, fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW ?? 0));
    try {
        const opened = await handle.stat();
        if (!opened.isFile() || opened.dev !== before.dev || opened.ino !== before.ino || opened.nlink !== 1 || opened.size !== before.size || opened.mode !== before.mode)
            throw Error(`File identity changed before read: ${absolute}`);
        const chunks = [];
        let total = 0;
        while (true) {
            const chunk = Buffer.alloc(Math.min(65536, limit + 1 - total));
            if (!chunk.length)
                throw Error(`File exceeded read bound: ${absolute}`);
            const { bytesRead } = await handle.read(chunk, 0, chunk.length, null);
            if (!bytesRead)
                break;
            total += bytesRead;
            if (total > limit)
                throw Error(`File exceeded read bound: ${absolute}`);
            chunks.push(chunk.subarray(0, bytesRead));
        }
        const after = await handle.stat(), pathAfter = await lstat(absolute);
        if (after.dev !== opened.dev || after.ino !== opened.ino || after.size !== opened.size || after.mode !== opened.mode || after.mtimeMs !== opened.mtimeMs || after.ctimeMs !== opened.ctimeMs || pathAfter.dev !== opened.dev || pathAfter.ino !== opened.ino || pathAfter.size !== opened.size || pathAfter.mode !== opened.mode || pathAfter.nlink !== 1)
            throw Error(`File changed during bounded read: ${absolute}`);
        return Buffer.concat(chunks, total);
    }
    finally {
        await handle.close();
    }
}
function oid(v, n = 'oid') {
    if (typeof v !== 'string' || !OID.test(v))
        throw Error(`Invalid ${n}`);
}
function sha(v, n = 'digest') {
    if (typeof v !== 'string' || !HASH.test(v))
        throw Error(`Invalid ${n}`);
}
function exactSource(source, label) {
    keys(source, SRC, label);
    source.root = abs(source.root, `${label} root`);
    source.gitDir = abs(source.gitDir, `${label} gitDir`);
    source.commonDir = abs(source.commonDir, `${label} commonDir`);
    oid(source.head, `${label} head`);
    oid(source.tree, `${label} tree`);
    sha(source.indexDigest, `${label} index digest`);
    sha(source.metadataDigest, `${label} metadata digest`);
    if (typeof source.branch !== 'string' || !source.branch || source.branch.length > 256 || /[\r\n\0]/.test(source.branch)) {
        throw Error(`Invalid ${label} branch`);
    }
    if (!Array.isArray(source.files) || source.files.length > 4096) {
        throw Error(`Invalid ${label} files`);
    }
    let previous = '';
    for (const file of source.files) {
        keys(file, FIELDS, 'source file');
        if (!allowed(file.path) || file.path <= previous) {
            throw Error('Source files must be sorted, unique, and allowlisted');
        }
        if (file.mode !== '100644' && file.mode !== '100755') {
            throw Error('Unsupported source file mode');
        }
        oid(file.oid, 'source blob oid');
        if (file.oid.length !== source.head.length)
            throw Error('Source object format mismatch');
        sha(file.sha256, 'source file hash');
        previous = file.path;
    }
}
function validate(input) {
    json(input);
    const review = JSON.parse(canon(input));
    keys(review, TOP, 'managed clean source review');
    if (review.format !== FORMAT || review.mode !== MODE)
        throw Error('Unsupported review format or mode');
    exactSource(review.stable, 'stable');
    exactSource(review.sandbox, 'sandbox');
    if (review.stable.commonDir !== review.sandbox.commonDir)
        throw Error('Git common directory mismatch');
    if (review.stable.root === review.sandbox.root)
        throw Error('Source roots must differ');
    if (path.resolve(review.stable.root, '.worktrees', 'sandbox') !== review.sandbox.root) {
        throw Error('Sandbox root must be the exact stable/.worktrees/sandbox path');
    }
    if (review.stable.head.length !== review.sandbox.head.length || review.stable.tree.length !== review.sandbox.tree.length) {
        throw Error('Git object formats differ between source identities');
    }
    if (review.sandbox.branch !== 'experiment/sandbox')
        throw Error('Unexpected sandbox branch');
    if (!Array.isArray(review.history) || review.history.length === 0 || review.history.length > 4096) {
        throw Error('Invalid bounded history');
    }
    let parent = review.stable.head;
    let pathCount = 0;
    const changed = new Set();
    for (const entry of review.history) {
        keys(entry, HIST, 'history entry');
        oid(entry.commit, 'history commit');
        if (entry.commit.length !== review.stable.head.length || entry.commit === parent) {
            throw Error('Invalid history commit identity');
        }
        if (!Array.isArray(entry.parents) || entry.parents.length !== 1 || entry.parents[0] !== parent) {
            throw Error('History must be linear and oldest-first');
        }
        oid(entry.parents[0], 'history parent');
        if (!Array.isArray(entry.paths))
            throw Error('History paths must be an array');
        let previous = '';
        for (const sourcePath of entry.paths) {
            if (!allowed(sourcePath) || sourcePath <= previous)
                throw Error('History paths must be sorted and allowlisted');
            changed.add(sourcePath);
            previous = sourcePath;
            if (++pathCount > 20000)
                throw Error('History path bound exceeded');
        }
        parent = entry.commit;
    }
    if (parent !== review.sandbox.head)
        throw Error('History does not end at sandbox HEAD');
    if (!Array.isArray(review.deliveryPaths) || review.deliveryPaths.length === 0) {
        throw Error('Missing delivery paths');
    }
    let previousDelivery = '';
    const delivered = new Set();
    const stableFiles = new Map(review.stable.files.map(file => [file.path, file.sha256]));
    const sandboxFiles = new Map(review.sandbox.files.map(file => [file.path, file.sha256]));
    for (const item of review.deliveryPaths) {
        keys(item, DEL, 'delivery path');
        if (!allowed(item.path) || item.path <= previousDelivery) {
            throw Error('Delivery paths must be sorted, unique, and allowlisted');
        }
        for (const value of [item.beforeSha256, item.afterSha256]) {
            if (value !== null)
                sha(value, 'delivery hash');
        }
        if (item.beforeSha256 === item.afterSha256)
            throw Error('Delivery path must have a net change');
        if ((stableFiles.get(item.path) ?? null) !== item.beforeSha256 ||
            (sandboxFiles.get(item.path) ?? null) !== item.afterSha256 || !changed.has(item.path)) {
            throw Error('Delivery hashes do not match the source inventories and history');
        }
        delivered.add(item.path);
        previousDelivery = item.path;
    }
    if ([...delivered].some(sourcePath => !changed.has(sourcePath))) {
        throw Error('Delivery set contains a path outside committed history');
    }
    if (review.expectedMergedTree !== review.sandbox.tree)
        throw Error('Merged tree must equal the sandbox tree');
    oid(review.expectedMergedTree, 'expected merged tree');
    sha(review.preservationDigest, 'preservation digest');
    if (!Array.isArray(review.blockers) || review.blockers.length !== 0)
        throw Error('Blocked review is not executable');
    if (Buffer.byteLength(canon(review), 'utf8') > 1024 * 1024)
        throw Error('Review exceeds the bounded JSON size');
    const body = Object.fromEntries(TOP.filter(key => key !== 'reviewDigest').map(key => [key, review[key]]));
    if (!HASH.test(review.reviewDigest) || hash(body) !== review.reviewDigest)
        throw Error('Review digest mismatch');
    return clone(review);
}
export function validateManagedCleanSourceReview(r) { return validate(r); }
export function createManagedCleanSourcePlan({ review, installationId, namespaceDigest } = {}) {
    review = validate(review);
    if (typeof installationId !== 'string' || !UUID.test(installationId))
        throw Error('installationId must be UUID');
    sha(namespaceDigest, 'namespaceDigest');
    return createManagedSourceMergePlan({ format: 1, mode: 'managed_source_merge_plan_v1', installationId, namespaceDigest, reviewDigest: review.reviewDigest, stable: { root: review.stable.root, head: review.stable.head, branch: review.stable.branch, indexDigest: review.stable.indexDigest }, sandbox: { root: review.sandbox.root, head: review.sandbox.head, branch: review.sandbox.branch, indexDigest: review.sandbox.indexDigest }, deliveryPaths: review.deliveryPaths, expectedMergedTree: review.expectedMergedTree, preservationDigest: review.preservationDigest, blockers: [] });
}
const CONFIG = new Map([
    ['core.repositoryformatversion', /^(0|1)$/],
    ['core.filemode', /^(true|false)$/i],
    ['core.bare', /^false$/i],
    ['core.logallrefupdates', /^(true|false)$/i],
    ['core.ignorecase', /^(true|false)$/i],
    ['core.symlinks', /^(true|false)$/i],
    ['extensions.objectformat', /^(sha1|sha256)$/i],
    ['user.name', /^[^\r\n\0]{1,256}$/],
    ['user.email', /^[^\r\n\0]{1,256}$/],
]);
function checkConfig(text, label) {
    let section = '';
    const seen = new Set();
    for (const rawLine of text.split(/\r?\n/)) {
        const line = rawLine.trim();
        if (!line || line.startsWith('#') || line.startsWith(';'))
            continue;
        const sectionMatch = /^\[([A-Za-z0-9." -]+)\]$/.exec(line);
        if (sectionMatch) {
            section = sectionMatch[1].replaceAll('"', '').toLowerCase().split(/\s+/).filter(Boolean).join('.');
            continue;
        }
        const pair = /^([A-Za-z][A-Za-z0-9-]*)\s*=\s*(.*)$/.exec(line);
        if (!pair || !section)
            throw Error(`Unsupported Git config syntax: ${label}`);
        const key = `${section}.${pair[1].toLowerCase()}`;
        const valueRule = CONFIG.get(key);
        if (!valueRule || !valueRule.test(pair[2]) || seen.has(key))
            throw Error(`Unsafe Git config key: ${key}`);
        seen.add(key);
    }
}
async function gitDirs(root) {
    const marker = path.join(root, '.git');
    const markerStat = await lstat(marker);
    if (markerStat.isSymbolicLink())
        throw Error('Linked .git metadata rejected');
    let gitDir;
    if (markerStat.isDirectory()) {
        gitDir = marker;
    }
    else if (markerStat.isFile()) {
        const markerText = (await readBounded(marker, 4096)).toString('utf8');
        const match = /^gitdir: ([^\r\n]+)\r?\n?$/.exec(markerText);
        if (!match)
            throw Error('Invalid worktree .git marker');
        gitDir = path.resolve(root, match[1]);
    }
    else {
        throw Error('Unsupported .git marker type');
    }
    await ordinary(gitDir, true);
    let commonDir = gitDir;
    const commonFile = path.join(gitDir, 'commondir');
    if (await exists(commonFile)) {
        const commonStat = await lstat(commonFile);
        if (!commonStat.isFile() || commonStat.isSymbolicLink() || commonStat.nlink !== 1) {
            throw Error('Unsafe Git commondir file');
        }
        const raw = (await readBounded(commonFile, 4096)).toString('utf8');
        if (!raw || !/^[^\r\n\0]+\r?\n?$/.test(raw))
            throw Error('Invalid Git commondir metadata');
        commonDir = path.resolve(gitDir, raw.trimEnd());
        await ordinary(commonDir, true);
    }
    return { gitDir: path.resolve(gitDir), commonDir: path.resolve(commonDir) };
}
async function gitMeta(root, dirs) {
    const configFiles = [...new Set([
            path.join(dirs.commonDir, 'config'),
            path.join(dirs.gitDir, 'config'),
        ])];
    const configHashes = [];
    if (await exists(path.join(dirs.gitDir, 'config.worktree')))
        throw Error('Git worktree config rejected');
    for (const filename of configFiles) {
        if (!await exists(filename))
            continue;
        const bytes = await readBounded(filename, 65536);
        const text = bytes.toString('utf8');
        if (!Buffer.from(text, 'utf8').equals(bytes))
            throw Error('Invalid Git config encoding');
        checkConfig(text, filename);
        configHashes.push([path.resolve(filename), hash(bytes)]);
    }
    if (dirs.gitDir !== dirs.commonDir) {
        const identityFile = path.join(dirs.gitDir, 'gitdir');
        const referencedGitFile = (await readBounded(identityFile, 4096)).toString('utf8');
        if (!/^[^\r\n\0]+\r?\n?$/.test(referencedGitFile) ||
            path.resolve(referencedGitFile.trimEnd()) !== path.resolve(root, '.git')) {
            throw Error('Git worktree identity mismatch');
        }
    }
    const operationMarkers = [
        'MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'BISECT_LOG', 'AUTO_MERGE',
        'index.lock', 'config.lock', 'HEAD.lock', 'locked', 'rebase-merge', 'rebase-apply', 'sequencer',
    ];
    for (const marker of operationMarkers) {
        if (await exists(path.join(dirs.gitDir, marker)))
            throw Error(`Active Git operation marker: ${marker}`);
    }
    const attributeFiles = new Set([
        path.join(dirs.gitDir, 'info', 'attributes'),
        path.join(dirs.commonDir, 'info', 'attributes'),
    ]);
    for (const filename of attributeFiles) {
        if (await exists(filename) && (await readBounded(filename, 65536)).length !== 0) {
            throw Error('Git attributes rejected');
        }
    }
    for (const relative of ['objects/info/alternates', 'objects/info/http-alternates', 'info/grafts']) {
        if (await exists(path.join(dirs.commonDir, relative)))
            throw Error('Git alternates or grafts rejected');
    }
    const replaceDir = path.join(dirs.commonDir, 'refs', 'replace');
    if (await exists(replaceDir)) {
        await ordinary(replaceDir, true);
        if ((await readdir(replaceDir)).length !== 0)
            throw Error('Loose Git replace refs rejected');
    }
    const salt = hash(root).slice(0, 16);
    const globalConfig = path.join(dirs.commonDir, `.managed-source-global-${salt}.gitconfig`);
    const xdgConfig = path.join(dirs.commonDir, `.managed-source-xdg-${salt}`);
    if (await exists(globalConfig) || await exists(xdgConfig))
        throw Error('Isolated Git config path is occupied');
    // Must complete before any Git process can inspect refs or objects.
    await assertManagedGitObjectStore({ commonDir: dirs.commonDir, gitDir: dirs.gitDir });
    return { configHashes, globalConfig, xdg: xdgConfig };
}
function envFor(metadata) {
    return {
        PATH: process.env.PATH ?? '',
        SYSTEMROOT: process.env.SYSTEMROOT ?? '',
        WINDIR: process.env.WINDIR ?? '',
        HOME: metadata.xdg,
        USERPROFILE: metadata.xdg,
        XDG_CONFIG_HOME: metadata.xdg,
        GIT_CONFIG_NOSYSTEM: '1',
        GIT_CONFIG_GLOBAL: metadata.globalConfig,
        GIT_CONFIG_COUNT: '0',
        GIT_ATTR_NOSYSTEM: '1',
        GIT_NO_REPLACE_OBJECTS: '1',
        GIT_OPTIONAL_LOCKS: '0',
        GIT_TERMINAL_PROMPT: '0',
        GIT_ASKPASS: '',
        SSH_ASKPASS: '',
        LC_ALL: 'C',
    };
}
function invoke(root, metadata, executablePath, args, allowExitOne = false) {
    const fixedArgs = [
        '--no-pager',
        '-c', `safe.directory=${root}`,
        '-c', 'core.hooksPath=NUL',
        '-c', 'core.fsmonitor=false',
        '-c', 'core.autocrlf=false',
        '-c', 'core.untrackedCache=false',
        '-c', 'commit.gpgsign=false',
        '-c', 'tag.gpgsign=false',
        '-c', 'rerere.enabled=false',
        '-c', 'merge.autoStash=false',
        '-c', 'gc.auto=0',
        '-c', 'maintenance.auto=false',
        '-c', 'credential.helper=',
        '-c', 'diff.external=',
        '-c', 'diff.renames=false',
        '-C', root,
        ...args,
    ];
    const result = spawnSync(executablePath, fixedArgs, {
        cwd: root,
        env: envFor(metadata),
        encoding: 'buffer',
        windowsHide: true,
        timeout: 20000,
        maxBuffer: 4 * 1024 * 1024,
        stdio: ['ignore', 'pipe', 'pipe'],
    });
    if (result.error)
        throw result.error;
    if (result.status !== 0 && !(allowExitOne && result.status === 1)) {
        const errorText = (result.stderr ?? Buffer.alloc(0)).toString('utf8').slice(0, 500);
        throw Error(`Restricted Git failed (${args[0]}): ${errorText}`);
    }
    return { code: result.status, out: result.stdout ?? Buffer.alloc(0) };
}
async function executable(input) {
    if (typeof input !== 'string' || !path.isAbsolute(input) || path.resolve(input) !== path.normalize(input)) {
        throw Error('gitExecutable must be absolute and normalized');
    }
    const file = await ordinary(input, false, true);
    if (process.platform === 'win32' && path.extname(file).toLowerCase() !== '.exe') {
        throw Error('Native Git .exe required');
    }
    const before = await lstat(file);
    if (!before.isFile() || before.isSymbolicLink())
        throw Error('Git executable is not an ordinary file');
    const handle = await open(file, fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW ?? 0));
    try {
        const opened = await handle.stat();
        const after = await lstat(file);
        if (!opened.isFile() || opened.dev !== before.dev || opened.ino !== before.ino ||
            opened.size !== before.size || after.dev !== opened.dev || after.ino !== opened.ino ||
            after.size !== opened.size) {
            throw Error('Git executable identity changed during verification');
        }
    }
    finally {
        await handle.close();
    }
    return file;
}
function nul(buffer, label) {
    if (buffer.length === 0)
        return [];
    if (buffer.at(-1) !== 0)
        throw Error(`Malformed ${label} output`);
    return buffer.subarray(0, -1).toString('utf8').split('\0');
}
function treeMap(buffer) {
    const entries = new Map();
    for (const row of nul(buffer, 'tree')) {
        const tab = row.indexOf('\t');
        const fields = row.slice(0, tab).split(' ');
        const sourcePath = row.slice(tab + 1);
        if (tab < 0 || fields.length !== 3 || !sourcePath || entries.has(sourcePath)) {
            throw Error('Malformed Git tree inventory');
        }
        const [mode, type, objectId] = fields;
        if (type !== 'blob' || !['100644', '100755'].includes(mode) || !OID.test(objectId)) {
            throw Error(`Unsupported Git tree entry: ${sourcePath}`);
        }
        if (sourcePath.split('/').some(segment => segment.toLowerCase() === '.gitattributes')) {
            throw Error('Git attributes rejected');
        }
        entries.set(sourcePath, { mode, oid: objectId });
    }
    return entries;
}
function indexMap(buffer) {
    const entries = new Map();
    for (const row of nul(buffer, 'index')) {
        const tab = row.indexOf('\t');
        const fields = row.slice(0, tab).split(' ');
        const sourcePath = row.slice(tab + 1);
        if (tab < 0 || fields.length !== 3 || fields[2] !== '0' || !sourcePath || entries.has(sourcePath) ||
            !['100644', '100755'].includes(fields[0]) || !OID.test(fields[1])) {
            throw Error('Staged, conflicted, or non-ordinary Git index entry');
        }
        entries.set(sourcePath, { mode: fields[0], oid: fields[1] });
    }
    return entries;
}
function equalMap(left, right) {
    return left.size === right.size && [...left].every(([key, value]) => right.get(key)?.mode === value.mode && right.get(key)?.oid === value.oid);
}
async function blobHash(root, sourcePath, entry) {
    if (!allowed(sourcePath))
        throw Error(`Non-source tracked path rejected: ${sourcePath}`);
    const filename = path.resolve(root, ...sourcePath.split('/'));
    if (!filename.startsWith(`${root}${path.sep}`))
        throw Error('Tracked path escaped the source root');
    let parent = root;
    for (const segment of sourcePath.split('/').slice(0, -1)) {
        parent = path.join(parent, segment);
        await ordinary(parent, true);
    }
    const info = await lstat(filename);
    if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1 || info.size > 32 * 1024 * 1024) {
        throw Error(`Unsafe or oversized tracked source: ${sourcePath}`);
    }
    const fileMode = (info.mode & 0o111) !== 0 ? '100755' : '100644';
    if (fileMode !== entry.mode)
        throw Error(`Working source mode differs from Git tree: ${sourcePath}`);
    const bytes = await readBounded(filename, 32 * 1024 * 1024);
    const objectFormat = entry.oid.length === 64 ? 'sha256' : 'sha1';
    const objectId = createHash(objectFormat).update(`blob ${bytes.length}\0`).update(bytes).digest('hex');
    if (objectId !== entry.oid)
        throw Error(`Raw source bytes differ from Git object: ${sourcePath}`);
    return hash(bytes);
}
async function noUntracked(root, tree, nestedRoot) {
    const tracked = [...tree.keys()], seen = { count: 0 };
    async function walk(dir, relative = '', depth = 0) {
        if (depth > 64)
            throw Error('Worktree traversal depth exceeded');
        for (const entry of await readdir(dir, { withFileTypes: true })) {
            if (++seen.count > 20000)
                throw Error('Worktree entry-count bound exceeded');
            const rel = relative ? `${relative}/${entry.name}` : entry.name, full = path.join(dir, entry.name), resolved = path.resolve(full);
            if (!relative && entry.name.toLowerCase() === '.git')
                continue;
            if (path.resolve(root) !== path.resolve(nestedRoot) && resolved === path.resolve(nestedRoot))
                continue;
            if (entry.isSymbolicLink())
                throw Error(`Linked/reparse worktree entry rejected: ${rel}`);
            if (BLOCK.has(entry.name.toLowerCase()))
                throw Error(`Protected directory or file rejected by name: ${rel}`);
            if (entry.isDirectory()) {
                const isNestedAncestor = path.resolve(nestedRoot).startsWith(`${resolved}${path.sep}`);
                const hasTracked = tracked.some(item => item.startsWith(`${rel}/`));
                if (!isNestedAncestor && !hasTracked)
                    throw Error(`Untracked or protected directory rejected before descent: ${rel}`);
                await ordinary(full, true);
                await walk(full, rel, depth + 1);
                continue;
            }
            if (!entry.isFile())
                throw Error(`Special filesystem entry rejected: ${rel}`);
            if (rel.toLowerCase() === '.gitattributes' || rel.toLowerCase().endsWith('/.gitattributes'))
                throw Error('Unsafe Git attributes rejected');
            if (!tracked.includes(rel))
                throw Error(`Untracked or protected path rejected: ${rel}`);
        }
    }
    await walk(root);
}
async function state(root, dirs, gitExecutable, nestedRoot) {
    const metadata = await gitMeta(root, dirs);
    const git = (args, allowExitOne = false) => invoke(root, metadata, gitExecutable, args, allowExitOne);
    const replacementRefs = git(['for-each-ref', '--format=%(refname)', 'refs/replace']).out.toString('utf8').trim();
    if (replacementRefs)
        throw Error('Packed or loose Git replace refs rejected');
    const head = git(['rev-parse', '--verify', 'HEAD']).out.toString('utf8').trim();
    oid(head, 'HEAD');
    const branch = git(['symbolic-ref', '--quiet', '--short', 'HEAD']).out.toString('utf8').trim();
    if (!branch)
        throw Error('Detached HEAD rejected');
    const reportedGitDir = path.resolve(git(['rev-parse', '--absolute-git-dir']).out.toString('utf8').trim());
    const reportedCommonDir = path.resolve(git(['rev-parse', '--path-format=absolute', '--git-common-dir']).out.toString('utf8').trim());
    if (reportedGitDir !== dirs.gitDir || reportedCommonDir !== dirs.commonDir) {
        throw Error('Git directory identity mismatch');
    }
    const treeOid = git(['rev-parse', '--verify', `${head}^{tree}`]).out.toString('utf8').trim();
    oid(treeOid, 'tree');
    const entries = treeMap(git(['ls-tree', '-r', '--full-tree', '-z', head]).out);
    for (const sourcePath of entries.keys()) {
        if (!allowed(sourcePath))
            throw Error(`Non-source tracked path rejected: ${sourcePath}`);
    }
    let indexPath = git(['rev-parse', '--git-path', 'index']).out.toString('utf8').trim();
    indexPath = path.isAbsolute(indexPath) ? indexPath : path.resolve(root, indexPath);
    await ordinary(indexPath);
    const indexBytes = await readBounded(indexPath, 64 * 1024 * 1024);
    if (indexBytes.length < 12 || indexBytes.length > 64 * 1024 * 1024 || indexBytes.subarray(0, 4).toString('ascii') !== 'DIRC') {
        throw Error('Invalid or oversized Git index');
    }
    assertManagedPlainIndex(indexBytes, { objectFormat: head.length === 64 ? 'sha256' : 'sha1' });
    const indexEntries = indexMap(git(['ls-files', '--stage', '-z']).out);
    if (!equalMap(indexEntries, entries))
        throw Error('Staged index differs from HEAD');
    const indexFlags = nul(git(['ls-files', '-v', '-z']).out, 'index flags');
    if (indexFlags.length !== indexEntries.size || indexFlags.some(row => row[0] !== 'H' || !indexEntries.has(row.slice(2)))) {
        throw Error('Special Git index flags rejected');
    }
    await noUntracked(root, entries, nestedRoot);
    const stashTip = git(['for-each-ref', '--format=%(refname) %(objectname)', 'refs/stash']).out.toString('utf8').trim();
    let stash = [];
    if (stashTip) {
        const reflog = git(['reflog', 'show', '--format=%H', 'refs/stash']).out.toString('utf8').trim();
        stash = reflog ? reflog.split(/\r?\n/) : [];
        for (const stashOid of stash)
            oid(stashOid, 'stash reflog oid');
    }
    const metadataDigest = hash({
        config: metadata.configHashes,
        stash,
        operations: [],
        gitDir: dirs.gitDir,
        commonDir: dirs.commonDir,
    });
    const files = [];
    for (const [sourcePath, entry] of entries) {
        files.push({
            path: sourcePath,
            sha256: await blobHash(root, sourcePath, entry),
            mode: entry.mode,
            oid: entry.oid,
        });
    }
    return {
        root,
        head,
        branch,
        gitDir: dirs.gitDir,
        commonDir: dirs.commonDir,
        tree: treeOid,
        indexDigest: hash(indexBytes),
        metadataDigest,
        files,
        entries,
        stash,
        indexPath,
        metadata,
    };
}
function historyFiles(buf) {
    let all = [];
    for (let p of nul(buf, 'commit paths')) {
        if (!allowed(p))
            throw Error(`Protected or non-source committed path: ${p}`);
        all.push(p);
    }
    return [...new Set(all)].sort(cmp);
}
function same(a, b) { return canon(a) === canon(b); }
function preservation(a, b, delivery) { let ds = new Set(delivery.map(x => x.path)); return hash({ stable: { head: a.head, tree: a.tree, indexDigest: a.indexDigest, metadataDigest: a.metadataDigest, nonDelivery: [...a.entries].filter(([p]) => !ds.has(p)).map(([p, e]) => [p, e.mode, e.oid]), stash: a.stash }, sandbox: { head: b.head, tree: b.tree, indexDigest: b.indexDigest, metadataDigest: b.metadataDigest, stash: b.stash } }); }
async function inspect({ stableRoot, sandboxRoot, gitExecutable }) {
    const executablePath = await executable(gitExecutable);
    const stable = await ordinary(abs(stableRoot, 'stable root'), true);
    const sandbox = await ordinary(abs(sandboxRoot, 'sandbox root'), true);
    if (path.resolve(stable, '.worktrees', 'sandbox') !== sandbox) {
        throw Error('Sandbox must be exact stable/.worktrees/sandbox');
    }
    const stableDirs = await gitDirs(stable);
    const sandboxDirs = await gitDirs(sandbox);
    if (stableDirs.commonDir !== sandboxDirs.commonDir)
        throw Error('Sources must share Git common directory');
    const stableState = await state(stable, stableDirs, executablePath, sandbox);
    const sandboxState = await state(sandbox, sandboxDirs, executablePath, sandbox);
    if (sandboxState.branch !== 'experiment/sandbox' || stableState.branch === 'experiment/sandbox') {
        throw Error('Unexpected stable/sandbox branch identity');
    }
    const ancestry = invoke(stable, stableState.metadata, executablePath, ['merge-base', '--is-ancestor', stableState.head, sandboxState.head], true);
    if (ancestry.code !== 0)
        throw Error('Stable is not a proper sandbox ancestor');
    const revisionList = invoke(sandbox, sandboxState.metadata, executablePath, ['rev-list', '--reverse', '--topo-order', `${stableState.head}..${sandboxState.head}`]).out.toString('utf8').trim();
    const commits = revisionList ? revisionList.split(/\r?\n/) : [];
    if (commits.length === 0 || commits.length > 4096)
        throw Error('Empty or oversized committed history');
    const history = [];
    let expectedParent = stableState.head;
    for (const commit of commits) {
        oid(commit, 'history commit');
        const parents = invoke(sandbox, sandboxState.metadata, executablePath, ['rev-list', '--parents', '-n', '1', commit]).out.toString('utf8').trim().split(/\s+/).slice(1);
        if (parents.length !== 1 || parents[0] !== expectedParent)
            throw Error('Non-linear history rejected');
        const paths = historyFiles(invoke(sandbox, sandboxState.metadata, executablePath, ['diff-tree', '--no-commit-id', '--name-only', '-r', '--no-renames', '--no-ext-diff', '--no-textconv', '-z', commit]).out);
        history.push({ commit, parents, paths });
        expectedParent = commit;
    }
    if (expectedParent !== sandboxState.head)
        throw Error('History does not reach sandbox HEAD');
    const changedPaths = [...new Set(history.flatMap(entry => entry.paths))].sort(cmp);
    const stableFiles = new Map(stableState.files.map(file => [file.path, file.sha256]));
    const sandboxFiles = new Map(sandboxState.files.map(file => [file.path, file.sha256]));
    const deliveryPaths = [];
    for (const sourcePath of changedPaths) {
        const beforeSha256 = stableFiles.get(sourcePath) ?? null;
        const afterSha256 = sandboxFiles.get(sourcePath) ?? null;
        if (beforeSha256 !== afterSha256)
            deliveryPaths.push({ path: sourcePath, beforeSha256, afterSha256 });
    }
    if (deliveryPaths.length === 0)
        throw Error('Committed history has no net source delivery');
    const body = {
        format: FORMAT,
        mode: MODE,
        stable: {
            root: stableState.root,
            head: stableState.head,
            branch: stableState.branch,
            indexDigest: stableState.indexDigest,
            gitDir: stableState.gitDir,
            commonDir: stableState.commonDir,
            tree: stableState.tree,
            metadataDigest: stableState.metadataDigest,
            files: stableState.files,
        },
        sandbox: {
            root: sandboxState.root,
            head: sandboxState.head,
            branch: sandboxState.branch,
            indexDigest: sandboxState.indexDigest,
            gitDir: sandboxState.gitDir,
            commonDir: sandboxState.commonDir,
            tree: sandboxState.tree,
            metadataDigest: sandboxState.metadataDigest,
            files: sandboxState.files,
        },
        history,
        deliveryPaths,
        expectedMergedTree: sandboxState.tree,
        preservationDigest: preservation(stableState, sandboxState, deliveryPaths),
        blockers: [],
    };
    body.reviewDigest = hash(body);
    return validate(body);
}
export async function inspectManagedCleanSource(options = {}) {
    return inspect(options);
}
function planFor(review, plan) {
    const expected = createManagedCleanSourcePlan({
        review,
        installationId: plan.installationId,
        namespaceDigest: plan.namespaceDigest,
    });
    if (canon(expected) !== canon(plan))
        throw Error('Plan does not bind the complete reviewed source range');
    return expected;
}
function validateWriteContext(context) {
    if (!isObj(context))
        throw Error('Invalid coordinator write context');
    json(context);
    keys(context, ['operationId', 'writeId'], 'coordinator write context');
    if (typeof context.operationId !== 'string' || !UUID.test(context.operationId) ||
        typeof context.writeId !== 'string' || !UUID.test(context.writeId)) {
        throw Error('Invalid coordinator write token');
    }
}
/**
 * Build a trusted in-process adapter. When called by recorded-package execution,
 * assertWriteBoundary must synchronously verify the held canonical package at the
 * last possible point before spawnSync. This is not a renderer callback.
 */
export async function createManagedCleanSourceAdapter({ review: untrustedReview, gitExecutable, assertWriteBoundary, } = {}) {
    if (assertWriteBoundary !== undefined && typeof assertWriteBoundary !== 'function') {
        throw Error('assertWriteBoundary must be a trusted synchronous function');
    }
    const review = validate(untrustedReview);
    const executablePath = await executable(gitExecutable);
    let attempted = false;
    let boundaryActive = false;
    let merged = false;
    let verified = false;
    async function revalidate(plan) {
        planFor(review, plan);
        const current = await inspect({
            stableRoot: review.stable.root,
            sandboxRoot: review.sandbox.root,
            gitExecutable: executablePath,
        });
        if (current.reviewDigest !== review.reviewDigest)
            throw Error('Managed source review drifted');
        const exactPlan = createManagedCleanSourcePlan({
            review: current,
            installationId: plan.installationId,
            namespaceDigest: plan.namespaceDigest,
        });
        return {
            blockers: [],
            namespaceDigest: plan.namespaceDigest,
            planDigest: exactPlan.planDigest,
            reviewDigest: current.reviewDigest,
        };
    }
    async function merge(plan, context) {
        if (attempted || boundaryActive || verified)
            throw Error('Adapter is single-use');
        validateWriteContext(context);
        await revalidate(plan);
        const stableDirs = await gitDirs(review.stable.root);
        const metadata = await gitMeta(review.stable.root, stableDirs);
        if (attempted || boundaryActive)
            throw Error('Adapter write was already attempted');
        // No await or asynchronous callback may occur from this guard through spawnSync.
        boundaryActive = true;
        try {
            if (assertWriteBoundary) {
                const result = assertWriteBoundary();
                if (result && typeof result.then === 'function') {
                    throw Error('Write-boundary guard must be synchronous');
                }
            }
            attempted = true;
            const command = invoke(review.stable.root, metadata, executablePath, [
                '-c', 'user.name=Managed Source Merge Adapter',
                '-c', 'user.email=managed-source-adapter@invalid',
                'merge', '--no-ff', '--no-edit', '--no-stat', '--no-verify', review.sandbox.head,
            ], true);
            merged = command.code === 0;
            return { exitCode: command.code };
        }
        finally {
            boundaryActive = false;
        }
    }
    async function verify(plan) {
        if (!attempted || !merged || verified)
            throw Error('No unverified merge');
        planFor(review, plan);
        const stableDirs = await gitDirs(review.stable.root);
        const metadata = await gitMeta(review.stable.root, stableDirs);
        const git = (args) => invoke(review.stable.root, metadata, executablePath, args);
        const mergeCommit = git(['rev-parse', '--verify', 'HEAD']).out.toString('utf8').trim();
        const parents = git(['rev-list', '--parents', '-n', '1', mergeCommit]).out.toString('utf8').trim().split(/\s+/).slice(1);
        if (parents.length !== 2 || parents[0] !== review.stable.head || parents[1] !== review.sandbox.head) {
            throw Error('Merge parents differ from review');
        }
        const mergedTree = git(['rev-parse', '--verify', `${mergeCommit}^{tree}`]).out.toString('utf8').trim();
        if (mergedTree !== review.expectedMergedTree)
            throw Error('Merged tree differs from review');
        const actualPaths = historyFiles(git([
            'diff-tree', '--no-commit-id', '--name-only', '-r', '--no-renames', '--no-ext-diff', '--no-textconv', '-z',
            `${review.stable.head}..${mergeCommit}`,
        ]).out);
        const expectedPaths = review.deliveryPaths.map(item => item.path);
        if (!same(actualPaths, expectedPaths))
            throw Error('Merge delta differs from exact delivery paths');
        const oldTree = treeMap(git(['ls-tree', '-r', '--full-tree', '-z', review.stable.head]).out);
        const actualTree = treeMap(git(['ls-tree', '-r', '--full-tree', '-z', mergeCommit]).out);
        const deliverySet = new Set(expectedPaths);
        const oldNonDelivery = [...oldTree]
            .filter(([sourcePath]) => !deliverySet.has(sourcePath))
            .map(([sourcePath, entry]) => [sourcePath, entry.mode, entry.oid]);
        const actualNonDelivery = [...actualTree]
            .filter(([sourcePath]) => !deliverySet.has(sourcePath))
            .map(([sourcePath, entry]) => [sourcePath, entry.mode, entry.oid]);
        if (!same(actualNonDelivery, oldNonDelivery))
            throw Error('Stable non-delivery tree changed');
        const stableNow = await state(review.stable.root, stableDirs, executablePath, review.sandbox.root);
        if (stableNow.head !== mergeCommit)
            throw Error('Stable HEAD differs from verified merge commit');
        for (const item of review.deliveryPaths) {
            const actualHash = stableNow.files.find(file => file.path === item.path)?.sha256 ?? null;
            if (actualHash !== item.afterSha256)
                throw Error(`Merged raw file hash mismatch: ${item.path}`);
        }
        const sandboxDirs = await gitDirs(review.sandbox.root);
        const sandboxNow = await state(review.sandbox.root, sandboxDirs, executablePath, review.sandbox.root);
        if (stableNow.branch !== review.stable.branch || stableNow.root !== review.stable.root ||
            stableNow.gitDir !== review.stable.gitDir || stableNow.commonDir !== review.stable.commonDir) {
            throw Error('Stable branch or identity changed during merge');
        }
        if (sandboxNow.head !== review.sandbox.head || sandboxNow.branch !== review.sandbox.branch ||
            sandboxNow.root !== review.sandbox.root || sandboxNow.gitDir !== review.sandbox.gitDir ||
            sandboxNow.commonDir !== review.sandbox.commonDir || sandboxNow.indexDigest !== review.sandbox.indexDigest ||
            sandboxNow.metadataDigest !== review.sandbox.metadataDigest) {
            throw Error('Sandbox identity, branch, HEAD, index, stash, or metadata changed during merge');
        }
        if (stableNow.metadataDigest !== review.stable.metadataDigest) {
            throw Error('Stable config, stash, or operation metadata changed');
        }
        const receipt = {
            deliveryPaths: expectedPaths,
            mergeCommit,
            mergedTree,
            preservationDigest: review.preservationDigest,
            sandboxParent: parents[1],
            stableParent: parents[0],
        };
        verified = true;
        return receipt;
    }
    return Object.freeze({ revalidate, merge, verify });
}
