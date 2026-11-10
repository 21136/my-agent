import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../..");

test("opt-in env gates for quiet drawer Win32 GUI", () => {
  const doc = readFileSync(path.join(repo, "docs/WORKBENCH-UI.md"), "utf8");
  assert.match(doc, /CURRENT_WORK_QUIET_DRAWER_GUI_OPT_IN/);
  assert.match(doc, /_run82-current-work-quiet-drawer-gui/);
  assert.match(doc, /interaction\/layout/);
  const browser = readFileSync(path.join(here, "current-work-quiet-drawer-browser-e2e.test.mjs"), "utf8");
  assert.match(browser, /CURRENT_WORK_QUIET_DRAWER_GUI_OPT_IN/);
  assert.match(browser, /visualAuthenticity/);
  assert.match(browser, /unified-current-work-history-fold/);
  assert.match(browser, /FIFO/);
  const styles = readFileSync(path.join(here, "isolated-shell-styles.mjs"), "utf8");
  assert.match(styles, /neon-twin\.css/);
  assert.match(styles, /composer-companion\.css/);
  assert.match(styles, /VISUAL_DISCLAIMER/);
  assert.match(styles, /composerBarRadius/);
  assert.match(browser, /verifyInteractionStyles/);
  const pet = readFileSync(path.join(here, "isolated-shell-pet-assets.mjs"), "utf8");
  assert.match(pet, /createPublicAssetRouter/);
  const fixture = readFileSync(path.join(here, "current-work-quiet-drawer-browser-fixture.py"), "utf8");
  assert.match(fixture, /companion_unbound/);
  assert.match(fixture, /work_record_post_turn/);
  assert.match(fixture, /project_worker_requirement_verification/);
  const run82 = readFileSync(path.join(repo, "desktop/_run82-current-work-quiet-drawer-gui.cmd"), "utf8");
  assert.match(run82, /current-work-quiet-drawer-browser-e2e\.test\.mjs/);
  assert.match(run82, /EXIT_CODE=%ERRORLEVEL%/);
});
