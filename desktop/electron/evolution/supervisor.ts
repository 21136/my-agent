import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import * as path from "node:path";
import { ContractError, canonicalJson, contentHash, parseControlRequest, type CandidateDescriptor, type ControlRequest, type ControlResult, type Hash, type WorkspaceScope } from "./contracts";
import { JournalStore, beginOperation, type Intent } from "./journal";
import { BindingStore, type HostBinding } from "./binding";
import { GrantStore } from "./authorization";
import { createT4RecoveryCandidateDescriptor, type BuildReceipt, type CandidateRunner, type InstanceReceipt, type SnapshotDescriptorFactory } from "./runner";
import { T4_RECOVERY_OWNED_PATHS } from "./test-profile";

export interface WorkspaceClient {
  inspect(scope:WorkspaceScope,query:Record<string,unknown>,operationId:string):Promise<Record<string,unknown>>;
  patch(scope:WorkspaceScope,patch:Record<string,unknown>,operationId:string):Promise<Record<string,unknown>>;
  snapshot(scope:WorkspaceScope,includePaths:string[],workspaceRevision:number,operationId:string):Promise<Record<string,unknown>>;
}
export type Projection={
  evolution_id:string;conversation_id:string;state_revision:number;current_candidate_id:string|null;current_instance_id:string|null;
  pending_operation_ids:string[];unknown_operation_ids:string[];isolation_mode:"local_managed"|"protected_guest"|null;
  candidate_status:"not_run"|"starting"|"ready"|"failed"|"stopped"|"quarantined";
  prototype_control_verified:boolean;scripted_recovery_verified:boolean;agent_recovery_verified:boolean;
  independent_review:"incomplete"|"not_run"|"passed"|"failed";
  main_host_replaced:boolean;late_event_count:number;
};
export type SourceSnapshotRef={source_snapshot_id:Hash;tree_root:string;manifest_path:string;workspace_revision:number};
export type TrustedHostBindingInput={
  conversation_id:string;scope_id:string;host_instance_id:string;allowed_scope_ids:string[];
  confirmation_id:string;expires_at:string;
};
export type TrustedHostBindingResult={evolution_id:string;authorization_id:string;scope_id:string;workspace_revision:number};
export type TrustedHostAuthorizationInput={conversation_id:string;evolution_id:string;kind:"source.patch"|"candidate.build"|"candidate.launch";constraints:Record<string,unknown>;confirmation_id:string;expires_at:string;host_instance_id:string;allowed_scope_ids:string[]};
export type TrustedHostAuthorizationResult={evolution_id:string;authorization_id:string;kind:TrustedHostAuthorizationInput["kind"]};
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const SOURCE_HASH=/^sha256:[0-9a-f]{64}$/;
function isSafeOwnedPath(value:unknown):value is string{return typeof value==="string"&&!!value&&!value.startsWith("/")&&!value.includes("\\")&&!value.includes(":")&&!value.split("/").some(part=>!part||part==="."||part==="..");}
export type SupervisorDeps={
  journal:JournalStore;bindings:BindingStore;grants:GrantStore;runner:CandidateRunner;workspace:WorkspaceClient;
  scopes:Map<string,WorkspaceScope>;runRoot:string;runtimePaths:Map<string,string>;
  snapshots:Map<Hash,SourceSnapshotRef>;descriptors:Map<string,CandidateDescriptor>;profileFixtures:Map<string,string>;descriptorFactories?:SnapshotDescriptorFactory[];
};
type MutableProjection=Projection&{late_events:Array<Record<string,unknown>>;seen_instance_sequence:number};
const IMPLEMENTED_ACTIONS=new Set(["inspect","patch","snapshot","build","launch","stop"]);
function fail(code:ConstructorParameters<typeof ContractError>[0],message:string):never{throw new ContractError(code,message);}
function asObject(value:unknown,label:string):Record<string,unknown>{if(value===null||typeof value!=="object"||Array.isArray(value))fail("invalid_request",`${label} must be an object`);return value as Record<string,unknown>;}
function fault(error:unknown):{code:ControlResult["error"] extends infer T?T extends {code:infer C}?C:never:never;message:string;evidence_ids:string[];retryable:boolean}{
  const code=(error as {code?:string})?.code;
  const allowed=new Set(["invalid_request","permission_denied","scope_conflict","stale_revision","identity_mismatch","protocol_incompatible","unsupported_capability","isolation_unavailable","environment_not_ready","budget_exceeded","build_failed","start_failed","case_failed","capture_unavailable","scene_stale","restore_ambiguous","timeout","cancelled","outcome_unknown","adoption_blocked","data_incompatible","recovery_required"]);
  return {code:(typeof code==="string"&&allowed.has(code)?code:"invalid_request") as never,message:(error instanceof Error?error.message:"operation failed").slice(0,4096),evidence_ids:[],retryable:false};
}
function normalizeWorkerResult(result:Record<string,unknown>):Record<string,unknown>{
  if(result.ok===false){const detail=asObject(result.error,"workspace error");const code=typeof detail.code==="string"?detail.code:"scope_conflict";const allowed=new Set(["invalid_request","permission_denied","scope_conflict","budget_exceeded","unsupported_capability","outcome_unknown","identity_mismatch","recovery_required"]);throw new ContractError(allowed.has(code)?code as ConstructorParameters<typeof ContractError>[0]:"scope_conflict",typeof detail.message==="string"?detail.message:"workspace operation failed");}
  if(result.ok!==true)fail("scope_conflict","workspace worker returned an invalid result envelope");
  if(Buffer.byteLength(canonicalJson(result),"utf8")>65_536)fail("budget_exceeded","workspace result exceeds the control response limit");
  return result;
}
function resultEnvelope(request:ControlRequest,stateRevision:number,status:ControlResult["status"],result:unknown,error:ControlResult["error"]):ControlResult{
  let normalized:unknown;
  try{normalized=JSON.parse(JSON.stringify(result??null));}catch{fail("invalid_request","control result is not serializable JSON");}
  const value:ControlResult={operation_id:request.operation_id,evolution_id:request.evolution_id,state_revision:stateRevision,status,result:normalized as ControlResult["result"],error};
  canonicalJson(value);return value;
}

export class EvolutionSupervisor {
  readonly deps:SupervisorDeps;
  private readonly projections=new Map<string,MutableProjection>();
  private readonly builds=new Map<Hash,BuildReceipt>();
  private readonly instances=new Map<string,InstanceReceipt>();

  constructor(deps:SupervisorDeps){
    this.deps=deps;
    if(!path.isAbsolute(deps.runRoot))fail("invalid_request","supervisor run root must be absolute");
    const root=path.resolve(deps.runRoot);
    const artifacts=path.resolve(process.cwd(),"artifacts");
    const relative=path.relative(artifacts,root);
    if(relative===""||relative===".."||relative.startsWith(`..${path.sep}`)||path.isAbsolute(relative))fail("permission_denied","supervisor run root must remain under sandbox/artifacts");
    if(readFileSync(path.join(root,".evolution-test-run"),"utf8").replace(/^\uFEFF/,"").trim()!=="evolution-a")fail("permission_denied","supervisor run root marker is missing");
  }

  private configuredScope(scopeId:string):WorkspaceScope{
    const matches=[...new Set(this.deps.scopes.values())].filter(scope=>scope.scope_id===scopeId);
    if(matches.length!==1)fail("permission_denied","workspace scope is not uniquely registered by the trusted Host");
    const scope=matches[0];
    const keys=["scope_id","source_root","owned_paths","state_root","read_only_roots"];
    if(Object.keys(scope).length!==keys.length||keys.some(key=>!Object.hasOwn(scope,key))||!UUID.test(scope.scope_id)||!path.isAbsolute(scope.source_root)||!path.isAbsolute(scope.state_root)||!Array.isArray(scope.owned_paths)||scope.owned_paths.length<1||scope.owned_paths.length>256||!Array.isArray(scope.read_only_roots)||scope.read_only_roots.some(item=>typeof item!=="string"||!path.isAbsolute(item)))fail("invalid_request","trusted workspace scope configuration is malformed");
    const normalized=scope.owned_paths.map(item=>{if(!isSafeOwnedPath(item))fail("invalid_request","trusted owned path is malformed");return item;});
    const folded=normalized.map(item=>item.toLowerCase());
    if(new Set(folded).size!==folded.length)fail("invalid_request","trusted owned paths contain case collisions");
    return scope;
  }

  async bindFromTrustedHost(input:TrustedHostBindingInput):Promise<TrustedHostBindingResult>{
    if(input===null||typeof input!=="object"||Object.keys(input).length!==6||!["conversation_id","scope_id","host_instance_id","allowed_scope_ids","confirmation_id","expires_at"].every(key=>Object.hasOwn(input,key)))fail("invalid_request","trusted host binding fields are invalid");
    for(const key of ["conversation_id","scope_id","host_instance_id","confirmation_id"] as const)if(!UUID.test(input[key]))fail("invalid_request",`${key} must be a canonical UUID`);
    if(!Array.isArray(input.allowed_scope_ids)||input.allowed_scope_ids.length<1||input.allowed_scope_ids.length>256||input.allowed_scope_ids.some(id=>typeof id!=="string"||!UUID.test(id))||new Set(input.allowed_scope_ids).size!==input.allowed_scope_ids.length)fail("invalid_request","trusted host scope allowlist is malformed");
    if(!input.allowed_scope_ids.includes(input.scope_id))fail("permission_denied","workspace scope is not allowed by the authenticated Host bootstrap");
    if(typeof input.expires_at!=="string"||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$/.test(input.expires_at)||!Number.isFinite(Date.parse(input.expires_at))||Date.parse(input.expires_at)<=Date.now())fail("permission_denied","scope confirmation is expired or malformed");
    const scope=this.configuredScope(input.scope_id);
    let binding=this.deps.bindings.findForConversation(input.conversation_id);
    let created=false;
    if(binding){
      if(binding.scope_id!==input.scope_id||binding.host_instance_id!==input.host_instance_id||binding.status!=="bound")fail("permission_denied","conversation already has a different or frozen evolution binding");
    }else{
      binding=this.deps.bindings.bind({conversation_id:input.conversation_id,scope_id:input.scope_id,host_instance_id:input.host_instance_id});
      created=true;
    }
    this.deps.scopes.set(binding.evolution_id,scope);
    try{
      const inventory=normalizeWorkerResult(await this.deps.workspace.inspect(scope,{query:"source_list"},randomUUID()));
      if(Object.keys(inventory).length!==3||inventory.ok!==true||!Number.isSafeInteger(inventory.workspace_revision)||(inventory.workspace_revision as number)<0||!Array.isArray(inventory.files)||inventory.files.length>scope.owned_paths.length)fail("scope_conflict","trusted source inventory is malformed");
      const expected:Record<string,string>=Object.fromEntries(scope.owned_paths.map(ownedPath=>[ownedPath,"missing"]));
      const seen=new Set<string>();
      for(const entry of inventory.files){
        if(entry===null||typeof entry!=="object"||Array.isArray(entry)||Object.keys(entry).length!==3)fail("scope_conflict","trusted source inventory entry is malformed");
        const file=entry as Record<string,unknown>;
        if(typeof file.path!=="string"||!Object.hasOwn(expected,file.path)||seen.has(file.path)||!Number.isSafeInteger(file.byte_length)||(file.byte_length as number)<0||typeof file.sha256!=="string"||!SOURCE_HASH.test(file.sha256))fail("scope_conflict","trusted source inventory contains an invalid or unowned file");
        seen.add(file.path);expected[file.path]=file.sha256;
      }
      const authorizationId=this.deps.grants.createConfirmed({binding,kind:"source.patch",constraints:{owned_paths:scope.owned_paths.slice(),expected_before_hashes:expected},confirmation_id:input.confirmation_id,expires_at:input.expires_at});
      return {evolution_id:binding.evolution_id,authorization_id:authorizationId,scope_id:scope.scope_id,workspace_revision:inventory.workspace_revision as number};
    }catch(error){
      if(created){
        try{this.deps.bindings.freeze(binding.evolution_id);this.deps.bindings.release(binding.evolution_id);this.deps.scopes.delete(binding.evolution_id);}
        catch{fail("recovery_required","trusted binding failed after lease creation and could not be safely released");}
      }
      throw error;
    }
  }

  async authorizeFromTrustedHost(input:TrustedHostAuthorizationInput):Promise<TrustedHostAuthorizationResult>{
    const fields=["conversation_id","evolution_id","kind","constraints","confirmation_id","expires_at","host_instance_id","allowed_scope_ids"];
    if(input===null||typeof input!=="object"||Object.keys(input).length!==fields.length||fields.some(key=>!Object.hasOwn(input,key)))fail("invalid_request","trusted Host authorization fields are invalid");
    for(const key of ["conversation_id","evolution_id","confirmation_id","host_instance_id"] as const)if(!UUID.test(input[key]))fail("invalid_request",key+" must be a canonical UUID");
    if(!Array.isArray(input.allowed_scope_ids)||input.allowed_scope_ids.length<1||input.allowed_scope_ids.length>256||input.allowed_scope_ids.some(id=>typeof id!=="string"||!UUID.test(id))||new Set(input.allowed_scope_ids).size!==input.allowed_scope_ids.length)fail("invalid_request","trusted Host scope allowlist is malformed");
    const binding=this.deps.bindings.require(input.evolution_id,input.conversation_id);
    if(binding.status!=="bound"||binding.host_instance_id!==input.host_instance_id||!input.allowed_scope_ids.includes(binding.scope_id))fail("permission_denied","trusted Host does not own this active binding");
    const expires=Date.parse(input.expires_at);if(typeof input.expires_at!=="string"||!/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,3})?(?:Z|[+-][0-9]{2}:[0-9]{2})$/.test(input.expires_at)||!Number.isFinite(expires)||expires<=Date.now()||expires>Date.now()+600_000)fail("permission_denied","action confirmation is expired or exceeds the 10 minute limit");
    const scope=this.scope(binding);const constraints=asObject(input.constraints,"trusted grant constraints");
    if(input.kind==="source.patch"){
      const paths=constraints.owned_paths;if(!Array.isArray(paths)||paths.length<1||paths.some(item=>typeof item!=="string"||!scope.owned_paths.includes(item)))fail("permission_denied","patch grant exceeds the bound owned paths");
      const expected=asObject(constraints.expected_before_hashes,"patch expected hashes");
      const inventory=normalizeWorkerResult(await this.deps.workspace.inspect(scope,{query:"source_list"},randomUUID()));
      if(Object.keys(inventory).length!==3||inventory.ok!==true||!Number.isSafeInteger(inventory.workspace_revision)||(inventory.workspace_revision as number)<0||!Array.isArray(inventory.files)||inventory.files.length>scope.owned_paths.length)fail("scope_conflict","trusted source inventory is malformed");
      const current:Record<string,string>=Object.fromEntries(scope.owned_paths.map(ownedPath=>[ownedPath,"missing"]));const seen=new Set<string>();
      for(const entry of inventory.files){const file=asObject(entry,"source inventory entry");if(Object.keys(file).length!==3||typeof file.path!=="string"||!Object.hasOwn(current,file.path)||seen.has(file.path)||!Number.isSafeInteger(file.byte_length)||(file.byte_length as number)<0||typeof file.sha256!=="string"||!SOURCE_HASH.test(file.sha256))fail("scope_conflict","trusted source inventory contains an invalid or unowned file");seen.add(file.path);current[file.path]=file.sha256;}
      for(const pathValue of paths as string[])if(current[pathValue]!==expected[pathValue])fail("stale_revision","patch target changed after the user confirmation preview");
    }else if(input.kind==="candidate.build"){
      const snapshotId=String(constraints.source_snapshot_id) as Hash;const snapshot=this.deps.snapshots.get(snapshotId);if(!snapshot)fail("permission_denied","build grant snapshot is not owned by this supervisor");
      const isolation=constraints.isolation_mode;if(isolation!=="local_managed"&&isolation!=="protected_guest")fail("invalid_request","build grant isolation mode is invalid");
      const digest=String(constraints.build_input_digest);const matches=[...this.deps.descriptors.values()].filter(descriptor=>descriptor.source_snapshot_id===snapshotId&&descriptor.isolation_mode===isolation&&contentHash({descriptor,source_snapshot_id:snapshotId,isolation_mode:isolation})===digest);
      if(matches.length!==1)fail("identity_mismatch","build grant does not identify exactly one registered candidate recipe");
    }else if(input.kind==="candidate.launch"){
      const candidateId=String(constraints.candidate_id) as Hash;const build=this.builds.get(candidateId);if(!build)fail("permission_denied","launch grant candidate is not a build owned by this supervisor");
      const profileId=String(constraints.profile_fixture_id);const profile=this.deps.profileFixtures.get(profileId);if(!profile)fail("permission_denied","launch grant profile is not in the trusted registry");
      if(constraints.isolation_mode!==build.isolation_mode)fail("identity_mismatch","launch grant isolation differs from the registered build");
    }else fail("unsupported_capability","trusted Host cannot authorize this capability kind");
    const authorizationId=this.deps.grants.createConfirmed({binding,kind:input.kind,constraints,confirmation_id:input.confirmation_id,expires_at:input.expires_at});
    return{evolution_id:binding.evolution_id,authorization_id:authorizationId,kind:input.kind};
  }

  private scope(binding:HostBinding):WorkspaceScope{
    const scope=this.deps.scopes.get(binding.evolution_id);
    if(!scope||scope.scope_id!==binding.scope_id)fail("permission_denied","workspace scope does not match the trusted binding");
    return scope;
  }

  private ownedPath(value:string,label:string):string{
    const target=path.resolve(value);const relative=path.relative(path.resolve(this.deps.runRoot),target);
    if(relative===""||relative===".."||relative.startsWith(`..${path.sep}`)||path.isAbsolute(relative))fail("permission_denied",`${label} escaped the marked run root`);
    return target;
  }

  private state(binding:HostBinding):MutableProjection{
    let current=this.projections.get(binding.evolution_id);
    if(!current){current={evolution_id:binding.evolution_id,conversation_id:binding.conversation_id,state_revision:binding.state_revision,current_candidate_id:null,current_instance_id:null,pending_operation_ids:[],unknown_operation_ids:[],isolation_mode:null,candidate_status:"not_run",prototype_control_verified:false,scripted_recovery_verified:false,agent_recovery_verified:false,independent_review:"incomplete",main_host_replaced:false,late_event_count:0,late_events:[],seen_instance_sequence:0};this.projections.set(binding.evolution_id,current);}
    current.state_revision=binding.state_revision;return current;
  }

  private publicProjection(state:MutableProjection):Projection{
    const {late_events:_,seen_instance_sequence:__,...projection}=state;
    return structuredClone(projection);
  }

  project(evolutionId:string,conversationId:string):Projection{
    const binding=this.deps.bindings.require(evolutionId,conversationId);const state=this.state(binding);
    const recovery=this.deps.journal.recover();
    state.unknown_operation_ids=recovery.unknown_operation_ids.filter(id=>this.deps.journal.get(id)?.request.evolution_id===evolutionId);
    return this.publicProjection(state);
  }

  async dispatch(requestValue:ControlRequest):Promise<ControlResult>{
    let request:ControlRequest;
    request=parseControlRequest(requestValue);
    let binding:HostBinding;
    try{binding=this.deps.bindings.require(request.evolution_id,request.conversation_id);}catch(error){return resultEnvelope(request,bindingRevision(this.deps,request),"failed",null,fault(error));}
    const state=this.state(binding);
    if(request.action==="inspect")return this.inspect(request,binding,state);
    if(!IMPLEMENTED_ACTIONS.has(request.action))return resultEnvelope(request,binding.state_revision,"failed",null,fault(new ContractError("unsupported_capability",`action ${request.action} is not implemented in slice A`)));

    let begun:Intent|ControlResult;
    try{begun=beginOperation(request,{journal:this.deps.journal,bindings:this.deps.bindings,grants:this.deps.grants});}
    catch(error){const details=fault(error);const status=details.code==="outcome_unknown"?"unknown":"failed";return resultEnvelope(request,bindingRevision(this.deps,request),status,null,details);}
    if("status" in begun)return begun;
    const revision=request.expected_state_revision+1;
    state.state_revision=revision;state.pending_operation_ids.push(request.operation_id);
    try{
      this.deps.journal.markEffect(request.operation_id,"started");
      const value=await this.execute(request,binding,state);
      const outcomeStatus=(value&&typeof value==="object"&&"status" in value&&["failed","cancelled","unknown"].includes(String((value as Record<string,unknown>).status)))?String((value as Record<string,unknown>).status) as ControlResult["status"]:"succeeded";
      const result=resultEnvelope(request,revision,outcomeStatus,value,null);
      this.deps.journal.finish(result);
      if(outcomeStatus==="failed")state.candidate_status="failed";
      return result;
    }catch(error){
      const details=fault(error);
      const status=details.code==="outcome_unknown"?"unknown":"failed";
      const result=resultEnvelope(request,revision,status,null,details);
      try{this.deps.journal.finish(result);}catch{/* retain the started intent as unknown if terminal persistence failed */}
      if(request.action==="build"||request.action==="launch")state.candidate_status="failed";else if(request.action==="stop"&&state.current_instance_id===(request.payload as Record<string,unknown>).instance_id)state.candidate_status="quarantined";
      return result;
    }finally{
      state.pending_operation_ids=state.pending_operation_ids.filter(id=>id!==request.operation_id);
      try{const latest=this.deps.bindings.require(request.evolution_id,request.conversation_id);state.state_revision=latest.state_revision;}catch{/* binding release is independently authorized after terminal effects */}
    }
  }

  private async inspect(request:ControlRequest,binding:HostBinding,state:MutableProjection):Promise<ControlResult>{
    const payload=request.payload as Record<string,unknown>;const query=payload.query;
    try{
      if(query==="operation"){
        const operation=this.deps.journal.get(String(payload.operation_id));
        if(!operation||operation.request.evolution_id!==binding.evolution_id)return resultEnvelope(request,binding.state_revision,"failed",null,fault(new ContractError("permission_denied","operation is not visible to this conversation")));
        if(operation.result)return resultEnvelope(request,binding.state_revision,operation.result.status,{operation_id:operation.request.operation_id,action:operation.request.action,terminal_result:operation.result},operation.result.error);
        const unknown=operation.effect==="started"||operation.effect==="observed";
        return resultEnvelope(request,binding.state_revision,unknown?"unknown":"running",{operation_id:operation.request.operation_id,action:operation.request.action,effect:operation.effect},unknown?{code:"outcome_unknown",message:"operation outcome is not terminal",evidence_ids:[],retryable:false}:null);
      }
      if(query==="summary")return resultEnvelope(request,binding.state_revision,"succeeded",this.publicProjection(state),null);
      const scope=this.scope(binding);
      const result=normalizeWorkerResult(await this.deps.workspace.inspect(scope,payload,request.operation_id));
      return resultEnvelope(request,binding.state_revision,"succeeded",result,null);
    }catch(error){return resultEnvelope(request,binding.state_revision,"failed",null,fault(error));}
  }

  private async execute(request:ControlRequest,binding:HostBinding,state:MutableProjection):Promise<Record<string,unknown>>{
    const payload=request.payload as Record<string,unknown>;
    const scope=request.action==="stop"?null:this.scope(binding);
    if(request.action==="patch")return normalizeWorkerResult(await this.deps.workspace.patch(scope!,payload,request.operation_id));
    if(request.action==="snapshot"){
      const result=normalizeWorkerResult(await this.deps.workspace.snapshot(scope!,payload.include_paths as string[],payload.workspace_revision as number,request.operation_id));
      const id=result.source_snapshot_id;if(typeof id!=="string"||!/^sha256:[0-9a-f]{64}$/.test(id)||typeof result.tree_root!=="string"||typeof result.manifest_path!=="string")fail("invalid_request","snapshot worker omitted its content identity or paths");
      const treeRoot=this.ownedPath(result.tree_root,"snapshot tree root");const manifestPath=this.ownedPath(result.manifest_path,"snapshot manifest");
      this.deps.snapshots.set(id as Hash,{source_snapshot_id:id as Hash,tree_root:treeRoot,manifest_path:manifestPath,workspace_revision:Number(result.workspace_revision)});
      for(const factory of this.deps.descriptorFactories??[]){this.deps.descriptors.delete(factory.artifact_id);const manifestBytes=readFileSync(manifestPath);let manifest:unknown;try{manifest=JSON.parse(manifestBytes.toString("utf8"));}catch{fail("identity_mismatch","snapshot manifest is invalid JSON");}if(Buffer.from(canonicalJson(manifest),"utf8").compare(manifestBytes)!==0||contentHash(manifest)!==id)fail("identity_mismatch","snapshot manifest bytes do not match their source identity");if(!manifest||typeof manifest!=="object"||Array.isArray(manifest))continue;const item=manifest as Record<string,unknown>;const files=Array.isArray(item.files)?item.files:[];const paths=files.map(file=>file&&typeof file==="object"&&!Array.isArray(file)?(file as Record<string,unknown>).path:null);const expected=[...T4_RECOVERY_OWNED_PATHS].sort();if(item.scope_id!==scope!.scope_id||item.workspace_revision!==result.workspace_revision||paths.length!==expected.length||paths.some((value,index)=>value!==expected[index]))continue;const runtimePath=this.deps.runtimePaths.get(factory.runtime_id);if(!runtimePath)fail("environment_not_ready","registered T4 Node runtime is missing");const descriptor=createT4RecoveryCandidateDescriptor(id as Hash,factory.runtime_id,runtimePath);this.deps.descriptors.set(factory.artifact_id,descriptor);return{...result,t4_recovery_descriptor:{artifact_id:factory.artifact_id,build_input_digest:contentHash({descriptor,source_snapshot_id:id,isolation_mode:"local_managed"}),isolation_mode:"local_managed"}};}
      return result;
    }
    if(request.action==="build"){
      const snapshotId=String(payload.source_snapshot_id) as Hash;const snapshot=this.deps.snapshots.get(snapshotId);if(!snapshot)fail("permission_denied","source snapshot is not owned by the supervisor");
      this.ownedPath(snapshot.tree_root,"registered snapshot tree");this.ownedPath(snapshot.manifest_path,"registered snapshot manifest");
      const descriptorId=String(payload.descriptor_artifact_id);const descriptor=this.deps.descriptors.get(descriptorId);if(!descriptor)fail("permission_denied","candidate descriptor is not in the trusted artifact registry");
      const buildDigest=contentHash({descriptor,source_snapshot_id:snapshotId,isolation_mode:payload.isolation_mode});if(buildDigest!==payload.build_input_digest)fail("identity_mismatch","build input digest does not match the registered descriptor and snapshot");
      if(descriptor.source_snapshot_id!==snapshotId||payload.isolation_mode!==descriptor.isolation_mode)fail("identity_mismatch","descriptor snapshot/isolation does not match the build request");
      const receipt=await this.deps.runner.prepare({descriptor,source_root:snapshot.tree_root,run_root:this.deps.runRoot,runtime_paths:Object.fromEntries(this.deps.runtimePaths),authorization:{authorization_id:request.authorization_id,kind:"candidate.build",isolation_mode:"local_managed"}});
      this.builds.set(receipt.candidate_id,receipt);state.current_candidate_id=receipt.candidate_id;state.current_instance_id=null;state.candidate_status="not_run";state.isolation_mode="local_managed";
      return {build_id:receipt.build_id,source_snapshot_id:receipt.source_snapshot_id,build_fingerprint:receipt.build_fingerprint,candidate_id:receipt.candidate_id,artifact_hashes:receipt.artifact_hashes,isolation_mode:receipt.isolation_mode};
    }
    if(request.action==="launch"){
      const candidateId=String(payload.candidate_id) as Hash;const build=this.builds.get(candidateId);if(!build)fail("permission_denied","candidate build is not owned by the supervisor");
      const profileId=String(payload.profile_fixture_id);const profile=this.deps.profileFixtures.get(profileId);if(!profile)fail("permission_denied","profile fixture is not in the trusted registry");
      this.ownedPath(profile,"candidate profile");
      state.current_candidate_id=candidateId;state.candidate_status="starting";state.isolation_mode=build.isolation_mode;state.seen_instance_sequence=0;
      const receipt=await this.deps.runner.launch(build,{profile_root:profile,authorization:{authorization_id:request.authorization_id,kind:"candidate.launch",isolation_mode:"local_managed",candidate_id:candidateId}});
      this.instances.set(receipt.instance_id,receipt);state.current_instance_id=receipt.instance_id;state.candidate_status=receipt.status==="ready"?"ready":receipt.status==="quarantined"?"quarantined":"failed";
      const stopAuthorizationId=this.deps.grants.bindInstance(request.authorization_id,receipt.instance_id);
      return {candidate_id:receipt.candidate_id,instance_id:receipt.instance_id,status:receipt.status,stop_scope:receipt.stop_scope,descendants_verified:receipt.descendants_verified,components:receipt.components,stop_authorization_id:stopAuthorizationId};
    }
    if(request.action==="stop"){
      const instanceId=String(payload.instance_id);if(binding.status==="bound")this.deps.grants.revoke(binding.evolution_id);const receipt=await this.deps.runner.stop(instanceId);
      const exitsObserved=receipt.components.length>0&&receipt.components.every(component=>component.exit_observed===true);const confirmed:InstanceReceipt=receipt.status==="stopped"&&exitsObserved?receipt:{...receipt,status:"quarantined"};this.instances.set(instanceId,confirmed);
      if(state.current_instance_id===instanceId)state.candidate_status=confirmed.status==="stopped"?"stopped":"quarantined";
      return {instance_id:instanceId,status:confirmed.status,stop_scope:confirmed.stop_scope,descendants_verified:confirmed.descendants_verified,components:confirmed.components};
    }
    fail("unsupported_capability",`action ${request.action} is not dispatched by slice A`);
  }

  resolveStopAuthorizationFromTrustedHost(input:{conversation_id:string;evolution_id:string;instance_id:string;host_instance_id:string;allowed_scope_ids:string[]}):{evolution_id:string;instance_id:string;authorization_id:string}{
    const fields=["conversation_id","evolution_id","instance_id","host_instance_id","allowed_scope_ids"];
    if(input===null||typeof input!=="object"||Object.keys(input).length!==fields.length||fields.some(key=>!Object.hasOwn(input,key)))fail("invalid_request","trusted stop authorization fields are invalid");
    for(const key of ["conversation_id","evolution_id","instance_id","host_instance_id"] as const)if(!UUID.test(input[key]))fail("invalid_request",key+" must be a canonical UUID");
    if(!Array.isArray(input.allowed_scope_ids)||input.allowed_scope_ids.length<1||input.allowed_scope_ids.length>256||input.allowed_scope_ids.some(id=>typeof id!=="string"||!UUID.test(id))||new Set(input.allowed_scope_ids).size!==input.allowed_scope_ids.length)fail("invalid_request","trusted Host scope allowlist is malformed");
    const binding=this.deps.bindings.require(input.evolution_id,input.conversation_id);
    if(binding.host_instance_id!==input.host_instance_id||!input.allowed_scope_ids.includes(binding.scope_id))fail("permission_denied","trusted Host does not own this binding");
    const state=this.state(binding);if(state.current_instance_id!==input.instance_id||!this.instances.has(input.instance_id))fail("permission_denied","instance is not the current owned instance for this binding");
    const authorization_id=this.deps.grants.resolveInstanceGrant(input.evolution_id,input.instance_id);
    return{evolution_id:input.evolution_id,instance_id:input.instance_id,authorization_id};
  }

  disconnectHost(hostInstanceId:string):void{
    const evolutionIds=new Set(this.deps.journal.readEvents().filter(event=>event.kind==="binding.created"&&typeof event.evolution_id==="string").map(event=>String(event.evolution_id)));
    for(const evolutionId of evolutionIds){
      const binding=this.deps.journal.getBinding(evolutionId);if(binding?.host_instance_id!==hostInstanceId)continue;
      if(binding.status!=="frozen")this.deps.bindings.freeze(evolutionId);
      const current=this.deps.bindings.require(evolutionId,binding.conversation_id);const state=this.state(current);state.state_revision=current.state_revision;
    }
  }
  acceptRuntimeEvent(event:{evolution_id:string;conversation_id:string;candidate_id:string;instance_id:string;instance_sequence:number;kind:string;origin:"supervisor_observed"|"candidate_reported"|"guest_reported";payload:Record<string,unknown>}):boolean{
    const binding=this.deps.bindings.require(event.evolution_id,event.conversation_id);const state=this.state(binding);
    const current=this.instances.get(state.current_instance_id??"");
    const validSequence=Number.isSafeInteger(event.instance_sequence)&&event.instance_sequence>state.seen_instance_sequence;
    if(!current||current.instance_id!==event.instance_id||current.candidate_id!==event.candidate_id||!validSequence||event.origin!=="supervisor_observed"){
      state.late_event_count+=1;
      const summary={candidate_id:String(event.candidate_id).slice(0,80),instance_id:String(event.instance_id).slice(0,80),instance_sequence:Number.isSafeInteger(event.instance_sequence)?event.instance_sequence:-1,kind:String(event.kind).slice(0,80),origin:event.origin,payload_digest:(()=>{try{return contentHash(event.payload);}catch{return null;}})()};
      if(state.late_events.length<128)state.late_events.push(summary);
      return false;
    }
    state.seen_instance_sequence=event.instance_sequence;
    if(event.kind==="runtime.ready"&&current.status==="ready")state.candidate_status="ready";
    else if(event.kind==="runtime.exited")state.candidate_status="failed";
    return true;
  }
}
function bindingRevision(deps:SupervisorDeps,request:ControlRequest):number{return deps.journal.getBinding(request.evolution_id)?.state_revision??request.expected_state_revision;}


