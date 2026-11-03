import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const ts = createRequire(import.meta.url)("typescript");
const SESSION = "20261030-abcdef12";
const CARD_ID = `wc-${"a".repeat(64)}`;
const NEXT_SESSION = "20261031-12345678";
function loadClient() {
  const window = { setTimeout: () => 1, clearTimeout: () => {}, myAgentDesktop: {} };
  const crypto = { randomUUID: () => "22222222-2222-4222-8222-222222222222" };
  class Socket {
    static OPEN = 1;
    static instances = [];
    readyState = 0;
    handlers = new Map();
    sent = [];
    constructor() { Socket.instances.push(this); queueMicrotask(() => { this.readyState = 1; this.emit("open", {}); }); }
    addEventListener(type, fn) { this.handlers.set(type, [...(this.handlers.get(type) ?? []), fn]); }
    emit(type, value) { for (const fn of this.handlers.get(type) ?? []) fn(value); }
    send(value) { this.sent.push(JSON.parse(value)); }
    close() { this.readyState = 3; this.emit("close", {}); }
    message(value) { this.emit("message", { data: JSON.stringify(value) }); }
  }
  const cache = new Map();
  function load(url) {
    if (cache.has(url.href)) return cache.get(url.href);
    const exports = {};
    cache.set(url.href, exports);
    const js = ts.transpileModule(readFileSync(url, "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
    new Function("exports", "require", "window", "WebSocket", "crypto", js)(exports, (id) => load(new URL(id.endsWith(".ts") ? id : `${id}.ts`, url)), window, Socket, crypto);
    return exports;
  }
  const Client = load(new URL("../src/api/ws.ts", import.meta.url)).AgentWsClient;
  return { client: new Client("127.0.0.1", 1234), Socket };
}
const linkedCard = (overrides = {}) => ({
  card_id: CARD_ID, saved_at: "2026-10-30T01:00:00Z", viewed_at: null,
  snapshot: {
    kind: "linked_item_copy", session_id: SESSION, captured_at: "2026-10-30T00:59:00Z",
    copy_id: "b".repeat(64), completeness_scope: "item_requirement", project_id: "project-1",
    source_session_id: "20261030-12345678", source_item_digest: "c".repeat(64),
    binding_id: "binding-1", item_id: "item-1", item_title: "Synthetic item", item_status_at_capture: "completed",
    requirement_id: "requirement-1", requirement_text: "Synthetic requirement", requirement_status_at_capture: "completed",
    worker_id: "worker-1", worker_run_id: "worker-run-1",
    host_start: { reservation_id: "reservation-1", parent_run_id: "parent-run-1", turn_epoch: 3 },
    host_lifecycle: { sequence: 3, status: "model_turn_finished", finish_reason: "completed" },
    operation_result: "unknown", verification: "not_verified", ...overrides,
  },
})
const linkedProjectCard = () => ({
  card_id: CARD_ID, saved_at: "2026-10-30T01:00:00Z", viewed_at: null,
  snapshot: {
    kind: "linked_project_session_copy", session_id: SESSION, captured_at: "2026-10-30T00:59:00Z",
    copy_id: "d".repeat(64), binding_id: "binding-1",
    source: {
      scope: "authorized_source_session_only", mapping_claim: "not_asserted", project_id: "project-1",
      source_session_id: "20261030-12345678", source_revision: 17,
      items: [{ item_id: "item-1", title: "Synthetic item", status: "completed", requirements: [
        { requirement_id: "req-1", text: "Synthetic requirement", status: "completed" },
      ] }],
      worker_facts: [{ binding_id: "binding-1", item_id: "item-1", requirement_id: "req-1",
        worker_id: "worker-1", worker_run_id: "worker-run-1",
        host_start: { reservation_id: "reservation-1", parent_run_id: "parent-run-1", turn_epoch: 3 },
        host_lifecycle: { sequence: 3, status: "model_turn_finished", finish_reason: "completed" },
        operation_result: "unknown", verification: "not_verified" }],
    },
  },
});

const card = {
  card_id: CARD_ID, saved_at: "2026-10-30T01:00:00Z", viewed_at: null,
  snapshot: {
    kind: "project", session_id: SESSION, project_id: "project-1", captured_at: "2026-10-30T00:59:00Z", completeness: "complete",
    item_count: 1, requirement_count: 1, result_count: 0, omitted_item_count: 0, omitted_requirement_count: 0, omitted_result_count: 0,
    incomplete_reason: null, persisted: false, history_complete: true, missing_current_result_count: 0,
    cards: [{ session_id: SESSION, project_id: "project-1", item_id: "item-1", title: "项目", status: "in_progress", source_refs: [], requirements: [{
      session_id: SESSION, project_id: "project-1", item_id: "item-1", requirement_id: "req-1", text: "要求", status: "in_progress", source_ref: null, latest_result: null,
      result_count: 0, omitted_result_count: 0, current_result_missing: false, current_run_id: null, latest_result_is_current: null,
    }] }],
  },
};

test("linked project-session copies accept exact source scope and reject altered source identities", async () => {
  const { client, Socket } = loadClient();
  await client.connect();
  const socket = Socket.instances[0];
  socket.message({ type: "session.banner", session_id: SESSION });
  const received = [];
  client.onEvent(event => received.push(event));
  const card = linkedProjectCard();
  const present = { type: "work-card.present", session_id: SESSION,
    presentation_id: "8126fc51-e014-4f84-bd14-9815b4374e58", card_id: CARD_ID, card };
  socket.message(present);
  assert.deepEqual(received.filter(row => row.type === "work-card.present"), [present]);

  const scope = { session_id: SESSION, kind: "linked_project_session_copy",
    project_id: "project-1", mapping_claim: "not_asserted" };
  client.getWorkCard(CARD_ID);
  const found = { type: "work-card.get.done", status: "found", message: "found", card_id: CARD_ID,
    card, source_scope: scope };
  socket.message(found);
  assert.deepEqual(received.filter(row => row.type === "work-card.get.done"), [found]);
  client.markWorkCardSeen(CARD_ID);
  const seen = { type: "work-card.seen.done", status: "acknowledged", message: "viewed", card_id: CARD_ID,
    viewed_at: "2026-10-30T01:01:00Z", source_scope: scope };
  socket.message(seen);
  assert.deepEqual(received.filter(row => row.type === "work-card.seen.done"), [seen]);

  const foundRows = [found];
  const seenRows = [seen];
  for (const source_scope of [
    { ...scope, session_id: NEXT_SESSION },
    { ...scope, project_id: "other-project" },
    { ...scope, mapping_claim: "current" },
    { ...scope, extra: "forged" },
  ]) {
    client.getWorkCard(CARD_ID);
    socket.message({ ...found, source_scope });
    assert.deepEqual(received.filter(row => row.type === "work-card.get.done"), foundRows);
    socket.message(found);
    foundRows.push(found);
    client.markWorkCardSeen(CARD_ID);
    socket.message({ ...seen, source_scope });
    assert.deepEqual(received.filter(row => row.type === "work-card.seen.done"), seenRows);
    socket.message(seen);
    seenRows.push(seen);
  }
  assert.deepEqual(received.filter(row => row.type === "work-card.get.done"), foundRows);
  assert.deepEqual(received.filter(row => row.type === "work-card.seen.done"), seenRows);
  for (const snapshot of [
    { ...card.snapshot, session_id: NEXT_SESSION },
    { ...card.snapshot, source: { ...card.snapshot.source, mapping_claim: "current" } },
    { ...card.snapshot, source: { ...card.snapshot.source, worker_facts: [] } },
  ]) {
    socket.message({ ...present, presentation_id: crypto.randomUUID(), card: { ...card, snapshot } });
  }
  assert.deepEqual(received.filter(row => row.type === "work-card.present"), [present]);
  client.destroy();
});

test("work-card.present accepts strict linked_item_copy snapshots and rejects malformed or foreign-parent copies", async () => {
  const { client, Socket } = loadClient();
  await client.connect();
  const socket = Socket.instances[0];
  socket.message({ type: "session.banner", session_id: SESSION });
  const received = [];
  client.onEvent(event => received.push(event));
  const event = { type: "work-card.present", session_id: SESSION,
    presentation_id: "7cfe09c0-4f54-4d04-9f14-9815b4374e58", card_id: CARD_ID, card: linkedCard() };
  socket.message(event);
  assert.deepEqual(received.filter(row => row.type === "work-card.present"), [event]);
  client.getWorkCard(CARD_ID);
  const history = { type: "work-card.get.done", status: "found", message: "found", card_id: CARD_ID,
    card: linkedCard(), source_scope: { session_id: SESSION, project_id: "parent-project" } };
  socket.message(history);
  assert.deepEqual(received.filter(row => row.type === "work-card.get.done"), [history],
    "history lookup remains compatible with the legacy parent project scope");

  const linkedScope = { session_id: SESSION, kind: "linked_item_copy", project_id: "project-1", item_id: "item-1" };
  client.getWorkCard(CARD_ID);
  const linkedHistory = { ...history, source_scope: linkedScope };
  socket.message(linkedHistory);
  assert.deepEqual(received.filter(row => row.type === "work-card.get.done"), [history, linkedHistory],
    "backend linked scope must be accepted only when session/project/item match the snapshot");
  const linkedHistoryRows = [history, linkedHistory];
  for (const source_scope of [
    { ...linkedScope, session_id: NEXT_SESSION },
    { ...linkedScope, project_id: "foreign-project" },
    { ...linkedScope, item_id: "foreign-item" },
    { ...linkedScope, extra: "forged" },
  ]) {
    client.getWorkCard(CARD_ID);
    socket.message({ ...history, source_scope });
    assert.deepEqual(received.filter(row => row.type === "work-card.get.done"), linkedHistoryRows);
    socket.message(linkedHistory);
    linkedHistoryRows.push(linkedHistory);
  }
  assert.deepEqual(received.filter(row => row.type === "work-card.get.done"), linkedHistoryRows);

  client.markWorkCardSeen(CARD_ID);
  const seen = { type: "work-card.seen.done", status: "acknowledged", message: "viewed", card_id: CARD_ID,
    viewed_at: "2026-10-30T01:01:00Z", source_scope: linkedScope };
  socket.message(seen);
  assert.deepEqual(received.filter(row => row.type === "work-card.seen.done"), [seen]);
  const seenRows = [seen];
  for (const source_scope of [
    { ...linkedScope, session_id: NEXT_SESSION },
    { ...linkedScope, project_id: "foreign-project" },
    { ...linkedScope, item_id: "foreign-item" },
    { ...linkedScope, extra: "forged" },
  ]) {
    client.markWorkCardSeen(CARD_ID);
    socket.message({ ...seen, source_scope });
    assert.deepEqual(received.filter(row => row.type === "work-card.seen.done"), seenRows);
    socket.message(seen);
    seenRows.push(seen);
  }
  assert.deepEqual(received.filter(row => row.type === "work-card.seen.done"), seenRows);

  for (const snapshot of [
    { ...linkedCard().snapshot, completeness_scope: "project" },
    { ...linkedCard().snapshot, operation_result: "completed" },
    { ...linkedCard().snapshot, verification: "verified" },
    { ...linkedCard().snapshot, host_lifecycle: { ...linkedCard().snapshot.host_lifecycle, sequence: 4 } },
    { ...linkedCard().snapshot, unexpected: "field" },
  ]) socket.message({ ...event, presentation_id: crypto.randomUUID(), card: { ...linkedCard(), snapshot } });
  socket.message({ ...event, presentation_id: crypto.randomUUID(), card: {
    ...linkedCard(), snapshot: { ...linkedCard().snapshot, session_id: NEXT_SESSION },
  } });
  assert.deepEqual(received.filter(row => row.type === "work-card.present"), [event]);
  client.destroy();
});

test("work-card.present re-dispatches exact replay for ACK retry and rejects foreign or conflicting identity", async () => {
  const { client, Socket } = loadClient();
  await client.connect();
  const socket = Socket.instances[0];
  socket.message({ type: "session.banner", session_id: SESSION });
  const received = [];
  client.onEvent((event) => received.push(event));
  const event = { type: "work-card.present", session_id: SESSION, presentation_id: "7cfe09c0-4f54-4d04-9f14-9815b4374e58", card_id: CARD_ID, card };
  socket.message(event);
  socket.message(event);
  socket.message({ ...event, session_id: "other", presentation_id: "84bd1b85-6941-4b64-84ef-9815b4374e58" });
  socket.message({ ...event, presentation_id: `${event.presentation_id}\n` });
  socket.message({ ...event, card_id: `wc-${"b".repeat(64)}`, card: { ...card, card_id: `wc-${"b".repeat(64)}` } });
  socket.message({ ...event, card_id: `wc-${"b".repeat(64)}`, card: { ...card, card_id: `wc-${"b".repeat(64)}` } });
  socket.message({ ...event, card: { ...card, card_id: `wc-${"b".repeat(64)}` }, presentation_id: "9126fc51-e014-4f84-bd14-9815b4374e58" });
  assert.deepEqual(received.filter((item) => item.type === "work-card.present"), [event, event], "exact replay stays dispatchable for ACK retry");
  client.destroy();
});


test("work-card.received is session/card-bound, retries after send failure, and never means viewed", async () => {
  const { client, Socket } = loadClient();await client.connect();const socket=Socket.instances[0];socket.message({type:"session.banner",session_id:SESSION});
  const delivered=[];client.onEvent(e=>delivered.push(e));
  const event={type:"work-card.present",session_id:SESSION,presentation_id:"7cfe09c0-4f54-4d04-9f14-9815b4374e58",card_id:CARD_ID,card};socket.message(event);
  assert.throws(()=>client.acknowledgeWorkCardReceived({sessionId:"other",presentationId:event.presentation_id,cardId:CARD_ID}),/invalid|stale/i);
  assert.throws(()=>client.acknowledgeWorkCardReceived({sessionId:SESSION,presentationId:event.presentation_id,cardId:`wc-${"b".repeat(64)}`}),/invalid|stale/i);
  socket.readyState=0;assert.throws(()=>client.acknowledgeWorkCardReceived({sessionId:SESSION,presentationId:event.presentation_id,cardId:CARD_ID}),/WebSocket not connected/);
  socket.readyState=Socket.OPEN;client.acknowledgeWorkCardReceived({sessionId:SESSION,presentationId:event.presentation_id,cardId:CARD_ID});
  client.acknowledgeWorkCardReceived({sessionId:SESSION,presentationId:event.presentation_id,cardId:CARD_ID});
  assert.deepEqual(socket.sent.filter(item=>item.type.startsWith("work-card.")),[
    {type:"work-card.received",session_id:SESSION,presentation_id:event.presentation_id,card_id:CARD_ID},
    {type:"work-card.received",session_id:SESSION,presentation_id:event.presentation_id,card_id:CARD_ID},
  ]);
  client.destroy();
});
test("work-card.receipt.backlog strictly validates session/counts and coalesces identical pushes", async () => {
  const { client, Socket } = loadClient();await client.connect();const socket=Socket.instances[0];socket.message({type:"session.banner",session_id:SESSION});
  const events=[];client.onEvent(event=>events.push(event));
  const backlog={type:"work-card.receipt.backlog",session_id:SESSION,pending_count:23,deferred_count:2,legacy_untracked_count:1,retry_batch_size:20,batch_limit:20,max_send_attempts:3};
  socket.message(backlog);socket.message(backlog);
  socket.message({...backlog,deferred_count:3});
  socket.message({...backlog,session_id:"foreign"});
  socket.message({...backlog,extra:"ignored"});
  socket.message({...backlog,retry_batch_size:24});
  assert.deepEqual(events.filter(event=>event.type==="work-card.receipt.backlog"),[backlog,{...backlog,deferred_count:3}]);
  client.destroy();
});
test("work-card.present accepts an isolated project_scoped snapshot and rejects mixed project rows", async () => {
  const { client, Socket } = loadClient();
  await client.connect();
  const socket = Socket.instances[0];
  socket.message({ type: "session.banner", session_id: SESSION });
  const received = [];
  client.onEvent(event => received.push(event));
  const scoped = structuredClone(card);
  scoped.snapshot.kind = "project_scoped";
  scoped.snapshot.cards[0].status = "unknown";
  scoped.snapshot.cards[0].source_refs = [{ kind: "project", id: "project-1" }];
  scoped.snapshot.cards[0].requirements[0].status = "unknown";
  scoped.snapshot.cards[0].requirements[0].source_ref = { kind: "project", id: "project-1" };
  scoped.snapshot.cards[0].requirements[0].project_id = "project-1";
  scoped.snapshot.cards[0].requirements[0].current_result_missing = false;
  scoped.snapshot.cards[0].requirements[0].current_run_id = "run-1";
  scoped.snapshot.cards[0].requirements[0].latest_result_is_current = true;
  scoped.snapshot.cards[0].requirements[0].result_count = 1;
  scoped.snapshot.cards[0].requirements[0].latest_result = { session_id: SESSION, project_id: "project-1",
    item_id: "item-1", requirement_id: "req-1", result_id: "result-1", run_id: "run-1",
    status: "unknown", summary: "result", verification_status: "not_verified", sources: [] };
  scoped.snapshot.result_count = 1;
  const event = { type: "work-card.present", session_id: SESSION,
    presentation_id: "84bd1b85-6941-4b64-84ef-9815b4374e58", card_id: CARD_ID, card: scoped };
  socket.message(event);
  const mixed = structuredClone(scoped);
  mixed.snapshot.cards[0].requirements[0].project_id = "project-2";
  socket.message({ ...event, presentation_id: "9126fc51-e014-4f84-bd14-9815b4374e58", card: mixed });
  assert.deepEqual(received.filter(item => item.type === "work-card.present"), [event]);
  assert.deepEqual(socket.sent.filter(item => item.type.startsWith("work-card.")), [], "presenting a card must not synthesize a seen/result-view acknowledgement");
  client.destroy();
});

test('work-card.present accepts only a fully identity-bound pre-start project-scoped blocked snapshot', async () => {
  const { client, Socket } = loadClient();
  await client.connect();
  const socket = Socket.instances[0];
  socket.message({ type: 'session.banner', session_id: SESSION });
  const received = [];
  client.onEvent(event => received.push(event));
  const blocked = structuredClone(card);
  blocked.snapshot.kind = 'project_scoped';
  blocked.snapshot.project_id = 'project-1';
  const item = blocked.snapshot.cards[0];
  item.project_id = 'project-1';
  item.status = 'unknown';
  item.source_refs = [{ kind: 'project', id: 'project-1' }];
  const requirement = item.requirements[0];
  requirement.project_id = 'project-1';
  requirement.status = 'blocked';
  requirement.source_ref = { kind: 'project', id: 'project-1' };
  requirement.current_result_missing = false;
  requirement.current_run_id = null;
  requirement.latest_result_is_current = null;
  requirement.result_count = 1;
  requirement.latest_result = { session_id: SESSION, project_id: 'project-1', item_id: item.item_id,
    requirement_id: requirement.requirement_id, result_id: 'prestart-block-result', run_id: 'prestart-block-run',
    status: 'blocked', summary: 'Blocked before worker start.', verification_status: 'not_verified', sources: [] };
  blocked.snapshot.result_count = 1;
  const presentation = '12345678-1234-4123-8123-000000000001';
  const event = { type: 'work-card.present', session_id: SESSION, presentation_id: presentation,
    card_id: CARD_ID, card: blocked };
  socket.message(event);
  assert.deepEqual(received.filter(row => row.type === 'work-card.present'), [event]);

  const invalid = [];
  const mutate = fn => { const value = structuredClone(blocked); fn(value.snapshot.cards[0], value.snapshot.cards[0].requirements[0], value.snapshot.cards[0].requirements[0].latest_result); invalid.push(value); };
  mutate((_i, r) => { r.current_run_id = 'unexpected-run'; });
  mutate((_i, r) => { r.latest_result_is_current = true; });
  mutate((i) => { i.status = 'blocked'; });
  mutate((_i, r) => { r.status = 'unknown'; });
  mutate((_i, _r, result) => { result.session_id = NEXT_SESSION; });
  mutate((_i, _r, result) => { result.item_id = 'foreign-item'; });
  mutate((_i, _r, result) => { result.requirement_id = 'foreign-requirement'; });
  mutate((_i, _r, result) => { result.project_id = 'foreign-project'; });
  mutate((_i, _r, result) => { result.status = 'unknown'; });
  mutate((_i, _r, result) => { result.verification_status = 'passed'; });
  mutate((_i, r) => { r.project_id = 'foreign-project'; });
  mutate((i) => { i.project_id = 'foreign-project'; });
  mutate((_i, r) => { r.source_ref.id = 'foreign-project'; });
  mutate((i) => { i.session_id = NEXT_SESSION; });
  mutate((_i, r) => { r.session_id = NEXT_SESSION; });
  mutate((_i, r) => { r.item_id = 'foreign-item'; });
  for (let index = 0; index < invalid.length; index += 1) {
    const id = `12345678-1234-4123-8123-${String(index + 2).padStart(12, '0')}`;
    socket.message({ ...event, presentation_id: id, card: invalid[index] });
  }
  socket.message({ ...event, session_id: NEXT_SESSION,
    presentation_id: '12345678-1234-4123-8123-000000000099' });
  assert.deepEqual(received.filter(row => row.type === 'work-card.present'), [event]);
  assert.deepEqual(socket.sent.filter(row => row.type.startsWith('work-card.')), []);
  client.destroy();
});

test('project-scoped pending without a result remains identity-bound at WS and presentation layers', async () => {
  const {client,Socket}=loadClient(); await client.connect();
  const socket=Socket.instances[0]; socket.message({type:'session.banner',session_id:SESSION});
  const events=[]; client.onEvent(e=>events.push(e));
  const scoped=structuredClone(card); scoped.snapshot.kind='project_scoped';
  const item=scoped.snapshot.cards[0]; item.status='unknown';item.source_refs=[{kind:'project',id:'project-1'}];
  const req=item.requirements[0];Object.assign(req,{project_id:'project-1',status:'pending',source_ref:{kind:'project',id:'project-1'},latest_result:null,result_count:0,omitted_result_count:0,current_result_missing:false,current_run_id:null,latest_result_is_current:null});
  scoped.snapshot.result_count=0;
  const event={type:'work-card.present',session_id:SESSION,presentation_id:'a0000000-0000-4000-8000-000000000001',card_id:CARD_ID,card:scoped};
  const mod={};const url=new URL('../src/shells/unified/work-card-presentation.ts',import.meta.url);
  new Function('exports',ts.transpileModule(readFileSync(url,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)(mod);
  const registry=new mod.WorkCardPresentationRegistry(SESSION);
  socket.message(event);assert.equal(events.filter(e=>e.type==='work-card.present').length,1);
  assert.equal(registry.present(event).kind,'added');
  const invalid=[];
  const mutate=fn=>{const x=structuredClone(scoped);fn(x.snapshot.cards[0],x.snapshot.cards[0].requirements[0]);invalid.push(x)};
  mutate((_i,r)=>r.project_id='other-project');
  mutate((_i,r)=>r.source_ref.id='other-project');
  mutate((i)=>i.source_refs[0].id='other-project');
  mutate((_i,r)=>r.session_id=NEXT_SESSION);
  mutate((_i,r)=>r.item_id='other-item');
  mutate((_i,r)=>r.current_result_missing=true);
  mutate((_i,r)=>r.current_run_id='forged-run');
  mutate((_i,r)=>r.latest_result_is_current=true);
  mutate((_i,r)=>r.omitted_result_count=1);
  mutate((_i,r)=>r.status='completed');
  mutate((_i,r)=>r.latest_result={});
  for(let index=0;index<invalid.length;index++){
    const bad={...event,presentation_id:`a0000000-0000-4000-8000-${String(index+2).padStart(12,'0')}`,card:invalid[index]};
    socket.message(bad);assert.equal(registry.present(bad).kind,'rejected');
  }
  assert.equal(events.filter(e=>e.type==='work-card.present').length,1);
  const rerun=structuredClone(scoped);const row=rerun.snapshot.cards[0].requirements[0];
  Object.assign(row,{status:'in_progress',current_run_id:'new-run',latest_result_is_current:false,result_count:1,omitted_result_count:1});
  Object.assign(rerun.snapshot,{result_count:1,omitted_result_count:1,history_complete:false});
  const validRerun={...event,presentation_id:'a0000000-0000-4000-8000-000000000100',card:rerun};
  socket.message(validRerun);assert.equal(registry.present(validRerun).kind,'added');
  for (const [i,change] of [
    r=>{r.latest_result_is_current=true}, r=>{r.current_run_id=null},
    r=>{r.omitted_result_count=0}, r=>{r.source_ref.id='other-project'},
  ].entries()) {
    const invalidRerun=structuredClone(rerun);change(invalidRerun.snapshot.cards[0].requirements[0]);
    const bad={...event,presentation_id:`a0000000-0000-4000-8000-${String(i+101).padStart(12,'0')}`,card:invalidRerun};
    socket.message(bad);assert.equal(registry.present(bad).kind,'rejected');
  }
  assert.equal(events.filter(e=>e.type==='work-card.present').length,2);
  assert.deepEqual(socket.sent.filter(e=>e.type.startsWith('work-card.')),[]);
  client.destroy();
});
