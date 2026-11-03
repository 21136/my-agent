import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir, homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import test from "node:test";

const here = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(here, "..");
const repo = path.resolve(desktop, "..", "..", "..");
const core = path.join(desktop, "..", "agent-core");
const python = path.join(repo, ".venv", "Scripts", "python.exe");
const require = createRequire(path.join(desktop, "package.json"));
const ts = require("typescript");

const pythonFixture = String.raw`
import asyncio, json, os, pathlib, sys, threading, uuid
root=pathlib.Path(sys.argv[1]).resolve(); core=pathlib.Path(sys.argv[2]).resolve()
assert pathlib.Path(os.environ["MY_AGENT_ROOT"]).resolve()==root
for name in ("data","workspace","evolve"): (root/name).mkdir(parents=True,exist_ok=True)
(root/"evolve"/"_index.toml").write_text("# isolated fixture\\n",encoding="utf-8")
sys.path.insert(0,str(core))
from paths import AgentPaths
from llm_client import LLMResponse
import server
from websockets.asyncio.server import serve
paths=AgentPaths.discover()
assert paths.agent_root==root and paths.workspace==root/"workspace" and paths.data==root/"data"
class _ReplyClient:
 def __init__(self): self.cancel_event=threading.Event()
 def set_cancel_event(self,event): self.cancel_event=event
 def chat(self,_messages,*,stream=None,**_kwargs):
  text="synthetic fixture reply"
  if stream and stream.on_content_delta: stream.on_content_delta(text)
  return LLMResponse("fixture",text,[],"stop",None,{})
real_build_repl=server._build_repl
def fixture_build_repl(*args,**kwargs):
 repl=real_build_repl(*args,**kwargs)
 client=_ReplyClient(); client.set_cancel_event(repl.agent.cancel_event)
 repl.agent.llm=client
 return repl
server._build_repl=fixture_build_repl
run_line_stages=[]
real_run_line=server._run_line
async def traced_run_line(*args,**kwargs):
 attachment_session_id=kwargs.get("external_user_message_attachment_session_id")
 entry={"phase":"run_line_enter","lock_held":kwargs.get("lock_held") is True,"external_request_id_kwarg_present":"external_user_message_request_id" in kwargs,"external_request_id_present":type(kwargs.get("external_user_message_request_id")) is str,"external_request_id_valid":False,"attachment_session_proof_present":type(attachment_session_id) is str,"attachment_session_binding_valid":(attachment_session_id is None or (type(attachment_session_id) is str and bool(args) and getattr(getattr(args[0],"session",None),"conversation_id",None)==attachment_session_id)),"prestarted_run_present":kwargs.get("prestarted_run_id") is not None,"work_record_binding_present":kwargs.get("work_record_binding") is not None}
 value=kwargs.get("external_user_message_request_id")
 if type(value) is str:
  try: entry["external_request_id_valid"]=str(uuid.UUID(value))==value
  except (ValueError,TypeError,AttributeError): pass
 run_line_stages.append(entry)
 try:
  result=await real_run_line(*args,**kwargs)
  entry["phase"]="run_line_return"
  return result
 except Exception as exc:
  entry["phase"]="run_line_exception"; entry["exception_class"]=type(exc).__name__
  raise
 finally:
  (root/"run-line-stage.json").write_text(json.dumps(run_line_stages),encoding="utf-8")
server._run_line=traced_run_line
dispatch_stages=[]
real_dispatch=server.WsSessionHandler._dispatch
async def traced_dispatch(self,message,*args,**kwargs):
 if type(message) is dict and message.get("type")=="user.message":
  value=message.get("request_id")
  valid=False
  if type(value) is str:
   try: valid=str(uuid.UUID(value))==value
   except (ValueError,TypeError,AttributeError): pass
  attachments=message.get("attachments")
  dispatch_stages.append({"phase":"user_message_dispatch","request_id_present":type(value) is str,"request_id_valid":valid,"attachment_count":len(attachments) if type(attachments) is list else 0})
  (root/"dispatch-stage.json").write_text(json.dumps(dispatch_stages),encoding="utf-8")
 return await real_dispatch(self,message,*args,**kwargs)
server.WsSessionHandler._dispatch=traced_dispatch
async def main():
 handler=server.WsSessionHandler(paths)
 async with serve(handler.handle,"127.0.0.1",0,max_size=65536) as listener:
  print(json.dumps({"port":listener.sockets[0].getsockname()[1],"fixture":"real Agent/WsSessionHandler; controlled _ReplyClient"}),flush=True)
  await asyncio.to_thread(sys.stdin.readline)
asyncio.run(main())
`;

const moduleCache = new Map();
function loadTypeScript(url, injected) {
  if (moduleCache.has(url.href)) return moduleCache.get(url.href);
  const exports = {};
  moduleCache.set(url.href, exports);
  const source = ts.transpileModule(readFileSync(url, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  new Function("exports", "require", "window", "WebSocket", "crypto", source)(
    exports,
    (specifier) => {
      assert.ok(specifier.startsWith("."), "unexpected non-local desktop dependency");
      const resolved = new URL(specifier.endsWith(".ts") ? specifier : `${specifier}.ts`, url);
      return loadTypeScript(resolved, injected);
    },
    injected.window,
    injected.WebSocket,
    injected.crypto,
  );
  return exports;
}
function safeRemoveFixture(root, identity, tempCanonical) {
  const stat = lstatSync(root, { bigint: true });
  if (!stat.isDirectory() || stat.isSymbolicLink() || realpathSync.native(root) !== identity.path
      || stat.dev !== identity.dev || stat.ino !== identity.ino || stat.birthtimeNs !== identity.birthtimeNs) {
    throw new Error("isolated fixture identity changed; preserving it");
  }
  const relative = path.relative(tempCanonical, identity.path);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error("isolated fixture escaped OS temp; preserving it");
  }
  rmSync(identity.path, { recursive: true, force: false });
}

function waitForStartup(child, timeoutMs = 15000) {
  const lines = createInterface({ input: child.stdout });
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("fixture_start_timeout")), timeoutMs);
    lines.once("line", (line) => {
      clearTimeout(timer);
      try { resolve(JSON.parse(line)); } catch { reject(new Error("fixture_start_protocol_error")); }
    });
    child.once("error", () => { clearTimeout(timer); reject(new Error("fixture_spawn_error")); });
    child.once("exit", () => { clearTimeout(timer); reject(new Error("fixture_exit_before_ready")); });
  });
}

function waitUntil(predicate, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const poll = () => {
      if (predicate()) return resolve();
      if (Date.now() >= deadline) return reject(new Error("ws_event_timeout"));
      setTimeout(poll, 10);
    };
    poll();
  });
}

test("real AgentWsClient receives the exact persisted ACK and reducer marks the ordinary message saved", {
  skip: !existsSync(python), timeout: 60000,
}, async (t) => {
  const evidence = [];
  const received = [];
  const rawEvents = [];
  const outgoingEvidence = [];
  const tempCanonical = realpathSync.native(tmpdir());
  const root = mkdtempSync(path.join(tempCanonical, "user-message-ack-real-ws-"));
  const initial = lstatSync(root, { bigint: true });
  const identity = { path: realpathSync.native(root), dev: initial.dev, ino: initial.ino, birthtimeNs: initial.birthtimeNs };
  const profile = path.join(root, "profile");
  const profilePaths = {
    USERPROFILE: path.join(profile, "userprofile"), APPDATA: path.join(profile, "appdata", "Roaming"),
    LOCALAPPDATA: path.join(profile, "appdata", "Local"), TEMP: path.join(profile, "temp"),
    TMP: path.join(profile, "temp"), HOME: path.join(profile, "home"),
  };
  for (const value of Object.values(profilePaths)) mkdirSync(value, { recursive: true });
  const env = {};
  for (const key of ["SYSTEMROOT", "WINDIR", "PATH", "PATHEXT"]) {
    if (typeof process.env[key] === "string") env[key] = process.env[key];
  }
  Object.assign(env, profilePaths, {
    MY_AGENT_ROOT: root, PYTHONDONTWRITEBYTECODE: "1", PYTHONNOUSERSITE: "1",
    PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8",
  });
  assert.equal(Object.keys(env).some((key) => /(?:API.?KEY|TOKEN|SECRET|CREDENTIAL)/i.test(key)), false);

  const child = spawn(python, ["-I", "-B", "-c", pythonFixture, root, core], {
    cwd: core, env, stdio: ["pipe", "pipe", "pipe"], windowsHide: true, shell: false,
  });
  let client;
  let childExited = false;
  child.stderr.on("data", () => {}); // Fixture diagnostics never expose traceback or payload text.
  child.once("exit", () => { childExited = true; });
  t.after(async () => {
    client?.destroy();
    if (!childExited) {
      child.stdin.end();
      await Promise.race([
        new Promise((resolve) => child.once("exit", resolve)),
        new Promise((resolve) => setTimeout(resolve, 3000)),
      ]);
    }
    if (!childExited) {
      child.kill();
      await Promise.race([
        new Promise((resolve) => child.once("exit", resolve)),
        new Promise((resolve) => setTimeout(resolve, 1500)),
      ]);
    }
    if (childExited) safeRemoveFixture(root, identity, tempCanonical);
  });

  try {
    const startup = await waitForStartup(child);
    assert.equal(startup.fixture, "real Agent/WsSessionHandler; controlled _ReplyClient");
    evidence.push("backend_ready");

    const runtimeWindow = { setTimeout, clearTimeout, myAgentDesktop: {} };
    const injected = { window: runtimeWindow, WebSocket: globalThis.WebSocket, crypto: globalThis.crypto };
    const { AgentWsClient } = loadTypeScript(new URL("../src/api/ws.ts", import.meta.url), injected);
    const { createChatSession } = loadTypeScript(new URL("../src/shells/chat-state.ts", import.meta.url), injected);
    const chat = createChatSession({ showProcess: true, confirmInBlocks: false });
    client = new AgentWsClient("127.0.0.1", startup.port);
    client.onEvent((event) => {
      received.push(event);
      chat.handleEvent(event);
    });
    await client.connect();
    client.ws.addEventListener("message", (frame) => {
      try {
        const event = JSON.parse(String(frame.data));
        if (event && typeof event.type === "string") {
          rawEvents.push({
            type: event.type,
            sessionMatches: event.type === "user.message.persisted" ? event.session_id === chat.model.sessionId : undefined,
            requestMatches: event.type === "user.message.persisted" ? event.request_id === requestId : undefined,
            finishReason: event.type === "turn.end" && ["stop", "completed", "cancelled", "timeout"].includes(event.finish_reason)
              ? event.finish_reason : undefined,
          });
        }
      } catch { rawEvents.push({ type: "malformed-frame" }); }
    });
    evidence.push("ws_connected");
    await waitUntil(() => received.some((event) => event.type === "session.banner"));
    const sessionId = chat.model.sessionId;
    assert.ok(sessionId, "real session.banner must bind a Session before dispatch");
    evidence.push("session_bound");

    const text = "ordinary externally supplied text";
    const requestId = globalThis.crypto.randomUUID();
    chat.pushUserMessage(text, { requestId, sessionId, receiptEligible: true });
    const socketSend = client.ws.send.bind(client.ws);
    client.ws.send = (raw) => {
      try {
        const message = JSON.parse(String(raw));
        if (message.type === "user.message") outgoingEvidence.push({
          phase: "user_message_wire_send",
          request_id_present: typeof message.request_id === "string",
          request_id_matches_dispatch: message.request_id === requestId,
        });
      } catch { outgoingEvidence.push({ phase: "wire_frame_parse_error" }); }
      return socketSend(raw);
    };
    const dispatch = client.sendMessage(text, undefined, { requestId });
    assert.equal(dispatch.requestId, requestId, "AgentWsClient dispatch must retain the request ID");
    assert.equal(dispatch.sessionId, sessionId, "AgentWsClient dispatch must bind the active Session");
    evidence.push("ordinary_message_dispatched");

    await waitUntil(() => received.some((event) => event.type === "turn.end"));
    const turnEnd = received.find((event) => event.type === "turn.end");
    evidence.push(turnEnd.finish_reason === "stop" ? "turn_end_stop_received" : "turn_end_received");
    await waitUntil(() => received.some((event) => event.type === "user.message.persisted"));
    evidence.push("exact_ack_forwarded_by_client");
    const dispatchStages = JSON.parse(readFileSync(path.join(root, "dispatch-stage.json"), "utf8"));
    const runLineStages = JSON.parse(readFileSync(path.join(root, "run-line-stage.json"), "utf8"));
    assert.equal(dispatchStages.length, 1);
    assert.equal(dispatchStages[0].request_id_valid, true, "backend dispatch must receive a canonical request ID");
    assert.equal(runLineStages.length, 1);
    assert.equal(runLineStages[0].external_request_id_valid, true,
      "the ordinary Agent run must receive the exact persistence receipt identity");
    evidence.push("backend_request_identity_forwarded");
    const receipts = received.filter((event) => event.type === "user.message.persisted");
    assert.equal(receipts.length, 1, "one durable append produces one bodyless ACK");
    const receipt = receipts[0];
    assert.deepEqual(Object.keys(receipt).sort(), ["request_id", "session_id", "type"]);
    assert.equal(receipt.session_id === dispatch.sessionId && receipt.request_id === dispatch.requestId, true,
      "ACK must exactly identify the dispatched request and Session");
    assert.equal(received.some((event) => event.type === "turn.end" && event.finish_reason === "stop"), true,
      "the controlled _ReplyClient turn must finish with stop");

    const userBlock = chat.model.blocks.find((block) => block.kind === "user" && block.persistence?.requestId === requestId);
    assert.equal(userBlock?.persistence?.state, "saved", "real reducer must accept the matching ACK");
    evidence.push("reducer_saved");

    const historyPath = path.join(root, "data", "sessions", sessionId, "messages.jsonl");
    const rows = readFileSync(historyPath, "utf8").trim().split(/\r?\n/).map((line) => JSON.parse(line));
    assert.equal(rows.some((row) => row.role === "user" && row.content === text), true,
      "the exact synthetic ordinary message must be present in the Session JSONL");
    evidence.push("jsonl_verified");
  } catch (error) {
    const exceptionClass = error instanceof Error ? error.constructor.name : "UnknownError";
    throw new Error(JSON.stringify({
      phase_evidence: evidence,
      exception_class: exceptionClass,
      raw_event_types: rawEvents.map((event) => event.type),
      client_event_types: received.map((event) => event.type),
      raw_ack_identity: rawEvents.filter((event) => event.type === "user.message.persisted").map(({ sessionMatches, requestMatches }) => ({ sessionMatches, requestMatches })),
      raw_turn_end_codes: rawEvents.filter((event) => event.type === "turn.end").map((event) => event.finishReason),
      backend_dispatch: existsSync(path.join(root, "dispatch-stage.json"))
        ? JSON.parse(readFileSync(path.join(root, "dispatch-stage.json"), "utf8")) : [],
      backend_gates: existsSync(path.join(root, "run-line-stage.json"))
        ? JSON.parse(readFileSync(path.join(root, "run-line-stage.json"), "utf8")) : [],
      outgoing: outgoingEvidence,
    }));
  }
});



test("real staged attachment ACK binds its exact Session/request and expired IDs never save", {
  skip: !existsSync(python), timeout: 60000,
}, async (t) => {
  const evidence = [];
  const received = [];
  const tempCanonical = realpathSync.native(tmpdir());
  const root = mkdtempSync(path.join(tempCanonical, "user-message-attachment-ack-agent-"));
  const rootStat = lstatSync(root, { bigint: true });
  const rootIdentity = { path: realpathSync.native(root), dev: rootStat.dev, ino: rootStat.ino, birthtimeNs: rootStat.birthtimeNs };
  const sourceRoot = mkdtempSync(path.join(tempCanonical, "user-message-attachment-ack-source-"));
  const sourceStat = lstatSync(sourceRoot, { bigint: true });
  const sourceIdentity = { path: realpathSync.native(sourceRoot), dev: sourceStat.dev, ino: sourceStat.ino, birthtimeNs: sourceStat.birthtimeNs };
  const sourcePath = path.join(sourceRoot, "synthetic-attachment.txt");
  const syntheticBytes = "SYNTHETIC_ATTACHMENT_BYTES_ONLY";
  writeFileSync(sourcePath, syntheticBytes, "utf8");

  const profile = path.join(root, "profile");
  const profilePaths = {
    USERPROFILE: path.join(profile, "userprofile"), APPDATA: path.join(profile, "appdata", "Roaming"),
    LOCALAPPDATA: path.join(profile, "appdata", "Local"), TEMP: path.join(profile, "temp"),
    TMP: path.join(profile, "temp"), HOME: path.join(profile, "home"),
  };
  for (const value of Object.values(profilePaths)) mkdirSync(value, { recursive: true });
  const env = {};
  for (const key of ["SYSTEMROOT", "WINDIR", "PATH", "PATHEXT"]) {
    if (typeof process.env[key] === "string") env[key] = process.env[key];
  }
  Object.assign(env, profilePaths, {
    MY_AGENT_ROOT: root, PYTHONDONTWRITEBYTECODE: "1", PYTHONNOUSERSITE: "1",
    PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8",
  });
  assert.equal(Object.keys(env).some((key) => /(?:API.?KEY|TOKEN|SECRET|CREDENTIAL)/i.test(key)), false);

  const child = spawn(python, ["-I", "-B", "-c", pythonFixture, root, core], {
    cwd: core, env, stdio: ["pipe", "pipe", "pipe"], windowsHide: true, shell: false,
  });
  let client;
  let childExited = false;
  child.stderr.on("data", () => {});
  child.once("exit", () => { childExited = true; });
  t.after(async () => {
    client?.destroy();
    if (!childExited) {
      child.stdin.end();
      await Promise.race([
        new Promise((resolve) => child.once("exit", resolve)),
        new Promise((resolve) => setTimeout(resolve, 3000)),
      ]);
    }
    if (!childExited) {
      child.kill();
      await Promise.race([
        new Promise((resolve) => child.once("exit", resolve)),
        new Promise((resolve) => setTimeout(resolve, 1500)),
      ]);
    }
    if (childExited) {
      safeRemoveFixture(root, rootIdentity, tempCanonical);
      safeRemoveFixture(sourceRoot, sourceIdentity, tempCanonical);
    }
  });

  const rawEvents = [];
  try {
    const startup = await waitForStartup(child);
    assert.equal(startup.fixture, "real Agent/WsSessionHandler; controlled _ReplyClient");
    evidence.push("backend_ready");

    const injected = {
      window: { setTimeout, clearTimeout, myAgentDesktop: {} },
      WebSocket: globalThis.WebSocket,
      crypto: globalThis.crypto,
    };
    const { AgentWsClient } = loadTypeScript(new URL("../src/api/ws.ts", import.meta.url), injected);
    const { createChatSession } = loadTypeScript(new URL("../src/shells/chat-state.ts", import.meta.url), injected);
    const chat = createChatSession({ showProcess: true, confirmInBlocks: false });
    client = new AgentWsClient("127.0.0.1", startup.port);
    client.onEvent((event) => {
      received.push(event);
      chat.handleEvent(event);
    });
    await client.connect();
    client.ws.addEventListener("message", (frame) => {
      try {
        const event = JSON.parse(String(frame.data));
        if (event && typeof event.type === "string") rawEvents.push({
          type: event.type,
          hasRequestId: typeof event.request_id === "string",
          hasSessionId: typeof event.session_id === "string",
          finishReason: event.type === "turn.end" && ["stop", "completed", "cancelled", "timeout"].includes(event.finish_reason)
            ? event.finish_reason : undefined,
        });
      } catch { rawEvents.push({ type: "malformed-frame" }); }
    });
    await waitUntil(() => received.some((event) => event.type === "session.banner"));
    const sessionId = chat.model.sessionId;
    assert.ok(sessionId);
    evidence.push("session_bound");

    client.stageFiles([sourcePath], "grow");
    await waitUntil(() => received.some((event) => event.type === "file.staged"));
    const firstStageEvent = received.find((event) => event.type === "file.staged");
    assert.equal(firstStageEvent.items.length, 1);
    const staged = firstStageEvent.items[0];
    assert.equal(staged.copied, true, "external synthetic file must be copied into the isolated Agent workspace");
    assert.ok(staged.ref.includes(`workspace/_drops/${sessionId}/`));
    assert.equal(existsSync(path.resolve(root, ...staged.ref.split("/"))), true, "the staged copy must exist on disk");
    evidence.push("real_file_stage_complete");

    const prompt = "synthetic attachment question";
    const requestId = globalThis.crypto.randomUUID();
    const displayText = `[附件]\n- ${staged.name} → ${staged.ref}\n\n${prompt}`;
    const dispatch = client.sendMessage(prompt, [staged.id], { requestId });
    chat.pushUserMessage(displayText, {
      requestId: dispatch.requestId, sessionId: dispatch.sessionId,
      receiptEligible: true, hasAttachments: true,
    });
    assert.equal(dispatch.requestId, requestId);
    assert.equal(dispatch.sessionId, sessionId);
    evidence.push("attachment_message_dispatched");

    await waitUntil(() => received.some((event) => event.type === "user.message.persisted" && event.request_id === requestId));
    await waitUntil(() => received.some((event) => event.type === "turn.end"));
    evidence.push("attachment_ack_and_turn_end_received");
    const receipts = received.filter((event) => event.type === "user.message.persisted");
    assert.equal(receipts.length, 1);
    assert.deepEqual(Object.keys(receipts[0]).sort(), ["request_id", "session_id", "type"]);
    assert.equal(receipts[0].request_id === requestId && receipts[0].session_id === sessionId, true,
      "ACK must match the exact attachment request and Session");
    const savedBlock = chat.model.blocks.find((block) => block.kind === "user" && block.persistence?.requestId === requestId);
    assert.equal(savedBlock?.persistence?.state, "saved");
    assert.equal(savedBlock?.persistence?.attachmentRetention, "unknown",
      "saved confirms the JSONL user row, not permanent attachment retention");
    evidence.push("attachment_reducer_saved");

    const dispatchStages = JSON.parse(readFileSync(path.join(root, "dispatch-stage.json"), "utf8"));
    const runLineStages = JSON.parse(readFileSync(path.join(root, "run-line-stage.json"), "utf8"));
    assert.equal(dispatchStages.length, 1);
    assert.equal(dispatchStages[0].request_id_valid, true);
    assert.equal(dispatchStages[0].attachment_count, 1);
    assert.equal(runLineStages.length, 1);
    assert.equal(runLineStages[0].external_request_id_valid, true);
    assert.equal(runLineStages[0].attachment_session_proof_present, true);
    assert.equal(runLineStages[0].attachment_session_binding_valid, true);
    evidence.push("backend_attachment_source_bound");

    const historyPath = path.join(root, "data", "sessions", sessionId, "messages.jsonl");
    const rows = readFileSync(historyPath, "utf8").trim().split(/\r?\n/).map((line) => JSON.parse(line));
    const savedMessage = rows.find((row) => row.role === "user" && row.content.includes(prompt));
    assert.ok(savedMessage);
    assert.equal(savedMessage.content.includes(staged.ref), true, "JSONL must preserve the server-resolved attachment reference");
    assert.equal(savedMessage.content.includes(staged.name), true);
    assert.equal(savedMessage.content.includes(syntheticBytes), false, "JSONL stores the reference, not file bytes");
    evidence.push("attachment_jsonl_reference_verified");

    client.stageFiles([sourcePath], "grow");
    await waitUntil(() => received.filter((event) => event.type === "file.staged").length === 2);
    const expiredStage = received.filter((event) => event.type === "file.staged")[1].items[0];
    client.unstageFile(expiredStage.id);
    await waitUntil(() => received.some((event) => event.type === "file.unstaged" && event.attachment_id === expiredStage.id));
    const expiredRequestId = globalThis.crypto.randomUUID();
    const expiredText = "synthetic expired attachment request";
    const expiredDispatch = client.sendMessage(expiredText, [expiredStage.id], { requestId: expiredRequestId });
    chat.pushUserMessage(`[附件]\n- ${expiredStage.name} → ${expiredStage.ref}\n\n${expiredText}`, {
      requestId: expiredDispatch.requestId, sessionId: expiredDispatch.sessionId,
      receiptEligible: true, hasAttachments: true,
    });
    await waitUntil(() => received.some((event) => event.type === "error" && event.message === "unknown or expired attachment id"));
    evidence.push("expired_attachment_rejected");
    assert.equal(received.some((event) => event.type === "user.message.persisted" && event.request_id === expiredRequestId), false);
    const rejectedBlock = chat.model.blocks.find((block) => block.kind === "user" && block.persistence?.requestId === expiredRequestId);
    assert.equal(rejectedBlock?.persistence?.state, "unknown", "expired attachment must not appear saved");
    assert.equal(rejectedBlock?.persistence?.attachmentRetention, "unknown");
    const finalRows = readFileSync(historyPath, "utf8").trim().split(/\r?\n/).map((line) => JSON.parse(line));
    assert.equal(finalRows.some((row) => row.role === "user" && row.content.includes(expiredText)), false);
    assert.equal(received.filter((event) => event.type === "user.message.persisted").length, 1);
    evidence.push("expired_attachment_not_persisted_or_saved");
  } catch (error) {
    const exceptionClass = error instanceof Error ? error.constructor.name : "UnknownError";
    throw new Error(JSON.stringify({
      phase_evidence: evidence,
      exception_class: exceptionClass,
      raw_event_types: rawEvents.map((event) => event.type),
      client_event_types: received.map((event) => event.type),
      ack_count: received.filter((event) => event.type === "user.message.persisted").length,
      backend_dispatch: existsSync(path.join(root, "dispatch-stage.json"))
        ? JSON.parse(readFileSync(path.join(root, "dispatch-stage.json"), "utf8")) : [],
      backend_gates: existsSync(path.join(root, "run-line-stage.json"))
        ? JSON.parse(readFileSync(path.join(root, "run-line-stage.json"), "utf8")) : [],
    }));
  }
});
