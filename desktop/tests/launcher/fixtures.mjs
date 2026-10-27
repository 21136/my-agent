import { mkdirSync, mkdtempSync, writeFileSync, rmSync, realpathSync, lstatSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import assert from 'node:assert/strict';
const base = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../artifacts/fixed-control-e0');
export function put(root, rel, bytes) { const p = path.join(root, rel); mkdirSync(path.dirname(p), { recursive: true }); writeFileSync(p, bytes); return p; }
export function git(root, ...args) { const env = { ...process.env }; for (const k of Object.keys(env))
    if (/^GIT_/i.test(k))
        delete env[k]; return execFileSync('git', args, { cwd: root, env, encoding: 'utf8', windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] }).trim(); }
export function fixture(t, { repositories = false } = {}) {
    mkdirSync(base, { recursive: true });
    const root = mkdtempSync(path.join(base, 'run-'));
    t.after(() => { const rel = path.relative(realpathSync(base), realpathSync(root)); assert.ok(rel && !rel.startsWith('..') && !path.isAbsolute(rel)); assert.equal(lstatSync(root).isSymbolicLink(), false); rmSync(root, { recursive: true,maxRetries:5,retryDelay:50 }); });
    const run_id = randomUUID();
    put(root, '.fixed-control-fixture.json', JSON.stringify({ format: 1, owner: 'my-agent-fixed-control-e0', run_id }));
    for (const dir of ['payload', 'releases', 'launcher', 'control-state', 'host/data', 'host/workspace', 'host/profile', 'runtimes/node', 'runtimes/electron', 'runtimes/python', 'runtimes/python_base'])
        mkdirSync(path.join(root, dir), { recursive: true });
    put(root, 'payload/desktop/package.json', JSON.stringify({ name: 'fixture-host', main: 'dist-electron/main.js' }));
    put(root, 'payload/desktop/dist-electron/main.js', '// sealed main');
    put(root, 'payload/desktop/dist-electron/preload.js', '// sealed preload');
    put(root, 'payload/desktop/dist/index.html', '<title>sealed</title>');
    put(root, 'payload/agent-core/server.py', '# fixed');
    put(root,'payload/agent-core/fixed_host_entry.py','# fixed cold entry');
    put(root, 'payload/evolve/_index.core.toml', '');
    for (const name of ['node', 'electron', 'python', 'python_base'])
        put(root, 'runtimes/' + name + '/runtime.exe', name + ' fixture bytes (not executable)');
    const runtimeInputs = Object.fromEntries(['node', 'electron', 'python', 'python_base'].map(name => [name, { root: path.join(root, 'runtimes', name), entry: 'runtime.exe', scope: 'tree' }]));
    const input = { runRoot: root, sourceRoot: path.join(root, 'payload'), files: ['desktop/package.json', 'desktop/dist-electron/main.js', 'desktop/dist-electron/preload.js', 'desktop/dist/index.html', 'agent-core/server.py', 'agent-core/fixed_host_entry.py', 'evolve/_index.core.toml'], runtimeInputs, provenance: { kind: 'fixture', source_snapshot: 'test-input-v1' } };
    if (repositories) {
        const stable = path.join(root, 'source-stable'), sandbox = path.join(root, 'source-sandbox');
        mkdirSync(stable);
        git(stable, 'init', '-b', 'stable');
        git(stable, 'config', 'user.name', 'Fixed Host Fixture');
        git(stable, 'config', 'user.email', 'fixture@example.invalid');
        git(stable, 'config', 'commit.gpgsign', 'false');
        git(stable, 'config', 'core.autocrlf', 'false');
        git(stable, 'config', 'core.hooksPath', path.join(root, 'no-hooks'));
        put(stable, 'desktop/package.json', '{}\n');
        put(stable, 'desktop/file.txt', 'base\n');
        put(stable, 'agent-core/server.py', '# base\n');
        put(stable, '.gitignore', 'data/\n');
        git(stable, 'add', '.');
        git(stable, 'commit', '-m', 'fixture base');
        git(stable, 'worktree', 'add', '-b', 'experiment/sandbox', sandbox);
    }
    return { root, run_id, input };
}
export function bindingFor(f, release) { return { format: 1, mode: 'launcher_fixture_v1', installation_id: f.run_id, binding_revision: 1, run_root: f.root, launcher_root: path.join(f.root, 'launcher'), state_root: path.join(f.root, 'control-state'), instance_root: path.join(f.root, 'host'), stable_source_root: path.join(f.root, 'source-stable'), sandbox_source_root: path.join(f.root, 'source-sandbox'), git_common_dir: path.join(f.root, 'source-stable', '.git'), stable_branch: 'stable', sandbox_branch: 'experiment/sandbox', release }; }

