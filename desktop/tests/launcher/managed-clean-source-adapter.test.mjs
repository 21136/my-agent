import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, mkdtemp, readFile, writeFile, lstat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { inspectManagedCleanSource, validateManagedCleanSourceReview, createManagedCleanSourcePlan, createManagedCleanSourceAdapter, } from '../../launcher/managed-clean-source-adapter.mjs';
const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const expectedSandboxRoot = path.resolve('F:/my-agent-main/.worktrees/sandbox');
if (path.resolve(process.cwd()) !== repositoryRoot || repositoryRoot !== expectedSandboxRoot) {
    throw new Error('Tests must run from F:/my-agent-main/.worktrees/sandbox; no fixtures were created');
}
const evidenceRoot = path.join(repositoryRoot, 'artifacts/managed-execution-20261024');
const gitExecutable = 'C:/Program Files/Git/cmd/git.exe';
const installationId = 'f6c2d116-3dc5-4ff1-99b1-c7feac43e2f0';
const namespaceDigest = createHash('sha256').update('synthetic namespace').digest('hex');
const sha = value => createHash('sha256').update(value).digest('hex');
const canonical = value => Array.isArray(value)
    ? `[${value.map(canonical).join(',')}]`
    : value && typeof value === 'object'
        ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`
        : JSON.stringify(value);
async function createManagedCleanSourceFixture({ name = 'adapter' } = {}) {
    // This guard must precede every directory creation and every Git invocation.
    if (path.resolve(process.cwd()) !== expectedSandboxRoot || repositoryRoot !== expectedSandboxRoot) {
        throw new Error('Refusing fixture creation outside the explicit sandbox worktree');
    }
    const retained = name === 'refusal' ? process.env.MANAGED_SOURCE_ADAPTER_REFUSAL_ROOT : process.env.MANAGED_SOURCE_ADAPTER_PRIMARY_ROOT;
    let runRoot;
    if (retained) {
        runRoot = path.resolve(retained);
        if (!runRoot.startsWith(`${evidenceRoot}${path.sep}`))
            throw new Error('Retained fixture must remain under evidence root');
    }
    else {
        await mkdir(evidenceRoot, { recursive: true });
        runRoot = await mkdtemp(path.join(evidenceRoot, `${name}-${randomUUID()}-`));
    }
    const stableRoot = path.join(runRoot, 'stable');
    const sandboxRoot = path.join(stableRoot, '.worktrees', 'sandbox');
    const template = path.join(runRoot, 'empty-template');
    const home = path.join(runRoot, 'empty-home');
    const hooks = path.join(runRoot, 'empty-hooks');
    const globalConfig = path.join(runRoot, 'empty-global.gitconfig');
    if (!retained) {
        await Promise.all([
            mkdir(stableRoot, { recursive: true }), mkdir(template), mkdir(home), mkdir(hooks), writeFile(globalConfig, ''),
        ]);
    }
    const isolation = { home, hooks, globalConfig };
    function git(cwd, ...args) {
        const resolved = path.resolve(cwd);
        if (![stableRoot, sandboxRoot].includes(resolved) || !resolved.startsWith(`${evidenceRoot}${path.sep}`)) {
            throw new Error(`Refusing Git outside the owned fixture: ${resolved}`);
        }
        const env = {
            PATH: process.env.PATH ?? '', SYSTEMROOT: process.env.SYSTEMROOT ?? '', WINDIR: process.env.WINDIR ?? '',
            HOME: isolation.home, USERPROFILE: isolation.home, XDG_CONFIG_HOME: isolation.home,
            GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: isolation.globalConfig, GIT_CONFIG_COUNT: '0',
            GIT_ATTR_NOSYSTEM: '1', GIT_OPTIONAL_LOCKS: '0', GIT_TERMINAL_PROMPT: '0', LC_ALL: 'C',
        };
        const fixed = [
            '--no-pager', '-c', `safe.directory=${resolved}`, '-c', 'commit.gpgsign=false', '-c', 'tag.gpgsign=false',
            '-c', 'core.fsmonitor=false', '-c', `core.hooksPath=${isolation.hooks}`, '-c', 'credential.helper=',
            '-c', 'maintenance.auto=false', '-c', 'gc.auto=0',
        ];
        const result = spawnSync(gitExecutable, [...fixed, ...args], {
            cwd: resolved, env, encoding: 'utf8', windowsHide: true, timeout: 15000, maxBuffer: 1024 * 1024,
        });
        if (result.error)
            throw result.error;
        if (result.status !== 0)
            throw new Error(`synthetic Git failed (${args[0]}): ${result.stderr}`);
        return result.stdout.trim();
    }
    if (!path.isAbsolute(gitExecutable) || (await lstat(gitExecutable)).isSymbolicLink())
        throw new Error('Test Git must be a verified absolute executable');
    const deliveryBytes = Buffer.from('export const reviewed = true;\n');
    if (retained)
        return { runRoot, stableRoot, sandboxRoot, git, deliveryBytes, reused: true };
    git(stableRoot, 'init', '-b', 'main', `--template=${template}`);
    // Inspect the just-created config directly before any later Git command can follow an include.
    const initialConfig = await readFile(path.join(stableRoot, '.git', 'config'), 'utf8');
    if (/^\s*include(?:if)?\s*=/im.test(initialConfig) || /\[include(?:If)?\]/i.test(initialConfig))
        throw new Error('Unexpected include in synthetic repository config');
    git(stableRoot, 'config', 'user.name', 'Synthetic Managed Source');
    git(stableRoot, 'config', 'user.email', 'managed-source@example.invalid');
    await mkdir(path.join(stableRoot, '.git', 'info'), { recursive: true });
    await writeFile(path.join(stableRoot, '.git', 'info', 'exclude'), '/.worktrees/\n');
    await mkdir(path.join(stableRoot, 'desktop', 'launcher'), { recursive: true });
    await writeFile(path.join(stableRoot, 'desktop', 'launcher', 'base.mjs'), 'export const base = true;\n');
    git(stableRoot, 'add', '--', 'desktop/launcher/base.mjs');
    git(stableRoot, 'commit', '-m', 'synthetic stable baseline');
    git(stableRoot, 'branch', 'experiment/sandbox');
    await mkdir(path.dirname(sandboxRoot), { recursive: true });
    git(stableRoot, 'worktree', 'add', sandboxRoot, 'experiment/sandbox');
    // A worktree is linked through a .git file; inspect its direct metadata before following it.
    const marker = await readFile(path.join(sandboxRoot, '.git'), 'utf8');
    if (!/^gitdir: [^\r\n]+\r?\n?$/.test(marker))
        throw new Error('Unexpected synthetic worktree marker');
    await writeFile(path.join(sandboxRoot, 'desktop', 'launcher', 'reviewed.mjs'), deliveryBytes);
    git(sandboxRoot, 'add', '--', 'desktop/launcher/reviewed.mjs');
    git(sandboxRoot, 'commit', '-m', 'synthetic reviewed source');
    return { runRoot, stableRoot, sandboxRoot, git, deliveryBytes };
}
// Exported only from this test module so the parent integration may reuse a retained fixture if desired.
export { createManagedCleanSourceFixture };
let primaryReview;
test('inspects, binds, revalidates, and performs one clean linear shared-worktree merge', async (t) => {
    const fixture = await createManagedCleanSourceFixture();
    t.diagnostic(`retained fixture=${fixture.runRoot}`);
    const { stableRoot, sandboxRoot, git } = fixture;
    const stableHead = git(stableRoot, 'rev-parse', 'HEAD');
    const sandboxHead = git(sandboxRoot, 'rev-parse', 'HEAD');
    const stableIndex = git(stableRoot, 'rev-parse', '--git-path', 'index');
    const sandboxIndex = git(sandboxRoot, 'rev-parse', '--git-path', 'index');
    const stableIndexBytes = await readFile(path.resolve(stableRoot, stableIndex));
    const sandboxIndexBytes = await readFile(path.resolve(sandboxRoot, sandboxIndex));
    const stablePreserved = await readFile(path.join(stableRoot, 'desktop', 'launcher', 'base.mjs'));
    const review = await inspectManagedCleanSource({ stableRoot, sandboxRoot, gitExecutable });
    primaryReview = review;
    assert.deepEqual(Object.keys(review).sort(), [
        'blockers', 'deliveryPaths', 'expectedMergedTree', 'format', 'history', 'mode', 'preservationDigest',
        'reviewDigest', 'sandbox', 'stable',
    ].sort());
    assert.equal(review.format, 1);
    assert.equal(review.mode, 'managed_clean_source_review_v1');
    assert.deepEqual(review.blockers, []);
    assert.equal(review.stable.head, stableHead);
    assert.equal(review.sandbox.head, sandboxHead);
    assert.equal(review.expectedMergedTree, review.sandbox.tree);
    assert.deepEqual(review.history.map(entry => entry.commit), [sandboxHead]);
    assert.deepEqual(review.deliveryPaths, [{
            path: 'desktop/launcher/reviewed.mjs', beforeSha256: null, afterSha256: sha(fixture.deliveryBytes),
        }]);
    assert.ok(Object.isFrozen(review) && Object.isFrozen(review.stable) && Object.isFrozen(review.history[0]));
    assert.equal(validateManagedCleanSourceReview(review).reviewDigest, review.reviewDigest);
    const plan = createManagedCleanSourcePlan({ review, installationId, namespaceDigest });
    assert.equal(plan.reviewDigest, review.reviewDigest);
    assert.equal(plan.expectedMergedTree, review.sandbox.tree);
    const tampered = JSON.parse(JSON.stringify(review));
    tampered.deliveryPaths[0].afterSha256 = sha('not the reviewed source');
    assert.throws(() => validateManagedCleanSourceReview(tampered), /digest|review|delivery/i);
    assert.throws(() => createManagedCleanSourcePlan({ review, installationId, namespaceDigest: 'x' }), /namespace/i);
    await assert.rejects(() => inspectManagedCleanSource({ stableRoot, sandboxRoot: path.join(stableRoot, '.worktrees', 'wrong'), gitExecutable }));
    const configFile = path.join(stableRoot, '.git', 'config');
    const safeConfig = await readFile(configFile);
    try {
        await writeFile(configFile, Buffer.concat([safeConfig, Buffer.from('\n[include]\n path = C:/forbidden.gitconfig\n')]));
        await assert.rejects(() => inspectManagedCleanSource({ stableRoot, sandboxRoot, gitExecutable }), /unsafe Git config/i);
    }
    finally {
        await writeFile(configFile, safeConfig);
    }
    const adapter = await createManagedCleanSourceAdapter({ review, gitExecutable });
    const beforeWrite = await adapter.revalidate(plan);
    assert.deepEqual(Object.keys(beforeWrite).sort(), ['blockers', 'namespaceDigest', 'planDigest', 'reviewDigest'].sort());
    assert.deepEqual(beforeWrite.blockers, []);
    assert.equal(beforeWrite.reviewDigest, review.reviewDigest);
    const sourcePath = path.join(sandboxRoot, 'desktop', 'launcher', 'reviewed.mjs');
    await writeFile(sourcePath, 'unreviewed drift\n');
    await assert.rejects(() => adapter.revalidate(plan), /drift|changed|review|dirty/i, 'drift must be rejected before any stable write');
    assert.equal(git(stableRoot, 'rev-parse', 'HEAD'), stableHead);
    await writeFile(sourcePath, fixture.deliveryBytes);
    assert.deepEqual((await adapter.revalidate(plan)).blockers, []);
    const guarded = await createManagedCleanSourceAdapter({ review, gitExecutable, assertWriteBoundary() { throw new Error('package descriptor drifted'); } });
    await assert.rejects(() => guarded.merge(plan, { operationId: randomUUID(), writeId: randomUUID() }), /package descriptor drifted/);
    assert.equal(git(stableRoot, 'rev-parse', 'HEAD'), stableHead, 'failed final boundary guard must precede Git');
    const merged = await adapter.merge(plan, { operationId: randomUUID(), writeId: randomUUID() });
    assert.deepEqual(merged, { exitCode: 0 });
    const receipt = await adapter.verify(plan);
    assert.deepEqual(Object.keys(receipt).sort(), [
        'deliveryPaths', 'mergeCommit', 'mergedTree', 'preservationDigest', 'sandboxParent', 'stableParent',
    ].sort());
    assert.equal(receipt.stableParent, stableHead);
    assert.equal(receipt.sandboxParent, sandboxHead);
    assert.equal(receipt.mergedTree, review.expectedMergedTree);
    assert.equal(receipt.preservationDigest, review.preservationDigest);
    assert.deepEqual(receipt.deliveryPaths, ['desktop/launcher/reviewed.mjs']);
    const actualParents = git(stableRoot, 'rev-list', '--parents', '-n', '1', 'HEAD').split(' ').slice(1);
    assert.deepEqual(actualParents, [stableHead, sandboxHead]);
    assert.equal(await readFile(path.join(stableRoot, 'desktop', 'launcher', 'reviewed.mjs'), 'utf8'), fixture.deliveryBytes.toString());
    assert.deepEqual(await readFile(path.join(stableRoot, 'desktop', 'launcher', 'base.mjs')), stablePreserved);
    assert.notDeepEqual(await readFile(path.resolve(stableRoot, stableIndex)), stableIndexBytes, 'stable index must reflect the verified merged tree');
    assert.deepEqual(await readFile(path.resolve(sandboxRoot, sandboxIndex)), sandboxIndexBytes);
    assert.equal(git(sandboxRoot, 'rev-parse', 'HEAD'), sandboxHead);
    await mkdir(path.join(sandboxRoot, '.managed-runtime'), { recursive: true });
    await writeFile(path.join(sandboxRoot, '.managed-runtime', 'blocked.bin'), 'synthetic protected bytes');
    await assert.rejects(() => inspectManagedCleanSource({ stableRoot, sandboxRoot, gitExecutable }), /protected directory/i);
    const stagedPath = 'desktop/launcher/staged-refusal.mjs';
    await writeFile(path.join(sandboxRoot, ...stagedPath.split('/')), 'synthetic staged bytes\n');
    git(sandboxRoot, 'add', '--', stagedPath);
    await assert.rejects(() => inspectManagedCleanSource({ stableRoot, sandboxRoot, gitExecutable }), /staged index/i);
});
test('refuses dirty, staged, attributed, protected, non-linear, or drifted sources before merge', async (t) => {
    const fixture = await createManagedCleanSourceFixture({ name: 'refusal' });
    t.diagnostic(`retained refusal fixture=${fixture.runRoot}`);
    const { stableRoot, sandboxRoot, git } = fixture;
    let review;
    try {
        review = await inspectManagedCleanSource({ stableRoot, sandboxRoot, gitExecutable });
    }
    catch (error) {
        if (fixture.reused && /attribute|protected|untracked/i.test(error.message))
            return;
        throw error;
    }
    primaryReview = review;
    const plan = createManagedCleanSourcePlan({ review, installationId, namespaceDigest });
    const adapter = await createManagedCleanSourceAdapter({ review, gitExecutable });
    const original = await readFile(path.join(sandboxRoot, 'desktop', 'launcher', 'reviewed.mjs'));
    await writeFile(path.join(sandboxRoot, 'desktop', 'launcher', 'reviewed.mjs'), 'dirty\n');
    await assert.rejects(() => adapter.revalidate(plan), /dirty|drift|changed|clean/i);
    await writeFile(path.join(sandboxRoot, 'desktop', 'launcher', 'reviewed.mjs'), original);
    await writeFile(path.join(sandboxRoot, '.gitattributes'), 'desktop/launcher/reviewed.mjs filter=forbidden\n');
    await assert.rejects(() => inspectManagedCleanSource({ stableRoot, sandboxRoot, gitExecutable }), /attribute|filter|unsafe/i);
    await writeFile(path.join(sandboxRoot, '.gitattributes'), '');
    await mkdir(path.join(sandboxRoot, '.managed-runtime'), { recursive: true });
    await writeFile(path.join(sandboxRoot, '.managed-runtime', 'blocked.bin'), 'synthetic protected bytes');
    await assert.rejects(() => inspectManagedCleanSource({ stableRoot, sandboxRoot, gitExecutable }), /untracked|protected|unsafe|clean/i);
    assert.equal(git(stableRoot, 'rev-parse', 'HEAD'), review.stable.head);
});
test('strict review rejects accessors, hidden properties, cycles, malformed identities, and digest tampering', () => {
    const review = primaryReview;
    assert.ok(review, 'primary retained fixture ran first');
    const accessor = JSON.parse(JSON.stringify(review));
    let invoked = false;
    Object.defineProperty(accessor.stable, 'head', { enumerable: true, get() { invoked = true; return review.stable.head; } });
    assert.throws(() => validateManagedCleanSourceReview(accessor));
    assert.equal(invoked, false);
    const hidden = JSON.parse(JSON.stringify(review));
    Object.defineProperty(hidden, 'hidden', { value: true, enumerable: false });
    assert.throws(() => validateManagedCleanSourceReview(hidden));
    const cycle = JSON.parse(JSON.stringify(review));
    cycle.stable.cycle = cycle;
    assert.throws(() => validateManagedCleanSourceReview(cycle));
    const nonlinear = JSON.parse(JSON.stringify(review));
    nonlinear.history[0].parents = ['9'.repeat(40)];
    const nonlinearBody = { ...nonlinear };
    delete nonlinearBody.reviewDigest;
    nonlinear.reviewDigest = sha(canonical(nonlinearBody));
    assert.throws(() => validateManagedCleanSourceReview(nonlinear), /linear/i);
    const identity = JSON.parse(JSON.stringify(review));
    identity.stable.root = 'C:/some/other/repository';
    assert.throws(() => validateManagedCleanSourceReview(identity));
});
test('adapter requires the coordinator write-boundary token before revalidation or merge', async () => {
    const runRoot = path.resolve(process.env.MANAGED_SOURCE_ADAPTER_REFUSAL_ROOT ?? '');
    const stableRoot = path.join(runRoot, 'stable');
    const sandboxRoot = path.join(stableRoot, '.worktrees', 'sandbox');
    const gitdirLine = (await readFile(path.join(sandboxRoot, '.git'), 'utf8')).trim();
    const sandboxGitDir = path.resolve(sandboxRoot, gitdirLine.slice('gitdir: '.length));
    const base = { path: 'desktop/launcher/base.mjs', sha256: sha('base'), mode: '100644', oid: 'a'.repeat(40) };
    const delivered = { path: 'desktop/launcher/reviewed.mjs', sha256: sha('after'), mode: '100644', oid: 'b'.repeat(40) };
    const review = {
        format: 1, mode: 'managed_clean_source_review_v1',
        stable: { root: stableRoot, head: '1'.repeat(40), branch: 'main', indexDigest: sha('si'), gitDir: path.join(stableRoot, '.git'), commonDir: path.join(stableRoot, '.git'), tree: '3'.repeat(40), metadataDigest: sha('sm'), files: [base] },
        sandbox: { root: sandboxRoot, head: '2'.repeat(40), branch: 'experiment/sandbox', indexDigest: sha('bi'), gitDir: sandboxGitDir, commonDir: path.join(stableRoot, '.git'), tree: '4'.repeat(40), metadataDigest: sha('bm'), files: [base, delivered].sort((a, b) => a.path < b.path ? -1 : 1) },
        history: [{ commit: '2'.repeat(40), parents: ['1'.repeat(40)], paths: [delivered.path] }],
        deliveryPaths: [{ path: delivered.path, beforeSha256: null, afterSha256: delivered.sha256 }],
        expectedMergedTree: '4'.repeat(40), preservationDigest: sha('preservation'), blockers: [],
    };
    review.reviewDigest = sha(canonical(review));
    const validReview = validateManagedCleanSourceReview(review);
    const plan = createManagedCleanSourcePlan({ review: validReview, installationId, namespaceDigest });
    const adapter = await createManagedCleanSourceAdapter({ review: validReview, gitExecutable });
    await assert.rejects(() => adapter.merge(plan), /write context|write token/i);
    assert.equal((await readFile(path.join(stableRoot, '.git', 'HEAD'), 'utf8')).trim(), 'ref: refs/heads/main');
});
