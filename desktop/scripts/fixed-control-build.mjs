import {existsSync,mkdirSync,readFileSync,readdirSync,renameSync,unlinkSync,writeFileSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {canonical,fileEntry,realDirectory,regularFile,sha,walk} from '../launcher/paths.mjs';
import {execFileSync} from 'node:child_process';
import {cleanHostEnvironment} from '../launcher/binding.mjs';

const HERE=path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_SANDBOX=path.resolve(HERE,'../..');
const OWNER='my-agent-fixed-control-e0-build-receipt';
const RECEIPT_REL='artifacts/fixed-control-e0/build-receipt.json';
const REQUIRED_INPUT_TREES=['electron','src','launcher','scripts'];
const OPTIONAL_INPUT_TREES=['public'];
const CONFIG_FILES=/^(?:index\.html|package\.json|package-lock\.json|(?:vite|tsconfig)(?:\.[^/]+)?\.json|(?:vite|vitest)\.[^/]+\.[cm]?[jt]s)$/i;

export function assertSandboxRoot(value){
 const root=realDirectory(value,'sandbox root');
 if(path.basename(root).toLowerCase()!=='sandbox'||path.basename(path.dirname(root)).toLowerCase()!=='.worktrees')throw Error('sandbox root must be the canonical .worktrees/sandbox directory');
 return root;
}
function rejectDotenvFiles(desktop){
 const found=readdirSync(desktop,{withFileTypes:true}).find(entry=>/^\.env/i.test(entry.name));
 if(found)throw Error('fixed-control build refuses desktop dotenv entry by filename: '+found.name);
}

function sortedEntries(sandboxRoot, relPaths){
 const desktop=path.join(sandboxRoot,'desktop'),entries=[];
 for(const rel of relPaths){const entry=fileEntry(desktop,rel);entries.push(entry);}
 return entries.sort((a,b)=>a.path.localeCompare(b.path));
}
export function captureBuildInputs(sandboxRoot=DEFAULT_SANDBOX){
 sandboxRoot=assertSandboxRoot(sandboxRoot);
 const desktop=realDirectory(path.join(sandboxRoot,'desktop'),'desktop build root'),relPaths=[];rejectDotenvFiles(desktop);
 for(const dir of REQUIRED_INPUT_TREES){const root=realDirectory(path.join(desktop,dir),'desktop/'+dir+' build inputs');for(const rel of walk(root).files)relPaths.push(dir+'/'+rel);}
 for(const dir of OPTIONAL_INPUT_TREES){const root=path.join(desktop,dir);if(existsSync(root)){realDirectory(root,'desktop/'+dir+' build inputs');for(const rel of walk(root).files)relPaths.push(dir+'/'+rel);}}
 for(const entry of readdirSync(desktop,{withFileTypes:true})){if(CONFIG_FILES.test(entry.name)&&!entry.isDirectory())relPaths.push(entry.name);}
 return sortedEntries(sandboxRoot,[...new Set(relPaths)]);
}
export function captureBuildOutputs(sandboxRoot=DEFAULT_SANDBOX){
 sandboxRoot=assertSandboxRoot(sandboxRoot);const desktop=realDirectory(path.join(sandboxRoot,'desktop'),'desktop build root'),relPaths=[];
 for(const dir of ['dist-electron','dist']){const root=realDirectory(path.join(desktop,dir),'desktop/'+dir+' build output');for(const rel of walk(root).files)relPaths.push(dir+'/'+rel);}
 if(!relPaths.includes('dist-electron/main.js')||!relPaths.includes('dist-electron/preload.js')||!relPaths.includes('dist/index.html'))throw Error('build did not produce Electron main/preload and renderer index');
 return sortedEntries(sandboxRoot,relPaths);
}
function inventoryHash(entries){return sha(Buffer.from(canonical(entries)));}
function buildReceiptValue(inputs,outputs){return {format:1,owner:OWNER,attestation:false,input_sha256:inventoryHash(inputs),output_sha256:inventoryHash(outputs),inputs,outputs};}
function sameInventory(expected,current){return canonical(expected)===canonical(current);}
export function requireCurrentBuildReceipt(sandboxRoot=DEFAULT_SANDBOX){
 sandboxRoot=assertSandboxRoot(sandboxRoot);const receiptPath=path.join(sandboxRoot,...RECEIPT_REL.split('/'));
 if(!existsSync(receiptPath))throw Error('missing explicit fixed-control build receipt; run fixed-control-build.mjs --build');
 let receipt;try{receipt=JSON.parse(readFileSync(regularFile(receiptPath,'fixed-control build receipt'),'utf8'));}catch(error){throw Error('invalid fixed-control build receipt: '+error.message);}
 const keys=['format','owner','attestation','input_sha256','output_sha256','inputs','outputs'];
 if(!receipt||typeof receipt!=='object'||Array.isArray(receipt)||Object.keys(receipt).sort().join('\0')!==keys.sort().join('\0')||receipt.format!==1||receipt.owner!==OWNER||receipt.attestation!==false||!Array.isArray(receipt.inputs)||!Array.isArray(receipt.outputs))throw Error('invalid or tampered fixed-control build receipt');
 if(receipt.input_sha256!==inventoryHash(receipt.inputs)||receipt.output_sha256!==inventoryHash(receipt.outputs))throw Error('fixed-control build receipt inventory hash mismatch');
 const inputs=captureBuildInputs(sandboxRoot);if(!sameInventory(receipt.inputs,inputs))throw Error('fixed-control build receipt is old: current source/build inputs differ');
 const outputs=captureBuildOutputs(sandboxRoot);if(!sameInventory(receipt.outputs,outputs))throw Error('fixed-control build receipt is old or tampered: current build outputs differ');
 return {path:receiptPath,receipt,sha256:sha(readFileSync(receiptPath)),inputs,outputs};
}
function commandFor(step,desktop){
 if(step==='dependency-audit')return {executable:process.execPath,args:[path.join(desktop,'scripts','dependency-audit.mjs')],cwd:desktop,env:cleanHostEnvironment()};
 if(step==='tsc')return {executable:process.execPath,args:[path.join(desktop,'node_modules','typescript','bin','tsc'),'-p','tsconfig.node.json'],cwd:desktop,env:cleanHostEnvironment()};
 if(step==='vite-build')return {executable:process.execPath,args:[path.join(desktop,'node_modules','vite','bin','vite.js'),'build'],cwd:desktop,env:cleanHostEnvironment()};
 throw Error('unknown fixed-control build step');
}
export function runFixedControlBuild({sandboxRoot=DEFAULT_SANDBOX,args=process.argv.slice(2),runner=defaultRunner}={}){
 if(!Array.isArray(args)||args.length===0)return {built:false};
 if(args.length!==1||args[0]!=='--build')throw Error('Usage: node desktop/scripts/fixed-control-build.mjs [--build]');
 if(typeof runner!=='function')throw Error('build runner must be a function');
 sandboxRoot=assertSandboxRoot(sandboxRoot);const desktop=realDirectory(path.join(sandboxRoot,'desktop'),'desktop build root');rejectDotenvFiles(desktop);
 const before=captureBuildInputs(sandboxRoot);
 for(const step of ['dependency-audit','tsc','vite-build'])runner({step,...commandFor(step,desktop)});
 const after=captureBuildInputs(sandboxRoot);if(!sameInventory(before,after))throw Error('desktop build inputs changed during build; no receipt was written');
 const outputs=captureBuildOutputs(sandboxRoot),receipt=buildReceiptValue(after,outputs),artifacts=path.join(sandboxRoot,'artifacts','fixed-control-e0');
 if(!existsSync(artifacts))mkdirSync(artifacts,{recursive:true});realDirectory(artifacts,'fixed-control artifacts root');
 const target=path.join(artifacts,'build-receipt.json'),temporary=target+'.'+process.pid+'.tmp';
 try{writeFileSync(temporary,canonical(receipt)+'\n',{flag:'wx'});renameSync(temporary,target);}catch(error){if(existsSync(temporary))try{unlinkSync(temporary);}catch{}throw error;}
 return {built:true,receipt,path:target,input_sha256:receipt.input_sha256,output_sha256:receipt.output_sha256};
}
function defaultRunner({step,executable,args,cwd,env}){try{return execFileSync(executable,args,{cwd,env,encoding:'utf8',windowsHide:true,stdio:'inherit',maxBuffer:4*1024*1024});}catch(error){throw Error(`${step} failed${error.status===undefined?'':` (exit ${error.status})`}: ${error.message}`);}}

const isMain=process.argv[1]&&pathToFileURL(path.resolve(process.argv[1])).href===import.meta.url;
if(isMain){const args=process.argv.slice(2);if(args.includes('--help')||args.includes('-h'))console.log('Usage: node desktop/scripts/fixed-control-build.mjs [--build]\nDefault is inert. --build runs dependency audit, tsc, and Vite build, then writes an unsigned build inventory receipt.');else try{const result=runFixedControlBuild({args});if(result.built)console.log(JSON.stringify({receipt:result.path,input_sha256:result.input_sha256,output_sha256:result.output_sha256,attestation:false},null,2));else console.log('Inert: pass --build to run the fixed-control build.');}catch(error){console.error(`Fixed-control build failed: ${error.message}`);process.exitCode=1;}}
