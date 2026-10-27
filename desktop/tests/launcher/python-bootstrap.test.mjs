import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { test } from 'node:test';
import { fixture, bindingFor, put } from './fixtures.mjs';
import { freezeHostRelease } from '../../launcher/release.mjs';
import { bindingDigest } from '../../launcher/binding.mjs';
import { canonical } from '../../launcher/paths.mjs';
import { randomUUID } from 'node:crypto';

const sandboxRoot = path.resolve(import.meta.dirname, '../../..');
const workspaceRoot = path.resolve(sandboxRoot, '../..');
const python = path.join(workspaceRoot, '.venv', 'Scripts', 'python.exe');
const parserRoot = path.join(sandboxRoot, 'agent-core');
const skipReason = existsSync(python) ? false : `required interpreter is missing: ${python}`;
const parserCode = `import json,sys; sys.path.insert(0, ${JSON.stringify(parserRoot)}); from fixed_host import read_fixed_host_layout; print(json.dumps(read_fixed_host_layout(sys.stdin.buffer),ensure_ascii=False,sort_keys=True,separators=(',',':')))`;

async function makeFrame(t) {
  const f = fixture(t, { repositories: true });
  const release = freezeHostRelease(f.input);
  const binding = bindingFor(f, release);
  const bindingPath = put(f.root, 'control-state/binding.json', `${canonical(binding)}\n`);
  const codeRoot = path.join(release.root, 'app');
  put(f.root, 'host/.fixed-host-instance.json', `${canonical({ format: 1, mode: 'launcher_fixture_v1', code_root: codeRoot })}\n`);
  const bootstrap = {
    protocol: 'my-agent-fixed-host-v1',
    binding_path: bindingPath,
    binding_digest: bindingDigest(binding),
    release_id: release.release_id,
    instance_id: randomUUID(),
    nonce: 'a'.repeat(64),
  };
  const contextModule = await import('../../electron/fixed-host-context.ts');
  const context = contextModule.loadFixedHostContext(codeRoot, ['--fixed-host-bootstrap-stdin'], {
    readInput: () => Buffer.from(`${canonical(bootstrap)}\n`),
    executable: path.join(f.root, 'runtimes/electron/runtime.exe'),
  });
  assert.ok(context, 'fixed Host context should be created from the fixture');
  return context.sidecarLayoutFrame();
}

function parseWithIsolatedPython(frame) {
  return spawnSync(python, ['-I', '-B', '-S', '-c', parserCode], {
    input: frame,
    encoding: 'utf8',
    windowsHide: true,
  });
}

test('fixed Host sidecar layout frame round-trips through the isolated Python parser', { skip: skipReason }, async t => {
  const frame = await makeFrame(t);
  assert.equal(frame.endsWith('\n'), true, 'frame must end in one real LF');
  assert.equal(frame.endsWith('\\n'), false, 'frame terminator must not be a literal backslash-n');
  assert.equal(frame.split('\n').length, 2, 'frame must contain exactly one LF');
  assert.equal(frame, `${canonical(JSON.parse(frame))}\n`, 'Node producer must emit recursively canonical JSON');

  const parsed = parseWithIsolatedPython(frame);
  assert.equal(parsed.error, undefined, 'isolated Python parser process should run');
  assert.equal(parsed.status, 0, 'Python parser should accept the Node-generated frame');
  assert.deepEqual(JSON.parse(parsed.stdout), JSON.parse(frame));

  const payload = JSON.parse(frame);
  const duplicate = frame.replace(
    '{"code_root":',
    `{"code_root":${JSON.stringify(payload.code_root)},"code_root":`,
  );
  const noncanonical = ` ${frame}`;
  const trailingFrame = `${frame}{}`;
  for (const [label, invalidFrame] of [
    ['duplicate field', duplicate],
    ['noncanonical JSON', noncanonical],
    ['trailing frame', trailingFrame],
  ]) {
    const rejected = parseWithIsolatedPython(invalidFrame);
    assert.equal(rejected.error, undefined, `${label}: isolated Python parser process should run`);
    assert.notEqual(rejected.status, 0, `${label}: parser must reject the frame`);
  }
});

