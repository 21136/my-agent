import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {createRequire} from 'node:module';
import os from 'node:os'; import path from 'node:path'; import test from 'node:test';
const require=createRequire(import.meta.url),ts=require('typescript');
const pwPath=process.env.CODEX_PLAYWRIGHT_PATH || path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const edge=process.env.ISOLATED_TEST_BROWSER || (process.platform==='win32'?'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe':'');
const root=new URL('../src/shells/unified/',import.meta.url);const source=readFileSync(new URL('index.ts',root),'utf8');
const ast=ts.createSourceFile('index.ts',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TS);let template;const found={};
function visit(n){if(ts.isBinaryExpression(n)&&n.left.getText(ast)==='root.innerHTML'&&ts.isTemplateExpression(n.right)) template=n.right.getText(ast);if(ts.isFunctionDeclaration(n)&&n.name)found[n.name.text]=n.getText(ast);ts.forEachChild(n,visit)}visit(ast);
const names=['setMainFocus','selectSingleChatTool','selectRailTab','showNowChat','syncMainFocusView'];for(const n of names)assert.ok(found[n],n);assert.ok(template);
const layout=template.slice(1,-1).replace('${new URL("./assets/composer-companion.png", import.meta.url).href}','');
const files=['unified.css','neon-twin.css','sidebar-v2.css','settings-panel.css','music-panel.css','shell-dev-panel.css','current-work-overview.css','single-chat-tools.css'];
const css=files.map(f=>readFileSync(new URL(f,root),'utf8')).join('\n');
const topbar=ts.transpileModule(readFileSync(new URL('topbar.ts',root),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const functions=ts.transpileModule(names.map(n=>found[n]).join('\n'),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
const init=`const exports={}; const require=()=>({escapeHtml:x=>String(x)}); ${topbar}
const root=document.querySelector('#root'), shellEl=root.querySelector('.unified-shell'), chatEl=root.querySelector('#unified-chat'), documentEl=root.querySelector('#unified-document'), projectsEl=root.querySelector('#unified-projects'), settingsEl=root.querySelector('#unified-settings'), musicEl=root.querySelector('#unified-music'), shellDevEl=root.querySelector('#unified-shell-dev'), composerDock=root.querySelector('#unified-composer-dock'), composer=root.querySelector('#unified-composer'), statusEl=root.querySelector('#unified-status'), tokenBar=root.querySelector('#unified-token-bar'), toolRegion=root.querySelector('#unified-tool-region'), toolTitle=root.querySelector('#unified-tool-title'), runtimeTool=root.querySelector('#unified-runtime-tool');
let runtimeToolOpen=false, docLeavePrompt=false, shellDevLastOpenOk=false, allowLeave=true;
const runtimeBadges=root.querySelector('.sidebar-runtime-badges');runtimeTool.append(runtimeBadges);runtimeBadges.querySelector('#sidebar-terminals').innerHTML='<button id="synthetic-terminal-action">合成终端动作</button>';runtimeBadges.querySelector('button').onclick=()=>window.terminalAction++;
const projectState={mainFocus:'chat',railTab:'now',docsChatSplit:true,projectId:'synthetic',docDirty:false};
const client={listProjects(){throw Error('project listing prohibited')}};
function requestLeaveDocument(){return allowLeave} function mainFocusForRail(t){return ({now:'chat',docs:'document',settings:'settings',music:'music','shell-dev':'shell-dev',projects:'projects'})[t]};
function clearExpandedSurface(){} function syncComposerMetaVisibility(){} function renderDocumentPane(){documentEl.innerHTML='<button id="synthetic-doc-action">合成文档动作</button>';documentEl.querySelector('button').onclick=()=>window.docAction++} function openDocumentReader(){setMainFocus('document')} function renderProjectsPane(){} function renderSettingsStage(){settingsEl.textContent='synthetic-settings'} function renderMusicStage(){musicEl.textContent='synthetic-music'} function renderShellDevStage(){shellDevEl.textContent='synthetic-shell-dev'} function syncMusicViewBounds(){} function syncShellDevViewBounds(){} function updateWorkbenchEmpty(){} function renderSidebar(){} function refreshSidebarSessions(){} function refreshTerminals(){window.refreshes++} function refreshServices(){window.refreshes++} function setStatus(s){statusEl.textContent=s}
${functions}
root.querySelector('#unified-tool-close').addEventListener('click',()=>{if(runtimeToolOpen){runtimeToolOpen=false;syncMainFocusView()}else showNowChat()});
window.docAction=0;window.terminalAction=0;window.refreshes=0;window.setLeave=v=>allowLeave=v; window.selectSingleChatTool=selectSingleChatTool;
exports.renderTopbarV2(root.querySelector('#unified-topbar'),{proposals:[],projectLabel:'合成绑定项目',contextLabel:'',headerCtas:[],memoryCount:0,pendingMemoryCount:0,sessionCount:0},{onSelectTool:selectSingleChatTool});
syncMainFocusView();`;
const html=`<!doctype html><html data-theme="dark"><head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'"><style>html,body,#root{height:100%;margin:0}${css}</style></head><body><div id="root">${layout}</div><script>${init}</script></body></html>`;
test('actual shell template uses full-page tools; runtime keeps chat split; return restores chat', {skip:!existsSync(pwPath)||!existsSync(edge)},async()=>{
 const {chromium}=require(pwPath),browser=await chromium.launch({headless:true,executablePath:edge,args:['--disable-background-networking','--no-first-run']});try {for(const [width,height] of [[1200,900],[360,740]]){const c=await browser.newContext({viewport:{width,height}});let requests=0,errors=[];await c.route('**/*',r=>{requests++;return r.abort()});const page=await c.newPage();page.on('pageerror',e=>errors.push(e.message));await page.setContent(html);await page.locator('#unified-chat').evaluate(e=>e.textContent='合成聊天');
 assert.equal(await page.locator('#unified-sidebar').isVisible(),false);for(const id of ['#unified-new-chat','#unified-new-thread','#unified-new-project','#unified-open-sessions'])assert.equal(await page.locator(id).count(),0);
 for(const id of ['docs','settings','music','shell-dev','runtime']) {
   if(id==='runtime') await page.evaluate(()=>selectSingleChatTool('runtime'));
   else await page.locator('#unified-tool-'+id).click();
   assert.equal(await page.locator('#unified-tool-region').isVisible(),true,id);
   const fullPage = id !== 'runtime';
   assert.equal(await page.locator('.unified-shell').evaluate(e=>e.classList.contains('is-tool-fullpage')), fullPage, id+' fullpage class');
   assert.equal(await page.locator('#unified-chat').isVisible(), !fullPage, id+' chat visibility');
   if(fullPage){
     const chatBox=await page.locator('#unified-chat').boundingBox();
     const toolBox=await page.locator('#unified-tool-region').boundingBox();
     assert.equal(chatBox, null, id+' chat not laid out');
     assert.ok(toolBox && toolBox.width > width*0.6, id+' tool takes stage width');
     assert.equal(await page.locator('#unified-composer-dock').isVisible(), false, id+' composer hidden');
   } else {
     const box=await page.locator('#unified-tool-region').boundingBox(),input=await page.locator('#unified-input').boundingBox();
     assert.ok(box&&input&&box.y+box.height<=input.y+1,`${id} tool overlaps composer`);
     await page.locator('#unified-input').fill('合成消息');
     await page.locator('#synthetic-terminal-action').click();
     assert.equal(await page.evaluate(()=>terminalAction),1);
   }
   if(id==='docs'){
     await page.locator('#synthetic-doc-action').click();
     assert.equal(await page.evaluate(()=>docAction),1);
     await page.evaluate(()=>setLeave(false));
     await page.locator('#unified-tool-close').click();
     assert.equal(await page.locator('#unified-tool-region').isVisible(),true);
     await page.evaluate(()=>setLeave(true));
   }
   await page.locator('#unified-tool-close').click();
   assert.equal(await page.locator('#unified-tool-region').isVisible(),false,id);
   assert.equal(await page.locator('#unified-chat').isVisible(),true,id+' chat restored');
 }
 assert.equal(await page.evaluate(()=>refreshes),2);
 assert.equal(await page.locator('.unified-shell').getAttribute('data-main-focus'),'chat');
 assert.equal(await page.locator('.unified-shell').getAttribute('data-rail'),'now');
 assert.deepEqual(errors,[]);
 assert.equal(requests,0);
 console.log(`isolated shell ${width}x${height}: full-page tools, runtime split, return and document leave guard verified; zero network requests`);
 await c.close();}}finally{await browser.close()}
});
