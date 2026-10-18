/**
 * Pet-as-assistant avatar: an animated chibi shown to the left of the assistant side
 * of every chat turn. Pure helpers only (no DOM state) so they stay unit-testable.
 */

/** Relative to the page so it resolves on the Vite dev server and from dist/index.html (file://). */
export const PET_AVATAR_ASSET = "./pet-assets/avatar";

export const PET_AVATAR_STATES = ["idle", "thinking", "coding", "done", "error"] as const;
export type PetAvatarState = (typeof PET_AVATAR_STATES)[number];

export const PET_AVATAR_LABELS: Record<PetAvatarState, string> = {
  idle: "小魔王 · 随时待命",
  thinking: "小魔王 · 在琢磨…",
  coding: "小魔王 · 正动手…",
  done: "小魔王 · 搞定啦",
  error: "小魔王 · 踩坑了",
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
export function renderPetAvatarHtml(state: PetAvatarState, settled = false): string {
  const label = PET_AVATAR_LABELS[state];
  return `<div class="unified-pet-avatar" data-pet-state="${state}" role="img" aria-label="${label}" title="${label}">`
    + `<picture><source media="(prefers-reduced-motion: reduce)" srcset="${petAvatarSrc(state, true)}" />`
    + `<img class="unified-pet-avatar-img" src="${petAvatarSrc(state, settled)}" alt="" width="40" height="40" decoding="async" draggable="false" /></picture>`
    + `</div>`;
}

export const PET_STATE_TAGS: Record<PetAvatarState, string> = {
  idle: "随时待命",
  thinking: "在琢磨…",
  coding: "正动手…",
  done: "搞定",
  error: "踩坑了",
};

export function currentPartnerName(): string {
  const customName = typeof localStorage !== "undefined" ? (localStorage.getItem("partner_name") || "").trim() : "";
  return customName || "小魔王";
}

/** Name line above the assistant side of a turn, with a state tag. */
export function renderPetNameHtml(state: PetAvatarState): string {
  const displayName = currentPartnerName();
  return `<div class="unified-pet-name">${displayName}<span class="unified-pet-state">${PET_STATE_TAGS[state]}</span></div>`;
}

export function renderIntimatePresenceStageHtml(projectId = ""): string {
  const name = currentPartnerName();
  const hour = new Date().getHours();
  let greeting = "在呢，今天想先拿哪块开刀？";
  let sub = "直接把想法或代码丢给我，剩下的我来折腾。";
  if (hour >= 22 || hour < 5) {
    greeting = "都这么晚了，还在陪我熬夜？";
    sub = projectId
      ? `正帮你盯紧 <strong>${projectId}</strong> 呢，把活儿交给我，你靠着歇会儿。`
      : "有什么想改的、想查的直接吱一声，今晚我替你兜底。";
  } else if (hour < 11) {
    greeting = "早啊，墨镜已经擦亮了。";
    sub = projectId
      ? `今天继续推进 <strong>${projectId}</strong>，还是先随便聊聊？`
      : "今天想写点新东西，还是让我帮你巡检一遍代码？";
  } else if (hour >= 18) {
    greeting = "忙完白天的事了？来，换我们主场。";
    sub = projectId
      ? `<strong>${projectId}</strong> 随时能接着动工，听你指挥。`
      : "不管是改 Bug、审代码还是闲聊，随时奉陪。";
  } else if (projectId) {
    sub = `当前正在 <strong>${projectId}</strong> 工作区，随时能动手。`;
  }

  return `<div class="unified-presence-stage" role="region" aria-label="${name}在场">
    <div class="unified-presence-halo">
      <div class="unified-pet-avatar unified-presence-avatar" data-pet-state="idle" title="点我试试">
        <picture>
          <source media="(prefers-reduced-motion: reduce)" srcset="${petAvatarSrc("idle", true)}" />
          <img class="unified-pet-avatar-img" src="${petAvatarSrc("idle", false)}" alt="${name}" width="96" height="96" decoding="async" draggable="false" />
        </picture>
      </div>
    </div>
    <div class="unified-presence-name">${name} · 在线陪你</div>
    <h2 class="unified-presence-greeting">${greeting}</h2>
    <p class="unified-presence-sub">${sub}</p>
    <div class="unified-presence-prompts">
      <button type="button" class="unified-presence-chip" data-quick-prompt="去看看我刚改的代码，帮我把把关">👀 看看我刚改的代码</button>
      <button type="button" class="unified-presence-chip" data-quick-prompt="帮我跑一遍相关测试，看看稳不稳">🧪 跑把测试验验成色</button>
      <button type="button" class="unified-presence-chip" data-quick-prompt="今天想跟你聊聊接下来的优化思路">💭 聊聊接下来的思路</button>
    </div>
  </div>`;
}
