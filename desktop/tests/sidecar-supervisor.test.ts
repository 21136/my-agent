import assert from "node:assert/strict";
import test from "node:test";
import { SidecarSupervisor } from "../electron/sidecar-supervisor.ts";

test("sidecar restart tracks stopping, starting, and ready lifecycle", async () => {
  const events: string[] = [];
  const supervisor = new SidecarSupervisor();
  const result = await supervisor.restart({
    stop: () => events.push("stop"),
    start: async () => {
      events.push("start");
      return { ok: true, host: "127.0.0.1", port: 8765 };
    },
  });
  assert.equal(result.ok, true);
  assert.equal(result.state, "ready");
  assert.deepEqual(events, ["stop", "start"]);
  assert.equal(supervisor.state, "ready");
});

test("candidate failure recovers old sidecar without promoting", async () => {
  const events: string[] = [];
  const supervisor = new SidecarSupervisor();
  const result = await supervisor.switchCandidate({
    startCandidate: async () => {
      events.push("candidate:start");
      return { ok: true, host: "127.0.0.1", port: 9876 };
    },
    healthCandidate: async () => {
      events.push("candidate:health");
      return false;
    },
    stopCandidate: () => events.push("candidate:stop"),
    stopCurrent: () => events.push("current:stop"),
    promoteCandidate: async () => {
      events.push("candidate:promote");
      return { ok: true, host: "127.0.0.1", port: 8765 };
    },
    recoverCurrent: async () => {
      events.push("current:recover");
      return { ok: true, host: "127.0.0.1", port: 8765 };
    },
  });
  assert.equal(result.ok, false);
  assert.equal(result.state, "rolled_back");
  assert.equal(result.recovered, true);
  assert.deepEqual(events, ["candidate:start", "candidate:health", "candidate:stop", "current:recover"]);
});
