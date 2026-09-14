import assert from "node:assert/strict";
import {
  clearPanelHtml,
  groupTerminalSessions,
  patchPanelHtml,
  reconcileTerminalSession,
  terminalActivityCaption,
  terminalCwdLabel,
  terminalDisplayStatusLabel,
  terminalHumanTitle,
  terminalsPanelFingerprint,
  terminalsSummaryParts,
} from "../src/shells/unified/terminal-list.ts";

const NOW = Date.parse("2026-09-14T12:00:00Z");

function session(partial: Record<string, string> & { alive?: boolean }): {
  session_id: string;
  command: string;
  cwd: string;
  state: string;
  last_activity_at: string;
  alive?: boolean;
} {
  return {
    session_id: partial.session_id || "it-1",
    command: partial.command || "python -i",
    cwd: partial.cwd || "workspace/demo",
    state: partial.state || "running",
    last_activity_at: partial.last_activity_at || "2026-09-14T11:59:00Z",
    ...(partial.alive === undefined ? {} : { alive: partial.alive }),
  };
}

assert.equal(terminalHumanTitle('python -u -c "import sys; print(1)"'), "python");
assert.equal(terminalHumanTitle("python -i"), "python -i");
assert.equal(terminalHumanTitle("npm test"), "npm test");
assert.equal(terminalHumanTitle("npm run dev"), "npm run dev");
assert.equal(
  terminalHumanTitle('"F:/my-agent-main/.venv/Scripts/python.exe" -c "import time"'),
  "python",
);
assert.equal(terminalHumanTitle("uvicorn main:app --reload"), "uvicorn main:app --reload");
assert.equal(terminalHumanTitle("python -m pytest tests"), "python -m pytest tests");
assert.equal(terminalHumanTitle("", "workspace/demo"), "workspace/demo");
assert.equal(terminalCwdLabel("F:/foo/bar/baz"), "bar/baz");
assert.equal(terminalCwdLabel("."), "");

const grouped = groupTerminalSessions(
  [
    session({ session_id: "ended-old", state: "closed", last_activity_at: "2026-09-01T00:00:00Z" }),
    session({ session_id: "lost", state: "lost", last_activity_at: "2026-09-10T00:00:00Z" }),
    session({ session_id: "run", state: "running", last_activity_at: "2026-09-14T11:59:00Z" }),
    session({ session_id: "ended-new", state: "exited", last_activity_at: "2026-09-12T00:00:00Z" }),
    session({ session_id: "failed", state: "failed", last_activity_at: "2026-09-11T00:00:00Z" }),
  ],
  { endedCollapsed: true, endedShowAll: false, nowMs: NOW },
);
assert.deepEqual(grouped.active.map((item) => item.session_id), ["run"]);
assert.deepEqual(grouped.attention.map((item) => item.session_id), ["failed", "lost"]);
assert.equal(grouped.endedTotal, 2);
assert.equal(grouped.endedVisible.length, 0);

const preview = groupTerminalSessions(
  Array.from({ length: 8 }, (_, index) => session({
    session_id: `ended-${index}`,
    state: "closed",
    last_activity_at: `2026-09-${String(index + 1).padStart(2, "0")}T00:00:00Z`,
  })),
  { endedCollapsed: false, endedShowAll: false, endedPreview: 5, nowMs: NOW },
);
assert.equal(preview.endedVisible.length, 5);
assert.equal(preview.endedHiddenCount, 3);

const selected = groupTerminalSessions(
  [
    session({ session_id: "ended-a", state: "closed" }),
    session({ session_id: "ended-b", state: "exited" }),
  ],
  { selectedId: "ended-b", endedCollapsed: true, endedShowAll: false, nowMs: NOW },
);
assert.deepEqual(selected.endedVisible.map((item) => item.session_id), ["ended-b"]);

assert.deepEqual(terminalsSummaryParts(1, 6, 54), ["1 运行中", "6 需处理"]);
assert.deepEqual(terminalsSummaryParts(0, 0, 54), ["无活动会话"]);

const staleRunning = session({
  session_id: "zombie",
  state: "running",
  last_activity_at: "2026-09-09T12:00:00Z",
  alive: true,
});
const reconciledZombie = reconcileTerminalSession(staleRunning, NOW);
assert.equal(reconciledZombie.state, "lost");
assert.equal(reconciledZombie.alive, false);
assert.equal(terminalDisplayStatusLabel(staleRunning, NOW), "会话已丢失");
assert.match(terminalActivityCaption(staleRunning.last_activity_at, { live: false, nowMs: NOW }), /最后见于 5 天前/);
assert.notEqual(terminalDisplayStatusLabel(staleRunning, NOW), "运行中");

const staleGrouped = groupTerminalSessions(
  [
    session({ session_id: "fresh", state: "running", last_activity_at: "2026-09-14T11:50:00Z" }),
    staleRunning,
  ],
  { endedCollapsed: true, endedShowAll: false, nowMs: NOW },
);
assert.deepEqual(staleGrouped.active.map((item) => item.session_id), ["fresh"]);
assert.deepEqual(staleGrouped.attention.map((item) => item.session_id), ["zombie"]);
assert.deepEqual(terminalsSummaryParts(staleGrouped.active.length, staleGrouped.attention.length, staleGrouped.endedTotal), [
  "1 运行中",
  "1 需处理",
]);

const freshLive = session({ session_id: "fresh-live", last_activity_at: "2026-09-14T11:59:30Z" });
assert.equal(terminalDisplayStatusLabel(freshLive, NOW), "运行中");
assert.equal(terminalActivityCaption(freshLive.last_activity_at, { live: true, nowMs: NOW }), "刚刚活动");

const fingerprintA = terminalsPanelFingerprint({
  terminalSessions: [session({ session_id: "run", last_activity_at: "2026-09-14T00:00:00Z" })],
  terminalsLoading: false,
  terminalsError: "",
  terminalsCollapsed: false,
  terminalsEndedCollapsed: true,
  terminalsEndedShowAll: false,
  terminalDetails: null,
  terminalOutput: "",
  terminalOutputLoading: false,
  terminalOutputError: "",
  terminalOutputCursorReset: false,
  terminalOutputTruncated: false,
});
const fingerprintB = terminalsPanelFingerprint({
  terminalSessions: [session({ session_id: "run", last_activity_at: "2026-09-14T00:01:00Z" })],
  terminalsLoading: false,
  terminalsError: "",
  terminalsCollapsed: false,
  terminalsEndedCollapsed: true,
  terminalsEndedShowAll: false,
  terminalDetails: null,
  terminalOutput: "",
  terminalOutputLoading: false,
  terminalOutputError: "",
  terminalOutputCursorReset: false,
  terminalOutputTruncated: false,
});
assert.equal(fingerprintA, fingerprintB, "activity ticks must not rewrite the panel");

const restored: number[] = [];
const panel: {
  dataset: { renderKey?: string };
  innerHTML: string;
  querySelector: (selector: string) => { scrollTop: number } | null;
} = {
  dataset: {},
  innerHTML: "<div class='sidebar-terminals-list'></div>",
  querySelector() {
    return {
      get scrollTop() { return 42; },
      set scrollTop(value: number) { restored.push(value); },
    };
  },
};
patchPanelHtml(panel, "<div class='sidebar-terminals-list'>one</div>", "k1", [".sidebar-terminals-list"]);
assert.equal(panel.innerHTML, "<div class='sidebar-terminals-list'>one</div>");
assert.equal(panel.dataset.renderKey, "k1");
assert.deepEqual(restored, [42]);
patchPanelHtml(panel, "<div class='sidebar-terminals-list'>two</div>", "k1", [".sidebar-terminals-list"]);
assert.equal(panel.innerHTML, "<div class='sidebar-terminals-list'>one</div>", "unchanged key must keep DOM");
clearPanelHtml(panel);
assert.equal(panel.innerHTML, "");
assert.equal(panel.dataset.renderKey, undefined);

console.log("terminal-list tests ok");
