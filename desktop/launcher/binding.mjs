import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { assertFixtureRun, assertRecord, canonical, inside, readJson, realDirectory, samePath, sha, UUID } from './paths.mjs';
import { verifyHostRelease } from './release.mjs';
const FIELDS = ['format', 'mode', 'installation_id', 'binding_revision', 'run_root', 'launcher_root', 'state_root', 'instance_root', 'stable_source_root', 'sandbox_source_root', 'git_common_dir', 'stable_branch', 'sandbox_branch', 'release'];
export function cleanHostEnvironment(env = process.env) { const result = {}; for (const [key, value] of Object.entries(env))
    if (/^(?:SYSTEMROOT|WINDIR|COMSPEC|TEMP|TMP|PATH|PATHEXT|USERPROFILE|LOCALAPPDATA|APPDATA|LANG|LC_ALL)$/i.test(key) && typeof value === 'string')
        result[key] = value; return { ...result, PYTHONDONTWRITEBYTECODE: '1', PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', PYTHONNOUSERSITE: '1' }; }
function git(cwd, ...args) { return execFileSync('git', ['--no-optional-locks', ...args], { cwd, env: { ...cleanHostEnvironment(), GIT_TERMINAL_PROMPT: '0' }, windowsHide: true, encoding: 'utf8', timeout: 15000, maxBuffer: 1024 * 1024, stdio: ['ignore', 'pipe', 'pipe'] }).trim(); }
export function bindingDigest(binding) { return sha(canonical(binding)); }
export function validateFixtureBinding(binding) {
    assertRecord(binding, FIELDS, 'fixture binding');
    if (binding.format !== 1 || binding.mode !== 'launcher_fixture_v1' || !UUID.test(binding.installation_id) || !Number.isSafeInteger(binding.binding_revision) || binding.binding_revision < 1)
        throw Error('fixture binding identity invalid');
    const run = assertFixtureRun(binding.run_root), marker = readJson(path.join(run, '.fixed-control-fixture.json'));
    if (marker.run_id !== binding.installation_id)
        throw Error('fixture installation mismatch');
    for (const [key, name] of Object.entries({ launcher_root: 'launcher', state_root: 'control-state', instance_root: 'host', stable_source_root: 'source-stable', sandbox_source_root: 'source-sandbox' })) {
        const actual = realDirectory(binding[key], key);
        if (!samePath(actual, path.join(run, name)))
            throw Error('fixture ' + key + ' must be its registered isolated root');
    }
    for (const part of ['data', 'workspace', 'profile'])
        realDirectory(path.join(binding.instance_root, part), 'isolated Host ' + part);
    if (binding.sandbox_branch !== 'experiment/sandbox' || typeof binding.stable_branch !== 'string' || !/^[-A-Za-z0-9_./]+$/.test(binding.stable_branch) || binding.stable_branch === binding.sandbox_branch)
        throw Error('fixture branches invalid');
    const common = realDirectory(binding.git_common_dir, 'Git common directory');
    if (!samePath(common, path.join(binding.stable_source_root, '.git')) || !inside(run, common))
        throw Error('Git common-dir must belong to this fixture, not a real repository');
    for (const [key, branch] of [['stable_source_root', binding.stable_branch], ['sandbox_source_root', binding.sandbox_branch]]) {
        const source = binding[key];
        if (!samePath(realDirectory(git(source, 'rev-parse', '--show-toplevel'), 'Git worktree top'), source) || !samePath(realDirectory(git(source, 'rev-parse', '--path-format=absolute', '--git-common-dir'), 'Git common-dir'), common) || git(source, 'symbolic-ref', '--short', 'HEAD') !== branch)
            throw Error('fixture Git worktree/common-dir/branch mismatch');
    }
    const manifest = verifyHostRelease(binding.release);
    if (!samePath(binding.release.root, path.join(run, 'releases', binding.release.release_id)))
        throw Error('release root escaped fixture');
    return { binding, digest: bindingDigest(binding), manifest };
}
export function readFixtureBinding(file) { const binding = readJson(file); const verified = validateFixtureBinding(binding); if (!samePath(file, path.join(binding.state_root, 'binding.json')))
    throw Error('binding file is not its registered controller state path'); return verified; }
