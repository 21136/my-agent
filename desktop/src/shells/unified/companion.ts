/**
 * Pet-as-assistant avatar: an animated chibi shown to the left of the assistant side
 * of every chat turn. Pure helpers only (no DOM state) so they stay unit-testable.
 */

/** Relative to the page so it resolves on the Vite dev server and from dist/index.html (file://). */
export const PET_AVATAR_ASSET = "./pet-assets/avatar";

export const PET_AVATAR_STATES = ["idle", "thinking", "coding", "done", "error"] as const;
export type PetAvatarState = (typeof PET_AVATAR_STATES)[number];

export const PET_AVATAR_LABELS: Record<PetAvatarState, string> = {
  idle: "助手",
  thinking: "助手 · 思考中",
  coding: "助手 · 执行中",
  done: "助手 · 已完成",
  error: "助手 · 出错了",
};

/** Facts about one turn's assistant side, gathered by the shell. */
export type PetTurnSummary = {
  /** This turn is the one currently being processed. */
  live: boolean;
  /** A tool / code execution is running right now. */
  toolRunning: boolean;
  /** The model is streaming its reasoning. */
  thinkingLive: boolean;
  /** The reply text is being streamed. */
  replyStreaming: boolean;
  /** A final assistant reply exists. */
  hasReply: boolean;
  /** At least one tool in the turn failed. */
  toolFailed: boolean;
  /** The turn produced a system notice (errors surface as notices). */
  hasNotice: boolean;
  /** Waiting for the user to approve a tool call. */
  confirmPending: boolean;
};

export function petTurnState(s: PetTurnSummary): PetAvatarState {
  if (s.live) {
    if (s.confirmPending) return "idle";
    if (s.toolRunning) return "coding";
    if (s.replyStreaming && !s.thinkingLive) return "idle";
    return "thinking";
  }
  if (s.hasReply) return "done";
  if (s.toolFailed || s.hasNotice) return "error";
  return "done";
}

export function petAvatarSrc(state: PetAvatarState, still = false): string {
  return `${PET_AVATAR_ASSET}/pet-${state}${still ? "-still" : ""}.webp`;
}

/**
 * Avatar markup. `<picture>` swaps to the static first frame when the OS asks for
 * reduced motion, without any JS.
 */
export function renderPetAvatarHtml(state: PetAvatarState): string {
  const label = PET_AVATAR_LABELS[state];
  return `<div class="unified-pet-avatar" data-pet-state="${state}" role="img" aria-label="${label}" title="${label}">`
    + `<picture><source media="(prefers-reduced-motion: reduce)" srcset="${petAvatarSrc(state, true)}" />`
    + `<img class="unified-pet-avatar-img" src="${petAvatarSrc(state)}" alt="" width="40" height="40" decoding="async" draggable="false" /></picture>`
    + `</div>`;
}

export const PET_STATE_TAGS: Record<PetAvatarState, string> = {
  idle: "待命",
  thinking: "思考中",
  coding: "执行中",
  done: "已完成",
  error: "出错了",
};

/** Name line above the assistant side of a turn, with a state tag. */
export function renderPetNameHtml(state: PetAvatarState): string {
  return `<div class="unified-pet-name">my-agent<span class="unified-pet-state">${PET_STATE_TAGS[state]}</span></div>`;
}
