# 伴侶壳 · 桌宠（pet）

> 版本 **0.2.1** · 2026-07-18  
> **状态**：`implemented`（M0 + **M1 done** · 2026-07-13；**DOC-01** pet→daily 映射 §1.3）  
> 关联：[DESKTOP.md](./DESKTOP.md) §3.3.6 · [DAILY-SHELL.md](./DAILY-SHELL.md) · [TURN-FEEDBACK.md](./TURN-FEEDBACK.md) · [FILES-DROP.md](./FILES-DROP.md) · [TASKS.md](./TASKS.md)

---

## 0. 已决摘要

### M0（不改）

| ID | 决议 |
|----|------|
| **P0** | 桌宠是 **默认入口**；重活进 **工作台**（grow / daily / project 全窗） |
| **P1** | 桌宠 **UI 壳** = `pet`；**后端会话** = `daily`（`shell_switch` · `shell_sessions.daily`） |
| **P2** | 桌宠与工作台 **WS 互斥**（`session:control` suspend / resume） |
| **P3** | busy 仅 **光球 mood**（idle / listening / busy / nudge）；**不**全窗 `is-agent-busy` 染色 |
| **P4** | `confirm.request` 时气泡 **自动展开**；确认卡在气泡内 |
| **P5** | 关窗 = **缩托盘**；托盘退出才杀 sidecar |
| **P6** | 聊天区默认只展示 **最近 6 轮**（UI 裁剪；服务端 daily 会话仍全量） |

### M1（2026-07-13 评审 · 已决）

| ID | 决议 |
|----|------|
| **P7** | **重活接引**：**部分场景自动开工作台**；其余 notice + 一键（§3.1.1 A/B 档） |
| **P8** | **拖放**：与 daily **完全同规则**（FILES-DROP F1–F11；落点 F5 `_drops/`） |
| **P9** | **6 轮裁剪**：`recall` 期间 **临时多显示几轮**（§3.2.1） |
| **P10** | **M1a 可拆开 ship**；推荐顺序 **i3 → i2 → i1**（或 i2+i3 打包、i1 单发） |
| **P11** | **govern**：桌宠侧 **仅 B 档**（不自动开）；用户点「去工作台」时 **落 grow**，**永不**落 govern 占位壳 |
| **P12** | **气泡视觉**：长期 **白底简洁** + 轻 accent；**不**搬 daily Amp 霓彩进气泡 |
| **P13** | **拖放入口**：M1 **须先展开气泡**；收起态拖到光球 **defer → M2**（T-pet-i4b） |

实现：`desktop/pet.html` · `desktop/src/pet-main.ts` · `desktop/src/shells/pet/` · `electron/main.ts` 双窗。

---

## 1. 动机与定位

### 1.1 一句话

**有个伴** — 蹲在屏幕一角，随时能聊两句；不抢主屏、不像车间。

### 1.2 与三壳分工

```text
pet（姿态）  ──后端──►  daily 会话线
workbench    ──可切──►  grow / daily / project 三线独立会话
```

| 壳 | 心智 | 桌宠是否承担 |
|----|------|--------------|
| **pet** | 陪伴、轻聊、随手问 | ✅ 主场景 |
| **daily** | 畅谈、接梗、workflow、qa、recall | ✅ 后端同线 |
| **grow** | 造工具、审 proposal、改 evolve | ❌ → 接引工作台 |
| **project** | 做产物、计划门、验收 | ❌ → 接引工作台 |

### 1.3 DOC-01 · pet→daily 会话映射（**已定 · T-1806-doc-01**）

> **一句话**：桌宠是 **UI 姿态**；聊天、历史、拖放、confirm 全部走 **`daily` 会话线**。  
> 读者须知：`pet` **不是** `shell_sessions` 的第四个键；与工作台切到「日用」时 **共用** 同一 `session_id`。

| 层 | 伴侶（pet） | 工作台 · 日用（daily） | 说明 |
|----|-------------|------------------------|------|
| **UI 壳 / 窗体** | `pet`（光球 + 窄气泡） | `daily`（Amp 全窗） | 两套前端；视觉与布局独立 |
| **Backend shell** | **`daily`** | `daily` | `desktop/src/shells/pet/index.ts`：`BACKEND_SHELL = "daily"` |
| **`shell.switch`** | 发 `{ shell: "daily" }` | 同左 | 切到伴侶 = 切到 daily 会话，**不是** `shell: "pet"` |
| **`data/state.json` · `shell_sessions`** | 键 **`daily`** → `session_id` | 同键同值 | grow / daily / project **三线**；无 `pet` 键 |
| **消息落盘** | `data/sessions/<daily_id>/` | 同路径 | 伴侶与工作台日用 **历史互通** |
| **拖放落点** | `_drops/<daily_session_id>/` | 同左 | FILES-DROP 与 daily 完全同规则（P8） |
| **`active_shell`（服务端）** | 值为 **`daily`** | `daily` | 伴侣连上后 sidecar 活跃壳是 daily |
| **WS 连接** | 与工作台 **互斥**（P2） | 同左 | 不同时双连；suspend / resume 换窗 |

**对照 · 三线独立（T-1116）**：

```text
shell_sessions.grow     → grow 专用 session
shell_sessions.daily    → daily 专用 session  ← pet 与工作台 daily 共用
shell_sessions.project  →（经 project_sessions 再分项目）
# 不存在 shell_sessions.pet
```

**实现锚点**：`shells/pet/index.ts`（`BACKEND_SHELL` · `shellSwitch("daily")`）· `shell_switch.py`（仅 grow/daily/project）· [DESKTOP.md](./DESKTOP.md) §3.3.6 / §3.9.2。

### 1.4 非目标（M1 也不做）

| 非目标 | 理由 |
|--------|------|
| 桌宠内切 grow / project | 违背「重活进工作台」；窄气泡装不下侧栏 / proposal 顶栏 |
| 桌宠独立第四条会话线 | 破坏 `shell_sessions.daily` 共用设计 |
| 复制 daily Amp 霓彩全窗动效 | 违背「不抢主屏」；见 **P12** |
| 双窗同时连 WS | 与 P2 冲突 |
| 星图 / 深木咖啡馆类视觉 | 见 DAILY-SHELL §1 否决史 |
| 工作台 `govern` 占位当落点 | 壳未实现（T-904h defer）；见 **P11** |

---

## 2. M0 现状与缺口（代码对照）

### 2.1 已有

- 光球 + 窄气泡（~340×480）+ 输入 + 发送
- `BACKEND_SHELL = "daily"`；`shell.switch(daily)`
- mood 四态 + idle 随机眨眼
- confirm 小卡片
- 「工作台」按钮 → `app:open-workbench`
- click-through 收起态；展开时接收指针
- 气泡：暖白半透明 + 轻粉 accent（`pet.css`）

### 2.2 缺口（M1）

| 缺口 | 关联 |
|------|------|
| 无 `ui.route` 感知 | **P7** · T-pet-i1 |
| 无 recall 高亮 / 扩窗 | **P9** · T-pet-i2 |
| 轮次反馈粗 | T-pet-i2 |
| 无拖放 | **P8** · **P13** · T-pet-i4 |
| 历史附件不渲染 | T-pet-i3 |
| 位置固定 | T-pet-i5（M2） |
| 往返无提示 | T-pet-i6（M2） |

---

## 3. M1 规格

### 3.0 发布策略（**P10 · 已决**）

M1a **不强制** i1+i2+i3 同批发版；三项 **无硬依赖**，可独立验收。

| 顺序 | 任务 | 说明 |
|------|------|------|
| 1 | **T-pet-i3** | 风险最低；纯历史展示 |
| 2 | **T-pet-i2** | 回顾体验；不动窗口路由 |
| 3 | **T-pet-i1** | 价值最大；单独回归 A/B 档与互斥 WS |

**可选打包**：`i2 + i3` 同一小版本，`i1` 另发。

---

### 3.1 T-pet-i1 · 重活接引

#### 3.1.1 A/B 档（**P7**）

桌宠 WS 收到 `ui.route`（`auto=true`）：

| 档位 | 条件（对齐 `activity_router` reason） | 行为 |
|------|--------------------------------------|------|
| **A · 自动开** | proposal 待处理；计划待确认；养 agent；造/改工具；workspace 项目/开发；探索 evolve | suspend 桌宠 → 开工作台 → `shell.switch(target)` + 短 notice |
| **B · 仅提示** | 续接 coding/项目；**治理/审查（govern）**；其他弱信号 | nudge + notice +「去工作台」；**不**自动跳窗 |
| **忽略** | `shell = daily` | — |

#### 3.1.2 govern（**P11**）

- `ui.route.shell === "govern"` → **恒 B 档**（不自动开工作台）。
- 用户点「去工作台」→ `shell.switch("grow")`（**非** `govern`）+ notice：「治理壳未就绪，已在生长壳打开」。
- T-904h govern 壳实现后，再评估是否改为 `shell.switch("govern")`。

#### 3.1.3 其他约定

| 项 | 决议 |
|----|------|
| `topics_added` | notice 内一行 muted 展示 |
| B 档 notice 消失 | 下一条 user 消息，或用户手动关 |
| project 路由 | 对齐 `shell.switch` 既有 `project_id` 载荷 |

**验收**：有待审 proposal → A 档自动进 grow；说「跑 review」→ B 档；点去工作台 → grow 非占位页。

---

### 3.2 T-pet-i2 · recall + 轮次反馈

#### 3.2.1 可见轮次（**P9**）

| 状态 | `visibleTurnLimit` |
|------|-------------------|
| 平常 | **6** |
| `recall` 活跃期 | `max(6, k + 2)` 且覆盖全部 `recallHighlightTurns`（**k = 4**） |
| 结束条件 | 该轮 assistant 流式结束 → 恢复 6 |

#### 3.2.2 状态栏

| 事件 | `#pet-status` |
|------|---------------|
| `turn.start` recall | 正在回顾… |
| `turn.start` qa | 想一下… |
| `turn.start` execute / `tool.start` | 正在查… |
| `confirm.request` | 等你确认… |
| 默认 | 就绪 |

**不做**：过程块、顶栏 intent、`turn.notice`（M1 省略）、`session.banner` 记忆仪表盘。

---

### 3.3 T-pet-i3 · 历史附件回放

- 用户消息 → `formatUserMessageHtml`（`user-message.ts`）。
- chip 窄栏可略缩小；M1 **不** `openPath`，仅 tooltip。

---

### 3.4 T-pet-i4 · 拖放（**P8**）

与 daily **同一套** `mountFileDrop` + FILES-DROP F1–F11；落点 `_drops/<daily_session_id>/`。

#### 3.4.1 入口（**P13**）

| 态 | M1 |
|----|-----|
| 气泡 **已展开** | ✅ 可拖入 composer |
| **收起**（仅光球） | ❌；提示先点光球展开 |

**M2 · T-pet-i4b（defer）**：拖到光球 → 自动展开 + 挂 chip（需处理 click-through / 窗体扩命中区）。

---

### 3.5 视觉（**P12**）

| 层 | 约定 |
|----|------|
| 光球 | 像素角色 + mood 动画（主视觉） |
| 气泡 | **暖白半透明**、细边框、小字号；**保持简洁** |
| accent | 现有粉系标签/边框即可；**不**引入 Amp 霓彩底/扫光 |
| busy | **仅光球**；气泡背景不动态染色 |

工作台 daily Amp 与桌宠气泡 **刻意区分**：伴侶安静、全窗嗨。

---

### 3.6 T-pet-i5 · 拖拽定位（M2）

收起态拖光球定位置；`data/state.json` → `pet_window`。展开态拖拽 TBD。

---

### 3.7 T-pet-i6 · 往返提示（M2）

resume 桌宠时 daily 线有未读 assistant → 角标或一次性 notice。

---

### 3.8 T-pet-i7 · reduced-motion（M2）

`prefers-reduced-motion: reduce` 时关闭 idle 眨眼等。

---

## 4. 里程碑

| 阶段 | 范围 | 完成标志 |
|------|------|----------|
| **M0** | 光球 + daily + confirm + 互斥 WS | **done** |
| **M1a** | i3 → i2 → i1（可拆分 ship） | **done** |
| **M1b** | i4（展开态拖放） | **done** |
| **M2** | i4b · i5 · i6 · i7 | 收起态拖放；定位持久化；往返提示；减动效 |

---

## 5. 任务编号

| ID | 任务 | 依赖 | 状态 |
|----|------|------|------|
| T-pet-i1 | `ui.route` A/B + 工作台接引 + govern→grow | T-906, P7, P11 | **done** |
| T-pet-i2 | recall 扩窗 + 高亮 + 状态栏 | T-905, P9 | **done** |
| T-pet-i3 | `user-message` 附件回放 | T-1207 | **done** |
| T-pet-i4 | 展开态拖放（= daily） | T-1206, P8, P13 | **done** |
| T-pet-i4b | 收起态拖到光球自动展开 | T-pet-i4 | defer · M2 |
| T-pet-i5 | 拖拽 + 位置持久化 | M0 | defer · M2 |
| T-pet-i6 | 往返未读提示 | T-1116 | defer · M2 |
| T-pet-i7 | reduced-motion | — | defer · M2 |

> 实现进度回写 `TASKS.md`；T-1208 完成时合并为 T-pet-i4 **done**。

---

## 6. 开放问题

| # | 问题 | 状态 |
|---|------|------|
| 1 | M1a 打包还是拆开 ship？ | **已决：可拆开（P10）** |
| 2 | 重活接引自动开？ | **已决：部分 A 档（P7）** |
| 3 | 拖放规则？ | **已决：同 daily（P8）** |
| 4 | recall 扩窗？ | **已决：是（P9）** |
| 5 | govern 落点？ | **已决：B 档；手动进 grow（P11）** |
| 6 | 气泡视觉？ | **已决：白底简洁（P12）** |
| 7 | 收起态拖入？ | **已决：M1 否，M2 i4b（P13）** |
| 8 | 精灵换皮？ | M2+ 开放 |
| 9 | i5 展开态拖拽方式 | M2 开放 |

---

## 7. 变更记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0-draft | 2026-07-13 | 初稿 |
| 0.1.1-draft | 2026-07-13 | P7–P9 |
| 0.1.2 | 2026-07-13 | P10–P13；M1 规格定稿 |
| 0.2.0 | 2026-07-13 | M1 实现：i1–i4（`pet-route.ts` · `pet/index.ts`） |
| **0.2.1** | 2026-07-18 | **DOC-01 / T-1806-doc-01**：§1.3 pet→daily 会话映射表 |
