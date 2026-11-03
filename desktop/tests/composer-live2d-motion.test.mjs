import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import test from "node:test";
import { applyIdleMotionParameters } from "../src/shells/unified/composer-live2d.ts";

const idleMotion = JSON.parse(readFileSync(new URL("../public/live2d/chibi-front/see-through-chibi-v1.idle.motion3.json", import.meta.url), "utf8"));

test("idle motion curves change the matching model parameters from a baseline over time", () => {
  const parameters = { ids: ["ParamBreath", "ParamEyeLOpen"], values: new Float32Array(2) };
  const baseline = new Float32Array([0.25, 1]);
  applyIdleMotionParameters(parameters, idleMotion, baseline, 0);
  const atStart = Array.from(parameters.values);
  applyIdleMotionParameters(parameters, idleMotion, baseline, 4.5);
  const later = Array.from(parameters.values);
  assert.notDeepEqual(later, atStart);
  assert.notEqual(later[0], baseline[0], "ParamBreath follows its idle curve");
  assert.equal(later[1], baseline[1], "unanimated parameters keep their baseline");
});

test("idle motion evaluator supports the candidate Cubism Bezier segment format", () => {
  const parameters = { ids: ["ParamBreath"], values: new Float32Array([0]) };
  const motion = { Curves: [{ Target: "Parameter", Id: "ParamBreath", Segments: [0, 0, 1, 1/3, 1/3, 2/3, 2/3, 1, 1] }] };
  applyIdleMotionParameters(parameters, motion, new Float32Array([0]), 0.5);
  assert.ok(Math.abs(parameters.values[0] - 0.5) < 0.02);
});

