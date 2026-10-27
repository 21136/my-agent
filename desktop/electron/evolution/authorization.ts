import { randomUUID } from "node:crypto";
import { ContractError, canonicalJson, type CapabilityGrant, type ControlRequest } from "./contracts";
import type { HostBinding } from "./binding";
import { BindingStore } from "./binding";
import { JournalStore, type StateMutation } from "./journal";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const HASH = /^sha256:[0-9a-f]{64}$/;
type GrantKind = CapabilityGrant["kind"];
type StoredGrant = CapabilityGrant & { revoked: boolean; derived_from: string | null };
function fail(code: ConstructorParameters<typeof ContractError>[0], message: string): never { throw new ContractError(code, message); }
function plain(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value) || Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null) fail("invalid_request", `${label} must be a plain object`);
  return value as Record<string, unknown>;
}
function exact(value: Record<string, unknown>, keys: string[], label: string): void {
  if (Object.keys(value).length !== keys.length || keys.some((key) => !Object.hasOwn(value, key))) fail("invalid_request", `${label} fields do not match its capability schema`);
}
function uuid(value: unknown, label: string): string {
  if (typeof value !== "string" || !UUID.test(value)) fail("invalid_request", `${label} must be a UUID`);
  return value;
}
function hash(value: unknown, label: string): string {
  if (typeof value !== "string" || !HASH.test(value)) fail("invalid_request", `${label} must be a sha256 hash`);
  return value;
}
function safeRelativePath(value: unknown, label: string): string {
  if (typeof value !== "string" || !value || value.startsWith("/") || value.includes("\\") || value.includes(":") || value.split("/").some((p) => !p || p === "." || p === "..")) fail("invalid_request", `${label} must be a safe relative POSIX path`);
  return value;
}
function isoDate(value: unknown): string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(Date.parse(value))) fail("invalid_request", "expires_at must be an ISO-8601 timestamp with a zone");
  return value;
}
function validateConstraints(kind: GrantKind, value: unknown): Record<string, unknown> {
  const constraints = plain(value, "grant constraints");
  canonicalJson(constraints);
  if (kind === "source.patch") {
    exact(constraints, ["owned_paths", "expected_before_hashes"], "source.patch constraints");
    const paths = constraints.owned_paths;
    if (!Array.isArray(paths) || paths.length < 1 || paths.length > 256) fail("invalid_request", "owned_paths must contain 1..256 paths");
    const seen = new Set<string>();
    paths.forEach((item, index) => { const p=safeRelativePath(item,`owned_paths[${index}]`); const key=p.toLowerCase(); if(seen.has(key))fail("invalid_request","owned_paths contains duplicates or case collisions"); seen.add(key); });
    const hashes = plain(constraints.expected_before_hashes, "expected_before_hashes");
    if (Object.keys(hashes).length !== paths.length) fail("invalid_request", "every owned path must have one expected before hash");
    for (const item of paths) {
      if (!Object.hasOwn(hashes, item)) fail("invalid_request", "expected hash map must exactly match owned_paths");
      if (hashes[item] !== "missing") hash(hashes[item], `expected_before_hashes.${item}`);
    }
    return constraints;
  }
  if (kind === "candidate.build") {
    exact(constraints, ["source_snapshot_id", "build_input_digest", "isolation_mode"], "candidate.build constraints");
    hash(constraints.source_snapshot_id, "source_snapshot_id"); hash(constraints.build_input_digest, "build_input_digest");
    if (constraints.isolation_mode !== "local_managed" && constraints.isolation_mode !== "protected_guest") fail("invalid_request", "invalid isolation_mode");
    return constraints;
  }
  if (kind === "candidate.launch") {
    exact(constraints, ["candidate_id", "profile_fixture_id", "isolation_mode"], "candidate.launch constraints");
    hash(constraints.candidate_id, "candidate_id"); uuid(constraints.profile_fixture_id, "profile_fixture_id");
    if (constraints.isolation_mode !== "local_managed" && constraints.isolation_mode !== "protected_guest") fail("invalid_request", "invalid isolation_mode");
    return constraints;
  }
  fail("unsupported_capability", `grant schema is not implemented in slice A: ${kind}`);
}

export class GrantStore {
  readonly journal: JournalStore;
  private readonly bindings: BindingStore;
  private readonly clock: () => number;
  private grants = new Map<string, StoredGrant>();

  constructor(journal: JournalStore, clock: () => number = Date.now) {
    this.journal = journal; this.bindings = new BindingStore(journal); this.clock = clock; this.refresh();
  }

  private refresh(): void {
    const next = new Map<string, StoredGrant>();
    for (const event of this.journal.readEvents()) {
      if (event.kind === "grant.created") {
        const item=plain(event.value,"persisted grant") as unknown as StoredGrant;
        if (typeof item.authorization_id !== "string" || !UUID.test(item.authorization_id) || next.has(item.authorization_id)) fail("recovery_required","journal contains duplicate or malformed grant id");
        next.set(item.authorization_id,structuredClone(item));
      } else if (event.kind === "grant.instance_bound") {
        const value=plain(event.value,"instance grant binding");
        const parent=next.get(String(value.parent_authorization_id));
        const child=plain(value.grant,"derived grant") as unknown as StoredGrant;
        if (!parent || child.derived_from!==parent.authorization_id || child.instance_id===null || next.has(child.authorization_id)) fail("recovery_required","journal contains invalid derived grant");
        next.set(child.authorization_id,structuredClone(child));
      } else if (event.kind === "grant.revoked") {
        const value=plain(event.value,"grant revocation");
        const current=next.get(String(value.authorization_id));
        if (!current) fail("recovery_required","journal revokes a missing grant");
        current.revoked=true;
      }
    }
    this.grants=next;
  }

  createConfirmed(input: { binding: HostBinding; kind: string; constraints: Record<string, unknown>; confirmation_id: string; expires_at: string }): string {
    const binding=this.bindings.require(input.binding.evolution_id,input.binding.conversation_id);
    this.refresh();
    if([...this.grants.values()].some((grant)=>grant.human_confirmation_id===input.confirmation_id))fail("identity_mismatch","human confirmation id has already been consumed");
    if(binding.status!=="bound")fail("permission_denied","cannot authorize a frozen binding");
    if(binding.state_revision!==input.binding.state_revision)fail("stale_revision","confirmation was created for a stale binding revision");
    uuid(input.confirmation_id,"confirmation_id");
    const expires=isoDate(input.expires_at);
    if(Date.parse(expires)<=this.clock())fail("permission_denied","authorization is already expired");
    const supported=["source.patch","candidate.build","candidate.launch"];
    if(typeof input.kind!=="string"||!supported.includes(input.kind))fail("unsupported_capability",`grant kind is not available in slice A: ${input.kind}`);
    const kind=input.kind as GrantKind;
    const checked=validateConstraints(kind,input.constraints);
    const candidateId=kind==="candidate.launch"?String(checked.candidate_id):null;
    const grant:StoredGrant={authorization_id:randomUUID(),evolution_id:binding.evolution_id,candidate_id:candidateId,instance_id:null,kind,constraints:checked as CapabilityGrant["constraints"],expires_at:expires,budget_id:null,human_confirmation_id:input.confirmation_id,revoked:false,derived_from:null};
    const mutation:StateMutation={kind:"grant.created",evolution_id:binding.evolution_id,value:grant as unknown as Record<string,unknown>};
    this.journal.appendMutation(mutation);this.refresh();return grant.authorization_id;
  }

  bindInstance(authorizationId:string,instanceId:string):string {
    uuid(authorizationId,"authorization_id");uuid(instanceId,"instance_id");this.refresh();
    const parent=this.grants.get(authorizationId);
    if(!parent||parent.kind!=="candidate.launch")fail("permission_denied","cannot derive an instance grant without a recorded launch authorization");
    const child:StoredGrant={...structuredClone(parent),authorization_id:randomUUID(),instance_id:instanceId,revoked:false,derived_from:parent.authorization_id};
    const value={parent_authorization_id:parent.authorization_id,grant:child};
    this.journal.appendMutation({kind:"grant.instance_bound",evolution_id:parent.evolution_id,value});
    this.refresh();return child.authorization_id;
  }

  require(request:ControlRequest,binding:HostBinding):void {
    if(binding.evolution_id!==request.evolution_id||binding.conversation_id!==request.conversation_id)fail("permission_denied","request does not belong to this binding");
    if(request.action==="inspect")return;
    this.refresh();
    const grant=this.grants.get(request.authorization_id);
    if(request.action==="stop"){
      const instanceId=(request.payload as Record<string,unknown>).instance_id;
      if(!grant||grant.evolution_id!==binding.evolution_id||grant.kind!=="candidate.launch"||grant.instance_id!==instanceId||grant.derived_from===null)fail("permission_denied","stop is limited to the exact instance owned by this binding");
      return; // revocation/expiry never prevents reducing risk for this owned instance.
    }
    if(!grant||grant.evolution_id!==binding.evolution_id||grant.revoked||grant.instance_id!==null||Date.parse(grant.expires_at)<=this.clock())fail("permission_denied","authorization is missing, revoked, expired, or bound to another instance");
    const payload=request.payload as Record<string,unknown>;
    if(request.action==="patch"&&grant.kind==="source.patch"){
      const paths=grant.constraints.owned_paths as string[];const expected=grant.constraints.expected_before_hashes as Record<string,unknown>;
      if(!paths.includes(String(payload.path))||expected[payload.path as string]!==payload.expected_before_hash)fail("permission_denied","patch path or before hash exceeds the confirmed grant");return;
    }
    if(request.action==="snapshot"&&grant.kind==="source.patch"){
      const paths=grant.constraints.owned_paths as string[];const include=payload.include_paths as string[];
      if(!include.every((item)=>paths.includes(item)))fail("permission_denied","snapshot includes a path outside the confirmed scope");return;
    }
    if(request.action==="build"&&grant.kind==="candidate.build"){
      for(const key of ["source_snapshot_id","build_input_digest","isolation_mode"] as const)if(grant.constraints[key]!==payload[key])fail("permission_denied",`build ${key} differs from the confirmed grant`);return;
    }
    if(request.action==="launch"&&grant.kind==="candidate.launch"){
      if(grant.constraints.candidate_id!==payload.candidate_id||grant.constraints.profile_fixture_id!==payload.profile_fixture_id)fail("permission_denied","candidate/profile differs from the confirmed launch grant");return;
    }
    if(request.action==="run_case"&&grant.kind==="candidate.launch"){
      if(grant.instance_id!==payload.instance_id)fail("permission_denied","case is not bound to the authorized instance");return;
    }
    fail("permission_denied","authorization kind does not permit this action");
  }

  resolveInstanceGrant(evolutionId:string,instanceId:string):string{
    uuid(evolutionId,"evolution_id");uuid(instanceId,"instance_id");this.refresh();
    const matches=[...this.grants.values()].filter(grant=>grant.evolution_id===evolutionId&&grant.kind==="candidate.launch"&&grant.instance_id===instanceId&&grant.derived_from!==null);
    if(matches.length!==1)fail(matches.length>1?"recovery_required":"permission_denied",matches.length>1?"multiple stop grants are bound to the instance":"no stop grant is bound to this exact instance");
    return matches[0].authorization_id;
  }

  revoke(evolutionId:string):void {
    uuid(evolutionId,"evolution_id");this.refresh();
    const targets=[...this.grants.values()].filter((grant)=>grant.evolution_id===evolutionId&&!grant.revoked);
    for(const grant of targets){this.journal.appendMutation({kind:"grant.revoked",evolution_id:evolutionId,value:{authorization_id:grant.authorization_id,revoked_at:new Date(this.clock()).toISOString()}});this.refresh();}
  }
}

