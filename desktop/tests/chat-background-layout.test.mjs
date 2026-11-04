import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const css = readFileSync(path.join(here, "../src/shells/unified/neon-twin.css"), "utf8");
const asset = path.join(here, "../src/shells/unified/assets/chat-background-candidate.png");

test("sandbox chat wallpaper keeps a slightly-left reading lane and companion visible", () => {
  assert.ok(existsSync(asset), "the candidate wallpaper is present beside the unified shell styles");
  assert.match(css, /background-size:\s*cover/);
  assert.match(css, /background-position:\s*(?:right|\d+%|calc\([^)]*\))\s*center/);
  assert.match(css, /\.unified-chat\s*>\s*\*\s*\{[^}]*width:\s*min\(\s*\d+%\s*,\s*\d+rem\s*\)[^}]*margin-left:\s*max\(/s);
  assert.match(css, /\.unified-chat\s*>\s*\*\s*\{[^}]*margin-right:\s*auto/s);
  assert.match(css, /\.unified-composer-dock\s*>\s*\*\s*\{[^}]*width:\s*min\(\s*\d+%\s*,\s*\d+rem\s*\)[^}]*margin-left:\s*max\(/s);
  assert.match(css, /\.unified-pet-row\s*\{[^}]*--ma-pet-size:\s*\d+px/s);
  assert.match(css, /@media\s*\(max-width:\s*\d+px\)[\s\S]*?\.unified-chat\s*>\s*\*\s*,[\s\S]*?\.unified-composer-dock\s*>\s*\*\s*\{[^}]*width:\s*100%/);
  assert.match(css, /@media\s*\(max-width:\s*640px\)[\s\S]*?background-size:\s*cover/);
  assert.match(css, /@media\s*\(max-width:\s*900px\)[\s\S]*?\.unified-chat\s*>\s*\*\s*,[\s\S]*?\.unified-composer-dock\s*>\s*\*\s*\{[^}]*width:\s*100%/);
  assert.match(css, /@media\s*\(min-width:\s*901px\)\s*and\s*\(max-width:\s*1500px\)[\s\S]*?background-position:\s*calc\(100%\s*\+\s*220px\)\s*center/);
});
