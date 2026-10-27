import assert from 'node:assert/strict';
import { test } from 'node:test';
import { writeFileSync } from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { fixture, bindingFor, put } from './launcher/fixtures.mjs';
import { freezeHostRelease } from '../launcher/release.mjs';
import { bindingDigest } from '../launcher/binding.mjs';
import { canonical } from '../launcher/paths.mjs';
let context: Record<string, any> = {};
try {
    context = await import('../electron/fixed-host-context.ts');
}
catch (e: any) {
    if (e.code !== 'ERR_MODULE_NOT_FOUND')
        throw e;
}
function setup(t: any) { const f = fixture(t, { repositories: true }), release = freezeHostRelease(f.input), binding = bindingFor(f, release); const bindingPath = put(f.root, 'control-state/binding.json', canonical(binding) + '\n'); put(f.root, 'host/.fixed-host-instance.json', canonical({ format: 1, mode: 'launcher_fixture_v1', code_root: path.join(release.root, 'app') }) + '\n'); const frame = { protocol: 'my-agent-fixed-host-v1', binding_path: bindingPath, binding_digest: bindingDigest(binding), release_id: release.release_id, instance_id: randomUUID(), nonce: 'a'.repeat(64) }; return { f, release, binding, bindingPath, frame, codeRoot: path.join(release.root, 'app'), options: { readInput: () => Buffer.from(canonical(frame) + '\n'), executable: path.join(f.root, 'runtimes/electron/runtime.exe') } }; }
test('fixed Host context API is present', () => assert.equal(typeof context.loadFixedHostContext, 'function', 'fixed Host context missing'));
test('ordinary startup does not read stdin and is not a fixed identity', () => { assert.equal(typeof context.loadFixedHostContext, 'function'); assert.equal(context.loadFixedHostContext('not-used', [], { readInput: () => { throw Error('must not read'); } }), null); });
test('private bootstrap binds code root, instance and separate source targets', t => { const f = setup(t); assert.equal(typeof context.loadFixedHostContext, 'function'); const c = context.loadFixedHostContext(f.codeRoot, ['--fixed-host-bootstrap-stdin'], f.options); assert.equal(c.binding.stable_source_root, f.binding.stable_source_root); assert.equal(c.dataRoot, path.join(f.f.root, 'host/data')); assert.notEqual(c.dataRoot, path.join(f.codeRoot, 'data')); assert.doesNotThrow(() => c.revalidate()); assert.equal(c.readyFrame().nonce, f.frame.nonce); assert.equal(c.sidecarLayoutFrame(), canonical({ protocol: 'my-agent-fixed-layout-v1', code_root: f.codeRoot, instance_root: f.binding.instance_root, runtime:{python_executable:path.join(f.f.root,'runtimes/python/runtime.exe'),python_root:path.join(f.f.root,'runtimes/python'),python_base_root:path.join(f.f.root,'runtimes/python_base')} }) + '\n'); });
test('wrong executable, code root, digest, extra frame or extra authority rejects bootstrap', t => { const f = setup(t); assert.equal(typeof context.loadFixedHostContext, 'function'); const call = (root = f.codeRoot, frame = f.frame, opts = f.options) => context.loadFixedHostContext(root, ['--fixed-host-bootstrap-stdin'], { ...opts, readInput: () => Buffer.from(canonical(frame) + '\n') }); assert.throws(() => call(f.binding.stable_source_root)); assert.throws(() => call(f.codeRoot, { ...f.frame, binding_digest: '0'.repeat(64) })); assert.throws(() => call(f.codeRoot, { ...f.frame, allowReal: true })); assert.throws(() => call(f.codeRoot, f.frame, { ...f.options, executable: path.join(f.f.root, 'runtimes/node/runtime.exe') })); assert.throws(() => context.loadFixedHostContext(f.codeRoot, ['--fixed-host-bootstrap-stdin'], { ...f.options, readInput: () => Buffer.from(canonical(f.frame) + '\n{}\n') })); });
test('binding revision drift invalidates an existing Host before the next write', t => { const f = setup(t); assert.equal(typeof context.loadFixedHostContext, 'function'); const c = context.loadFixedHostContext(f.codeRoot, ['--fixed-host-bootstrap-stdin'], f.options); writeFileSync(f.bindingPath, canonical({ ...f.binding, binding_revision: 2 }) + '\n'); assert.throws(() => c.revalidate(), /binding|changed|revision/); });
test('fixed Host rejects Vite environment even when bootstrap and roots are otherwise valid', t => { const f = setup(t), previous = process.env.VITE_DEV_SERVER_URL; process.env.VITE_DEV_SERVER_URL = 'http://127.0.0.1:5174'; try {
    assert.throws(() => context.loadFixedHostContext(f.codeRoot, ['--fixed-host-bootstrap-stdin'], f.options), /development|Vite|fixed/i);
}
finally {
    if (previous === undefined)
        delete process.env.VITE_DEV_SERVER_URL;
    else
        process.env.VITE_DEV_SERVER_URL = previous;
} });
