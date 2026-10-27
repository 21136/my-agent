import { createHash, randomBytes } from "node:crypto";
import type { ChildProcess } from "node:child_process";
import { lstatSync, readFileSync } from "node:fs";
import * as path from "node:path";
import { ContractError, canonicalJson, type CandidateDescriptor, type Hash, type ProcessRecipe } from "./contracts";
import { LocalManagedRunner } from "./runners/local";
import { T4_RECOVERY_ADAPTER_ARTIFACT_ID, T4_RECOVERY_BUILD_RECIPE_ARTIFACT_ID, T4_RECOVERY_ENVIRONMENT_PROFILE_ID, T4_RECOVERY_MAIN_COMPONENT_ID, T4_RECOVERY_SERVICE_COMPONENT_ID } from "./test-profile";

export type SnapshotDescriptorFactory={artifact_id:string;kind:"t4-recovery-node-v1";runtime_id:string;profile_fixture_id:string};
export function createT4RecoveryCandidateDescriptor(sourceSnapshotId:Hash,runtimeId:string,runtimePath:string):CandidateDescriptor{
  if(!/^sha256:[0-9a-f]{64}$/.test(sourceSnapshotId)||!/^([0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$/.test(runtimeId)||!path.isAbsolute(runtimePath))throw new ContractError("invalid_request","T4 recovery descriptor identity is invalid");
  if(process.platform!=="win32"||!(process.arch==="x64"||process.arch==="arm64"))throw new ContractError("environment_not_ready","T4 recovery Node runtime requires a supported Windows Host");
  const info=lstatSync(runtimePath);if(!info.isFile()||info.isSymbolicLink())throw new ContractError("permission_denied","T4 Node runtime must be a regular file");
  const runtimeBytes=readFileSync(runtimePath);const ref=(artifactId:string,value:unknown,mediaType:string)=>{const bytes=Buffer.from(canonicalJson(value),"utf8");return{artifact_id:artifactId,sha256:("sha256:"+createHash("sha256").update(bytes).digest("hex")) as Hash,byte_length:bytes.length,media_type:mediaType,origin:"supervisor_observed" as const};};
  return{descriptor_version:"1.0",source_snapshot_id:sourceSnapshotId,build_recipe:ref(T4_RECOVERY_BUILD_RECIPE_ARTIFACT_ID,{kind:"t4-local-copy-builder-v1"},"application/vnd.my-agent.evolution-build-recipe+json"),launch_recipe:{processes:[{component_id:T4_RECOVERY_MAIN_COMPONENT_ID,role:"service",runtime_id:runtimeId,entrypoint:"evolve/tool-catalog/recovery/main.cjs",argv:[],cwd:"evolve/tool-catalog/recovery",environment_profile_id:T4_RECOVERY_ENVIRONMENT_PROFILE_ID,depends_on:[],required:true,readiness:"protocol"},{component_id:T4_RECOVERY_SERVICE_COMPONENT_ID,role:"service",runtime_id:runtimeId,entrypoint:"evolve/tool-catalog/recovery/service.cjs",argv:[],cwd:"evolve/tool-catalog/recovery",environment_profile_id:T4_RECOVERY_ENVIRONMENT_PROFILE_ID,depends_on:[],required:true,readiness:"protocol"}]},runtimes:[{runtime_id:runtimeId,kind:"node",version:process.version,platform:"win32",arch:process.arch as "x64"|"arm64",use:"run",distribution:{artifact_id:runtimeId,sha256:("sha256:"+createHash("sha256").update(runtimeBytes).digest("hex")) as Hash,byte_length:runtimeBytes.length,media_type:"application/vnd.nodejs.runtime",origin:"supervisor_observed"}}],dependency_lock:[],adapter:ref(T4_RECOVERY_ADAPTER_ARTIFACT_ID,{kind:"t4-json-lines-protocol-v1"},"application/vnd.my-agent.evolution-adapter+json"),data_contract_digest:("sha256:"+createHash("sha256").update(canonicalJson({kind:"none"}),"utf8").digest("hex")) as Hash,mode:"sealed_build",isolation_mode:"local_managed",required_capabilities:[]};
}
export type BuildReceipt = { build_id:string; source_snapshot_id:Hash; build_fingerprint:Hash; candidate_id:Hash; output_root:string; artifact_hashes:Record<string,Hash>; isolation_mode:"local_managed" };
export type ComponentReceipt = { component_id:string; pid:number|null; ready:boolean; exit_observed:boolean; exit_code?:number|null; stderr_excerpt?:string };
export type InstanceReceipt = { candidate_id:Hash; instance_id:string; components:ComponentReceipt[]; degraded_components:string[]; status:"starting"|"ready"|"failed"|"stopping"|"stopped"|"quarantined"; stop_scope:"declared_processes"; descendants_verified:false };
export type OwnedChild = { component_id:string; child:ChildProcess; instance_id:string; ready:boolean; exit_observed:boolean; boot_nonce:string; stdout_buffer:string; stderr_bytes:number };
export type CandidateRunner = Pick<LocalManagedRunner,"prepare"|"launch"|"stop"|"inspect"> & { readonly lifecycle:"declared_components_only" };

function exactObject(value:unknown,fields:string[],label:string):Record<string,unknown>{
  if(value===null||typeof value!=="object"||Array.isArray(value)||Object.getPrototypeOf(value)!==Object.prototype&&Object.getPrototypeOf(value)!==null)throw new ContractError("invalid_request",`${label} must be an object`);
  const object=value as Record<string,unknown>;if(Object.keys(object).length!==fields.length||fields.some(field=>!Object.hasOwn(object,field)))throw new ContractError("invalid_request",`${label} fields are missing or unknown`);return object;
}
function validId(value:unknown,label:string):asserts value is string{if(typeof value!=="string"||!/^([0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$/.test(value))throw new ContractError("invalid_request",`${label} must be a canonical UUID`);}
function validateArtifact(value:unknown,label:string):void{const artifact=exactObject(value,["artifact_id","sha256","byte_length","media_type","origin"],label);validId(artifact.artifact_id,`${label}.artifact_id`);if(typeof artifact.sha256!=="string"||!/^sha256:[0-9a-f]{64}$/.test(artifact.sha256)||!Number.isSafeInteger(artifact.byte_length)||(artifact.byte_length as number)<0||typeof artifact.media_type!=="string"||!new Set(["supervisor_observed","broker_observed","guest_reported","candidate_reported","human_confirmed"]).has(String(artifact.origin)))throw new ContractError("invalid_request",`${label} metadata is invalid`);}

export function validateProcessGraph(descriptorValue:unknown):ProcessRecipe[]{
  const d=exactObject(descriptorValue,["descriptor_version","source_snapshot_id","build_recipe","launch_recipe","runtimes","dependency_lock","adapter","data_contract_digest","mode","isolation_mode","required_capabilities"],"candidate descriptor") as unknown as CandidateDescriptor;
  if(d.descriptor_version!=="1.0"||!/^sha256:[0-9a-f]{64}$/.test(d.source_snapshot_id)||!/^sha256:[0-9a-f]{64}$/.test(d.data_contract_digest))throw new ContractError("invalid_request","candidate descriptor version or content digest is invalid");
  validateArtifact(d.build_recipe,"build_recipe");validateArtifact(d.adapter,"adapter");
  if(d.mode!=="live_dev"&&d.mode!=="sealed_build"||d.isolation_mode!=="local_managed"&&d.isolation_mode!=="protected_guest")throw new ContractError("invalid_request","descriptor mode is invalid");
  if(!Array.isArray(d.dependency_lock)||d.dependency_lock.length>1024)throw new ContractError("invalid_request","dependency_lock is invalid");d.dependency_lock.forEach((item,index)=>validateArtifact(item,`dependency_lock[${index}]`));
  if(!Array.isArray(d.required_capabilities)||d.required_capabilities.length>256||d.required_capabilities.some(value=>typeof value!=="string"||Buffer.byteLength(value,"utf8")>128))throw new ContractError("invalid_request","required_capabilities is invalid");
  if(!d.launch_recipe||typeof d.launch_recipe!=="object"||Array.isArray(d.launch_recipe))throw new ContractError("invalid_request","launch_recipe is invalid");
  const launch=exactObject(d.launch_recipe,["processes"],"launch_recipe");
  if(!Array.isArray(launch.processes)||launch.processes.length<1||launch.processes.length>16)throw new ContractError("invalid_request","process list must contain 1..16 declarations");
  if(!Array.isArray(d.runtimes)||d.runtimes.length<1||d.runtimes.length>8)throw new ContractError("invalid_request","runtime list is invalid");
  const runtimeIds=new Set<string>();
  for(const raw of d.runtimes){
    const runtime=exactObject(raw,["runtime_id","kind","version","platform","arch","use","distribution"],"runtime spec");validId(runtime.runtime_id,"runtime_id");
    if(runtimeIds.has(runtime.runtime_id))throw new ContractError("invalid_request","duplicate runtime id");runtimeIds.add(runtime.runtime_id);
    if(!["python","node","electron","executable"].includes(String(runtime.kind))||typeof runtime.version!=="string"||!runtime.version||runtime.platform!=="win32"||!["x64","arm64"].includes(String(runtime.arch))||!["build","run","both"].includes(String(runtime.use)))throw new ContractError("invalid_request","runtime properties are invalid");
    validateArtifact(runtime.distribution,"runtime distribution");
  }
  const processes=launch.processes as ProcessRecipe[];const byId=new Map<string,ProcessRecipe>();
  for(const raw of processes){
    const proc=exactObject(raw,["component_id","role","runtime_id","entrypoint","argv","cwd","environment_profile_id","depends_on","required","readiness"],"process recipe") as unknown as ProcessRecipe;
    validId(proc.component_id,"component_id");if(byId.has(proc.component_id))throw new ContractError("invalid_request","duplicate component id");
    if(!["agent","web","desktop","service","adapter"].includes(proc.role)||!runtimeIds.has(proc.runtime_id)||typeof proc.required!=="boolean"||!["protocol","process_alive","artifact_probe"].includes(proc.readiness))throw new ContractError("invalid_request","process recipe fields are invalid");
    if(typeof proc.entrypoint!=="string"||!proc.entrypoint||proc.entrypoint.startsWith("/")||proc.entrypoint.includes("\\")||proc.entrypoint.split("/").some(part=>!part||part===".."||part==="."))throw new ContractError("permission_denied","entrypoint must be a safe path inside the candidate snapshot");
    if(typeof proc.cwd!=="string"||proc.cwd.startsWith("/")||proc.cwd.includes("\\")||proc.cwd.split("/").some(part=>part===".."||part===""))throw new ContractError("permission_denied","working directory must remain inside the candidate snapshot");
    if(!Array.isArray(proc.argv)||proc.argv.length>64||proc.argv.some(arg=>typeof arg!=="string"||Buffer.byteLength(arg,"utf8")>4096||arg.includes("\0")))throw new ContractError("invalid_request","argv is invalid or exceeds its bound");
    validId(proc.environment_profile_id,"environment_profile_id");
    if(!Array.isArray(proc.depends_on)||new Set(proc.depends_on).size!==proc.depends_on.length||proc.depends_on.includes(proc.component_id))throw new ContractError("invalid_request","component dependencies are invalid");
    proc.depends_on.forEach((id,index)=>validId(id,`depends_on[${index}]`));byId.set(proc.component_id,proc);
  }
  for(const proc of processes)for(const dependency of proc.depends_on)if(!byId.has(dependency))throw new ContractError("invalid_request","component dependency is not declared");
  const remaining=new Map(processes.map(proc=>[proc.component_id,new Set(proc.depends_on)]));const ordered:ProcessRecipe[]=[];
  while(remaining.size){const ready=processes.filter(proc=>remaining.has(proc.component_id)&&remaining.get(proc.component_id)!.size===0);if(!ready.length)throw new ContractError("invalid_request","component dependency graph contains a cycle");for(const proc of ready){ordered.push(proc);remaining.delete(proc.component_id);for(const dependencies of remaining.values())dependencies.delete(proc.component_id);}}
  return ordered;
}

export function validateReadyMessage(value:unknown,identity:{candidate_id:string;instance_id:string;boot_nonce:string},requiredComponentIds:string[]):{ok:boolean;code?:string}{
  if(value===null||typeof value!=="object"||Array.isArray(value))return {ok:false,code:"invalid_request"};
  const message=value as Record<string,unknown>;
  if(message.type!=="ready"||message.candidate_id!==identity.candidate_id||message.instance_id!==identity.instance_id||message.boot_nonce!==identity.boot_nonce||typeof message.component_id!=="string"||!requiredComponentIds.includes(message.component_id))return {ok:false,code:"identity_mismatch"};
  return {ok:true};
}

export function freshBootNonce():string{return randomBytes(32).toString("hex");}
export function chooseRunner(mode:string,authorizedLocal:boolean):CandidateRunner{
  if(mode==="protected_guest")throw new ContractError("isolation_unavailable","protected guest backend is not implemented; local execution is not a fallback");
  if(mode!=="local_managed")throw new ContractError("invalid_request","unknown isolation mode");
  if(!authorizedLocal)throw new ContractError("permission_denied","local_managed execution requires an explicit scope grant");
  return new LocalManagedRunner();
}
