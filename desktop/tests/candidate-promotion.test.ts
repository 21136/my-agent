import assert from "node:assert/strict";
import test from "node:test";
import { promoteCandidateWithRestart } from "../electron/candidate-promotion.ts";

test("candidate promotion stops the probe before starting the promoted process", async () => {
  const events: string[] = [];
  const result = await promoteCandidateWithRestart({
    candidateRoot: "F:/my-agent-main/data/self-hosting/ui-candidate",
    candidate: { proc: { pid: 123 }, info: { host: "127.0.0.1", port: 63994 } },
    stopCandidate: async () => { events.push("stop-candidate"); },
    startPromoted: async (root) => {
      events.push(`start-promoted:${root}`);
      return { ok: true, host: "127.0.0.1", port: 8765 };
    },
  });

  assert.deepEqual(events, [
    "stop-candidate",
    "start-promoted:F:/my-agent-main/data/self-hosting/ui-candidate",
  ]);
  assert.deepEqual(result, { ok: true, host: "127.0.0.1", port: 8765 });
});
