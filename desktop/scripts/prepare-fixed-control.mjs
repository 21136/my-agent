import {randomUUID} from 'node:crypto';
import {constants,copyFileSync,existsSync,lstatSync,mkdirSync,readFileSync,statfsSync,writeFileSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {isAbsolute} from 'node:path';
import {execFileSync} from 'node:child_process';
import {assertFixtureRun,canonical,fileEntry,realDirectory,regularFile,safeRelative,samePath,sha,walk} from '../launcher/paths.mjs';
import {freezeHostRelease} from '../launcher/release.mjs';
import {cleanHostEnvironment,validateFixtureBinding} from '../launcher/binding.mjs';
import {assertSandboxRoot,requireCurrentBuildReceipt} from './fixed-control-build.mjs';

const HERE=path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_SANDBOX=path.resolve(HERE,'../..');
const EVOLVE_INDEX='# E0 fixture index. No evolving tools or personal topics are admitted.\n';
const BLOCKED_PARTS=new Set(['.git','.venv','venv','__pycache__','tests','test','data','workspace','profile','profiles','cache','caches','logs','config','configs','secrets','credentials']);
const BLOCKED_PY_PARTS=new Set([...BLOCKED_PARTS,'scripts']);
const BLOCKED_FILE=/^(?:\.env(?:\.|$)|.*\.(?:pem|key|p12|pfx|db|sqlite3?|jsonl|log|pyc|bak|backup|dump))$/i;
const BUILD_EXT=/\.(?:js|cjs|mjs|json|html|css|svg|png|jpg|jpeg|webp|ico|woff2?|ttf|otf|wasm|node|dll|dat|bin)$/i;
function allowedBuildPath(rel){return safeRelative(rel)&&!rel.split('/').some(p=>BLOCKED_PARTS.has(p.toLowerCase()))&&!rel.split('/').some(p=>BLOCKED_FILE.test(p))&&BUILD_EXT.test(rel);}
function sourceInventories(sandboxRoot){
 const desktop=path.join(sandboxRoot,'desktop'),core=path.join(sandboxRoot,'agent-core');
 const pkg=JSON.parse(readFileSync(regularFile(path.join(desktop,'package.json'),'desktop package metadata'),'utf8'));
 if(typeof pkg.name!=='string'||!pkg.name||pkg.main!=='dist-electron/main.js')throw Error('desktop package metadata must identify dist-electron/main.js');
 const releaseFiles=new Map();
 const addTree=(root,prefix,filter)=>{root=realDirectory(root,prefix+' build root');for(const rel of walk(root).files){if(!filter(rel))continue;const source=path.join(root,...rel.split('/'));releaseFiles.set(prefix+'/'+rel,source);}};
 addTree(path.join(desktop,'dist-electron'),'desktop/dist-electron',allowedBuildPath);
 addTree(path.join(desktop,'dist'),'desktop/dist',allowedBuildPath);
 if(!releaseFiles.has('desktop/dist-electron/main.js')||!releaseFiles.has('desktop/dist-electron/preload.js')||!releaseFiles.has('desktop/dist/index.html'))throw Error('built Electron main/preload and renderer index are required');
 const builtMain=readFileSync(releaseFiles.get('desktop/dist-electron/main.js'),'utf8');
 for(const marker of ['--fixed-host-bootstrap-stdin','my-agent-fixed-host-v1','fixed-host.ready','-S','fixed_host_entry.py','python_base_root'])if(!builtMain.includes(marker))throw Error('compiled Electron main is stale or lacks fixed-Host bootstrap marker: '+marker);
 for(const rel of ['electron/main.ts','electron/preload.ts','electron/fixed-host-context.ts'])regularFile(path.join(desktop,...rel.split('/')),'fixed Host build input');
 const pyRoot=realDirectory(core,'agent-core source');
 const py=walk(pyRoot).files.filter(rel=>rel.endsWith('.py')&&!rel.split('/').some(p=>BLOCKED_PY_PARTS.has(p.toLowerCase()))&&!BLOCKED_FILE.test(rel));
 if(!py.includes('server.py')||!py.includes('fixed_host_entry.py'))throw Error('agent-core/server.py and fixed_host_entry.py are required');
 for(const rel of py)releaseFiles.set('agent-core/'+rel,path.join(pyRoot,...rel.split('/')));
 const promptRoot=path.join(core,'prompts');
 if(existsSync(promptRoot))for(const rel of walk(promptRoot).files.filter(rel=>/\.txt$/i.test(rel)))releaseFiles.set('agent-core/prompts/'+rel,path.join(promptRoot,...rel.split('/')));
 for(const rel of walk(pyRoot).files.filter(rel=>rel.startsWith('templates/')&&/\.(?:toml|txt|j2|jinja|tmpl)$/i.test(rel)))releaseFiles.set('agent-core/'+rel,path.join(pyRoot,...rel.split('/')));
 return {desktop,core,pkg,releaseFiles};
}
function launcherSources(){
 const source=path.resolve(HERE,'../launcher');
 const files=walk(source).files.filter(rel=>rel.endsWith('.mjs')||rel.endsWith('.d.mts'));
 files.push('cli.mjs');
 const unique=[...new Set(files)].sort();
 for(const rel of unique){if(!safeRelative(rel))throw Error('unsafe launcher source path');regularFile(path.join(source,...rel.split('/')),'launcher module');}
 return unique.map(rel=>({path:rel,source:path.join(source,...rel.split('/'))}));
}
function mkdirOwned(dir){mkdirSync(dir,{recursive:true});return realDirectory(dir,'new fixture directory');}
function copyVerified(source,destination,expected){mkdirSync(path.dirname(destination),{recursive:true});copyFileSync(regularFile(source,'copy source'),destination,constants.COPYFILE_EXCL);const copied=readFileSync(destination),expectedHash=typeof expected==='string'?expected:expected.sha256;if(sha(copied)!==expectedHash||(typeof expected!=='string'&&copied.length!==expected.byte_length))throw Error('copied bytes differ from pinned fixture preparation input');}
function git(cwd,...args){const env={...cleanHostEnvironment(),GIT_TERMINAL_PROMPT:'0',GIT_CONFIG_NOSYSTEM:'1'};return execFileSync('git',['--no-optional-locks',...args],{cwd,encoding:'utf8',windowsHide:true,timeout:15000,maxBuffer:1024*1024,stdio:['ignore','pipe','pipe'],env}).trim();}
function makeFixtureRepositories(run,stableBranch,sandboxBranch){
 const stable=path.join(run,'source-stable'),sandbox=path.join(run,'source-sandbox');mkdirSync(stable);git(stable,'init','-b',stableBranch);git(stable,'config','user.name','Fixed Control Fixture');git(stable,'config','user.email','fixture@example.invalid');git(stable,'config','commit.gpgsign','false');git(stable,'config','core.autocrlf','false');git(stable,'config','core.hooksPath',path.join(run,'no-hooks'));
 mkdirSync(path.join(stable,'desktop'),{recursive:true});mkdirSync(path.join(stable,'agent-core'),{recursive:true});
 writeFileSync(path.join(stable,'desktop','package.json'),'{\"name\":\"fixed-control-fixture\",\"private\":true}\n',{flag:'wx'});writeFileSync(path.join(stable,'agent-core','fixture.py'),'# Synthetic fixed-control fixture only.\n',{flag:'wx'});
 writeFileSync(path.join(stable,'.fixed-source-fixture'),'fixture only\n',{flag:'wx'});git(stable,'add','.fixed-source-fixture','desktop/package.json','agent-core/fixture.py');git(stable,'commit','-m','initialize disposable fixed-control fixture');git(stable,'worktree','add','-b',sandboxBranch,sandbox);
 return {stable,sandbox};
}
function estimateBytes(releaseFiles,launcher){let n=0;for(const p of releaseFiles.values())n+=2*lstatSync(p).size;for(const x of launcher)n+=lstatSync(x.source).size;return n;}
function availableBytes(root){const s=statfsSync(root);return Number(s.bavail)*Number(s.bsize);}
function writeKitManifest(root,records){const manifest={format:1,owner:'my-agent-fixed-control-e0-launcher',files:records.sort((a,b)=>a.path.localeCompare(b.path))};writeFileSync(path.join(root,'manifest.json'),canonical(manifest)+'\n',{flag:'wx'});}

const runtimeDigest=root=>walk(root).files.map(rel=>fileEntry(root,rel));
export async function prepareFixedControl({sandboxRoot=DEFAULT_SANDBOX,nodeExecutable=process.execPath,electronRoot=path.join(DEFAULT_SANDBOX,'desktop','node_modules','electron','dist'),pythonRoot=path.resolve(DEFAULT_SANDBOX,'..','..','.venv'),pythonBaseRoot,runtimeProbe=probePythonRuntime,availableBytes:availableOverride}={}){
 sandboxRoot=assertSandboxRoot(sandboxRoot);const buildReceipt=requireCurrentBuildReceipt(sandboxRoot),receiptOutputs=new Map(buildReceipt.outputs.map(item=>[item.path,item]));const artifacts=path.join(sandboxRoot,'artifacts','fixed-control-e0');
 const payload=sourceInventories(sandboxRoot),nodeFile=regularFile(nodeExecutable,'Node runtime executable'),electron=realDirectory(electronRoot,'Electron runtime'),python=realDirectory(pythonRoot,'Python runtime');
 const pythonEntry=pythonRuntimeEntry(python),pythonExecutable=regularFile(path.join(python,...pythonEntry.split('/')),'approved Python interpreter');
 const probed=runtimeProbe(pythonExecutable);if(!probed||typeof probed!=='object'||!isAbsolute(probed.executable)||!isAbsolute(probed.prefix)||!isAbsolute(probed.base_prefix))throw Error('Python runtime probe returned an invalid identity');
 if(!samePath(regularFile(probed.executable,'probed Python executable'),pythonExecutable)||!samePath(realDirectory(probed.prefix,'Python venv prefix'),python)||samePath(realDirectory(probed.prefix,'Python venv prefix'),realDirectory(probed.base_prefix,'Python base prefix')))throw Error('approved Python interpreter does not match the selected venv root/executable');
 const baseRoot=realDirectory(pythonBaseRoot??probed.base_prefix,'Python base runtime');if(!samePath(baseRoot,realDirectory(probed.base_prefix,'probed Python base prefix')))throw Error('Python base runtime does not match sys.base_prefix');const baseEntry=pythonBaseRuntimeEntry(baseRoot);
 const kitSources=launcherSources();const required=['binding.mjs','paths.mjs','release.mjs','journal.mjs','lifecycle.mjs','index.mjs','cli.mjs'];for(const rel of required)if(!kitSources.some(x=>x.path===rel))throw Error('independent launcher module missing: '+rel);
 // Inventory every runtime tree before creating a run or copying any payload; reuse these exact records below.
 const runtimeInputs={node:runtimeDigest(path.dirname(nodeFile)),electron:runtimeDigest(electron),python:runtimeDigest(python),python_base:runtimeDigest(baseRoot)};
 if(existsSync(artifacts))realDirectory(artifacts,'fixture artifacts root');else mkdirOwned(artifacts);
 const estimate=estimateBytes(payload.releaseFiles,kitSources)+64*1024**2;
 const free=availableOverride?availableOverride(artifacts):availableBytes(artifacts);if(!Number.isFinite(free)||free<estimate)throw Error(`insufficient fixture capacity: need ${estimate} bytes, have ${free}`);
 const run=path.join(artifacts,'run-'+randomUUID());mkdirSync(run);realDirectory(run,'new fixture run root');const run_id=randomUUID();writeFileSync(path.join(run,'.fixed-control-fixture.json'),canonical({format:1,owner:'my-agent-fixed-control-e0',run_id})+'\n',{flag:'wx'});assertFixtureRun(run);
 for(const dir of ['releases','launcher','control-state','host/data','host/workspace','host/profile'])mkdirOwned(path.join(run,dir));
 const payloadRoot=mkdirOwned(path.join(run,'payload'));
 const packageRecord={name:payload.pkg.name,...(typeof payload.pkg.version==='string'?{version:payload.pkg.version}:{}),main:'dist-electron/main.js',...(typeof payload.pkg.type==='string'?{type:payload.pkg.type}:{})};
 writeFileSync(path.join(payloadRoot,'desktop-package.json'),canonical(packageRecord)+'\n',{flag:'wx'});
 const files=['desktop/package.json'];
 for(const [rel,source] of [...payload.releaseFiles].sort(([a],[b])=>a.localeCompare(b))){const targetRel=rel==='desktop/package.json'?'desktop/package.json':rel;let expected;if(rel.startsWith('desktop/dist-electron/')||rel.startsWith('desktop/dist/')){const buildPath=rel.slice('desktop/'.length);expected=receiptOutputs.get(buildPath);if(!expected)throw Error('fixed-control build receipt has no output entry for '+buildPath);}else expected=sha(readFileSync(source));copyVerified(source,path.join(payloadRoot,...targetRel.split('/')),expected);files.push(targetRel);}
 // Package metadata is newly constructed; never copy arbitrary package.json fields.
 mkdirSync(path.join(payloadRoot,'desktop'),{recursive:true});copyFileSync(path.join(payloadRoot,'desktop-package.json'),path.join(payloadRoot,'desktop','package.json'),constants.COPYFILE_EXCL);files.push('desktop/package.json');
 mkdirSync(path.join(payloadRoot,'evolve'),{recursive:true});writeFileSync(path.join(payloadRoot,'evolve','_index.core.toml'),EVOLVE_INDEX,{flag:'wx'});files.push('evolve/_index.core.toml');
 const uniqueFiles=[...new Set(files)].sort();
 const launcherRecords=[];for(const item of kitSources){const dest=path.join(run,'launcher',...item.path.split('/'));const digest=sha(readFileSync(item.source));copyVerified(item.source,dest,digest);launcherRecords.push({path:item.path,sha256:digest,byte_length:lstatSync(dest).size});}
 writeKitManifest(path.join(run,'launcher'),launcherRecords);
 const sourceInputs=buildSourceInputs(sandboxRoot,kitSources);
 const buildInputs={format:1,owner:'my-agent-fixed-control-e0-build-input-inventory',attestation:false,build_receipt:{sha256:buildReceipt.sha256,input_sha256:buildReceipt.receipt.input_sha256,output_sha256:buildReceipt.receipt.output_sha256},source_inputs:sourceInputs,build_outputs:buildReceipt.outputs,release_payload:uniqueFiles.map(rel=>fileEntry(payloadRoot,rel)),launcher_inputs:launcherRecords,runtime_inputs:runtimeInputs};
 const buildInputsBytes=Buffer.from(canonical(buildInputs)+'\n');writeFileSync(path.join(run,'build-inputs.json'),buildInputsBytes,{flag:'wx'});
 const source_snapshot='fixed-control-e0:'+sha(buildInputsBytes);
 const finalBuildReceipt=requireCurrentBuildReceipt(sandboxRoot);if(finalBuildReceipt.sha256!==buildReceipt.sha256)throw Error('fixed-control build receipt changed during preparation');
 const release=freezeHostRelease({runRoot:run,sourceRoot:payloadRoot,files:uniqueFiles,runtimeInputs:{node:{root:path.dirname(nodeFile),entry:path.basename(nodeFile),scope:'tree'},electron:{root:electron,entry:electronRuntimeEntry(electron),scope:'tree'},python:{root:python,entry:pythonEntry,scope:'tree'},python_base:{root:baseRoot,entry:baseEntry,scope:'tree'}},provenance:{kind:'sealed-development-build',source_snapshot}});
 const instanceMarker={format:1,mode:'launcher_fixture_v1',code_root:path.join(release.root,'app')};writeFileSync(path.join(run,'host','.fixed-host-instance.json'),canonical(instanceMarker)+'\n',{flag:'wx'});
 const repos=makeFixtureRepositories(run,'stable','experiment/sandbox');
 const binding={format:1,mode:'launcher_fixture_v1',installation_id:run_id,binding_revision:1,run_root:run,launcher_root:path.join(run,'launcher'),state_root:path.join(run,'control-state'),instance_root:path.join(run,'host'),stable_source_root:repos.stable,sandbox_source_root:repos.sandbox,git_common_dir:path.join(repos.stable,'.git'),stable_branch:'stable',sandbox_branch:'experiment/sandbox',release};
 validateFixtureBinding(binding);const bindingPath=path.join(binding.state_root,'binding.json');writeFileSync(bindingPath,canonical(binding)+'\n',{flag:'wx'});
 return {runRoot:run,binding,bindingPath,release,buildInputsManifest:path.join(run,'build-inputs.json'),launcherManifest:path.join(binding.launcher_root,'manifest.json'),payloadFileCount:uniqueFiles.length,launcherFileCount:launcherRecords.length,estimatedBytes:estimate};
}
function buildSourceInputs(sandboxRoot,launcherSources){
 const roots=[['desktop/electron',path.join(sandboxRoot,'desktop','electron')],['desktop/src',path.join(sandboxRoot,'desktop','src')],['agent-core',path.join(sandboxRoot,'agent-core')]];const records=[];
 for(const [prefix,root] of roots){for(const rel of walk(root).files){
  if(BLOCKED_FILE.test(rel)||rel.split('/').some(p=>(prefix==='agent-core'?BLOCKED_PY_PARTS:BLOCKED_PARTS).has(p.toLowerCase())))continue;
  if(prefix==='desktop/electron'&&!/\.(?:ts|tsx|js|mjs|css)$/i.test(rel))continue;
  if(prefix==='desktop/src'&&!/\.(?:ts|tsx|js|jsx|mjs|css|html|svg|png|webp|json)$/i.test(rel))continue;
  if(prefix==='agent-core'&&!(/\.py$/i.test(rel)||rel.startsWith('prompts/')&&/\.txt$/i.test(rel)))continue;
  const entry=fileEntry(root,rel);entry.path=prefix+'/'+rel;records.push(entry);
 }}
 const packageEntry=fileEntry(path.join(sandboxRoot,'desktop'),'package.json');packageEntry.path='desktop/package.json';records.push(packageEntry);
 for(const item of launcherSources){const entry=fileEntry(path.dirname(item.source),path.basename(item.source));entry.path='launcher-source/'+item.path;records.push(entry);}
 return records.sort((a,b)=>a.path.localeCompare(b.path));
}
function electronRuntimeEntry(root){const names=walk(root).files;if(names.includes('electron.exe'))return 'electron.exe';if(names.includes('Electron.app/Contents/MacOS/Electron'))return 'Electron.app/Contents/MacOS/Electron';if(names.includes('electron'))return 'electron';throw Error('Electron runtime entry not found');}
function pythonRuntimeEntry(root){const names=walk(root).files;const found=names.find(rel=>/^(?:python(?:\.exe)?|Scripts\/python\.exe|bin\/python3?)$/i.test(rel));if(!found)throw Error('Python venv interpreter not found');return found;}
function pythonBaseRuntimeEntry(root){const names=walk(root).files;const expected=process.platform==='win32'?['python.exe']:['bin/python3','bin/python'];const found=expected.find(rel=>names.includes(rel));if(!found)throw Error('Python base runtime interpreter entry not found');return found;}
function probePythonRuntime(executable){const code='import json,sys; print(json.dumps({"executable":sys.executable,"prefix":sys.prefix,"base_prefix":sys.base_prefix},separators=(",",":")))';const raw=execFileSync(executable,['-I','-B','-c',code],{encoding:'utf8',windowsHide:true,timeout:15000,maxBuffer:65536,stdio:['ignore','pipe','pipe'],env:cleanHostEnvironment()}).trim();const value=JSON.parse(raw);if(!value||Object.keys(value).sort().join(',')!=='base_prefix,executable,prefix')throw Error('Python runtime probe response schema invalid');return value;}

const isMain=process.argv[1]&&pathToFileURL(path.resolve(process.argv[1])).href===import.meta.url;
if(isMain){const args=process.argv.slice(2);if(args.includes('--help')||args.includes('-h')){console.log('Usage: node desktop/scripts/prepare-fixed-control.mjs --prepare\nCreates a marked disposable fixture under sandbox/artifacts/fixed-control-e0. Does not launch it.');process.exitCode=0;}else if(args.length!==1||args[0]!=='--prepare'){console.error('Use --prepare or --help. Preparation never launches the application.');process.exitCode=2;}else try{const result=await prepareFixedControl();console.log(JSON.stringify({runRoot:result.runRoot,bindingPath:result.bindingPath,releaseId:result.release.release_id,payloadFileCount:result.payloadFileCount,launcherFileCount:result.launcherFileCount,estimatedBytes:result.estimatedBytes},null,2));}catch(error){console.error(`Preparation failed: ${error.message}`);process.exitCode=1;}}





