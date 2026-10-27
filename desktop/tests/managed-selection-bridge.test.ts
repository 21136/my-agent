import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { createManagedSelectionHandler } from '../electron/managed-selection-bridge.ts';

const digest = 'a'.repeat(64);
const request = () => ({ format: 1, mode: 'managed_source_selection_request_v1', reviewDigest: digest, selectedPaths: ['desktop/a.ts'] });
const report = { review_digest: digest, mode: 'managed_source_review_v1', execution: false };
const proposal = { mode: 'managed_source_selection_proposal_v1', execution: false };
function make(overrides: Record<string, unknown> = {}) {
  const calls: string[] = [];
  const handler = createManagedSelectionHandler({
    isTrusted: (caller: { trusted: boolean }) => caller.trusted,
    isFixture: false,
    validateRequest: (value: any) => { calls.push('validate'); if (!value || Object.keys(value).length !== 4 || value.reviewDigest !== digest) throw Error('invalid token=private'); return value; },
    resolveRoots: async () => { calls.push('resolve'); return { stableRoot: 'host-stable', sandboxRoot: 'host-sandbox' }; },
    inspect: async (roots: any) => { calls.push('inspect'); assert.equal(roots.stableRoot, 'host-stable'); return report; },
    propose: (value: any) => { calls.push('propose'); assert.deepEqual(value, { review: report, request: request() }); return proposal; },
    ...overrides,
  } as any);
  return { handler, calls };
}
const trusted = () => ({ trusted: true });

test('selection trust precedes arity, request validation and every source access', async () => {
  const { handler, calls } = make();
  assert.equal((await handler({ trusted: false }, { roots: 'private' })).ok, false);
  assert.deepEqual(calls, []);
});
test('selection rejects fixtures, wrong arity, and extra renderer authority before source access', async () => {
  const fixture = make({ isFixture: true });
  assert.equal((await fixture.handler(trusted(), request())).ok, false); assert.deepEqual(fixture.calls, []);
  for (const args of [[], [request(), request()], [{ ...request(), stableRoot: 'attacker' }]]) {
    const { handler, calls } = make();
    assert.equal((await handler(trusted(), ...args)).ok, false);
    assert.ok(!calls.includes('resolve'));
  }
});
test('selection only uses fresh host report and exact validated request', async () => {
  const { handler, calls } = make();
  assert.deepEqual(await handler(trusted(), request()), { ok: true, proposal });
  assert.deepEqual(calls, ['validate', 'resolve', 'inspect', 'propose']);
});
test('stale digest never reaches planner', async () => {
  const { handler, calls } = make({ inspect: async () => ({ ...report, review_digest: 'b'.repeat(64) }) });
  const result = await handler(trusted(), request());
  assert.equal(result.ok, false); assert.match((result as any).message, /变化|重新/); assert.ok(!calls.includes('propose'));
});
test('busy gate serializes all selection source captures and releases after failure', async () => {
  let release!: () => void;
  const { handler, calls } = make({ resolveRoots: async () => { await new Promise<void>(r => { release = r; }); throw Error('host private C:/secret'); } });
  const first = handler(trusted(), request());
  assert.equal((await handler(trusted(), request())).ok, false);
  release(); const failed = await first;
  assert.equal(failed.ok, false); assert.doesNotMatch((failed as any).message, /private|secret|token/);
  const next = handler(trusted(), request()); release(); await next;
  assert.equal(calls.filter(c => c === 'validate').length, 3);
});
test('trust is rechecked after resolving and before inspecting', async () => {
  const caller = trusted(); const { handler, calls } = make({ resolveRoots: async () => { caller.trusted = false; return { stableRoot: 'x', sandboxRoot: 'y' }; } });
  assert.equal((await handler(caller, request())).ok, false); assert.ok(!calls.includes('inspect')); assert.ok(!calls.includes('propose'));
});
test('trust lost during capture prevents planner and report disclosure', async () => {
  const caller = trusted(); const { handler, calls } = make({ inspect: async () => { caller.trusted = false; return report; } });
  assert.equal((await handler(caller, request())).ok, false); assert.ok(!calls.includes('propose'));
});
test('request is detached before async capture so caller mutation cannot change scope', async () => {
  const value = request();
  const { handler } = make({ resolveRoots: async () => { value.selectedPaths.push('desktop/evil.ts'); return { stableRoot: 'x', sandboxRoot: 'y' }; }, inspect: async () => report });
  assert.deepEqual(await handler(trusted(), value), { ok: true, proposal });
});
test('planner errors are sanitized and never become a success proposal', async () => {
  const { handler } = make({ propose: () => { throw Error('C:/private/key=secret'); } });
  assert.deepEqual(await handler(trusted(), request()), { ok: false, message: '所选范围暂无法核验，请重新读取源码审查后重试。' });
});
test('production wiring uses trusted handler and selection API, never adoption executor', () => {
  const main = readFileSync(new URL('../electron/main.ts', import.meta.url), 'utf8');
  const preload = readFileSync(new URL('../electron/preload.ts', import.meta.url), 'utf8');
  const index = readFileSync(new URL('../src/shells/unified/index.ts', import.meta.url), 'utf8');
  assert.match(main, /shell-dev:managed-source-selection/);
  const block = main.slice(main.indexOf('const managedSelectionHandler'), main.indexOf('ipcMain.handle("shell-dev:sandbox-adopt-status"'));
  assert.match(block, /assertShellDevWorkbenchSender/); assert.match(block, /validateManagedSelectionRequest/); assert.match(block, /inspectManagedReview/);
  assert.doesNotMatch(block, /executeRecorded|mergeSandbox|prepareAndMerge|writeFile|execSync|spawn\(/);
  assert.match(preload, /proposeManagedSourceSelection/); assert.match(index, /mountManagedSourceSelection/);
  assert.match(index, /managedSourceSelection\.clear\(\)/); assert.match(index, /managedSourceSelection\.show\(report\)/);
});

test('bridge with production request validator rejects hostile payloads before root access', async () => {
  const { validateManagedSelectionRequest } = await import('../launcher/managed-source-selection.mjs');
  const invalid = [
    { ...request(), review: report },
    { ...request(), selectedPaths: [] },
    { ...request(), selectedPaths: ['desktop/../data/chat.json'] },
    { ...request(), selectedPaths: ['desktop/.env'] },
    { ...request(), selectedPaths: ['desktop/A.ts', 'desktop/a.ts'] },
    { ...request(), selectedPaths: Array.from({ length: 2049 }, (_, n) => `desktop/a${n}.ts`) },
  ];
  for (const value of invalid) {
    const { handler, calls } = make({ validateRequest: validateManagedSelectionRequest });
    assert.equal((await handler(trusted(), value)).ok, false);
    assert.deepEqual(calls, []);
  }
});
