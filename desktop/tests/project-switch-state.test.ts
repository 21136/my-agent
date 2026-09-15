import assert from "node:assert/strict";
import { test } from "node:test";
import {
  expectedProjectIdForEvents,
  isCrossProjectSession,
  pendingSwitchProjectId,
  shouldApplyProjectEvent,
  type ProjectSwitchFilterState,
} from "../src/shells/unified/project-switch-state.ts";

function state(partial: Partial<ProjectSwitchFilterState>): ProjectSwitchFilterState {
  return {
    projectId: "music",
    switchInProgress: false,
    pendingPickerId: "",
    switchOverlayProjectId: "",
    ...partial,
  };
}

test("pending switch project prefers picker then overlay", () => {
  assert.equal(pendingSwitchProjectId(state({ pendingPickerId: "private-code" })), "private-code");
  assert.equal(
    pendingSwitchProjectId(state({ switchOverlayProjectId: "private-code" })),
    "private-code",
  );
  assert.equal(pendingSwitchProjectId(state({})), "");
});

test("cross-project session detect", () => {
  assert.equal(isCrossProjectSession("music", "private-code"), true);
  assert.equal(isCrossProjectSession("music", "music"), false);
  assert.equal(isCrossProjectSession("music", ""), false);
  assert.equal(isCrossProjectSession("", "private-code"), false);
});

test("during switch, accept target project events and drop the old project", () => {
  const switching = state({
    projectId: "music",
    switchInProgress: true,
    pendingPickerId: "private-code",
  });
  assert.equal(expectedProjectIdForEvents(switching), "private-code");
  assert.equal(shouldApplyProjectEvent(switching, "private-code"), true);
  assert.equal(shouldApplyProjectEvent(switching, "music"), false);
  assert.equal(shouldApplyProjectEvent(switching, ""), true);
});

test("switch overlay pending still isolates events after loading flag clears", () => {
  const overlay = state({
    projectId: "music",
    switchInProgress: false,
    switchOverlayProjectId: "private-code",
  });
  assert.equal(expectedProjectIdForEvents(overlay), "private-code");
  assert.equal(shouldApplyProjectEvent(overlay, "private-code"), true);
  assert.equal(shouldApplyProjectEvent(overlay, "music"), false);
});

test("idle bound project still drops stale sibling events", () => {
  const idle = state({ projectId: "music" });
  assert.equal(shouldApplyProjectEvent(idle, "music"), true);
  assert.equal(shouldApplyProjectEvent(idle, "private-code"), false);
  assert.equal(shouldApplyProjectEvent(idle, ""), true);
});
