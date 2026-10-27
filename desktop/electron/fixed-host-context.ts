import { readSync } from 'node:fs';
import path from 'node:path';
import { assertRecord, canonical, HASH, UUID, readFixtureBinding, readJson, samePath, type FixtureBinding } from '../launcher/index.mjs';
const FLAG = '--fixed-host-bootstrap-stdin';
const MAX_FRAME = 65536;
type Bootstrap = {
    protocol: 'my-agent-fixed-host-v1';
    binding_path: string;
    binding_digest: string;
    release_id: string;
    instance_id: string;
    nonce: string;
};
/** Synchronous only at explicit private-stdin startup, before Electron selects profile paths. */
function readPrivateInput(): Buffer { const buffer = Buffer.alloc(MAX_FRAME + 1); let length = 0; for (;;) {
    const count = readSync(0, buffer, length, buffer.length - length, null);
    length += count;
    if (length > MAX_FRAME)
        throw Error('fixed Host bootstrap exceeds frame bound');
    if (!count)
        return buffer.subarray(0, length);
} }
export function loadFixedHostContext(codeRoot: string, args: readonly string[], options: {
    readInput?: () => Buffer;
    executable?: string;
} = {}) {
    if (!args.includes(FLAG))
        return null;
    if (Object.keys(process.env).some(key => /^VITE_/i.test(key)))
        throw Error('fixed Host cannot start with a Vite development environment');
    if (args.filter(a => a === FLAG).length !== 1)
        throw Error('duplicate fixed Host startup flag');
    const bytes = (options.readInput ?? readPrivateInput)();
    if (!Buffer.isBuffer(bytes) || bytes.length === 0 || bytes.length > MAX_FRAME)
        throw Error('invalid fixed Host bootstrap frame');
    let raw: unknown;
    try {
        raw = JSON.parse(bytes.toString('utf8'));
    }
    catch {
        throw Error('invalid fixed Host bootstrap JSON');
    }
    const item = assertRecord(raw, ['protocol', 'binding_path', 'binding_digest', 'release_id', 'instance_id', 'nonce'], 'fixed Host bootstrap');
    if (item.protocol !== 'my-agent-fixed-host-v1' || typeof item.binding_path !== 'string' || !path.isAbsolute(item.binding_path) || typeof item.binding_digest !== 'string' || !HASH.test(item.binding_digest) || typeof item.release_id !== 'string' || !HASH.test(item.release_id) || typeof item.instance_id !== 'string' || !UUID.test(item.instance_id) || typeof item.nonce !== 'string' || !HASH.test(item.nonce) || bytes.toString('utf8') !== canonical(item) + '\n')
        throw Error('invalid/noncanonical fixed Host bootstrap identity');
    const frame = item as Bootstrap;
    const verified = readFixtureBinding(frame.binding_path), binding = verified.binding;
    if (verified.digest !== frame.binding_digest || binding.release.release_id !== frame.release_id || !samePath(codeRoot, path.join(binding.release.root, 'app')))
        throw Error('fixed Host binding/release/code root mismatch');
    const executable = options.executable ?? process.execPath;
    if (!samePath(executable, path.join(verified.manifest.runtimes.electron.root, verified.manifest.runtimes.electron.entry)))
        throw Error('fixed Host executable differs from fixed Electron distribution');
    const marker = readJson(path.join(binding.instance_root, '.fixed-host-instance.json'));
    assertRecord(marker, ['format', 'mode', 'code_root'], 'fixed Host instance marker');
    const expected = { format: 1, mode: 'launcher_fixture_v1', code_root: path.resolve(codeRoot) };
    if (canonical(marker) !== canonical(expected))
        throw Error('fixed Host instance layout marker mismatch');
    function revalidate(): void { if (Object.keys(process.env).some(key => /^VITE_/i.test(key)))
        throw Error('fixed Host development environment changed'); const current = readFixtureBinding(frame.binding_path); if (current.digest !== frame.binding_digest || current.binding.binding_revision !== binding.binding_revision)
        throw Error('fixed Host binding changed; renewed confirmation required'); const next = readJson(path.join(binding.instance_root, '.fixed-host-instance.json')); if (canonical(next) !== canonical(expected))
        throw Error('fixed Host instance layout changed'); }
    return { binding, bindingDigest: verified.digest, manifest: verified.manifest, codeRoot: path.resolve(codeRoot), dataRoot: path.join(binding.instance_root, 'data'), workspaceRoot: path.join(binding.instance_root, 'workspace'), profileRoot: path.join(binding.instance_root, 'profile'), revalidate,
        readyFrame: () => ({ type: 'fixed-host.ready', installation_id: binding.installation_id, release_id: frame.release_id, instance_id: frame.instance_id, nonce: frame.nonce }),
        sidecarLayoutFrame: () => canonical({ protocol: 'my-agent-fixed-layout-v1', code_root: path.resolve(codeRoot), instance_root: binding.instance_root, runtime:{python_executable:path.join(verified.manifest.runtimes.python.root,verified.manifest.runtimes.python.entry),python_root:verified.manifest.runtimes.python.root,python_base_root:verified.manifest.runtimes.python_base.root} }) + '\n' };
}
export type FixedHostContext = NonNullable<ReturnType<typeof loadFixedHostContext>>;
