import assert from "node:assert/strict";
import test from "node:test";
import { formatCandidateSwitchResult, normalizeCandidateSwitchResult } from "../src/host-candidate.ts";

test("candidate switch result preserves rollback state for the operator", () => {
  const result = normalizeCandidateSwitchResult({
    ok: false,
    state: "rolled_back",
    recovered: true,
    error: "candidate health check failed",
  });

  assert.equal(result.state, "rolled_back");
  assert.equal(result.recovered, true);
  assert.match(formatCandidateSwitchResult(result), /已回滚/);
});

test("invalid candidate switch response fails closed", () => {
  const result = normalizeCandidateSwitchResult(null);

  assert.equal(result.ok, false);
  assert.equal(result.state, "blocked");
  assert.equal(result.recovered, false);
  assert.match(formatCandidateSwitchResult(result), /无效/);
});
