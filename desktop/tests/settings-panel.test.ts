import assert from 'node:assert/strict';
import { test } from 'node:test';
import { existsSync, readFileSync } from 'node:fs';
import { mainFocusForRail } from '../src/shells/unified/rail.ts';

test('settings owns a dedicated stage, other rails keep their destinations', () => {
  assert.equal(mainFocusForRail('settings'), 'settings');
  assert.equal(mainFocusForRail('now'), 'chat');
  assert.equal(mainFocusForRail('docs'), 'document');
  assert.equal(mainFocusForRail('projects'), 'projects');
});
const moduleUrl = new URL('../src/shells/unified/settings-panel.ts', import.meta.url);
async function panel() {
  assert.ok(existsSync(moduleUrl), 'production settings panel must exist');
  return import(moduleUrl.href);
}
const data = {section:'appearance',partnerName:'小魔王',nicknameDraft:'小魔王',avatarSrc:'/pet.webp',daysTogether:3,theme:'light',inactiveTerminals:0,stoppedServices:0,fontControlHtml:'<div data-chat-font-control></div>',feedback:'',roleModels:{preferences:{main:'m1',subagent:'m2'},models:[{id:'m1',name:'Model 1',configured:true},{id:'m2',name:'Model 2',configured:true}],pendingRole:'',error:''},autoRecognition:{enabled:false,pending:false,available:true,error:false}};
test('settings sidebar contains navigation, not cramped controls', async () => {
  const p = await panel();
  const html = p.renderSettingsSidebar('appearance');
  for (const label of ['外观与阅读','专属搭档','模型与权限','运行环境','返回聊天']) assert.ok(html.includes(label));
  assert.match(html, /data-settings-section="appearance" aria-current="page"/);
  assert.ok(!html.includes('type="range"'));
  assert.equal(p.isSettingsSection('runtime'), true);
  assert.equal(p.isSettingsSection('other'), false);
});
test('appearance contains actual theme choices, reusable font control and reading preview', async () => {
  const p = await panel();
  const html = p.renderSettingsPane(data);
  assert.match(html, /role="radiogroup"/);
  assert.match(html, /data-settings-theme="light"[^>]*checked/);
  assert.ok(html.includes(data.fontControlHtml));
  assert.match(html, /settings-reading-preview/);
  assert.match(html, /settings-switch-cli/);
  assert.ok(!html.includes('55 天'));
});
test('companion uses actual bond age and safely escapes saved and unsaved names', async () => {
  const p = await panel();
  const html = p.renderSettingsPane({...data,section:'companion',partnerName:'<img onerror="bad">',nicknameDraft:'" autofocus x="'});
  assert.ok(html.includes('3 天'));
  assert.ok(html.includes('evolve/persona.md'));
  assert.ok(!html.includes('<img onerror='));
  assert.ok(html.includes('&lt;img'));
  assert.ok(!html.includes('value="" autofocus'));
  assert.match(html, /data-settings-name-form/);
});
test('model section delegates to existing real configuration entries', async () => {
  const p = await panel();
  const html = p.renderSettingsPane({...data,section:'models'});
  assert.match(html, /settings-open-model-keys/);
  assert.match(html, /settings-open-host-scope/);
  assert.ok(!html.includes('预览不会'));
});
test('runtime reflects real counts and disables cleanup only when empty', async () => {
  const p = await panel();
  const empty = p.renderSettingsPane({...data,section:'runtime'});
  assert.match(empty, /data-action="settings-prune-runtime" disabled/);
  const populated = p.renderSettingsPane({...data,section:'runtime',inactiveTerminals:2,stoppedServices:1});
  assert.ok(populated.includes('2 个可清理'));
  assert.ok(populated.includes('1 个可清理'));
  assert.ok(!populated.includes('data-action="settings-prune-runtime" disabled'));
});

test('models section lets the user choose main and subagent models and control semantic recognition', async () => {
  const p = await panel();
  const html = p.renderSettingsPane({...data,section:'models'});
  assert.match(html, /data-role="main"/);
  assert.match(html, /data-role="subagent"/);
  assert.match(html, /<option value="m1" selected>Model 1<\/option>/);
  assert.match(html, /role="switch"[^>]*aria-checked="false"/);
  assert.match(html, /aria-label="自动识别语义"/);
  const recognition = html.match(/<section class="section settings-semantic-recognition"[\s\S]*?<\/section>/)?.[0] ?? '';
  assert.match(recognition, /<h2>自动识别语义<\/h2>/);
  assert.match(recognition, /role="switch"/);
  assert.doesNotMatch(recognition, /<p|settings-recognition-description/);
  assert.match(html, /aria-checked="false">开启<\/button>/);
  const shell = readFileSync(new URL('../src/shells/unified/index.ts', import.meta.url), 'utf8');
  const transport = readFileSync(new URL('../src/api/ws.ts', import.meta.url), 'utf8');
  assert.match(shell, /client\.listRoleModels\(\)/);
  assert.match(shell, /client\.setRoleModel\(role, model\)/);
  assert.match(shell, /case "llm_role_models\.done"/);
  assert.match(shell, /setAutoRecognition\(autoRecognition\.enabled \? "revoke" : "enable"\)/);
  assert.match(transport, /type: "llm_role_models\.get"/);
  assert.match(transport, /type: "llm_role_models\.set"/);
});

test('runtime settings provides access to the existing terminal and service controls', async () => {
  const p = await panel();
  const html = p.renderSettingsPane({...data,section:'runtime'});
  assert.match(html, /settings-open-runtime-status/);
  assert.match(html, /终端与服务/);
  const shell = readFileSync(new URL('../src/shells/unified/index.ts', import.meta.url), 'utf8');
  assert.match(shell, /case "settings-open-runtime-status": selectSingleChatTool\("runtime"\)/);
});
