import { createHash, randomUUID } from "node:crypto";
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { appendFileSync, copyFileSync, existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, realpathSync, writeFileSync } from "node:fs";
import * as path from "node:path";
import { ContractError, canonicalJson, contentHash, type CandidateDescriptor, type Hash, type ProcessRecipe } from "../contracts";
import { candidateEnvironment } from "../environment";
import { freshBootNonce, validateProcessGraph, validateReadyMessage, type BuildReceipt, type CandidateRunner, type ComponentReceipt, type InstanceReceipt, type OwnedChild } from "../runner";

const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const HASH=/^sha256:[0-9a-f]{64}$/;
const MAX_ARTIFACT_BYTES=64*1024*1024;
const MAX_LOG_BYTES=10*1024*1024;
const MAX_READY_LINE=65_536;
const START_TIMEOUT_MS=180_000;
const STOP_TIMEOUT_MS=10_000;
type LocalAuthorization={authorization_id:string;kind:"candidate.build"|"candidate.launch";isolation_mode:"local_managed";candidate_id?:string};
type PrepareInput={descriptor:unknown;source_root:string;run_root:string;runtime_paths:Record<string,string>;authorization:LocalAuthorization};
type LaunchInput={profile_root:string;authorization:LocalAuthorization};
type ProcessState=OwnedChild&{recipe:ProcessRecipe;stdout_total:number;stderr_total:number;stderr_buffer:string;stderr_excerpt:string;exit_code:number|null;stdout_log_path:string;stderr_log_path:string;stdout_log_buffer:string;stderr_log_buffer:string;line_window_start:number;lines_in_window:number;ready_timer:NodeJS.Timeout|null;ready_resolve:(value:boolean)=>void;ready_promise:Promise<boolean>;closed_promise:Promise<number|null>;close_resolve:(code:number|null)=>void;spawn_error:Error|null};
type InternalInstance={receipt:InstanceReceipt;build:BuildReceipt;profileRoot:string;bootNonce:string;children:Map<string,ProcessState>;logBytes:number;stopping:boolean};
type LocalRunnerOptions={spawnProcess?:typeof spawn;startTimeoutMs?:number;stopTimeoutMs?:number};
function fail(code:ConstructorParameters<typeof ContractError>[0],message:string):never{throw new ContractError(code,message);}
function plain(value:unknown,label:string):Record<string,unknown>{if(value===null||typeof value!=="object"||Array.isArray(value)||Object.getPrototypeOf(value)!==Object.prototype&&Object.getPrototypeOf(value)!==null)fail("invalid_request",`${label} must be a plain object`);return value as Record<string,unknown>;}
function realDirectory(value:string,label:string):string{if(!path.isAbsolute(value))fail("invalid_request",`${label} must be absolute`);const real=realpathSync.native(value);const info=lstatSync(real);if(!info.isDirectory()||info.isSymbolicLink())fail("scope_conflict",`${label} must be a real directory`);return real;}
function regularFile(value:string,label:string):string{const info=lstatSync(value);if(!info.isFile()||info.isSymbolicLink())fail("scope_conflict",`${label} must be a regular file`);return realpathSync.native(value);}
function inside(root:string,target:string):boolean{const relative=path.relative(root,target);return relative!==""&&relative!==".."&&!relative.startsWith(`..${path.sep}`)&&!path.isAbsolute(relative);}
function digest(bytes:Buffer):Hash{return `sha256:${createHash("sha256").update(bytes).digest("hex")}`;}
function validateAuthorization(auth:LocalAuthorization,kind:LocalAuthorization["kind"],candidateId?:string):void{
  if(!auth||!UUID.test(auth.authorization_id)||auth.kind!==kind||auth.isolation_mode!=="local_managed")fail("permission_denied",`a confirmed ${kind} local_managed authorization is required`);
  if(candidateId!==undefined&&auth.candidate_id!==candidateId)fail("permission_denied","launch authorization is bound to another candidate");
}
function validateDescriptor(value:unknown):CandidateDescriptor{
  const d=plain(value,"candidate descriptor") as unknown as CandidateDescriptor;
  if(d.descriptor_version!=="1.0"||!HASH.test(d.source_snapshot_id)||d.isolation_mode!=="local_managed"||d.mode!=="sealed_build")fail("unsupported_capability","A local builder accepts only sealed local_managed descriptors");
  if(!Array.isArray(d.dependency_lock)||d.dependency_lock.length!==0)fail("unsupported_capability","A fixture builder does not install candidate dependencies");
  if(!Array.isArray(d.required_capabilities)||d.required_capabilities.length!==0)fail("unsupported_capability","A fixture builder supports no guest capabilities");
  if(!d.build_recipe||!d.adapter||!HASH.test(d.build_recipe.sha256)||!HASH.test(d.adapter.sha256))fail("invalid_request","descriptor artifacts are malformed");
  return d;
}
function safeSnapshotFile(snapshotRoot:string,relative:string):string{
  if(!relative||path.isAbsolute(relative)||relative.includes("\\")||relative.split("/").some(part=>!part||part==="."||part===".."))fail("permission_denied","candidate file path escapes the sealed snapshot");
  const candidate=path.resolve(snapshotRoot,...relative.split("/"));if(!inside(snapshotRoot,candidate))fail("permission_denied","candidate file escaped snapshot root");
  let current=snapshotRoot;
  for(const component of relative.split("/")){current=path.join(current,component);const info=lstatSync(current);const attributes=(info as typeof info & {attributes?:number;fileAttributes?:number}).attributes??(info as typeof info & {fileAttributes?:number}).fileAttributes??0;if(info.isSymbolicLink()||(attributes&0x400)!==0)fail("permission_denied","candidate snapshot contains a symlink/reparse point");}
  const real=regularFile(candidate,"sealed snapshot file");if(!inside(snapshotRoot,real))fail("permission_denied","candidate file resolved outside snapshot root");return real;
}
function writeJsonNew(file:string,value:unknown):void{writeFileSync(file,`${canonicalJson(value)}\n`,{flag:"wx"});}
function appendRedactedLine(file:string,line:string,nonce:string):void{appendFileSync(file,`${line.split(nonce).join("[redacted]")}\n`,"utf8");}

export class LocalManagedRunner implements CandidateRunner {
  readonly lifecycle="declared_components_only" as const;
  private readonly builds=new Map<string,BuildReceipt&{descriptor:CandidateDescriptor;runtime_paths:Record<string,string>}>();
  private readonly instances=new Map<string,InternalInstance>();
  private readonly spawnProcess:typeof spawn;
  private readonly startTimeoutMs:number;
  private readonly stopTimeoutMs:number;
  constructor(options:LocalRunnerOptions={}){
    this.spawnProcess=options.spawnProcess??spawn;
    this.startTimeoutMs=options.startTimeoutMs??START_TIMEOUT_MS;
    this.stopTimeoutMs=options.stopTimeoutMs??STOP_TIMEOUT_MS;
    if(!Number.isSafeInteger(this.startTimeoutMs)||this.startTimeoutMs<1||this.startTimeoutMs>START_TIMEOUT_MS||!Number.isSafeInteger(this.stopTimeoutMs)||this.stopTimeoutMs<1||this.stopTimeoutMs>STOP_TIMEOUT_MS)fail("invalid_request","runner timeout is outside its safety bound");
  }

  async prepare(input:PrepareInput):Promise<BuildReceipt>{
    validateAuthorization(input.authorization,"candidate.build");
    const descriptor=validateDescriptor(input.descriptor);
    const processes=validateProcessGraph(descriptor);
    if(processes.some(proc=>proc.readiness==="artifact_probe"))fail("unsupported_capability","A fixture runner does not implement artifact_probe readiness");
    const runRoot=realDirectory(input.run_root,"run_root");
    const runMarker=path.join(runRoot,".evolution-test-run");
    if(readFileSync(runMarker,"utf8").replace(/^\uFEFF/,"").trim()!=="evolution-a")fail("permission_denied","candidate build root is not a marked evolution run directory");
    const sourceRoot=realDirectory(input.source_root,"source_root");
    if(!inside(runRoot,sourceRoot)&&sourceRoot!==runRoot)fail("permission_denied","snapshot source must be inside the authorized run root");
    if(Object.keys(input.runtime_paths).length!==descriptor.runtimes.length)fail("invalid_request","runtime path map must exactly match the declared runtimes");
    const snapshotManifestPath=safeSnapshotFile(path.dirname(sourceRoot),"manifest.json");
    const snapshotManifest=JSON.parse(readFileSync(snapshotManifestPath,"utf8")) as unknown;
    if(contentHash(snapshotManifest)!==descriptor.source_snapshot_id)fail("identity_mismatch","sealed source manifest does not match source_snapshot_id");
    const buildId=randomUUID();
    const buildsRoot=path.join(runRoot,"builds");
    if(existsSync(buildsRoot)){if(lstatSync(buildsRoot).isSymbolicLink()||!lstatSync(buildsRoot).isDirectory())fail("scope_conflict","build output parent is not a real directory");}
    else mkdirSync(buildsRoot,{recursive:false});
    const outputRoot=path.join(buildsRoot,buildId);
    mkdirSync(outputRoot,{recursive:false});
    const runtimePaths:Record<string,string>={};
    const runtimeDigests:Record<string,Hash>={};
    try{
      mkdirSync(path.join(outputRoot,"workspace"),{recursive:false});
      mkdirSync(path.join(outputRoot,"runtimes"),{recursive:false});
      const entrypointDirectories=new Set(processes.map(proc=>path.posix.dirname(proc.entrypoint)));
      if(entrypointDirectories.size!==1)fail("unsupported_capability","a fixture build requires one fixed candidate manifest directory");
      const candidateManifestRelative=path.posix.join([...entrypointDirectories][0],"manifest.json");
      const referenced=new Set(processes.map(proc=>proc.entrypoint));
      referenced.add(candidateManifestRelative);
      const manifestObject=plain(snapshotManifest,"source snapshot manifest");
      if(!Array.isArray(manifestObject.files))fail("invalid_request","source snapshot manifest file list is missing");
      const manifestEntries=new Map<string,{byte_length:number;sha256:string}>();
      for(const entry of manifestObject.files){
        const file=plain(entry,"source snapshot file entry");
        if(typeof file.path!=="string"||!Number.isSafeInteger(file.byte_length)||!HASH.test(String(file.sha256))||manifestEntries.has(file.path))fail("invalid_request","source snapshot manifest entry is malformed");
        manifestEntries.set(file.path,{byte_length:file.byte_length as number,sha256:String(file.sha256)});
      }
      const manifestPaths=[...manifestEntries.keys()];
      if(manifestPaths.length!==referenced.size||manifestPaths.some(file=>!referenced.has(file)))fail("unsupported_capability","A fixture build requires exactly the declared entrypoints and manifest file");
      const outputFiles:Record<string,Hash>={};
      for(const relative of [...referenced].sort()){
        const source=safeSnapshotFile(sourceRoot,relative);
        const bytes=readFileSync(source);
        if(bytes.length>MAX_ARTIFACT_BYTES)fail("budget_exceeded",`candidate fixture file exceeds limit: ${relative}`);
        const expected=manifestEntries.get(relative);
        if(!expected||bytes.length!==expected.byte_length||digest(bytes)!==expected.sha256)fail("identity_mismatch",`source snapshot bytes changed for ${relative}`);
        const target=path.resolve(outputRoot,"workspace",...relative.split("/"));
        if(!inside(path.join(outputRoot,"workspace"),target))fail("permission_denied","build output path escaped workspace");
        mkdirSync(path.dirname(target),{recursive:true});
        writeFileSync(target,bytes,{flag:"wx"});
        outputFiles[relative]=digest(bytes);
      }
      for(const recipe of processes){
        const entry=path.join(outputRoot,"workspace",...recipe.entrypoint.split("/"));
        const syntax=spawnSync(process.execPath,["--check",entry],{cwd:path.dirname(entry),env:candidateEnvironment({profileRoot:path.join(outputRoot,"build-check"),runtimePath:process.execPath}),encoding:"utf8",windowsHide:true,shell:false,timeout:10_000,maxBuffer:64*1024});
        if(syntax.error||syntax.status!==0)fail("build_failed",`Node syntax check failed for declared component ${recipe.component_id}`);
      }
      for(const runtime of descriptor.runtimes){
        if(runtime.kind!=="node")fail("unsupported_capability","A local fixture builder supports only Node runtime");
        const provided=input.runtime_paths[runtime.runtime_id];
        if(!provided)fail("invalid_request","runtime path is missing");
        const executable=regularFile(provided,"trusted runtime executable");
        const runtimeBytes=readFileSync(executable);
        const runtimeHash=digest(runtimeBytes);
        if(runtimeHash!==runtime.distribution.sha256)fail("identity_mismatch","trusted runtime bytes do not match the declared distribution artifact");
        const suffix=process.platform==="win32"?".exe":"";
        const target=path.join(outputRoot,"runtimes",`${runtime.runtime_id}${suffix}`);
        copyFileSync(executable,target);
        if(digest(readFileSync(target))!==runtimeHash)fail("identity_mismatch","copied runtime bytes differ from the verified input");
        runtimePaths[runtime.runtime_id]=target;runtimeDigests[runtime.runtime_id]=runtimeHash;
        outputFiles[`runtime/${runtime.runtime_id}`]=runtimeHash;
      }
      const manifest=JSON.parse(readFileSync(path.join(sourceRoot,...candidateManifestRelative.split("/")),"utf8")) as unknown;
      const candidateManifest=plain(manifest,"candidate fixture manifest");
      if(candidateManifest.broken!==true&&candidateManifest.broken!==false)fail("invalid_request","fixture manifest must declare a boolean broken state");
      const parserBytes=readFileSync(process.execPath);
      const hostParser={executable_sha256:digest(parserBytes),version:process.version};
      const buildFingerprint=contentHash({source_snapshot_id:descriptor.source_snapshot_id,descriptor,runtime_digests:runtimeDigests,artifact_hashes:outputFiles,host_parser:hostParser,platform:process.platform,arch:process.arch});
      const candidateId=contentHash({source_snapshot_id:descriptor.source_snapshot_id,build_fingerprint:buildFingerprint,data_contract_digest:descriptor.data_contract_digest,isolation_mode:descriptor.isolation_mode});
      const receipt:BuildReceipt&{descriptor:CandidateDescriptor;runtime_paths:Record<string,string>}={build_id:buildId,source_snapshot_id:descriptor.source_snapshot_id,build_fingerprint:buildFingerprint,candidate_id:candidateId,output_root:outputRoot,artifact_hashes:outputFiles,isolation_mode:"local_managed",descriptor,runtime_paths:runtimePaths};
      writeJsonNew(path.join(outputRoot,"build-receipt.json"),{build_id:buildId,source_snapshot_id:receipt.source_snapshot_id,build_fingerprint:receipt.build_fingerprint,candidate_id:candidateId,output_root:outputRoot,artifact_hashes:outputFiles,isolation_mode:"local_managed"});
      this.builds.set(buildId,receipt);
      return structuredClone(receipt);
    }catch(error){
      // Failed build roots are kept as evidence; never remove an unowned/partial tree automatically.
      throw error;
    }
  }

  async launch(build:BuildReceipt,input:LaunchInput):Promise<InstanceReceipt>{
    validateAuthorization(input.authorization,"candidate.launch",build.candidate_id);
    const stored=this.builds.get(build.build_id);
    if(!stored||stored.candidate_id!==build.candidate_id||stored.build_fingerprint!==build.build_fingerprint)fail("identity_mismatch","build receipt is not owned by this runner instance");
    if(this.instances.has(input.authorization.authorization_id))fail("identity_mismatch","launch authorization was already used");
    const profileRoot=path.resolve(input.profile_root);
    if(!path.isAbsolute(input.profile_root))fail("invalid_request","candidate profile root must be absolute");
    const runRoot=path.dirname(path.dirname(stored.output_root));
    if(!inside(runRoot,profileRoot))fail("permission_denied","candidate profile must remain under the marked run root");
    mkdirSync(profileRoot,{recursive:false});
    for(const directory of ["tmp","appdata","localappdata"])mkdirSync(path.join(profileRoot,directory),{recursive:false});
    const instanceId=randomUUID();const nonce=freshBootNonce();
    const logsRoot=path.join(runRoot,"candidate-logs");
    if(existsSync(logsRoot)){const info=lstatSync(logsRoot);const attributes=(info as typeof info & {attributes?:number}).attributes??0;if(!info.isDirectory()||info.isSymbolicLink()||(attributes&0x400)!==0||realpathSync.native(logsRoot)!==logsRoot||!inside(runRoot,logsRoot))fail("scope_conflict","candidate log root is not a fixed regular run child");}else mkdirSync(logsRoot,{recursive:false});
    const instanceLogRoot=path.join(logsRoot,instanceId);mkdirSync(instanceLogRoot,{recursive:false});
    const ordered=validateProcessGraph(stored.descriptor);
    const owned=new Map<string,ProcessState>();
    const receipt:InstanceReceipt={candidate_id:build.candidate_id,instance_id:instanceId,components:ordered.map(proc=>({component_id:proc.component_id,pid:null,ready:false,exit_observed:false,exit_code:null,stderr_excerpt:""})),degraded_components:[],status:"starting",stop_scope:"declared_processes",descendants_verified:false};
    const state:InternalInstance={receipt,build:stored,profileRoot,bootNonce:nonce,children:owned,logBytes:0,stopping:false};
    this.instances.set(instanceId,state);
    try{
      for(const recipe of ordered){
        const failedDependency=recipe.depends_on.some(id=>!owned.get(id)?.ready);
        if(failedDependency)fail("start_failed","required component dependency did not become ready");
        const runtime=stored.runtime_paths[recipe.runtime_id];
        if(!runtime)fail("environment_not_ready","declared runtime is missing from the frozen build");
        const cwd=path.resolve(stored.output_root,"workspace",...recipe.cwd.split("/").filter(Boolean));
        if(cwd!==path.join(stored.output_root,"workspace")&&!inside(path.join(stored.output_root,"workspace"),cwd))fail("permission_denied","component cwd escaped the candidate workspace");
        const entry=safeSnapshotFile(path.join(stored.output_root,"workspace"),recipe.entrypoint);
        const componentProfile=path.join(profileRoot,recipe.component_id);
        mkdirSync(componentProfile,{recursive:false});
        for(const directory of ["tmp","appdata","localappdata"])mkdirSync(path.join(componentProfile,directory),{recursive:false});
        const stdoutLogPath=path.join(instanceLogRoot,`${recipe.component_id}.stdout.log`);const stderrLogPath=path.join(instanceLogRoot,`${recipe.component_id}.stderr.log`);writeFileSync(stdoutLogPath,"",{flag:"wx"});writeFileSync(stderrLogPath,"",{flag:"wx"});
        const child=this.spawnProcess(runtime,[entry,...recipe.argv],{cwd,env:candidateEnvironment({profileRoot:componentProfile,runtimePath:runtime}),stdio:["pipe","pipe","pipe"],shell:false,windowsHide:true});
        let resolveReady!:(value:boolean)=>void;const readyPromise=new Promise<boolean>(resolve=>{resolveReady=resolve;});
        let resolveClose!:(code:number|null)=>void;const closedPromise=new Promise<number|null>(resolve=>{resolveClose=resolve;});
        const ownedChild:ProcessState={component_id:recipe.component_id,child,instance_id:instanceId,ready:false,exit_observed:false,boot_nonce:nonce,stdout_buffer:"",stderr_buffer:"",stderr_bytes:0,recipe,stdout_total:0,stderr_total:0,stderr_excerpt:"",exit_code:null,stdout_log_path:stdoutLogPath,stderr_log_path:stderrLogPath,stdout_log_buffer:"",stderr_log_buffer:"",line_window_start:Date.now(),lines_in_window:0,ready_timer:null,ready_resolve:resolveReady,ready_promise:readyPromise,closed_promise:closedPromise,close_resolve:resolveClose,spawn_error:null};
        owned.set(recipe.component_id,ownedChild);
        const component=receipt.components.find(item=>item.component_id===recipe.component_id)!;component.pid=child.pid??null;
        child.once("spawn",()=>{if(recipe.readiness==="process_alive"&&!ownedChild.ready){ownedChild.ready=true;ownedChild.ready_resolve(true);}});
        child.once("error",error=>{ownedChild.spawn_error=error;ownedChild.ready_resolve(false);});
        child.once("close",code=>{try{if(ownedChild.stdout_log_buffer)appendRedactedLine(ownedChild.stdout_log_path,ownedChild.stdout_log_buffer,nonce);if(ownedChild.stderr_log_buffer)appendRedactedLine(ownedChild.stderr_log_path,ownedChild.stderr_log_buffer,nonce);}catch{state.receipt.status="failed";}ownedChild.stdout_log_buffer="";ownedChild.stderr_log_buffer="";ownedChild.exit_observed=true;ownedChild.exit_code=code;component.exit_observed=true;component.exit_code=code;component.stderr_excerpt=ownedChild.stderr_excerpt;ownedChild.close_resolve(code);if(!ownedChild.ready)ownedChild.ready_resolve(false);if(!state.stopping&&code!==0&&recipe.required)state.receipt.status="failed";});
        const acceptLine=():boolean=>{
          const now=Date.now();if(now-ownedChild.line_window_start>=1000){ownedChild.line_window_start=now;ownedChild.lines_in_window=0;}
          ownedChild.lines_in_window+=1;
          if(ownedChild.lines_in_window>100){state.receipt.status="failed";ownedChild.child.kill();ownedChild.ready_resolve(false);return false;}
          return true;
        };
        const consume=(chunk:Buffer,isStderr:boolean)=>{
          state.logBytes+=chunk.length;if(state.logBytes>MAX_LOG_BYTES){ownedChild.child.kill();state.receipt.status="failed";return;}
          if(isStderr){ownedChild.stderr_total+=chunk.length;ownedChild.stderr_bytes+=chunk.length;ownedChild.stderr_excerpt=(ownedChild.stderr_excerpt+chunk.toString("utf8")).split(nonce).join("[redacted]").slice(0,1024);ownedChild.stderr_log_buffer+=chunk.toString("utf8");for(;;){const newline=ownedChild.stderr_log_buffer.indexOf("\n");if(newline<0)break;const line=ownedChild.stderr_log_buffer.slice(0,newline).replace(/\r$/u,"");ownedChild.stderr_log_buffer=ownedChild.stderr_log_buffer.slice(newline+1);try{appendRedactedLine(ownedChild.stderr_log_path,line,nonce);}catch{ownedChild.child.kill();state.receipt.status="failed";return;}}ownedChild.stderr_buffer+=chunk.toString("utf8");if(Buffer.byteLength(ownedChild.stderr_buffer,"utf8")>MAX_READY_LINE){ownedChild.child.kill();state.receipt.status="failed";return;}
            for(;;){const newline=ownedChild.stderr_buffer.indexOf("\n");if(newline<0)break;ownedChild.stderr_buffer=ownedChild.stderr_buffer.slice(newline+1);if(!acceptLine())return;}return;}
          ownedChild.stdout_total+=chunk.length;ownedChild.stdout_log_buffer+=chunk.toString("utf8");for(;;){const newline=ownedChild.stdout_log_buffer.indexOf("\n");if(newline<0)break;const line=ownedChild.stdout_log_buffer.slice(0,newline).replace(/\r$/u,"");ownedChild.stdout_log_buffer=ownedChild.stdout_log_buffer.slice(newline+1);try{appendRedactedLine(ownedChild.stdout_log_path,line,nonce);}catch{ownedChild.child.kill();state.receipt.status="failed";return;}}ownedChild.stdout_buffer+=chunk.toString("utf8");
          if(Buffer.byteLength(ownedChild.stdout_buffer,"utf8")>MAX_READY_LINE){ownedChild.child.kill();state.receipt.status="failed";return;}
          for(;;){const newline=ownedChild.stdout_buffer.indexOf("\n");if(newline<0)break;const line=ownedChild.stdout_buffer.slice(0,newline).replace(/\r$/u,"");ownedChild.stdout_buffer=ownedChild.stdout_buffer.slice(newline+1);if(!acceptLine())return;if(!line)continue;
            try{const message=JSON.parse(line) as unknown;const checked=validateReadyMessage(message,{candidate_id:build.candidate_id,instance_id:instanceId,boot_nonce:nonce},[recipe.component_id]);if(checked.ok&&!ownedChild.ready){ownedChild.ready=true;ownedChild.ready_resolve(true);}}
            catch{/* arbitrary stdout is untrusted candidate output, never interpreted as control */}
          }
        };
        child.stdout?.on("data",chunk=>consume(Buffer.from(chunk),false));child.stderr?.on("data",chunk=>consume(Buffer.from(chunk),true));
        ownedChild.ready_timer=setTimeout(()=>ownedChild.ready_resolve(false),this.startTimeoutMs);ownedChild.ready_timer.unref();
        child.stdin?.write(`${JSON.stringify({type:"boot",candidate_id:build.candidate_id,instance_id:instanceId,boot_nonce:nonce,component_id:recipe.component_id})}\n`);
        const ready=await readyPromise;
        if(ownedChild.ready_timer)clearTimeout(ownedChild.ready_timer);
        if(!ready){
          if(!recipe.required){state.receipt.degraded_components.push(recipe.component_id);try{child.kill();}catch{/* child exit state remains observed below */}continue;}
          state.receipt.status="failed";await this.stopOwned(state,true);return structuredClone(state.receipt);
        }
        component.ready=true;
      }
      state.receipt.status="ready";return structuredClone(state.receipt);
    }catch(error){state.receipt.status="failed";await this.stopOwned(state,true);if(error instanceof ContractError)throw error;throw new ContractError("start_failed",error instanceof Error?error.message:"candidate startup failed");}
  }

  async stop(instanceId:string):Promise<InstanceReceipt>{
    const state=this.instances.get(instanceId);if(!state)fail("permission_denied","instance is not owned by this runner");
    await this.stopOwned(state);return structuredClone(state.receipt);
  }

  inspect(instanceId:string):InstanceReceipt{
    const state=this.instances.get(instanceId);if(!state)fail("permission_denied","instance is not owned by this runner");
    for(const component of state.receipt.components){const child=state.children.get(component.component_id);if(child){component.pid=child.child.pid??null;component.ready=child.ready;component.exit_observed=child.exit_observed;}}
    return structuredClone(state.receipt);
  }

  private async stopOwned(state:InternalInstance,startupFailure=false):Promise<void>{
    state.stopping=true;state.receipt.status="stopping";
    const reverse=[...state.children.values()].reverse();
    for(const owned of reverse){
      if(owned.exit_observed)continue;
      try{owned.child.stdin?.write(`${JSON.stringify({type:"stop",instance_id:state.receipt.instance_id,boot_nonce:state.bootNonce})}\n`);}catch{/* inspect exit below */}
      const exited=await Promise.race([owned.closed_promise.then(()=>true),new Promise<boolean>(resolve=>setTimeout(()=>resolve(false),this.stopTimeoutMs))]);
      if(!exited){try{owned.child.kill();}catch{/* retain quarantined state */}}
      const confirmed=exited||await Promise.race([owned.closed_promise.then(()=>true),new Promise<boolean>(resolve=>setTimeout(()=>resolve(false),Math.min(this.stopTimeoutMs,1000)))]);
      owned.exit_observed=confirmed;const component=state.receipt.components.find(item=>item.component_id===owned.component_id);if(component){component.exit_observed=confirmed;component.exit_code=owned.exit_code;component.stderr_excerpt=owned.stderr_excerpt;}
    }
    const allExited=[...state.children.values()].every(item=>item.exit_observed);
    state.receipt.status=allExited?(startupFailure?"failed":"stopped"):"quarantined";
  }
}
