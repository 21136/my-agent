import assert from "node:assert/strict";
import { appendFileSync, mkdtempSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { test } from "node:test";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);
const { JournalStore, beginOperation } = require("../../../artifacts/evolution-control-build/journal.js");
const { BindingStore } = require("../../../artifacts/evolution-control-build/binding.js");
const { parseControlResult } = require("../../../artifacts/evolution-control-build/contracts.js");
const { GrantStore } = require("../../../artifacts/evolution-control-build/authorization.js");
const uuid = (digit) => `${digit.repeat(8)}-${digit.repeat(4)}-4${digit.repeat(3)}-8${digit.repeat(3)}-${digit.repeat(12)}`;
function root() {
  const run = process.env.EVOLUTION_TEST_ROOT;
  assert.ok(run, "approved EVOLUTION_TEST_ROOT is required");
  assert.equal(readFileSync(path.join(run, ".evolution-test-run"), "utf8").trim(), "evolution-a");
  return mkdtempSync(path.join(run, "evo-journal-"));
}
function fixture() {
  const rootPath = root();
  const journal = new JournalStore(rootPath);
  const bindings = new BindingStore(journal);
  const binding = bindings.bind({ conversation_id: uuid("3"), scope_id: uuid("6"), host_instance_id: uuid("4") });
  const grants = new GrantStore(journal);
  const authorization_id = grants.createConfirmed({
    binding, kind: "candidate.build",
    constraints: { source_snapshot_id: `sha256:${"a".repeat(64)}`, build_input_digest: `sha256:${"b".repeat(64)}`, isolation_mode: "local_managed" },
    confirmation_id: uuid("5"), expires_at: "2099-01-01T00:00:00.000Z",
  });
  const request = {
    protocol: "evolution/1.0", operation_id: uuid("1"), evolution_id: binding.evolution_id,
    conversation_id: binding.conversation_id, host_run_id: uuid("4"), expected_state_revision: bindings.require(binding.evolution_id, binding.conversation_id).state_revision,
    authorization_id, action: "build",
    payload: { source_snapshot_id: `sha256:${"a".repeat(64)}`, descriptor_artifact_id: uuid("6"), build_input_digest: `sha256:${"b".repeat(64)}`, isolation_mode: "local_managed" },
  };
  return { rootPath, journal, bindings, grants, binding, request };
}

test("same operation id is idempotent and a changed payload is rejected", () => {
  const { journal, bindings, grants, binding, request } = fixture();
  const intent = beginOperation(request, { journal, bindings, grants });
  assert.equal(intent.effect, "not_started");
  const bytes = readFileSync(path.join(journal.root, "journal.jsonl"), "utf8");
  const replay = beginOperation(request, { journal, bindings, grants });
  assert.equal(replay.operation_id, request.operation_id);
  assert.equal(replay.status, "unknown");
  assert.equal(readFileSync(path.join(journal.root, "journal.jsonl"), "utf8"), bytes);
  const changed = { ...request, payload: { ...request.payload, build_input_digest: `sha256:${"c".repeat(64)}` } };
  assert.throws(() => beginOperation(changed, { journal, bindings, grants }), (error) => error.code === "identity_mismatch");
  journal.close();
});

test("binding preserves native legacy conversation ids while generating a UUID evolution id", () => {
  const journal = new JournalStore(root());
  const bindings = new BindingStore(journal);
  const conversation_id = "20261026-1a2b3c4d";
  const binding = bindings.bind({ conversation_id, scope_id: uuid("6"), host_instance_id: uuid("4") });
  assert.equal(binding.conversation_id, conversation_id);
  assert.match(binding.evolution_id, /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.deepEqual(bindings.findForConversation(conversation_id), binding);
  assert.throws(() => bindings.findForConversation("20260230-1a2b3c4d"), error => error.code === "invalid_request");
});

test("a truncated tail is preserved, quarantined and never replayed", () => {
  const { rootPath, journal, bindings, binding, request } = fixture();
  const currentBinding = bindings.require(binding.evolution_id, binding.conversation_id);
  const current = { ...currentBinding, state_revision: currentBinding.state_revision + 1 };
  journal.begin(request, current);
  journal.markEffect(request.operation_id, "started");
  const retry = { ...request, operation_id: uuid("7"), expected_state_revision: bindings.require(binding.evolution_id, binding.conversation_id).state_revision };
  assert.throws(() => beginOperation(retry, { journal, bindings, grants: new GrantStore(journal) }), (error) => error.code === "outcome_unknown");
  journal.close();
  const journalPath = path.join(rootPath, "journal.jsonl");
  appendFileSync(journalPath, '{"partial":', "utf8");
  const before = readFileSync(journalPath);
  const recovered = new JournalStore(path.dirname(journalPath));
  const state = recovered.recover();
  assert.equal(state.corrupt_tail, true);
  assert.ok(state.quarantined_tail_path);
  assert.deepEqual(state.unknown_operation_ids, [request.operation_id]);
  assert.deepEqual(readFileSync(journalPath), before, "recovery must not truncate the authoritative log");
  assert.throws(() => recovered.begin({ ...request, operation_id: uuid("7") }, { ...current, state_revision: current.state_revision + 1 }), (error) => error.code === "recovery_required");
  recovered.close();
});



test("only one journal writer may own a root in a process", () => {
  const directory = root();
  const first = new JournalStore(directory);
  assert.throws(() => new JournalStore(directory), (error) => error.code === "scope_conflict");
  first.close();
  const next = new JournalStore(directory);
  next.close();
});


test("restart reconstructs bindings, grants and unknown effects without replay", () => {
  const { rootPath, journal, bindings, grants, binding, request } = fixture();
  beginOperation(request, { journal, bindings, grants });
  journal.markEffect(request.operation_id, "started");
  const before = readFileSync(path.join(rootPath, "journal.jsonl"));
  journal.close();
  const restarted = new JournalStore(rootPath);
  const restartedBindings = new BindingStore(restarted);
  const restartedGrants = new GrantStore(restarted);
  const projection = restartedBindings.require(binding.evolution_id, binding.conversation_id);
  assert.equal(projection.state_revision, request.expected_state_revision + 1);
  assert.equal(restarted.get(request.operation_id).effect, "started");
  assert.deepEqual(restarted.recover().unknown_operation_ids, [request.operation_id]);
  const retry = { ...request, operation_id: uuid("8"), expected_state_revision: projection.state_revision };
  assert.throws(() => beginOperation(retry, { journal: restarted, bindings: restartedBindings, grants: restartedGrants }), (error) => error.code === "outcome_unknown");
  assert.deepEqual(readFileSync(path.join(rootPath, "journal.jsonl")), before, "recovery must not append a replay");
  restarted.close();
});

test("terminal results survive restart and do not remain unknown", () => {
  const { rootPath, journal, bindings, grants, request } = fixture();
  beginOperation(request, { journal, bindings, grants });
  const result = parseControlResult({ operation_id:request.operation_id,evolution_id:request.evolution_id,state_revision:request.expected_state_revision+1,status:"succeeded",result:{build_id:uuid("8")},error:null });
  journal.finish(result);
  journal.close();
  const restarted = new JournalStore(rootPath);
  assert.equal(restarted.get(request.operation_id).effect, "terminal");
  assert.equal(restarted.get(request.operation_id).result.status, "succeeded");
  assert.deepEqual(restarted.recover().unknown_operation_ids, []);
  restarted.close();
});


test("a second operating-system process cannot acquire the same journal writer lease", () => {
  const directory=root();
  const journal=new JournalStore(directory);
  const modulePath=path.resolve("artifacts/evolution-control-build/journal.js");
  const source=`const {JournalStore}=require(${JSON.stringify(modulePath)});try{new JournalStore(${JSON.stringify(directory)});process.exit(5)}catch(error){if(error.code!=="scope_conflict")process.exit(6);process.exit(0)}`;
  const env={};
  for(const key of ["SystemRoot","WINDIR","PATH","TEMP","TMP"])if(process.env[key])env[key]=process.env[key];
  const child=spawnSync(process.execPath,["-e",source],{cwd:process.cwd(),env,encoding:"utf8",windowsHide:true,timeout:10000});
  assert.equal(child.status,0,child.stderr);
  journal.close();
});


test("a dead supervisor lock is quarantined before journal recovery",()=>{
  const directory=root();const lock=path.join(directory,"journal.jsonl.writer-lock");
  const source=`const fs=require("node:fs");const fd=fs.openSync(${JSON.stringify(lock)},"wx");fs.writeFileSync(fd,JSON.stringify({owner_token:"stale-owner",pid:process.pid}));fs.closeSync(fd)`;
  const env={};for(const key of ["SystemRoot","WINDIR","PATH","TEMP","TMP"])if(process.env[key])env[key]=process.env[key];
  const child=spawnSync(process.execPath,["-e",source],{cwd:process.cwd(),env,encoding:"utf8",windowsHide:true,timeout:10000});
  assert.equal(child.status,0,child.stderr);
  const journal=new JournalStore(directory);
  assert.ok(require("node:fs").readdirSync(directory).some(name=>name.startsWith("journal.jsonl.writer-lock.stale-")));
  assert.equal(journal.recover().corrupt_tail,false);
  journal.close();
});


test("terminal unknown result still freezes new side effects for that evolution",()=>{
  const {journal,bindings,grants,binding,request}=fixture();
  const intent=beginOperation(request,{journal,bindings,grants});
  const unknown={operation_id:request.operation_id,evolution_id:request.evolution_id,state_revision:request.expected_state_revision+1,status:"unknown",result:null,error:{code:"outcome_unknown",message:"completion was not observed",evidence_ids:[],retryable:false}};
  journal.finish(unknown);assert.deepEqual(journal.recover().unknown_operation_ids,[request.operation_id]);
  const current=bindings.require(binding.evolution_id,binding.conversation_id);const retry={...request,operation_id:uuid("8"),expected_state_revision:current.state_revision};
  assert.throws(()=>beginOperation(retry,{journal,bindings,grants}),(error)=>error.code==="outcome_unknown");
  assert.equal(journal.get(request.operation_id).result.status,"unknown");journal.close();
});
