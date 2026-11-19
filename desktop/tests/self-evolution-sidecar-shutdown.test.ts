import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { spawn, type ChildProcess } from 'node:child_process';
import test from 'node:test';

let stopSidecarGracefully: ((child: ChildProcess, graceMs: number, killWaitMs?: number) => Promise<unknown>) | undefined;
try {
  ({ stopSidecarGracefully } = await import('../electron/self-evolution-sidecar-shutdown.ts'));
} catch {
  // RED: helper may not exist yet.
}

function fakeChild(killResult = false): ChildProcess & { ended: boolean; killCalls: number } {
  const child = new EventEmitter() as unknown as ChildProcess & { ended: boolean; killCalls: number };
  Object.assign(child, {
    pid: 123, exitCode: null, signalCode: null, killed: false,
    stdin: { destroyed: false, writable: true, end() { child.ended = true; } },
    ended: false, killCalls: 0,
    kill() { child.killCalls++; return killResult; },
  });
  return child;
}

test('auto-evolution sidecar shutdown ends stdin and awaits child close', async () => {
  assert.equal(typeof stopSidecarGracefully, 'function');
  const child = spawn(process.execPath, ['-e', "process.stdin.once('end', () => setTimeout(() => process.exit(0), 100)); process.stdin.resume(); setInterval(() => {}, 1000);"], { stdio: ['pipe', 'ignore', 'ignore'] });
  const startedAt = Date.now();
  const outcome = await stopSidecarGracefully!(child, 1000, 100);
  assert.deepEqual(outcome, { status: 'exited', forced: false });
  assert.ok(Date.now() - startedAt >= 75, 'shutdown must wait for EOF handling and actual process close');
  assert.ok(child.exitCode !== null || child.signalCode !== null, 'process exit event must be observed');
});

test('sidecar grace expiry force-stops only its own child and confirms close', async () => {
  assert.equal(typeof stopSidecarGracefully, 'function');
  const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000);'], { stdio: ['pipe', 'ignore', 'ignore'] });
  const outcome = await stopSidecarGracefully!(child, 40, 500);
  assert.deepEqual(outcome, { status: 'exited', forced: true });
  assert.ok(child.exitCode !== null || child.signalCode !== null, 'a kill request is not process-exit evidence');
});

test('failed kill and missing terminal events resolve as unknown after the second deadline', async () => {
  assert.equal(typeof stopSidecarGracefully, 'function');
  const child = fakeChild(false);
  const startedAt = Date.now();
  const outcome = await Promise.race([
    stopSidecarGracefully!(child, 35, 45),
    new Promise(resolve => setTimeout(() => resolve('not-settled'), 180)),
  ]);
  assert.deepEqual(outcome, { status: 'unknown', forced: true });
  assert.equal(child.ended, true, 'stdin EOF remains the first shutdown request');
  assert.equal(child.killCalls, 1, 'only the supplied child may be force-stopped');
  assert.ok(Date.now() - startedAt >= 70 && Date.now() - startedAt < 500, 'both shutdown phases must be bounded');
  child.emit('close', null, null);
  child.exitCode = 0;
  assert.deepEqual(await stopSidecarGracefully!(child, 35, 45), {status:'unknown',forced:true}, 'a later state read must not rewrite an earlier unknown into confirmed success');
});

test('spawn failure is resolved by close without waiting for a nonexistent exit event', async () => {
  assert.equal(typeof stopSidecarGracefully, 'function');
  const child = spawn(`${process.execPath}.no-such-executable`, [], { stdio: ['pipe', 'ignore', 'ignore'] });
  const outcome = await Promise.race([
    stopSidecarGracefully!(child, 500, 100),
    new Promise(resolve => setTimeout(() => resolve('not-settled'), 700)),
  ]);
  assert.deepEqual(outcome, { status: 'spawn-failed' });
});
