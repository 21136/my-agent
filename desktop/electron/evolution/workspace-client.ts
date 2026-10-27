import { createHash, randomUUID } from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, lstatSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import * as path from "node:path";
import { ContractError, canonicalJson, parseJsonBytes, type WorkspaceScope } from "./contracts";
import { verifyControlRelease, type ControlRelease } from "./control-release";

const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const MAX_WORKER_BYTES=65_536;
const MAX_WORKER_TIMEOUT_MS=600_000;
type SpawnProcess=typeof spawn;
type WorkerResult=Record<string,unknown>;
export type PythonWorkspaceClientOptions={release:ControlRelease;sandboxRoot:string;runRoot:string;spawnProcess?:SpawnProcess;timeoutMs?:number};
function fail(code:ConstructorParameters<typeof ContractError>[0],message:string):never{throw new ContractError(code,message);}
function inside(parent:string,child:string):boolean{const relative=path.relative(parent,child);return relative!==""&&relative!==".."&&!relative.startsWith(`..${path.sep}`)&&!path.isAbsolute(relative);}
function realDirectory(value:string,label:string):string{const resolved=path.resolve(value);const info=lstatSync(resolved);if(!info.isDirectory()||info.isSymbolicLink())fail("permission_denied",`${label} must be a real directory`);return realpathSync.native(resolved);}
function unknownWorkerResult(message="workspace worker outcome is unknown"):WorkerResult{return{ok:false,error:{code:"outcome_unknown",message}};}
function validWorkerError(value:unknown):value is WorkerResult{if(value===null||typeof value!=="object"||Array.isArray(value))return false;const result=value as Record<string,unknown>;if(Object.keys(result).length!==2||result.ok!==false||result.error===null||typeof result.error!=="object"||Array.isArray(result.error))return false;const error=result.error as Record<string,unknown>;return Object.keys(error).length===2&&typeof error.code==="string"&&typeof error.message==="string"&&error.message.length<=512;}

export class PythonWorkspaceClient{
  private readonly release:ControlRelease;private readonly sandboxRoot:string;private readonly runRoot:string;private readonly pythonRoot:string;private readonly spawnProcess:SpawnProcess;private readonly timeoutMs:number;
  private constructor(options:PythonWorkspaceClientOptions,sandboxRoot:string,runRoot:string){this.release=options.release;this.sandboxRoot=sandboxRoot;this.runRoot=runRoot;this.pythonRoot=path.join(options.release.root,"python");this.spawnProcess=options.spawnProcess??spawn;this.timeoutMs=options.timeoutMs??600_000;}
  static async create(options:PythonWorkspaceClientOptions):Promise<PythonWorkspaceClient>{await verifyControlRelease(options.release);const sandboxRoot=realDirectory(options.sandboxRoot,"sandbox root");const runRoot=realDirectory(options.runRoot,"workspace run root");const artifacts=realDirectory(path.join(sandboxRoot,"artifacts"),"sandbox artifacts root");if(!inside(artifacts,runRoot))fail("permission_denied","workspace run root escaped sandbox/artifacts");const marker=path.join(runRoot,".evolution-test-run");if(!existsSync(marker)||readFileSync(marker,"utf8").replace(/^\uFEFF/,"").trim()!=="evolution-a")fail("permission_denied","workspace run root marker is missing");const timeout=options.timeoutMs??600_000;if(!Number.isSafeInteger(timeout)||timeout<1||timeout>MAX_WORKER_TIMEOUT_MS)fail("invalid_request","workspace worker timeout is outside the supported range");return new PythonWorkspaceClient(options,sandboxRoot,runRoot);}
  async inspect(scope:WorkspaceScope,query:Record<string,unknown>,operationId:string):Promise<WorkerResult>{return await this.call(scope,operationId,"inspect",query);}
  async patch(scope:WorkspaceScope,patch:Record<string,unknown>,operationId:string):Promise<WorkerResult>{return await this.call(scope,operationId,"patch",patch);}
  async snapshot(scope:WorkspaceScope,includePaths:string[],workspaceRevision:number,operationId:string):Promise<WorkerResult>{return await this.call(scope,operationId,"snapshot",{workspace_revision:workspaceRevision,include_paths:includePaths});}
  private async call(scope:WorkspaceScope,operationId:string,action:"inspect"|"patch"|"snapshot",payload:Record<string,unknown>):Promise<WorkerResult>{
    if(!UUID.test(operationId))fail("invalid_request","workspace operation id must be a canonical UUID");await verifyControlRelease(this.release);if(!scope||!UUID.test(scope.scope_id)||!path.isAbsolute(scope.source_root)||!path.isAbsolute(scope.state_root))fail("invalid_request","workspace scope is malformed");
    const configRoot=path.join(this.runRoot,"workspace-config");if(existsSync(configRoot)){const real=realDirectory(configRoot,"workspace configuration root");if(!inside(this.runRoot,real))fail("permission_denied","workspace configuration root escaped the run");}else mkdirSync(configRoot,{recursive:false});
    const config={scope_id:scope.scope_id,source_root:scope.source_root,owned_paths:scope.owned_paths,state_root:scope.state_root,read_only_roots:scope.read_only_roots};const configBytes=Buffer.from(`${canonicalJson(config)}\n`,"utf8");const configPath=path.join(configRoot,`${operationId}-${randomUUID()}.json`);writeFileSync(configPath,configBytes,{flag:"wx"});
    const input=Buffer.from(canonicalJson({operation_id:operationId,action,payload}),"utf8");if(input.length>MAX_WORKER_BYTES)return unknownWorkerResult("workspace worker request exceeds 64 KiB");
    const env:NodeJS.ProcessEnv={};for(const key of ["SYSTEMROOT","WINDIR","TEMP","TMP"]){const value=process.env[key];if(value)env[key]=value;}env.PYTHONDONTWRITEBYTECODE="1";env.PYTHONNOUSERSITE="1";env.EVOLUTION_SANDBOX_ROOT=this.sandboxRoot;env.EVOLUTION_WORKSPACE_CONFIG=configPath;env.EVOLUTION_WORKSPACE_CONFIG_SHA256=createHash("sha256").update(configBytes).digest("hex");
    return await new Promise<WorkerResult>(resolve=>{let settled=false;let outputBytes=0;let errorBytes=0;const output:Buffer[]=[];let child:ChildProcess|null=null;let timer:NodeJS.Timeout|null=null;const finish=(value:WorkerResult)=>{if(settled)return;settled=true;if(timer)clearTimeout(timer);resolve(value);};const stop=()=>{try{child?.kill();}catch{/* outcome remains unknown */}};
      try{child=this.spawnProcess(this.release.python_executable,["-m","evolution.workspace_worker"],{cwd:this.pythonRoot,env,stdio:["pipe","pipe","pipe"],shell:false,windowsHide:true});}catch{return finish(unknownWorkerResult("trusted workspace worker could not be started"));}
      if(!child.stdin||!child.stdout||!child.stderr)return finish(unknownWorkerResult("trusted workspace worker has incomplete stdio"));
      timer=setTimeout(()=>{stop();finish(unknownWorkerResult("trusted workspace worker timed out"));},this.timeoutMs);timer.unref();
      child.stdout.on("data",chunk=>{const bytes=Buffer.from(chunk);outputBytes+=bytes.length;if(outputBytes>MAX_WORKER_BYTES){stop();finish(unknownWorkerResult("workspace worker output exceeded 64 KiB"));return;}output.push(bytes);});
      child.stderr.on("data",chunk=>{errorBytes+=Buffer.from(chunk).length;if(errorBytes>MAX_WORKER_BYTES){stop();finish(unknownWorkerResult("workspace worker diagnostics exceeded 64 KiB"));}});
      child.once("error",()=>finish(unknownWorkerResult("trusted workspace worker process failed")));
      child.once("close",code=>{if(settled)return;let result:unknown;try{result=parseJsonBytes(Buffer.concat(output,outputBytes));}catch{return finish(unknownWorkerResult("workspace worker returned invalid or incomplete JSON"));}if(code===0&&result!==null&&typeof result==="object"&&!Array.isArray(result)&&(result as Record<string,unknown>).ok===true)return finish(result as WorkerResult);if(code===2&&validWorkerError(result))return finish(result);finish(unknownWorkerResult("workspace worker exited without a verified result"));});
      child.stdin.once("error",()=>{stop();finish(unknownWorkerResult("workspace worker request could not be delivered"));});try{child.stdin.end(input);}catch{stop();finish(unknownWorkerResult("workspace worker request could not be delivered"));}
    });
  }
}
