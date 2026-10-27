import assert from 'node:assert/strict';
import {test} from 'node:test';
import {existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, unlinkSync, writeFileSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {fixture, git, put} from './fixtures.mjs';

const cliPath = new URL('../../launcher/cli.mjs', import.meta.url);
const prepPath = new URL('../../scripts/prepare-fixed-control.mjs', import.meta.url);
const buildPath = new URL('../../scripts/fixed-control-build.mjs', import.meta.url);
let cli = {};
let prep = {};
let build = {};
try { cli = await import(cliPath); } catch (error) { if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error; }
try { prep = await import(prepPath); } catch (error) { if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error; }
try { build = await import(buildPath); } catch (error) { if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error; }

function syntheticSandbox(t, options = {}) {
  const f = fixture(t, options);
  const sandboxRoot = path.join(f.root, '.worktrees', 'sandbox');
  const desktop = path.join(sandboxRoot, 'desktop');
  const electronSource = path.join(desktop, 'electron');
  const rendererSource = path.join(desktop, 'src');
  const core = path.join(sandboxRoot, 'agent-core');
  const electronRoot = path.join(f.root, 'synthetic-electron');
  const pythonRoot = path.join(f.root, 'synthetic-python');
  const pythonBaseRoot = path.join(f.root, 'synthetic-python-base');
  mkdirSync(path.join(desktop, 'dist-electron', 'evolution'), {recursive: true});
  mkdirSync(electronSource, {recursive: true});
  mkdirSync(path.join(desktop, 'launcher'), {recursive: true});
  mkdirSync(path.join(desktop, 'public'), {recursive: true});
  mkdirSync(path.join(desktop, 'scripts'), {recursive: true});
  mkdirSync(rendererSource, {recursive: true});
  mkdirSync(path.join(desktop, 'dist', 'assets'), {recursive: true});
  mkdirSync(core, {recursive: true});
  mkdirSync(electronRoot, {recursive: true});
  mkdirSync(pythonRoot, {recursive: true});
  mkdirSync(pythonBaseRoot, {recursive: true});
  put(sandboxRoot, 'desktop/package.json', JSON.stringify({name: 'fixture', version: '1', main: 'dist-electron/main.js', private_secret: 'must-not-copy'}));
  put(sandboxRoot, 'desktop/package-lock.json', JSON.stringify({lockfileVersion: 3, packages: {}}));
  put(sandboxRoot, 'desktop/index.html', '<main>source fixture</main>');
  put(sandboxRoot, 'desktop/vite.config.ts', 'export default {};');
  put(sandboxRoot, 'desktop/tsconfig.node.json', '{"compilerOptions":{}}');
  put(sandboxRoot, 'desktop/launcher/source.mjs', 'export const launcherSource = true;');
  put(sandboxRoot, 'desktop/public/public.txt', 'public input');
  put(sandboxRoot, 'desktop/scripts/build-input.mjs', 'export const buildScriptInput = true;')
  put(sandboxRoot, 'desktop/dist-electron/main.js', 'main --fixed-host-bootstrap-stdin my-agent-fixed-host-v1 fixed-host.ready -S fixed_host_entry.py python_base_root');
  put(sandboxRoot, 'desktop/dist-electron/evolution/host.js', 'trusted fixed control');
  put(sandboxRoot, 'desktop/electron/main.ts', 'source main');
  put(sandboxRoot, 'desktop/electron/preload.ts', 'source preload');
  put(sandboxRoot, 'desktop/electron/fixed-host-context.ts', 'source fixed context');
  put(sandboxRoot, 'desktop/src/index.ts', 'renderer source');
  put(sandboxRoot, 'desktop/dist-electron/preload.js', 'preload');
  put(sandboxRoot, 'desktop/dist-electron/other.js', 'other compiled output');
  put(sandboxRoot, 'desktop/dist/index.html', '<main>fixture</main>');
  put(sandboxRoot, 'desktop/dist/assets/app.js', 'renderer asset');
  put(sandboxRoot, 'desktop/dist/.env', 'never copy');
  put(sandboxRoot, 'agent-core/server.py', 'print("fixture")');
  put(sandboxRoot, 'agent-core/helper.py', '# imported runtime module');
  put(sandboxRoot, 'agent-core/evolution/__init__.py', '');
  put(sandboxRoot, 'agent-core/evolution/contracts.py', 'class ContractError(Exception): pass');
  put(sandboxRoot, 'agent-core/tools/builtin/evolution.py', 'from evolution.contracts import ContractError');
  put(sandboxRoot, 'agent-core/tests/test_private.py', '# excluded test code');
  put(sandboxRoot, 'agent-core/prompts/core.txt', 'fixture prompt template');
  put(sandboxRoot, 'agent-core/data/private.py', '# excluded private tree');
  put(electronRoot, 'electron.exe', 'electron runtime bytes');
  put(pythonRoot, 'python.exe', 'python runtime bytes');
  put(pythonBaseRoot, 'python.exe', 'python base runtime bytes');
  put(sandboxRoot, 'agent-core/fixed_host_entry.py', '# fixed worker entry');
  const input={f, sandboxRoot, electronRoot, pythonRoot, pythonBaseRoot, runtimeProbe: () => ({executable: path.join(pythonRoot, 'python.exe'), prefix: pythonRoot, base_prefix: pythonBaseRoot})};
  if(!options.withoutReceipt) build.runFixedControlBuild({sandboxRoot,args:['--build'],runner:({step})=>{input.buildSteps??=[];input.buildSteps.push(step);}});
  return input;
}

test('preparation module import and CLI help are inert', async t => {
  const f = fixture(t);
  assert.equal(typeof prep.prepareFixedControl, 'function', 'preparation API missing');
  assert.equal(typeof cli.runCli, 'function', 'independent CLI API missing');
  assert.equal(existsSync(path.join(f.root, 'launcher', 'manifest.json')), false);
  const output = await cli.runCli(['--help'], {write: () => {}});
  assert.equal(output.code, 0);
  assert.equal(existsSync(path.join(f.root, 'launcher', 'manifest.json')), false);
});

test('build wrapper is opt-in and inventories the explicit audit, tsc, and Vite build steps', t => {
  const input=syntheticSandbox(t,{withoutReceipt:true});
  const inert=build.runFixedControlBuild({sandboxRoot:input.sandboxRoot,runner:()=>{throw new Error('runner must not run without --build');},args:[]});
  assert.equal(inert.built,false);
  assert.equal(existsSync(path.join(input.sandboxRoot,'artifacts','fixed-control-e0','build-receipt.json')),false);
  const result=build.runFixedControlBuild({sandboxRoot:input.sandboxRoot,runner:({step})=>{input.buildSteps??=[];input.buildSteps.push(step);},args:['--build']});
  assert.equal(result.built,true);
  assert.deepEqual(input.buildSteps,['dependency-audit','tsc','vite-build']);
  assert.equal(result.receipt.attestation,false);
  assert.ok(result.receipt.outputs.some(item=>item.path==='dist-electron/main.js'&&item.sha256));
});

test('build wrapper withholds the receipt when its input inventory changes mid-build', t => {
  const input=syntheticSandbox(t,{withoutReceipt:true});
  assert.throws(()=>build.runFixedControlBuild({sandboxRoot:input.sandboxRoot,args:['--build'],runner:({step})=>{if(step==='tsc')put(input.sandboxRoot,'desktop/electron/main.ts','concurrent source edit');}}),/inputs changed during build/i);
  assert.equal(existsSync(path.join(input.sandboxRoot,'artifacts','fixed-control-e0','build-receipt.json')),false);
});

test('build input inventory allows a missing optional public directory and ignores unrelated desktop trees', t => {
  const input=syntheticSandbox(t,{withoutReceipt:true});
  rmSync(path.join(input.sandboxRoot,'desktop','public'),{recursive:true});
  mkdirSync(path.join(input.sandboxRoot,'desktop','node_modules','unrelated'),{recursive:true});
  put(input.sandboxRoot,'desktop/node_modules/unrelated/large-or-linked-build-irrelevant.txt','ignore');
  const entries=build.captureBuildInputs(input.sandboxRoot);
  assert.ok(!entries.some(item=>item.path.startsWith('public/')));
  assert.ok(!entries.some(item=>item.path.startsWith('node_modules/')));
  assert.ok(!entries.some(item=>item.path==='.npmrc'));
});

test('fixed build rejects dotenv files by filename and passes a sanitized environment to every runner step', t => {
  const input=syntheticSandbox(t,{withoutReceipt:true});
  put(input.sandboxRoot,'desktop/.env.local','PRIVATE_TEST_VALUE=do-not-read-or-log');
  let calls=0;
  assert.throws(()=>build.runFixedControlBuild({sandboxRoot:input.sandboxRoot,args:['--build'],runner:()=>{calls++;}}),/dotenv|\.env/i);
  assert.equal(calls,0);
  unlinkSync(path.join(input.sandboxRoot,'desktop','.env.local'));
  const name='VITE_TEST_OVERRIDE';const previous=process.env[name];process.env[name]='not-a-secret';
  try {
    build.runFixedControlBuild({sandboxRoot:input.sandboxRoot,args:['--build'],runner:({env})=>{calls++;assert.equal(Object.keys(env).some(key=>/^NODE_|^VITE_/i.test(key)),false);}});
  } finally {if(previous===undefined)delete process.env[name];else process.env[name]=previous;}
  assert.equal(calls,3);
});

test('build and prepare reject a non-sandbox root before runner, probe, or writes', async t => {
  const f=fixture(t);
  const source=readFileSync(path.join(f.root,'payload','desktop','dist-electron','main.js'));
  let runnerCalls=0,probeCalls=0;
  assert.throws(()=>build.runFixedControlBuild({sandboxRoot:f.root,args:['--build'],runner:()=>{runnerCalls++;}}),/sandbox root|\.worktrees.*sandbox/i);
  await assert.rejects(()=>prep.prepareFixedControl({sandboxRoot:f.root,runtimeProbe:()=>{probeCalls++;throw new Error('probe should not run');}}),/sandbox root|\.worktrees.*sandbox/i);
  assert.equal(runnerCalls,0);
  assert.equal(probeCalls,0);
  assert.equal(existsSync(path.join(f.root,'artifacts')),false);
  assert.deepEqual(readFileSync(path.join(f.root,'payload','desktop','dist-electron','main.js')),source);
});

test('preparation rejects missing build receipt before creating a run', async t => {
  const input=syntheticSandbox(t,{withoutReceipt:true});
  const artifactRoot=path.join(input.sandboxRoot,'artifacts','fixed-control-e0');
  const before=existsSync(artifactRoot)?readdirSync(artifactRoot).filter(name=>name.startsWith('run-')).length:0;
  await assert.rejects(()=>prep.prepareFixedControl({sandboxRoot:input.sandboxRoot,nodeExecutable:path.join(input.f.root,'runtimes','node','runtime.exe'),electronRoot:input.electronRoot,pythonRoot:input.pythonRoot,pythonBaseRoot:input.pythonBaseRoot,runtimeProbe:input.runtimeProbe,availableBytes:()=>8*1024**3}),/receipt|build/i);
  const after=existsSync(artifactRoot)?readdirSync(artifactRoot).filter(name=>name.startsWith('run-')).length:0;
  assert.equal(after,before);
});

test('preparation rejects source drift even when compiled main markers remain unchanged', async t => {
  const input=syntheticSandbox(t);
  put(input.sandboxRoot,'desktop/electron/main.ts','changed source with existing fixed-host markers unrelated to stale dist');
  const artifactRoot=path.join(input.sandboxRoot,'artifacts','fixed-control-e0');
  const before=existsSync(artifactRoot)?readdirSync(artifactRoot).filter(name=>name.startsWith('run-')).length:0;
  await assert.rejects(()=>prep.prepareFixedControl({sandboxRoot:input.sandboxRoot,nodeExecutable:path.join(input.f.root,'runtimes','node','runtime.exe'),electronRoot:input.electronRoot,pythonRoot:input.pythonRoot,pythonBaseRoot:input.pythonBaseRoot,runtimeProbe:input.runtimeProbe,availableBytes:()=>8*1024**3}),/receipt|source|input|build/i);
  const after=existsSync(artifactRoot)?readdirSync(artifactRoot).filter(name=>name.startsWith('run-')).length:0;
  assert.equal(after,before);
});

test('preparation rejects output drift despite intact fixed Host markers', async t => {
  const input=syntheticSandbox(t);
  put(input.sandboxRoot,'desktop/dist-electron/main.js','main --fixed-host-bootstrap-stdin my-agent-fixed-host-v1 fixed-host.ready -S fixed_host_entry.py python_base_root drift');
  const artifactRoot=path.join(input.sandboxRoot,'artifacts','fixed-control-e0');
  const before=existsSync(artifactRoot)?readdirSync(artifactRoot).filter(name=>name.startsWith('run-')).length:0;
  await assert.rejects(()=>prep.prepareFixedControl({sandboxRoot:input.sandboxRoot,nodeExecutable:path.join(input.f.root,'runtimes','node','runtime.exe'),electronRoot:input.electronRoot,pythonRoot:input.pythonRoot,pythonBaseRoot:input.pythonBaseRoot,runtimeProbe:input.runtimeProbe,availableBytes:()=>8*1024**3}),/receipt|output|build/i);
  const after=existsSync(artifactRoot)?readdirSync(artifactRoot).filter(name=>name.startsWith('run-')).length:0;
  assert.equal(after,before);
});


test('preparation pins copied desktop outputs to the receipt even if main changes after receipt validation', async t => {
  const input=syntheticSandbox(t);
  const artifactRoot=path.join(input.sandboxRoot,'artifacts','fixed-control-e0');
  let changed=false;
  await assert.rejects(()=>prep.prepareFixedControl({
    sandboxRoot:input.sandboxRoot,
    nodeExecutable:path.join(input.f.root,'runtimes','node','runtime.exe'),
    electronRoot:input.electronRoot,
    pythonRoot:input.pythonRoot,
    pythonBaseRoot:input.pythonBaseRoot,
    runtimeProbe:()=>{
      changed=true;
      put(input.sandboxRoot,'desktop/dist-electron/main.js','main --fixed-host-bootstrap-stdin my-agent-fixed-host-v1 fixed-host.ready -S fixed_host_entry.py python_base_root changed-after-receipt');
      return input.runtimeProbe();
    },
    availableBytes:()=>8*1024**3,
  }),/receipt|output|hash|build|pinned/i);
  assert.equal(changed,true);
  const runs=existsSync(artifactRoot)?readdirSync(artifactRoot).filter(name=>name.startsWith('run-')):[];
  for(const run of runs){
    const releases=path.join(artifactRoot,run,'releases');
    assert.equal(existsSync(releases)?readdirSync(releases).some(name=>/^[0-9a-f]{64}$/.test(name)):false,false,'changed build output must not be published');
  }
});


test('preparation rejects a replaced receipt before publishing the release', async t => {
  const input=syntheticSandbox(t);
  const artifactRoot=path.join(input.sandboxRoot,'artifacts','fixed-control-e0');
  const receiptPath=path.join(artifactRoot,'build-receipt.json');
  let replaced=false;
  await assert.rejects(()=>prep.prepareFixedControl({
    sandboxRoot:input.sandboxRoot,
    nodeExecutable:path.join(input.f.root,'runtimes','node','runtime.exe'),
    electronRoot:input.electronRoot,
    pythonRoot:input.pythonRoot,
    pythonBaseRoot:input.pythonBaseRoot,
    runtimeProbe:()=>{
      replaced=true;
      const receipt=JSON.parse(readFileSync(receiptPath,'utf8'));
      writeFileSync(receiptPath,JSON.stringify(receipt,null,2));
      return input.runtimeProbe();
    },
    availableBytes:()=>8*1024**3,
  }),/receipt|changed|build/i);
  assert.equal(replaced,true);
  const runs=existsSync(artifactRoot)?readdirSync(artifactRoot).filter(name=>name.startsWith('run-')):[];
  for(const run of runs){
    const releases=path.join(artifactRoot,run,'releases');
    assert.equal(existsSync(releases)?readdirSync(releases).some(name=>/^[0-9a-f]{64}$/.test(name)):false,false,'replaced receipt must not publish a release');
  }
});

test('preparation rejects a tampered build receipt before creating a run', async t => {
  const input=syntheticSandbox(t);
  const receiptPath=path.join(input.sandboxRoot,'artifacts','fixed-control-e0','build-receipt.json');
  const receipt=JSON.parse(readFileSync(receiptPath,'utf8'));receipt.outputs[0].sha256='0'.repeat(64);writeFileSync(receiptPath,JSON.stringify(receipt));
  const artifactRoot=path.dirname(receiptPath);
  const before=readdirSync(artifactRoot).filter(name=>name.startsWith('run-')).length;
  await assert.rejects(()=>prep.prepareFixedControl({sandboxRoot:input.sandboxRoot,nodeExecutable:path.join(input.f.root,'runtimes','node','runtime.exe'),electronRoot:input.electronRoot,pythonRoot:input.pythonRoot,pythonBaseRoot:input.pythonBaseRoot,runtimeProbe:input.runtimeProbe,availableBytes:()=>8*1024**3}),/receipt|output|build|hash/i);
  const after=readdirSync(artifactRoot).filter(name=>name.startsWith('run-')).length;
  assert.equal(after,before);
});

test('preparation creates separate disposable Git targets and a hash-checked independent launcher kit', async t => {
  const input = syntheticSandbox(t);
  const result = await prep.prepareFixedControl({
    sandboxRoot: input.sandboxRoot,
    nodeExecutable: path.join(input.f.root, 'runtimes', 'node', 'runtime.exe'),
    electronRoot: input.electronRoot,
    pythonRoot: input.pythonRoot,
    pythonBaseRoot: input.pythonBaseRoot,
    runtimeProbe: input.runtimeProbe,
    availableBytes: () => 8 * 1024 ** 3,
  });
  assert.equal(result.binding.mode, 'launcher_fixture_v1');
  assert.notEqual(result.binding.stable_source_root, result.binding.sandbox_source_root);
  assert.deepEqual(JSON.parse(readFileSync(path.join(result.release.root, 'app', 'desktop', 'package.json'), 'utf8')), {main: 'dist-electron/main.js', name: 'fixture', version: '1'});
  assert.equal(existsSync(path.join(result.release.root, 'app', 'desktop', 'dist', '.env')), false);
  assert.equal(existsSync(path.join(result.release.root, 'app', 'agent-core', 'tests', 'test_private.py')), false);
  assert.equal(existsSync(path.join(result.release.root, 'app', 'agent-core', 'evolution', 'contracts.py')), true);
  assert.equal(existsSync(path.join(result.release.root, 'app', 'agent-core', 'tools', 'builtin', 'evolution.py')), true);
  assert.equal(existsSync(path.join(result.release.root, 'app', 'desktop', 'dist-electron', 'evolution', 'host.js')), true);
  const buildInputs = JSON.parse(readFileSync(result.buildInputsManifest, 'utf8'));
  assert.equal(buildInputs.attestation, false);
  assert.ok(buildInputs.source_inputs.some(item => item.path === 'agent-core/evolution/contracts.py'));
  assert.ok(buildInputs.release_payload.some(item => item.path === 'agent-core/fixed_host_entry.py'));
  assert.ok(buildInputs.runtime_inputs.python_base.length > 0);
  assert.ok(buildInputs.build_outputs.some(item => item.path === 'dist-electron/main.js'));
  assert.equal(JSON.parse(readFileSync(path.join(result.runRoot, 'host', '.fixed-host-instance.json'), 'utf8')).code_root, path.join(result.release.root, 'app'));
  assert.match(readFileSync(path.join(result.release.root, 'app', 'evolve', '_index.core.toml'), 'utf8'), /E0 fixture index/);
  assert.equal((await cli.verifyLauncherKit(result.binding.launcher_root)).files.length > 0, true);
  assert.equal(existsSync(path.join(result.binding.state_root, 'binding.json')), true);
  assert.equal(existsSync(path.join(result.binding.instance_root, 'data')), true);
});

test('prepared fixture Git worktrees are adopter-ready and clean at a shared baseline', async t => {
  const input = syntheticSandbox(t);
  const result = await prep.prepareFixedControl({
    sandboxRoot: input.sandboxRoot,
    nodeExecutable: path.join(input.f.root, 'runtimes', 'node', 'runtime.exe'),
    electronRoot: input.electronRoot,
    pythonRoot: input.pythonRoot,
    pythonBaseRoot: input.pythonBaseRoot,
    runtimeProbe: input.runtimeProbe,
    availableBytes: () => 8 * 1024 ** 3,
  });
  const adopterModule = await import(new URL('../../electron/sandbox-adopt.ts', import.meta.url));
  const status = await adopterModule.createSandboxAdopter({
    stableRoot: result.binding.stable_source_root,
    sandboxRoot: result.binding.sandbox_source_root,
  }).getStatus();
  assert.equal(status.sandboxActive, true);
  assert.deepEqual(status.fatalBlockers, ['没有待合入的实验改动。']);
  assert.equal(existsSync(path.join(result.binding.sandbox_source_root, 'desktop', 'package.json')), true);
  assert.equal(existsSync(path.join(result.binding.sandbox_source_root, 'agent-core', 'fixture.py')), true);
  const stableHead = git(result.binding.stable_source_root, 'rev-parse', 'HEAD');
  assert.equal(git(result.binding.sandbox_source_root, 'rev-parse', 'HEAD'), stableHead);
  assert.equal(git(result.binding.stable_source_root, 'status', '--porcelain'), '');
  assert.equal(git(result.binding.sandbox_source_root, 'status', '--porcelain'), '');
});

test('capacity failure occurs before creating the fixture run or copying inputs', async t => {
  const input = syntheticSandbox(t);
  const artifactRoot = path.join(input.sandboxRoot, 'artifacts', 'fixed-control-e0');
  const before = existsSync(artifactRoot) ? readdirSync(artifactRoot).filter(name => name.startsWith('run-')).length : 0;
  await assert.rejects(() => prep.prepareFixedControl({
    sandboxRoot: input.sandboxRoot,
    nodeExecutable: path.join(input.f.root, 'runtimes', 'node', 'runtime.exe'),
    electronRoot: input.electronRoot,
    pythonRoot: input.pythonRoot,
    pythonBaseRoot: input.pythonBaseRoot,
    runtimeProbe: input.runtimeProbe,
    availableBytes: () => 1,
  }), /capacity|space/i);
  const after = existsSync(artifactRoot) ? readdirSync(artifactRoot).filter(name => name.startsWith('run-')).length : 0;
  assert.equal(after, before);
});

test('launcher kit verification rejects tampering and --check never spawns', async t => {
  const input = syntheticSandbox(t);
  const result = await prep.prepareFixedControl({
    sandboxRoot: input.sandboxRoot,
    nodeExecutable: path.join(input.f.root, 'runtimes', 'node', 'runtime.exe'),
    electronRoot: input.electronRoot,
    pythonRoot: input.pythonRoot,
    pythonBaseRoot: input.pythonBaseRoot,
    runtimeProbe: input.runtimeProbe,
    availableBytes: () => 8 * 1024 ** 3,
  });
  let spawns = 0;
  const checked = await cli.runCli(['--check', path.join(result.binding.state_root, 'binding.json')], {write: () => {}, nodeExecutable: path.join(input.f.root, 'runtimes', 'node', 'runtime.exe'), spawn: () => { spawns++; }});
  assert.equal(checked.code, 0);
  assert.equal(checked.nodeRuntimeMatches, true);
  assert.equal(spawns, 0);
  writeFileSync(path.join(result.binding.launcher_root, 'paths.mjs'), 'tampered');
  await assert.rejects(async () => cli.verifyLauncherKit(result.binding.launcher_root), /launcher|hash|inventory|manifest/i);
});

test('stale compiled main without fixed Host bootstrap markers is refused before run creation', async t => {
  const input=syntheticSandbox(t);
  put(input.sandboxRoot, 'desktop/dist-electron/main.js', 'stale main without fixed bootstrap');
  build.runFixedControlBuild({sandboxRoot:input.sandboxRoot,runner:()=>{},args:['--build']});
  const artifactRoot=path.join(input.sandboxRoot,'artifacts','fixed-control-e0');
  const before=existsSync(artifactRoot)?readdirSync(artifactRoot).filter(name=>name.startsWith('run-')).length:0;
  await assert.rejects(()=>prep.prepareFixedControl({sandboxRoot:input.sandboxRoot,nodeExecutable:path.join(input.f.root,'runtimes','node','runtime.exe'),electronRoot:input.electronRoot,pythonRoot:input.pythonRoot,pythonBaseRoot:input.pythonBaseRoot,runtimeProbe:input.runtimeProbe,availableBytes:()=>8*1024**3}),/stale|marker/i);
  const after=existsSync(artifactRoot)?readdirSync(artifactRoot).filter(name=>name.startsWith('run-')).length:0;
  assert.equal(after,before);
});

test('Python probe identity must match the selected venv and a distinct base prefix', async t => {
  const input=syntheticSandbox(t);
  const artifactRoot=path.join(input.sandboxRoot,'artifacts','fixed-control-e0');
  const before=existsSync(artifactRoot)?readdirSync(artifactRoot).filter(name=>name.startsWith('run-')).length:0;
  await assert.rejects(()=>prep.prepareFixedControl({sandboxRoot:input.sandboxRoot,nodeExecutable:path.join(input.f.root,'runtimes','node','runtime.exe'),electronRoot:input.electronRoot,pythonRoot:input.pythonRoot,pythonBaseRoot:input.pythonBaseRoot,runtimeProbe:()=>({executable:path.join(input.pythonRoot,'other.exe'),prefix:input.pythonRoot,base_prefix:input.pythonBaseRoot}),availableBytes:()=>8*1024**3}),/match|Python/i);
  await assert.rejects(()=>prep.prepareFixedControl({sandboxRoot:input.sandboxRoot,nodeExecutable:path.join(input.f.root,'runtimes','node','runtime.exe'),electronRoot:input.electronRoot,pythonRoot:input.pythonRoot,pythonBaseRoot:input.pythonRoot,runtimeProbe:()=>({executable:path.join(input.pythonRoot,'python.exe'),prefix:input.pythonRoot,base_prefix:input.pythonRoot}),availableBytes:()=>8*1024**3}),/venv|base prefix/i);
  const after=existsSync(artifactRoot)?readdirSync(artifactRoot).filter(name=>name.startsWith('run-')).length:0;
  assert.equal(after,before);
});

test('fixture Git ignores ambient GIT_DIR, GIT_WORK_TREE, and GIT_INDEX_FILE', async t => {
  const input=syntheticSandbox(t,{repositories:true});
  const stable=path.join(input.f.root,'source-stable');
  const headBefore=git(stable,'rev-parse','HEAD');
  const indexBefore=readFileSync(path.join(stable,'.git','index'));
  const names=['GIT_DIR','GIT_WORK_TREE','GIT_INDEX_FILE'];const saved=Object.fromEntries(names.map(name=>[name,process.env[name]]));
  process.env.GIT_DIR=path.join(stable,'.git');process.env.GIT_WORK_TREE=stable;process.env.GIT_INDEX_FILE=path.join(stable,'.git','index');
  try { await prep.prepareFixedControl({sandboxRoot:input.sandboxRoot,nodeExecutable:path.join(input.f.root,'runtimes','node','runtime.exe'),electronRoot:input.electronRoot,pythonRoot:input.pythonRoot,pythonBaseRoot:input.pythonBaseRoot,runtimeProbe:input.runtimeProbe,availableBytes:()=>8*1024**3}); }
  finally { for(const name of names){if(saved[name]===undefined)delete process.env[name];else process.env[name]=saved[name];} }
  assert.equal(git(stable,'rev-parse','HEAD'),headBefore);
  assert.deepEqual(readFileSync(path.join(stable,'.git','index')),indexBefore);
});

test('launch requires the attested Node executable and rejects NODE/VITE overrides before spawn', async t => {
  const input=syntheticSandbox(t);
  const result=await prep.prepareFixedControl({sandboxRoot:input.sandboxRoot,nodeExecutable:path.join(input.f.root,'runtimes','node','runtime.exe'),electronRoot:input.electronRoot,pythonRoot:input.pythonRoot,pythonBaseRoot:input.pythonBaseRoot,runtimeProbe:input.runtimeProbe,availableBytes:()=>8*1024**3});
  const args=['--launch',path.join(result.binding.state_root,'binding.json')];let spawns=0;
  await assert.rejects(()=>cli.runCli(args,{write:()=>{},entryPath:path.join(result.binding.launcher_root,'cli.mjs'),nodeExecutable:process.execPath,env:{},spawn:()=>{spawns++;}}),/current Node executable/i);
  await assert.rejects(()=>cli.runCli(args,{write:()=>{},entryPath:path.join(result.binding.launcher_root,'cli.mjs'),nodeExecutable:path.join(input.f.root,'runtimes','node','runtime.exe'),env:{NODE_PATH:'C:/untrusted',VITE_DEV_SERVER_URL:'http://127.0.0.1'},spawn:()=>{spawns++;}}),/unsafe launch environment/i);
  assert.equal(spawns,0);
});

test('CLI rejects ambiguous commands and never launches on import or --prepare', async () => {
  const messages = [];
  assert.equal((await cli.runCli([], {write: value => messages.push(value)})).code, 2);
  assert.equal((await cli.runCli(['--prepare'], {write: value => messages.push(value)})).code, 2);
});





