import { closeSync, existsSync, fsyncSync, fstatSync, lstatSync, mkdirSync, openSync, readFileSync, realpathSync, renameSync, statSync, unlinkSync, writeSync } from "node:fs";
import { randomUUID } from "node:crypto";
import * as path from "node:path";
import { ContractError, canonicalJson, contentHash, parseControlRequest, parseControlResult, type ControlRequest, type ControlResult } from "./contracts";
import type { HostBinding } from "./binding";
import { isEvolutionConversationId } from "./conversation-id";

const MAX_JOURNAL_BYTES = 256 * 1024 * 1024;
const MAX_JOURNAL_LINE_BYTES = 1024 * 1024;
const ZERO_DIGEST = null;
const WRITERS = new Map<string, JournalStore>();

type JournalEvent = Record<string, unknown>;
type JournalLine = { sequence: number; previous_digest: string | null; event: JournalEvent; digest: string };
export type EffectState = "not_started" | "started" | "observed" | "terminal";
export type Intent = { request: ControlRequest; payload_hash: string; effect: EffectState; result: ControlResult | null };
export type StateMutation = {
  kind: "binding.created" | "binding.frozen" | "binding.released" | "grant.created" | "grant.revoked" | "grant.instance_bound";
  evolution_id: string;
  value: Record<string, unknown>;
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
function fail(code: ConstructorParameters<typeof ContractError>[0], message: string): never { throw new ContractError(code, message); }
function object(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) fail("invalid_request", `${label} must be an object`);
  return value as Record<string, unknown>;
}
function validateBinding(value: unknown): HostBinding {
  const v = object(value, "binding");
  const expected = ["evolution_id", "conversation_id", "scope_id", "host_instance_id", "state_revision", "status"];
  if (Object.keys(v).length !== expected.length || expected.some((k) => !Object.hasOwn(v, k))) fail("invalid_request", "invalid binding fields");
  if (!isEvolutionConversationId(v.conversation_id)) fail("invalid_request", "invalid binding conversation_id");
  for (const key of ["evolution_id", "scope_id", "host_instance_id"] as const) {
    if (typeof v[key] !== "string" || !UUID.test(v[key])) fail("invalid_request", `invalid binding ${key}`);
  }
  if (!Number.isSafeInteger(v.state_revision) || (v.state_revision as number) < 0) fail("invalid_request", "invalid binding state_revision");
  if (v.status !== "bound" && v.status !== "frozen") fail("invalid_request", "invalid binding status");
  return v as unknown as HostBinding;
}
function equalBinding(a: HostBinding, b: HostBinding): boolean {
  return a.evolution_id === b.evolution_id && a.conversation_id === b.conversation_id && a.scope_id === b.scope_id && a.host_instance_id === b.host_instance_id && a.state_revision === b.state_revision && a.status === b.status;
}
function clone<T>(value: T): T { return structuredClone(value); }
function unresolved(intent:Intent):boolean{return intent.effect==="started"||intent.effect==="observed"||intent.effect==="terminal"&&intent.result?.status==="unknown";}

export class JournalStore {
  readonly root: string;
  readonly journalPath: string;
  private readonly fd: number;
  private readonly writerKey: string;
  private readonly processLockPath: string;
  private readonly processLockToken: string;
  private processLockIdentity: {dev:number;ino:number} | null = null;
  private readonly events: JournalEvent[] = [];
  private readonly intents = new Map<string, Intent>();
  private readonly bindings = new Map<string, HostBinding>();
  private sequence = 0;
  private validBytes = 0;
  private previousDigest: string | null = ZERO_DIGEST;
  private closed = false;
  private corruptTail = false;
  private quarantinedTailPath: string | null = null;

  constructor(root: string) {
    if (typeof root !== "string" || !path.isAbsolute(root)) fail("invalid_request", "journal root must be an absolute trusted path");
    mkdirSync(root, { recursive: true });
    const rootInfo = lstatSync(root);
    if (!rootInfo.isDirectory() || rootInfo.isSymbolicLink()) fail("scope_conflict", "journal root must be a real directory");
    this.root = realpathSync.native(root);
    this.journalPath = path.join(this.root, "journal.jsonl");
    const journalKey = process.platform === "win32" ? this.journalPath.toLowerCase() : this.journalPath;
    if (WRITERS.has(journalKey)) fail("scope_conflict", "journal already has a writer in this process");
    this.writerKey = journalKey;
    this.processLockPath = `${this.journalPath}.writer-lock`;
    this.processLockToken = randomUUID();
    WRITERS.set(journalKey, this);
    let lockFd: number | null = null;
    try {
      lockFd=this.acquireProcessLock();
      const lockBytes = Buffer.from(canonicalJson({ owner_token:this.processLockToken, pid:process.pid, created_at:new Date().toISOString() }), "utf8");
      let lockWritten = 0;
      while (lockWritten < lockBytes.length) lockWritten += writeSync(lockFd, lockBytes, lockWritten, lockBytes.length - lockWritten);
      fsyncSync(lockFd);
      closeSync(lockFd); lockFd = null;
      const lockInfo=statSync(this.processLockPath);
      this.processLockIdentity={dev:lockInfo.dev,ino:lockInfo.ino};
      if (existsSync(this.journalPath)) {
        const info = lstatSync(this.journalPath);
        if (!info.isFile() || info.isSymbolicLink()) fail("scope_conflict", "journal path is not a regular file");
        if (info.size > MAX_JOURNAL_BYTES) fail("budget_exceeded", "journal exceeds 256 MiB recovery bound");
        this.load(readFileSync(this.journalPath));
      }
      this.fd = openSync(this.journalPath, "a");
    } catch (error) {
      if (lockFd !== null) { try { closeSync(lockFd); } catch { /* preserve original failure */ } }
      try {
        const info=statSync(this.processLockPath);
        const parsed=JSON.parse(readFileSync(this.processLockPath,"utf8")) as {owner_token?:string};
        if(parsed.owner_token===this.processLockToken&&this.processLockIdentity!==null&&info.dev===this.processLockIdentity.dev&&info.ino===this.processLockIdentity.ino)unlinkSync(this.processLockPath);
      } catch { /* stale/unknown lock is left fail-closed for explicit recovery */ }
      WRITERS.delete(journalKey);
      throw error;
    }
  }

  private acquireProcessLock():number {
    for(let attempt=0;attempt<3;attempt++){
      try{return openSync(this.processLockPath,"wx",0o600);}
      catch(error){if((error as NodeJS.ErrnoException).code!=="EEXIST")throw error;}
      let existing:Buffer;let owner:{pid?:unknown;owner_token?:unknown};
      try{
        existing=readFileSync(this.processLockPath);
        if(existing.length>1024)fail("recovery_required","journal writer lock is oversized and requires manual recovery");
        owner=JSON.parse(existing.toString("utf8")) as {pid?:unknown;owner_token?:unknown};
      }catch(error){if(error instanceof ContractError)throw error;fail("recovery_required","journal writer lock is corrupt and requires manual recovery");}
      if(!Number.isSafeInteger(owner.pid)||(owner.pid as number)<=0||typeof owner.owner_token!=="string")fail("recovery_required","journal writer lock identity is invalid and requires manual recovery");
      try{process.kill(owner.pid as number,0);fail("scope_conflict","journal is owned by another live supervisor process");}
      catch(error){
        if(error instanceof ContractError)throw error;
        const code=(error as NodeJS.ErrnoException).code;
        if(code!=="ESRCH"&&code!=="EINVAL")fail("scope_conflict","journal writer liveness is uncertain; refusing lock takeover");
      }
      const stalePath=`${this.processLockPath}.stale-${randomUUID()}`;
      try{renameSync(this.processLockPath,stalePath);}
      catch(error){if((error as NodeJS.ErrnoException).code==="ENOENT")continue;fail("recovery_required","stale journal writer lock could not be isolated");}
      try{if(!readFileSync(stalePath).equals(existing))fail("recovery_required","journal writer lock changed during stale-owner isolation");}
      catch(error){if(error instanceof ContractError)throw error;fail("recovery_required","isolated stale journal lock could not be verified");}
    }
    fail("scope_conflict","journal writer lock changed repeatedly; retry after inspection");
  }

  private ensureOpen(): void {
    if (this.closed) fail("recovery_required", "journal writer is closed");
  }

  private load(bytes: Buffer): void {
    let offset = 0;
    while (offset < bytes.length) {
      const newline = bytes.indexOf(0x0a, offset);
      if (newline < 0) { this.quarantine(bytes.subarray(offset)); this.corruptTail = true; return; }
      const lineBytes = bytes.subarray(offset, newline);
      const nextOffset = newline + 1;
      if (lineBytes.length === 0 || lineBytes.length > MAX_JOURNAL_LINE_BYTES) {
        this.quarantine(bytes.subarray(offset)); this.corruptTail = true; return;
      }
      try {
        const text = new TextDecoder("utf-8", { fatal: true }).decode(lineBytes);
        const parsed = JSON.parse(text) as unknown;
        const entry = object(parsed, "journal line") as unknown as JournalLine;
        const keys = Object.keys(entry).sort();
        if (keys.join(",") !== "digest,event,previous_digest,sequence") throw new Error("invalid journal line keys");
        if (!Number.isSafeInteger(entry.sequence) || entry.sequence !== this.sequence + 1) throw new Error("journal sequence mismatch");
        if (entry.previous_digest !== this.previousDigest) throw new Error("journal chain predecessor mismatch");
        object(entry.event, "journal event");
        const expected = contentHash({ sequence: entry.sequence, previous_digest: entry.previous_digest, event: entry.event });
        if (entry.digest !== expected || canonicalJson(entry) !== text) throw new Error("journal digest or canonical form mismatch");
        this.applyEvent(entry.event, true);
        this.events.push(clone(entry.event));
        this.sequence = entry.sequence;
        this.previousDigest = entry.digest;
        this.validBytes = nextOffset;
        offset = nextOffset;
      } catch {
        this.quarantine(bytes.subarray(offset));
        this.corruptTail = true;
        return;
      }
    }
  }

  private quarantine(tail: Buffer): void {
    const destination = path.join(this.root, `journal-corrupt-${randomUUID()}.tail`);
    const fd = openSync(destination, "wx", 0o600);
    try {
      let offset = 0;
      while (offset < tail.length) offset += writeSync(fd, tail, offset, tail.length - offset);
      fsyncSync(fd);
    } finally { closeSync(fd); }
    this.quarantinedTailPath = destination;
  }

  private append(event: JournalEvent): void {
    this.ensureOpen();
    if (this.corruptTail) fail("recovery_required", "journal has a corrupt tail; writes are quarantined");
    const sequence = this.sequence + 1;
    const digest = contentHash({ sequence, previous_digest: this.previousDigest, event });
    const entry: JournalLine = { sequence, previous_digest: this.previousDigest, event, digest };
    const line = `${canonicalJson(entry)}\n`;
    const bytes = Buffer.from(line, "utf8");
    if (bytes.length > MAX_JOURNAL_LINE_BYTES) fail("budget_exceeded", "journal entry exceeds 1 MiB");
    if (fstatSync(this.fd).size + bytes.length > MAX_JOURNAL_BYTES) fail("budget_exceeded", "journal exceeds 256 MiB bound");
    try {
      let written = 0;
      while (written < bytes.length) {
        const count = writeSync(this.fd, bytes, written, bytes.length - written);
        if (count <= 0) throw new Error("short journal write made no progress");
        written += count;
      }
      fsyncSync(this.fd);
    } catch (error) {
      this.corruptTail = true;
      try {
        const onDisk = readFileSync(this.journalPath);
        if (onDisk.length > this.validBytes) this.quarantine(onDisk.subarray(this.validBytes));
      } catch { /* retain the original journal and fail closed even if diagnosis cannot be copied */ }
      throw new ContractError("outcome_unknown", `journal append could not be durably confirmed: ${error instanceof Error ? error.message : String(error)}`);
    }
    this.applyEvent(event, false);
    this.events.push(clone(event));
    this.sequence = sequence;
    this.previousDigest = digest;
    this.validBytes += bytes.length;
  }

  private currentBinding(evolutionId: string): HostBinding | null { return this.bindings.get(evolutionId) ?? null; }

  private validateMutation(record: StateMutation): JournalEvent {
    const evolutionId = record.evolution_id;
    if (!UUID.test(evolutionId)) fail("invalid_request", "mutation evolution_id must be a UUID");
    const current = this.currentBinding(evolutionId);
    let next: HostBinding | null;
    if (record.kind === "binding.created") {
      if (current) fail("identity_mismatch", "evolution binding already exists");
      next = validateBinding(record.value);
      if (next.evolution_id !== evolutionId || next.state_revision !== 0 || next.status !== "bound") fail("invalid_request", "new binding must start at revision zero and bound");
      for (const existing of this.bindings.values()) {
        if (existing.scope_id === next.scope_id || existing.conversation_id === next.conversation_id) fail("permission_denied", "scope or conversation already has a writer binding");
      }
    } else {
      if (!current) fail("permission_denied", "binding does not exist");
      if (record.kind === "binding.frozen") {
        if (current.status !== "bound") fail("identity_mismatch", "binding is already frozen");
        next = { ...current, state_revision: current.state_revision + 1, status: "frozen" };
      } else if (record.kind === "binding.released") {
        if (current.status !== "frozen") fail("permission_denied", "binding must be frozen before release");
        if ([...this.intents.values()].some((intent) => intent.request.evolution_id === evolutionId && unresolved(intent))) fail("outcome_unknown", "cannot release a binding with an unknown side effect");
        next = null;
      } else {
        if (current.status !== "bound") fail("permission_denied", "cannot change authorization on a frozen binding");
        next = { ...current, state_revision: current.state_revision + 1 };
      }
    }
    const value = record.kind === "binding.created" ? validateBinding(record.value) : object(record.value, "mutation value");
    canonicalJson(value);
    return { ...record, value, next_binding: next };
  }

  private applyEvent(eventValue: JournalEvent, replay: boolean): void {
    const event = object(eventValue, "event");
    if (event.kind === "operation.begin") {
      const request = parseControlRequest(event.request);
      const operationId = request.operation_id;
      const payloadHash = contentHash(request);
      if (event.payload_hash !== payloadHash || this.intents.has(operationId)) throw new Error("invalid/reused operation intent");
      const current = this.currentBinding(request.evolution_id);
      if (!current || current.conversation_id !== request.conversation_id || current.status !== "bound" && request.action !== "stop") throw new Error("operation has no matching active binding");
      if (current.state_revision !== request.expected_state_revision) throw new Error("operation revision is stale");
      if ([...this.intents.values()].some((intent) => intent.request.evolution_id === request.evolution_id && unresolved(intent) && request.action !== "stop")) throw new Error("operation blocked by unknown prior effect");
      const next = validateBinding(event.next_binding);
      if (!equalBinding(next, { ...current, state_revision: current.state_revision + 1 })) throw new Error("operation binding transition mismatch");
      if (event.effect !== "not_started" || event.result !== null) throw new Error("invalid initial operation state");
      this.bindings.set(request.evolution_id, next);
      this.intents.set(operationId, { request, payload_hash: payloadHash, effect: "not_started", result: null });
      return;
    }
    if (event.kind === "operation.effect") {
      const id = event.operation_id;
      if (typeof id !== "string" || typeof event.effect !== "string") throw new Error("invalid effect event");
      const intent = this.intents.get(id);
      if (!intent || intent.effect === "terminal") throw new Error("effect does not match an open intent");
      const allowed = intent.effect === "not_started" && event.effect === "started" || intent.effect === "started" && event.effect === "observed";
      if (!allowed) throw new Error("non-monotonic effect transition");
      intent.effect = event.effect as EffectState;
      return;
    }
    if (event.kind === "operation.finish") {
      if (typeof event.operation_id !== "string") throw new Error("invalid finished operation id");
      const intent = this.intents.get(event.operation_id);
      if (!intent || intent.effect === "terminal") throw new Error("finish does not match an open intent");
      const result = parseControlResult(event.result);
      if (result.operation_id !== event.operation_id || result.evolution_id !== intent.request.evolution_id || result.state_revision !== intent.request.expected_state_revision + 1 || result.status === "running") throw new Error("terminal result identity/state mismatch");
      intent.effect = "terminal"; intent.result = result;
      return;
    }
    const kind = event.kind;
    if (!["binding.created", "binding.frozen", "binding.released", "grant.created", "grant.revoked", "grant.instance_bound"].includes(String(kind))) throw new Error("unknown journal event kind");
    if (typeof event.evolution_id !== "string") throw new Error("mutation has no evolution id");
    const current = this.currentBinding(event.evolution_id);
    const next = event.next_binding === null ? null : validateBinding(event.next_binding);
    if (kind === "binding.created") {
      const created = validateBinding(event.value);
      if (current || created.evolution_id !== event.evolution_id || created.state_revision !== 0 || created.status !== "bound" || !equalBinding(created, next as HostBinding)) throw new Error("invalid binding creation");
      for (const existing of this.bindings.values()) if (existing.scope_id === created.scope_id || existing.conversation_id === created.conversation_id) throw new Error("duplicate scope writer");
      this.bindings.set(created.evolution_id, created);
    } else {
      if (!current) throw new Error("mutation has no existing binding");
      if (kind === "binding.released") {
        if (current.status !== "frozen" || next !== null) throw new Error("invalid binding release");
        this.bindings.delete(event.evolution_id);
      } else {
        if (!next || next.evolution_id !== current.evolution_id || next.conversation_id !== current.conversation_id || next.scope_id !== current.scope_id || next.host_instance_id !== current.host_instance_id || next.state_revision !== current.state_revision + 1) throw new Error("invalid state revision transition");
        if (kind === "binding.frozen" && (current.status !== "bound" || next.status !== "frozen")) throw new Error("invalid freeze transition");
        if ((kind === "grant.created" || kind === "grant.revoked" || kind === "grant.instance_bound") && (current.status !== "bound" || next.status !== "bound")) throw new Error("authorization mutation on a frozen binding");
        this.bindings.set(event.evolution_id, next);
      }
    }
    if (!replay && kind === "binding.created" && next === null) throw new Error("impossible binding state");
  }

  appendMutation(record: StateMutation): void {
    this.ensureOpen();
    const event = this.validateMutation(record);
    this.append(event);
  }

  replayMutations(): readonly StateMutation[] {
    return this.events.filter((event) => ["binding.created", "binding.frozen", "binding.released", "grant.created", "grant.revoked", "grant.instance_bound"].includes(String(event.kind)))
      .map((event) => ({ kind: event.kind as StateMutation["kind"], evolution_id: event.evolution_id as string, value: clone(event.value as Record<string, unknown>) }));
  }

  readEvents(): readonly JournalEvent[] { return this.events.map(clone); }
  getBinding(evolutionId: string): HostBinding | null { const binding=this.currentBinding(evolutionId); return binding ? clone(binding) : null; }
  get(operationId: string): Intent | null { const intent=this.intents.get(operationId); return intent ? clone(intent) : null; }

  begin(requestValue: ControlRequest, nextBindingValue: HostBinding): Intent {
    this.ensureOpen();
    if (this.corruptTail) fail("recovery_required", "journal has a corrupt tail; writes are quarantined");
    const request = parseControlRequest(requestValue);
    const payloadHash = contentHash(request);
    const prior = this.intents.get(request.operation_id);
    if (prior) {
      if (prior.payload_hash !== payloadHash) fail("identity_mismatch", "operation id was reused with a different request");
      return clone(prior);
    }
    const current = this.currentBinding(request.evolution_id);
    if (!current || current.conversation_id !== request.conversation_id) fail("permission_denied", "conversation does not own this binding");
    if (current.status === "frozen" && request.action !== "stop") fail("permission_denied", "binding is frozen");
    if (current.state_revision !== request.expected_state_revision) fail("stale_revision", "expected state revision is stale");
    if (request.action !== "stop" && [...this.intents.values()].some((intent) => intent.request.evolution_id === request.evolution_id && unresolved(intent))) fail("outcome_unknown", "an earlier side effect is unresolved");
    const nextBinding = validateBinding(nextBindingValue);
    if (!equalBinding(nextBinding, { ...current, state_revision: current.state_revision + 1 })) fail("stale_revision", "next binding is not the single expected revision");
    const event: JournalEvent = { kind: "operation.begin", operation_id: request.operation_id, evolution_id: request.evolution_id, request, payload_hash: payloadHash, effect: "not_started", result: null, next_binding: nextBinding };
    this.append(event);
    return clone(this.intents.get(request.operation_id)!);
  }

  markEffect(operationId: string, effect: EffectState): void {
    this.ensureOpen();
    if (effect !== "started" && effect !== "observed") fail("invalid_request", "use finish() for terminal operations");
    const intent = this.intents.get(operationId);
    if (!intent || !(intent.effect === "not_started" && effect === "started" || intent.effect === "started" && effect === "observed")) fail("identity_mismatch", "invalid operation effect transition");
    this.append({ kind: "operation.effect", operation_id: operationId, effect });
  }

  finish(resultValue: ControlResult): void {
    this.ensureOpen();
    const result = parseControlResult(resultValue);
    const intent = this.intents.get(result.operation_id);
    if (!intent || intent.effect === "terminal") fail("identity_mismatch", "terminal result has no open matching intent");
    if (result.evolution_id !== intent.request.evolution_id || result.state_revision !== intent.request.expected_state_revision + 1 || result.status === "running") fail("identity_mismatch", "terminal result identity or revision does not match the intent");
    this.append({ kind: "operation.finish", operation_id: result.operation_id, result });
  }

  recover(): { unknown_operation_ids: string[]; corrupt_tail: boolean; quarantined_tail_path: string | null } {
    return { unknown_operation_ids: [...this.intents.values()].filter(unresolved).map((intent) => intent.request.operation_id), corrupt_tail: this.corruptTail, quarantined_tail_path: this.quarantinedTailPath };
  }

  close(): void {
    if (this.closed) return;
    try { fsyncSync(this.fd); } finally {
      closeSync(this.fd); this.closed = true; WRITERS.delete(this.writerKey);
      try {
        const info=statSync(this.processLockPath);
        const parsed=JSON.parse(readFileSync(this.processLockPath,"utf8")) as {owner_token?:string};
        if(parsed.owner_token!==this.processLockToken||this.processLockIdentity===null||info.dev!==this.processLockIdentity.dev||info.ino!==this.processLockIdentity.ino)fail("scope_conflict","journal writer lock identity changed; refusing to release another owner's lock");
        unlinkSync(this.processLockPath);
      } catch(error) {
        if(error instanceof ContractError)throw error;
        fail("recovery_required","journal closed but writer lock could not be safely released");
      }
    }
  }
}

export function beginOperation(request: ControlRequest, deps: {
  journal: JournalStore; bindings: import("./binding").BindingStore; grants: import("./authorization").GrantStore;
}): Intent | ControlResult {
  const { journal, bindings, grants } = deps;
  const validated = parseControlRequest(request);
  const binding = bindings.require(validated.evolution_id, validated.conversation_id);
  const previous = journal.get(validated.operation_id);
  if (previous) {
    if (previous.payload_hash !== contentHash(validated)) fail("identity_mismatch", "operation id was reused with a different payload");
    return previous.result ?? { operation_id: request.operation_id, evolution_id: request.evolution_id, state_revision: previous.request.expected_state_revision + 1, status: "unknown", result: null, error: { code: "outcome_unknown", message: "Inspect the existing operation", evidence_ids: [], retryable: false } };
  }
  if (binding.status === "frozen" && validated.action !== "stop") fail("permission_denied", "binding is frozen");
  grants.require(validated, binding);
  bindings.requireRevision(validated.evolution_id, validated.expected_state_revision);
  return journal.begin(validated, { ...binding, state_revision: binding.state_revision + 1 });
}
