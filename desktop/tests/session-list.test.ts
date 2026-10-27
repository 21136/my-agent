import assert from "node:assert/strict";
import test from "node:test";

import {
  buildSessionRows,
  dateBucket,
  filterSessionRows,
  renderSessionFiltersHtml,
  renderSessionListHtml,
  type SessionListInput,
} from "../src/shells/unified/session-list.ts";

const now = new Date(2026, 9, 18, 10, 0, 0);

function input(over: Partial<SessionListInput> = {}): SessionListInput {
  return {
    sessions: [
      { session_id: "c1", title: "闲聊", updated_at: new Date(2026, 9, 18, 9, 5).toISOString(), message_count: 4 },
      { session_id: "p1", title: "demo 项目", updated_at: new Date(2026, 9, 17, 20, 0).toISOString(), project_id: "demo" },
      { session_id: "o1", title: "other", updated_at: new Date(2026, 9, 1).toISOString(), project_id: "other" },
    ],
    threads: [
      { sessionId: "t-live", title: "主线", preview: "", updatedAt: new Date(2026, 9, 18, 9, 30).toISOString(), archived: false },
      { sessionId: "t-old", title: "旧线", preview: "", updatedAt: new Date(2026, 9, 14).toISOString(), archived: true },
    ],
    projectId: "demo",
    activeSessionId: "t-live",
    currentSessionId: "t-live",
    working: false,
    query: "",
    filter: "all",
    loaded: true,
    now,
    ...over,
  };
}

test("date buckets", () => {
  assert.equal(dateBucket(new Date(2026, 9, 18, 1).toISOString(), now), "today");
  assert.equal(dateBucket(new Date(2026, 9, 17, 23).toISOString(), now), "yesterday");
  assert.equal(dateBucket(new Date(2026, 9, 13).toISOString(), now), "week");
  assert.equal(dateBucket(new Date(2026, 8, 1).toISOString(), now), "earlier");
  assert.equal(dateBucket("", now), "earlier");
});

test("bound project threads replace its collapsed row; other projects stay", () => {
  const rows = buildSessionRows(input());
  assert.deepEqual(rows.map((r) => r.sessionId).sort(), ["c1", "o1", "t-live", "t-old"]);
  const live = rows.find((r) => r.sessionId === "t-live")!;
  assert.equal(live.kind, "thread");
  assert.equal(live.dot, "live");
  assert.equal(live.current, true);
  assert.equal(rows.find((r) => r.sessionId === "t-old")!.dot, "archived");
  assert.equal(buildSessionRows(input({ working: true })).find((r) => r.sessionId === "t-live")!.dot, "running");
});

test("without threads the bound project keeps its session row", () => {
  const rows = buildSessionRows(input({ threads: [] }));
  assert.ok(rows.some((r) => r.sessionId === "p1" && r.kind === "project"));
});

test("empty ordinary sessions are hidden unless current", () => {
  const sessions = [
    { session_id: "e1", title: "e1", updated_at: now.toISOString(), message_count: 0 },
    { session_id: "e2", title: "e2", updated_at: now.toISOString(), message_count: 0 },
  ];
  const rows = buildSessionRows(input({ sessions, threads: [], projectId: "", currentSessionId: "e2" }));
  assert.deepEqual(rows.map((r) => [r.sessionId, r.title]), [["e2", "新会话"]]);
});

test("filters and search", () => {
  const rows = buildSessionRows(input());
  assert.deepEqual(filterSessionRows(rows, "chat", "").map((r) => r.sessionId), ["c1"]);
  assert.deepEqual(filterSessionRows(rows, "project", "").map((r) => r.sessionId).sort(), ["t-live", "t-old"]);
  assert.deepEqual(filterSessionRows(rows, "all", "旧").map((r) => r.sessionId), ["t-old"]);
  const chips = renderSessionFiltersHtml(input());
  assert.match(chips, /data-session-filter="all"/);
  assert.match(chips, /data-session-filter="project"/);
  assert.match(chips, /data-session-filter="chat"/);
});

test("list html groups by date and wires click targets", () => {
  const html = renderSessionListHtml(input());
  assert.ok(html.indexOf("今天") < html.indexOf("本周"));
  assert.match(html, /data-thread-id="t-live"/);
  assert.match(html, /data-open-session="c1" data-project-id=""/);
  assert.match(html, /data-open-session="o1" data-project-id="other"/);
  assert.match(html, /aria-current="true"/);
  assert.match(renderSessionListHtml(input({ loaded: false })), /加载中/);
  assert.match(renderSessionListHtml(input({ query: "zzz" })), /没有匹配的会话/);
  assert.doesNotMatch(renderSessionListHtml(input({ sessions: [{ session_id: "x", title: "<b>", updated_at: "" }], threads: [], projectId: "" })), /<b>/);
});


test("merged lane session rows carry the lane onto the click target", () => {
  const sessions = [
    { session_id: "stable-session", title: "Stable", updated_at: now.toISOString(), execution_lane: "stable" as const },
    { session_id: "sandbox-session", title: "Sandbox", updated_at: now.toISOString(), execution_lane: "sandbox" as const },
  ];
  const html = renderSessionListHtml(input({ sessions, threads: [], projectId: "" }));
  assert.match(html, /data-open-session="stable-session" data-project-id="" data-sidecar-lane="stable"/);
  assert.match(html, /data-open-session="sandbox-session" data-project-id="" data-sidecar-lane="sandbox"/);
  assert.match(html, /实验运行时/);
});

test("current project threads replace only their own lane and current tags are lane-bound", () => {
  const rows = buildSessionRows(input({
    currentLane: "sandbox",
    sessions: [
      { session_id: "t-live", title: "stable copy", updated_at: "", project_id: "demo", execution_lane: "stable" },
      { session_id: "sandbox-project", title: "sandbox", updated_at: "", project_id: "demo", execution_lane: "sandbox" },
    ],
  }));
  const copy = rows.find((row) => row.title === "stable copy")!;
  assert.ok(copy, "current project threads must not hide the other lane");
  assert.equal(copy.current, false);
  assert.equal(copy.sidecarLane, "stable");
  assert.equal(rows.find((row) => row.kind === "thread" && row.sessionId === "t-live")!.sidecarLane, "sandbox");
  assert.ok(!rows.some((row) => row.sessionId === "sandbox-project"));
});
