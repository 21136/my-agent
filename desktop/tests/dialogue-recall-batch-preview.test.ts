import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import {
  isConfirmChoiceAllowed,
  isWorkCardRecallPreview,
  renderConfirmCardHtml,
  renderWorkCardRecallBatch,
  renderWorkCardRecallReadable,
  summarizeConfirmPreview,
} from "../src/shells/unified/confirm-preview.ts";

const SINGLE_SYSTEM = "You are a read-only semantic recognizer. Compare the question directly against EVERY candidate supplied; the list is complete and must not be searched, reordered, shortened, or prefiltered. Return exactly one JSON object with exactly keys status and candidate_ids. Use match with exactly one candidate ID, ambiguous with two or more plausible IDs, or not_found with an empty list. Copy IDs verbatim from the supplied candidates. Do not invent IDs or add explanations. Never call tools, alter records, or infer/access any other session.";
const BATCH_SYSTEM = "You are a read-only semantic recognizer. Compare the question with EVERY candidate in this batch only. Other batches may exist; do not infer their contents. Return exactly one JSON object with exactly keys status and candidate_ids. Use match with exactly one candidate ID, ambiguous with two or more plausible IDs, or not_found with an empty list. Copy IDs verbatim from this batch. Do not invent IDs, explain, call tools, alter records, or infer/access any other session.";
const question = "当时提到的启动阈值是多少？";
const endpoint = "https://synthetic.example/v1/chat/completions";
const sessionId = "synthetic-dialogue-session";

function candidate(index: number, role: "user" | "assistant", excerpt: string) {
  return { id: `msg-${String(index).padStart(8, "0")}`, title: `${role} message ${index + 1}`, excerpt };
}
function providerPayload(system: string, candidates: unknown[], promptQuestion = question) {
  return {
    model: "gpt-6-luna",
    messages: [
      { role: "system", content: system },
      { role: "user", content: JSON.stringify({ question: promptQuestion, candidates }) },
    ],
    temperature: 0,
    stream: false,
    response_format: { type: "json_object" },
  };
}
function dialogueBatch(batches: unknown[][]) {
  return JSON.stringify({
    model_id: "0x567-flash",
    vendor: "0x567",
    endpoint,
    session_id: sessionId,
    provider_payloads: batches.map((rows) => providerPayload(BATCH_SYSTEM, rows)),
  });
}
function singleDialogue(rows: unknown[]) {
  return JSON.stringify({
    model_id: "0x567-flash", vendor: "0x567", endpoint, session_id: sessionId,
    provider_payload: providerPayload(SINGLE_SYSTEM, rows),
  });
}

const first = [
  candidate(0, "user", "Synthetic source <script>window.evil()</script> & exact phrase."),
  candidate(3, "assistant", "Quoted \"phrase\" and line one\nline two."),
];
const second = [
  candidate(8, "user", "Long synthetic evidence: " + "完整原文-".repeat(40)),
  candidate(12, "assistant", "最后一条完整合成原文。"),
];
const validBatch = dialogueBatch([first, second]);

test("dialogue batch preview strictly recognizes frozen payloads and discloses all batches without truncation", () => {
  assert.equal(isWorkCardRecallPreview(validBatch), true);
  assert.equal(isConfirmChoiceAllowed(validBatch, "y", false), false);
  assert.equal(isConfirmChoiceAllowed(validBatch, "a", false), false);
  assert.equal(isConfirmChoiceAllowed(validBatch, "n", false), true);
  assert.equal(isConfirmChoiceAllowed(validBatch, "y", true), true);
  assert.equal(isConfirmChoiceAllowed(validBatch, "a", true), true);

  const view = summarizeConfirmPreview(validBatch);
  assert.equal(view.compact, true);
  assert.equal(view.detail, validBatch);
  assert.match(view.headline, /对话原文语义回查/);
  assert.match(view.reason, /全部 4 条/);
  assert.match(view.reason, /2 批/);
  assert.match(view.reason, /最多 2 次/);
  assert.match(view.reason, /费用/);

  const readable = renderWorkCardRecallReadable(validBatch);
  assert.match(readable, /接收方：0x567/);
  assert.match(readable, new RegExp(endpoint.replaceAll(".", "\\.")));
  assert.match(readable, /共 4 条原文，2 批/);
  assert.match(readable, /最多 2 次请求/);
  assert.match(readable, /可能产生费用/);
  assert.match(readable, /一次确认授权全部批次/);
  assert.match(readable, /data-recall-batch="0"/);
  assert.match(readable, /data-recall-batch="1"/);
  assert.match(readable, /第 1 批（2 条原文）/);
  assert.match(readable, /第 2 批（2 条原文）/);
  assert.doesNotMatch(readable, /window\.evil/);

  const firstBody = renderWorkCardRecallBatch(validBatch, 0);
  const secondBody = renderWorkCardRecallBatch(validBatch, 1);
  assert.match(firstBody, /完整原文（JSON 字符串）/);
  assert.match(firstBody, /&lt;script&gt;window\.evil\(\)&lt;\/script&gt; &amp; exact phrase/);
  assert.match(firstBody, /Quoted \\&quot;phrase\\&quot; and line one\\nline two/);
  assert.match(secondBody, /完整原文-/);
  assert.match(secondBody, /最后一条完整合成原文/);
  assert.equal(renderWorkCardRecallBatch(validBatch, 2), "");

  const html = renderConfirmCardHtml(validBatch, "<button>同意</button>");
  assert.match(html, /data-work-card-recall/);
  assert.match(html, /<details class="unified-confirm-details"/);
  assert.match(html, /完整原始预览/);
  assert.ok(html.includes(validBatch.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;")));
});

test("dialogue batch rejects mixed work-card candidates, inconsistent prompts, malformed IDs and mixed payload forms", () => {
  const envelope = { model_id: "0x567-flash", vendor: "0x567", endpoint, session_id: sessionId };
  const validBodies = JSON.parse(validBatch).provider_payloads;
  const mixedCardCandidate = { id: "wc-" + "a".repeat(64), title: "card candidate", excerpt: "{}" };
  const malformedTitle = candidate(15, "user", "synthetic");
  malformedTitle.title = "assistant message 17";
  const duplicateAcrossBatches = dialogueBatch([[first[0]], [first[0]]]);
  const reverseOrder = dialogueBatch([[second[0]], [first[0]]]);
  const inconsistentQuestion = JSON.stringify({
    ...envelope,
    provider_payloads: [validBodies[0], providerPayload(BATCH_SYSTEM, second, "另一条问题")],
  });
  const wrongSystem = JSON.stringify({
    ...envelope,
    provider_payloads: [validBodies[0], providerPayload(SINGLE_SYSTEM, second)],
  });
  const mixedPayloadKeys = JSON.stringify({ ...JSON.parse(validBatch), provider_payload: providerPayload(SINGLE_SYSTEM, first) });
  const tooManyBatches = dialogueBatch(Array.from({ length: 33 }, () => [candidate(0, "user", "synthetic")]));

  for (const invalid of [
    JSON.stringify({ ...envelope, provider_payloads: [providerPayload(BATCH_SYSTEM, [first[0], mixedCardCandidate])] }),
    JSON.stringify({ ...envelope, provider_payloads: [providerPayload(BATCH_SYSTEM, [malformedTitle])] }),
    duplicateAcrossBatches,
    reverseOrder,
    inconsistentQuestion,
    wrongSystem,
    mixedPayloadKeys,
    tooManyBatches,
  ]) {
    assert.equal(isWorkCardRecallPreview(invalid), false, invalid.slice(0, 100));
    assert.doesNotMatch(renderConfirmCardHtml(invalid, ""), /data-work-card-recall/);
    assert.equal(isConfirmChoiceAllowed(invalid, "y", false), true, "invalid data must not masquerade as the sensitive batch kind");
  }
});

test("single dialogue recall and existing work-card batch previews retain their existing paths", () => {
  const single = singleDialogue(first);
  assert.equal(isWorkCardRecallPreview(single), true);
  assert.equal(isConfirmChoiceAllowed(single, "y", false), false);
  assert.match(renderWorkCardRecallReadable(single), /全部 2 条可检索用户\/助手原文候选/);
  assert.doesNotMatch(renderWorkCardRecallReadable(single), /data-recall-batch/);

  const cardSystem = BATCH_SYSTEM;
  const cardCandidate = { id: "wc-" + "b".repeat(64), title: "synthetic card", excerpt: JSON.stringify({ scope: "independent", items: [{ title: "synthetic item", requirements: ["synthetic requirement"] }] }) };
  const cardBatch = JSON.stringify({
    model_id: "0x567-flash", vendor: "0x567", endpoint, session_id: sessionId,
    provider_payloads: [providerPayload(cardSystem, [cardCandidate])],
  });
  assert.equal(isWorkCardRecallPreview(cardBatch), true);
  assert.match(renderWorkCardRecallReadable(cardBatch), /1 张卡片/);
  assert.match(renderWorkCardRecallBatch(cardBatch, 0), /synthetic requirement/);
});




test("actual Python dialogue batch preparation produces a desktop-readable frozen preview", () => {
  const desktopRoot = path.resolve(fileURLToPath(new URL("../", import.meta.url)));
  const repoRoot = path.resolve(desktopRoot, "..", "..", "..");
  const core = path.resolve(desktopRoot, "..", "agent-core");
  const python = path.join(repoRoot, ".venv", "Scripts", "python.exe");
  const fixture = String.raw`
import json, sys
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from dialogue_recall import _prepare_dialogue_batches
from work_records_luna_recall import WorkRecordsLunaRecall
question = "合成历史里的阈值是多少？"
candidates = []
for i in range(10):
    role = "user" if i % 2 == 0 else "assistant"
    excerpt = f"SYNTHETIC-DIALOGUE-{i:02d}-BEGIN\\n" + (f"合成完整原文-{i:02d}-" * 700) + f"\\nSYNTHETIC-DIALOGUE-{i:02d}-END"
    candidates.append({"id": f"msg-{i:08d}", "title": f"{role} message {i + 1}", "excerpt": excerpt})
matcher = WorkRecordsLunaRecall(SimpleNamespace(chat=lambda *_a, **_k: None), max_input_tokens=1_000_000, output_reserve_tokens=128)
payloads, preview, error = _prepare_dialogue_batches(
    question, candidates, session_id="synthetic-python-session",
    endpoint="https://synthetic.example/v1/chat/completions", matcher=matcher,
    provider_limit=1_000_000,
)
assert error is None and preview is not None and payloads is not None, error
assert len(payloads) > 1, "synthetic corpus must exercise actual batching"
print(json.dumps({"preview": preview, "candidates": candidates, "batch_count": len(payloads)}, ensure_ascii=True, separators=(",", ":")))
`;
  const env = Object.fromEntries(["PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"]
    .filter((key) => typeof process.env[key] === "string")
    .map((key) => [key, process.env[key]]));
  Object.assign(env, { PYTHONDONTWRITEBYTECODE: "1", PYTHONNOUSERSITE: "1", PYTHONUTF8: "1" });
  assert.equal(Object.keys(env).some((key) => /(?:API.?KEY|TOKEN|SECRET|CREDENTIAL)/i.test(key)), false);
  const result = spawnSync(python, ["-I", "-B", "-c", fixture, core], {
    cwd: core, env, encoding: "utf8", timeout: 15000, maxBuffer: 4 * 1024 * 1024, windowsHide: true,
  });
  assert.equal(result.error, undefined, result.error?.message);
  assert.equal(result.status, 0, result.stderr);

  const generated = JSON.parse(result.stdout);
  const preview = generated.preview as string;
  const frozen = JSON.parse(preview);
  assert.equal(isWorkCardRecallPreview(preview), true);
  assert.equal(frozen.session_id, "synthetic-python-session");
  assert.equal(frozen.endpoint, endpoint);
  assert.equal(frozen.provider_payloads.length, generated.batch_count);
  assert.ok(frozen.provider_payloads.length > 1);
  assert.equal(isConfirmChoiceAllowed(preview, "y", false), false);
  assert.equal(isConfirmChoiceAllowed(preview, "y", true), true);
  assert.equal(isConfirmChoiceAllowed(preview, "n", false), true);

  const gathered = frozen.provider_payloads.flatMap((payload: any) => {
    assert.equal(payload.messages[0].content, BATCH_SYSTEM);
    const body = JSON.parse(payload.messages[1].content);
    assert.equal(body.question, "合成历史里的阈值是多少？");
    return body.candidates;
  });
  assert.deepEqual(gathered, generated.candidates, "Python preview must retain every complete source candidate in order");

  const readable = renderWorkCardRecallReadable(preview);
  assert.match(readable, /接收方：0x567/);
  assert.match(readable, /共 10 条原文/);
  assert.match(readable, new RegExp(`${generated.batch_count} 批`));
  assert.match(readable, new RegExp(`最多 ${generated.batch_count} 次请求`));
  assert.match(readable, /可能产生费用/);
  assert.match(readable, /一次确认授权全部批次/);
  assert.doesNotMatch(readable, /SYNTHETIC-DIALOGUE-00-BEGIN/, "large original text is materialized per batch on expansion");
  for (let index = 0; index < frozen.provider_payloads.length; index += 1) {
    const batchHtml = renderWorkCardRecallBatch(preview, index);
    const body = JSON.parse(frozen.provider_payloads[index].messages[1].content);
    for (const candidate of body.candidates) {
      const encodedOriginal = JSON.stringify(candidate.excerpt)
        .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
      assert.ok(batchHtml.includes(encodedOriginal), `batch ${index} must expose the entire escaped original`);
    }
  }
  const confirm = renderConfirmCardHtml(preview, "<button>同意</button>");
  assert.equal(summarizeConfirmPreview(preview).detail, preview);
  assert.match(confirm, /data-work-card-recall/);
  assert.ok(confirm.includes(preview.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;")));
});

