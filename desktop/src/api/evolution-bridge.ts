export type EvolutionHostRequest =
  | { type: "evolution.begin"; conversation_id: string }
  | { type: "evolution.inspect"; conversation_id: string }
  | { type: "evolution.stop"; conversation_id: string };
export type EvolutionDesktopRelay = { onEvolutionRequest?: (handler: (request: unknown) => void) => (() => void) | undefined };
export type EvolutionRelayClient = { sendEvolutionRequest(request: EvolutionHostRequest): void };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const TYPES = new Set(["evolution.begin", "evolution.inspect", "evolution.stop"]);
export function normalizeEvolutionHostRequest(value: unknown, currentConversationId?: string): EvolutionHostRequest {
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null) throw new TypeError("evolution WS request must be a plain object");
  const item = value as Record<string, unknown>;
  if (Object.keys(item).length !== 2 || !Object.hasOwn(item, "type") || !Object.hasOwn(item, "conversation_id")) throw new TypeError("evolution WS request fields are invalid");
  if (typeof item.type !== "string" || !TYPES.has(item.type) || typeof item.conversation_id !== "string" || !UUID.test(item.conversation_id)) throw new TypeError("evolution WS request type or conversation id is invalid");
  if (currentConversationId !== undefined && item.conversation_id !== currentConversationId) throw new TypeError("evolution WS request does not match the current session");
  return { type: item.type as EvolutionHostRequest["type"], conversation_id: item.conversation_id };
}
export function bindEvolutionHostRelay(input: {
  desktop?: EvolutionDesktopRelay;
  client: EvolutionRelayClient;
  onError?: (error: Error) => void;
}): () => void {
  const subscribe = input.desktop?.onEvolutionRequest;
  if (typeof subscribe !== "function") return () => undefined;
  const cleanup = subscribe((value) => {
    try {
      input.client.sendEvolutionRequest(normalizeEvolutionHostRequest(value));
    } catch (error) {
      input.onError?.(error instanceof Error ? error : new Error(String(error)));
    }
  });
  return typeof cleanup === "function" ? cleanup : () => undefined;
}
