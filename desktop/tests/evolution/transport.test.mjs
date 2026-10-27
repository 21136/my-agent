import assert from "node:assert/strict";
import {test} from "node:test";
import {createRequire} from "node:module";
const require=createRequire(import.meta.url);let api={};try{api=require("../../../artifacts/evolution-control-build/transport.js");}catch{/* RED until T6 transport is added. */}
function exported(name){assert.equal(typeof api[name],"function",`transport must export ${name}`);return api[name];}

test("transport negotiates the highest common minor and fails closed without a common major",()=>{
  const negotiate=exported("negotiateVersion");assert.deepEqual(negotiate({supported_major:1,supported_minor:[0,1,2]},{supported_major:1,supported_minor:[0,2]}),{major:1,minor:2});
  assert.throws(()=>negotiate({supported_major:2,supported_minor:[0]},{supported_major:1,supported_minor:[9]}),error=>error.code==="protocol_incompatible");
});

test("host token comparison rejects mismatches and malformed lengths",()=>{
  const same=exported("constantTimeTokenEquals");const token="a".repeat(64);assert.equal(same(token,token),true);assert.equal(same(token,"b".repeat(64)),false);assert.equal(same(token,"a"),false);
});

test("transport uncertainty preserves operation identity and never allocates a retry id",()=>{
  const unknown=exported("unknownControlResult");const request={protocol:"evolution/1.0",operation_id:"11111111-1111-4111-8111-111111111111",evolution_id:"22222222-2222-4222-8222-222222222222",conversation_id:"33333333-3333-4333-8333-333333333333",host_run_id:"44444444-4444-4444-8444-444444444444",expected_state_revision:7,authorization_id:"55555555-5555-4555-8555-555555555555",action:"inspect",payload:{query:"summary"}};
  const result=unknown(request,"timeout");assert.equal(result.status,"unknown");assert.equal(result.error.code,"outcome_unknown");assert.equal(result.operation_id,request.operation_id);assert.equal(result.evolution_id,request.evolution_id);assert.equal(result.state_revision,request.expected_state_revision);
});

test("trusted entry modules are importable without starting a host or helper",()=>{
  const serverEntry=require("../../../artifacts/evolution-control-build/supervisor-entry.js");const clientEntry=require("../../../artifacts/evolution-control-build/control-client-entry.js");
  assert.equal(typeof serverEntry.serveSupervisor,"function");assert.equal(typeof clientEntry.runControlClientEntry,"function");
});

test("hello authentication requires exact host, release, schema, version and token",()=>{
  const authenticate=exported("authenticateHello");const bootstrap={pipe_name:"\\\\.\\pipe\\test",host_instance_id:"66666666-6666-4666-8666-666666666666",host_token:"a".repeat(64),control_release:{release_id:"sha256:"+"b".repeat(64)}};
  const hello={type:"hello",supported_major:1,supported_minor:[0],host_instance_id:bootstrap.host_instance_id,schema_digest:api.CONTROL_SCHEMA_DIGEST,release_id:bootstrap.control_release.release_id,host_token:bootstrap.host_token};
  assert.deepEqual(authenticate(hello,bootstrap),{major:1,minor:0,schema_digest:hello.schema_digest,release_id:hello.release_id});
  assert.equal(authenticate({...hello,host_token:"d".repeat(64)},bootstrap),null);assert.equal(authenticate({...hello,extra:"reject"},bootstrap),null);
});

test("invalid request timeout is rejected before opening the private pipe",async()=>{
  const requestControl=exported("requestControl");const request={protocol:"evolution/1.0",operation_id:"11111111-1111-4111-8111-111111111111",evolution_id:"22222222-2222-4222-8222-222222222222",conversation_id:"33333333-3333-4333-8333-333333333333",host_run_id:"44444444-4444-4444-8444-444444444444",expected_state_revision:0,authorization_id:"55555555-5555-4555-8555-555555555555",action:"inspect",payload:{query:"summary"}};
  const bootstrap={pipe_name:"\\\\.\\pipe\\t6-test",host_instance_id:"66666666-6666-4666-8666-666666666666",host_token:"a".repeat(64),control_release:{release_id:"sha256:"+"b".repeat(64)}};
  await assert.rejects(()=>requestControl({bootstrap,request,timeoutMs:0}),error=>error.code==="invalid_request");
});

test("trusted binding is a separate host-bootstrap message, never a ControlRequest action",async()=>{
  const parse=exported("parseTrustedHostBindingRequest");
  const dispatch=exported("dispatchTrustedHostMessage");
  const request={type:"host.bind",conversation_id:"33333333-3333-4333-8333-333333333333",scope_id:"55555555-5555-4555-8555-555555555555",confirmation_id:"77777777-7777-4777-8777-777777777777",expires_at:"2099-01-01T00:00:00.000Z"};
  assert.deepEqual(parse(request,[request.scope_id]),request);
  assert.throws(()=>parse({...request,extra:"reject"},[request.scope_id]),error=>error.code==="invalid_request");
  assert.throws(()=>parse(request,["66666666-6666-4666-8666-666666666666"]),error=>error.code==="permission_denied");
  const bootstrap={host_instance_id:"44444444-4444-4444-8444-444444444444",allowed_scope_ids:[request.scope_id]};
  let received=null;
  const supervisor={bindFromTrustedHost:async input=>{received=input;return {evolution_id:"66666666-6666-4666-8666-666666666666",authorization_id:"88888888-8888-4888-8888-888888888888",scope_id:request.scope_id,workspace_revision:3};}};
  const result=await dispatch(request,bootstrap,supervisor);
  assert.deepEqual(received,{...request,host_instance_id:bootstrap.host_instance_id,allowed_scope_ids:bootstrap.allowed_scope_ids});
  assert.deepEqual(result,{type:"host.bind.result",confirmation_id:request.confirmation_id,status:"succeeded",result:{evolution_id:"66666666-6666-4666-8666-666666666666",authorization_id:"88888888-8888-4888-8888-888888888888",scope_id:request.scope_id,workspace_revision:3},error:null});
  const {parseControlRequest}=require("../../../artifacts/evolution-control-build/contracts.js");
  assert.throws(()=>parseControlRequest({...request,action:"begin"}),error=>error.code==="invalid_request");
});

test("trusted binding accepts only the exact native legacy conversation id form",()=>{
  const parse=exported("parseTrustedHostBindingRequest");
  const request={type:"host.bind",conversation_id:"20261026-1a2b3c4d",scope_id:"55555555-5555-4555-8555-555555555555",confirmation_id:"77777777-7777-4777-8777-777777777777",expires_at:"2099-01-01T00:00:00.000Z"};
  assert.deepEqual(parse(request,[request.scope_id]),request);
  assert.throws(()=>parse({...request,conversation_id:"20260230-1a2b3c4d"},[request.scope_id]),error=>error.code==="invalid_request");
  assert.throws(()=>parse({...request,scope_id:"20261026-1a2b3c4d"},["20261026-1a2b3c4d"]),error=>error.code==="invalid_request");
});

test("trusted binding response must match the submitted confirmation and scope",()=>{
  const decode=exported("decodeTrustedHostBindingResult");
  const request={type:"host.bind",conversation_id:"33333333-3333-4333-8333-333333333333",scope_id:"55555555-5555-4555-8555-555555555555",confirmation_id:"77777777-7777-4777-8777-777777777777",expires_at:"2099-01-01T00:00:00.000Z"};
  const response={type:"host.bind.result",confirmation_id:request.confirmation_id,status:"succeeded",result:{evolution_id:"66666666-6666-4666-8666-666666666666",authorization_id:"88888888-8888-4888-8888-888888888888",scope_id:request.scope_id,workspace_revision:3},error:null};
  assert.deepEqual(decode(response,request),response);
  assert.throws(()=>decode({...response,confirmation_id:"99999999-9999-4999-8999-999999999999"},request),error=>error.code==="identity_mismatch");
  assert.throws(()=>decode({...response,result:{...response.result,scope_id:"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}},request),error=>error.code==="identity_mismatch");
});

test("fixed control helper accepts a separately typed host binding command without treating it as a control action",()=>{
  const {validateControlClientInput}=require("../../../artifacts/evolution-control-build/control-client-entry.js");
  assert.equal(typeof validateControlClientInput,"function");
  const bootstrap={pipe_name:"\\\\.\\pipe\\trusted",host_instance_id:"44444444-4444-4444-8444-444444444444",host_token:"a".repeat(64),control_release:{release_id:"sha256:"+"b".repeat(64),root:"F:/release",node_executable:"F:/node.exe",python_executable:"F:/python.exe",manifest_path:"F:/release/manifest.json",manifest_digest:"sha256:"+"c".repeat(64)}};
  const request={type:"host.bind",conversation_id:"33333333-3333-4333-8333-333333333333",scope_id:"55555555-5555-4555-8555-555555555555",confirmation_id:"77777777-7777-4777-8777-777777777777",expires_at:"2099-01-01T00:00:00.000Z"};
  const parsed=validateControlClientInput({bootstrap,request,timeout_ms:30000});
  assert.equal(parsed.request.type,"host.bind");assert.equal(parsed.request.scope_id,request.scope_id);
  assert.throws(()=>validateControlClientInput({bootstrap,request:{...request,extra:"reject"},timeout_ms:30000}),error=>error.code==="invalid_request");
});

test("typed action grants and stop recovery stay inside the authenticated Host channel",async()=>{
  const parseGrant=exported("parseTrustedHostAuthorizationRequest");
  const dispatch=exported("dispatchTrustedHostMessage");
  const scopeId="55555555-5555-4555-8555-555555555555";
  const bootstrap={host_instance_id:"44444444-4444-4444-8444-444444444444",allowed_scope_ids:[scopeId]};
  const grant={type:"host.authorize",conversation_id:"20261026-1a2b3c4d",evolution_id:"66666666-6666-4666-8666-666666666666",kind:"candidate.build",constraints:{source_snapshot_id:"sha256:"+"a".repeat(64),build_input_digest:"sha256:"+"b".repeat(64),isolation_mode:"local_managed"},confirmation_id:"77777777-7777-4777-8777-777777777777",expires_at:"2099-01-01T00:00:00.000Z"};
  assert.deepEqual(parseGrant(grant,bootstrap.allowed_scope_ids),grant);
  assert.throws(()=>parseGrant({...grant,token:"renderer"},bootstrap.allowed_scope_ids),error=>error.code==="invalid_request");
  let authorized=null;let recovered=null;
  const supervisor={authorizeFromTrustedHost:async input=>{authorized=input;return{evolution_id:grant.evolution_id,authorization_id:"88888888-8888-4888-8888-888888888888",kind:grant.kind}},resolveStopAuthorizationFromTrustedHost:input=>{recovered=input;return{evolution_id:input.evolution_id,instance_id:input.instance_id,authorization_id:"99999999-9999-4999-8999-999999999999"}}};
  const grantResult=await dispatch(grant,bootstrap,supervisor);
  assert.equal(grantResult.type,"host.authorize.result");assert.equal(grantResult.result.authorization_id,"88888888-8888-4888-8888-888888888888");
  const stopRequest={type:"host.resolve-stop",conversation_id:grant.conversation_id,evolution_id:grant.evolution_id,instance_id:"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"};
  assert.deepEqual(exported("parseTrustedHostResolveStopRequest")(stopRequest),stopRequest);
  const stopResult=await dispatch(stopRequest,bootstrap,supervisor);
  assert.equal(stopResult.type,"host.resolve-stop.result");assert.equal(stopResult.result.authorization_id,"99999999-9999-4999-8999-999999999999");
  assert.equal(authorized.host_instance_id,bootstrap.host_instance_id);assert.deepEqual(authorized.allowed_scope_ids,bootstrap.allowed_scope_ids);
  assert.equal(recovered.host_instance_id,bootstrap.host_instance_id);assert.deepEqual(recovered.allowed_scope_ids,bootstrap.allowed_scope_ids);
});

test("fixed helper accepts only typed host.authorize and host.resolve-stop frames",()=>{
  const {validateControlClientInput}=require("../../../artifacts/evolution-control-build/control-client-entry.js");
  const bootstrap={pipe_name:"\\\\.\\pipe\\trusted",host_instance_id:"44444444-4444-4444-8444-444444444444",host_token:"a".repeat(64),control_release:{release_id:"sha256:"+"b".repeat(64),root:"F:/release",node_executable:"F:/node.exe",python_executable:"F:/python.exe",manifest_path:"F:/release/manifest.json",manifest_digest:"sha256:"+"c".repeat(64)}};
  const common={conversation_id:"33333333-3333-4333-8333-333333333333",evolution_id:"66666666-6666-4666-8666-666666666666"};
  const grant={type:"host.authorize",...common,kind:"candidate.build",constraints:{source_snapshot_id:"sha256:"+"a".repeat(64),build_input_digest:"sha256:"+"b".repeat(64),isolation_mode:"local_managed"},confirmation_id:"77777777-7777-4777-8777-777777777777",expires_at:"2099-01-01T00:00:00.000Z"};
  assert.equal(validateControlClientInput({bootstrap,request:grant,timeout_ms:30000}).request.type,"host.authorize");
  const stop={type:"host.resolve-stop",...common,instance_id:"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"};
  assert.equal(validateControlClientInput({bootstrap,request:stop,timeout_ms:30000}).request.type,"host.resolve-stop");
  assert.throws(()=>validateControlClientInput({bootstrap,request:{...stop,extra:"reject"},timeout_ms:30000}),error=>error.code==="invalid_request");
});

test("T9 stale-ready injection is accepted only by the fixed T4 Host and returns the unchanged current projection",async()=>{
  const parse=exported("parseT9LateReadyRequest");const dispatch=exported("dispatchTrustedHostMessage");
  const request={type:"host.t9-late-ready",conversation_id:"33333333-3333-4333-8333-333333333333",evolution_id:"66666666-6666-4666-8666-666666666666",candidate_id:"sha256:"+"a".repeat(64),instance_id:"88888888-8888-4888-8888-888888888888",instance_sequence:1};
  assert.deepEqual(parse(request),request);assert.throws(()=>parse({...request,instance_sequence:0}),error=>error.code==="invalid_request");
  const bootstrap={host_instance_id:"44444444-4444-4444-8444-444444444444",allowed_scope_ids:["55555555-5555-4555-8555-555555555555"]};let injected=null;
  const supervisor={deps:{descriptorFactories:[{kind:"t4-recovery-node-v1"}],bindings:{require:()=>({host_instance_id:bootstrap.host_instance_id,scope_id:bootstrap.allowed_scope_ids[0]})}},acceptRuntimeEvent:event=>{injected=event;return false;},project:()=>({conversation_id:request.conversation_id,current_candidate_id:"sha256:"+"b".repeat(64),current_instance_id:"99999999-9999-4999-8999-999999999999",late_event_count:1})};
  const result=await dispatch(request,bootstrap,supervisor);
  assert.equal(injected.origin,"candidate_reported");assert.equal(injected.instance_id,request.instance_id);
  assert.equal(result.type,"host.t9-late-ready.result");assert.equal(result.accepted,false);assert.equal(result.projection.current_instance_id,supervisor.project().current_instance_id);
  const ordinaryHost={...supervisor,deps:{...supervisor.deps,descriptorFactories:[]}};
  await assert.rejects(()=>dispatch(request,bootstrap,ordinaryHost),error=>error.code==="permission_denied");
});

test("fixed helper routes only the exact private T9 stale-ready frame",()=>{
  const {validateControlClientInput}=require("../../../artifacts/evolution-control-build/control-client-entry.js");
  const bootstrap={pipe_name:"\\\\.\\pipe\\trusted",host_instance_id:"44444444-4444-4444-8444-444444444444",host_token:"a".repeat(64),control_release:{release_id:"sha256:"+"b".repeat(64),root:"F:/release",node_executable:"F:/node.exe",python_executable:"F:/python.exe",manifest_path:"F:/release/manifest.json",manifest_digest:"sha256:"+"c".repeat(64)}};
  const request={type:"host.t9-late-ready",conversation_id:"33333333-3333-4333-8333-333333333333",evolution_id:"66666666-6666-4666-8666-666666666666",candidate_id:"sha256:"+"a".repeat(64),instance_id:"88888888-8888-4888-8888-888888888888",instance_sequence:1};
  assert.equal(validateControlClientInput({bootstrap,request,timeout_ms:30000}).request.type,"host.t9-late-ready");
  assert.throws(()=>validateControlClientInput({bootstrap,request:{...request,extra:true},timeout_ms:30000}),error=>error.code==="invalid_request");
});
