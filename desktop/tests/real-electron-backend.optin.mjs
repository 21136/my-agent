import assert from 'node:assert/strict';
import {existsSync,lstatSync,mkdirSync,readFileSync,readdirSync,realpathSync,rmSync} from 'node:fs';
import {spawn,spawnSync} from 'node:child_process';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {cleanHostEnvironment,readFixtureBinding} from '../launcher/binding.mjs';
import {assertFixtureRun,canonical,inside,readJson,realDirectory,regularFile,samePath,walk} from '../launcher/paths.mjs';

const HERE=path.dirname(fileURLToPath(import.meta.url));
const SANDBOX=path.resolve(HERE,'../..');
const FIXTURE_ARTIFACTS=path.join(SANDBOX,'artifacts','fixed-control-e0');

export function assertOptIn(env=process.env){
 if(env.REAL_ELECTRON_BACKEND_OPT_IN!=='1')throw Error('real Electron fixture is opt-in; set REAL_ELECTRON_BACKEND_OPT_IN=1 explicitly');
}

export function assertFixedHostPortZero(source){
 if(typeof source!=='string'||!source.includes('fixedHostContext || evolutionTestHostContext ? 0')||!source.includes('fixedHostContext ? ["-I","-B","-S",script,"--port","0"]'))
  throw Error('fixed Host backend must use its real --port 0 startup path; refusing a fixed-port or default-lane launch');
}

export function assertFixtureInstanceLayout(binding){
 const root=realDirectory(binding.instance_root,'isolated fixture Host root');
 const expected=['.fixed-host-instance.json','data','profile','workspace'];
 const actual=readdirSync(root).sort();
 if(canonical(actual)!==canonical(expected))throw Error('isolated fixture Host root has unexpected entries');
 for(const name of ['data','profile','workspace'])realDirectory(path.join(root,name),'isolated fixture '+name);
 const marker=readJson(path.join(root,'.fixed-host-instance.json'));
 const expectedMarker={format:1,mode:'launcher_fixture_v1',code_root:path.join(binding.release.root,'app')};
 if(canonical(marker)!==canonical(expectedMarker))throw Error('isolated fixture Host instance marker identity mismatch');
 return root;
}
export function assertCleanFixtureRoots(instanceRoot){
 const root=realDirectory(instanceRoot,'isolated fixture Host root');
 const roots=['data','workspace','profile'].map(name=>path.join(root,name));
 for(const dir of roots){
  realDirectory(dir,'isolated fixture '+path.basename(dir));
  const inventory=walk(dir);
  if(inventory.files.length||inventory.directories.length)throw Error('isolated fixture '+path.basename(dir)+' must be empty before launch');
 }
 return roots;
}

const E0_PROFILE_ENV_RELATIVE={
 USERPROFILE:'userprofile', APPDATA:'appdata/Roaming', LOCALAPPDATA:'appdata/Local',
 HOME:'home', XDG_CONFIG_HOME:'xdg-config', XDG_DATA_HOME:'xdg-data', XDG_CACHE_HOME:'xdg-cache',
 TEMP:'tmp', TMP:'tmp', TMPDIR:'tmp',
};
const E0_PROFILE_EXTRA_RELATIVE=[
 'appdata','userprofile/Desktop','userprofile/Downloads','electron-userData','electron-sessionData',
];
function directoryIdentity(profileRoot,relative){
 const target=path.resolve(profileRoot,...relative.split('/'));
 const info=lstatSync(target,{bigint:true});
 if(!info.isDirectory()||info.isSymbolicLink()||info.dev===0n||info.ino===0n||info.birthtimeNs===0n
   ||!samePath(realpathSync.native(target),target)||!inside(profileRoot,target))
  throw Error('E0 isolated profile directory is not a canonical owned directory');
 return {relative,path:target,dev:info.dev.toString(),ino:info.ino.toString(),birthtimeNs:info.birthtimeNs.toString()};
}
export function prepareIsolatedHostEnvironment(profileRoot,sourceEnvironment=process.env){
 const profile=realDirectory(path.resolve(profileRoot),'E0 fixture profile root');
 const relatives=[...new Set([...Object.values(E0_PROFILE_ENV_RELATIVE),...E0_PROFILE_EXTRA_RELATIVE])].sort();
 for(const relative of relatives)mkdirSync(path.join(profile,...relative.split('/')),{recursive:true});
 const directories=Object.fromEntries(Object.entries(E0_PROFILE_ENV_RELATIVE).map(([key,relative])=>[key,path.join(profile,...relative.split('/'))]));
 const identities=relatives.map(relative=>directoryIdentity(profile,relative));
 const profileStat=lstatSync(profile,{bigint:true});
 const profileIdentity={path:profile,dev:profileStat.dev.toString(),ino:profileStat.ino.toString(),birthtimeNs:profileStat.birthtimeNs.toString()};
 const env={...cleanHostEnvironment(sourceEnvironment),...directories,PYTHONDONTWRITEBYTECODE:'1',PYTHONNOUSERSITE:'1'};
 if(Object.keys(env).some(key=>/(?:API.?KEY|TOKEN|SECRET|CREDENTIAL)/i.test(key)))throw Error('E0 isolated environment contains a credential-like variable');
 return {profileRoot:profile,profileIdentity,directories,identities,env};
}
export function assertOwnedHostEnvironment(layout,{requireEmpty=false}={}){
 if(!layout||typeof layout!=='object'||typeof layout.profileRoot!=='string'||!Array.isArray(layout.identities)
   ||!layout.env||typeof layout.env!=='object'||!layout.directories||typeof layout.directories!=='object')throw Error('E0 isolated environment layout is invalid');
 const profile=realDirectory(layout.profileRoot,'E0 fixture profile root');
 const host=path.dirname(profile);
 if(!samePath(profile,path.join(host,'profile'))||!samePath(host,path.dirname(profile)))throw Error('E0 profile is not the bound Host profile root');
 const profileInfo=lstatSync(profile,{bigint:true});
 const expectedProfile=layout.profileIdentity;
 if(!expectedProfile||!profileInfo.isDirectory()||profileInfo.isSymbolicLink()
   ||profileInfo.dev.toString()!==expectedProfile.dev||profileInfo.ino.toString()!==expectedProfile.ino
   ||profileInfo.birthtimeNs.toString()!==expectedProfile.birthtimeNs)throw Error('E0 profile identity changed');
 for(const [key,relative] of Object.entries(E0_PROFILE_ENV_RELATIVE)){
  const expected=path.join(profile,...relative.split('/'));
  if(typeof layout.env[key]!=='string'||!samePath(layout.env[key],expected)
    ||!samePath(layout.directories[key],expected))throw Error('E0 user environment path escaped its isolated profile');
 }
 for(const identity of layout.identities){
  const current=directoryIdentity(profile,identity.relative);
  if(current.dev!==identity.dev||current.ino!==identity.ino||current.birthtimeNs!==identity.birthtimeNs)
   throw Error('E0 isolated profile child identity changed');
 }
 const inventory=walk(profile);
 if(requireEmpty){
  const expected=layout.identities.map(item=>item.relative).sort();
  if(inventory.files.length||canonical(inventory.directories)!==canonical(expected))throw Error('E0 isolated profile layout is not the exact empty fixture tree');
 }
 return true;
}
export function assertCleanFixtureDataWorkspace(instanceRoot){
 const root=realDirectory(instanceRoot,'isolated fixture Host root');
 for(const name of ['data','workspace']){
  const directory=realDirectory(path.join(root,name),'isolated fixture '+name);
  const inventory=walk(directory);
  if(inventory.files.length||inventory.directories.length)throw Error('isolated fixture '+name+' must remain empty before launch');
 }
 return true;
}
export function buildFixedControlLaunchSpec({nodeExecutable,launcherEntry,bindingPath,env}){
 for(const [value,label] of [[nodeExecutable,'Node executable'],[launcherEntry,'fixture launcher'],[bindingPath,'fixture binding']])
  if(typeof value!=='string'||!path.isAbsolute(value))throw Error('E0 '+label+' must be absolute');
 if(!env||typeof env!=='object'||Array.isArray(env))throw Error('E0 isolated environment is required');
 return {executable:nodeExecutable,args:[launcherEntry,'--launch',bindingPath],options:{cwd:path.dirname(launcherEntry),env,shell:false,windowsHide:true,stdio:['ignore','pipe','pipe']}};
}

const E0_PATH_PREFLIGHT=String.raw`
import json,os,pathlib,sys
if len(sys.argv)!=5: raise SystemExit(31)
code_root=pathlib.Path(sys.argv[1]).resolve(strict=True)
instance_root=pathlib.Path(sys.argv[2]).resolve(strict=True)
profile_root=pathlib.Path(sys.argv[3]).resolve(strict=True)
expected_python=pathlib.Path(sys.argv[4]).resolve(strict=True)
agent_core=code_root/'agent-core'
if not agent_core.is_dir() or agent_core.is_symlink(): raise SystemExit(32)
sys.path.insert(0,str(agent_core))
from paths import AgentPaths
paths=AgentPaths.from_fixed_layout(code_root,instance_root)
actual_python=pathlib.Path(sys.executable).resolve(strict=True)
if os.path.normcase(str(actual_python))!=os.path.normcase(str(expected_python)): raise SystemExit(33)
def canonical_env(key,relative):
 raw=os.environ.get(key)
 if not raw: raise SystemExit(34)
 target=pathlib.Path(raw)
 resolved=target.resolve(strict=True)
 expected=(profile_root/pathlib.PurePosixPath(relative)).resolve(strict=True)
 if os.path.normcase(str(resolved))!=os.path.normcase(str(expected)) or os.path.normcase(str(target.absolute()))!=os.path.normcase(str(resolved)): raise SystemExit(35)
 return str(resolved)
required={'USERPROFILE':'userprofile','APPDATA':'appdata/Roaming','LOCALAPPDATA':'appdata/Local','TEMP':'tmp','TMP':'tmp'}
user_roots={key:canonical_env(key,relative) for key,relative in required.items()}
optional={}
for key in ('HOME','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_CACHE_HOME','TMPDIR'):
 raw=os.environ.get(key)
 if raw is None: optional[key]='absent'
 else:
  target=pathlib.Path(raw); resolved=target.resolve(strict=True)
  if os.path.normcase(str(target.absolute()))!=os.path.normcase(str(resolved)):
   raise SystemExit(36)
  try: resolved.relative_to(profile_root)
  except ValueError: raise SystemExit(37)
  optional[key]=str(resolved)
if os.environ.get('MY_AGENT_ROOT') is not None: raise SystemExit(38)
expected={'agent_root':instance_root,'data':instance_root/'data','workspace':instance_root/'workspace','evolve':code_root/'evolve'}
actual={'agent_root':paths.agent_root,'data':paths.data,'workspace':paths.workspace,'evolve':paths.evolve}
if any(os.path.normcase(str(actual[key].resolve(strict=True)))!=os.path.normcase(str(value.resolve(strict=True))) for key,value in expected.items()):
 raise SystemExit(39)
profile_stat=profile_root.stat()
print(json.dumps({'status':'verified','host_started':False,'python_executable':str(actual_python),'code_root':str(code_root),'profile_root':str(profile_root),'profile_identity':{'device':str(profile_stat.st_dev),'inode':str(profile_stat.st_ino)},'agent_root':str(paths.agent_root),'data':str(paths.data),'workspace':str(paths.workspace),'evolve':str(paths.evolve),'my_agent_root_env':'absent_fixed_layout_is_authoritative','user_roots':user_roots,'optional_roots':optional},separators=(',',':')))
`;
export function runE0IsolationPreflight({pythonExecutable,codeRoot,instanceRoot,layout,spawnSyncImpl=spawnSync}){
 if(typeof spawnSyncImpl!=='function')throw Error('E0 preflight process runner is invalid');
 if(typeof pythonExecutable!=='string'||!path.isAbsolute(pythonExecutable))throw Error('E0 preflight Python executable must be absolute');
 const python=regularFile(pythonExecutable,'E0 preflight Python executable');
 const code=realDirectory(codeRoot,'E0 preflight code root');
 const instance=realDirectory(instanceRoot,'E0 preflight Host instance');
 if(canonical(readdirSync(instance).sort())!==canonical(['.fixed-host-instance.json','data','profile','workspace']))throw Error('E0 preflight Host instance layout mismatch');
 const marker=readJson(path.join(instance,'.fixed-host-instance.json'));
 if(canonical(marker)!==canonical({format:1,mode:'launcher_fixture_v1',code_root:code}))throw Error('E0 preflight fixed-layout marker mismatch');
 if(!samePath(path.join(instance,'profile'),layout?.profileRoot))throw Error('E0 preflight profile does not match the fixed Host instance');
 assertCleanFixtureDataWorkspace(instance);
 assertOwnedHostEnvironment(layout,{requireEmpty:true});
 const env=cleanHostEnvironment(layout.env);
 if(Object.keys(env).some(key=>/(?:API.?KEY|TOKEN|SECRET|CREDENTIAL)/i.test(key)))throw Error('E0 preflight environment contains a credential-like variable');
 if(Object.hasOwn(env,'MY_AGENT_ROOT'))throw Error('E0 preflight must use the fixed-layout path resolver, not an ambient root');
 const expected={USERPROFILE:layout.directories.USERPROFILE,APPDATA:layout.directories.APPDATA,
  LOCALAPPDATA:layout.directories.LOCALAPPDATA,TEMP:layout.directories.TEMP,TMP:layout.directories.TMP};
 for(const [key,value] of Object.entries(expected))if(typeof env[key]!=='string'||!samePath(env[key],value))throw Error('E0 preflight environment is not bound to its owned profile');
 const result=spawnSyncImpl(python,['-I','-B','-S','-c',E0_PATH_PREFLIGHT,code,instance,layout.profileRoot,python],{
  cwd:instance,env,encoding:'utf8',windowsHide:true,shell:false,timeout:20000,maxBuffer:64*1024,
 });
 if(result?.error||result?.status!==0||typeof result.stdout!=='string')throw Error('E0 isolated Python path preflight failed; Electron launch is forbidden');
 const lines=result.stdout.trim().split(/\r?\n/);
 if(lines.length!==1)throw Error('E0 isolated Python path preflight returned unexpected output; Electron launch is forbidden');
 let proof;
 try{proof=JSON.parse(lines[0]);}catch{throw Error('E0 isolated Python path preflight returned invalid identity JSON; Electron launch is forbidden');}
 const exactKeys=['agent_root','code_root','data','evolve','host_started','my_agent_root_env','optional_roots','profile_identity','profile_root','python_executable','status','user_roots','workspace'];
 if(typeOfRecord(proof)!=='object'||canonical(Object.keys(proof).sort())!==canonical(exactKeys.sort())
  ||proof.status!=='verified'||proof.host_started!==false||proof.my_agent_root_env!=='absent_fixed_layout_is_authoritative'
  ||!samePath(proof.python_executable,python)||!samePath(proof.code_root,code)
  ||!samePath(proof.profile_root,layout.profileRoot)||!samePath(proof.agent_root,instance)
  ||!samePath(proof.data,path.join(instance,'data'))||!samePath(proof.workspace,path.join(instance,'workspace'))
  ||!samePath(proof.evolve,path.join(code,'evolve')))
  throw Error('E0 isolated Python path preflight identity mismatch; Electron launch is forbidden');
 for(const [key,value] of Object.entries(expected))if(!proof.user_roots||!samePath(proof.user_roots[key],value))throw Error('E0 isolated Python path preflight user-root mismatch; Electron launch is forbidden');
 const expectedOptional=['HOME','TMPDIR','XDG_CACHE_HOME','XDG_CONFIG_HOME','XDG_DATA_HOME'].sort();
 if(typeOfRecord(proof.optional_roots)!=='object'||canonical(Object.keys(proof.optional_roots).sort())!==canonical(expectedOptional))throw Error('E0 isolated Python path preflight optional-root shape mismatch');
 for(const value of Object.values(proof.optional_roots))if(value!=='absent'&&(!inside(layout.profileRoot,value)||!samePath(path.resolve(value),value)))throw Error('E0 isolated Python path preflight optional root escaped profile');
 return proof;
}
function typeOfRecord(value){return value!==null&&typeof value==='object'&&!Array.isArray(value)?'object':typeof value;}
async function launchFixedControl(spec,write){
 return await new Promise((resolve,reject)=>{
  let stdout='',stdoutBytes=0,overflow=false,settled=false;
  let child;
  try{child=spawn(spec.executable,spec.args,spec.options);}catch{reject(new Error('isolated E0 launcher spawn failed'));return;}
  child.stdout?.on('data',chunk=>{stdoutBytes+=Buffer.byteLength(chunk);if(stdoutBytes>64*1024){overflow=true;return;}stdout+=String(chunk);});
  child.stderr?.on('data',()=>{});
  child.once('error',()=>{if(!settled){settled=true;reject(new Error('isolated E0 launcher process failed'));}});
  child.once('close',(code,signal)=>{
   if(settled)return;settled=true;
   if(code!==0||signal!==null||overflow){reject(new Error('isolated E0 launcher did not complete normally'));return;}
   try{const result=JSON.parse(stdout.trim().split(/\r?\n/).at(-1));write(JSON.stringify(result));resolve({code,result});}
   catch{reject(new Error('isolated E0 launcher returned no valid completion receipt'));}
  });
 });
}

function snapshotRoots(roots){
 return roots.map(root=>({root,inventory:walk(root)}));
}
function assertCreatedRunIdentity(prepared,expected){
 const run=assertFixtureRun(prepared.runRoot),binding=expected.binding;
 if(!samePath(path.dirname(run),FIXTURE_ARTIFACTS)||!samePath(run,binding.run_root))throw Error('prepared fixture is not this invocation\'s new sandbox run');
 const marker=readJson(path.join(run,'.fixed-control-fixture.json'));
 if(marker.run_id!==binding.installation_id)throw Error('new fixture run marker identity mismatch');
 const current=readFixtureBinding(prepared.bindingPath);
 if(current.digest!==prepared.bindingDigest||current.digest!==expected.digest)throw Error('new fixture binding identity changed');
 return current;
}

async function prepareNewFixture(env){
 const desktop=path.resolve(SANDBOX,'desktop');
 const {prepareFixedControl}=await import(pathToFileURL(path.join(desktop,'scripts','prepare-fixed-control.mjs')).href);
 const pythonRoot=env.REAL_ELECTRON_PYTHON_ROOT||path.resolve(SANDBOX,'..','..','.venv');
 const prepared=await prepareFixedControl({
  sandboxRoot:SANDBOX,
  nodeExecutable:env.REAL_ELECTRON_NODE||process.execPath,
  electronRoot:env.REAL_ELECTRON_ROOT||path.join(desktop,'node_modules','electron','dist'),
  pythonRoot,
  ...(env.REAL_ELECTRON_PYTHON_BASE_ROOT?{pythonBaseRoot:env.REAL_ELECTRON_PYTHON_BASE_ROOT}:{}),
 });
 const checked=readFixtureBinding(prepared.bindingPath);
 if(!samePath(prepared.runRoot,checked.binding.run_root))throw Error('preparation returned inconsistent run/binding roots');
 return {...prepared,bindingDigest:checked.digest,binding:checked.binding};
}

function cleanCreatedRun(prepared,expected){
 const current=assertCreatedRunIdentity(prepared,expected);
 const statePath=path.join(current.binding.state_root,'instance.json');
 const state=readJson(statePath);
 if(state.state!=='stopped'||state.instance_id!==expected.instance_id||existsSync(path.join(current.binding.state_root,'instance.lock')))
  throw Error('created fixture is not verified stopped; preserving its files');
 walk(prepared.runRoot); // Reject links, aliases, devices, and overlarge cleanup trees.
 const parent=realDirectory(path.dirname(prepared.runRoot),'fixture cleanup parent');
 if(!samePath(parent,FIXTURE_ARTIFACTS)||!inside(parent,prepared.runRoot))throw Error('created fixture cleanup escaped its artifact root');
 rmSync(prepared.runRoot,{recursive:true,force:false,maxRetries:3,retryDelay:50});
}
export async function runRealElectronBackendFixture({env=process.env,write=console.log}={}){
 assertOptIn(env);
 const mainSource=readFileSync(path.join(SANDBOX,'desktop','electron','main.ts'),'utf8');
 assertFixedHostPortZero(mainSource);
 const prepared=await prepareNewFixture(env);
 const binding=prepared.binding,bindingPath=prepared.bindingPath;
 assertCreatedRunIdentity(prepared,{binding,digest:prepared.bindingDigest});
 assertFixtureInstanceLayout(binding);
 const roots=assertCleanFixtureRoots(binding.instance_root);
 const isolated=prepareIsolatedHostEnvironment(path.join(binding.instance_root,'profile'),env);
 assertCleanFixtureDataWorkspace(binding.instance_root);
 assertOwnedHostEnvironment(isolated,{requireEmpty:true});
 const before=snapshotRoots(roots);
 const launcherEntry=path.join(binding.launcher_root,'cli.mjs');
 const {runCli}=await import(pathToFileURL(launcherEntry).href);
 const checked=readFixtureBinding(bindingPath);
 const pythonRuntime=checked.manifest.runtimes.python;
 const pythonExecutable=path.join(pythonRuntime.root,...pythonRuntime.entry.split('/'));
 const pathProof=runE0IsolationPreflight({pythonExecutable,codeRoot:path.join(binding.release.root,'app'),instanceRoot:binding.instance_root,layout:isolated});
 write(JSON.stringify(pathProof));
 const preflight=await runCli(['--check',bindingPath],{entryPath:launcherEntry,nodeExecutable:process.execPath,env:isolated.env,write:()=>{}});
 if(preflight.code!==0||!preflight.nodeRuntimeMatches)throw Error('verified fixture Node runtime does not match this process; preserving prepared run');
 const same=readFixtureBinding(bindingPath);
 if(same.digest!==prepared.bindingDigest)throw Error('new fixture binding changed before launch; preserving prepared run');
 assertFixtureInstanceLayout(same.binding);
 assertCleanFixtureDataWorkspace(same.binding.instance_root);
 assertOwnedHostEnvironment(isolated,{requireEmpty:true});
 write('Launching this invocation\'s new disposable Electron + isolated backend. The real backend uses OS-assigned port 0; close the Electron window normally to finish.');
 const spec=buildFixedControlLaunchSpec({nodeExecutable:process.execPath,launcherEntry,bindingPath,env:isolated.env});
 const launched=await launchFixedControl(spec,write);
 if(launched.result?.state!=='stopped'||!launched.result.instance_id)throw Error('fixed Host did not report a verified normal stop; preserving this run');
 assertOwnedHostEnvironment(isolated);
 const expected={binding:same.binding,digest:same.digest,instance_id:launched.result.instance_id};
 assertCreatedRunIdentity(prepared,expected);
 const changed=roots.map((root,index)=>({root,files_after:walk(root).files,directories_after:walk(root).directories,before_files:before[index].inventory.files}));
 cleanCreatedRun(prepared,expected);
 return {state:'stopped',installation_id:binding.installation_id,instance_id:launched.result.instance_id,backend_port_mode:'OS-assigned (--port 0)',isolated_roots:changed,cleaned_run:prepared.runRoot};
}

const isMain=process.argv[1]&&pathToFileURL(path.resolve(process.argv[1])).href===import.meta.url;
if(isMain){
 if(process.argv.slice(2).some(arg=>arg==='--help'||arg==='-h')){
  console.log('Usage: REAL_ELECTRON_BACKEND_OPT_IN=1 node desktop/tests/real-electron-backend.optin.mjs');
  console.log('Optional: REAL_ELECTRON_NODE, REAL_ELECTRON_ROOT, REAL_ELECTRON_PYTHON_ROOT, REAL_ELECTRON_PYTHON_BASE_ROOT. Creates a new fixed-control fixture; only a normally stopped, identity-matching run is removed.');
 }else try{const result=await runRealElectronBackendFixture();console.log(JSON.stringify(result,null,2));}
 catch(error){console.error(`Real Electron fixture refused/failed: ${error.message}`);process.exitCode=1;}
}



