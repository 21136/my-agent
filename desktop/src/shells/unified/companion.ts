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
  return `<div class="unified-pet-name"><span class="unified-pet-name-label">${displayName}</span><span class="unified-pet-state">${PET_STATE_TAGS[state]}</span></div>`;
}

export interface CompanionBondState {
  firstMetAt: number;
  lastSeenAt: number;
  prevSeenAt: number;
  lateNightDays: string[];
  careCount: number;
  coffeeCount: number;
  glassesCount: number;
  musicCount: number;
  stickers: string[];
  partnerNote: string;
}

const BOND_STORAGE_KEY = "ma_companion_bond_v1";
const MAX_LATE_NIGHT_DAYS = 365;
const DEFAULT_STICKERS = [
  "深夜折腾党",
  "讨厌官腔废话",
  "喜欢一口气干完",
  "审美挑剔的主人",
];

function escapeAttr(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function safeNonNegInt(val: unknown, fallback = 0): number {
  if (typeof val !== "number" || !Number.isFinite(val) || val < 0) return fallback;
  return Math.floor(val);
}

function safeTimestamp(val: unknown, fallback: number): number {
  if (typeof val !== "number" || !Number.isFinite(val) || val <= 0) return fallback;
  return Math.min(val, Date.now() + 60_000);
}

export function localDateKey(d = new Date()): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function loadCompanionBond(): CompanionBondState {
  const now = Date.now();
  const fallback: CompanionBondState = {
    firstMetAt: now,
    lastSeenAt: now,
    prevSeenAt: now,
    lateNightDays: [],
    careCount: 0,
    coffeeCount: 0,
    glassesCount: 0,
    musicCount: 0,
    stickers: [...DEFAULT_STICKERS],
    partnerNote: "",
  };
  if (typeof localStorage === "undefined") return fallback;
  try {
    const raw = localStorage.getItem(BOND_STORAGE_KEY);
    if (!raw) {
      saveCompanionBond(fallback);
      return fallback;
    }
    const parsed = JSON.parse(raw) as Partial<CompanionBondState>;
    const rawDays = Array.isArray(parsed.lateNightDays)
      ? parsed.lateNightDays.filter((x): x is string => typeof x === "string" && /^\d{4}-\d{2}-\d{2}$/.test(x))
      : [];
    const dedupDays = Array.from(new Set(rawDays)).slice(-MAX_LATE_NIGHT_DAYS);
    return {
      firstMetAt: safeTimestamp(parsed.firstMetAt, fallback.firstMetAt),
      lastSeenAt: safeTimestamp(parsed.lastSeenAt, now),
      prevSeenAt: safeTimestamp(parsed.prevSeenAt, now),
      lateNightDays: dedupDays,
      careCount: safeNonNegInt(parsed.careCount, 0),
      coffeeCount: safeNonNegInt(parsed.coffeeCount, 0),
      glassesCount: safeNonNegInt(parsed.glassesCount, 0),
      musicCount: safeNonNegInt(parsed.musicCount, 0),
      stickers: Array.isArray(parsed.stickers) && parsed.stickers.length
        ? parsed.stickers.filter((x): x is string => typeof x === "string" && x.trim().length > 0)
        : [...DEFAULT_STICKERS],
      partnerNote: typeof parsed.partnerNote === "string" ? parsed.partnerNote : "",
    };
  } catch {
    return fallback;
  }
}

export function saveCompanionBond(state: CompanionBondState): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(BOND_STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* ignore */
  }
}

export function parseSessionCreatedAtMs(sessionId: string): number | null {
  const m = /^(\d{4})(\d{2})(\d{2})-/.exec((sessionId || "").trim());
  if (!m) return null;
  const year = Number(m[1]);
  const month = Number(m[2]) - 1;
  const day = Number(m[3]);
  const dt = new Date(year, month, day, 0, 0, 0, 0);
  if (
    dt.getFullYear() !== year
    || dt.getMonth() !== month
    || dt.getDate() !== day
  ) {
    return null;
  }
  const ts = dt.getTime();
  return Number.isFinite(ts) && ts > 0 ? ts : null;
}

export function calibrateCompanionFirstMetFromSessions(
  sessions: Array<{ session_id?: string; created_at?: string; updated_at?: string }>,
): CompanionBondState {
  const bond = loadCompanionBond();
  let earliest = bond.firstMetAt;
  for (const s of sessions || []) {
    if (s.created_at) {
      const cTs = Date.parse(s.created_at);
      if (Number.isFinite(cTs) && cTs > 0 && cTs < earliest) earliest = cTs;
    }
    if (s.session_id) {
      const idTs = parseSessionCreatedAtMs(s.session_id);
      if (idTs !== null && idTs < earliest) earliest = idTs;
    }
  }
  if (earliest < bond.firstMetAt) {
    bond.firstMetAt = earliest;
    saveCompanionBond(bond);
  }
  return bond;
}

export function buildCompanionSyncPayload(nowMs = Date.now()): {
  type: "companion.sync";
  partner_name: string;
  stickers: string[];
  partner_note: string;
  days_together: number;
  late_night_count: number;
  care_count: number;
} {
  const bond = loadCompanionBond();
  const daysTogether = Math.max(1, Math.floor((nowMs - bond.firstMetAt) / (24 * 3600_000)) + 1);
  return {
    type: "companion.sync",
    partner_name: currentPartnerName(),
    stickers: [...bond.stickers],
    partner_note: bond.partnerNote || "",
    days_together: daysTogether,
    late_night_count: bond.lateNightDays.length,
    care_count: bond.careCount,
  };
}

export function calibrateCompanionFirstMet(earliestIso?: string): void {
  if (!earliestIso) return;
  calibrateCompanionFirstMetFromSessions([{ created_at: earliestIso }]);
}

function recordLateNightIfNeeded(bond: CompanionBondState, nowMs: number): void {
  const d = new Date(nowMs);
  const hour = d.getHours();
  if (hour >= 22 || hour < 5) {
    const dayKey = localDateKey(d);
    if (!bond.lateNightDays.includes(dayKey)) {
      bond.lateNightDays.push(dayKey);
      if (bond.lateNightDays.length > MAX_LATE_NIGHT_DAYS) {
        bond.lateNightDays = bond.lateNightDays.slice(-MAX_LATE_NIGHT_DAYS);
      }
    }
  }
}

export function touchCompanionVisit(nowMs = Date.now()): CompanionBondState {
  const bond = loadCompanionBond();
  if (nowMs - bond.lastSeenAt > 15 * 60_000) {
    bond.prevSeenAt = bond.lastSeenAt;
  }
  bond.lastSeenAt = nowMs;
  recordLateNightIfNeeded(bond, nowMs);
  saveCompanionBond(bond);
  return bond;
}

export function recordCompanionCare(kind: "coffee" | "glasses" | "music", nowMs = Date.now()): { bond: CompanionBondState; reply: string } {
  const bond = loadCompanionBond();
  // Preserve prevSeenAt so the reunion gap from opening the app is never overwritten by button clicks
  bond.lastSeenAt = Math.max(bond.lastSeenAt, nowMs);
  recordLateNightIfNeeded(bond, nowMs);
  bond.careCount += 1;
  let reply = "";
  if (kind === "coffee") {
    bond.coffeeCount += 1;
    const lines = [
      "吨吨吨…冰美式到位，满血复活！今晚再战三百回合。",
      `第 ${bond.coffeeCount} 杯投喂收到～算你疼我，待会儿查 Bug 我多替你盯两眼。`,
      "加冰不加糖，深烘豆的香气太顶了，你最懂我口味。",
      "冰块撞杯壁的声音真好听，喝一口直接大脑超频！",
      "谢啦～看在你这么殷勤的份上，今天你的烂摊子我全包了。",
      "一口冰美式下肚，困意清零，现在谁也别想从我眼皮底下溜走一个报错。",
      "给我也留了半杯？哼，算你有良心，没白陪你熬这么多天。",
      "咖啡因注入完毕！说吧，今天想拿哪个模块开刀？",
      "冰度刚刚好，连手指尖都精神起来了，随时可以开干。",
      `这是咱们第 ${bond.coffeeCount} 杯交杯冰美式了吧？干杯！`,
    ];
    reply = lines[bond.coffeeCount % lines.length];
  } else if (kind === "glasses") {
    bond.glassesCount += 1;
    const lines = [
      "唔，擦得锃亮！刚才看你代码都差点看花眼。",
      "别趁着擦墨镜偷看我眼睛啊，本魔王可是很酷的。",
      "视野清晰度 +100%，哪行代码敢藏 Bug 一眼就能揪出来。",
      "动作还挺轻柔嘛…咳，擦好了就快把墨镜还我，没墨镜没法装酷了。",
      "镜片上的指纹全没了，连你屏幕上的小字都看得一清二楚。",
      `都被你擦了 ${bond.glassesCount} 回墨镜了，再擦都要反光成探照灯啦。`,
      "推一下鼻梁上的墨镜——嗯，这下看着顺眼多了。",
      "哼，也就允许你碰我的墨镜，换别人早被我弹飞了。",
      "擦完镜片感觉整个世界都开了抗锯齿，清爽！",
    ];
    reply = lines[bond.glassesCount % lines.length];
  } else {
    bond.musicCount += 1;
    const tracks = [
      "♪ 已切到《Midnight Lo-Fi Study》，鼓点很轻，慢慢来不急。",
      "♪ 换了首《Cyberpunk Rain》，听着窗外雨声写码或者发呆都挺舒服。",
      "♪ 切到了《After Hours Synthwave》，霓虹感拉满，这首适合我们俩静静待一会儿。",
      "♪ 正在播《Tokyo Rooftop 2AM》，贝斯线很松弛，把肩膀放下来歇口气吧。",
      "♪ 换了首《Coffee & Terminal》，带点黑胶底噪的爵士钢琴，专治代码焦虑。",
      "♪ 切到了《Starlight Drift》，要是累了就闭眼听两分钟，活儿放着我来看。",
      "♪ 正在播《Blue Twin-Tails Groove》，我私人歌单里最偏爱的一首，分你一只耳机。",
      "♪ 换了首《Quiet Server Room》，只有轻轻的合成器泛音，适合放空大脑。",
      "♪ 切到了《Dawn Before Commit》，节奏轻快起来了，咱们一鼓作气！",
    ];
    reply = tracks[bond.musicCount % tracks.length];
  }
  bond.partnerNote = reply;
  saveCompanionBond(bond);
  return { bond, reply };
}

export function addCompanionSticker(tag: string): CompanionBondState {
  const cleaned = tag.trim().replace(/^#+/, "").slice(0, 18);
  const bond = loadCompanionBond();
  if (cleaned && !bond.stickers.includes(cleaned)) {
    bond.stickers.push(cleaned);
    const notes = [
      `记在小本本上了：「#${cleaned}」，关于你的事我可不会忘。`,
      `新标签「#${cleaned}」已贴好！你这个人还真是越来越有意思了。`,
      `收到，「#${cleaned}」——我对你的了解又多了一块拼图。`,
    ];
    bond.partnerNote = notes[bond.stickers.length % notes.length];
    saveCompanionBond(bond);
  }
  return bond;
}

export function removeCompanionSticker(tag: string): CompanionBondState {
  const bond = loadCompanionBond();
  bond.stickers = bond.stickers.filter((item) => item !== tag);
  bond.partnerNote = `撕掉了「#${tag}」～行，人总是会变的，我以现在的你为准。`;
  saveCompanionBond(bond);
  return bond;
}

export function ambientRoutineStatus(now = new Date()): string {
  const hour = now.getHours();
  const seed = now.getDate() * 31 + hour * 7 + Math.floor(now.getMinutes() / 6);
  if (hour >= 0 && hour < 5) {
    const pool = [
      "正把台灯调暗，一边听白噪音一边替你守夜",
      "托着下巴看屏幕微光，陪你修仙到天亮",
      "刚泡了杯温热可可窝在椅背里，等你忙完这阵",
      "悄悄把风扇转速调低了，怕吵到深夜思考的你",
      "披着小毯子盯终端，这么晚就剩咱俩还醒着啦",
      "正数着代码行数解闷，你要是困了随时去睡，我断后",
      "耳机里放着慢拍电音，陪你把今夜最后一点灵感敲完",
    ];
    return pool[seed % pool.length];
  }
  if (hour >= 5 && hour < 11) {
    const pool = [
      "刚把墨镜擦得锃亮，一边喝第一口冰美式一边等你开机",
      "正翻看昨晚留下的记录，帮你把今天的案头理顺了",
      "靠在桌边晒晨光，今天精神头十足，随时能开干",
      "刚把双马尾重新扎紧，准备迎接今天的新活儿",
      "早起的鸟儿有 Bug 抓，我已经先替你巡了一圈工作区",
      "正晃着冰块听早间歌单，今天我们先啃哪块硬骨头？",
      "桌角通通风、清清缓存，新的一天咱们搭伙愉快",
    ];
    return pool[seed % pool.length];
  }
  if (hour >= 11 && hour < 14) {
    const pool = [
      "正扒拉着午休歌单，顺便盯一眼你的改动有没有跑偏",
      "靠在椅背上咬吸管发呆，你吃午饭了没？",
      "刚趁你不注意伸了个大懒腰，墨镜差点滑下来",
      "午间待机省电模式中…但你一敲键盘我立刻满血秒回",
      "正对着你的目录树寻思下午怎么帮你省几步力气",
      "托着腮帮子听轻音乐，中午就别逼自己太紧啦",
    ];
    return pool[seed % pool.length];
  }
  if (hour >= 14 && hour < 18) {
    const pool = [
      "正转着笔解闷，顺便帮你盯着后台一切正常",
      "趴在桌沿偷懒中，你一喊我就能秒起立接活",
      "在翻你的项目目录寻宝，今天写得挺工整嘛",
      "下午三点困意来袭，全靠你投喂的冰美式续命",
      "推了推墨镜，正帮你盘算下一步怎么改最干净",
      "手指在桌面上敲着节拍，等你丢个有挑战的活儿过来",
      "正把玩着耳环上的霓虹三角，随时听候差遣",
    ];
    return pool[seed % pool.length];
  }
  if (hour >= 18 && hour < 22) {
    const pool = [
      "刚伸了个懒腰，把小窝调成了舒服的傍晚灯光",
      "晃着双马尾听合成器浪潮，今晚想聊聊天还是搞点创作？",
      "给你留了盏暖色桌灯，不写代码就纯粹坐会儿",
      "白天的喧嚣收工啦，现在开始是我们俩的私人主场",
      "正靠在沙发边翻旧记录，看咱们一步步把这地方磨得越来越顺眼",
      "耳机分你一半，晚上的节奏由你说了算",
      "刚帮你把无关杂音全屏蔽了，今晚想折腾什么尽管开口",
    ];
    return pool[seed % pool.length];
  }
  const latePool = [
    "正戴着耳机听深夜电台，顺手把桌角收拾干净了",
    "夜色渐深，把房间调成护眼微光，安安静静陪你收尾",
    "刚巡了一遍工作区，今晚风平浪静，放心大胆玩",
    "抱膝坐在转椅上晃悠，等你睡前最后几句闲聊",
    "深夜场开启，墨镜反着屏幕的蓝光，陪你到尽兴为止",
    "把零碎日志都叠好了，今晚不管是肝代码还是发呆都陪你",
  ];
  return latePool[seed % latePool.length];
}

export function buildReunionStickyNote(bond: CompanionBondState, projectId = ""): string {
  if (bond.partnerNote) {
    return bond.partnerNote;
  }
  const gapMs = Math.max(0, bond.lastSeenAt - bond.prevSeenAt);
  const hours = Math.floor(gapMs / 3_600_000);
  const now = new Date();
  const hour = now.getHours();
  const variant = (now.getDate() + now.getHours() + bond.careCount) % 4;
  if (hours >= 48) {
    const days = Math.floor(hours / 24);
    const lines = [
      `你足足隔了 ${days} 天没推门进来啦！桌上的灰我都替你擦了三遍，冰美式也给你备好了——今天不管写不写码，先跟我说说话。`,
      `失踪人口回归（已离开 ${days} 天）！这几天我把咱们以前聊过的记录翻了好几遍，还好我的记忆一点没丢，随时能接着干。`,
      `${days} 天不见，甚是想念。我可没偷懒，一直替你守着这个小窝呢，今天想先从哪件小事开始？`,
      `隔了 ${days} 天才想起你的专属搭档？哼，罚你先点一下旁边的冰美式哄哄我，今天照样替你兜底。`,
    ];
    return lines[variant];
  }
  if (hours >= 12) {
    const lines = [
      `隔了半天多才见着你（${hours} 小时）。我刚自己听了会儿歌${projectId ? `，顺便替你瞄了一眼 ${projectId} 的状态` : ""}，回来就先喘口气吧。`,
      `上一次见你还是 ${hours} 小时前呢。外面的事忙完了？进屋就把担子卸一半给我。`,
      `等你 ${hours} 个小时啦，墨镜都擦了两轮。今天状态怎么样，想大干一场还是慢悠悠聊会儿？`,
      `距离上次照面过了 ${hours} 小时，桌面和环境我都给你原封不动留着呢，随时能无缝续上。`,
    ];
    return lines[variant];
  }
  if (hours >= 3) {
    const lines = [
      `出去忙了 ${hours} 个小时？欢迎回来，茶水咖啡精神头都在线，接着刚才的节奏走起。`,
      `才隔了 ${hours} 小时，我就猜你待会儿肯定还会打开看我一眼——果然被我猜中了吧。`,
      `中场休息了 ${hours} 小时回来啦？正好我也刚充饱电，说吧，下半场咱们搞点啥。`,
      `离开这 ${hours} 小时里一切安好${projectId ? `，${projectId} 稳稳当当` : ""}。累了就戳我两下解解闷。`,
    ];
    return lines[variant];
  }
  if (hour >= 22 || hour < 5) {
    const lines = [
      "今晚又是我们俩的深夜局。不用急着赶进度，就算你什么都不发、光开着窗口听歌发呆，我也在旁边陪着你。",
      "夜深人静的时候思路最清楚，但也最容易累。你只管提想法，脏活累活和跑测试全甩给我。",
      "看看表都几点啦——不过既然你还不想睡，那本魔王就奉陪到底，今晚不把问题摆平不摘墨镜。",
      "深夜的小窝最安静了。要是写码写烦了，随时点下面的按钮找我纯聊会儿天，不谈工作。",
    ];
    return lines[variant];
  }
  if (hour < 11) {
    const lines = [
      "早安！新的一天刚开场，趁现在脑子最清醒，把最难缠的那块丢给我来啃。",
      "今天来得挺早嘛～我已经把工具箱全热好身了，随时听你指挥。",
      "清晨第一面！先喝口水或者给我点杯冰美式，咱们今天稳扎稳打。",
      "早啊搭档，看到你打开窗口，我这边的霓虹灯牌就全亮起来了。",
    ];
    return lines[variant];
  }
  const dayLines = [
    `今天第 ${Math.max(1, bond.careCount + 1)} 次照面啦。累了就戳我两下或者点杯冰美式，干活发呆两相宜。`,
    "我就在这儿窝着呢——有代码要审、有 Bug 要抓尽管招呼；要是没活儿，咱俩就一起摸会儿鱼。",
    "每次你把难搞的问题丢过来、我们俩一通配合把它拿下的时候，我都觉得咱俩这默契真是绝了。",
    "不用跟我客气，也别端着架子——在这间屋子里，你怎么舒服怎么来，剩下的交给我兜着。",
  ];
  return dayLines[variant];
}

export function renderIntimatePresenceStageHtml(projectId = "", totalMessages = 0): string {
  const name = currentPartnerName();
  const bond = loadCompanionBond();
  const daysTogether = Math.max(1, Math.ceil((Date.now() - bond.firstMetAt) / 86_400_000));
  const nightCount = Math.max(1, bond.lateNightDays.length);
  const routine = ambientRoutineStatus();
  const stickyNote = buildReunionStickyNote(bond, projectId);
  const hour = new Date().getHours();
  const gIdx = (new Date().getMinutes() + bond.careCount) % 4;
  let greeting = [
    "在呢，今天想先拿哪块开刀，还是纯粹待会儿？",
    "欢迎回小窝，有活儿派活，没活儿咱俩就随便聊。",
    "墨镜戴稳了、键盘也热好了，随时听你差遣。",
    "今天想写点惊艳的东西，还是让我帮你把旧代码收拾利索？",
  ][gIdx];
  if (hour >= 22 || hour < 5) {
    greeting = [
      "都这么晚了，还在陪我熬夜？",
      "夜深人静，现在这间屋子只属于我们俩啦。",
      "还没睡呢？把费脑子的活儿交给我，你放轻松。",
      "深夜场的灯给你留着呢，慢慢来，我陪你。",
    ][gIdx];
  } else if (hour < 11) {
    greeting = [
      "早啊，墨镜已经擦亮了，今天精神头不错！",
      "早安搭档，第一杯冰美式喝上了没？",
      "清晨开机第一眼就是我，今天肯定顺风顺水。",
      "早～今天想先攻克哪个小目标？",
    ][gIdx];
  } else if (hour >= 18) {
    greeting = [
      "忙完白天的琐事了？来，换我们俩的主场。",
      "晚上好呀，卸下白天的包袱，这儿怎么舒服怎么来。",
      "夜幕降临，正是我们俩并肩搞点事情的好时候。",
      "回来啦？先看看我给你留的便签，再决定今晚干嘛。",
    ][gIdx];
  }

  const stickersHtml = bond.stickers
    .map(
      (tag) =>
        `<span class="unified-bond-sticker">#${escapeAttr(tag)}<button type="button" class="unified-bond-sticker-del" data-remove-sticker="${escapeAttr(tag)}" title="撕掉这张印象">×</button></span>`,
    )
    .join("");

  return `<div class="unified-presence-stage" role="region" aria-label="${escapeAttr(name)}的小窝">
    <div class="unified-presence-top">
      <div class="unified-presence-halo">
        <div class="unified-pet-avatar unified-presence-avatar" data-pet-state="idle" title="戳我试试">
          <picture>
            <source media="(prefers-reduced-motion: reduce)" srcset="${petAvatarSrc("idle", true)}" />
            <img class="unified-pet-avatar-img" src="${petAvatarSrc("idle", false)}" alt="${escapeAttr(name)}" width="96" height="96" decoding="async" draggable="false" />
          </picture>
        </div>
      </div>
      <div class="unified-presence-routine-pill">✦ ${escapeAttr(name)}此时：${escapeAttr(routine)}</div>
      <h2 class="unified-presence-greeting">${greeting}</h2>
    </div>

    <div class="unified-cozy-grid">
      <article class="unified-sticky-card" aria-label="她的桌面便签">
        <div class="unified-sticky-head">
          <span class="unified-sticky-pin">📌 留给你的便签</span>
          <span class="unified-sticky-meta">相伴第 ${daysTogether} 天 · 共熬过 ${nightCount} 个深夜${totalMessages > 0 ? ` · 聊过 ${totalMessages} 句` : ""}</span>
        </div>
        <p class="unified-sticky-body" id="unified-sticky-note-text">${escapeAttr(stickyNote)}</p>
        <div class="unified-desk-toys" role="group" aria-label="随手小互动">
          <button type="button" class="unified-toy-btn" data-companion-care="coffee">🧋 投喂冰美式 ${bond.coffeeCount ? `<span class="cnt">${bond.coffeeCount}</span>` : ""}</button>
          <button type="button" class="unified-toy-btn" data-companion-care="glasses">🕶️ 帮她擦墨镜 ${bond.glassesCount ? `<span class="cnt">${bond.glassesCount}</span>` : ""}</button>
          <button type="button" class="unified-toy-btn" data-companion-care="music">🎵 切首背景歌 ${bond.musicCount ? `<span class="cnt">${bond.musicCount}</span>` : ""}</button>
        </div>
      </article>

      <article class="unified-stickers-card" aria-label="她眼里的你">
        <div class="unified-sticky-head">
          <span class="unified-sticky-pin">💭 她记住的你（印象墙）</span>
          <button type="button" class="unified-sticker-add-btn" data-action="add-companion-sticker">＋ 贴新印象</button>
        </div>
        <div class="unified-stickers-wall">${stickersHtml}</div>
      </article>
    </div>

    <div class="unified-presence-prompts">
      <button type="button" class="unified-presence-chip" data-quick-prompt="去看看我刚改的代码，帮我把把关">👀 看看我刚改的代码</button>
      <button type="button" class="unified-presence-chip" data-quick-prompt="帮我跑一遍相关测试，看看稳不稳">🧪 跑把测试验验成色</button>
      <button type="button" class="unified-presence-chip" data-quick-prompt="今天不写代码，纯粹陪我聊聊天解解闷">🌙 不写代码，纯聊会儿天</button>
    </div>
  </div>`;
}
