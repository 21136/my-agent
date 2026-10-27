import { existsSync, lstatSync, readFileSync, realpathSync } from "node:fs";
import * as path from "node:path";
import { ContractError } from "./contracts";

export type EvolutionTestProfile={profile_root:string;control_release_root:string;trusted_python_executable:string;ws_port:0;renderer_port:0;fixture_scope_id:string;candidate_fixture_profile?:"t4-recovery-node-v1"};
export const T4_RECOVERY_PROFILE="t4-recovery-node-v1" as const;
export const T4_RECOVERY_OWNED_PATHS=["evolve/tool-catalog/recovery/main.cjs","evolve/tool-catalog/recovery/service.cjs","evolve/tool-catalog/recovery/manifest.json"] as const;
export const T4_RECOVERY_RUNTIME_ID="77777777-7777-4777-8777-777777777777";
export const T4_RECOVERY_PROFILE_FIXTURE_ID="88888888-8888-4888-8888-888888888888";
export const T4_RECOVERY_DESCRIPTOR_ARTIFACT_ID="99999999-9999-4999-8999-999999999999";
export const T4_RECOVERY_MAIN_COMPONENT_ID="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
export const T4_RECOVERY_SERVICE_COMPONENT_ID="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
export const T4_RECOVERY_ENVIRONMENT_PROFILE_ID="cccccccc-cccc-4ccc-8ccc-cccccccccccc";
export const T4_RECOVERY_BUILD_RECIPE_ARTIFACT_ID="dddddddd-dddd-4ddd-8ddd-dddddddddddd";
export const T4_RECOVERY_ADAPTER_ARTIFACT_ID="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
function fail(message:string):never{throw new ContractError("permission_denied",message);}
function realDirectory(value:string,label:string):string{const info=lstatSync(value);if(!info.isDirectory()||info.isSymbolicLink())fail(`${label} must be a real directory`);return realpathSync.native(value);}
function inside(parent:string,child:string):boolean{const rel=path.relative(parent,child);return rel!==""&&rel!==".."&&!rel.startsWith(`..${path.sep}`)&&!path.isAbsolute(rel);}
function markedRunRoot(target:string,artifacts:string):string{let current=target;while(inside(artifacts,current)){const marker=path.join(current,".evolution-test-run");try{if(readFileSync(marker,"utf8").replace(/^\uFEFF/,"").trim()==="evolution-a")return current;}catch{/* continue to the nearest ancestor */}current=path.dirname(current);}fail("test profile must be inside a marked evolution run root");}
export function validateEvolutionTestProfile(value:unknown,sandboxRoot:string):EvolutionTestProfile{
  if(value===null||typeof value!=="object"||Array.isArray(value))fail("test profile must be an object");const profile=value as Record<string,unknown>;const keys=["profile_root","control_release_root","trusted_python_executable","ws_port","renderer_port","fixture_scope_id"];const recovery=Object.hasOwn(profile,"candidate_fixture_profile");const acceptedKeys=recovery?[...keys,"candidate_fixture_profile"]:keys;if(Object.keys(profile).length!==acceptedKeys.length||acceptedKeys.some(key=>!Object.hasOwn(profile,key)))fail("test profile fields are invalid");if(recovery&&profile.candidate_fixture_profile!==T4_RECOVERY_PROFILE)fail("candidate fixture profile is unsupported");
  const sandbox=realDirectory(path.resolve(sandboxRoot),"sandbox root");const artifacts=realDirectory(path.join(sandbox,"artifacts"),"sandbox artifacts root");for(const key of ["profile_root","control_release_root","trusted_python_executable"]){if(typeof profile[key]!=="string"||!path.isAbsolute(profile[key] as string))fail(`${key} must be an absolute path`);}
  const profileRoot=realDirectory(path.resolve(profile.profile_root as string),"test profile root");const releaseRoot=realDirectory(path.resolve(profile.control_release_root as string),"control release root");if(!inside(artifacts,profileRoot)||!inside(artifacts,releaseRoot))fail("test profile and control release must remain under sandbox/artifacts");if(markedRunRoot(profileRoot,artifacts)!==markedRunRoot(releaseRoot,artifacts))fail("test profile and release must share one marked run root");if(inside(profileRoot,releaseRoot)||inside(releaseRoot,profileRoot)||profileRoot===releaseRoot)fail("test profile and control release must be disjoint");
  if(profile.ws_port!==0||profile.renderer_port!==0)fail("test host must use dynamic port zero");if(typeof profile.fixture_scope_id!=="string"||!UUID.test(profile.fixture_scope_id))fail("fixture_scope_id must be a canonical UUID");
  const python=path.resolve(profile.trusted_python_executable as string);const pythonInfo=lstatSync(python);if(!pythonInfo.isFile()||pythonInfo.isSymbolicLink())fail("trusted Python executable must be a regular file");
  if(!existsSync(path.join(releaseRoot,"manifest.json")))fail("control release manifest is missing");
  return{profile_root:profileRoot,control_release_root:releaseRoot,trusted_python_executable:realpathSync.native(python),ws_port:0,renderer_port:0,fixture_scope_id:profile.fixture_scope_id,...(recovery?{candidate_fixture_profile:T4_RECOVERY_PROFILE}:{})};
}
