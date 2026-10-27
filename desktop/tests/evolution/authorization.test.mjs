import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
import { test } from "node:test";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);
const { JournalStore, beginOperation } = require("../../../artifacts/evolution-control-build/journal.js");
const { BindingStore } = require("../../../artifacts/evolution-control-build/binding.js");
const { GrantStore } = require("../../../artifacts/evolution-control-build/authorization.js");
const uuid = (digit) => `${digit.repeat(8)}-${digit.repeat(4)}-4${digit.repeat(3)}-8${digit.repeat(3)}-${digit.repeat(12)}`;
function create() {
  const run = process.env.EVOLUTION_TEST_ROOT;
  assert.ok(run, "approved EVOLUTION_TEST_ROOT is required");
  assert.equal(readFileSync(path.join(run, ".evolution-test-run"), "utf8").trim(), "evolution-a");
  const root = mkdtempSync(path.join(run, "evo-auth-"));
  const journal = new JournalStore(root);
  const bindings = new BindingStore(journal);
  const binding = bindings.bind({ conversation_id: uuid("3"), scope_id: uuid("6"), host_instance_id: uuid("4") });
  return { root, journal, bindings, binding, grants: new GrantStore(journal) };
}

test("a write lease prevents another conversation binding the same scope", () => {
  const { journal, bindings, binding } = create();
  assert.throws(() => bindings.bind({ conversation_id: uuid("7"), scope_id: binding.scope_id, host_instance_id: uuid("8") }), (error) => error.code === "permission_denied");
  bindings.freeze(binding.evolution_id);
  assert.throws(() => bindings.bind({ conversation_id: uuid("7"), scope_id: binding.scope_id, host_instance_id: uuid("8") }), (error) => error.code === "permission_denied");
  journal.close();
});

test("revocation blocks new build but still permits stopping its exact owned instance", () => {
  const { journal, bindings, binding, grants } = create();
  const candidate = `sha256:${"a".repeat(64)}`;
  const auth = grants.createConfirmed({ binding, kind: "candidate.launch", constraints: { candidate_id: candidate, profile_fixture_id: uuid("6"), isolation_mode: "local_managed" }, confirmation_id: uuid("5"), expires_at: "2099-01-01T00:00:00.000Z" });
  const instance = uuid("7");
  const stopAuthorization = grants.bindInstance(auth, instance);
  const current = bindings.require(binding.evolution_id, binding.conversation_id);
  const launch = { protocol:"evolution/1.0",operation_id:uuid("1"),evolution_id:binding.evolution_id,conversation_id:binding.conversation_id,host_run_id:uuid("4"),expected_state_revision:current.state_revision,authorization_id:auth,action:"launch",payload:{candidate_id:candidate,profile_fixture_id:uuid("6")} };
  grants.revoke(binding.evolution_id);
  assert.throws(() => beginOperation(launch,{journal,bindings,grants}), (error) => error.code === "permission_denied");
  const stopBinding=bindings.require(binding.evolution_id,binding.conversation_id);
  const stop={...launch,operation_id:uuid("2"),expected_state_revision:stopBinding.state_revision,authorization_id:stopAuthorization,action:"stop",payload:{instance_id:instance}};
  assert.doesNotThrow(() => grants.require(stop,stopBinding));
  const foreign={...stop,payload:{instance_id:uuid("8")}};
  assert.throws(() => grants.require(foreign,stopBinding),(error)=>error.code === "permission_denied");
  journal.close();
});

test("expired grants cannot authorize new side effects", () => {
  const { journal, bindings, binding } = create();
  let now = Date.parse("2028-01-01T00:00:00.000Z");
  const grants = new GrantStore(journal, () => now);
  const auth = grants.createConfirmed({ binding, kind:"source.patch", constraints:{owned_paths:["main.cjs"],expected_before_hashes:{"main.cjs":"missing"}},confirmation_id:uuid("5"),expires_at:"2029-01-01T00:00:00.000Z" });
  now = Date.parse("2030-01-01T00:00:00.000Z");
  const current=bindings.require(binding.evolution_id,binding.conversation_id);
  const request={protocol:"evolution/1.0",operation_id:uuid("1"),evolution_id:binding.evolution_id,conversation_id:binding.conversation_id,host_run_id:uuid("4"),expected_state_revision:current.state_revision,authorization_id:auth,action:"patch",payload:{path:"main.cjs",expected_before_hash:"missing",content_utf8:"x"}};
  assert.throws(()=>grants.require(request,current),(error)=>error.code==="permission_denied");
  journal.close();
});


test("a human confirmation id cannot be replayed to mint another grant", () => {
  const { journal, binding, grants } = create();
  const grant = { binding, kind:"source.patch", constraints:{owned_paths:["main.cjs"],expected_before_hashes:{"main.cjs":"missing"}}, confirmation_id:uuid("5"), expires_at:"2099-01-01T00:00:00.000Z" };
  grants.createConfirmed(grant);
  assert.throws(() => grants.createConfirmed({...grant,binding}), (error)=>error.code==="identity_mismatch");
  journal.close();
});

test("a completed authorized launch can derive its exact stop grant after parent expiry or revocation",()=>{
  const {journal,bindings,binding}=create();let now=Date.parse("2028-01-01T00:00:00.000Z");const grants=new GrantStore(journal,()=>now);const candidate=`sha256:${"b".repeat(64)}`;
  const parent=grants.createConfirmed({binding,kind:"candidate.launch",constraints:{candidate_id:candidate,profile_fixture_id:uuid("6"),isolation_mode:"local_managed"},confirmation_id:uuid("5"),expires_at:"2029-01-01T00:00:00.000Z"});
  now=Date.parse("2030-01-01T00:00:00.000Z");grants.revoke(binding.evolution_id);
  const instance=uuid("7");const stopGrant=grants.bindInstance(parent,instance);const current=bindings.require(binding.evolution_id,binding.conversation_id);
  const stop={protocol:"evolution/1.0",operation_id:uuid("1"),evolution_id:binding.evolution_id,conversation_id:binding.conversation_id,host_run_id:uuid("4"),expected_state_revision:current.state_revision,authorization_id:stopGrant,action:"stop",payload:{instance_id:instance}};
  assert.doesNotThrow(()=>grants.require(stop,current));journal.close();
});
