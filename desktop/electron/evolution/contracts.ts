import { createHash } from "node:crypto";

export type Id = string;
export type Hash = `sha256:${string}`;
export type IsoDate = string;
export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
export type IsolationMode = "local_managed" | "protected_guest";
export type Origin = "supervisor_observed" | "broker_observed" | "guest_reported" | "candidate_reported" | "human_confirmed";
export type Action = "inspect" | "patch" | "snapshot" | "build" | "launch" | "stop" | "run_case" | "capture_scene" | "restore_scene" | "prepare_adoption" | "execute_adoption_step";
export type WorkspaceScope = { scope_id: Id; source_root: string; owned_paths: string[]; state_root: string; read_only_roots: string[] };
export type Fault = { code: ErrorCode; message: string; evidence_ids: Id[]; retryable: boolean };

export type CapabilityGrant = {
  authorization_id: Id;
  evolution_id: Id;
  candidate_id: Id | null;       // 编辑阶段可为 null；运行授权必须绑定实际 candidate
  instance_id: Id | null;        // 运行时的能力再收窄到具体 instance
  kind: "source.patch" | "candidate.build" | "candidate.launch" | "model.infer" |
        "dependency.fetch" | "scene.capture" | "artifact.export" |
        "source.merge" | "release.activate" | "data.migrate";
  constraints: { [key: string]: JsonValue };
  expires_at: IsoDate;
  budget_id: Id | null;
  human_confirmation_id: Id | null;
};

export type ControlRequest = {
  protocol: "evolution/1.0";
  operation_id: Id;
  evolution_id: Id;
  conversation_id: Id;
  host_run_id: Id;
  expected_state_revision: number;
  authorization_id: Id;
  action: "inspect" | "patch" | "snapshot" | "build" | "launch" | "stop" |
          "run_case" | "capture_scene" | "restore_scene" | "prepare_adoption" |
          "execute_adoption_step";
  payload: { [key: string]: JsonValue };
};
export type ErrorCode = "invalid_request" | "permission_denied" | "scope_conflict" |
  "stale_revision" | "identity_mismatch" | "protocol_incompatible" |
  "unsupported_capability" | "isolation_unavailable" | "environment_not_ready" |
  "budget_exceeded" | "build_failed" | "start_failed" | "case_failed" |
  "capture_unavailable" | "scene_stale" | "restore_ambiguous" |
  "timeout" | "cancelled" | "outcome_unknown" | "adoption_blocked" |
  "data_incompatible" | "recovery_required";
export type ControlResult = {
  operation_id: Id;
  evolution_id: Id;
  state_revision: number;
  status: "running" | "succeeded" | "failed" | "cancelled" | "unknown";
  result: JsonValue;
  error: null | { code: ErrorCode; message: string; evidence_ids: Id[]; retryable: boolean };
};
export type EventKind = "workspace.changed" | "snapshot.created" |
  "build.started" | "build.completed" | "build.failed" |
  "runtime.starting" | "runtime.ready" | "runtime.exited" |
  "case.started" | "case.completed" | "scene.captured" | "scene.restored" |
  "budget.denied" | "operation.cancelled" | "adoption.changed" | "recovery.required";
export type EvolutionEvent = {
  protocol: "evolution/1.0";
  event_id: Id;
  journal_sequence: number;
  evolution_id: Id;
  state_revision: number;
  operation_id: Id | null;
  candidate_id: Id | null;
  instance_id: Id | null;
  instance_sequence: number | null;
  occurred_at: IsoDate;
  received_at: IsoDate;
  origin: Origin;
  kind: EventKind;               // 必须属于协商版本的事件白名单
  payload: { [key: string]: JsonValue };
};

export type ArtifactRef = {
  artifact_id: Id;
  sha256: Hash;
  byte_length: number;
  media_type: string;
  origin: Origin;
};
export type RuntimeKind = "python" | "node" | "electron" | "executable";
export type RuntimeSpec = {
  runtime_id: Id;
  kind: RuntimeKind;
  version: string;
  platform: "win32";
  arch: "x64" | "arm64";
  use: "build" | "run" | "both";
  distribution: ArtifactRef;         // 批准来源的实际运行时，不借 stable
};
export type ProcessRecipe = {
  component_id: Id;
  role: "agent" | "web" | "desktop" | "service" | "adapter";
  runtime_id: Id;
  entrypoint: string;                // guest_workspace 内的相对路径
  argv: string[];
  cwd: string;                       // guest_workspace 内的相对目录
  environment_profile_id: Id;        // schema 限定的 guest env 配置
  depends_on: Id[];                  // component_id 构成无环图
  required: boolean;
  readiness: "protocol" | "process_alive" | "artifact_probe";
};
export type CandidateDescriptor = {
  descriptor_version: "1.0";
  source_snapshot_id: Hash;
  build_recipe: ArtifactRef;         // guest 内解释的描述，非宿主可执行脚本
  launch_recipe: { processes: ProcessRecipe[] };
  runtimes: RuntimeSpec[];
  dependency_lock: ArtifactRef[];    // 无依赖也必须显式为空
  adapter: ArtifactRef;
  data_contract_digest: Hash;       // 无持久化为规范化 {kind:"none"} 的摘要
  mode: "live_dev" | "sealed_build";
  isolation_mode: IsolationMode;
  required_capabilities: string[];
};
export type RuntimeIdentity = {
  candidate_id: Hash;
  instance_id: Id;
  source_snapshot_id: Hash;
  build_fingerprint: Hash;
  protocol: "evolution/1.0";
  boot_challenge_response: string;  // 私有通道字段，不写公共事件/截图
  capabilities: string[];
};

export type CandidatePhase = "draft" | "snapshotting" | "preparing" | "building" | "built" |
  "starting" | "ready" | "stopping" | "stopped" | "failed" | "quarantined";
export type CaseVerdict = "not_run" | "running" | "passed" | "failed" | "inconclusive" | "cancelled";
export type SourceDelivery = "uncommitted" | "committed" | "merged";
export type RuntimeAdoption = "not_requested" | "prepared" | "confirmed" | "switching" |
  "observing" | "active" | "rolled_back" | "blocked";
export type ReviewStatus = "not_run" | "passed" | "failed" | "incomplete" | "risk_accepted";
export type DataMigration = "not_required" | "planned" | "rehearsed" | "running" |
  "validated" | "reversed" | "blocked";

export type ModelRequest = {
  instance_id: Id;
  case_id: Id | null;
  request_id: Id;
  authorization_id: Id;
  model_alias: string;
  input_artifact: ArtifactRef;
  tool_schema_artifact: ArtifactRef | null;
  max_output_tokens: number;
};
export type ModelReceipt = {
  request_id: Id;
  provider_request_id: string | null;
  resolved_model: string;
  request_digest: Hash;
  response_digest: Hash | null;
  tool_call_ids: string[];
  consumed_artifact_ids: Id[];
  image_input_consumed: boolean;
  status: "completed" | "failed" | "unknown" | "denied";
  evidence_ids: Id[];
};

export type CaseSpec = {
  case_id: Id;
  candidate_id: Hash;
  instance_id: Id;
  kind: "tool_model_roundtrip" | "core_path_replacement" | "native_startup" |
        "scene_roundtrip" | "recovery_drill" | "isolation_probe" | "data_rehearsal";
  fixture_id: Id;
  fixture_digest: Hash;
  case_nonce: string;
  required_capabilities: string[];
  oracle_id: Id;                  // 已登记且在可信版本中的结果判断器，不是候选脚本
  timeout_ms: number;
  grant_ids: Id[];
};
export type CaseResult = {
  case_id: Id;
  candidate_id: Hash;
  instance_id: Id;
  verdict: CaseVerdict;
  checks: { check_id: Id; verdict: CaseVerdict; origin: Origin; evidence_ids: Id[] }[];
  model_receipt_ids: Id[];
  output_artifacts: ArtifactRef[];
  started_at: IsoDate;
  ended_at: IsoDate | null;
};

export type Rect = { x: number; y: number; width: number; height: number };
export type LocatorHint = {
  kind: "stable_key" | "role_name" | "structural_path" | "text_digest";
  value: string;
  frame_path: string[];
};
export type Annotation = {
  kind: "point" | "rectangle" | "stroke";
  points: { x: number; y: number }[];
  note: string;
};
export type SceneContext = {
  scene_id: Id;
  evolution_id: Id;
  conversation_id: Id;
  candidate_id: Hash;
  instance_id: Id;
  surface_id: Id;
  surface_kind: "web_surface" | "native_surface";
  document_generation: number;
  captured_at: IsoDate;
  route: { origin_label: string; pathname: string; hash_route: string; redacted_query_keys: string[] };
  viewport: { width: number; height: number; dpr: number; zoom: number;
              visual_offset_x: number; visual_offset_y: number };
  scroll: { x: number; y: number };
  selection: null | { hints: LocatorHint[]; rect: Rect; frame_path: string[];
                      availability: "element" | "region_only" };
  annotations: Annotation[];
  screenshot: null | ArtifactRef;
  capture_origin: "stable_webcontents" | "guest_observer" | "user_upload";
  unavailable: string[];
  privacy: { redaction_policy_version: string; redacted: boolean; disclosure_grant_id: Id | null };
};
export type RestoreResult = {
  source_scene_id: Id;
  target_instance_id: Id;
  target_surface_id: Id;
  target_generation: number;
  match: "exact" | "approximate" | "missing" | "inaccessible";
  candidates: { hints: LocatorHint[]; rect: Rect }[];
  restored_scene_id: Id | null;
  reason: string;
};

export type AdoptionStep = {
  step_id: Id;
  kind: "source_commit" | "source_merge" | "sandbox_sync" | "stage_release" |
        "rehearse_data" | "quiesce_old_host" | "migrate_data" |
        "activate_release" | "observe_release" | "recover_release";
  prerequisites: Id[];
  authorization_kind: CapabilityGrant["kind"] | "explicit_git_confirmation";
  reversible: boolean;
  data_loss_possible: boolean;
};
export type AdoptionPlan = {
  plan_id: Id;
  plan_hash: Hash;
  evolution_id: Id;
  candidate_id: Hash;
  release_id: Hash;
  source_snapshot_id: Hash;
  build_fingerprint: Hash;
  expected_stable_baseline: Hash;
  expected_stable_data_revision: string;
  owned_paths: string[];
  pre_existing_paths: string[];
  unrelated_paths: string[];
  selected_delivery: "none" | "owned_changes" | "whole_branch_explicit";
  validation_evidence_ids: Id[];
  open_checks: { name: string; status: ReviewStatus; evidence_ids: Id[] }[];
  steps: AdoptionStep[];
  rollback_release_id: Hash;
  data_migration: {
    required: boolean;
    source_schema: string;
    target_schema: string;
    rehearsal_fixture_digest: Hash | null;
    reverse_supported: boolean;
    reverse_evidence_ids: Id[];
  };
  expires_at: IsoDate;
};

export const MAX_CONTROL_BYTES = 65_536;
const MAX_STRING_BYTES = 65_536;
const MAX_JSON_DEPTH = 64;
const MAX_COLLECTION_ITEMS = 4_096;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const HASH_RE = /^sha256:[0-9a-f]{64}$/;
const ERROR_CODES = new Set<ErrorCode>([
  "invalid_request", "permission_denied", "scope_conflict", "stale_revision", "identity_mismatch",
  "protocol_incompatible", "unsupported_capability", "isolation_unavailable", "environment_not_ready",
  "budget_exceeded", "build_failed", "start_failed", "case_failed", "capture_unavailable",
  "scene_stale", "restore_ambiguous", "timeout", "cancelled", "outcome_unknown", "adoption_blocked",
  "data_incompatible", "recovery_required",
]);
const ACTIONS = new Set<Action>([
  "inspect", "patch", "snapshot", "build", "launch", "stop", "run_case", "capture_scene",
  "restore_scene", "prepare_adoption", "execute_adoption_step",
]);

export class ContractError extends Error {
  readonly code: ErrorCode;
  readonly field?: string;
  constructor(code: ErrorCode, message: string, field?: string) {
    super(message); this.name = "ContractError"; this.code = code; this.field = field;
  }
}
function invalid(message: string, field?: string): never { throw new ContractError("invalid_request", message, field); }
function record(value: unknown, field: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) invalid("expected object", field);
  const proto = Object.getPrototypeOf(value);
  if (proto !== Object.prototype && proto !== null) invalid("expected plain JSON object", field);
  for (const key of Reflect.ownKeys(value)) {
    if (typeof key !== "string") invalid("symbol key is not JSON", field);
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (!descriptor?.enumerable || !("value" in descriptor)) invalid("non-enumerable/accessor property is not JSON", `${field}.${key}`);
  }
  return value as Record<string, unknown>;
}
function exactKeys(value: Record<string, unknown>, allowed: readonly string[], required: readonly string[], field: string): void {
  const allow = new Set(allowed);
  for (const key of Object.keys(value)) if (!allow.has(key)) invalid("unknown field", `${field}.${key}`);
  for (const key of required) if (!Object.hasOwn(value, key)) invalid("missing field", `${field}.${key}`);
}
function validString(value: unknown, field: string, maxBytes = MAX_STRING_BYTES): asserts value is string {
  if (typeof value !== "string" || Buffer.byteLength(value, "utf8") > maxBytes || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)) invalid("expected bounded Unicode string", field);
}
function validUuid(value: unknown, field: string): asserts value is string {
  validString(value, field, 36); if (!UUID_RE.test(value)) invalid("expected canonical lowercase UUID", field);
}
function validHash(value: unknown, field: string): asserts value is string {
  validString(value, field, 71); if (!HASH_RE.test(value)) invalid("expected sha256 lowercase hash", field);
}
function assertJsonValue(value: unknown, field = "$", depth = 0, ancestors = new Set<object>()): asserts value is JsonValue {
  if (depth > MAX_JSON_DEPTH) invalid("JSON nesting exceeds limit", field);
  if (value === null || typeof value === "boolean") return;
  if (typeof value === "string") { validString(value, field); return; }
  if (typeof value === "number") { if (!Number.isSafeInteger(value)) invalid("A protocol numbers must be safe integers", field); return; }
  if (typeof value !== "object") invalid("value is not JSON", field);
  if (ancestors.has(value)) invalid("cyclic value is not JSON", field);
  ancestors.add(value);
  if (Array.isArray(value)) {
    if (Object.getPrototypeOf(value) !== Array.prototype) invalid("array must use the standard prototype", field);
    if (value.length > MAX_COLLECTION_ITEMS) invalid("array exceeds item limit", field);
    for (const key of Reflect.ownKeys(value)) {
      if (key === "length") continue;
      if (typeof key !== "string" || !/^(?:0|[1-9][0-9]*)$/.test(key) || Number(key) >= value.length) invalid("array has a non-JSON property", field);
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (!descriptor?.enumerable || !("value" in descriptor)) invalid("array accessor is not JSON", `${field}[${key}]`);
    }
    for (let i=0;i<value.length;i++) { if (!Object.hasOwn(value,i)) invalid("sparse array",`${field}[${i}]`); assertJsonValue(value[i],`${field}[${i}]`,depth+1,ancestors); }
  } else {
    const object=record(value,field), keys=Object.keys(object);
    if (keys.length>MAX_COLLECTION_ITEMS) invalid("object exceeds field limit",field);
    for(const key of keys){validString(key,`${field} key`);assertJsonValue(object[key],`${field}.${key}`,depth+1,ancestors);}
  }
  ancestors.delete(value);
}
function quote(value: string): string { return JSON.stringify(value); }
function canonicalValue(value: JsonValue): string {
  if(value===null||typeof value==="boolean"||typeof value==="number")return String(value);
  if(typeof value==="string")return quote(value);
  if(Array.isArray(value))return `[${value.map(canonicalValue).join(",")}]`;
  const object=value as Record<string,JsonValue>, keys=Object.keys(object).sort();
  return `{${keys.map(k=>`${quote(k)}:${canonicalValue(object[k])}`).join(",")}}`;
}
export function canonicalJson(value: unknown): string { assertJsonValue(value); return canonicalValue(value); }
export function contentHash(value: unknown): Hash { return `sha256:${createHash("sha256").update(canonicalJson(value),"utf8").digest("hex")}`; }
function safePath(value: unknown, field: string): asserts value is string {
  validString(value,field,4_096);
  if(!value||value.startsWith("/")||value.includes("\\")||value.includes(":")||value.includes("\0"))invalid("expected safe relative POSIX path",field);
  if(value.split("/").some(p=>!p||p==="."||p===".."))invalid("path traversal or ambiguous component",field);
}
function exactPayload(value:unknown,allowed:readonly string[],required:readonly string[],action:string):Record<string,unknown>{
  const p=record(value,`payload(${action})`);exactKeys(p,allowed,required,`payload(${action})`);assertJsonValue(p,`payload(${action})`);return p;
}
function bounded(value:unknown,what:string):void{if(Buffer.byteLength(canonicalJson(value),"utf8")>MAX_CONTROL_BYTES)throw new ContractError("budget_exceeded",`${what} exceeds 64 KiB`);}
export function validateActionPayload(action:Action,payloadValue:unknown):Record<string,unknown>{
  if(!ACTIONS.has(action))invalid("unknown action","action");
  const payload=record(payloadValue,"payload");assertJsonValue(payload,"payload");bounded(payload,"payload");
  if(action==="inspect"){
    const q=payload.query;
    if(q==="summary"||q==="source_list")exactPayload(payload,["query"],["query"],action);
    else if(q==="operation"){const p=exactPayload(payload,["query","operation_id"],["query","operation_id"],action);validUuid(p.operation_id,"payload.operation_id");}
    else if(q==="source_text"){const p=exactPayload(payload,["query","path"],["query","path"],action);safePath(p.path,"payload.path");}
    else invalid("unsupported inspect query","payload.query");
    return payload;
  }
  if(action==="patch"){
    const p=exactPayload(payload,["path","expected_before_hash","content_utf8"],["path","expected_before_hash","content_utf8"],action);safePath(p.path,"payload.path");
    if(p.expected_before_hash!=="missing")validHash(p.expected_before_hash,"payload.expected_before_hash");validString(p.content_utf8,"payload.content_utf8",32_768);return payload;
  }
  if(action==="snapshot"){
    const p=exactPayload(payload,["workspace_revision","include_paths"],["workspace_revision","include_paths"],action);
    if(typeof p.workspace_revision!=="number"||!Number.isSafeInteger(p.workspace_revision)||p.workspace_revision<0)invalid("invalid workspace revision","payload.workspace_revision");
    if(!Array.isArray(p.include_paths)||p.include_paths.length===0||p.include_paths.length>256)invalid("invalid include_paths","payload.include_paths");
    const names=new Set<string>();p.include_paths.forEach((value,index)=>{safePath(value,`payload.include_paths[${index}]`);const key=value.toLocaleLowerCase("en-US");if(names.has(key))invalid("duplicate/case-colliding include path",`payload.include_paths[${index}]`);names.add(key);});return payload;
  }
  if(action==="build"){
    const p=exactPayload(payload,["source_snapshot_id","descriptor_artifact_id","build_input_digest","isolation_mode"],["source_snapshot_id","descriptor_artifact_id","build_input_digest","isolation_mode"],action);
    validHash(p.source_snapshot_id,"payload.source_snapshot_id");validUuid(p.descriptor_artifact_id,"payload.descriptor_artifact_id");validHash(p.build_input_digest,"payload.build_input_digest");
    if(p.isolation_mode!=="local_managed"&&p.isolation_mode!=="protected_guest")invalid("invalid isolation mode","payload.isolation_mode");return payload;
  }
  if(action==="launch"){
    const p=exactPayload(payload,["candidate_id","profile_fixture_id"],["candidate_id","profile_fixture_id"],action);validHash(p.candidate_id,"payload.candidate_id");validUuid(p.profile_fixture_id,"payload.profile_fixture_id");return payload;
  }
  if(action==="stop"){const p=exactPayload(payload,["instance_id"],["instance_id"],action);validUuid(p.instance_id,"payload.instance_id");return payload;}
  if(action==="run_case"){const p=exactPayload(payload,["instance_id","case_spec_artifact_id"],["instance_id","case_spec_artifact_id"],action);validUuid(p.instance_id,"payload.instance_id");validUuid(p.case_spec_artifact_id,"payload.case_spec_artifact_id");return payload;}
  // Later slices parse the envelope but cannot dispatch these actions in A.
  return payload;
}
export function parseControlRequest(value:unknown):ControlRequest{
  assertJsonValue(value);bounded(value,"control request");const r=record(value,"request");
  const keys=["protocol","operation_id","evolution_id","conversation_id","host_run_id","expected_state_revision","authorization_id","action","payload"];
  exactKeys(r,keys,keys,"request");if(r.protocol!=="evolution/1.0")throw new ContractError("protocol_incompatible","unsupported protocol version","protocol");
  for(const k of ["operation_id","evolution_id","conversation_id","host_run_id","authorization_id"] as const)validUuid(r[k],k);
  if(typeof r.expected_state_revision!=="number"||!Number.isSafeInteger(r.expected_state_revision)||r.expected_state_revision<0)invalid("invalid state revision","expected_state_revision");
  if(typeof r.action!=="string"||!ACTIONS.has(r.action as Action))invalid("unknown action","action");
  const a=r.action as Action;
  if(["inspect","patch","snapshot","build","launch","stop","run_case"].includes(a))validateActionPayload(a,r.payload);else record(r.payload,"payload");
  return r as unknown as ControlRequest;
}
export function parseControlResult(value:unknown):ControlResult{
  assertJsonValue(value);bounded(value,"control result");const r=record(value,"result");
  const keys=["operation_id","evolution_id","state_revision","status","result","error"];exactKeys(r,keys,keys,"result");
  validUuid(r.operation_id,"operation_id");validUuid(r.evolution_id,"evolution_id");
  if(typeof r.state_revision!=="number"||!Number.isSafeInteger(r.state_revision)||r.state_revision<0)invalid("invalid state revision","state_revision");
  if(!["running","succeeded","failed","cancelled","unknown"].includes(String(r.status)))invalid("invalid result status","status");
  if(r.error!==null){const e=record(r.error,"error"),ek=["code","message","evidence_ids","retryable"];exactKeys(e,ek,ek,"error");
    if(typeof e.code!=="string"||!ERROR_CODES.has(e.code as ErrorCode))invalid("unknown error code","error.code");validString(e.message,"error.message",4_096);
    if(!Array.isArray(e.evidence_ids)||e.evidence_ids.length>256)invalid("invalid evidence_ids","error.evidence_ids");e.evidence_ids.forEach((id,i)=>validUuid(id,`error.evidence_ids[${i}]`));if(typeof e.retryable!=="boolean")invalid("retryable must be boolean","error.retryable");}
  return r as unknown as ControlResult;
}
function scanJson(text:string):void{
  let i=0;const fail=():never=>{throw new ContractError("invalid_request","malformed or duplicate-key JSON");};
  const ws=()=>{while(text[i]===" "||text[i]==="\t"||text[i]==="\r"||text[i]==="\n")i++;};
  const str=():string=>{if(text[i]!=="\"")return fail();const start=i++;
    while(i<text.length){const c=text.charCodeAt(i);if(c<0x20)return fail();if(text[i]==="\""){i++;try{return JSON.parse(text.slice(start,i)) as string;}catch{return fail();}}
      if(text[i]==="\\"){i++;if(i>=text.length)return fail();if(text[i]==="u"){if(!/^[0-9a-fA-F]{4}$/.test(text.slice(i+1,i+5)))return fail();i+=5;}else{if(!'"\\/bfnrt'.includes(text[i]))return fail();i++;}}else i++;}return fail();};
  const val=(d:number):void=>{if(d>MAX_JSON_DEPTH)invalid("JSON nesting exceeds limit");ws();if(text[i]==="\""){str();return;}
    if(text[i]==="{"){i++;ws();const seen=new Set<string>();if(text[i]==="}"){i++;return;}let n=0;while(true){ws();const k=str();if(seen.has(k))invalid("duplicate JSON object key",k);seen.add(k);if(++n>MAX_COLLECTION_ITEMS)invalid("object exceeds field limit");ws();if(text[i++]!==":")return fail();val(d+1);ws();if(text[i]==="}"){i++;return;}if(text[i++]!==",")return fail();}}
    if(text[i]==="["){i++;ws();if(text[i]==="]"){i++;return;}let n=0;while(true){if(++n>MAX_COLLECTION_ITEMS)invalid("array exceeds item limit");val(d+1);ws();if(text[i]==="]"){i++;return;}if(text[i++]!==",")return fail();}}
    for(const lit of ["true","false","null"])if(text.startsWith(lit,i)){i+=lit.length;return;}
    const m=/-?(?:0|[1-9][0-9]*)/.exec(text.slice(i));if(m){if(!Number.isSafeInteger(Number(m[0])))invalid("unsafe JSON integer");i+=m[0].length;if(text[i]==="."||text[i]==="e"||text[i]==="E")invalid("A protocol numbers must be integers");return;}return fail();};
  val(0);ws();if(i!==text.length)fail();
}
export function parseJsonBytes(bytes:Buffer|Uint8Array):unknown{
  if(!(bytes instanceof Uint8Array))invalid("expected byte input");if(bytes.byteLength>MAX_CONTROL_BYTES)throw new ContractError("budget_exceeded","JSON message exceeds 64 KiB");
  let text:string;try{text=new TextDecoder("utf-8",{fatal:true}).decode(bytes);}catch{invalid("JSON is not valid UTF-8");}scanJson(text);
  let v:unknown;try{v=JSON.parse(text);}catch{invalid("malformed JSON");}assertJsonValue(v);return v;
}
