import assert from "node:assert/strict";
import test from "node:test";
import { buildMaskGroups, blendModeForFlags, compositeDrawableMask, drawableUsesInvertedMask } from "../src/shells/unified/composer-live2d.ts";

test("Cubism drawable mask lists are grouped deterministically with inversion kept distinct", () => {
  const drawables = {
    count: 10,
    maskCounts: [4, 1, 1, 0],
    masks: [[1, 2, 7, 8], [8], [7], []],
    constantFlags: [12, 4, 4, 4],
  };
  const groups = buildMaskGroups(drawables);
  assert.deepEqual([...groups.values()], [
    { maskIds: [1, 2, 7, 8], inverted: true, drawableIndices: [0] },
    { maskIds: [8], inverted: false, drawableIndices: [1] },
    { maskIds: [7], inverted: false, drawableIndices: [2] },
  ]);
});

test("Cubism blend flags map to Canvas blend operations without mistaking inversion for blend", () => {
  assert.equal(blendModeForFlags(0), "source-over");
  assert.equal(blendModeForFlags(1), "lighter");
  assert.equal(blendModeForFlags(2), "multiply");
  assert.equal(blendModeForFlags(12), "source-over");
  assert.equal(drawableUsesInvertedMask(12), true);
  assert.equal(drawableUsesInvertedMask(4), false);
});

test("mask alpha is applied to an isolated drawable layer and restores the blend state", () => {
  const ops = [];
  const ctx = { globalCompositeOperation: "lighter", save(){ops.push("save")}, drawImage(_image,x,y){ops.push([this.globalCompositeOperation,x,y])}, restore(){this.globalCompositeOperation="lighter";ops.push("restore")} };
  compositeDrawableMask(ctx, {}, false);
  compositeDrawableMask(ctx, {}, true);
  assert.deepEqual(ops, ["save",["destination-in",0,0],"restore","save",["destination-out",0,0],"restore"]);
  assert.equal(ctx.globalCompositeOperation, "lighter");
});

