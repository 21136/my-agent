import assert from 'node:assert/strict';
import {test} from 'node:test';
import * as fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {spawnSync} from 'node:child_process';
import vm from 'node:vm';
import {createHash} from 'node:crypto';
import * as paths from '../../launcher/paths.mjs';

const require=createRequire(import.meta.url);
const {transformSync}=require('esbuild');
const source=fileURLToPath(new URL('../../launcher/paths.mjs',import.meta.url));
const base=process.env.FIXED_HOST_NATIVE_EVIDENCE ?? fileURLToPath(new URL('../../../artifacts/physical-fs-tests/',import.meta.url));
function ownedRoot() { fs.mkdirSync(base,{recursive:true});return fs.mkdtempSync(path.join(base,'physical-fs-test-')); }
function cjsCode() {
  const result=transformSync(fs.readFileSync(source,'utf8'),{format:'cjs',platform:'node',target:'node20'});
  assert.equal(result.warnings.length,0,'CJS conversion must have no import.meta/top-level-await warnings');
  return result.code;
}

test('physical paths preserve raw ASAR files under Node and convert cleanly to CJS',()=>{
  const root=ownedRoot(),bytes=Buffer.from('physical archive bytes, never a virtual directory');
  fs.writeFileSync(path.join(root,'example.asar'),bytes,{flag:'wx'});
  assert.deepEqual(paths.walk(root),{files:['example.asar'],directories:[]});
  const expected={path:'example.asar',sha256:createHash('sha256').update(bytes).digest('hex'),byte_length:bytes.length};
  assert.deepEqual(paths.fileEntry(root,'example.asar'),expected);
  const module={exports:{}};
  vm.runInNewContext(cjsCode(),{exports:module.exports,module,require,process,Buffer});
  assert.deepEqual(JSON.parse(JSON.stringify(module.exports.walk(root))),{files:['example.asar'],directories:[]});
  assert.deepEqual(JSON.parse(JSON.stringify(module.exports.fileEntry(root,'example.asar'))),expected);
});

test('Electron physical filesystem selection fails closed if original-fs cannot load',()=>{
  const exports={},deniedRequire=id=>{
    if(id==='node:module')return {createRequire:()=>{throw Error('original-fs unavailable');}};
    return require(id);
  };
  assert.throws(()=>vm.runInNewContext(cjsCode(),{exports,module:{exports},require:deniedRequire,
    process:{versions:{electron:'test'},execPath:process.execPath,platform:process.platform},Buffer}),/original-fs unavailable/);
});

test('headless Electron ESM and converted CJS verify the same complete physical runtime tree',{
  skip:!process.env.FIXED_HOST_PHYSICAL_MANIFEST,
},t=>{
  const root=ownedRoot(),manifest=path.resolve(process.env.FIXED_HOST_PHYSICAL_MANIFEST);
  const runtime=JSON.parse(fs.readFileSync(manifest,'utf8')).runtimes.electron;
  const expected={files:runtime.files.map(f=>f.path).sort(),directories:runtime.directories};
  assert.deepEqual(paths.walk(runtime.root),expected,'Node baseline must match the same manifest');
  for(const item of runtime.files)assert.deepEqual(paths.fileEntry(runtime.root,item.path),item);
  fs.writeFileSync(path.join(root,'paths-converted.cjs'),cjsCode(),{flag:'wx'});
  fs.writeFileSync(path.join(root,'package.json'),JSON.stringify({name:'headless-physical-fs-regression',main:'main.cjs'}),{flag:'wx'});
  const appSource=`
const {app}=require('electron'),fs=require('original-fs'),path=require('node:path');
for(const key of ['userData','sessionData','crashDumps','logs']){const dir=path.join(__dirname,key);fs.mkdirSync(dir,{recursive:true});app.setPath(key,dir);}
app.disableHardwareAcceleration();
app.whenReady().then(async()=>{
 const runtime=JSON.parse(fs.readFileSync(${JSON.stringify(manifest)},'utf8')).runtimes.electron;
 const expected={files:runtime.files.map(f=>f.path).sort(),directories:runtime.directories};
 const noAsarBefore=process.noAsar;
 const results=[];
 for(const [mode,module] of [['esm',await import(${JSON.stringify(pathToFileURL(source).href)})],['cjs',require('./paths-converted.cjs')]]){
  try{
   const tree=module.walk(runtime.root);
   const entries=runtime.files.map(item=>module.fileEntry(runtime.root,item.path));
   results.push({mode,tree_match:JSON.stringify(tree)===JSON.stringify(expected),
    entries_match:entries.length===runtime.files.length&&entries.every((entry,i)=>['path','sha256','byte_length'].every(k=>entry[k]===runtime.files[i][k])),file_count:tree.files.length,directory_count:tree.directories.length});
  }catch(error){results.push({mode,error:error.message,tree_match:false,entries_match:false});}
 }
 fs.writeFileSync(path.join(__dirname,'result.json'),JSON.stringify({results,no_asar_unchanged:process.noAsar===noAsarBefore}),{flag:'wx'});
 app.exit(0);
}).catch(()=>app.exit(2));
`;
  fs.writeFileSync(path.join(root,'main.cjs'),appSource,{flag:'wx'});
  const env=Object.fromEntries(Object.entries(process.env).filter(([k])=>/^(SystemRoot|WINDIR|PATH|TEMP|TMP|USERPROFILE|LOCALAPPDATA|APPDATA|COMSPEC)$/i.test(k)));
  const child=spawnSync(path.join(runtime.root,runtime.entry),[root],{env,windowsHide:true,timeout:30000,maxBuffer:65536,encoding:'utf8'});
  fs.writeFileSync(path.join(root,'process.json'),JSON.stringify({status:child.status,signal:child.signal,error:child.error?.code??null,stdout:child.stdout,stderr:child.stderr}),{flag:'wx'});
  assert.equal(child.status,0,'headless probe must exit normally');
  const result=JSON.parse(fs.readFileSync(path.join(root,'result.json'),'utf8'));
  t.diagnostic('Retained complete-tree Electron regression: '+root);
  assert.equal(result.no_asar_unchanged,true);
  for(const item of result.results){assert.equal(item.tree_match,true,JSON.stringify(item));assert.equal(item.entries_match,true,JSON.stringify(item));}
  assert.equal(result.results.length,2);
});