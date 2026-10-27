import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync, writeFileSync, symlinkSync, existsSync } from 'node:fs';
import path from 'node:path';
import { fixture, put, bindingFor, git } from './fixtures.mjs';
let release = {}, binding = {};
try {
    release = await import('../../launcher/release.mjs');
    binding = await import('../../launcher/binding.mjs');
}
catch (e) {
    if (e.code !== 'ERR_MODULE_NOT_FOUND')
        throw e;
}
function freeze(t, options = {}) { assert.equal(typeof release.freezeHostRelease, 'function', 'fixed release API missing'); const f = fixture(t, options); return { ...f, release: release.freezeHostRelease(f.input) }; }
test('fixed release API exists', () => assert.equal(typeof release.freezeHostRelease, 'function', 'fixed release API missing'));
test('published bytes are independent of mutable source; repeat publication never overwrites', t => { const f = freeze(t); put(f.root, 'payload/desktop/dist-electron/main.js', 'changed source'); assert.doesNotThrow(() => release.verifyHostRelease(f.release)); assert.throws(() => release.freezeHostRelease({ ...f.input, sourceRoot: path.join(f.release.root, 'app') }), /existing|exists|overwrite/); });
test('tampered or additional release bytes fail verification', t => { const f = freeze(t); put(f.release.root, 'app/desktop/dist-electron/main.js', 'tampered'); assert.throws(() => release.verifyHostRelease(f.release), /digest|integrity|length/); });
test('unmanifested files including empty extra directories fail closed', t => { const f = freeze(t); put(f.release.root, 'app/extra.js', 'extra'); assert.throws(() => release.verifyHostRelease(f.release), /manifest|inventory/); });
test('runtime distribution drift invalidates fixed release', t => { const f = freeze(t); put(f.root, 'runtimes/electron/extra.dll', 'drift'); assert.throws(() => release.verifyHostRelease(f.release), /runtime|inventory/); });
test('manifest identity cannot be changed independently of descriptor', t => { const f = freeze(t); const p = path.join(f.release.root, 'manifest.json'); const v = JSON.parse(readFileSync(p)); v.provenance.source_snapshot = 'other'; writeFileSync(p, JSON.stringify(v)); assert.throws(() => release.verifyHostRelease(f.release), /identity|digest/); });
test('unsafe, private and Windows alias input paths are refused', t => { const f = fixture(t); assert.equal(typeof release.freezeHostRelease, 'function'); for (const files of [['../outside.py'], ['data/state.json'], ['.env'], ['desktop/A.js', 'desktop/a.js'], ['desktop/name.'], ['desktop/CON.txt']])
    assert.throws(() => release.freezeHostRelease({ ...f.input, files }), /path|private|alias|reserved|source/); });
test('source links are refused rather than copied', t => { const f = fixture(t); const target = put(f.root, 'outside/file.js', 'outside'); try {
    symlinkSync(path.dirname(target), path.join(f.root, 'payload', 'linked'), 'junction');
}
catch (e) {
    if (['EPERM', 'EISDIR', 'ENOTSUP'].includes(e.code)) {
        t.skip('junction creation unsupported by this sandbox volume: ' + e.code);
        return;
    }
    throw e;
} assert.equal(typeof release.freezeHostRelease, 'function'); assert.throws(() => release.freezeHostRelease({ ...f.input, files: ['linked/file.js'] }), /link|canonical/); });
test('capacity rejection precedes copying or publication', t => { const f = fixture(t); assert.equal(typeof release.freezeHostRelease, 'function'); assert.throws(() => release.freezeHostRelease(f.input, { availableBytes: () => 0 }), /space|capacity/); assert.equal(existsSync(path.join(f.root, 'releases', 'manifest.json')), false); });
test('fixture binding validates two isolated Git worktrees without writing them', t => { const f = freeze(t, { repositories: true }); assert.equal(typeof binding.validateFixtureBinding, 'function'); const b = bindingFor(f, f.release), before = git(b.stable_source_root, 'rev-parse', 'HEAD'); const v = binding.validateFixtureBinding(b); assert.match(v.digest, /^[a-f0-9]{64}$/); assert.equal(git(b.stable_source_root, 'rev-parse', 'HEAD'), before); assert.equal(git(b.stable_source_root, 'status', '--porcelain'), ''); });
test('real repository binding, wrong branch, extra authority and overlapping state are refused', t => { const f = freeze(t, { repositories: true }); assert.equal(typeof binding.validateFixtureBinding, 'function'); const b = bindingFor(f, f.release); for (const bad of [{ ...b, stable_source_root: path.resolve(f.root, '../../../..') }, { ...b, stable_branch: 'other' }, { ...b, allowRealRepository: true }, { ...b, state_root: b.stable_source_root }])
    assert.throws(() => binding.validateFixtureBinding(bad)); });
test('a linked worktree with a common-dir outside fixture run cannot be admitted', t => { const f = freeze(t, { repositories: true }); const b = bindingFor(f, f.release); assert.equal(typeof binding.validateFixtureBinding, 'function'); assert.throws(() => binding.validateFixtureBinding({ ...b, git_common_dir: path.resolve(f.root, '../../../../../.git') }), /common|fixture|root|canonical/); });
test('lexical dot-segment absolute roots are not admitted as canonical fixture paths', t => { const f = freeze(t, { repositories: true }); const b = bindingFor(f, f.release); assert.throws(() => binding.validateFixtureBinding({ ...b, state_root: path.join(f.root, 'unused') + path.sep + '..' + path.sep + 'control-state' }), /canonical|alias/); });
test('a single-file runtime cannot masquerade as the complete Electron/Python/Node environment', t => { const f = fixture(t); for (const name of ['node', 'electron', 'python'])
    assert.throws(() => release.freezeHostRelease({ ...f.input, runtimeInputs: { ...f.input.runtimeInputs, [name]: { ...f.input.runtimeInputs[name], scope: 'file' } } }), /tree|runtime|complete/); });

test('a venv-only snapshot cannot omit the Python base standard library',t=>{const f=fixture(t);const runtimeInputs={...f.input.runtimeInputs};delete runtimeInputs.python_base;assert.throws(()=>release.freezeHostRelease({...f.input,runtimeInputs}),/runtime|fields/);});

test('publishing retries a transient Windows directory lock without overwriting another release',async t=>{
 const paths=await import('../../launcher/paths.mjs');assert.equal(typeof paths.publishNewDirectory,'function','bounded atomic directory publication missing');
 const {mkdirSync,renameSync}=await import('node:fs');const f=fixture(t),stage=path.join(f.root,'releases/.stage-transient'),target=path.join(f.root,'releases','f'.repeat(64));mkdirSync(stage);put(stage,'manifest.json','{}');let attempts=0;
 paths.publishNewDirectory(stage,target,{rename(a,b){attempts++;if(attempts<3)throw Object.assign(Error('temporary scanner lock'),{code:'EPERM'});renameSync(a,b);},wait(){}});assert.equal(attempts,3);assert.equal(existsSync(path.join(target,'manifest.json')),true);
 const other=path.join(f.root,'releases/.stage-other');mkdirSync(other);assert.throws(()=>paths.publishNewDirectory(other,target),/existing|overwrite/);
});
