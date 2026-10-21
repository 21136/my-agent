import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const hydrateSource = readFileSync(
  new URL("../src/agent-image-hydrate.ts", import.meta.url),
  "utf8",
);
const unifiedSource = readFileSync(
  new URL("../src/shells/unified/index.ts", import.meta.url),
  "utf8",
);

test("chat and composer share one agent image hydrate helper", () => {
  assert.match(hydrateSource, /agentImageDataUrlCache/);
  assert.match(unifiedSource, /hydrateAgentImageElements/);
  assert.doesNotMatch(unifiedSource, /const agentImageDataUrlCache = new Map/);
});
