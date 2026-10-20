export type WorldLane = "personal" | "shared";
export type WorldEventOrigin = "simulated" | "explicit-app-event";

export interface CompanionWorldEvent {
  id: string;
  lane: WorldLane;
  at: number;
  title: string;
  body: string;
  origin: WorldEventOrigin;
  sourceId?: string;
  seenAt: number | null;
  dismissedAt: number | null;
}

export interface CompanionWorldState {
  schemaVersion: 1;
  lastSimulatedAt: number;
  sceneId: string;
  activityId: string;
  personalArc: { id: string; stage: number; updatedAt: number };
  events: CompanionWorldEvent[];
  worldPaused: boolean;
  sharingMuted: boolean;
}

const DAY_MS = 24 * 60 * 60 * 1000;
const MAX_WORLD_EVENTS = 100;
const MAX_ID_LENGTH = 128;
const MAX_SOURCE_ID_LENGTH = 256;
const MAX_TITLE_LENGTH = 200;
const MAX_BODY_LENGTH = 2_000;

const PERSONAL_BEATS = [
  {
    sceneId: "home",
    activityId: "listening",
    title: "她给窗边留了一段安静的时间",
    body: "喜欢的那首歌又放了一遍，今天不急着赶去下一站。",
  },
  {
    sceneId: "harbor",
    activityId: "browsing-records",
    title: "她在旧城区发现一点新鲜事",
    body: "原本只是随便逛逛，最后还是为一个意外的小发现停下脚步。",
  },
  {
    sceneId: "beyond",
    activityId: "planning-a-journey",
    title: "她把下一个想去的地方记了下来",
    body: "还没决定什么时候出发，不过她已经开始期待路上的风景。",
  },
] as const;

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function safeTime(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : null;
}

function cleanString(value: unknown, maxLength: number): string | null {
  if (typeof value !== "string") return null;
  const cleaned = value.trim();
  return cleaned.length > 0 && cleaned.length <= maxLength ? cleaned : null;
}

function safeStage(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? value
    : null;
}

function normalizeEvent(value: unknown): CompanionWorldEvent | null {
  const record = asRecord(value);
  if (!record) return null;

  const id = cleanString(record.id, MAX_ID_LENGTH);
  const at = safeTime(record.at);
  const title = cleanString(record.title, MAX_TITLE_LENGTH);
  const body = cleanString(record.body, MAX_BODY_LENGTH);
  const lane = record.lane === "personal" || record.lane === "shared" ? record.lane : null;
  const origin = record.origin === "simulated" || record.origin === "explicit-app-event" ? record.origin : null;
  if (!id || at === null || !title || !body || !lane || !origin) return null;

  const sourceId = cleanString(record.sourceId, MAX_SOURCE_ID_LENGTH);
  if (origin === "explicit-app-event" && (!sourceId || lane !== "shared")) return null;
  if (origin === "simulated" && lane !== "personal") return null;

  return {
    id,
    at,
    title,
    body,
    lane,
    origin,
    ...(sourceId ? { sourceId } : {}),
    seenAt: record.seenAt === null ? null : safeTime(record.seenAt),
    dismissedAt: record.dismissedAt === null ? null : safeTime(record.dismissedAt),
  };
}

function latestEvents(events: CompanionWorldEvent[]): CompanionWorldEvent[] {
  return events.slice(-MAX_WORLD_EVENTS);
}

export function createDefaultCompanionWorld(nowMs: number): CompanionWorldState {
  const now = safeTime(nowMs) ?? 0;
  return {
    schemaVersion: 1,
    lastSimulatedAt: now,
    sceneId: "home",
    activityId: "resting",
    personalArc: {
      id: "late-night-broadcast",
      stage: 0,
      updatedAt: now,
    },
    events: [],
    worldPaused: false,
    sharingMuted: false,
  };
}

export function normalizeCompanionWorld(value: unknown, nowMs: number): CompanionWorldState {
  const now = safeTime(nowMs) ?? 0;
  const fallback = createDefaultCompanionWorld(now);
  const record = asRecord(value);
  if (!record || record.schemaVersion !== 1) return fallback;

  const lastSimulatedAt = safeTime(record.lastSimulatedAt);
  const sceneId = cleanString(record.sceneId, MAX_ID_LENGTH);
  const activityId = cleanString(record.activityId, MAX_ID_LENGTH);
  const arc = asRecord(record.personalArc);
  const arcId = cleanString(arc?.id, MAX_ID_LENGTH);
  const arcStage = safeStage(arc?.stage);
  const arcUpdatedAt = safeTime(arc?.updatedAt);
  if (
    lastSimulatedAt === null ||
    !sceneId ||
    !activityId ||
    !arcId ||
    arcStage === null ||
    arcUpdatedAt === null
  ) {
    return fallback;
  }

  const events = Array.isArray(record.events)
    ? latestEvents(record.events.map(normalizeEvent).filter((event): event is CompanionWorldEvent => event !== null))
    : [];

  return {
    schemaVersion: 1,
    lastSimulatedAt,
    sceneId,
    activityId,
    personalArc: { id: arcId, stage: arcStage, updatedAt: arcUpdatedAt },
    events,
    worldPaused: typeof record.worldPaused === "boolean" ? record.worldPaused : false,
    sharingMuted: typeof record.sharingMuted === "boolean" ? record.sharingMuted : false,
  };
}

export function advanceCompanionWorld(
  state: CompanionWorldState,
  nowMs: number,
): { state: CompanionWorldState; addedEvents: CompanionWorldEvent[] } {
  const safeNow = safeTime(nowMs);
  const current = normalizeCompanionWorld(state, safeNow ?? state.lastSimulatedAt);
  const now = safeNow ?? current.lastSimulatedAt;

  if (now <= current.lastSimulatedAt) return { state: current, addedEvents: [] };

  if (current.worldPaused) {
    return {
      state: { ...current, lastSimulatedAt: now },
      addedEvents: [],
    };
  }

  const elapsedDays = Math.floor((now - current.lastSimulatedAt) / DAY_MS);
  if (elapsedDays < 1) return { state: current, addedEvents: [] };

  const progressedAt = current.lastSimulatedAt + elapsedDays * DAY_MS;
  const nextStage = current.personalArc.stage + elapsedDays;
  const beat = PERSONAL_BEATS[nextStage % PERSONAL_BEATS.length];
  const previousEvents = current.events;
  let addedEvents: CompanionWorldEvent[] = [];

  // A small, deterministic milestone rhythm. Large absences collapse to their latest milestone.
  const latestMilestone = Math.floor(nextStage / PERSONAL_BEATS.length) * PERSONAL_BEATS.length;
  const firstMilestone = Math.floor(current.personalArc.stage / PERSONAL_BEATS.length + 1) * PERSONAL_BEATS.length;
  if (latestMilestone >= firstMilestone && latestMilestone > 0) {
    const eventStage = latestMilestone;
    const eventBeat = PERSONAL_BEATS[eventStage % PERSONAL_BEATS.length];
    const eventAt = current.lastSimulatedAt + (eventStage - current.personalArc.stage) * DAY_MS;
    const event: CompanionWorldEvent = {
      id: `personal:${current.personalArc.id}:${eventStage}`,
      lane: "personal",
      at: eventAt,
      title: eventBeat.title,
      body: eventBeat.body,
      origin: "simulated",
      seenAt: null,
      dismissedAt: null,
    };
    if (!previousEvents.some((existing) => existing.id === event.id)) addedEvents = [event];
  }

  return {
    state: {
      ...current,
      lastSimulatedAt: progressedAt,
      sceneId: beat.sceneId,
      activityId: beat.activityId,
      personalArc: { ...current.personalArc, stage: nextStage, updatedAt: progressedAt },
      events: latestEvents([...previousEvents, ...addedEvents]),
    },
    addedEvents,
  };
}

export function recordExplicitSharedEvent(
  state: CompanionWorldState,
  input: { id: string; title: string; body: string; sourceId: string },
  nowMs: number,
): CompanionWorldState {
  const current = normalizeCompanionWorld(state, safeTime(nowMs) ?? state.lastSimulatedAt);
  const id = cleanString(input?.id, MAX_ID_LENGTH);
  const title = cleanString(input?.title, MAX_TITLE_LENGTH);
  const body = cleanString(input?.body, MAX_BODY_LENGTH);
  const sourceId = cleanString(input?.sourceId, MAX_SOURCE_ID_LENGTH);
  const at = safeTime(nowMs);
  if (!id || !title || !body || !sourceId || at === null) return current;
  if (current.events.some((event) => event.id === id)) return current;

  const event: CompanionWorldEvent = {
    id,
    lane: "shared",
    at,
    title,
    body,
    origin: "explicit-app-event",
    sourceId,
    seenAt: null,
    dismissedAt: null,
  };
  return { ...current, events: latestEvents([...current.events, event]) };
}

