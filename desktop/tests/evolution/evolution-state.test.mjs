import assert from "node:assert/strict";
import {test} from "node:test";
let api={};try{api=await import("../../src/evolution-state.ts");}catch{/* RED until the renderer state projection exists. */}
function view(){assert.equal(typeof api.evolutionView,"function","evolution-state must export evolutionView");return api.evolutionView;}

test("inactive and bound projections map to a compact, truthful view",()=>{
  const evolutionView=view();const inactive=evolutionView(null);assert.equal(inactive.active,false);assert.equal(inactive.canStop,false);
  const current=evolutionView({evolution_id:"22222222-2222-4222-8222-222222222222",conversation_id:"33333333-3333-4333-8333-333333333333",state_revision:4,current_candidate_id:"sha256:"+"a".repeat(64),current_instance_id:"44444444-4444-4444-8444-444444444444",pending_operation_ids:[],unknown_operation_ids:["55555555-5555-4555-8555-555555555555"],isolation_mode:"local_managed",candidate_status:"ready",prototype_control_verified:false,scripted_recovery_verified:false,agent_recovery_verified:false,independent_review:"incomplete"});
  assert.equal(current.active,true);assert.equal(current.canStop,true);assert.ok(current.openChecks.length>0);assert.equal(current.currentCandidate,"sha256:"+"a".repeat(64));
});

test("untrusted fields and candidate text never enter renderer view",()=>{
  const evolutionView=view();const result=evolutionView({evolution_id:"22222222-2222-4222-8222-222222222222",state_revision:0,candidate_status:"not_run",current_candidate_id:null,current_instance_id:null,pending_operation_ids:[],unknown_operation_ids:[],prototype_control_verified:false,scripted_recovery_verified:false,agent_recovery_verified:false,independent_review:"incomplete",host_token:"do-not-display",candidate_text:"untrusted"});
  assert.equal(Object.hasOwn(result,"host_token"),false);assert.equal(Object.hasOwn(result,"candidate_text"),false);
});
