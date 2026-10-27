import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {assertRecord,canonical,fileEntry,readJson,realDirectory,samePath,walk} from './paths.mjs';
import {readFixtureBinding} from './binding.mjs';

const HERE=path.dirname(fileURLToPath(import.meta.url));
const KIT_FIELDS=['format','owner','files'];
const HASH=/^[0-9a-f]{64}$/;
function verifyLauncherKit(root){
 root=realDirectory(root,'launcher kit root');const manifestPath=path.join(root,'manifest.json');const manifest=readJson(manifestPath,4*1024*1024);assertRecord(manifest,KIT_FIELDS,'launcher kit manifest');if(manifest.format!==1||manifest.owner!=='my-agent-fixed-control-e0-launcher'||!Array.isArray(manifest.files)||!manifest.files.length)throw Error('launcher kit manifest identity invalid');
 const seen=new Set();for(const item of manifest.files){assertRecord(item,['path','sha256','byte_length'],'launcher kit file');if(typeof item.path!=='string'||!item.path||item.path.startsWith('/')||item.path.includes('\\')||item.path.split('/').some(p=>!p||p==='.'||p==='..')||!HASH.test(item.sha256)||!Number.isSafeInteger(item.byte_length)||item.byte_length<0)throw Error('launcher kit manifest entry invalid');const key=item.path.toLowerCase();if(seen.has(key))throw Error('launcher kit path alias');seen.add(key);const actual=fileEntry(root,item.path);if(actual.sha256!==item.sha256||actual.byte_length!==item.byte_length)throw Error('launcher kit file hash/length mismatch: '+item.path);}
 const actual=walk(root).files.filter(rel=>rel!=='manifest.json').sort();const expected=manifest.files.map(x=>x.path).sort();if(canonical(actual)!==canonical(expected))throw Error('launcher kit inventory differs from its manifest');for(const required of ['binding.mjs','paths.mjs','release.mjs','journal.mjs','lifecycle.mjs','index.mjs','cli.mjs'])if(!seen.has(required))throw Error('launcher kit missing required module: '+required);return manifest;
}
export {verifyLauncherKit};

export async function runCli(args=process.argv.slice(2),{write=console.log,entryPath=HERE+'/cli.mjs',nodeExecutable=process.execPath,env=process.env,spawn}={}){
 if(!Array.isArray(args))throw Error('CLI arguments must be an array');
 if(args.length===1&&['--help','-h'].includes(args[0])){write('Usage: node cli.mjs --check <binding.json> | --launch <binding.json>\n--check validates fixture binding and launcher kit only. --launch explicitly starts the fixed Host.');return {code:0};}
 if(args.length!==2||!['--check','--launch'].includes(args[0])||typeof args[1]!=='string'||!args[1]){write('Usage: node cli.mjs --check <binding.json> | --launch <binding.json>');return {code:2};}
 const [,bindingPath]=args;const checked=readFixtureBinding(bindingPath);const kit=verifyLauncherKit(checked.binding.launcher_root);
 const nodeRuntime=checked.manifest.runtimes.node;const nodeRuntimeMatches=samePath(nodeExecutable,path.join(nodeRuntime.root,...nodeRuntime.entry.split('/')));
 if(args[0]==='--check'){write(JSON.stringify({state:nodeRuntimeMatches?'verified':'runtime_mismatch',mode:checked.binding.mode,installation_id:checked.binding.installation_id,release_id:checked.binding.release.release_id,manifest_digest:checked.binding.release.manifest_digest,node_runtime_matches:nodeRuntimeMatches,launcher_files:kit.files.length}));return {code:0,binding:checked.binding,nodeRuntimeMatches};}
 if(!nodeRuntimeMatches)throw Error('current Node executable does not match the recorded fixed runtime tree');
 const unsafe=Object.keys(env??{}).filter(key=>/^(?:NODE_OPTIONS|NODE_PATH|ELECTRON_RUN_AS_NODE|VITE_[A-Z0-9_]*)$/i.test(key));if(unsafe.length)throw Error('unsafe launch environment overrides: '+unsafe.sort().join(', '));
 const caller=realDirectory(path.dirname(path.resolve(entryPath)),'launcher entry directory');if(!samePath(caller,checked.binding.launcher_root))throw Error('--launch must be invoked from the verified copied launcher kit');
 if(!kit.files.some(f=>f.path==='lifecycle.mjs'))throw Error('verified launcher kit does not contain lifecycle.mjs');
 const lifecycle=await import('./lifecycle.mjs');if(typeof lifecycle.launchFixedHost!=='function')throw Error('fixed Host lifecycle API unavailable');
 const result=await lifecycle.launchFixedHost({bindingPath,spawn});write(JSON.stringify(result));return {code:0,result};
}
const isMain=process.argv[1]&&pathToFileURL(path.resolve(process.argv[1])).href===import.meta.url;
if(isMain){try{const result=await runCli();process.exitCode=result.code;}catch(error){console.error(`Launcher refused: ${error.message}`);process.exitCode=1;}}
