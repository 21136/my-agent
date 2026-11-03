import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { readFileSync, readdirSync, existsSync, lstatSync, mkdirSync, mkdtempSync, realpathSync, rmSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir, homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import test from "node:test";

const here = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(here, "..");
const repo = path.resolve(desktop, "..", "..", "..");
const python = path.join(repo, ".venv", "Scripts", "python.exe");
const core = path.join(desktop, "..", "agent-core");
const require = createRequire(path.join(desktop, "package.json"));
const esbuild = require("esbuild");
const playwrightPath = process.env.CODEX_PLAYWRIGHT_PATH
  || path.join(homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const edge = process.env.ISOLATED_TEST_BROWSER
  || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";

// Actual Python Agent + WsSessionHandler; only the HTTPS model transport is
// synthetic and intercepted. All persisted/provider input is fixture text.
const serverScript = String.raw`
import asyncio, json, os, pathlib, sys, threading
root=pathlib.Path(sys.argv[1]).resolve(); core=pathlib.Path(sys.argv[2]).resolve()
assert pathlib.Path(os.environ["MY_AGENT_ROOT"]).resolve()==root
for name in ("data","workspace","evolve"): (root/name).mkdir(parents=True,exist_ok=True)
(root/"evolve"/"_index.core.toml").write_text("[core]\\n",encoding="utf-8")
sys.path.insert(0,str(core))
from paths import AgentPaths
from session import create_new, _messages_version
from work_records import WorkRecordStore
from work_records_experiment import WorkRecordExperimentController
from work_records_luna_recall import WorkRecordsLunaRecall
from llm_client import LLMClient, LLMResponse
from llm_models import ModelEntry, ModelRegistry
from main import WorkRecordControllerBindings
import httpx, llm_client, llm_models, work_records_luna_recall, server
from websockets.asyncio.server import serve
paths=AgentPaths.discover()
assert paths.agent_root==root and paths.workspace==root/"workspace" and paths.data==root/"data" and paths.evolve==root/"evolve"
session=create_new(paths,conversation_id="20261101-abcdef12")
sources=[]
for i in range(22):
 role="user" if i % 2 == 0 else "assistant"
 excerpt=f"SYNTHETIC-LONG-RECORD-{i:02d}-BEGIN|\n"+(f"synthetic-segment-{i:02d}|"*1750)+f"\nSYNTHETIC-LONG-RECORD-{i:02d}-END"
 sources.append(excerpt)
 session.append_message({"role":role,"content":excerpt})
source=sources[-1]
entry=ModelEntry(id="0x567-flash",name="Synthetic fixture",vendor="0x567",base_url="https://synthetic.provider.invalid",provider_model="gpt-6-luna",api_key="fixture-only-never-sent",max_input_tokens=1000000)
registry=ModelRegistry(models=(entry,),default_flash_id=entry.id,default_pro_id=entry.id,alias_map={})
llm_models.get_registry=lambda *_args,**_kwargs: registry
llm_client.get_registry=lambda *_args,**_kwargs: registry
work_records_luna_recall.get_registry=lambda *_args,**_kwargs: registry
ModelRegistry.load=classmethod(lambda _cls,*_args,**_kwargs: registry)
provider_report={"attempts":0,"validated_calls":0,"failure_categories":[],"calls":[]}
def write_provider_report():
 (root/"provider-report.json").write_text(json.dumps(provider_report),encoding="utf-8")
write_provider_report()
main_model_report={"calls":0,"recall_tool_offered":0,"recall_tool_selected":0}
def write_main_model_report():
 (root/"main-model-report.json").write_text(json.dumps(main_model_report),encoding="utf-8")
write_main_model_report()
tool_report=[]
def write_tool_report():
 (root/"tool-report.json").write_text(json.dumps(tool_report),encoding="utf-8")
write_tool_report()
bridge_calls=[]
confirm_responses=[]
bridge_report_lock=threading.Lock()
def write_bridge_report():
 with bridge_report_lock:
  (root/"bridge-report.json").write_text(json.dumps({"calls":bridge_calls,"responses":confirm_responses}),encoding="utf-8")
real_deliver_confirm=server.WsBridge.deliver_confirm
def fixture_deliver_confirm(self,request_id,choice):
 accepted=real_deliver_confirm(self,request_id,choice)
 confirm_responses.append({"choice":choice,"accepted":accepted})
 write_bridge_report()
 return accepted
server.WsBridge.deliver_confirm=fixture_deliver_confirm
real_http_client=httpx.Client
real_batch_confirm=server.WsBridge.confirm_work_card_batch
def fixture_batch_confirm(self,preview,allow_approve_all,**kwargs):
 call={"preview_bytes":len(preview.encode("utf-8")),"batch_count":len(json.loads(preview)["provider_payloads"]),"allow_approve_all":allow_approve_all,"main_model_calls_before_confirm":main_model_report["calls"]}
 bridge_calls.append(call)
 write_bridge_report()
 result=real_batch_confirm(self,preview,allow_approve_all,**kwargs)
 call["result"]=result
 write_bridge_report()
 return result
server.WsBridge.confirm_work_card_batch=fixture_batch_confirm
def fixture_http_client(*args,**kwargs):
 def response(request):
  provider_report["attempts"]+=1
  write_provider_report()
  def fail(category):
   provider_report["failure_categories"].append(category)
   write_provider_report()
   raise AssertionError("synthetic MockTransport check failed: "+category)
  if str(request.url)!=entry.chat_completions_url(): fail("endpoint_mismatch")
  if request.headers.get("authorization")!="Bearer fixture-only-never-sent": fail("synthetic_authorization_mismatch")
  try: payload=json.loads(request.content)
  except Exception: fail("request_json_invalid")
  if type(payload) is not dict or payload.get("model")!="gpt-6-luna" or not isinstance(payload.get("messages"),list) or len(payload["messages"])!=2:
   fail("request_payload_shape_invalid")
  try: body=json.loads(payload["messages"][1]["content"])
  except Exception: fail("request_user_payload_invalid")
  if type(body) is not dict or type(body.get("question")) is not str or type(body.get("candidates")) is not list:
   fail("candidate_payload_shape_invalid")
  candidates=body["candidates"]
  # Resolve the latest exact question from the active Session's durable messages; never include it as a candidate.
  messages_path=root/"data"/"sessions"/session.conversation_id/"messages.jsonl"
  persisted=[json.loads(line) for line in messages_path.read_text(encoding="utf-8").splitlines()]
  current_question_index=next((index for index in range(len(persisted)-1,-1,-1)
   if persisted[index].get("role")=="user" and persisted[index].get("content")==body["question"]),None)
  if current_question_index is None: fail("current_question_not_persisted")
  current_question_id=f"msg-{current_question_index:08d}"
  if any(type(row) is not dict or type(row.get("id")) is not str for row in candidates):
   fail("candidate_identity_invalid")
  if any(row["id"]==current_question_id for row in candidates):
   fail("current_question_position_included")
  selected=next((row["id"] for row in candidates if source in row["excerpt"]),None)
  provider_report["validated_calls"]+=1
  provider_report["calls"].append({"question_excluded":True,"candidate_count":len(candidates),"selected_source_found":selected is not None,"model":payload["model"]})
  write_provider_report()
  result={"status":"match","candidate_ids":[selected]} if selected else {"status":"not_found","candidate_ids":[]}
  return httpx.Response(200,json={"model":"gpt-6-luna","choices":[{"index":0,"finish_reason":"stop","message":{"role":"assistant","content":json.dumps(result)}}]})
 return real_http_client(*args,transport=httpx.MockTransport(response),**kwargs)
llm_client.httpx.Client=fixture_http_client
class SyntheticMainModel:
 def chat(self,messages,*,tools=None,**kwargs):
  names=[item.get("function",{}).get("name") for item in (tools or [])]
  offered="recall_current_dialogue" in names
  main_model_report["calls"]+=1
  if offered: main_model_report["recall_tool_offered"]+=1
  if not offered:
   write_main_model_report()
   return LLMResponse(model="synthetic-main-model",content="synthetic fallback",tool_calls=[],finish_reason="stop",usage=None,raw={})
  main_model_report["recall_tool_selected"]+=1
  write_main_model_report()
  return LLMResponse(model="synthetic-main-model",content=None,tool_calls=[{"id":"synthetic-recall-call","type":"function","function":{"name":"recall_current_dialogue","arguments":"{}"}}],finish_reason="tool_calls",usage=None,raw={})
def factory(bound_session,agent):
 agent.llm=SyntheticMainModel()
 original_executor_run=agent.executor.run
 def tracked_executor_run(name,arguments):
  result=original_executor_run(name,arguments)
  if name=="recall_current_dialogue":
   data=result.data if isinstance(getattr(result,"data",None),dict) else {}
   answer=data.get("answer") if isinstance(data.get("answer"),str) else ""
   tool_report.append({"ok":bool(getattr(result,"ok",False)),"status":data.get("status"),"answer_chars":len(answer),"answer_contains_source":source in answer,"spilled":data.get("spilled",False),"error_code":str(getattr(getattr(result,"error",None),"code",None))})
   write_tool_report()
  return result
 agent.executor.run=tracked_executor_run
 client=LLMClient(paths=paths); client.set_cancel_event(agent.cancel_event)
 controller=WorkRecordExperimentController(agent=agent,store=WorkRecordStore(bound_session),recognizer=lambda *_:{"action":"none"},semantic_matcher=WorkRecordsLunaRecall(client))
 return WorkRecordControllerBindings(recall=controller)
async def main():
 handler=server.WsSessionHandler(paths,work_record_controller_factory=factory)
 async with serve(handler.handle,"127.0.0.1",0) as ws_server:
  print(json.dumps({"port":ws_server.sockets[0].getsockname()[1],"session":session.conversation_id,"root":str(root),"fixture":"real-agent-and-ws; synthetic-main-model-and-provider-transport"}),flush=True)
  await asyncio.to_thread(sys.stdin.readline)
asyncio.run(main())
`;

const rendererEntry = `
import { AgentWsClient } from "./src/api/ws.ts";
import { mountUnifiedShell } from "./src/shells/unified/index.ts";
(window as any).startRecallDesktop = async (port: number) => {
  (window as any).myAgentDesktop = {
    setBilibiliViewState: async () => true,
    setShellDevViewState: async () => true,
    setEvolutionConversationId: async () => true,
  };
  const client = new AgentWsClient("127.0.0.1", port);
  (window as any).__agentWsClient = client;
  (window as any).__disposeUnified = mountUnifiedShell(document.querySelector("#app") as HTMLElement, client);
  await client.connect();
};
`;

function safeRemoveFixture(root, identity, tempCanonical) {
  const stat = lstatSync(root, { bigint: true });
  if (!stat.isDirectory() || stat.isSymbolicLink() || realpathSync.native(root) !== identity.path
      || stat.dev !== identity.dev || stat.ino !== identity.ino || stat.birthtimeNs !== identity.birthtimeNs) {
    throw new Error("isolated test root identity changed; preserving it");
  }
  const relative = path.relative(tempCanonical, identity.path);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) throw new Error("isolated root escaped OS temp; preserving it");
  rmSync(identity.path, { recursive: true, force: false });
}

function waitForLine(child, stderr, timeoutMs = 15000) {
  const lines = createInterface({ input: child.stdout });
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`Agent fixture startup timed out: ${stderr()}`)), timeoutMs);
    lines.once("line", (line) => { clearTimeout(timer); try { resolve(JSON.parse(line)); } catch { reject(new Error("invalid Agent fixture startup JSON")); } });
    child.once("error", (error) => { clearTimeout(timer); reject(new Error(`Agent fixture spawn failed: ${error.message}`)); });
    child.once("exit", (code) => { clearTimeout(timer); reject(new Error(`Agent fixture exited before startup (${code}): ${stderr()}`)); });
  });
}

test("isolated synthetic main model selects dialogue recall through real WS and Edge DOM confirmation", {
  skip: !existsSync(playwrightPath) || !existsSync(edge), timeout: 90000,
}, async (t) => {
  const tempCanonical = realpathSync.native(tmpdir());
  const root = mkdtempSync(path.join(tempCanonical, "dialogue-recall-rendered-agent-"));
  const initial = lstatSync(root, { bigint: true });
  const identity = { path: realpathSync.native(root), dev: initial.dev, ino: initial.ino, birthtimeNs: initial.birthtimeNs };
  const profile = path.join(root, "profile");
  const profilePaths = {
    USERPROFILE: path.join(profile, "userprofile"), APPDATA: path.join(profile, "appdata", "Roaming"),
    LOCALAPPDATA: path.join(profile, "appdata", "Local"), TEMP: path.join(profile, "temp"), TMP: path.join(profile, "temp"),
    HOME: path.join(profile, "home"),
  };
  for (const value of Object.values(profilePaths)) mkdirSync(value, { recursive: true });
  const env = {};
  for (const key of ["SYSTEMROOT", "WINDIR", "PATH", "PATHEXT"]) if (typeof process.env[key] === "string") env[key] = process.env[key];
  Object.assign(env, profilePaths, {
    MY_AGENT_ROOT: root, PYTHONDONTWRITEBYTECODE: "1", PYTHONNOUSERSITE: "1", PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8",
  });
  assert.equal(Object.keys(env).some((key) => /(?:API.?KEY|TOKEN|SECRET|CREDENTIAL)/i.test(key)), false);

  const bundle = await esbuild.build({
    stdin: { contents: rendererEntry, resolveDir: desktop, sourcefile: "dialogue-recall-rendered-entry.ts", loader: "ts" },
    bundle: true, write: false, platform: "browser", format: "iife",
    define: { "import.meta.url": "location.href" },
    loader: { ".png": "dataurl", ".jpg": "dataurl", ".svg": "dataurl", ".woff2": "dataurl" },
    plugins: [{ name: "ignore-css-imports", setup(build) { build.onLoad({ filter: /\.css$/ }, () => ({ contents: "", loader: "js" })); } }],
    logLevel: "silent",
  });
  const styleRoot = new URL("../src/shells/unified/", import.meta.url);
  const background = readFileSync(new URL("assets/chat-background-dark.png", styleRoot)).toString("base64");
  const css = readFileSync(new URL("../src/styles/theme.css", import.meta.url), "utf8") + "\n" + [
    "unified.css", "work-card.css", "current-work-overview.css", "neon-twin.css", "composer-companion.css",
    "sidebar-v2.css", "message-reading.css", "single-chat-tools.css",
  ].map((name) => readFileSync(new URL(name, styleRoot), "utf8")).join("\n")
    .replaceAll("./assets/chat-background-dark.png", `data:image/png;base64,${background}`);

  const child = spawn(python, ["-I", "-B", "-c", serverScript, root, core], {
    cwd: core, env, stdio: ["pipe", "pipe", "pipe"], windowsHide: true, shell: false,
  });
  let browser;
  let context;
  let page;
  let uiServer;
  let stderr = "";
  const pageErrors = [];
  const consoleErrors = [];
  const requests = [];
  child.stderr.on("data", (chunk) => { if (stderr.length < 12000) stderr += String(chunk).slice(0, 12000 - stderr.length); });
  t.after(async () => {
    if (page) await page.evaluate(() => window.__disposeUnified?.()).catch(() => {});
    if (context) await context.close().catch(() => {});
    if (browser) await browser.close().catch(() => {});
    if (uiServer) await new Promise((resolve) => uiServer.close(resolve));
    if (child.exitCode === null && child.signalCode === null) {
      const exited = new Promise((resolve) => child.once("exit", resolve));
      child.kill();
      await Promise.race([exited, new Promise((resolve) => setTimeout(resolve, 1500))]);
    }
    if (child.exitCode !== null || child.signalCode !== null) safeRemoveFixture(root, identity, tempCanonical);
  });

  const startup = await waitForLine(child, () => stderr);
  assert.equal(startup.fixture, "real-agent-and-ws; synthetic-main-model-and-provider-transport");
  assert.equal(path.resolve(startup.root), path.resolve(root));
  const transparentPng = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l2sAAAAASUVORK5CYII=", "base64");
  const htmlServer = createServer((request, response) => {
    if (request.url !== "/" && /\.(?:png|webp|svg)$/i.test(request.url || "")) {
      response.writeHead(200, { "content-type": "image/png", "content-length": transparentPng.length });
      response.end(transparentPng);
      return;
    }
    response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    response.end(`<!doctype html><html data-theme="dark"><head><title>my-agent isolated desktop</title><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data: blob:; connect-src ws://127.0.0.1:${startup.port};"><style>html,body,#app{height:100%;margin:0}${css}</style></head><body><div id="app"></div></body></html>`);
  });
  uiServer = htmlServer;
  await new Promise((resolve, reject) => { htmlServer.once("error", reject); htmlServer.listen(0, "127.0.0.1", resolve); });
  const uiPort = htmlServer.address().port;
  const uiOrigin = `http://127.0.0.1:${uiPort}`;
  const { chromium } = createRequire(path.join(playwrightPath, "package.json"))("playwright");
  browser = await chromium.launch({ headless: true, executablePath: edge, args: ["--disable-background-networking", "--no-first-run"] });
  context = await browser.newContext({ viewport: { width: 1200, height: 900 }, serviceWorkers: "block" });
  await context.route("**/*", async (route) => {
    const url = route.request().url();
    requests.push(url);
    if (url.startsWith(`${uiOrigin}/`)) return route.continue();
    return route.abort("blockedbyclient");
  });
  page = await context.newPage();
  page.setDefaultTimeout(30000);
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  await page.goto(`${uiOrigin}/`, { waitUntil: "domcontentloaded" });
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  await page.evaluate((port) => window.startRecallDesktop(port), startup.port);
  assert.match(await page.title(), /my-agent/i);
  await page.locator("#unified-input").waitFor({ state: "visible" });
  const chat = page.locator("#unified-chat");
  await chat.getByText("SYNTHETIC-LONG-RECORD-21-BEGIN", { exact: false }).waitFor({ state: "visible" });

  // First turn: large preview uses the dedicated batch-confirm channel; refusal sends no provider request.
  await page.locator("#unified-input").fill("\u9608\u503c\u6700\u7ec8\u5b9a\u6210\u4ec0\u4e48\uff1f");
  await page.locator("#unified-send").click();
  let card = chat.locator("[data-work-card-recall]").last();
  await card.waitFor({ state: "visible" });
  assert.equal(await card.locator("details.unified-confirm-details").evaluate((node) => node.open), false);
  assert.equal(await card.locator('[data-confirm="y"]').isDisabled(), true);
  assert.equal(await card.locator('[data-confirm="n"]').isDisabled(), false);
  assert.match(await card.innerText(), /全部 23 条可检索用户\/助手原文候选/);
  const firstBridgeReport = JSON.parse(readFileSync(path.join(root, "bridge-report.json"), "utf8"));
  assert.equal(firstBridgeReport.calls.length, 1);
  assert.ok(firstBridgeReport.calls[0].preview_bytes > 768 * 1024, "large preview must use the dedicated batch-confirm channel");
  assert.ok(firstBridgeReport.calls[0].preview_bytes <= 2 * 1024 * 1024);
  assert.ok(firstBridgeReport.calls[0].batch_count > 1);
  assert.equal(firstBridgeReport.calls[0].allow_approve_all, false);
  await card.locator('[data-confirm="n"]').click();
  let assistant = chat.locator(".unified-turn-assistant .unified-turn-body").last();
  await assistant.getByText("未获授权", { exact: false }).waitFor({ state: "visible" });
  assert.doesNotMatch(await assistant.innerText(), /SYNTHETIC-LONG-RECORD-21/);
  assert.equal(JSON.parse(readFileSync(path.join(root, "provider-report.json"), "utf8")).attempts, 0, "refusal must send no provider request");
  const firstMainReport = JSON.parse(readFileSync(path.join(root, "main-model-report.json"), "utf8"));
  assert.ok(firstMainReport.calls >= 1);
  assert.equal(firstMainReport.recall_tool_offered, 1, "synthetic main-model branch must receive the recall tool");
  assert.equal(firstMainReport.recall_tool_selected, 1, "synthetic main model must select recall_current_dialogue");

  // Second turn: keyboard approval is blocked before expansion; opening the native
  // details reveals the raw/readable preview and enables consent in the real DOM.
  await page.locator("#unified-input").fill("\u9608\u503c\u6700\u7ec8\u5b9a\u6210\u4ec0\u4e48\uff1f");
  await page.locator("#unified-send").click();
  card = chat.locator("[data-work-card-recall]").last();
  await card.waitFor({ state: "visible" });
  const details = card.locator("details.unified-confirm-details");
  assert.equal(await details.evaluate((node) => node.open), false);
  assert.equal(await card.locator('[data-confirm="y"]').isDisabled(), true);
  assert.match(await card.innerText(), /全部 25 条可检索用户\/助手原文候选/);
  const bridgeReport = JSON.parse(readFileSync(path.join(root, "bridge-report.json"), "utf8"));
  assert.equal(bridgeReport.calls.length, 2, "both decisions should use the dedicated batch-confirm bridge");
  const approvedPreview = bridgeReport.calls[1];
  assert.ok(approvedPreview.preview_bytes > 768 * 1024);
  assert.ok(approvedPreview.preview_bytes <= 2 * 1024 * 1024);
  assert.ok(approvedPreview.batch_count > 1);
  assert.equal(approvedPreview.allow_approve_all, false);
  await page.locator("#unified-input").evaluate((input) => input.blur());
  await page.keyboard.press("y");
  await page.locator("#unified-status").getByText("请先展开完整原始预览", { exact: false }).waitFor({ state: "visible" });
  assert.equal(await card.locator('[data-confirm="y"]').isDisabled(), true);
  assert.equal(await details.evaluate((node) => node.open), false);
  await details.locator(":scope > summary").click();
  assert.equal(await details.evaluate((node) => node.open), true);
  assert.equal(await card.locator('[data-confirm="y"]').isDisabled(), false);
  assert.match(await details.locator(".unified-recall-readable").innerText(), /共 25 条原文/);
  assert.match(await details.locator("pre.unified-confirm-preview").innerText(), /SYNTHETIC-LONG-RECORD-21-END/);
  const approvedRawPreview = await card.locator("pre.unified-confirm-preview").textContent();
  const approvedPreviewPayload = JSON.parse(approvedRawPreview).provider_payloads;
  assert.equal(approvedPreviewPayload.length, approvedPreview.batch_count);
  const batchDetails = card.locator("details.unified-recall-batch");
  assert.equal(await batchDetails.count(), approvedPreview.batch_count);
  for (let index = 0; index < approvedPreview.batch_count; index += 1) {
    const batchDetail = batchDetails.nth(index);
    await batchDetail.locator("summary").click();
    await batchDetail.waitFor({ state: "attached" });
    await page.waitForFunction((element) => element.dataset.loaded === "true", await batchDetail.elementHandle());
    const returnedExcerpts = await batchDetail.locator(".unified-recall-candidate").evaluateAll((sections) =>
      sections.map((section) => {
        const values = section.querySelectorAll(".unified-recall-value");
        return JSON.parse(values[2]?.textContent || "null");
      })
    );
    const expectedExcerpts = JSON.parse(approvedPreviewPayload[index].messages[1].content).candidates.map((item) => item.excerpt);
    assert.deepEqual(returnedExcerpts, expectedExcerpts, `rendered batch ${index} must show every full original`);
  }
  const assistantCountBeforeApproval = await chat.locator(".unified-turn-assistant .unified-turn-body").count();
  await card.locator('[data-confirm="y"]').click();
  await page.waitForFunction((previousCount) =>
    document.querySelectorAll(".unified-turn-assistant .unified-turn-body").length > previousCount,
    assistantCountBeforeApproval,
  );
  assistant = chat.locator(".unified-turn-assistant .unified-turn-body").last();
  await assistant.waitFor({ state: "visible" });
  const finalAnswerText = await assistant.innerText();
  const providerReport = JSON.parse(readFileSync(path.join(root, "provider-report.json"), "utf8"));
  const finalBridgeReport = JSON.parse(readFileSync(path.join(root, "bridge-report.json"), "utf8"));
  const toolReport = JSON.parse(readFileSync(path.join(root, "tool-report.json"), "utf8"));
  assert.equal(finalBridgeReport.responses.at(-1)?.choice, "y", "browser approval must reach the real backend bridge");
  assert.equal(finalBridgeReport.responses.at(-1)?.accepted, true, "real batch bridge must accept the correlated response");
  assert.equal(finalBridgeReport.calls.at(-1)?.result, "y", JSON.stringify({ finalBridgeReport, providerReport, answer: finalAnswerText.slice(0, 500) }));
  assert.equal(providerReport.attempts, approvedPreview.batch_count, JSON.stringify({ finalBridgeReport, providerReport }));
  assert.equal(providerReport.validated_calls, approvedPreview.batch_count, JSON.stringify({ finalBridgeReport, providerReport }));
  assert.deepEqual(providerReport.failure_categories, []);
  assert.equal(toolReport.length, 2);
  assert.equal(toolReport[0].status, "denied");
  assert.equal(toolReport[0].answer_contains_source, false);
  assert.equal(toolReport[1].ok, true);
  assert.equal(toolReport[1].answer_contains_source, true);
  assert.equal(toolReport[1].spilled, false);
  const spillDir = path.join(root, "data", "sessions", startup.session, "tool_outputs");
  assert.equal(existsSync(spillDir) ? readdirSync(spillDir).length : 0, 0, "sensitive recall answer must remain in the current turn, not be duplicated as a spill file");
  const finalMainReport = JSON.parse(readFileSync(path.join(root, "main-model-report.json"), "utf8"));
  assert.equal(finalMainReport.recall_tool_offered, 2);
  assert.equal(finalMainReport.recall_tool_selected, 2);
  assert.equal(finalMainReport.calls, approvedPreview.main_model_calls_before_confirm, "matched history must not trigger another main-model request after consent");
  assert.match(finalAnswerText, /SYNTHETIC-LONG-RECORD-21-END/, JSON.stringify({ answer: finalAnswerText.slice(0, 800), providerReport, finalBridgeReport, toolReport }));
  assert.match(finalAnswerText, /消息序号 23/);
  assert.match(finalAnswerText, /隐私提示/);

  assert.ok(providerReport.calls.every((call) => call.model === "gpt-6-luna" && call.question_excluded));
  assert.equal(providerReport.calls.filter((call) => call.selected_source_found).length, 1, "only the batch containing the target source should match");
  assert.ok(providerReport.calls.every((call) => call.candidate_count > 0));
  const persisted = readFileSync(path.join(root, "data", "sessions", startup.session, "messages.jsonl"), "utf8")
    .trim().split(/\r?\n/).map((line) => JSON.parse(line));
  assert.equal(persisted.filter((message) => message.role === "user" && message.content === "\u9608\u503c\u6700\u7ec8\u5b9a\u6210\u4ec0\u4e48\uff1f").length, 2, "both synthetic questions must be persisted exactly once");
  assert.ok(!persisted.some((message) => message.role === "tool" || (message.role === "assistant" && message.tool_calls)), "tool transport must not persist retrieved content as a tool turn");
  assert.ok(persisted.some((message) => message.role === "assistant" && message.content.includes("SYNTHETIC-LONG-RECORD-21-END")));

  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
  assert.ok(requests.every((url) => url.startsWith(`${uiOrigin}/`)), "browser HTTP remained on the isolated local renderer origin");
  await page.evaluate(() => window.__disposeUnified?.());
  console.log(JSON.stringify({
    result: "PASS", browser: edge, renderer: "real unified-shell mount", websocket: "real AgentWsClient to actual Python Agent/WsSessionHandler",
    model: "synthetic main tool-selection model + semantic MockTransport; no external call", flow: ["two main-model turns each select recall_current_dialogue", "real Edge DOM/socket refusal sends zero semantic requests", "real Edge DOM/socket approval returns matched synthetic source without a second main-model call", "large synthetic session.history rendered", "preview > 768 KiB confirmed through dedicated batch bridge", "pre-expansion approval blocked", "refusal accepted with zero provider calls", "expand all batches and verify complete originals", "one consent triggers every local mock batch request", "matched reply and source citation shown"],
    fixtureRoot: path.basename(root), uiOrigin, websocketPort: startup.port,
  }));
});












