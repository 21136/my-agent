import assert from "node:assert/strict";
import { test } from "node:test";
import { getOptionalHostPath } from "../src/host-settings-path.ts";

test("a failed host-folder lookup degrades to unavailable without rejecting the wizard", async () => {
  let called = 0;
  const result = await getOptionalHostPath(async () => {
    called += 1;
    throw new Error("app:get-downloads-path failed");
  });

  assert.equal(called, 1);
  assert.equal(result, "");
});

test("a successful host-folder lookup keeps its returned path", async () => {
  assert.equal(await getOptionalHostPath(async () => "C:/Downloads"), "C:/Downloads");
});

test("an unavailable optional host-folder API degrades to an empty path", async () => {
  assert.equal(await getOptionalHostPath(undefined), "");
});
