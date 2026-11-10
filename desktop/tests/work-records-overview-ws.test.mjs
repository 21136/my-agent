import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
const ts = createRequire(import.meta.url)("typescript");
const SESSION = "20261030-abcdef12";
function harness() {
  let n = 0;
  const crypto = { randomUUID: () => `22222222-2222-4222-8222-${String(++n).padStart(12, "0")}` };
  const window = { setTimeout: () => 1, clearTimeout() {}, myAgentDesktop: {} };
  class Socket {
    static OPEN = 1; static instances = []; readyState = 0; sent = []; handlers = new Map();
    constructor() { Socket.instances.push(this); queueMicrotask(() => { this.readyState = 1; this.emit("open", {}); }); }
    addEventListener(type, fn) { this.handlers.set(type, [...(this.handlers.get(type) ?? []), fn]); }
    emit(type, event) { for (const fn of this.handlers.get(type) ?? []) fn(event); }
    send(value) { this.sent.push(JSON.parse(value)); }
    message(value) { this.emit("message", { data: JSON.stringify(value) }); }
    close() { this.readyState = 3; this.emit("close", {}); }
  }
  const cache = new Map();
  function load(url) {
    if (cache.has(url.href)) return cache.get(url.href);
    const exports = {}; cache.set(url.href, exports);
    const js = ts.transpileModule(readFileSync(url, "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
    new Function("exports", "require", "window", "WebSocket", "crypto", js)(exports,
      id => load(new URL(id.endsWith(".ts") ? id : `${id}.ts`, url)), window, Socket, crypto);
    return exports;
  }
  const Client = load(new URL("../src/api/ws.ts", import.meta.url)).AgentWsClient;
  return { client: new Client("127.0.0.1", 1234), Socket };
}
const response = (session_id, request_id, overrides = {}) => ({
  type: "work-records.overview.result", session_id, request_id, state: "no_records", groups: [],
  unread_important_results: [], unread_status: "known", visibility_limitations: [],
  visibility_counts: {
    visible_groups: 0, visible_items: 0, collapsed_groups: 0, collapsed_items: 0,
    attention_visible_items: 0, attention_visible_groups: 0,
  },
  snapshot_id: null,
  page: { page_size: 6, offset: 0, total_groups: 0, total_visible_groups: 0, total_visible_items: 0,
    total_collapsed_groups: 0, total_collapsed_items: 0, has_more: false, next_cursor: null },
  ...overrides,
});
async function ready() {
  const h = harness(); await h.client.connect(); const socket = h.Socket.instances[0];
  socket.message({ type: "session.banner", session_id: SESSION });
  return { ...h, socket };
}

test("work-record controls are session-bound and reject stale or mismatched replies", async () => {
  const { client, socket } = await ready(); const received = []; client.onEvent(event => received.push(event));
  const requestId = client.controlWorkRecord({ itemId: "item-1", requirementId: "req-1", action: "stop", expectedStatus: "queued", expectedRevision: 1 });
  const sent = socket.sent.at(-1);
  assert.equal(sent.type, "work-record.control");
  assert.equal(sent.session_id, SESSION);
  assert.equal(sent.request_id, requestId);
  assert.equal(sent.item_id, "item-1");
  assert.equal(sent.action, "stop");
  socket.message({ type: "work-record.control.done", request_id: "33333333-3333-4333-8333-333333333333",
    session_id: SESSION, item_id: "item-1", requirement_id: "req-1", action: "stop", status: "applied", message: "wrong id" });
  socket.message({ type: "work-record.control.done", request_id: requestId,
    session_id: "20261030-fedcba98", item_id: "item-1", requirement_id: "req-1", action: "stop", status: "applied", message: "wrong session" });
  assert.equal(received.filter(event => event.type === "work-record.control.done").length, 0);
  socket.message({ type: "work-record.control.done", request_id: requestId,
    session_id: SESSION, item_id: "item-1", requirement_id: "req-1", action: "stop", status: "applied", message: "stopped" });
  socket.message({ type: "work-record.control.done", request_id: requestId,
    session_id: SESSION, item_id: "item-1", requirement_id: "req-1", action: "stop", status: "applied", message: "duplicate" });
  assert.equal(received.filter(event => event.type === "work-record.control.done").length, 1);
  await client.openSession("20261030-fedcba98");
  socket.message({ type: "session.banner", session_id: "20261030-fedcba98" });
  const remoteRequestId = client.controlWorkRecord({ itemId: "remote-item", requirementId: "remote-req",
    projectId: "project-beta", expectedSourceRevision: 8, action: "stop", expectedStatus: "queued" });
  assert.equal(socket.sent.at(-1).project_id, "project-beta");
  socket.message({ type: "work-record.control.done", request_id: remoteRequestId, session_id: "20261030-fedcba98",
    item_id: "remote-item", project_id: "project-beta", requirement_id: "remote-req",
    action: "stop", status: "applied", message: "remote stopped" });
  assert.equal(received.at(-1).project_id, "project-beta");
  const revisionRequestId = client.controlWorkRecord({ itemId: "remote-item", requirementId: "remote-req",
    projectId: "project-beta", expectedSourceRevision: 8, action: "revise_requirement", expectedStatus: "stopped",
    newText: "revised exact requirement" });
  assert.equal(socket.sent.at(-1).action, "revise_requirement");
  assert.equal(socket.sent.at(-1).new_text, "revised exact requirement");
  socket.message({ type: "work-record.control.done", request_id: revisionRequestId, session_id: "20261030-fedcba98",
    item_id: "remote-item", project_id: "project-beta", requirement_id: "remote-req",
    action: "revise_requirement", status: "applied", message: "revised" });
  assert.equal(received.at(-1).action, "revise_requirement");
  const stale = client.controlWorkRecord({ itemId: "item-2", requirementId: "req-2", action: "continue", expectedStatus: "stopped", expectedRevision: 2 });
  const beforeControlCount = received.filter(event => event.type === "work-record.control.done").length;
  await client.openSession("20261030-12345678");
  socket.message({ type: "session.banner", session_id: "20261030-12345678" });
  socket.message({ type: "work-record.control.done", request_id: stale, session_id: "20261030-fedcba98",
    item_id: "item-2", requirement_id: "req-2", action: "continue", status: "applied", message: "late" });
  assert.equal(received.filter(event => event.type === "work-record.control.done").length, beforeControlCount);
  client.destroy();
});

test("explicit schedule launch is sent as a normal agent user message in the active session", async () => {
  const { client, socket } = await ready();
  const message = "synthetic explicit schedule launch request";
  const dispatch = client.sendMessage(message, undefined, { turnMode: "agent" });
  assert.deepEqual(socket.sent.at(-1), {
    type: "user.message", request_id: dispatch.requestId, session_id: SESSION,
    text: message, turn_mode: "agent",
  });
  assert.equal(dispatch.sessionId, SESSION);
  client.destroy();
});

test("structured overview query uses current session, a fresh UUID, and server cursor fields", async () => {
  const { client, socket } = await ready();
  const request_id = client.queryWorkRecordsOverview({ pageSize: 6, cursor: "cursor-token" });
  assert.deepEqual(socket.sent.at(-1), { type: "work-records.overview.query", session_id: SESSION,
    request_id, page_size: 6, cursor: "cursor-token", detail_paging: true });
  assert.match(request_id, /^[0-9a-f-]{36}$/);
  client.destroy();
});

test("overview result without attention counts is dropped by client validation", async () => {
  const { client, socket } = await ready();
  const received = [];
  client.onEvent(event => received.push(event));
  const request_id = client.queryWorkRecordsOverview();
  const legacy = response(SESSION, request_id);
  delete legacy.visibility_counts.attention_visible_items;
  delete legacy.visibility_counts.attention_visible_groups;
  socket.message(legacy);
  assert.equal(received.filter(event => event.type === "work-records.overview.result").length, 0);
  client.destroy();
});

test("selected-project overview query carries only validated project IDs and no caller session IDs", async () => {
  const { client, socket } = await ready();
  const requestId = client.queryWorkRecordsOverview({ pageSize: 6, projectIds: ["project-alpha", "project-beta"] });
  const sent = socket.sent.at(-1);
  assert.equal(sent.type, "work-records.overview.query");
  assert.deepEqual(sent.project_ids, ["project-alpha", "project-beta"]);
  assert.equal("source_session_ids" in sent, false);
  assert.equal("cursor" in sent, false);
  assert.ok(requestId);
  assert.throws(() => client.queryWorkRecordsOverview({ projectIds: ["same", "same"] }));
  assert.throws(() => client.queryWorkRecordsOverview({ projectIds: Array.from({ length: 7 }, (_, index) => `p${index}`) }));
  client.destroy();
});

test("selected-project history pages reject rows outside the authorized source sessions", async () => {
  const { client, socket } = await ready(); const received = []; client.onEvent(event => received.push(event));
  const request_id = client.queryWorkRecordsOverview({ projectIds: ["project-alpha", "project-beta"], mergedHistory: {} });
  const page = { page_size: 10, offset: 0, has_more: false, next_cursor: null, scope: "selected_projects_history", total: null };
  const sources = [
    { project_id: "project-alpha", session_id: "source-alpha", revision: 2 },
    { project_id: "project-beta", session_id: "source-beta", revision: 3 },
  ];
  const row = { project_id: "project-alpha", session_id: "source-alpha", item_id: "item", requirement_id: "req",
    run_id: "run", result_id: "result", status: "completed", summary: "synthetic", verification_status: "verified", recorded_at: 10 };
  const valid = response(SESSION, request_id, { state: "visible", aggregation_scope: "selected_project_sessions",
    selected_project_ids: ["project-alpha", "project-beta"], history_scope: "selected_projects_history",
    history_sources: sources, history: [row], history_page: page });
  socket.message({ ...valid, history: [{ ...row, project_id: "project-beta" }] });
  socket.message({ ...valid, history: [{ ...row, session_id: "unselected-session" }] });
  socket.message({ ...valid, history_page: { ...page, page_size: 0 } });
  assert.equal(received.filter(event => event.type === "work-records.overview.result").length, 0);
  socket.message(valid);
  assert.equal(received.filter(event => event.type === "work-records.overview.result").length, 1);
  client.destroy();
});

test("authorized single-requirement history request is scoped and validates revision-bearing pages", async () => {
  const { client, socket } = await ready(); const received = []; client.onEvent(event => received.push(event));
  const request_id = client.queryWorkRecordsOverview({ projectIds: ["project-alpha"], history: {
    projectId: "project-alpha", itemId: "item-one", requirementId: "req-one", cursor: "synthetic-cursor",
  } });
  const sent = socket.sent.at(-1);
  assert.equal(sent.history_project_id, "project-alpha");
  assert.equal(sent.history_item_id, "item-one");
  assert.equal(sent.history_requirement_id, "req-one");
  assert.equal(sent.history_cursor, "synthetic-cursor");
  assert.equal("source_session_ids" in sent, false);
  const event = response(SESSION, request_id, { state: "visible", aggregation_scope: "selected_project_sessions",
    selected_project_ids: ["project-alpha"], history_project_id: "project-alpha", history_item_id: "item-one",
    history_requirement_id: "req-one", source: { project_id: "project-alpha", session_id: "authorized-source", revision: 4 },
    history: [{ run_id: "run-synthetic", result_id: "result-synthetic", status: "completed", summary: "synthetic", verification_status: "unknown", recorded_at: 10 }],
    history_page: { page_size: 10, offset: 0, has_more: false, next_cursor: null, scope: "this_requirement_only" } });
  socket.message({ ...event, history_page: { ...event.history_page, scope: "all_history" } });
  assert.equal(received.length, 0, "scope mismatch is rejected");
  socket.message(event);
  assert.equal(received.length, 1);
  client.destroy();
});

test("legacy compatibility can explicitly disable optional detail paging without changing query shape", async () => {
  const { client, socket } = await ready();
  const request_id = client.queryWorkRecordsOverview({ detailPaging: false });
  assert.deepEqual(socket.sent.at(-1), { type: "work-records.overview.query", session_id: SESSION,
    request_id, page_size: 6 });
  client.destroy();
});

test("overview replies require matching request and current session", async () => {
  const { client, socket } = await ready(); const received = [];
  client.onEvent(event => received.push(event));
  const request_id = client.queryWorkRecordsOverview();
  socket.message(response(SESSION, "33333333-3333-4333-8333-333333333333"));
  socket.message(response("20261030-fedcba98", request_id));
  assert.equal(received.filter(event => event.type === "work-records.overview.result").length, 0);
  socket.message(response(SESSION, request_id));
  socket.message(response(SESSION, request_id));
  assert.equal(received.filter(event => event.type === "work-records.overview.result").length, 1);
  client.destroy();
});

test("session switch retires pending overview replies", async () => {
  const { client, socket } = await ready(); const received = [];
  client.onEvent(event => received.push(event));
  const oldRequest = client.queryWorkRecordsOverview();
  await client.openSession("20261030-fedcba98");
  socket.message({ type: "session.banner", session_id: "20261030-fedcba98" });
  socket.message(response(SESSION, oldRequest));
  assert.equal(received.filter(event => event.type === "work-records.overview.result").length, 0);
  client.destroy();
});

test("overview detail-page response validates and remains scoped to its cursor request", async () => {
  const { client, socket } = await ready(); const received = [];
  client.onEvent(event => received.push(event));
  const cursor = "synthetic-items-cursor";
  const request_id = client.queryWorkRecordsOverview({ cursor });
  const group = { kind: "project", id: "p1", visibility: "visible", status: "pending", status_counts: { pending: 2 },
    item_count: 2, requirement_count: 2, items_page: { kind: "items", group_kind: "project", group_id: "p1", item_id: null,
      offset: 1, total: 2, has_more: false, next_cursor: null },
    items: [{ item_id: "i2", title: "second", status: "pending", status_counts: { pending: 1 },
      requirements: [{ requirement_id: "r2", text: "second requirement", status: "pending" }],
      current_results: [], requirements_page: { kind: "requirements", group_kind: "project", group_id: "p1", item_id: "i2",
        offset: 0, total: 1, has_more: false, next_cursor: null } }] };
  const page = { kind: "items", group_kind: "project", group_id: "p1", item_id: null,
    offset: 1, total: 2, has_more: false, next_cursor: null };
  const detailEvent = response(SESSION, request_id, { state: "visible", snapshot_id: "snapshot-1", groups: [group], detail_page: page,
    visibility_counts: {
      visible_groups: 1, visible_items: 2, collapsed_groups: 0, collapsed_items: 0,
      attention_visible_items: 2, attention_visible_groups: 1,
    },
    page: { page_size: 6, offset: 0, total_groups: 1, total_visible_groups: 1, total_visible_items: 2,
      total_collapsed_groups: 0, total_collapsed_items: 0, has_more: false, next_cursor: null } });
  socket.message(detailEvent);
  socket.message(detailEvent);
  assert.equal(received.filter(event => event.type === "work-records.overview.result").length, 1,
    "one cursor reply is consumed once; a duplicate frame is ignored");
  const valid = received.find(event => event.type === "work-records.overview.result");
  assert.equal(valid.detail_page.kind, "items");
  client.destroy();
});

test("session switch retires a pending detail cursor reply", async () => {
  const { client, socket } = await ready(); const received = [];
  client.onEvent(event => received.push(event));
  const oldRequest = client.queryWorkRecordsOverview({ cursor: "synthetic-detail-cursor" });
  await client.openSession("20261030-fedcba98");
  socket.message({ type: "session.banner", session_id: "20261030-fedcba98" });
  const page = { kind: "items", group_kind: "project", group_id: "p1", item_id: null,
    offset: 5, total: 6, has_more: false, next_cursor: null };
  const group = { kind: "project", id: "p1", visibility: "visible", status: "pending", status_counts: { pending: 6 },
    item_count: 6, requirement_count: 6, items_page: page,
    items: [{ item_id: "late-item", title: "late", status: "pending", status_counts: { pending: 1 },
      requirements: [{ requirement_id: "late-req", text: "late", status: "pending" }], current_results: [],
      requirements_page: { kind: "requirements", group_kind: "project", group_id: "p1", item_id: "late-item",
        offset: 0, total: 1, has_more: false, next_cursor: null } }] };
  socket.message(response(SESSION, oldRequest, { state: "visible", snapshot_id: "old-snapshot", groups: [group], detail_page: page,
    visibility_counts: {
      visible_groups: 1, visible_items: 6, collapsed_groups: 0, collapsed_items: 0,
      attention_visible_items: 6, attention_visible_groups: 1,
    },
    page: { page_size: 6, offset: 0, total_groups: 1, total_visible_groups: 1, total_visible_items: 6,
      total_collapsed_groups: 0, total_collapsed_items: 0, has_more: false, next_cursor: null } }));
  assert.equal(received.filter(event => event.type === "work-records.overview.result").length, 0);
  client.destroy();
});


test("unread total/truncated metadata is strict while legacy omission stays compatible", async () => {
  const { client, socket } = await ready(); const received = [];
  client.onEvent(event => received.push(event));
  const samples = Array.from({ length: 100 }, (_, index) => ({ group: ["project", "p1"],
    item_id: `i-${index}`, requirement_id: `r-${index}`, result_id: `result-${index}` }));
  const request_id = client.queryWorkRecordsOverview();
  socket.message(response(SESSION, request_id, { unread_important_results: samples,
    unread_important_results_total: 101, unread_important_results_truncated: true }));
  assert.equal(received.length, 1);

  const badRequest = client.queryWorkRecordsOverview();
  socket.message(response(SESSION, badRequest, { unread_important_results: samples,
    unread_important_results_total: 101, unread_important_results_truncated: false }));
  assert.equal(received.length, 1, "a non-truncated sample must equal the stated total");

  const partialRequest = client.queryWorkRecordsOverview();
  socket.message(response(SESSION, partialRequest, { unread_important_results_total: 100 }));
  assert.equal(received.length, 1, "partial metadata is rejected rather than guessed");

  const lowerBoundRequest = client.queryWorkRecordsOverview();
  socket.message(response(SESSION, lowerBoundRequest, { unread_important_results: samples,
    unread_important_results_truncated: true }));
  assert.equal(received.length, 2, "a validated truncated sample without total is allowed as a lower bound");

  const legacyRequest = client.queryWorkRecordsOverview();
  socket.message(response(SESSION, legacyRequest));
  assert.equal(received.length, 3, "old responses without either metadata field remain accepted");
  client.destroy();
});


test("source snapshot overview validates provenance and rejects unsafe or corrupt claims", async () => {
  const {client,socket}=await ready(); const received=[]; client.onEvent(e=>received.push(e));
  const request_id=client.queryWorkRecordsOverview();
  const source={kind:"linked_source_snapshot",id:"linked-source-"+"a".repeat(64),visibility:"visible",status:"unknown",status_counts:{unknown:1},item_count:1,requirement_count:1,attention_status:"unknown",attention_reason:"worker_result_unverified",visibility_timer_status:"unknown",
    source_snapshot:{scope:"authorized_source_session_only",mapping_claim:"not_asserted",project_id:"p1",source_session_id:"source-1",source_revision:1,captured_at:"2026-10-30T01:00:00Z",copy_id:"a".repeat(64),intent_state:"reference_queued",card_id:null,viewed_at:null,superseded_snapshot_count:0,superseded_unviewed_count:0},
    items:[{item_id:"i1",title:"source",status:"unknown",status_at_capture:"completed",status_counts:{unknown:1},requirements:[{requirement_id:"r1",text:"req",status:"unknown",status_at_capture:"unknown",worker_facts:[]}],current_results:[]}]};
  source.items[0].requirements[0].worker_facts = [{binding_id:"binding",worker_id:"worker",worker_run_id:"run",host_start:{reservation_id:"reservation",parent_run_id:"parent",turn_epoch:2},host_lifecycle:{sequence:2,status:"model_turn_finished",finish_reason:"completed"},operation_result:"unknown",verification:"not_verified"}];
  const event=response(SESSION,request_id,{state:"visible",snapshot_id:"snapshot",groups:[source],visibility_counts:{visible_groups:1,visible_items:1,collapsed_groups:0,collapsed_items:0,attention_visible_items:1,attention_visible_groups:1},page:{page_size:6,offset:0,total_groups:1,total_visible_groups:1,total_visible_items:1,total_collapsed_groups:0,total_collapsed_items:0,has_more:false,next_cursor:null}});
  for (const bad of [{...source,attention_reason:["worker_result_unverified"]}, {...source,attention_status:["unknown"]}, {...source,visibility_timer_status:"bad"}, {...source,source_snapshot:{...source.source_snapshot,intent_state:["reference_queued"]}}, {...source,items:[{...source.items[0],requirements:[{...source.items[0].requirements[0],worker_facts:[{...source.items[0].requirements[0].worker_facts[0],host_lifecycle:{sequence:2,status:["model_turn_finished"],finish_reason:"completed"}}]}]}]}, {...source,source_snapshot:{...source.source_snapshot,mapping_claim:"current"}}, {...source,source_snapshot:{...source.source_snapshot,source_session_id:""}},
    {...source,source_snapshot:{...source.source_snapshot,superseded_unviewed_count:3}}, {...source,items:[{...source.items[0],status:"completed"}]},
    {...source,items:[{...source.items[0],requirements:[{...source.items[0].requirements[0],worker_facts:[{...source.items[0].requirements[0].worker_facts[0],verification:"verified"}]}]}]},
    {...source,items:[{...source.items[0],current_results:[{requirement_id:"r1",result_id:"fake",status:"completed",read_status:"viewed"}]}]}]) socket.message({...event,groups:[bad]});
  assert.equal(received.length,0); socket.message(event); assert.equal(received.length,1);
  assert.deepEqual(received[0].groups[0].source_snapshot,source.source_snapshot); client.destroy();
});

test("source overview accepts backend visibility timer and attention states without accepting collapsed rows",async()=>{
  const base={kind:"linked_source_snapshot",id:"linked-source-"+"a".repeat(64),visibility:"visible",status:"unknown",status_counts:{unknown:1},item_count:1,requirement_count:1,attention_status:"unknown",attention_reason:"captured_requirement_unresolved",visibility_timer_status:"elapsed",source_snapshot:{scope:"authorized_source_session_only",mapping_claim:"not_asserted",project_id:"p1",source_session_id:"source-1",source_revision:1,captured_at:"2026-10-30T01:00:00Z",copy_id:"a".repeat(64),intent_state:"reference_queued",card_id:null,viewed_at:null,superseded_snapshot_count:0,superseded_unviewed_count:0},items:[{item_id:"i1",title:"source",status:"unknown",status_at_capture:"in_progress",status_counts:{unknown:1},requirements:[{requirement_id:"r1",text:"req",status:"unknown",status_at_capture:"in_progress",worker_facts:[]}],current_results:[]}]};
  for (const [reason,timer] of [["captured_requirement_unresolved","elapsed"],["superseded_snapshot_attention","waiting"],["view_evidence_unavailable","unavailable"],["view_not_confirmed","not_viewed"],["publication_pending","unknown"]]) {
    const {client,socket}=await ready(), received=[];client.onEvent(e=>received.push(e));const request_id=client.queryWorkRecordsOverview();
    const event=response(SESSION,request_id,{state:"visible",snapshot_id:"snapshot",groups:[{...base,attention_reason:reason,visibility_timer_status:timer}],visibility_counts:{visible_groups:1,visible_items:1,collapsed_groups:0,collapsed_items:0,attention_visible_items:1,attention_visible_groups:1},page:{page_size:6,offset:0,total_groups:1,total_visible_groups:1,total_visible_items:1,total_collapsed_groups:0,total_collapsed_items:0,has_more:false,next_cursor:null}});
    for(const bad of [{...event.groups[0],attention_status:"none"},{...event.groups[0],attention_reason:"elapsed_terminal_capture"},{...event.groups[0],visibility_timer_status:"bogus"},{...event.groups[0],visibility:"collapsed",attention_status:"none",attention_reason:"elapsed_terminal_capture",visibility_timer_status:"elapsed"}]) socket.message({...event,groups:[bad]});
    assert.equal(received.length,0);socket.message(event);assert.equal(received.length,1,`${reason}/${timer}`);client.destroy();
  }
  const {client,socket}=await ready(), received=[];client.onEvent(e=>received.push(e));const request_id=client.queryWorkRecordsOverview();
  socket.message(response(SESSION,request_id,{state:"collapsed",snapshot_id:"snapshot",groups:[],visibility_counts:{visible_groups:0,visible_items:0,collapsed_groups:1,collapsed_items:1,attention_visible_items:0,attention_visible_groups:0},page:{page_size:6,offset:0,total_groups:0,total_visible_groups:0,total_visible_items:0,total_collapsed_groups:1,total_collapsed_items:1,has_more:false,next_cursor:null}}));
  assert.equal(received.length,1);assert.equal(received[0].state,"collapsed");client.destroy();
});
