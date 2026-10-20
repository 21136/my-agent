import type { CompanionWorldContextPayload } from "../../api/ws";
import type { CompanionWorldEvent, CompanionWorldState } from "./companion-world";

export type CompanionWorldTurnContext = CompanionWorldContextPayload;

const VALID_SCENES = new Set<CompanionWorldTurnContext["sceneId"]>(["home", "harbor", "beyond"]);
const DEFAULT_ACTIVITY_BY_SCENE: Record<CompanionWorldTurnContext["sceneId"], CompanionWorldTurnContext["activityId"]> = {
  home: "resting",
  harbor: "browsing-records",
  beyond: "planning-a-journey",
};
const ACTIVITIES_BY_SCENE: Record<CompanionWorldTurnContext["sceneId"], ReadonlySet<CompanionWorldTurnContext["activityId"]>> = {
  home: new Set(["resting", "listening"]),
  harbor: new Set(["browsing-records"]),
  beyond: new Set(["planning-a-journey"]),
};

function compactLine(value: string, maxLength: number): string {
  return value.replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/\s+/g, " ").trim().slice(0, maxLength);
}

function latestEventForLane(
  events: CompanionWorldEvent[],
  lane: CompanionWorldEvent["lane"],
): CompanionWorldTurnContext["events"][number] | null {
  const event = [...events]
    .filter((candidate) => candidate.lane === lane)
    .filter((candidate) => lane === "personal"
      ? candidate.origin === "simulated"
      : candidate.origin === "explicit-app-event" && Boolean(candidate.sourceId?.trim()))
    .sort((a, b) => b.at - a.at || b.id.localeCompare(a.id))[0];
  if (!event) return null;
  const title = compactLine(event.title, 160);
  const body = compactLine(event.body, 320);
  if (!title || !body) return null;
  return {
    lane,
    origin: event.origin,
    title,
    body,
    ...(lane === "shared" ? { sourceId: event.sourceId } : {}),
  };
}

export function buildCompanionWorldTurnContext(
  state: CompanionWorldState,
  options: { projectChat?: boolean } = {},
): CompanionWorldTurnContext | null {
  if (options.projectChat || state.worldPaused || state.sharingMuted) return null;

  const sceneId = VALID_SCENES.has(state.sceneId as CompanionWorldTurnContext["sceneId"])
    ? state.sceneId as CompanionWorldTurnContext["sceneId"]
    : "home";
  const candidateActivity = state.activityId as CompanionWorldTurnContext["activityId"];
  const activityId = ACTIVITIES_BY_SCENE[sceneId].has(candidateActivity)
    ? candidateActivity
    : DEFAULT_ACTIVITY_BY_SCENE[sceneId];
  const stage = Number.isSafeInteger(state.personalArc.stage) && state.personalArc.stage >= 0
    ? Math.min(state.personalArc.stage, 1_000_000)
    : 0;
  const events = ([
    latestEventForLane(state.events, "personal"),
    latestEventForLane(state.events, "shared"),
  ]).filter((event): event is CompanionWorldTurnContext["events"][number] => event !== null);

  return {
    version: 1,
    sceneId,
    activityId,
    personalArc: {
      id: state.personalArc.id === "late-night-broadcast" ? state.personalArc.id : "late-night-broadcast",
      stage,
    },
    events,
  };
}
