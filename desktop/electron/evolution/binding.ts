import { randomUUID } from "node:crypto";
import { ContractError } from "./contracts";
import { JournalStore, type StateMutation } from "./journal";

export type HostBinding = {
  evolution_id: string;
  conversation_id: string;
  scope_id: string;
  host_instance_id: string;
  state_revision: number;
  status: "bound" | "frozen";
};
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
function fail(code: ConstructorParameters<typeof ContractError>[0], message: string): never { throw new ContractError(code, message); }
function parseBinding(value: unknown): HostBinding {
  if (value === null || typeof value !== "object" || Array.isArray(value)) fail("recovery_required", "journal contains a malformed binding projection");
  const item = value as Record<string, unknown>;
  if (!["evolution_id", "conversation_id", "scope_id", "host_instance_id", "state_revision", "status"].every((key) => Object.hasOwn(item, key)) || Object.keys(item).length !== 6) fail("recovery_required", "journal binding has unexpected fields");
  for (const key of ["evolution_id", "conversation_id", "scope_id", "host_instance_id"] as const) if (typeof item[key] !== "string" || !UUID.test(item[key])) fail("recovery_required", `journal binding has invalid ${key}`);
  if (!Number.isSafeInteger(item.state_revision) || (item.state_revision as number) < 0 || item.status !== "bound" && item.status !== "frozen") fail("recovery_required", "journal binding state is invalid");
  return structuredClone(item) as unknown as HostBinding;
}

export class BindingStore {
  readonly journal: JournalStore;
  private readonly active = new Map<string, HostBinding>();

  constructor(journal: JournalStore) {
    this.journal = journal;
    this.refresh();
  }

  private refresh(): void {
    this.active.clear();
    for (const event of this.journal.readEvents()) {
      if (typeof event.evolution_id !== "string") continue;
      if (event.kind === "binding.released") { this.active.delete(event.evolution_id); continue; }
      if (event.next_binding === null || event.next_binding === undefined) continue;
      this.active.set(event.evolution_id, parseBinding(event.next_binding));
    }
  }

  bind(input: { conversation_id: string; scope_id: string; host_instance_id: string }): HostBinding {
    for (const [name, value] of Object.entries(input)) if (typeof value !== "string" || !UUID.test(value)) fail("invalid_request", `${name} must be a canonical UUID`);
    this.refresh();
    if ([...this.active.values()].some((binding) => binding.scope_id === input.scope_id)) fail("permission_denied", "sandbox already has a writer lease");
    if ([...this.active.values()].some((binding) => binding.conversation_id === input.conversation_id)) fail("permission_denied", "conversation already owns an evolution binding");
    const binding: HostBinding = { evolution_id: randomUUID(), ...input, state_revision: 0, status: "bound" };
    const mutation: StateMutation = { kind: "binding.created", evolution_id: binding.evolution_id, value: binding as unknown as Record<string, unknown> };
    this.journal.appendMutation(mutation);
    this.refresh();
    return structuredClone(binding);
  }

  findForConversation(conversationId: string): HostBinding | null {
    if (typeof conversationId !== "string" || !UUID.test(conversationId)) fail("invalid_request", "conversation_id must be a canonical UUID");
    this.refresh();
    const matches = [...this.active.values()].filter((binding) => binding.conversation_id === conversationId);
    if (matches.length > 1) fail("recovery_required", "conversation owns multiple active evolution bindings");
    return matches.length === 0 ? null : structuredClone(matches[0]);
  }

  require(evolutionId: string, conversationId: string): HostBinding {
    this.refresh();
    const binding = this.active.get(evolutionId);
    if (!binding || binding.conversation_id !== conversationId) fail("permission_denied", "conversation does not own this evolution binding");
    return structuredClone(binding);
  }

  requireRevision(evolutionId: string, expected: number): void {
    this.refresh();
    const binding = this.active.get(evolutionId);
    if (!binding) fail("permission_denied", "evolution binding does not exist");
    if (!Number.isSafeInteger(expected) || expected !== binding.state_revision) fail("stale_revision", "expected state revision is stale");
  }

  freeze(evolutionId: string): void {
    this.refresh();
    const binding = this.active.get(evolutionId);
    if (!binding) fail("permission_denied", "evolution binding does not exist");
    if (binding.status === "frozen") return;
    this.journal.appendMutation({ kind: "binding.frozen", evolution_id: evolutionId, value: { reason: "trusted_host_freeze" } });
    this.refresh();
  }

  release(evolutionId: string): void {
    this.refresh();
    const binding = this.active.get(evolutionId);
    if (!binding) fail("permission_denied", "evolution binding does not exist");
    if (binding.status !== "frozen") fail("permission_denied", "freeze the binding before release");
    if (this.journal.recover().unknown_operation_ids.some((operationId) => this.journal.get(operationId)?.request.evolution_id === evolutionId)) fail("outcome_unknown", "cannot release a lease while an operation outcome is unknown");
    this.journal.appendMutation({ kind: "binding.released", evolution_id: evolutionId, value: { released: true } });
    this.refresh();
  }
}
