import { createHash, randomUUID } from "node:crypto";
import { existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, realpathSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const scriptDir=path.dirname(fileURLToPath(import.meta.url));
const sandboxRoot=path.resolve(scriptDir,"../..");
const artifactsRoot=path.join(sandboxRoot,"artifacts");
const runBase=path.join(artifactsRoot,"ordinary-chat-h1");
const deny=message=>{throw Object.assign(new Error(message),{code:"permission_denied"});};
function realDirectory(target,label){const stat=lstatSync(target);if(!stat.isDirectory()||stat.isSymbolicLink()||!samePath(realpathSync.native(target),target))deny(`${label} must be a real directory`);return target;}
function samePath(a,b){return process.platform==="win32"?path.resolve(a).toLowerCase()===path.resolve(b).toLowerCase():path.resolve(a)===path.resolve(b);}
function inside(root,target){const rel=path.relative(root,target);return rel!==""&&rel!==".."&&!rel.startsWith(`..${path.sep}`)&&!path.isAbsolute(rel);}
function newMarkedRun(){
 const artifacts=realDirectory(artifactsRoot,"sandbox artifacts root");
 if(!inside(realpathSync.native(sandboxRoot),artifacts))deny("sandbox artifacts escaped the sandbox");
 if(existsSync(runBase))realDirectory(runBase,"ordinary chat H1 run root");else mkdirSync(runBase,{recursive:false});
 realDirectory(runBase,"ordinary chat H1 run root");
 const runRoot=path.join(runBase,`run-${randomUUID()}`);mkdirSync(runRoot,{recursive:false});
 writeFileSync(path.join(runRoot,".evolution-test-run"),"evolution-a\n",{flag:"wx"});
 writeFileSync(path.join(runRoot,".ordinary-chat-h1.json"),JSON.stringify({format:1,run_id:path.basename(runRoot),fixture:"ordinary-chat-h1-v1",created_at:new Date().toISOString()})+"\n",{flag:"wx"});
 return runRoot;
}
export function assertExistingH1UserDirectories(directories, profileRoot) {
 if (!Array.isArray(directories) || directories.length === 0 || directories.some(directory => typeof directory !== "string" || !path.isAbsolute(directory)) || typeof profileRoot !== "string" || !path.isAbsolute(profileRoot)) deny("isolated H1 profile directories and root must be absolute paths");
 const isolatedRoot = realDirectory(path.resolve(profileRoot), "isolated H1 profile root");
 for (const directory of new Set(directories)) {
  const isolatedDirectory = realDirectory(path.resolve(directory), "isolated H1 profile directory");
  if (!inside(isolatedRoot, isolatedDirectory)) deny("isolated H1 profile directory escaped the isolated H1 profile");
 }
 return directories;
}
export function requireOrdinaryChatH1OptIn(env=process.env){if(env.REAL_ELECTRON_CHAT_H1_OPT_IN!=="1")deny("set REAL_ELECTRON_CHAT_H1_OPT_IN=1 to launch the synthetic ordinary-chat H1 Electron fixture");}
export function requireOrdinaryChatH1GuiOptIn(env=process.env){
 if(env.REAL_ELECTRON_CHAT_H1_OPT_IN!=="1")deny("set REAL_ELECTRON_CHAT_H1_OPT_IN=1 to launch the synthetic H1 fixture");
 if(env.REAL_ELECTRON_CHAT_H1_GUI_OPT_IN!=="1")deny("set REAL_ELECTRON_CHAT_H1_GUI_OPT_IN=1 only after the main-agent GUI review");
}
export function assertLoopbackUrl(value,protocol){
 let url;try{url=new URL(value);}catch{deny("CDP/renderer endpoint must be a valid loopback URL");}
 const ip=url.hostname.replace(/^\[|\]$/g,"");
 if((protocol&&url.protocol!==protocol)||!(["127.0.0.1","localhost","::1"].includes(ip))||url.username||url.password||url.hash)deny("only credential-free loopback CDP/renderer endpoints are allowed");
 return url.href;
}
export function assertCurrentSessionOverviewRoute(agentSource,serverSource){
 if(typeof agentSource!=="string"||typeof serverSource!=="string")deny("sealed app route source is unavailable");
 const start=agentSource.indexOf("def _is_explicit_current_work_overview(");
 const route=agentSource.indexOf("if self.enable_work_record_recall:",start);
 const routeEnd=agentSource.indexOf("controller = self.work_record_recording_controller",route);
 const body=route>=0&&routeEnd>route?agentSource.slice(route,routeEnd):"";
 const recognizerEnd=agentSource.indexOf("\ndef ",start+8);const recognizer=start>=0?agentSource.slice(start,recognizerEnd>start?recognizerEnd:undefined):"";
 if(start<0||!recognizer.includes("当前会话的工作概况")||!recognizer.includes("return any(normalized == prefix + marker + suffix")||!body.includes("return self._run_current_work_overview(user_text)"))deny("sealed app current-session overview recall route is not enabled");
 if(!body.includes('explicit_session = "当前会话" in user_text or "本会话" in user_text')||!body.includes("if len(mapping) > 1:")||!body.includes("if not explicit_session:"))deny("sealed app explicit-session guard is missing");
 const factoryStart=serverSource.indexOf("def _desktop_work_record_controller_factory(");
 const factoryEnd=serverSource.indexOf("\ndef _build_repl(",factoryStart);
 const factory=factoryStart>=0&&factoryEnd>factoryStart?serverSource.slice(factoryStart,factoryEnd):"";
 const replStart=serverSource.indexOf("def _build_repl(",factoryStart);const replEnd=serverSource.indexOf("\ndef ",replStart+8);const repl=replStart>=0?serverSource.slice(replStart,replEnd>replStart?replEnd:undefined):"";
 if(!factory.includes("return WorkRecordControllerBindings(recall=controller)")||!repl.includes("if work_record_controller_factory is None:")||!repl.includes("_desktop_work_record_controller_factory(paths, bridge)"))deny("server default work-record recall factory is not bound");
 return {query:"当前会话的工作概况",recall_enabled:true,explicit_session_guard:true,server_default_recall_factory:true};
}
function decodedFrame(frame){
 if(typeof frame==="string"){try{return {payload:JSON.parse(frame),url:null,sequence:null};}catch{return {payload:null,url:null,sequence:null};}}
 if(frame&&typeof frame==="object"){let payload=frame.payload;if(typeof payload==="string"){try{payload=JSON.parse(payload);}catch{payload=null;}}return {payload,url:frame.url??null,websocketRequestId:frame.websocketRequestId??null,sequence:Number.isSafeInteger(frame.sequence)?frame.sequence:null};}
 return {payload:null,url:null,sequence:null};
}export async function waitForH1GuiTurnEnd(getFrames, { query, timeoutMs = 5_000, pollIntervalMs = 25 } = {}) {
 if (typeof getFrames !== "function" || typeof query !== "string" || !Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 30_000 || !Number.isSafeInteger(pollIntervalMs) || pollIntervalMs < 1 || pollIntervalMs > 1_000) deny("bounded H1 turn-end wait inputs are invalid");
 const deadline = Date.now() + timeoutMs;
 for (;;) {
  const frames = await getFrames();
  if (!frames || !Array.isArray(frames.sentFrames) || !Array.isArray(frames.receivedFrames)) deny("bounded H1 turn-end frame snapshot is invalid");
  const sent = frames.sentFrames.map(decodedFrame).find(item => item.payload?.type === "user.message" && item.payload.text === query && typeof item.websocketRequestId === "string" && Number.isSafeInteger(item.sequence));
  if (!sent) return frames;
  const ended = frames.receivedFrames.map(decodedFrame).some(item => item.payload?.type === "turn.end" && item.websocketRequestId === sent.websocketRequestId && Number.isSafeInteger(item.sequence) && item.sequence > sent.sequence);
  if (ended) return frames;
  if (Date.now() >= deadline) deny("Python turn.end did not confirm deterministic work_record_overview route");
  await new Promise(resolve => setTimeout(resolve, pollIntervalMs));
 }
}
export function assertGuiExchangeEvidence({query,sentFrames,receivedFrames,renderedUserText,renderedAssistantText,sessionId,persistedMessages}) {
 if(query!=="当前会话的工作概况"||!Array.isArray(sentFrames)||!Array.isArray(receivedFrames))deny("GUI exchange evidence inputs are malformed");
 const sentEvents=sentFrames.map(decodedFrame).filter(item=>item.payload?.type==="user.message");
 if(sentEvents.length!==1||sentEvents[0].payload.text!==query||typeof sentEvents[0].payload.request_id!=="string"||!/^[0-9a-f-]{36}$/i.test(sentEvents[0].payload.request_id))deny("renderer WebSocket did not send exactly one identified user.message composer query");
 const networkIds=new Set([...sentFrames,...receivedFrames].map(frame=>frame?.websocketRequestId).filter(value=>typeof value==="string"));
 if(networkIds.size!==1)deny("user.message and backend reply were not observed on one Electron WebSocket request");
 const all=[...sentFrames,...receivedFrames].map(decodedFrame);const sessionEvents=all.filter(item=>item.payload?.type==="session.banner"&&typeof item.payload.session_id==="string");
 const session=sessionEvents.find(item=>item.payload.session_id===sessionId);
 if(!session||typeof sessionId!=="string"||!/^[A-Za-z0-9._-]{1,128}$/.test(sessionId)||sessionId==="."||sessionId==="..")deny("user.message WebSocket session identity is missing or mismatched");
 const received=receivedFrames.map(decodedFrame);
 const answer=received.find(item=>item.payload?.type==="assistant.done"&&typeof item.payload.text==="string"&&item.payload.text.includes("当前会话侧车没有已记录事项"));
 if(!answer)deny("Python assistant.done overview response was not observed");
 const ended=received.find(item=>item.payload?.type==="turn.end"&&item.payload.ok===true&&item.payload.finish_reason==="work_record_overview");
 if(!ended)deny("Python turn.end did not confirm deterministic work_record_overview route");
 const entries=Array.isArray(persistedMessages)?persistedMessages:[];let userIndex=-1;
 for(let i=entries.length-1;i>=0;i--)if(entries[i]?.role==="user"&&entries[i]?.content===query){userIndex=i;break;}
 if(userIndex<0||entries[userIndex+1]?.role!=="assistant"||entries[userIndex+1]?.content!==answer.payload.text)deny("same-session persisted messages do not contain the exact adjacent user/reply pair");
 if(typeof renderedUserText!=="string"||!renderedUserText.includes(query)||typeof renderedAssistantText!=="string"||!renderedAssistantText.includes("当前会话侧车没有已记录事项")||!renderedAssistantText.includes("未修改工作记录数据"))deny("actual Electron workbench DOM does not render the backend user and assistant turns");
 return {finish_reason:ended.payload.finish_reason,request_id:sentEvents[0].payload.request_id,session_id:sessionId,websocket_request_id:[...networkIds][0],user_message_observed:true,assistant_done_observed:true,persisted_same_session_pair:true,rendered_user_turn:true,rendered_assistant_turn:true,request_reply_correlation:"single user.message on the same session-bound WebSocket plus exact adjacent persisted session pair; reply protocol does not echo request_id",reply_excerpt:answer.payload.text.slice(0,1000)};
}

export function buildH1HostBaseEnvironment(source = process.env) {
  const env = {};
  for (const key of ["SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA"]) if (source?.[key]) env[key] = source[key];
  env.PYTHONDONTWRITEBYTECODE = "1";
  return env;
}

export function buildIsolatedGuiEnvironment(env, roots) {
  const allowed = new Set(["SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE", "MY_AGENT_ROOT"]);
  for (const key of Object.keys(env ?? {})) if (!allowed.has(key)) deny("H1 fixture launcher environment contains a non-allowlisted key");
  for (const key of ["profile", "runRoot", "agentRoot", "userProfile", "localAppData", "appData", "home", "xdgConfigHome", "xdgDataHome", "xdgCacheHome", "temp"]) if (!path.isAbsolute(roots?.[key] ?? "")) deny("isolated credential/environment root is invalid");
  const runRoot = path.resolve(roots.runRoot), profileRoot = path.resolve(roots.profile), agentRoot = path.resolve(roots.agentRoot);
  if (!samePath(agentRoot, path.join(runRoot, "host", "app")) || !inside(runRoot, agentRoot) || !samePath(profileRoot, path.join(runRoot, "host", "profile")) || !inside(runRoot, profileRoot)) deny("H1 Python/app profile roots are not this sealed run");
  const expectedUserEnv = { USERPROFILE: roots.userProfile, APPDATA: roots.appData, LOCALAPPDATA: roots.localAppData, HOME: roots.home, XDG_CONFIG_HOME: roots.xdgConfigHome, XDG_DATA_HOME: roots.xdgDataHome, XDG_CACHE_HOME: roots.xdgCacheHome, TEMP: roots.temp, TMP: roots.temp, MY_AGENT_ROOT: agentRoot, PYTHONDONTWRITEBYTECODE: "1", PYTHONNOUSERSITE: "1" };
  for (const key of ["userProfile","localAppData","appData","home","xdgConfigHome","xdgDataHome","xdgCacheHome","temp"]) if (!inside(profileRoot, path.resolve(roots[key]))) deny("isolated H1 user directory escaped its profile");
  if (env?.MY_AGENT_ROOT && !samePath(env.MY_AGENT_ROOT, agentRoot)) deny("ambient MY_AGENT_ROOT conflicts with the sealed H1 app root");
  if (env?.PYTHONNOUSERSITE && env.PYTHONNOUSERSITE !== "1") deny("H1 Python user-site access must remain disabled");
  if (env?.PYTHONDONTWRITEBYTECODE && env.PYTHONDONTWRITEBYTECODE !== "1") deny("H1 Python bytecode writes must remain disabled");
  return { ...Object.fromEntries(["SYSTEMROOT", "WINDIR", "PATH"].filter(key => env?.[key]).map(key => [key, env[key]])), ...expectedUserEnv };
}

export function verifyOrdinaryChatH1AgentPaths(api, verified) {
  if (typeof api?.verifyH1AgentPaths !== "function") deny("Raman H1 AgentPaths preflight interface is unavailable; refusing launch");
  const input = { sandboxRoot: verified?.sandbox_root, runRoot: verified?.run_root, appRoot: verified?.app_root, profileRoot: verified?.profile_root, pythonExecutable: verified?.profile?.trusted_python_executable };
  if (Object.values(input).some(value => typeof value !== "string" || !path.isAbsolute(value))) deny("verified H1 AgentPaths input is incomplete");
  const actual = api.verifyH1AgentPaths(input);
  if (actual && typeof actual.then === "function") deny("Python AgentPaths preflight must complete synchronously before launch");
  const expected = { agent_root: input.appRoot, data: path.join(input.appRoot, "data"), workspace: path.join(input.appRoot, "workspace"), evolve: path.join(input.appRoot, "evolve") };
  if (!actual || Object.keys(actual).length !== Object.keys(expected).length || Object.keys(expected).some(key => typeof actual[key] !== "string" || !samePath(actual[key], expected[key]))) deny("Python AgentPaths did not resolve exclusively to the sealed H1 app root");
  return { status: "verified", agent_paths_discovery: "sealed_app_root", python_executable_verified: true, my_agent_root: "sealed_app_root", sidecar_cwd: "sealed_app_root", checked_roots: ["agent_root", "data", "workspace", "evolve"] };
}

export function assertEmptyH1Data(appRoot, profileRoot) {
  const data = path.join(appRoot, 'data'), sessions = path.join(data, 'sessions');
  const expectedProfileEntries = ['appdata', 'electron-sessionData', 'electron-userData', 'home', 'tmp', 'userprofile', 'xdg-cache', 'xdg-config', 'xdg-data'].sort();
  for (const dir of [data, sessions, profileRoot]) { const stat = lstatSync(dir); if (!stat.isDirectory() || stat.isSymbolicLink() || !samePath(realpathSync.native(dir), dir)) deny('H1 isolated data/profile root is not a canonical directory'); }
  if (JSON.stringify(readdirSync(data).sort()) !== JSON.stringify(['sessions']) || readdirSync(sessions).length || JSON.stringify(readdirSync(profileRoot).sort()) !== JSON.stringify(expectedProfileEntries)) deny('H1 isolated session and credential/profile roots must be empty before launch');
  for (const name of expectedProfileEntries) { const dir = path.join(profileRoot, name); const stat = lstatSync(dir); if (!stat.isDirectory() || stat.isSymbolicLink() || !samePath(realpathSync.native(dir), dir)) deny('H1 profile contains a link or reparse point'); }
  for (const dir of [path.join(profileRoot, 'appdata'), path.join(profileRoot, 'appdata', 'Local'), path.join(profileRoot, 'appdata', 'Roaming')]) { const stat = lstatSync(dir); if (!stat.isDirectory() || stat.isSymbolicLink() || !samePath(realpathSync.native(dir), dir)) deny('H1 appdata contains a link or reparse point'); }
  if (JSON.stringify(readdirSync(path.join(profileRoot, 'appdata')).sort()) !== JSON.stringify(['Local', 'Roaming'])) deny('H1 appdata root has unexpected entries');
  for (const dir of [path.join(profileRoot, 'appdata', 'Local'), path.join(profileRoot, 'appdata', 'Roaming'), ...expectedProfileEntries.filter(name => name !== 'appdata').map(name => path.join(profileRoot, name))]) if (readdirSync(dir).length) deny('H1 Electron and user-data roots must be empty before launch');
  return true;
}

export function assertSealedAgentRootMarker(appRootValue) {
  const appRoot = realDirectory(path.resolve(appRootValue), 'sealed H1 app root');
  const evolveRoot = realDirectory(path.join(appRoot, 'evolve'), 'sealed H1 AgentPaths evolve root');
  const markerPaths = ['_index.core.toml', '_index.toml'].map(name => path.join(evolveRoot, name));
  for (const marker of markerPaths) {
    if (!existsSync(marker)) deny('sealed app AgentPaths root app/evolve requires both index markers');
    const stat = lstatSync(marker);
    if (!stat.isFile() || stat.isSymbolicLink() || !samePath(realpathSync.native(marker), marker)) deny('sealed app AgentPaths index marker must be a canonical regular file');
  }
  return { core_index: markerPaths[0], legacy_index: markerPaths[1] };
}

export function assertRunLocalRenderer(manifest, rendererRoot, runRoot) {
  if (manifest?.build_provenance !== 'run-local-vite-renderer-and-esbuild-main-preload; renderer-source-and-sealed-output-hashes-verified'
    || !path.isAbsolute(rendererRoot) || !inside(path.resolve(runRoot), path.resolve(rendererRoot))
    || !path.basename(path.resolve(rendererRoot)).startsWith('dist')) {
    deny('sealed renderer is not attested as a run-local source-verified build');
  }
  return true;
}

async function connectCdp(wsUrl) {
  if (typeof WebSocket !== 'function') deny('Node built-in WebSocket is unavailable; GUI automation refused');
  const socket = new WebSocket(assertLoopbackUrl(wsUrl, 'ws:'));
  const events = [], commandHistory = [], pending = new Map(); let nextId = 0, closed = false, connectionError = null;
  let openResolve, openReject; const opened = new Promise((resolve, reject) => { openResolve = resolve; openReject = reject; });
  const failPending = error => { for (const item of pending.values()) { clearTimeout(item.timer); commandHistory.push({ method: item.method, status: error.code ?? 'cdp_connection_closed', duration_ms: Date.now() - item.startedAt }); item.reject(error); } pending.clear(); if (commandHistory.length > 64) commandHistory.splice(0, commandHistory.length - 64); };
  socket.addEventListener('open', () => openResolve());
  socket.addEventListener('error', () => { connectionError = Object.assign(new Error('Electron CDP WebSocket error'), { code: 'cdp_connection_error' }); openReject(connectionError); failPending(connectionError); });
  socket.addEventListener('close', () => { closed = true; connectionError ??= Object.assign(new Error('Electron CDP WebSocket closed'), { code: 'cdp_connection_closed' }); openReject(connectionError); failPending(connectionError); });
  socket.addEventListener('message', event => {
    let message; try { message = JSON.parse(String(event.data)); } catch { return; }
    if (Number.isInteger(message.id)) {
      const item = pending.get(message.id); if (!item) return;
      pending.delete(message.id); clearTimeout(item.timer); item.finishAt = Date.now();
      if (message.error) {
        const raw = String(message.error.message ?? '');
        const code = /execution context.*destroyed|cannot find context/i.test(raw) ? 'cdp_context_transition' : 'cdp_command_error';
        commandHistory.push({ method: item.method, status: code, duration_ms: item.finishAt - item.startedAt });
        item.reject(Object.assign(new Error('CDP command failed: ' + item.method), { code, method: item.method }));
      } else {
        commandHistory.push({ method: item.method, status: 'ok', duration_ms: item.finishAt - item.startedAt }); item.resolve(message.result);
      }
      if (commandHistory.length > 64) commandHistory.shift();
    } else if (typeof message.method === 'string') events.push(message);
  });
  let openTimer;
  await Promise.race([opened, new Promise((_, reject) => { openTimer = setTimeout(() => reject(Object.assign(new Error('Electron CDP open timed out'), { code: 'cdp_open_timeout' })), 10_000); })]).finally(() => clearTimeout(openTimer));
  return {
    events,
    command(method, params = {}, timeoutMs = 10_000) {
      if (closed) return Promise.reject(connectionError ?? Object.assign(new Error('Electron CDP is closed'), { code: 'cdp_connection_closed' }));
      const id = ++nextId, startedAt = Date.now();
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          pending.delete(id); commandHistory.push({ method, status: 'cdp_command_timeout', duration_ms: Date.now() - startedAt });
          if (commandHistory.length > 64) commandHistory.shift();
          reject(Object.assign(new Error('CDP command timed out: ' + method), { code: 'cdp_command_timeout', method }));
        }, timeoutMs);
        pending.set(id, { method, startedAt, timer, resolve, reject });
        try { socket.send(JSON.stringify({ id, method, params })); }
        catch { clearTimeout(timer); pending.delete(id); commandHistory.push({ method, status: 'cdp_command_send_failed', duration_ms: Date.now() - startedAt }); reject(Object.assign(new Error('CDP command send failed: ' + method), { code: 'cdp_command_send_failed', method })); }
      });
    },
    diagnostics() {
      return { connection: closed ? 'closed' : connectionError ? 'error' : 'open', ...summarizeH1CdpEvents(events), recent_commands: commandHistory.slice(-32), page_body_captured: false };
    },
    close() { try { socket.close(); } catch {} },
  };
}

export function h1ComposerSendKeyboardEvents() {
  return [
    { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, modifiers: 8 },
    { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, modifiers: 8 },
  ];
}

function safeCdpUrlMetadata(value) {
  try { const url = new URL(String(value)); return { origin: url.origin.slice(0, 200), port: url.port ? Number(url.port) : null, path: url.pathname.slice(0, 200) }; }
  catch { return { origin: null, port: null, path: null }; }
}

function networkFailureCategory(event) {
  const detail = ((event?.params?.errorText ?? '') + ' ' + (event?.params?.blockedReason ?? '')).toLowerCase();
  if (detail.includes('connection_refused')) return 'connection_refused';
  if (detail.includes('aborted')) return 'aborted';
  if (detail.includes('timed_out') || detail.includes('timeout')) return 'timeout';
  if (detail.includes('name_not_resolved')) return 'name_resolution_failed';
  if (detail.includes('csp')) return 'blocked_by_csp';
  if (detail.includes('client')) return 'blocked_by_client';
  return 'network_failure';
}
function safeExceptionClass(value) {
  return new Set(['Error','TypeError','ReferenceError','SyntaxError','RangeError','URIError','EvalError']).has(value) ? value : 'Other';
}
function safeScriptMetadata(raw) {
  const meta = safeCdpUrlMetadata(raw);
  return { origin: meta.origin, port: meta.port, path: meta.path };
}
const H1_WS_ERROR_CODE_CLASSES = new Map([
  ['managed_readonly_unavailable', 'managed_readonly_unavailable'],
  ['managed_readonly_rejected', 'managed_readonly_rejected'],
  ['managed_application_rejected', 'managed_application_rejected'],
]);
const H1_WS_ERROR_MESSAGE_CLASSES = new Map([
  ['run_service tool not found', 'run_service_tool_not_found'],
]);
const H1_WS_ERROR_CLASSES = [
  'message_only',
  ...H1_WS_ERROR_CODE_CLASSES.values(),
  ...H1_WS_ERROR_MESSAGE_CLASSES.values(),
  'other',
];
function classifyH1WsError(payload) {
  const keys = Object.keys(payload).sort();
  if (keys.length === 2 && keys[0] === 'code' && keys[1] === 'type' && typeof payload.code === 'string') {
    return H1_WS_ERROR_CODE_CLASSES.get(payload.code) ?? 'other';
  }
  if (keys.length === 2 && keys[0] === 'message' && keys[1] === 'type' && typeof payload.message === 'string') {
    return H1_WS_ERROR_MESSAGE_CLASSES.get(payload.message) ?? 'message_only';
  }
  return 'other';
}

export function summarizeH1GuiFrames(events, query) {
  const counts = {}, relevant = [];
  const wsErrorClassCounts = Object.fromEntries(H1_WS_ERROR_CLASSES.map(name => [name, 0]));
  const queryHash = createHash('sha256').update(query).digest('hex');
  for (const event of Array.isArray(events) ? events : []) {
    if (event.method !== 'Network.webSocketFrameSent' && event.method !== 'Network.webSocketFrameReceived') continue;
    const payloadText = event.params?.response?.payloadData; if (typeof payloadText !== 'string') continue;
    let payload; try { payload = JSON.parse(payloadText); } catch { continue; }
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) continue;
    const type = typeof payload.type === 'string' ? payload.type.slice(0, 64) : 'unknown'; counts[type] = (counts[type] ?? 0) + 1;
    if (type === 'error') wsErrorClassCounts[classifyH1WsError(payload)] += 1;
    const base = { direction: event.method.endsWith('Sent') ? 'sent' : 'received', type, websocket_request_id_sha256: typeof event.params?.requestId === 'string' ? createHash('sha256').update(event.params.requestId).digest('hex') : null };
    if (type === 'user.message' && payload.text === query) relevant.push({ ...base, request_id_sha256: typeof payload.request_id === 'string' ? createHash('sha256').update(payload.request_id).digest('hex') : null, query_sha256: queryHash, exact_query: true });
    else if (type === 'assistant.done') relevant.push({ ...base, text_sha256: typeof payload.text === 'string' ? createHash('sha256').update(payload.text).digest('hex') : null, expected_empty_overview: typeof payload.text === 'string' && payload.text.includes('当前会话侧车没有已记录事项') });
    else if (type === 'turn.end') relevant.push({ ...base, ok: payload.ok === true, finish_reason: typeof payload.finish_reason === 'string' ? payload.finish_reason.slice(0, 64) : null });
    else if (type === 'session.banner') relevant.push({ ...base, session_id_sha256: typeof payload.session_id === 'string' ? createHash('sha256').update(payload.session_id).digest('hex') : null });
  }
  return { frame_type_counts: counts, ws_error_class_counts: wsErrorClassCounts, relevant_frames: relevant.slice(-24), query_sha256: queryHash, raw_payloads_written: false };
}

export function buildH1GuiRoundTripDiagnostic(input) {
  const boolFields = ['textarea_present','focused','input_value_matches_query','send_button_present','send_button_enabled','user_query_visible','assistant_reply_visible'];
  const inputState = input.inputState && typeof input.inputState === 'object' ? Object.fromEntries(boolFields.filter(key => Object.hasOwn(input.inputState, key)).map(key => [key, input.inputState[key] === true])) : null;
  const errorCodes = new Set(['h1_workbench_ready_timeout','h1_app_startup_failure','cdp_command_timeout','cdp_context_transition','cdp_connection_error','cdp_connection_closed','permission_denied']);
  return { format: 1, status: input.status === 'success' ? 'success' : 'failed', stage: typeof input.stage === 'string' ? input.stage.slice(0, 64) : 'unknown', renderer: { host: '127.0.0.1', port: Number.isInteger(input.rendererPort) ? input.rendererPort : null, path: '/' }, python_ws: { host: '127.0.0.1', port: Number.isInteger(input.pythonWsPort) ? input.pythonWsPort : null }, query_sha256: typeof input.query === 'string' ? createHash('sha256').update(input.query).digest('hex') : null, input_state: inputState, dom_state: input.domState && typeof input.domState === 'object' ? { user_query_visible: input.domState.user_query_visible === true, assistant_reply_visible: input.domState.assistant_reply_visible === true } : null, websocket: summarizeH1GuiFrames(input.events, input.query ?? ''), error_category: errorCodes.has(input.errorCode) ? input.errorCode : input.errorCode ? 'other_failure' : null, page_body_captured: false };
}

export function summarizeH1CdpEvents(events) {
  const eventCounts = {}, resources = new Map(), runtimeExceptions = [], consoleErrors = [], navigations = [], websocketEndpoints = [];
  for (const event of Array.isArray(events) ? events : []) {
    const method = typeof event?.method === 'string' ? event.method : 'unknown';
    eventCounts[method] = (eventCounts[method] ?? 0) + 1;
    const params = event?.params ?? {};
    if (method === 'Network.requestWillBeSent' && typeof params.requestId === 'string') {
      const prior = resources.get(params.requestId) ?? {};
      resources.set(params.requestId, { ...prior, url: safeScriptMetadata(params.request?.url), resource_type: typeof params.type === 'string' ? params.type.slice(0, 32) : 'unknown' });
    } else if (method === 'Network.responseReceived' && typeof params.requestId === 'string') {
      const prior = resources.get(params.requestId) ?? { url: safeScriptMetadata(params.response?.url), resource_type: typeof params.type === 'string' ? params.type.slice(0, 32) : 'unknown' };
      prior.http_status = Number.isInteger(params.response?.status) ? params.response.status : null; resources.set(params.requestId, prior);
    } else if (method === 'Network.loadingFinished' && typeof params.requestId === 'string') {
      const prior = resources.get(params.requestId) ?? {}; prior.completed = true;
      prior.encoded_bytes = Number.isFinite(params.encodedDataLength) ? Math.max(0, Math.floor(params.encodedDataLength)) : null; resources.set(params.requestId, prior);
    } else if (method === 'Network.loadingFailed' && typeof params.requestId === 'string') {
      const prior = resources.get(params.requestId) ?? {}; prior.failed = true; prior.failure_category = networkFailureCategory(event); prior.canceled = params.canceled === true; resources.set(params.requestId, prior);
    } else if (method === 'Runtime.exceptionThrown') {
      const detail = params.exceptionDetails ?? {};
      runtimeExceptions.push({ category: 'uncaught_exception', class: safeExceptionClass(detail.exception?.className), url: safeScriptMetadata(detail.url), line: Number.isInteger(detail.lineNumber) ? detail.lineNumber + 1 : null, column: Number.isInteger(detail.columnNumber) ? detail.columnNumber + 1 : null });
    } else if (method === 'Runtime.consoleAPICalled') {
      const frame = params.stackTrace?.callFrames?.[0] ?? {};
      const kind = params.type === 'error' ? 'console_error' : params.type === 'warning' ? 'console_warning' : 'console_other';
      const item = { category: kind, url: safeScriptMetadata(frame.url), line: Number.isInteger(frame.lineNumber) ? frame.lineNumber + 1 : null, column: Number.isInteger(frame.columnNumber) ? frame.columnNumber + 1 : null };
      (kind === 'console_error' ? consoleErrors : []).push(item);
    } else if (method === 'Page.frameNavigated' && params.frame && !params.frame.parentId) {
      navigations.push(safeScriptMetadata(params.frame.url));
    } else if (method === 'Network.webSocketCreated') websocketEndpoints.push(safeScriptMetadata(params.url));
  }
  return { event_counts: eventCounts, resources: [...resources.values()].slice(-64), failed_resources: [...resources.values()].filter(item => item.failed).slice(-32), runtime_exceptions: runtimeExceptions.slice(-24), console_errors: consoleErrors.slice(-24), main_frame_navigations: navigations.slice(-16), websocket_endpoints: websocketEndpoints.slice(-8), page_body_captured: false };
}

function transientCdpEvaluationError(error) {
  return error?.code === 'cdp_command_timeout' || error?.code === 'cdp_context_transition';
}

async function cdpValue(cdp, expression, timeoutMs = 10_000) {
  const result = await cdp.command('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, timeoutMs);
  if (result.exceptionDetails) throw Object.assign(new Error('Electron workbench DOM evaluation raised an exception'), { code: 'cdp_evaluation_exception', method: 'Runtime.evaluate' });
  return result.result?.value;
}

export async function waitForH1Workbench(cdp, rendererPort, options = {}) {
  if (!Number.isInteger(rendererPort) || rendererPort < 1 || rendererPort > 65_535) deny('sealed renderer port is invalid');
  const timeoutMs = options.timeoutMs ?? 90_000, pollMs = options.pollMs ?? 250, evaluateTimeoutMs = options.evaluateTimeoutMs ?? 1_500;
  const deadline = Date.now() + timeoutMs, samples = [], transientErrors = [];
  const expression = `(()=>{
    const app=document.querySelector('#app');
    const status=app?.querySelector(':scope > p');
    const statusText=status?.textContent||'';
    const startupFailure=statusText.startsWith('启动失败：');
    const lower=statusText.toLowerCase();
    const errorCategory=!startupFailure?null:lower.includes('myagentdesktop preload api missing')?'preload_bridge_missing':lower.includes('websocket')||lower.includes('sidecar')||lower.includes('failed to fetch')?'sidecar_connection_error':lower.includes('mountshell')?'shell_mount_error':lower.includes('#app missing')?'app_root_missing':'generic_startup_failure';
    const scripts=[...document.scripts].filter(s=>s.src).map(s=>{let u;try{u=new URL(s.src,location.href)}catch{return null}const entries=performance.getEntriesByName(u.href);const timing=entries.at(-1);return{type:String(s.type||'classic').slice(0,32),origin:u.origin,port:u.port?Number(u.port):null,path:u.pathname.slice(0,200),async:s.async===true,defer:s.defer===true,timing_present:!!timing,loaded:!!timing&&timing.responseEnd>0,response_end_ms:timing&&Number.isFinite(timing.responseEnd)?Math.round(timing.responseEnd):null}}).filter(Boolean);
    return{title:document.title,href:location.href,input:!!document.querySelector('#unified-input'),app_root:{present:!!app,child_count:app?.children.length||0,shell_root_present:!!app?.querySelector('#shell-root'),app_chrome_present:!!app?.querySelector('#app-chrome'),startup_failure:errorCategory!==null,error_category:errorCategory},scripts};
  })()`;
  const safeApp = value => value && typeof value === 'object' ? {
    present: value.present === true, child_count: Number.isSafeInteger(value.child_count) ? value.child_count : 0,
    shell_root_present: value.shell_root_present === true, app_chrome_present: value.app_chrome_present === true,
    startup_failure: value.startup_failure === true,
    error_category: new Set(['preload_bridge_missing','sidecar_connection_error','shell_mount_error','app_root_missing','generic_startup_failure']).has(value.error_category) ? value.error_category : null,
  } : { present: false, child_count: 0, shell_root_present: false, app_chrome_present: false, startup_failure: false, error_category: null };
  const safeScripts = values => (Array.isArray(values) ? values : []).slice(0, 16).map(item => ({
    type: typeof item?.type === 'string' ? item.type.slice(0, 32) : 'unknown',
    origin: typeof item?.origin === 'string' ? item.origin.slice(0, 200) : null,
    port: Number.isInteger(item?.port) ? item.port : null,
    path: typeof item?.path === 'string' ? item.path.slice(0, 200) : null,
    async: item?.async === true, defer: item?.defer === true, timing_present: item?.timing_present === true,
    loaded: item?.loaded === true, response_end_ms: Number.isFinite(item?.response_end_ms) ? item.response_end_ms : null,
  }));
  const safeSample = state => ({
    at: new Date().toISOString(), title: typeof state?.title === 'string' ? state.title.replace(/[\r\n\t]/g, ' ').slice(0, 120) : '',
    ...safeCdpUrlMetadata(state?.href), composer_present: state?.input === true,
    app_root: safeApp(state?.app_root), scripts: safeScripts(state?.scripts),
  });
  while (Date.now() < deadline) {
    let state = null;
    try { state = await cdpValue(cdp, expression, evaluateTimeoutMs); }
    catch (error) {
      if (!transientCdpEvaluationError(error)) throw error;
      transientErrors.push({ code: error.code, method: error.method ?? 'Runtime.evaluate' }); if (transientErrors.length > 16) transientErrors.shift();
    }
    if (state && typeof state === 'object') {
      const sample = safeSample(state); samples.push(sample); if (samples.length > 24) samples.shift();
      if (sample.app_root.startup_failure) {
        throw Object.assign(new Error('sealed renderer app root reported a startup failure'), { code: 'h1_app_startup_failure', diagnostics: { app_root: sample.app_root, scripts: sample.scripts, samples, transient_timeouts: transientErrors.length, transient_errors: transientErrors, page_body_captured: false } });
      }
      if (state.title === 'my-agent' && state.href === 'http://127.0.0.1:' + rendererPort + '/' && state.input === true) {
        return { state: { title: state.title, href: state.href, input: true, app_root: sample.app_root, scripts: sample.scripts }, app_root: sample.app_root, scripts: sample.scripts, samples, transient_timeouts: transientErrors.length, transient_errors: transientErrors };
      }
    }
    await new Promise(resolve => setTimeout(resolve, pollMs));
  }
  const latest = samples.at(-1) ?? { app_root: safeApp(null), scripts: [] };
  throw Object.assign(new Error('sealed workbench did not expose the composer before deadline'), { code: 'h1_workbench_ready_timeout', diagnostics: { app_root: latest.app_root, scripts: latest.scripts, samples, transient_timeouts: transientErrors.length, transient_errors: transientErrors, page_body_captured: false } });
}

export function selectSealedRendererTarget(targets, { cdpPort, rendererPort }) {
  if (!Number.isInteger(cdpPort) || cdpPort < 1 || cdpPort > 65_535 || !Number.isInteger(rendererPort) || rendererPort < 1 || rendererPort > 65_535) deny('CDP target ports are invalid');
  const diagnostics = []; const acceptedTargets = [];
  for (const target of Array.isArray(targets) ? targets : []) {
    const title = typeof target?.title === 'string' ? target.title.replace(/[\r\n\t]/g, ' ').slice(0, 120) : '';
    const diagnostic = { type: typeof target?.type === 'string' ? target.type.slice(0, 32) : 'unknown', origin: null, port: null, path: null, title, reason: null };
    const reject = reason => { diagnostic.reason = reason; diagnostics.push(diagnostic); };
    if (target?.type !== 'page') { reject('not_page_target'); continue; }
    if (typeof target.url !== 'string') { reject('missing_page_url'); continue; }
    let pageUrl; try { pageUrl = new URL(target.url); } catch { reject('invalid_page_url'); continue; }
    diagnostic.origin = pageUrl.origin.slice(0, 200); diagnostic.port = pageUrl.port ? Number(pageUrl.port) : null; diagnostic.path = pageUrl.pathname.slice(0, 200);
    if (pageUrl.protocol !== 'http:') { reject('non_http_renderer_url'); continue; }
    if (pageUrl.hostname !== '127.0.0.1') { reject('non_loopback_renderer_origin'); continue; }
    if (pageUrl.username || pageUrl.password || pageUrl.search || pageUrl.hash) { reject('renderer_url_has_disallowed_components'); continue; }
    if (Number(pageUrl.port) !== rendererPort) { reject('renderer_port_mismatch'); continue; }
    if (pageUrl.pathname !== '/') { reject('renderer_path_mismatch'); continue; }
    if (title && title !== 'my-agent') { reject('unexpected_page_title'); continue; }
    let debuggerUrl; try { debuggerUrl = new URL(target.webSocketDebuggerUrl); } catch { reject('invalid_debugger_endpoint'); continue; }
    if (debuggerUrl.protocol !== 'ws:' || debuggerUrl.hostname !== '127.0.0.1' || Number(debuggerUrl.port) !== cdpPort || !debuggerUrl.pathname.startsWith('/devtools/page/') || debuggerUrl.search || debuggerUrl.hash || debuggerUrl.username || debuggerUrl.password) { reject('debugger_endpoint_not_bound_to_this_electron'); continue; }
    diagnostic.reason = 'accepted_sealed_renderer'; diagnostics.push(diagnostic); acceptedTargets.push(target);
  }
  if (acceptedTargets.length > 1) { for (const item of diagnostics) if (item.reason === 'accepted_sealed_renderer') item.reason = 'ambiguous_sealed_renderer_candidate'; return { target: null, diagnostics }; }
  return { target: acceptedTargets[0] ?? null, diagnostics };
}

async function discoverElectronPage(cdpPort, rendererPort, runRoot) {
  const endpoint = assertLoopbackUrl('http://127.0.0.1:' + cdpPort + '/json/list', 'http:');
  const deadline = Date.now() + 30_000; const history = new Map(); const diagnosticsPath = path.join(runRoot, 'ordinary-chat-h1-cdp-target-diagnostics.json');
  const remember = items => {
    const now = new Date().toISOString();
    for (const item of items) {
      const key = JSON.stringify([item.type, item.origin, item.port, item.path, item.title]); const current = history.get(key);
      history.set(key, { ...item, first_seen: current?.first_seen ?? now, last_seen: now });
    }
    while (history.size > 48) history.delete(history.keys().next().value);
  };
  const save = status => {
    const value = { format: 1, status, cdp_host: '127.0.0.1', cdp_port: cdpPort, expected_renderer_origin: 'http://127.0.0.1:' + rendererPort, expected_renderer_path: '/', page_body_captured: false, targets: [...history.values()] };
    writeFileSync(diagnosticsPath, JSON.stringify(value, null, 2) + '\n', { flag: 'wx' });
    return path.basename(diagnosticsPath);
  };
  while (Date.now() < deadline) {
    try {
      const response = await fetch(endpoint, { signal: AbortSignal.timeout(1200) });
      if (response.ok) {
        const targets = await response.json(); const selected = selectSealedRendererTarget(targets, { cdpPort, rendererPort }); remember(selected.diagnostics);
        if (selected.target) return { page: selected.target, diagnostics: save('sealed_renderer_selected') };
      }
    } catch (error) { if (error?.code === 'permission_denied') throw error; }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  save('sealed_renderer_not_found');
  deny('no CDP page matched this Electron process and the sealed renderer origin/port/path; metadata diagnostics were retained');
}

function saveCdpRuntimeDiagnostics(runRoot, status, page, cdp, stage, observation, error) {
  const target = page?.url ? { type: page.type, title: typeof page.title === 'string' ? page.title.replace(/[\r\n\t]/g, ' ').slice(0, 120) : '', ...safeCdpUrlMetadata(page.url) } : null;
  const value = { format: 1, status, stage, target, observation: observation ?? null, cdp: cdp?.diagnostics?.() ?? { connection: 'not_opened', event_counts: {}, main_frame_navigations: [], websocket_endpoints: [], recent_commands: [], page_body_captured: false }, error: error ? { code: error.code ?? 'error', method: error.method ?? null, diagnostics: error.diagnostics ?? null } : null, page_body_captured: false, created_at: new Date().toISOString() };
  const file = path.join(runRoot, 'ordinary-chat-h1-cdp-runtime-diagnostics.json');
  writeFileSync(file, JSON.stringify(value, null, 2) + '\n', { flag: 'wx' });
  return path.basename(file);
}

function captureH1GuiWebSocketFrames(events, pythonPort) {
 const wsUrls = new Map(), sentFrames = [], receivedFrames = [];
 for (let sequence = 0; sequence < events.length; sequence++) {
  const event = events[sequence], p = event.params ?? {};
  if (event.method === "Network.webSocketCreated") wsUrls.set(p.requestId, p.url);
  if (event.method === "Network.webSocketFrameSent" || event.method === "Network.webSocketFrameReceived") {
   const url = wsUrls.get(p.requestId); if (!url) continue;
   const target = event.method.endsWith("Sent") ? sentFrames : receivedFrames;
   target.push({ url, payload: p.response?.payloadData ?? "", websocketRequestId: p.requestId, sequence });
  }
 }
 for (const frame of [...sentFrames, ...receivedFrames]) {
  const ws = new URL(assertLoopbackUrl(frame.url, "ws:"));
  if (ws.hostname !== "127.0.0.1" || Number(ws.port) !== pythonPort) deny("renderer WebSocket did not use this run Python loopback port");
 }
 return { sentFrames, receivedFrames };
}
async function exerciseRealComposer(host, verified, runRoot) {
  if (typeof host?.waitForCdp !== "function") deny("native controlled H1 CDP interface is unavailable"); const browserWs = await host.waitForCdp(); const debugPort = Number(new URL(assertLoopbackUrl(browserWs, "ws:")).port);
  const discovered = await discoverElectronPage(debugPort, host.renderer_port, runRoot); const page = discovered.page;
  const query = '当前会话的工作概况'; let cdp = null, stage = 'connect_cdp', readiness = null, diagnosticsFile = null, inputState = null, domState = null;
  const observationPath = path.join(runRoot, 'ordinary-chat-h1-gui-observation.json');
  try {
    cdp = await connectCdp(page.webSocketDebuggerUrl);
    stage = 'enable_network_page_runtime';
    await cdp.command('Network.enable'); await cdp.command('Page.enable'); await cdp.command('Page.setLifecycleEventsEnabled', { enabled: true }); await cdp.command('Runtime.enable');
    stage = 'await_initial_loadurl_and_composer';
    readiness = await waitForH1Workbench(cdp, host.renderer_port);
    let rendered = readiness.state;
    stage = 'focus_composer_and_send';
    const focused = await cdpValue(cdp, "(()=>{const e=document.querySelector('#unified-input');if(!e)return false;e.focus();e.value='';e.dispatchEvent(new Event('input',{bubbles:true}));return document.activeElement===e})()", 2_000);
    if (focused !== true) deny('sealed renderer composer could not be focused');
    await cdp.command('Input.insertText', { text: query });
    const inputProbe = `(()=>{const e=document.querySelector('#unified-input');const b=document.querySelector('#unified-send');return{textarea_present:!!e,focused:document.activeElement===e,input_value_matches_query:!!e&&e.value===${JSON.stringify('当前会话的工作概况')},send_button_present:!!b,send_button_enabled:!!b&&!b.disabled}})()`;
    inputState = await cdpValue(cdp, inputProbe, 2_000);
    if (!inputState?.input_value_matches_query || !inputState?.send_button_enabled) deny('composer input or send button was not ready for the accepted Shift+Enter action');
    for (const keyEvent of h1ComposerSendKeyboardEvents()) await cdp.command('Input.dispatchKeyEvent', keyEvent);
    stage = 'await_python_reply_dom'; const replyDeadline = Date.now() + 90_000;
    while (Date.now() < replyDeadline) {
      try { rendered = await cdpValue(cdp, `(()=>({title:document.title,href:location.href,user:[...document.querySelectorAll('.unified-turn-user .unified-turn-body')].map(e=>e.innerText).at(-1)||'',assistant:[...document.querySelectorAll('.unified-turn-assistant .unified-turn-body')].map(e=>e.innerText).at(-1)||''}))()`, 1_500); }
      catch (error) { if (!transientCdpEvaluationError(error)) throw error; await new Promise(resolve => setTimeout(resolve, 250)); continue; }
      domState = { user_query_visible: rendered?.user?.includes(query) === true, assistant_reply_visible: rendered?.assistant?.includes('当前会话侧车没有已记录事项') === true };
      if (domState.user_query_visible && domState.assistant_reply_visible) break;
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    if (!domState?.user_query_visible || !domState?.assistant_reply_visible) deny('deterministic Python overview reply did not appear in the workbench DOM');
    const { sentFrames, receivedFrames } = await waitForH1GuiTurnEnd(
      () => captureH1GuiWebSocketFrames(cdp.events, host.port), { query },
    );    const banners = receivedFrames.map(decodedFrame).filter(item => item.payload?.type === 'session.banner' && typeof item.payload.session_id === 'string');
    if (!banners.length) deny('the isolated Python WebSocket did not identify its active session');
    const sessionId = banners[0].payload.session_id; if (banners.some(item => item.payload.session_id !== sessionId)) deny('session changed during the single GUI request');
    const sessionDir = path.join(verified.app_root, 'data', 'sessions', sessionId);
    realDirectory(sessionDir, 'isolated H1 conversation directory');
    if (!inside(path.join(verified.app_root, 'data', 'sessions'), sessionDir)) deny('session evidence escaped isolated H1 data');
    const messageLog = path.join(sessionDir, 'messages.jsonl'); const persistedMessages = readFileSync(messageLog, 'utf8').split(/\r?\n/).filter(Boolean).map(line => { try { return JSON.parse(line); } catch { deny('isolated session message log contains invalid JSON'); } });
    const exchange = assertGuiExchangeEvidence({ query, sentFrames, receivedFrames, renderedUserText: rendered?.user ?? '', renderedAssistantText: rendered?.assistant ?? '', sessionId, persistedMessages });
    writeFileSync(path.join(runRoot, 'ordinary-chat-h1-gui-correlation.json'), JSON.stringify({ format: 1, session_id: exchange.session_id, request_id: exchange.request_id, websocket_request_id: exchange.websocket_request_id, same_websocket_session_banner: true, persisted_message_log: path.relative(runRoot, messageLog).replace(/\\/g, '/'), persisted_message_log_sha256: createHash('sha256').update(readFileSync(messageLog)).digest('hex'), adjacent_user_reply_pair: exchange.persisted_same_session_pair, reply_protocol_echoes_request_id: false, correlation_mode: exchange.request_reply_correlation }, null, 2) + '\n', { flag: 'wx' });
    stage = 'capture_workbench_screenshot';
    const shot = await cdp.command('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    const screenshot = path.join(runRoot, 'ordinary-chat-h1-workbench.png'); const screenshotBytes = Buffer.from(shot.data, 'base64'); writeFileSync(screenshot, screenshotBytes, { flag: 'wx' });
    writeFileSync(observationPath, JSON.stringify(buildH1GuiRoundTripDiagnostic({ status: 'success', stage: 'complete', rendererPort: host.renderer_port, pythonWsPort: host.port, query, inputState, domState, events: cdp.events }), null, 2) + '\n', { flag: 'wx' });
    stage = 'complete'; diagnosticsFile = saveCdpRuntimeDiagnostics(runRoot, 'completed', page, cdp, stage, readiness, null);
    return { exchange, gui_observation: path.basename(observationPath), screenshot: path.basename(screenshot), screenshot_sha256: createHash('sha256').update(screenshotBytes).digest('hex'), session_message_log: path.relative(runRoot, messageLog).replace(/\\/g, '/'), renderer_url: 'http://127.0.0.1:' + host.renderer_port + '/', cdp_port: debugPort, python_ws_port: host.port, query, renderer_build: verified.manifest.build_provenance, cdp_target_diagnostics: discovered.diagnostics, cdp_runtime_diagnostics: diagnosticsFile, model_call_status: 'expected_no_model_call_not_observed', outbound_call_observation: 'not_observed' };
  } catch (error) {
    try { if (!existsSync(observationPath)) writeFileSync(observationPath, JSON.stringify(buildH1GuiRoundTripDiagnostic({ status: 'failed', stage, rendererPort: host.renderer_port, pythonWsPort: host.port, query, inputState, domState, events: cdp?.events ?? [], errorCode: error?.code }), null, 2) + '\n', { flag: 'wx' }); } catch {}
    try { if (!diagnosticsFile) diagnosticsFile = saveCdpRuntimeDiagnostics(runRoot, 'failed', page, cdp, stage, readiness, error); }
    catch { /* preserve the interaction failure; target diagnostics remain separate */ }
    throw error;
  } finally { cdp?.close(); }
}


export async function resolveH1PythonExecutable() {
  const local = path.resolve(sandboxRoot, '..', '..', '.venv', 'Scripts', 'python.exe');
  if (existsSync(local)) return local;
  const configured = process.env.MY_AGENT_PYTHON_EXECUTABLE;
  if (configured && path.isAbsolute(configured) && existsSync(configured)) return path.resolve(configured);
  const { execFileSync } = await import('node:child_process');
  try {
    const output = execFileSync(process.platform === 'win32' ? 'where.exe' : 'which', ['python'], { encoding: 'utf8', windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'] });
    const candidate = output.split(/\r?\n/).map(line => line.trim()).find(line => line && path.isAbsolute(line) && existsSync(line));
    if (candidate) return path.resolve(candidate);
  } catch {}
  deny('sandbox .venv is absent and no system Python executable could be resolved');
}

export function assertOrdinaryChatH1LaunchAuthorized(env = process.env) {
  requireOrdinaryChatH1GuiOptIn(env);
}

export async function runOrdinaryChatH1(options = {}) {
  const gui = options.gui === true;
  if (options.env !== undefined && options.env !== process.env) deny('H1 opt-in must come from the real process environment');
  assertOrdinaryChatH1LaunchAuthorized(process.env);
  if (options.electronExecutable || options.pythonExecutable || options.hostOptions) deny('H1 forbids runtime overrides and injected launch dependencies');
  const runRoot = newMarkedRun(); let host = null, finalError = null, guiResult = null, baseline = null, verified = null, environmentRoots = null, h1ChildEnvironment = null, agentPathsProof = null, fixtureEnvironmentEvidence = null;
  const api = await import(pathToFileURL(path.join(scriptDir, 'verify-evolution-a.mjs')).href);
  try {
    const python = options.pythonExecutable ?? await resolveH1PythonExecutable();
    const electron = options.electronExecutable ?? path.join(sandboxRoot, 'desktop', 'node_modules', 'electron', 'dist', 'electron.exe');
    baseline = api.captureStableBaseline(sandboxRoot, runRoot);
    const build = await api.buildH1ElectronMainOutputs({ sandboxRoot, runRoot });
    const frozen = await api.freezeHostFixture({ sandboxRoot, runRoot, trustedPythonExecutable: python, electronExecutable: electron, chatFixtureProfile: 'ordinary-chat-h1-v1' }, { electronBuildRoot: build.output_root, rendererBuild: true });
    verified = api.verifyFrozenHostFixture({ sandboxRoot, runRoot, ...frozen });
    if (verified.profile.chat_fixture_profile !== 'ordinary-chat-h1-v1' || verified.profile.candidate_fixture_profile !== undefined) deny('sealed profile is not exclusively the synthetic ordinary chat profile');
    assertSealedAgentRootMarker(verified.app_root);
    assertEmptyH1Data(verified.app_root, verified.profile_root);
    environmentRoots = { profile: verified.profile_root, runRoot, agentRoot: verified.app_root, userProfile: path.join(verified.profile_root, 'userprofile'), appData: path.join(verified.profile_root, 'appdata', 'Roaming'), localAppData: path.join(verified.profile_root, 'appdata', 'Local'), home: path.join(verified.profile_root, 'home'), xdgConfigHome: path.join(verified.profile_root, 'xdg-config'), xdgDataHome: path.join(verified.profile_root, 'xdg-data'), xdgCacheHome: path.join(verified.profile_root, 'xdg-cache'), temp: path.join(verified.profile_root, 'tmp') };
    assertExistingH1UserDirectories([environmentRoots.userProfile, environmentRoots.localAppData, environmentRoots.appData, environmentRoots.home, environmentRoots.xdgConfigHome, environmentRoots.xdgDataHome, environmentRoots.xdgCacheHome, environmentRoots.temp, path.join(environmentRoots.profile, 'electron-userData'), path.join(environmentRoots.profile, 'electron-sessionData')], environmentRoots.profile);
    h1ChildEnvironment = buildIsolatedGuiEnvironment(buildH1HostBaseEnvironment(), environmentRoots);
    agentPathsProof = verifyOrdinaryChatH1AgentPaths(api, verified);
    if (!samePath(h1ChildEnvironment.MY_AGENT_ROOT, verified.app_root)) deny('H1 child MY_AGENT_ROOT is not the sealed app root');
    if (gui) {
      assertRunLocalRenderer(verified.manifest, verified.renderer_root, runRoot);
      const agent = readFileSync(path.join(verified.app_root, 'agent-core', 'agent.py'), 'utf8');
      const server = readFileSync(path.join(verified.app_root, 'agent-core', 'server.py'), 'utf8');
      assertCurrentSessionOverviewRoute(agent, server);
    }
    fixtureEnvironmentEvidence = { forwarded_keys: Object.keys(h1ChildEnvironment).sort(), credential_env_forwarded_keys: Object.keys(h1ChildEnvironment).filter(key => /(?:API.?KEY|TOKEN|SECRET|CREDENTIAL)/i.test(key)).sort(), isolated_agent_root: h1ChildEnvironment.MY_AGENT_ROOT, isolated_user_profile: h1ChildEnvironment.USERPROFILE, isolated_app_data: h1ChildEnvironment.APPDATA, isolated_local_app_data: h1ChildEnvironment.LOCALAPPDATA, isolated_temp: h1ChildEnvironment.TEMP };
    if (fixtureEnvironmentEvidence.credential_env_forwarded_keys.length !== 0) deny('isolated child credential environment contains a credential-like key');
    const hostOptions = gui ? { cdp: { enabled: true } } : {};
    host = await api.startFrozenHost({ sandboxRoot, runRoot, ...frozen }, hostOptions);
    if (!Number.isInteger(host.port) || host.port < 1 || !Number.isInteger(host.renderer_port) || host.renderer_port < 1) deny('H1 Host omitted dynamic Python WS or sealed renderer readiness');
    if (gui) { guiResult = await exerciseRealComposer(host, verified, runRoot); if (!fixtureEnvironmentEvidence || fixtureEnvironmentEvidence.credential_env_forwarded_keys.length !== 0) deny('isolated child credential environment was not evidenced'); guiResult.credential_environment = fixtureEnvironmentEvidence; }
    const exit = await host.close(); host = null;
    if (exit.code !== 0 || exit.signal !== null || exit.python_environment_unchanged !== true || (gui && (exit.shutdown_method !== 'cdp_browser_close' || exit.shutdown_request_sent !== true || exit.forced_termination === true))) deny('H1 Host did not complete a verified normal close');
    api.verifyStableBaselineUnchanged(baseline, api.captureStableBaseline(sandboxRoot, runRoot));
    const ready = readReadyFrame(path.join(runRoot, 'host', 'logs', 'electron.stdout.log')); if (!ready) deny('ordinary H1 readiness evidence is missing');
    const evidence = { format: 1, status: gui ? 'gui_user_message_roundtrip_verified' : 'ready_and_stopped', scope: gui ? 'real sealed Electron composer -> renderer WebSocket user.message -> isolated Python deterministic overview -> rendered workbench reply; model egress not observed' : 'H1 host bootstrap/readiness/controlled shutdown only; no GUI chat interaction claimed', profile: 'ordinary-chat-h1-v1', synthetic_only: true, model_call_status: gui ? 'expected_no_model_call_not_observed' : 'not_observed', outbound_call_observation: 'not_observed', ws_host: '127.0.0.1', ws_port: ready.port, renderer_host: '127.0.0.1', renderer_port: ready.renderer_port, agent_paths_proof: agentPathsProof, ...(gui ? { gui: guiResult, cdp_runtime_diagnostics: guiResult.cdp_runtime_diagnostics, credentials_forwarded: fixtureEnvironmentEvidence.credential_env_forwarded_keys.length > 0, credential_environment: fixtureEnvironmentEvidence, route: { query: '当前会话的工作概况', recall_enabled: true, explicit_session_guard: true, server_default_recall_factory: true } } : {}), exit_code: exit.code, signal: exit.signal, forced_termination: exit.forced_termination === true, shutdown_timeout_ms: exit.shutdown_timeout_ms ?? null, python_environment_unchanged: exit.python_environment_unchanged, profile_sha256: verified.manifest.profile_sha256, renderer_build_provenance: verified.manifest.build_provenance, baseline_stable_files: baseline.stable_files, baseline_listeners: baseline.baseline_listeners, stable_tree_unchanged: true, run_root: runRoot, logs: 'host/logs', created_at: new Date().toISOString() };
    writeFileSync(path.join(runRoot, 'ordinary-chat-h1-evidence.json'), JSON.stringify(evidence, null, 2) + '\n', { flag: 'wx' }); (options.output ?? process.stdout).write(JSON.stringify(evidence, null, 2) + '\n'); return evidence;
  } catch (error) {
    finalError = error;
    try { writeFileSync(path.join(runRoot, 'ordinary-chat-h1-failure.json'), JSON.stringify({ format: 1, status: 'failed_closed', gui, reason: String(error?.message ?? error).slice(0, 1000), evidence_preserved: true, agent_paths_proof: agentPathsProof, cdp_target_diagnostics: existsSync(path.join(runRoot, 'ordinary-chat-h1-cdp-target-diagnostics.json')) ? 'ordinary-chat-h1-cdp-target-diagnostics.json' : null, cdp_runtime_diagnostics: existsSync(path.join(runRoot, 'ordinary-chat-h1-cdp-runtime-diagnostics.json')) ? 'ordinary-chat-h1-cdp-runtime-diagnostics.json' : null, gui_observation: existsSync(path.join(runRoot, 'ordinary-chat-h1-gui-observation.json')) ? 'ordinary-chat-h1-gui-observation.json' : null, credential_environment: fixtureEnvironmentEvidence, model_call_status: gui ? 'expected_no_model_call_not_observed' : 'not_observed', outbound_call_observation: 'not_observed', created_at: new Date().toISOString() }, null, 2) + '\n', { flag: 'wx' }); } catch {}
    throw error;
  } finally {
    if (host) try { const exit = await host.close(); if (exit.code !== 0 || exit.signal !== null || exit.python_environment_unchanged !== true) throw new Error('controlled H1 close was not verified'); }
      catch (error) { if (finalError) finalError.message += `; controlled close also failed: ${String(error?.message ?? error).slice(0, 240)}`; else throw error; }
  }
}

function readReadyFrame(log){if(!existsSync(log))return null;for(const line of readFileSync(log,"utf8").split(/\r?\n/)){try{const frame=JSON.parse(line);if(frame.type==="ordinary-chat-h1-host-ready"&&frame.host==="127.0.0.1"&&Number.isInteger(frame.port)&&frame.port>0&&frame.renderer_host==="127.0.0.1"&&Number.isInteger(frame.renderer_port)&&frame.renderer_port>0)return frame;}catch{}}return null;}
if(process.argv[1]&&samePath(path.resolve(process.argv[1]),fileURLToPath(import.meta.url))){if(process.argv.includes("--help")||process.argv.includes("-h")){console.log("Usage: REAL_ELECTRON_CHAT_H1_OPT_IN=1 node desktop/scripts/run-ordinary-chat-h1.mjs [--gui]\nGUI additionally requires REAL_ELECTRON_CHAT_H1_GUI_OPT_IN=1 after main-agent review. No model is called.");}else runOrdinaryChatH1({gui:process.argv.includes("--gui")}).catch(error=>{console.error(`Ordinary-chat H1 refused/failed: ${error.code??"fixture_error"}: ${error.message}`);process.exitCode=1;});}


