import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
const { parseControlRequest, parseControlResult, validateActionPayload, canonicalJson, contentHash, parseJsonBytes } = require("../../../artifacts/evolution-control-build/contracts.js");
const { encodeFrame, FrameDecoder } = require("../../../artifacts/evolution-control-build/codec.js");
const casesPath = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../protocol/evolution/v1-cases.json");
const vectors = JSON.parse(readFileSync(casesPath, "utf8"));
const validators = { control_request: parseControlRequest, control_result: parseControlResult, action_payload: (value) => validateActionPayload(value.action, value.payload), json_bytes: (value) => parseControlRequest(parseJsonBytes(Buffer.from(value.utf8, "utf8"))) };

test("shared v1 fixtures have the same accept/reject decision and error code", () => {
  for (const sample of vectors.cases) {
    if (sample.validator === "frame") {
      const decoder = new FrameDecoder();
      assert.throws(() => decoder.push(Buffer.from(sample.input.hex, "hex")), (error) => error.code === sample.expected.error_code, sample.id);
      continue;
    }
    const validate = validators[sample.validator];
    assert.ok(validate, `unknown validator ${sample.validator}`);
    if (sample.expected.accept) validate(sample.input);
    else assert.throws(() => validate(sample.input), (error) => error.code === sample.expected.error_code, sample.id);
  }
});

test("canonical UTF-8 and content hashes match fixed cross-language vectors", () => {
  for (const vector of vectors.canonical_vectors) {
    assert.equal(canonicalJson(vector.value), vector.canonical_utf8);
    assert.equal(contentHash(vector.value), vector.sha256);
  }
});

test("control requests accept native legacy conversation ids while keeping protocol identities UUID-only", () => {
  const request = vectors.cases.find((item) => item.id === "valid_inspect").input;
  const legacy = { ...request, conversation_id: "20261026-1a2b3c4d" };
  assert.equal(parseControlRequest(legacy).conversation_id, "20261026-1a2b3c4d");
  assert.throws(() => parseControlRequest({ ...legacy, conversation_id: "20260230-1a2b3c4d" }), (error) => error.code === "invalid_request");
  assert.throws(() => parseControlRequest({ ...legacy, evolution_id: "20261026-1a2b3c4d" }), (error) => error.code === "invalid_request");
});

test("frame codec accepts split and coalesced frames without losing boundaries", () => {
  const one = encodeFrame({ n: 1 });
  const two = encodeFrame({ n: 2 });
  const decoder = new FrameDecoder();
  assert.deepEqual(decoder.push(one.subarray(0, 2)), []);
  assert.deepEqual(decoder.push(Buffer.concat([one.subarray(2), two])), [{ n: 1 }, { n: 2 }]);
  decoder.finish();
});

test("frame codec rejects incomplete frames and invalid UTF-8", () => {
  const frame = encodeFrame({ ok: true });
  const truncated = new FrameDecoder();
  truncated.push(frame.subarray(0, frame.length - 1));
  assert.throws(() => truncated.finish(), (error) => error.code === "invalid_request");
  const invalidUtf8 = Buffer.from([0, 0, 0, 1, 0xff]);
  assert.throws(() => new FrameDecoder().push(invalidUtf8), (error) => error.code === "invalid_request");
});


test("strict validators reject accessors, inherited objects and non-JSON array properties", () => {
  const request = vectors.cases.find((item) => item.id === "valid_inspect").input;
  const inherited = Object.assign(Object.create({ injected: true }), request);
  assert.throws(() => parseControlRequest(inherited), (error) => error.code === "invalid_request");
  const accessor = { ...request };
  Object.defineProperty(accessor, "hidden", { enumerable: true, get() { throw new Error("must not run"); } });
  assert.throws(() => parseControlRequest(accessor), (error) => error.code === "invalid_request");
  const array = [1, 2];
  Object.defineProperty(array, "extra", { enumerable: false, value: 3 });
  assert.throws(() => canonicalJson(array), (error) => error.code === "invalid_request");
});

test("canonical content hashing supports manifests larger than one control frame", () => {
  const manifest = { files: Array.from({ length: 4096 }, (_, index) => ({ path: `src/${index}.ts`, digest: "a".repeat(96) })) };
  assert.ok(Buffer.byteLength(canonicalJson(manifest), "utf8") > vectors.max_control_bytes);
  assert.match(contentHash(manifest), /^sha256:[0-9a-f]{64}$/);
});


test("raw JSON and outgoing frames enforce byte budgets before parsing", () => {
  assert.throws(() => parseJsonBytes(Buffer.alloc(vectors.max_control_bytes + 1)), (error) => error.code === "budget_exceeded");
  assert.throws(() => encodeFrame({ body: "x".repeat(vectors.max_control_bytes) }), (error) => error.code === "budget_exceeded");
});
