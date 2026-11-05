import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const stylesheet = readFileSync(
  new URL("../src/shells/unified/unified.css", import.meta.url),
  "utf8",
);

test("inline chat images cap height so portrait wallpapers do not fill the viewport", () => {
  const mediaRule = stylesheet.match(
    /\.unified-generated-image,\s*\.unified-generated-video\s*\{([^}]*)\}/s,
  );
  assert.ok(mediaRule, "missing shared image/video rule");
  assert.match(mediaRule[1], /max-height:\s*min\(38vh,\s*16rem\)/);
  assert.match(mediaRule[1], /max-width:\s*100%/);
  const wrapRule = stylesheet.match(/\.unified-generated-image-wrap\s*\{([^}]*)\}/s);
  assert.ok(wrapRule, "missing .unified-generated-image-wrap rule");
  assert.match(wrapRule[1], /max-width:\s*min\(100%,\s*18rem\)/);
});
