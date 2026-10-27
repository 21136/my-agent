import assert from "node:assert/strict";
import {createRequire} from "node:module";
import {test} from "node:test";
const require=createRequire(import.meta.url);let api={};try{api=require("../../../artifacts/evolution-control-build/transport.js");}catch{}
const uuid=d=>`${d.repeat(8)}-${d.repeat(4)}-4${d.repeat(3)}-8${d.repeat(3)}-${d.repeat(12)}`;
const hash=d=>`sha256:${d.repeat(64)}`;
const request={type:"host.authorize",conversation_id:uuid("3"),evolution_id:uuid("2"),kind:"candidate.build",constraints:{source_snapshot_id:hash("a"),build_input_digest:hash("b"),isolation_mode:"local_managed"},confirmation_id:uuid("7"),expires_at:"2099-01-01T00:00:00.000Z"};
const bootstrap={host_instance_id:uuid("4"),allowed_scope_ids:[uuid("5")]};

test("trusted Host grant accepts only exact capability constraints and the authenticated scope",async()=>{
  assert.equal(typeof api.parseTrustedHostAuthorizationRequest,"function","transport must parse typed host.authorize frames");
  assert.equal(typeof api.dispatchTrustedHostMessage,"function");
  const parsed=api.parseTrustedHostAuthorizationRequest(request,bootstrap.allowed_scope_ids);
  assert.deepEqual(parsed,request);
  assert.throws(()=>api.parseTrustedHostAuthorizationRequest({...request,token:"renderer"},bootstrap.allowed_scope_ids),/fields|invalid/i);
  assert.throws(()=>api.parseTrustedHostAuthorizationRequest({...request,kind:"model.infer"},bootstrap.allowed_scope_ids),/capability|kind|unsupported/i);
  assert.throws(()=>api.parseTrustedHostAuthorizationRequest({...request,scope_id:uuid("6")},bootstrap.allowed_scope_ids),/fields|scope|allowlist/i);
  const calls=[];const supervisor={authorizeFromTrustedHost:async input=>{calls.push(input);return{evolution_id:request.evolution_id,authorization_id:uuid("8"),kind:request.kind}}};
  const response=await api.dispatchTrustedHostMessage(request,{...bootstrap,allowed_scope_ids:[...bootstrap.allowed_scope_ids,uuid("6")]},supervisor);
  assert.equal(response.type,"host.authorize.result");assert.equal(response.confirmation_id,request.confirmation_id);assert.equal(response.status,"succeeded");
  assert.equal(calls[0].host_instance_id,bootstrap.host_instance_id);assert.deepEqual(calls[0].allowed_scope_ids,[...bootstrap.allowed_scope_ids,uuid("6")]);
});


test("trusted Host resolves stop authority only for the exact bound instance",async()=>{
  const instance=uuid("9");const request={type:"host.resolve-stop",conversation_id:uuid("3"),evolution_id:uuid("2"),instance_id:instance};
  assert.equal(typeof api.parseTrustedHostResolveStopRequest,"function","transport must parse private stop-authority recovery frames");
  const parsed=api.parseTrustedHostResolveStopRequest(request);
  assert.deepEqual(parsed,request);
  assert.throws(()=>api.parseTrustedHostResolveStopRequest({...request,authorization_id:uuid("8")}),/fields|invalid/i);
  const calls=[];const supervisor={resolveStopAuthorizationFromTrustedHost:input=>{calls.push(input);return{evolution_id:request.evolution_id,instance_id:instance,authorization_id:uuid("8")}}};
  const response=await api.dispatchTrustedHostMessage(request,{...bootstrap},supervisor);
  assert.equal(response.type,"host.resolve-stop.result");assert.equal(response.result.instance_id,instance);assert.equal(calls[0].allowed_scope_ids,bootstrap.allowed_scope_ids);
});
