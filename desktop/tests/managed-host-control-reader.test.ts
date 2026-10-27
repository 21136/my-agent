import assert from "node:assert/strict";
import test from "node:test";
async function loadReader() { return import("../electron/managed-host-control-reader.ts").catch(() => ({})); }

test("managed control line reader preserves coalesced and fragmented lifecycle frames within bounds", async () => {
  const reader = await loadReader();
  assert.equal(typeof reader.ManagedControlLineBuffer, "function");
  const buffer = new reader.ManagedControlLineBuffer();
  const accept = '{"type":"accept_reopen"}';
  const shutdown = '{"type":"shutdown"}';
  assert.deepEqual(buffer.push(Buffer.from(`${accept}\n${shutdown.slice(0, 9)}`)), [accept]);
  assert.ok(buffer.bufferedBytes <= reader.MAX_CONTROL_FRAME_BYTES);
  assert.deepEqual(buffer.push(Buffer.from(`${shutdown.slice(9)}\n`)), [shutdown]);
  assert.equal(buffer.bufferedBytes, 0);
  buffer.push(Buffer.from('{"type":"incomplete"'));
  buffer.finish();
  assert.equal(buffer.bufferedBytes, 0);
});

test("managed control line reader discards an oversized line without losing the next bounded frame", async () => {
  const reader = await loadReader();
  assert.equal(typeof reader.ManagedControlLineBuffer, "function");
  const buffer = new reader.ManagedControlLineBuffer();
  const next = '{"type":"shutdown"}';
  assert.deepEqual(buffer.push(Buffer.alloc(reader.MAX_CONTROL_FRAME_BYTES + 32, 120)), []);
  assert.equal(buffer.bufferedBytes, 0);
  assert.deepEqual(buffer.push(Buffer.from(`\n${next}\n`)), [next]);
});

test("managed control line reader bounds queued coalesced lines", async () => {
  const reader = await loadReader();
  assert.equal(typeof reader.ManagedControlLineBuffer, "function");
  const buffer = new reader.ManagedControlLineBuffer();
  const frames = Array.from({ length: reader.MAX_PENDING_CONTROL_FRAMES + 1 }, (_, index) => `{"probe":${index}}`).join("\n") + "\n";
  assert.throws(() => buffer.push(Buffer.from(frames)), /queue overflow/);
});
