import { existsSync, lstatSync, readFileSync, realpathSync } from "node:fs";
import * as path from "node:path";
import { ContractError, type CandidateDescriptor, type WorkspaceScope } from "./contracts";
import type { ControlRelease } from "./control-release";
import { verifyControlRelease } from "./control-release";
import { BindingStore } from "./binding";
import { GrantStore } from "./authorization";
import { JournalStore } from "./journal";
import { LocalManagedRunner } from "./runners/local";
import { EvolutionSupervisor, type SupervisorDeps, type WorkspaceClient } from "./supervisor";
import type { CandidateRunner, SnapshotDescriptorFactory } from "./runner";
import { T4_RECOVERY_DESCRIPTOR_ARTIFACT_ID, T4_RECOVERY_OWNED_PATHS, T4_RECOVERY_PROFILE_FIXTURE_ID, T4_RECOVERY_RUNTIME_ID } from "./test-profile";
import type { HostBootstrap } from "./transport";
import { PythonWorkspaceClient } from "./workspace-client";

export type SupervisorProcessConfig={
  protocol:"my-agent-evolution-supervisor/1.0";
  sandbox_root:string;
  run_root:string;
  host_bootstrap:HostBootstrap;
  scopes:WorkspaceScope[];
  runtime_paths:Record<string,string>;
  descriptors:{artifact_id:string;descriptor:CandidateDescriptor}[];
  profile_fixtures:{id:string;root:string}[];
  descriptor_factories?:SnapshotDescriptorFactory[];
};
export type SupervisorRuntimeFactories={
  verifyRelease:(release:ControlRelease)=>Promise<void>;
  createJournal:(root:string)=>JournalStore;
  createWorkspace:(input:{release:ControlRelease;sandboxRoot:string;runRoot:string})=>Promise<WorkspaceClient>;
  createRunner:()=>CandidateRunner;
};
export type SupervisorRuntime={config:SupervisorProcessConfig;supervisor:EvolutionSupervisor;journal:JournalStore;closeJournal():void;isClosed():boolean};
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const HASH=/^sha256:[0-9a-f]{64}$/;
const TOKEN=/^[0-9a-f]{64}$/;
const PIPE_PREFIX="\\\\.\\pipe\\";
const BLOCKED_PATH_COMPONENTS=new Set([".git","data","env",".env",".venv","node_modules","__pycache__","artifacts"]);
function fail(code:ConstructorParameters<typeof ContractError>[0],message:string):never{throw new ContractError(code,message);}
function object(value:unknown,label:string):Record<string,unknown>{if(value===null||typeof value!=="object"||Array.isArray(value)||Object.getPrototypeOf(value)!==Object.prototype&&Object.getPrototypeOf(value)!==null)fail("invalid_request",`${label} must be a plain object`);return value as Record<string,unknown>;}
function exact(value:Record<string,unknown>,keys:string[],label:string):void{if(Object.keys(value).length!==keys.length||keys.some(key=>!Object.hasOwn(value,key)))fail("invalid_request",`${label} fields are missing or unknown`);}
function realDirectory(value:string,label:string):string{if(!path.isAbsolute(value))fail("invalid_request",`${label} must be absolute`);const info=lstatSync(value);if(!info.isDirectory()||info.isSymbolicLink())fail("permission_denied",`${label} must be a real directory`);return realpathSync.native(value);}
function inside(root:string,target:string):boolean{const relative=path.relative(root,target);return relative!==".."&&relative!==""&&!relative.startsWith(`..${path.sep}`)&&!path.isAbsolute(relative);}
function samePath(left:string,right:string):boolean{const a=path.resolve(left);const b=path.resolve(right);return process.platform==="win32"?a.toLowerCase()===b.toLowerCase():a===b;}
function validOwnedPath(value:unknown):value is string{return typeof value==="string"&&!!value&&!value.startsWith("/")&&!value.includes("\\")&&!value.includes(":")&&!value.split("/").some(part=>!part||part==="."||part===".."||BLOCKED_PATH_COMPONENTS.has(part.toLowerCase()));}
function controlRelease(value:unknown,runRoot:string):ControlRelease{
  const release=object(value,"control release");const keys=["release_id","root","node_executable","python_executable","manifest_path","manifest_digest"];exact(release,keys,"control release");
  if(typeof release.release_id!=="string"||!HASH.test(release.release_id)||release.manifest_digest!==release.release_id||typeof release.manifest_digest!=="string"||!HASH.test(release.manifest_digest))fail("identity_mismatch","control release digest is invalid");
  for(const key of ["root","node_executable","python_executable","manifest_path"] as const)if(typeof release[key]!=="string"||!path.isAbsolute(release[key]))fail("invalid_request",`control release ${key} must be absolute`);
  const releaseRoot=path.resolve(release.root as string);if(!inside(runRoot,releaseRoot)||path.resolve(release.manifest_path as string)!==path.join(releaseRoot,"manifest.json"))fail("permission_denied","control release must be sealed inside the marked run root");
  return structuredClone(release) as unknown as ControlRelease;
}
function parseScope(value:unknown,sandboxRoot:string,runRoot:string):WorkspaceScope{
  const scope=object(value,"workspace scope");exact(scope,["scope_id","source_root","owned_paths","state_root","read_only_roots"],"workspace scope");
  if(typeof scope.scope_id!=="string"||!UUID.test(scope.scope_id)||typeof scope.source_root!=="string"||!path.isAbsolute(scope.source_root)||typeof scope.state_root!=="string"||!path.isAbsolute(scope.state_root))fail("invalid_request","workspace scope identities and roots are invalid");
  const sourceRoot=realDirectory(scope.source_root,"workspace source root");const stateRoot=path.resolve(scope.state_root);
  if(!inside(sandboxRoot,sourceRoot)||!inside(runRoot,stateRoot))fail("permission_denied","workspace source/state roots escaped their trusted roots");
  if(sourceRoot===stateRoot||inside(sourceRoot,stateRoot)||inside(stateRoot,sourceRoot))fail("permission_denied","workspace source and state roots must be disjoint");
  if(!Array.isArray(scope.owned_paths)||scope.owned_paths.length<1||scope.owned_paths.length>256||scope.owned_paths.some(item=>!validOwnedPath(item))||new Set(scope.owned_paths.map(item=>(item as string).toLowerCase())).size!==scope.owned_paths.length)fail("permission_denied","workspace owned_paths are invalid or contain excluded components");
  if(!Array.isArray(scope.read_only_roots)||scope.read_only_roots.length>32||scope.read_only_roots.some(item=>typeof item!=="string"||!path.isAbsolute(item)))fail("invalid_request","workspace read_only_roots are invalid");
  const readOnly=scope.read_only_roots as string[];
  if(readOnly.some(root=>{const resolved=path.resolve(root);return resolved===sourceRoot||inside(resolved,sourceRoot);}))fail("permission_denied","workspace source overlaps a read-only root");
  return {scope_id:scope.scope_id,source_root:sourceRoot,owned_paths:(scope.owned_paths as string[]).slice(),state_root:stateRoot,read_only_roots:readOnly.map(root=>path.resolve(root))};
}

export function parseSupervisorProcessConfig(value:unknown,sandboxRootValue:string):SupervisorProcessConfig{
  const sandboxRoot=realDirectory(sandboxRootValue,"sandbox root");const artifacts=realDirectory(path.join(sandboxRoot,"artifacts"),"sandbox artifacts root");
  const config=object(value,"supervisor process config");const baseFields=["protocol","sandbox_root","run_root","host_bootstrap","scopes","runtime_paths","descriptors","profile_fixtures"];const hasDescriptorFactories=Object.hasOwn(config,"descriptor_factories");const expectedFields=hasDescriptorFactories?[...baseFields,"descriptor_factories"]:baseFields;if(Object.keys(config).length!==expectedFields.length||expectedFields.some(field=>!Object.hasOwn(config,field)))fail("invalid_request","supervisor process config fields are missing or unknown");
  if(config.protocol!=="my-agent-evolution-supervisor/1.0"||typeof config.sandbox_root!=="string"||path.resolve(config.sandbox_root)!==sandboxRoot||typeof config.run_root!=="string"||!path.isAbsolute(config.run_root))fail("invalid_request","supervisor process config protocol/root is invalid");
  const runRootLexical=path.resolve(config.run_root);if(!inside(artifacts,runRootLexical))fail("permission_denied","supervisor run root must remain inside sandbox/artifacts");
  const runRoot=realDirectory(runRootLexical,"supervisor run root");if(!inside(artifacts,runRoot))fail("permission_denied","supervisor run root resolved outside sandbox/artifacts");
  const marker=path.join(runRoot,".evolution-test-run");if(!existsSync(marker)||lstatSync(marker).isSymbolicLink()||awaitMarker(marker)!=="evolution-a")fail("permission_denied","supervisor run root marker is invalid");
  const host=object(config.host_bootstrap,"host bootstrap");exact(host,["pipe_name","host_instance_id","host_token","control_release","allowed_scope_ids"],"host bootstrap");
  if(typeof host.pipe_name!=="string"||!host.pipe_name.startsWith(PIPE_PREFIX)||host.pipe_name.length>240||host.pipe_name.slice(PIPE_PREFIX.length).includes("\\")||typeof host.host_instance_id!=="string"||!UUID.test(host.host_instance_id)||typeof host.host_token!=="string"||!TOKEN.test(host.host_token))fail("invalid_request","host bootstrap identity is malformed");
  const release=controlRelease(host.control_release,runRoot);
  if(!Array.isArray(config.scopes)||config.scopes.length<1||config.scopes.length>256)fail("invalid_request","supervisor requires a bounded trusted scope list");
  const scopes=(config.scopes as unknown[]).map(scope=>parseScope(scope,sandboxRoot,runRoot));const scopeIds=scopes.map(scope=>scope.scope_id);
  if(new Set(scopeIds).size!==scopeIds.length||!Array.isArray(host.allowed_scope_ids)||host.allowed_scope_ids.length!==scopeIds.length||host.allowed_scope_ids.some(id=>typeof id!=="string"||!UUID.test(id))||new Set(host.allowed_scope_ids).size!==scopeIds.length||scopeIds.some(id=>!(host.allowed_scope_ids as string[]).includes(id)))fail("permission_denied","host allowlist must exactly match the trusted scope registry");
  const runtimeObject=object(config.runtime_paths,"runtime path map");if(Object.keys(runtimeObject).length>8)fail("invalid_request","runtime path map exceeds its bound");const runtimePaths:Record<string,string>={};
  for(const [id,file] of Object.entries(runtimeObject)){if(!UUID.test(id)||typeof file!=="string"||!path.isAbsolute(file))fail("invalid_request","runtime path entry is invalid");const stat=lstatSync(file);if(!stat.isFile()||stat.isSymbolicLink())fail("permission_denied","runtime path must be a regular file");runtimePaths[id]=realpathSync.native(file);}
  if(!Array.isArray(config.descriptors)||config.descriptors.length>128)fail("invalid_request","candidate descriptor registry is invalid");const descriptors=(config.descriptors as unknown[]).map(raw=>{const item=object(raw,"candidate descriptor registry entry");exact(item,["artifact_id","descriptor"],"candidate descriptor registry entry");if(typeof item.artifact_id!=="string"||!UUID.test(item.artifact_id))fail("invalid_request","descriptor artifact id is invalid");return {artifact_id:item.artifact_id,descriptor:structuredClone(item.descriptor) as CandidateDescriptor};});
  if(new Set(descriptors.map(item=>item.artifact_id)).size!==descriptors.length)fail("invalid_request","candidate descriptor registry contains duplicate ids");
  if(!Array.isArray(config.profile_fixtures)||config.profile_fixtures.length>128)fail("invalid_request","profile fixture registry is invalid");const profiles=(config.profile_fixtures as unknown[]).map(raw=>{const item=object(raw,"profile fixture entry");exact(item,["id","root"],"profile fixture entry");if(typeof item.id!=="string"||!UUID.test(item.id)||typeof item.root!=="string"||!path.isAbsolute(item.root))fail("invalid_request","profile fixture entry is malformed");const root=path.resolve(item.root);if(!inside(runRoot,root))fail("permission_denied","profile fixture escaped the marked run root");return {id:item.id,root};});
  if(new Set(profiles.map(item=>item.id)).size!==profiles.length)fail("invalid_request","profile fixture registry contains duplicate ids");
  const descriptorFactories:SnapshotDescriptorFactory[]=[];
  if(hasDescriptorFactories){
    if(!Array.isArray(config.descriptor_factories)||config.descriptor_factories.length>1)fail("invalid_request","snapshot descriptor factory registry is invalid");
    for(const raw of config.descriptor_factories){
      const item=object(raw,"snapshot descriptor factory");exact(item,["artifact_id","kind","runtime_id","profile_fixture_id"],"snapshot descriptor factory");
      if(item.artifact_id!==T4_RECOVERY_DESCRIPTOR_ARTIFACT_ID||item.kind!=="t4-recovery-node-v1"||item.runtime_id!==T4_RECOVERY_RUNTIME_ID||item.profile_fixture_id!==T4_RECOVERY_PROFILE_FIXTURE_ID)fail("permission_denied","only the fixed T4 recovery descriptor factory is supported");
      descriptorFactories.push({artifact_id:String(item.artifact_id),kind:"t4-recovery-node-v1",runtime_id:String(item.runtime_id),profile_fixture_id:String(item.profile_fixture_id)});
    }
  }
  if(descriptorFactories.length){const factory=descriptorFactories[0];const expectedOwned=["evolve/tool-catalog/INDEX.md",...T4_RECOVERY_OWNED_PATHS];if(scopes.length!==1||JSON.stringify(scopes[0].owned_paths)!==JSON.stringify(expectedOwned)||descriptors.length!==0||Object.keys(runtimePaths).length!==1||!samePath(runtimePaths[factory.runtime_id],release.node_executable)||profiles.length!==1||profiles[0].id!==factory.profile_fixture_id||path.resolve(profiles[0].root)!==path.join(runRoot,"candidate-profile"))fail("permission_denied","T4 recovery factory requires its exact source scope, fixed Node runtime, and candidate profile");}
  return {protocol:"my-agent-evolution-supervisor/1.0",sandbox_root:sandboxRoot,run_root:runRoot,host_bootstrap:{pipe_name:host.pipe_name,host_instance_id:host.host_instance_id,host_token:host.host_token,control_release:release,allowed_scope_ids:scopeIds},scopes,runtime_paths:runtimePaths,descriptors,profile_fixtures:profiles,...(descriptorFactories.length?{descriptor_factories:descriptorFactories}:{})};
}

function awaitMarker(file:string):string{return readFileSync(file,"utf8").replace(/^\uFEFF/,"").trim();}

export async function createSupervisorRuntime(value:unknown,factories:Partial<SupervisorRuntimeFactories>={}):Promise<SupervisorRuntime>{
  const sandboxHint=(value as Record<string,unknown>|null)?.sandbox_root;
  if(typeof sandboxHint!=="string")fail("invalid_request","sandbox_root is required");
  const config=parseSupervisorProcessConfig(value,sandboxHint);
  const verifyRelease=factories.verifyRelease??verifyControlRelease;
  const createJournal=factories.createJournal??((root:string)=>new JournalStore(root));
  const createWorkspace=factories.createWorkspace??((input:{release:ControlRelease;sandboxRoot:string;runRoot:string})=>PythonWorkspaceClient.create(input));
  const createRunner=factories.createRunner??(()=>new LocalManagedRunner());
  await verifyRelease(config.host_bootstrap.control_release);
  const journalRoot=path.join(config.run_root,"supervisor-state");
  if(!inside(config.run_root,journalRoot))fail("permission_denied","journal root escaped the supervisor run root");
  const journal=createJournal(journalRoot);let closed=false;
  try{
    const bindings=new BindingStore(journal);const grants=new GrantStore(journal);
    const workspace=await createWorkspace({release:config.host_bootstrap.control_release,sandboxRoot:config.sandbox_root,runRoot:config.run_root});
    const runner=createRunner();
    const deps:SupervisorDeps={journal,bindings,grants,runner,workspace,scopes:new Map(config.scopes.map(scope=>[scope.scope_id,scope])),runRoot:config.run_root,runtimePaths:new Map(Object.entries(config.runtime_paths)),snapshots:new Map(),descriptors:new Map(config.descriptors.map(item=>[item.artifact_id,item.descriptor])),profileFixtures:new Map(config.profile_fixtures.map(item=>[item.id,item.root])),descriptorFactories:config.descriptor_factories??[]};
    const supervisor=new EvolutionSupervisor(deps);
    return {config,supervisor,journal,closeJournal(){if(closed)return;closed=true;journal.close();},isClosed:()=>closed};
  }catch(error){journal.close();throw error;}
}


