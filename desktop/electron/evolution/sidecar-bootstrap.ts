import * as path from "node:path";
import { canonicalJson, type Hash } from "./contracts";
import type { ControlRelease } from "./control-release";

export type EvolutionSidecarBootstrap={
  protocol:"my-agent-evolution-sidecar/1.0";
  control_release:ControlRelease;
  pipe_name:string;
  host_instance_id:string;
  host_token:string;
  scope_id:string;
  owned_paths:string[];
};
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const HASH=/^sha256:[0-9a-f]{64}$/;
const TOKEN=/^[0-9a-f]{64}$/;
const PIPE_PREFIX="\\\\.\\pipe\\";
const MAX_BOOTSTRAP_BYTES=65_536;
function fail(message:string):never{throw new TypeError(message);}
function safeOwnedPath(value:unknown):value is string{return typeof value==="string"&&!!value&&!value.startsWith("/")&&!value.includes("\\")&&!value.includes(":")&&!value.split("/").some(part=>!part||part==="."||part==="..");}

export function encodeEvolutionSidecarBootstrap(value:unknown):Buffer{
  if(value===null||typeof value!=="object"||Array.isArray(value))fail("sidecar bootstrap must be an object");
  const item=value as Record<string,unknown>;
  const fields=["protocol","control_release","pipe_name","host_instance_id","host_token","scope_id","owned_paths"];
  if(Object.keys(item).length!==fields.length||fields.some(key=>!Object.hasOwn(item,key))||item.protocol!=="my-agent-evolution-sidecar/1.0")fail("sidecar bootstrap fields are invalid");
  if(typeof item.pipe_name!=="string"||!item.pipe_name.startsWith(PIPE_PREFIX)||item.pipe_name.length>240||item.pipe_name.slice(PIPE_PREFIX.length).includes("\\"))fail("sidecar bootstrap pipe name is invalid");
  for(const key of ["host_instance_id","scope_id"] as const)if(typeof item[key]!=="string"||!UUID.test(item[key]))fail(`sidecar bootstrap ${key} is invalid`);
  if(typeof item.host_token!=="string"||!TOKEN.test(item.host_token))fail("sidecar bootstrap token is invalid");
  if(item.control_release===null||typeof item.control_release!=="object"||Array.isArray(item.control_release))fail("sidecar bootstrap control release is invalid");
  const release=item.control_release as Record<string,unknown>;
  const releaseKeys=["release_id","root","node_executable","python_executable","manifest_path","manifest_digest"];
  if(Object.keys(release).length!==releaseKeys.length||releaseKeys.some(key=>!Object.hasOwn(release,key))||typeof release.release_id!=="string"||!HASH.test(release.release_id)||release.release_id!==release.manifest_digest||typeof release.manifest_digest!=="string"||!HASH.test(release.manifest_digest))fail("sidecar bootstrap release identity is invalid");
  for(const key of ["root","node_executable","python_executable","manifest_path"] as const)if(typeof release[key]!=="string"||!path.isAbsolute(release[key]))fail(`sidecar bootstrap release ${key} is not absolute`);
  if(typeof item.control_release!=="object"||!Array.isArray(item.owned_paths)||item.owned_paths.length<1||item.owned_paths.length>256)fail("sidecar bootstrap owned_paths is invalid");
  const owned=item.owned_paths;
  if(owned.some(file=>!safeOwnedPath(file))||new Set(owned.map(file=>String(file).toLowerCase())).size!==owned.length)fail("sidecar bootstrap owned_paths contains unsafe or duplicate entries");
  const normalized={protocol:"my-agent-evolution-sidecar/1.0" as const,control_release:structuredClone(release) as unknown as ControlRelease,pipe_name:item.pipe_name,host_instance_id:item.host_instance_id,host_token:item.host_token,scope_id:item.scope_id,owned_paths:owned.slice() as string[]};
  const bytes=Buffer.from(`${canonicalJson(normalized)}\n`,"utf8");
  if(bytes.length>MAX_BOOTSTRAP_BYTES)fail("sidecar bootstrap exceeds the private frame limit");
  return bytes;
}


export class SidecarBootstrapRegistry {
  private readonly frames=new Map<string,Buffer>();
  private key(root:string):string{
    if(typeof root!=="string"||!path.isAbsolute(root))fail("trusted sidecar root must be absolute");
    const resolved=path.resolve(root);
    return process.platform==="win32"?resolved.toLowerCase():resolved;
  }
  register(root:string,bootstrap:unknown):void{
    this.frames.set(this.key(root),encodeEvolutionSidecarBootstrap(bootstrap));
  }
  forRoot(root:string):Buffer|null{
    const frame=this.frames.get(this.key(root));
    return frame?Buffer.from(frame):null;
  }
  clear(root:string):void{
    this.frames.delete(this.key(root));
  }
  clearAll():void{
    this.frames.clear();
  }
}


export function sidecarBootstrapLaunch(frame:Buffer|null):{args:string[];stdin:"pipe"|"ignore";privateInput:Buffer|null}{
  if(frame===null)return {args:[],stdin:"ignore",privateInput:null};
  if(!Buffer.isBuffer(frame)||frame.length===0||frame.length>MAX_BOOTSTRAP_BYTES||frame[frame.length-1]!==0x0a||frame.subarray(0,-1).includes(0x0a)||frame.includes(0x0d))fail("sidecar private frame is invalid");
  return {args:["--evolution-bootstrap-stdin"],stdin:"pipe",privateInput:Buffer.from(frame)};
}
