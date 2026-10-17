import assert from "node:assert/strict";
import test from "node:test";

import {
  WORKING_FRAME_COUNT,
  workingFramePath,
} from "../src/shells/pet/pet-animation.ts";

test("working animation exposes twelve ordered frames", () => {
  assert.equal(WORKING_FRAME_COUNT, 12);
  assert.equal(workingFramePath("/pet-assets/my-agent-working", 0), "/pet-assets/my-agent-working/working-01.png");
  assert.equal(workingFramePath("/pet-assets/my-agent-working", 11), "/pet-assets/my-agent-working/working-12.png");
});

test("working animation wraps frame indexes", () => {
  assert.equal(workingFramePath("/pet-assets/my-agent-working", 12), "/pet-assets/my-agent-working/working-01.png");
  assert.equal(workingFramePath("/pet-assets/my-agent-working", -1), "/pet-assets/my-agent-working/working-12.png");
});
