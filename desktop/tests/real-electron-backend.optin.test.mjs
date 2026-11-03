import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,renameSync,rmSync,writeFileSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {test} from 'node:test';
import {assertCleanFixtureRoots,assertOptIn} from './real-electron-backend.optin.mjs';
import {cleanHostEnvironment} from '../launcher/binding.mjs';
import * as e0 from './real-electron-backend.optin.mjs';

test('real GUI fixture requires an explicit opt-in token',()=>{
 assert.throws(()=>assertOptIn({}),/REAL_ELECTRON_BACKEND_OPT_IN=1/);
 assert.throws(()=>assertOptIn({REAL_ELECTRON_BACKEND_OPT_IN:'true'}),/REAL_ELECTRON_BACKEND_OPT_IN=1/);
 assert.doesNotThrow(()=>assertOptIn({REAL_ELECTRON_BACKEND_OPT_IN:'1'}));
});

test('fixture roots must be canonical empty directories before launch',t=>{
 const root=mkdtempSync(path.join(os.tmpdir(),'real-electron-fixture-'));
 t.after(()=>rmSync(root,{recursive:true,force:true}));
 const roots=['data','workspace','profile']; for(const name of roots)mkdirSync(path.join(root,name));
 assert.deepEqual(assertCleanFixtureRoots(root),roots.map(name=>path.join(root,name)));
 writeFileSync(path.join(root,'data','sentinel'),'must preserve');
 assert.throws(()=>assertCleanFixtureRoots(root),/must be empty/);
});
import {assertFixtureInstanceLayout} from './real-electron-backend.optin.mjs';
test('instance root admits only its identity marker and isolated data roots',t=>{
 const root=mkdtempSync(path.join(os.tmpdir(),'real-electron-instance-'));
 t.after(()=>rmSync(root,{recursive:true,force:true}));
 for(const name of ['data','workspace','profile'])mkdirSync(path.join(root,name));
 const binding={instance_root:root,release:{root:path.join(root,'..','release')}};
 writeFileSync(path.join(root,'.fixed-host-instance.json'),JSON.stringify({format:1,mode:'launcher_fixture_v1',code_root:path.join(binding.release.root,'app')}));
 assert.equal(assertFixtureInstanceLayout(binding),root);
 writeFileSync(path.join(root,'unexpected.txt'),'not owned');
 assert.throws(()=>assertFixtureInstanceLayout(binding),/unexpected entries/);
});
import {assertFixedHostPortZero} from './real-electron-backend.optin.mjs';
test('fixed Host is pinned to the port-0 backend startup path, not port 8765',()=>{
 assert.doesNotThrow(()=>assertFixedHostPortZero('const DEFAULT_WS_PORT = fixedHostContext || evolutionTestHostContext ? 0 : Number(env); const args = fixedHostContext ? ["-I","-B","-S",script,"--port","0"] : [script];'));
 assert.throws(()=>assertFixedHostPortZero('const DEFAULT_WS_PORT = 8765; const args = [script,"--port","8765"];'),/--port 0/);
});



test('E0 host environment redirects every user-data directory into the owned profile and rejects identity replacement', t => {
 const root=mkdtempSync(path.join(os.tmpdir(),'real-electron-owned-profile-'));
 t.after(()=>rmSync(root,{recursive:true,force:true}));
 const source={SYSTEMROOT:'C:/Windows',PATH:'C:/Windows/System32',USERPROFILE:'C:/Users/real-user',APPDATA:'C:/Users/real-user/AppData/Roaming',LOCALAPPDATA:'C:/Users/real-user/AppData/Local',TEMP:'C:/Users/real-user/AppData/Local/Temp',TMP:'C:/Users/real-user/AppData/Local/Temp',HOME:'C:/Users/real-user',XDG_CONFIG_HOME:'C:/Users/real-user/.config',LLM_API_KEY:'must-not-forward'};
 const profile=path.join(root,'host','profile');mkdirSync(profile,{recursive:true});
 const layout=e0.prepareIsolatedHostEnvironment(profile,source);
 for(const key of ['USERPROFILE','APPDATA','LOCALAPPDATA','TEMP','TMP','HOME','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_CACHE_HOME','TMPDIR']){
  const value=layout.env[key];
  assert.equal(path.resolve(value),path.resolve(layout.directories[key]));
  const relative=path.relative(root,path.resolve(value));
  assert.ok(relative&&!relative.startsWith('..')&&!path.isAbsolute(relative));
 }
 assert.equal(Object.hasOwn(layout.env,'LLM_API_KEY'),false);
 const electronEnv=cleanHostEnvironment(layout.env);
 for(const key of ['USERPROFILE','APPDATA','LOCALAPPDATA','TEMP','TMP']) assert.equal(path.resolve(electronEnv[key]),path.resolve(layout.directories[key]));
 assert.equal(Object.hasOwn(electronEnv,'LLM_API_KEY'),false);
 assert.equal(e0.assertOwnedHostEnvironment(layout,{requireEmpty:true}),true);
 const home=layout.directories.HOME,moved=home+'.original';
 renameSync(home,moved);mkdirSync(home);
 assert.throws(()=>e0.assertOwnedHostEnvironment(layout,{requireEmpty:true}),/identity|canonical|owned/i);
 rmSync(home,{recursive:true,force:true});renameSync(moved,home);
 assert.equal(e0.assertOwnedHostEnvironment(layout,{requireEmpty:true}),true);
});

test('E0 launch plan carries the exact isolated environment and never invokes a shell',()=>{
 const env={USERPROFILE:'F:/run/host/profile/userprofile',APPDATA:'F:/run/host/profile/appdata/Roaming',LOCALAPPDATA:'F:/run/host/profile/appdata/Local',HOME:'F:/run/host/profile/home',TEMP:'F:/run/host/profile/tmp',TMP:'F:/run/host/profile/tmp'};
 const spec=e0.buildFixedControlLaunchSpec({nodeExecutable:'F:/node/node.exe',launcherEntry:'F:/run/launcher/cli.mjs',bindingPath:'F:/run/control-state/binding.json',env});
 assert.equal(spec.executable,'F:/node/node.exe');
 assert.deepEqual(spec.args,['F:/run/launcher/cli.mjs','--launch','F:/run/control-state/binding.json']);
 assert.equal(spec.options.env,env);
 assert.equal(spec.options.cwd,'F:/run/launcher');
 assert.equal(spec.options.shell,false);
 assert.equal(spec.options.windowsHide,true);
});

test('E0 prelaunch child process verifies sanitized environment and fixed AgentPaths without starting Host', t => {
 const root=mkdtempSync(path.join(os.tmpdir(),'real-electron-preflight-'));
 t.after(()=>rmSync(root,{recursive:true,force:true}));
 const codeRoot=path.resolve(import.meta.dirname,'../..');
 const instanceRoot=path.join(root,'host');
 for(const name of ['data','workspace','profile'])mkdirSync(path.join(instanceRoot,name),{recursive:true});
 writeFileSync(path.join(instanceRoot,'.fixed-host-instance.json'),JSON.stringify({format:1,mode:'launcher_fixture_v1',code_root:codeRoot}),{flag:'wx'});
 const layout=e0.prepareIsolatedHostEnvironment(path.join(instanceRoot,'profile'),{
  SYSTEMROOT:process.env.SYSTEMROOT,PATH:process.env.PATH,USERPROFILE:'C:/Users/not-the-fixture',
  APPDATA:'C:/Users/not-the-fixture/AppData/Roaming',LOCALAPPDATA:'C:/Users/not-the-fixture/AppData/Local',
  TEMP:'C:/Users/not-the-fixture/Temp',TMP:'C:/Users/not-the-fixture/Temp',HOME:'C:/Users/not-the-fixture',
  LLM_API_KEY:'not-a-real-key',
 });
 const python=path.resolve(codeRoot,'..','..','.venv','Scripts','python.exe');
 const proof=e0.runE0IsolationPreflight({pythonExecutable:python,codeRoot,instanceRoot,layout});
 assert.equal(proof.status,'verified');
 assert.equal(proof.host_started,false);
 assert.equal(path.resolve(proof.agent_root),instanceRoot);
 assert.equal(path.resolve(proof.data),path.join(instanceRoot,'data'));
 assert.equal(path.resolve(proof.workspace),path.join(instanceRoot,'workspace'));
 assert.equal(proof.my_agent_root_env,'absent_fixed_layout_is_authoritative');
 assert.deepEqual(proof.user_roots.USERPROFILE,layout.directories.USERPROFILE);
 assert.deepEqual(proof.user_roots.APPDATA,layout.directories.APPDATA);
 assert.deepEqual(proof.user_roots.LOCALAPPDATA,layout.directories.LOCALAPPDATA);
 assert.deepEqual(proof.user_roots.TEMP,layout.directories.TEMP);
 assert.deepEqual(proof.user_roots.TMP,layout.directories.TMP);
 for(const key of ['HOME','TMPDIR','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_CACHE_HOME']) assert.equal(proof.optional_roots[key],'absent');
});
