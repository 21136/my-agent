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
const data = {section:'appearance',partnerName:'小魔王',nicknameDraft:'小魔王',avatarSrc:'/pet.webp',daysTogether:3,theme:'light',inactiveTerminals:0,stoppedServices:0,fontControlHtml:'<div data-chat-font-control></div>',feedback:'',roleModels:{preferences:{main:'m1',subagent:'m2'},models:[{id:'m1',name:'Model 1',configured:true},{id:'m2',name:'Model 2',configured:true}],pendingRole:'',error:''},autoRecognition:{enabled:false,pending:false,available:true,error:false},approvalPolicy:{mode:'manual',savedMode:'manual',pending:false,available:true,error:false}};
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
test('models section accurately scopes approval and preserves a manual reset', async () => {
  const p = await panel();
  const defaultHtml = p.renderSettingsPane({...data,section:'models'});
  assert.match(defaultHtml, /默认逐项确认/);
  assert.match(defaultHtml, /范围内的自动放行/);
  assert.match(defaultHtml, /项目内命令自动放行/);
  assert.doesNotMatch(defaultHtml, /完全放行/);
  assert.match(defaultHtml, /已绑定项目.*动手/);
  assert.match(defaultHtml, /任务范围明确/);
  assert.match(defaultHtml, /只读检查命令/);
  assert.match(defaultHtml, /默认逐项确认/);
  assert.doesNotMatch(defaultHtml, /未分类的简单前台命令/);
  assert.match(defaultHtml, /data-approval-mode="manual"[^>]*checked/);
  assert.match(defaultHtml, /data-action="settings-approval-reset"/);
  assert.match(defaultHtml, /技术说明/);
  assert.match(defaultHtml, /不是沙箱/);
  assert.match(defaultHtml, /run_command/);
  assert.doesNotMatch(defaultHtml, /settings-approval-shell-warning/);
  assert.match(defaultHtml, /终端命令可能在项目外/);
  assert.match(defaultHtml, /不会限制终端命令/);
  assert.doesNotMatch(defaultHtml, /项目外路径.*仍按原规则拦截/);
  const fullHtml = p.renderSettingsPane({...data,section:'models',approvalPolicy:{mode:'full',savedMode:'auto',pending:false,available:true,error:false}});
  assert.match(fullHtml, /data-approval-mode="full"[^>]*checked/);
  assert.match(fullHtml, /本次运行实际生效/);
  assert.match(fullHtml, /重启后回到默认逐项确认/);
  assert.match(fullHtml, /不要求单独任务范围/);
  assert.match(fullHtml, /普通终端命令可自动放行/);
  assert.match(fullHtml, /终端命令可能在项目外/);
  assert.match(defaultHtml, /默认逐项确认/);
  assert.match(defaultHtml, /范围内的自动放行/);
  assert.match(fullHtml, /data-action="settings-approval-apply-saved"/);
  assert.match(fullHtml, /data-approval-mode="auto"/);
  assert.match(fullHtml, /<strong>范围内的自动放行<\/strong>/);
  const unavailableHtml = p.renderSettingsPane({...data,section:'models',approvalPolicy:{mode:'manual',savedMode:'manual',pending:false,available:false,error:false}});
  assert.match(unavailableHtml, /disabled/);
  assert.match(unavailableHtml, /不会本地模拟/);
  const unknownHtml = p.renderSettingsPane({...data,section:'models',approvalPolicy:{mode:'manual',savedMode:'manual',known:false,pending:false,available:false,error:true,message:'当前实际档位未知，无法确认或切换。'}});
  assert.match(unknownHtml, /当前实际审批档位未知/);
  assert.doesNotMatch(unknownHtml, /data-approval-mode="manual"[^>]*checked/);
  assert.match(unknownHtml, /无法确认或切换/);
  const pendingHtml = p.renderSettingsPane({...data,section:'models',approvalPolicy:{mode:'manual',savedMode:'manual',pending:true,available:false,error:false}});
  assert.doesNotMatch(pendingHtml, /审批设置接口暂不可用/);
  assert.match(pendingHtml, /正在读取或保存审批设置/);
  const invalidHtml = p.renderSettingsPane({...data,section:'models',approvalPolicy:{mode:'unknown',savedMode:'full',pending:false,available:true,error:false}} as typeof data);
  assert.match(invalidHtml, /data-approval-mode="manual"[^>]*checked/);
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

test('approval settings use trusted Electron IPC with security rejection and settings wiring', async () => {
  const preload = readFileSync(new URL('../electron/preload.ts', import.meta.url), 'utf8');
  const main = readFileSync(new URL('../electron/main.ts', import.meta.url), 'utf8');
  const transport = readFileSync(new URL('../src/api/ws.ts', import.meta.url), 'utf8');
  const shell = readFileSync(new URL('../src/shells/unified/index.ts', import.meta.url), 'utf8');
  const browserBridge = readFileSync(new URL('../src/browser-desktop-bridge.ts', import.meta.url), 'utf8');

  assert.match(preload, /getApprovalPreferences: \(\) => ipcRenderer\.invoke\("approval-preferences:get"\)/);
  assert.match(preload, /setApprovalMode: \(mode: "manual" \| "auto" \| "full"\) => ipcRenderer\.invoke\("approval-preferences:set", mode\)/);
  const getStart = main.indexOf('ipcMain.handle("approval-preferences:get"');
  const setStart = main.indexOf('ipcMain.handle("approval-preferences:set"');
  const unavailableStart = main.indexOf('function unavailableApprovalPreferences', setStart);
  assert.ok(getStart >= 0 && setStart > getStart && unavailableStart > setStart, 'main process must register both approval IPC handlers');
  const getHandler = main.slice(getStart, setStart);
  const setHandler = main.slice(setStart, unavailableStart);
  assert.match(getHandler, /assertShellDevWorkbenchSender\(event\)/);
  assert.match(getHandler, /审批设置仅允许可信工作台主页面读取/);
  assert.match(setHandler, /assertShellDevWorkbenchSender\(event\)/);
  assert.match(setHandler, /审批设置仅允许可信工作台主页面修改/);
  assert.match(setHandler, /rawMode !== "manual" && rawMode !== "auto" && rawMode !== "full"/);
  assert.match(setHandler, /审批档位无效/);
  assert.match(setHandler, /dialog\.showMessageBox\(parent/);
  assert.match(setHandler, /buttons: \["确认切换", "取消"\], defaultId: 1, cancelId: 1/);
  assert.match(setHandler, /answer\.response !== 0 \|\| !assertShellDevWorkbenchSender\(event\) \|\| workbenchWindow !== parent \|\| parent\.isDestroyed\(\)/);
  assert.match(setHandler, /用户未确认切换；请重新读取当前审批档位/);
  assert.match(setHandler, /approvalControl !== control/);

  assert.match(transport, /getApprovalPreferences\(\): void/);
  assert.match(transport, /window\.myAgentDesktop\?\.getApprovalPreferences/);
  assert.match(transport, /setApprovalMode\(mode: ApprovalPreferenceMode\): void/);
  assert.match(transport, /window\.myAgentDesktop\?\.setApprovalMode/);
  assert.match(transport, /invalid approval mode/);
  assert.match(transport, /Only authenticated Electron IPC may update this view/);
  assert.match(browserBridge, /status: "rejected"/);
  assert.match(browserBridge, /浏览器预览不提供审批设置授权/);

  assert.match(shell, /client\.getApprovalPreferences\(\)/);
  assert.match(shell, /client\.setApprovalMode\(mode\)/);
  assert.match(shell, /case "approval_preferences\.state":/);
  assert.match(shell, /case "approval_preferences\.done":/);
  assert.match(shell, /const stateKnown = event\.status === "ok" && event\.state_known !== false/);
  assert.match(shell, /known: stateKnown, pending: false, available: stateKnown/);
  assert.match(shell, /known: false, pending: false, available: false, error: true/);
  assert.match(shell, /event\.state_known !== false/);
  assert.match(shell, /known: false, pending: false, available: false, error: true, message: "读取审批设置失败/);
  assert.match(shell, /known: false, pending: false, available: false, error: true, message: "请求未能发送/);
  assert.match(main, /state_known: false/);
  assert.match(transport, /当前实际审批档位未知/);
  assert.match(shell, /case "settings-approval-reset": setApprovalPreference\("manual"\)/);
  assert.match(shell, /case "settings-approval-apply-saved": setApprovalPreference\(approvalPreferences\.savedMode\)/);
});

test('runtime settings provides access to the existing terminal and service controls', async () => {
  const p = await panel();
  const html = p.renderSettingsPane({...data,section:'runtime'});
  assert.match(html, /settings-open-runtime-status/);
  assert.match(html, /终端与服务/);
  const shell = readFileSync(new URL('../src/shells/unified/index.ts', import.meta.url), 'utf8');
  assert.match(shell, /case "settings-open-runtime-status": selectSingleChatTool\("runtime"\)/);
});
