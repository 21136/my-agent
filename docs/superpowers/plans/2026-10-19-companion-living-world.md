# 小魔王生活世界 Implementation Plan

> **已被 2026-10-20 的方向修订取代：请勿执行下方旧的页面/顶栏入口任务。** 当前方向是保留本地世界状态，并只在普通非项目聊天回合注入经过校验的临时背景；不新增页面、入口、通知，也不改侧栏和 dock。具体以关联设计规格和当前源码为准。

---

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Each task has a review checkpoint; do not skip the visual prototype approval gate.

**Goal:** 在不打断现有聊天与工作台的前提下，为小魔王建立一个会随离线时间推进、有个人生活线和共同经历线、但不会给用户造成照料负担的可进入生活世界。

**Architecture:** 先做独立视觉原型确认空间尺度与入口，再以纯 TypeScript 状态转换器和独立本地存储建立世界；最后接入统一工作台现有主内容区。世界推进不依赖常驻进程或模型轮询；现实事件只来自应用明确事件，主动分享留在世界入口内，用户主动选择后才转入现有聊天。

**Tech Stack:** TypeScript、Vite、Electron unified shell、浏览器 `localStorage`、Node `node:test`。

**Spec:** `F:\my-agent-main\docs\superpowers\specs\2026-10-19-companion-living-world-design.md`

## Global Constraints

- 保留当前工作区中所有与本功能无关的已修改、已暂存和未跟踪文件；不 reset、clean、checkout、广泛暂存、提交或覆盖。
- 保留现有小魔王形象与聊天会话身份；世界数据与 `CompanionBondState`、聊天原文和项目数据分开保存。
- 世界时间仅在应用启动/进入世界时本地追进；不新增常驻后台进程、定时 LLM 请求、网络轮询、文件扫描或全局输入监听。
- 虚构生活只作为世界内叙事；项目、文件、命令和执行结果等现实事实必须来自明确应用事件，缺少来源时不生成事实性陈述。
- 世界事件不得自动发送聊天、执行工具、修改文件或替用户确认；“聊聊”入口只切回既有聊天，不自动发送消息。
- 不新增独立 Electron 窗口或侧栏导航 tab；世界作为 unified shell 主内容区的可切换页面。
- 冻结既有左侧导航、项目侧栏和小魔王 dock：不改它们的 DOM、宽度、排序、视觉样式或现有点击行为；世界入口仅放在主工作区既有顶栏菜单中。
- 先完成 Task 1 并由用户审阅视觉原型；未获认可前，不进入生产界面整合。
- 本计划不要求提交或推送；验证仅针对新增/修改的功能边界，不重启用户正在使用的客户端。

---

### Task 1: 制作独立生活世界视觉原型并确认入口与空间尺度

**Files:**
- Create: `F:\my-agent-main\desktop\mockups\companion-living-world-prototype.html`
- Create: `F:\my-agent-main\desktop\mockups\companion-living-world-prototype.css`

**Interfaces:**
- 原型是静态浏览器页面，不导入生产模块，不写入 localStorage，不触发桌面/聊天行为。
- 画面至少包含：占主视觉的大型生活空间、保留的小魔王形象、她自己的活动/目标、共同经历时间线，以及一个不抢眼的“回到聊天”入口。
- 原型提供“空间/时段切换”和“个人线/共同线切换”交互，用来观察场景尺度；不加入好感、饥饿、签到、惩罚或 KPI。

- [x] **Step 1: 建立原型结构与静态假数据**

页面使用独立 CSS，布局按 1440×900 优先、1280×720 可用设计；假数据需清楚标注为原型内容。主场景占主要视线，事件与个人目标作为可滚动的次级内容，不将世界做成统计仪表盘。

- [x] **Step 2: 给原型交互加最小状态**

点击场景/时段控件只切换本页的 `data-scene` / `data-time`；点击个人线/共同线切换事件列表。页面不创建聊天窗口，不向桌面发送通知。

- [x] **Step 3: 手动检查原型并停在用户评审点**

用可见静态预览检查主空间、长内容和交互；浏览器实测 1280×720 与 390×844 均无横向溢出。用户反馈“挺不错”并确认既有侧栏必须原样保留；视觉方向通过，继续进入状态层。

### Task 2: 建立可独立测试的世界状态与确定性离线推进

**Files:**
- Create: `F:\my-agent-main\desktop\src\shells\unified\companion-world.ts`
- Create: `F:\my-agent-main\desktop\tests\companion-world.test.ts`

**Interfaces:**

```ts
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

export function createDefaultCompanionWorld(nowMs: number): CompanionWorldState;
export function normalizeCompanionWorld(value: unknown, nowMs: number): CompanionWorldState;
export function advanceCompanionWorld(state: CompanionWorldState, nowMs: number): {
  state: CompanionWorldState;
  addedEvents: CompanionWorldEvent[];
};
export function recordExplicitSharedEvent(
  state: CompanionWorldState,
  input: { id: string; title: string; body: string; sourceId: string },
  nowMs: number,
): CompanionWorldState;
```

- [x] **Step 1: Write failing tests for default state, elapsed-time idempotence, pause, malformed state, and event provenance**

Cover: same timestamp produces no additional progress; a positive elapsed interval advances the personal line without requiring user input; paused state does not progress; malformed/unversioned data returns a valid default; a shared event cannot be recorded without an explicit non-empty `sourceId`; duplicate event IDs are ignored.

- [x] **Step 2: Run only the focused test file and confirm RED**

Run from `F:\my-agent-main\desktop`:

```powershell
node --test tests/companion-world.test.ts
```

Expected: module exports are missing and focused tests fail.

- [x] **Step 3: Implement pure state transitions**

Use injected timestamps only; do not read `Date.now()` inside transition functions. Compute elapsed whole-day progression arithmetically rather than replaying an unbounded loop. Keep generated events capped to the latest meaningful milestone so long absences cannot create a notification backlog. Generated personal events use `origin: "simulated"`; they must not claim a project/file/command was inspected or changed.

- [x] **Step 4: Run the focused tests and confirm GREEN**

Run the same focused command. Confirm no test writes to the user's real browser storage and existing `CompanionBondState` behavior remains untouched.

### Task 3: Add isolated, recoverable local persistence and initiative gating

**Files:**
- Create: `F:\my-agent-main\desktop\src\shells\unified\companion-world-store.ts`
- Create: `F:\my-agent-main\desktop\tests\companion-world-store.test.ts`
- Modify: `F:\my-agent-main\desktop\src\shells\unified\companion-world.ts`

**Interfaces:**

```ts
export const COMPANION_WORLD_STORAGE_KEY = "ma_companion_world_v1";
export function loadCompanionWorld(storage: Pick<Storage, "getItem" | "setItem">, nowMs: number): CompanionWorldState;
export function saveCompanionWorld(
  state: CompanionWorldState,
  storage: Pick<Storage, "setItem">,
): boolean;
export function selectWorldShareCandidate(
  state: CompanionWorldState,
  context: { busy: boolean; confirmPending: boolean; composing: boolean },
): CompanionWorldEvent | null;
```

- [x] **Step 1: Write failing store/gate tests using an in-memory `Storage` double**

Assert versioned key isolation, migration-safe fallback, corrupt JSON recovery, write failure returns `false`, and share candidates are suppressed when paused, muted, chat is busy, confirmation is pending, or the user is composing. Assert a dismissed/seen event is not selected again.

- [x] **Step 2: Run the focused store tests and confirm RED**

```powershell
node --test tests/companion-world-store.test.ts
```

- [x] **Step 3: Implement tolerant storage and non-intrusive selection**

Read only `ma_companion_world_v1`; never reuse or mutate `ma_companion_bond_v1`. Catch storage/JSON errors and keep the chat usable. A candidate remains a world event; this function must not send chat or invoke tools.

- [x] **Step 4: Run both world test files**

```powershell
node --test tests/companion-world.test.ts tests/companion-world-store.test.ts
```

Expected: both suites pass; previously stored world data is normalized rather than trusted blindly.

### Task 4: Render the world as a separate main-stage view without resetting chat

**Files:**
- Create: `F:\my-agent-main\desktop\src\shells\unified\companion-world-view.ts`
- Create: `F:\my-agent-main\desktop\src\shells\unified\companion-world-menu.ts` (pure markup/binding helper for the topbar entry)
- Create: `F:\my-agent-main\desktop\src\shells\unified\companion-world.css`
- Modify: `F:\my-agent-main\desktop\src\shells\unified\topbar.ts` (add one world action to the existing overflow menu; preserve every existing menu item and layout)
- Modify: `F:\my-agent-main\desktop\src\shells\unified\index.ts` (stage mount, focus switching, event wiring, persistence)
- Create: `F:\my-agent-main\desktop\tests\companion-world-view.test.ts`

**Interfaces:**

```ts
export function renderCompanionWorldView(state: CompanionWorldState, partnerName: string, shareCandidateId: string | null, activeLane: WorldLane): string;
// Rendered actions: data-world-action="return-workbench|select-scene|select-lane|mark-seen|dismiss|pause|mute"
```

- [x] **Step 1: Write failing view tests**

Check escaped user-controlled strings, both personal/shared lanes, paused/muted state, and that event actions are buttons with accessible names. Ensure no source content is inserted as trusted HTML.

- [x] **Step 2: Add a dedicated world stage and focus state**

Add a `<section id="unified-world">` sibling to existing chat/document/projects/settings/music sections. Use a separate `worldOpen` display flag in `index.ts`; do not change `MainFocus`, `RailTab`, `rail.ts`, or `projectState.mainFocus`. Hide existing main-stage children and the composer while world is open, then restore the same previously selected main focus and sidebar state when leaving.

- [x] **Step 3: Add the entry action without stealing project switching**

Add “生活世界” as an item inside the existing main-workbench topbar overflow menu and route through the current topbar action callback. Do not edit left-rail/sidebar/dock markup or CSS, do not add a `RailTab`, and do not change existing menu actions. Returning restores the focus and rail selection that were active before entering the world.

- [x] **Step 4: Wire world state and user-selected actions**

On shell mount, load and advance world state once, then persist it. On world entry, render current scene and both life lanes. Returning only closes the main-stage world view; the previous chat DOM, draft, session, main focus, and rail selection remain intact. It must not call `createChatSession`, clear `chat.model.blocks`, change session IDs, or send a message. Mark-seen/dismiss/pause/mute update only world state.

- [x] **Step 5: Run focused tests and confirm GREEN**

```powershell
node --test tests/companion-world-view.test.ts tests/companion-world.test.ts tests/companion-world-store.test.ts
```

### Task 5: Connect only explicit app events and close the quiet-share loop

**Files:**
- Create: `F:\my-agent-main\desktop\src\shells\unified\companion-world-app-events.ts`
- Modify: `F:\my-agent-main\desktop\src\shells\unified\index.ts`
- Modify: `F:\my-agent-main\desktop\src\shells\unified\companion-world.ts`
- Modify: `F:\my-agent-main\desktop\tests\companion-world.test.ts`
- Modify: `F:\my-agent-main\desktop\tests\companion-world-view.test.ts`

**Interfaces:**
- `verifiedProjectEventToSharedEvent(event)` accepts only successful `project.verify.done` events and returns an opaque stable source fingerprint; the index adapter records it through `recordExplicitSharedEvent`.
- Gate context derives from the existing unified chat state (`chat.isWorking()`, `chat.model.confirmPending`, composer focus/input); no OS-level activity inference.

- [x] **Step 1: Test the source boundary and quiet gate**

Test that `recordExplicitSharedEvent` rejects an empty source ID, simulated personal events are not treated as shared, the project-verification adapter rejects failed or unrecognized events, and busy/composing/confirm states produce no visible candidate. Test that the same successful verification payload maps to the same deduplicated source ID.

- [x] **Step 2: Select only a concrete application event with success semantics**

Use only the typed `project.verify.done` application event when both `ok` and `passed` are true. Derive a stable opaque source fingerprint from its command/path/exit-code tuple without storing those raw values; do not infer success from an assistant reply or generic tool completion.

- [x] **Step 3: Add a quiet in-world share card**

Render at most one eligible candidate in the world stage; never issue a toast, OS notification, chat message, or follow-up after dismissal. “聊聊” marks the candidate seen and restores the previous workbench view; it never sends a message.

- [x] **Step 4: Run focused source/gate tests**

```powershell
node --test tests/companion-world.test.ts tests/companion-world-store.test.ts tests/companion-world-view.test.ts
```

### Task 6: Verify the feature boundary without restarting the active client

**Files:**
- No additional source files unless a focused regression is discovered.

- [x] **Step 1: Run relevant existing companion tests**

From `F:\my-agent-main\desktop`:

```powershell
node --test tests/unified-companion.test.ts tests/companion-world.test.ts tests/companion-world-store.test.ts tests/companion-world-view.test.ts
```

- [x] **Step 2: Run the desktop production build**

```powershell
npm run build
```

Expected: TypeScript compilation and Vite build complete successfully.

- [ ] **Step 3: Perform manual acceptance in an isolated preview only**

Manual in-app visual acceptance is pending: the active Electron client was not restarted. Unit tests and the production build cover state/view contracts, but do not prove rendered focus preservation. When a safe preview is available, verify world entry from the topbar menu and exact left-rail/sidebar/dock preservation before claiming full acceptance.

---

## Historical Plan Self-Review (superseded)

The checks below belonged to the old page-based direction and are no longer the current acceptance criteria. Use the 2026-10-20 direction revision above and the revised design spec.







