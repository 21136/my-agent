import {
  createDefaultCompanionWorld,
  normalizeCompanionWorld,
  type CompanionWorldEvent,
  type CompanionWorldState,
} from "./companion-world.ts";

export const COMPANION_WORLD_STORAGE_KEY = "ma_companion_world_v1";

type WorldStorage = Pick<Storage, "getItem" | "setItem">;

type ShareContext = {
  busy: boolean;
  confirmPending: boolean;
  composing: boolean;
};

function persistBestEffort(state: CompanionWorldState, storage: Pick<Storage, "setItem">): void {
  try {
    storage.setItem(COMPANION_WORLD_STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Persistence is best-effort; the current app session must remain usable.
  }
}

export function loadCompanionWorld(storage: WorldStorage, nowMs: number): CompanionWorldState {
  const fallback = createDefaultCompanionWorld(nowMs);
  let raw: string | null;
  try {
    raw = storage.getItem(COMPANION_WORLD_STORAGE_KEY);
  } catch {
    return fallback;
  }

  if (raw === null) {
    persistBestEffort(fallback, storage);
    return fallback;
  }

  try {
    const normalized = normalizeCompanionWorld(JSON.parse(raw) as unknown, nowMs);
    const canonical = JSON.stringify(normalized);
    if (canonical !== raw) persistBestEffort(normalized, storage);
    return normalized;
  } catch {
    persistBestEffort(fallback, storage);
    return fallback;
  }
}

export function saveCompanionWorld(
  state: CompanionWorldState,
  storage: Pick<Storage, "setItem">,
): boolean {
  try {
    const normalized = normalizeCompanionWorld(state, state.lastSimulatedAt);
    storage.setItem(COMPANION_WORLD_STORAGE_KEY, JSON.stringify(normalized));
    return true;
  } catch {
    return false;
  }
}

export function selectWorldShareCandidate(
  state: CompanionWorldState,
  context: ShareContext,
): CompanionWorldEvent | null {
  if (
    state.worldPaused ||
    state.sharingMuted ||
    context.busy ||
    context.confirmPending ||
    context.composing
  ) {
    return null;
  }

  const candidates = state.events.filter((event) => event.seenAt === null && event.dismissedAt === null);
  if (!candidates.length) return null;
  return [...candidates].sort((a, b) => b.at - a.at || b.id.localeCompare(a.id))[0] ?? null;
}

