import assert from "node:assert/strict";
import {test} from "node:test";
import {bindEvolutionHostRelay,normalizeEvolutionHostRequest} from "../../src/api/evolution-bridge.ts";

const conversation="33333333-3333-4333-8333-333333333333";
test("WS evolution relay normalizes only the typed current-conversation request",()=>{
  assert.deepEqual(normalizeEvolutionHostRequest({type:"evolution.begin",conversation_id:conversation}),{type:"evolution.begin",conversation_id:conversation});
  assert.throws(()=>normalizeEvolutionHostRequest({type:"evolution.begin",conversation_id:conversation,host_token:"secret"}),/field|invalid|evolution/i);
  assert.throws(()=>normalizeEvolutionHostRequest({type:"evolution.stop",conversation_id:"not-a-uuid"}),/conversation|uuid|invalid/i);
  assert.throws(()=>normalizeEvolutionHostRequest({type:"evolution.inspect",conversation_id:conversation},"44444444-4444-4444-8444-444444444444"),/current session|conversation/i);
  assert.throws(()=>normalizeEvolutionHostRequest({type:"evolution.patch",conversation_id:conversation}),/type|action|invalid/i);
});

test("WS evolution relay accepts the native date-prefixed conversation id without widening UUID fields",()=>{
  const legacyConversation="20261026-1a2b3c4d";
  assert.deepEqual(normalizeEvolutionHostRequest({type:"evolution.begin",conversation_id:legacyConversation}),{type:"evolution.begin",conversation_id:legacyConversation});
  for(const invalid of ["20260230-1a2b3c4d","２０２６１０２６-1a2b3c4d","٢٠٢٦١٠٢٦-1a2b3c4d","20261026-1a2b3c4d\n","20261026-1a2b3c4d\r\n",conversation+"\n",conversation+"\r\n"]){
    assert.throws(()=>normalizeEvolutionHostRequest({type:"evolution.begin",conversation_id:invalid}),/conversation|invalid|date/i,JSON.stringify(invalid));
  }
});

test("renderer relay forwards the fixed evolution request to the current WS client",()=>{
  const bind=bindEvolutionHostRelay;assert.equal(typeof bind,"function","bindEvolutionHostRelay must be exported");
  let receive;let removed=0;const sent=[];const failed=[];
  const cleanup=bind({desktop:{onEvolutionRequest:handler=>{receive=handler;return()=>{removed+=1;};}},client:{sendEvolutionRequest:request=>sent.push(request)},onError:error=>failed.push(String(error))});
  receive({type:"evolution.inspect",conversation_id:conversation});
  assert.deepEqual(sent,[{type:"evolution.inspect",conversation_id:conversation}]);assert.deepEqual(failed,[]);cleanup();assert.equal(removed,1);
});

test("renderer relay rejects malformed host requests instead of widening the WS payload",()=>{
  const bind=bindEvolutionHostRelay;assert.equal(typeof bind,"function","bindEvolutionHostRelay must be exported");
  let receive;const sent=[];const failed=[];bind({desktop:{onEvolutionRequest:handler=>{receive=handler;return()=>{};}},client:{sendEvolutionRequest:request=>sent.push(request)},onError:error=>failed.push(String(error))});
  receive({type:"evolution.patch",conversation_id:conversation,owned_paths:["*"]});
  assert.deepEqual(sent,[]);assert.equal(failed.length,1);
});


