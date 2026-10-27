import assert from "node:assert/strict";
import test from "node:test";
import { isManagedRendererRequestAllowed, managedHostStartupFailure, parseManagedHostArguments, parseManagedHostBootstrap } from "../electron/managed-host-context.ts";
import { managedReadonlyHealthMatches, managedReadonlyReadyMatches, managedReadonlyStopMatches } from "../electron/managed-host-context.ts";

test("managed host accepts only its dedicated app path and marker", () => {
  assert.deepEqual(parseManagedHostArguments(["C:/sealed/desktop", "--managed-host-v1"], "C:/sealed/desktop"), {
    mode: "managed",
  });
  assert.throws(() => parseManagedHostArguments(["C:/other/desktop", "--managed-host-v1"], "C:/sealed/desktop"), /arguments rejected/);
  assert.throws(() => parseManagedHostArguments(["C:/sealed/desktop", "--managed-host-v1", "--profile=C:/outside"], "C:/sealed/desktop"), /arguments rejected/);
  assert.throws(() => parseManagedHostArguments(["C:/sealed/desktop"], "C:/sealed/desktop"), /arguments rejected/);
});

test("startup failure reports only a fixed stage and a fixed error code", () => {
  for (const stage of ["bootstrap", "context", "readonly", "application", "renderer"] as const) {
    assert.deepEqual(managedHostStartupFailure(stage), { ready: false, error: "managed_electron_startup_failed", stage });
  }
  assert.deepEqual(Object.keys(managedHostStartupFailure("context")).sort(), ["error", "ready", "stage"]);
});

test("managed host bootstrap is one bounded canonical frame with fixed identities", () => {
  const body = {
    access: "read_only",
    protocol: "my-agent-managed-electron-v1",
    namespace_root: "F:\\sandbox\\.managed-runtime\\11111111-1111-4111-8111-111111111111",
    stable_root: "F:\\stable",
    sandbox_root: "F:\\sandbox",
    operation_id: "33333333-3333-4333-8333-333333333333",
    profile_policy: "managed_host_chromium_outputs_v1",
    release_id: "d".repeat(64),
    binding_digest: "a".repeat(64),
    runtime_digest: "b".repeat(64),
    nonce: "22222222-2222-4222-8222-222222222222",
    control_token: "c".repeat(64),
  };
  const canonical = `${JSON.stringify(body, Object.keys(body).sort())}\n`;
  assert.deepEqual(parseManagedHostBootstrap(Buffer.from(canonical)), body);
  assert.throws(() => parseManagedHostBootstrap(Buffer.from(`${canonical}extra`)), /frame rejected/);
  assert.throws(() => parseManagedHostBootstrap(Buffer.from(canonical.replace('"control_token"', '"profile_root":"F:/outside","control_token"'))), /fields rejected/);
  assert.throws(() => parseManagedHostBootstrap(Buffer.from(canonical.replace("managed_host_chromium_outputs_v1", "arbitrary_profile"))), /identity rejected/);
});

test("managed renderer permits only sealed file assets and the owned loopback socket", () => {
  const root = "F:\\sealed\\desktop\\dist";
  assert.equal(isManagedRendererRequestAllowed("file:///F:/sealed/desktop/dist/index.html", root, 43123), true);
  assert.equal(isManagedRendererRequestAllowed("file:///F:/sealed/desktop/dist/assets/app.js", root, 43123), true);
  assert.equal(isManagedRendererRequestAllowed("file:///F:/sealed/desktop/other.js", root, 43123), false);
  assert.equal(isManagedRendererRequestAllowed("https://example.test/app.js", root, 43123), false);
  assert.equal(isManagedRendererRequestAllowed("ws://127.0.0.1:43123/", root, 43123), true);
  assert.equal(isManagedRendererRequestAllowed("ws://127.0.0.1:43124/", root, 43123), false);
  assert.equal(isManagedRendererRequestAllowed("http://127.0.0.1:43123/", root, 43123), false);
});

test("readonly handshake and stop receipt must preserve the full store and Host identity", () => {
  const identity = {
    nonce: "22222222-2222-4222-8222-222222222222",
    host_nonce: "33333333-3333-4333-8333-333333333333",
    profile_intent_digest: "a".repeat(64),
    installation_id: "11111111-1111-4111-8111-111111111111",
    data_store_id: "44444444-4444-4444-8444-444444444444",
    release_id: "b".repeat(64),
    binding_digest: "c".repeat(64),
    runtime_digest: "d".repeat(64),
  };
  const ready = { ready: true, mode: "managed_host_readonly", access: "read_only", host: "127.0.0.1", port: 43210,
    ...identity, generation: 3, revision: 9, lifecycle: "clean_shutdown", last_known_good: null };
  assert.equal(managedReadonlyReadyMatches(ready, identity), true);
  assert.equal(managedReadonlyReadyMatches({ ...ready, data_store_id: "55555555-5555-4555-8555-555555555555" }, identity), false);
  const health = { type: "managed.health", ready: true, access: "read_only", ...identity,
    generation: ready.generation, revision: ready.revision, lifecycle: ready.lifecycle, last_known_good: ready.last_known_good };
  assert.equal(managedReadonlyHealthMatches(health, ready), true);
  assert.equal(managedReadonlyHealthMatches({ ...health, host_nonce: "66666666-6666-4666-8666-666666666666" }, ready), false);
  const stopped = { type: "managed.host-readonly.stopped", clean: true, ...identity,
    generation: ready.generation, revision: ready.revision, lifecycle: ready.lifecycle, last_known_good: ready.last_known_good };
  assert.equal(managedReadonlyStopMatches(stopped, ready), true);
  assert.equal(managedReadonlyStopMatches({ ...stopped, revision: 10 }, ready), false);
  assert.equal(managedReadonlyStopMatches({ ...stopped, profile_intent_digest: "e".repeat(64) }, ready), false);
});
