# 计划审阅主列（PLAN-REVIEW-UI）

> 版本 **0.2.0** · 2026-08-04 · **状态：PRU-M0 done · Affordance 设计已签（Phase 40 · 代码待做）**  
> 触发：Phase 39 单入口后，采纳 diff 挤在左窄栏（`max-height: 9rem`），完整计划看不清；对标 Cursor Plan Mode「计划开在主区、聊天在边上」。  
> 原则（用户确认）：**一个输入 · 默认聊天主列 · 审计划时主列让给计划**。  
> **0.2.0**：主聊口述「点采纳」与侧栏无按钮 /「已写入」告知撞脸 → **控件自解释**（§10 · [BUG-022](./bugs/2026-08-04-adopt-affordance-mismatch.md)）。  
> 关联：[PLAN-SUBAGENT.md](./PLAN-SUBAGENT.md) · [PROJECT-SIDEBAR.md](./PROJECT-SIDEBAR.md) §6/§7/§15.13 · [WORKBENCH-UI.md](./WORKBENCH-UI.md) · [TASKS.md](./TASKS.md) Phase 40

---

## 0. 已决

| # | 结论 |
|---|------|
| P1 | **不**恢复双通道 / 双输入（废止 Phase 38） |
| P2 | **不**把完整计划长期塞左栏；左栏 = 决策入口 + 短摘要 |
| P3 | **审阅 / 采纳**占 **主列**（对标 Cursor 编辑器主区） |
| P4 | **主输入始终唯一**；审计划时仍可打字（改计划话术走 `plan_partner`） |
| P5 | Phase 39 后端不变：`plan_partner` · `project.plan.state` · 采纳 API 复用 |
| P6 | M0 **纯前端**；无新 WS 类型 |
| P7 | **控件自解释**：有待拍板动作时，UI 必须露出可点控件；**禁止**依赖主 Agent 口述按钮名（§10） |
| P8 | **待采纳 ≠ 已写入**：采纳后告知不得再贴 diff、不得长得像提案卡（§10） |

---

## 1. 空间分工（对标 Cursor）

| Cursor | my-agent（本设计） |
|--------|-------------------|
| 编辑器主区 = 计划虚拟文件 | **主列 `plan-review` 面** = 完整 diff / TASKS 片段 |
| Agent 窄栏 = 对话 | **默认主列 = 聊天**；审计划时聊天可收成右缘或顶部条（M0 可仍可见，仅降优先级） |
| Build = 拍板 | **采纳 / 忽略**（主列底部固定操作栏） |
| Plan 与 Agent 同一会话 | 过程卡仍在聊天流；点卡 → 打开主列审阅 |

```
默认（chat）                    审计划（plan-review）
┌────────┬─────────────────┐   ┌────────┬─────────────────┐
│ 左栏    │ 主列 · 聊天      │   │ 左栏    │ 主列 · 计划审阅   │
│ 短卡入口│                 │   │ 短卡高亮│  diff / 全文     │
│        │                 │   │        │  [采纳][忽略]     │
└────────┴─────────────────┘   └────────┴─────────────────┘
         ↑ 主输入（footer 不变）──────────────┘
```

---

## 2. 主列焦点状态机

```mermaid
stateDiagram-v2
  [*] --> chat: 默认 / 关闭审阅
  chat --> plan_review: 打开待采纳 / 点过程卡 / 侧栏「查看」
  plan_review --> chat: 关闭 / 全部处理完
  plan_review --> plan_full: 「查看完整计划」（可选 M1）
  plan_full --> plan_review: 返回审阅队列
  plan_full --> chat: 关闭
```

### 2.1 状态定义

| 状态 `mainFocus` | 主列内容 | 左栏 |
|------------------|----------|------|
| `chat` | `#unified-chat` 聊天流 | 当下决策面 + 待采纳 **短卡**（无完整 diff） |
| `plan_review` | `#unified-plan-review` 审阅面 | 短卡列表；当前项高亮 |
| `plan_full` | 完整 TASKS 开放队列（自 `overlayPanel=plan` 逻辑迁出） | 同左；☰ 与主列审阅互斥高亮 |

### 2.2 进入 `plan_review` 的入口

1. 侧栏采纳短卡 → **「查看」**（原 diff 区改为一行摘要 + 按钮）
2. 聊天 **过程卡** `plan-subagent`（`proposals_ready`）→ 点击卡片
3. `plan.subagent.done` 且 `proposalCount > 0` → **可选**自动打开（默认 **关**，仅闪侧栏角标；用户可设「自动展开审阅」）
4. 顶栏 / 过程卡文案：「N 条待采纳 → 审阅」

### 2.3 离开 `plan_review`

- 审阅面 **「返回聊天」** / `Esc`
- 当前队列 **全部采纳或忽略** 且无剩余 → 自动回 `chat`（toast：「计划已处理完」）
- 切换项目 / 归档线 → 强制 `chat` + 清空审阅队列焦点

---

## 3. 审阅面布局（`#unified-plan-review`）

插入位置：`unified-main` 内，与 `#unified-chat` **互斥显示**（同一槽位）。

```
┌─ 顶栏 ─────────────────────────────────────────┐
│ ← 返回聊天    计划审阅 · 2/5    [查看完整计划]      │
├────────────────────────────────────────────────┤
│ 标题：采纳写入 · TASKS.md                        │
│ 摘要：plan_partner / suggestion body（可换行）    │
├────────────────────────────────────────────────┤
│                                                │
│  diff 或 markdown 全文（可滚动，无 9rem 上限）    │
│                                                │
├────────────────────────────────────────────────┤
│  [采纳]  [忽略]  [上一条]  [下一条]               │
└────────────────────────────────────────────────┘
│  unified-composer（不变）                        │
└────────────────────────────────────────────────┘
```

### 3.1 队列数据源

优先复用现有结构，不新协议：

| 来源 | 字段 | 采纳动作 |
|------|------|----------|
| `project.plan.state.suggestions[]` | `PlanSuggestion`（`id`, `title`, `body`, `payload.diff`, `payload.path`） | `project.plan.accept_suggestion`（现有） |
| 顶栏 `evolve.proposals` | `ProposalItem` | `acceptProposal`（现有 `proposals.ts`） |

**M0 范围**：仅 **Plan 采纳卡**（`suggestions`）。evolve proposals 仍走 `#unified-expand`（已有），M1 可并入同一审阅面。

### 3.2 侧栏短卡（改造）

`renderSuggestionsBanner` 改为：

- 每条：**标题 + 一行摘要**（body 截断 80 字）
- **不渲染** `sidebar-suggestion-diff`（或仅 `+12 −3` 统计）
- 按钮：`[查看]`（主列）· `[采纳]` · `[忽略]`（保留快捷操作）

---

## 4. 过程卡（聊天内）改造

现有 `plan-subagent` 块增加：

- `data-status=proposals_ready` 时：`cursor: pointer` + `role=button`
- 副文案：「点击在主区审阅」
- 点击 → `mainFocus = plan_review`，`reviewIndex` 指向本回合关联的第一条待采纳（若可关联；否则打开队列首条）

**不**在过程卡里再塞完整 diff。

---

## 5. 与侧栏覆盖面板的关系

| 面板 | M0 行为 |
|------|---------|
| ☰ 完整计划 | 点击 → `mainFocus = plan_full`（主列），**不再**只在 `sidebar-overlay` 内滚动 |
| 地图 / 验收 / 项目 / 会话线 | 仍用 **侧栏 overlay**（辅助面，非主阅读负担） |
| `open-full-plan` 按钮 | 改绑 `mainFocus = plan_full` |

理由：完整 TASKS 与采纳审阅同属「要认真看」类，跟 Cursor 编辑器主区一致；项目列表等仍是导航，可留窄栏。

---

## 6. 组件与文件（实现地图）

| 文件 | 改动 |
|------|------|
| `desktop/src/shells/unified/index.ts` | `mainFocus` 状态；`#unified-plan-review` DOM；`chat`/`plan-review` 互斥；过程卡点击；`Esc` |
| `desktop/src/shells/unified/plan-review.ts` | **新建**：`renderPlanReview()`、`PlanReviewState`、队列导航、采纳/忽略委托 |
| `desktop/src/shells/unified/project-panel.ts` | 短卡 UI；`open-full-plan` 改发 callback；去掉侧栏内大 diff |
| `desktop/src/shells/unified/unified.css` | `.unified-plan-review`、`.unified-main[data-main-focus]` 布局 |
| `desktop/src/shells/chat-state.ts` | 可选：`plan-subagent` block 增加 `proposalIds` 供关联 |

**不改**：`agent-core/*`、WS 协议、Phase 39 `plan_partner` 流程。

---

## 7. 里程碑

| ID | 内容 | 验收 |
|----|------|------|
| **PRU-M0** | 主列审阅面 + 侧栏短卡 + 过程卡点击 | S-PRU-01～03 |
| **PRU-M1** | `plan_full` 迁主列；evolve proposals 统一审阅 UI | S-PRU-04 |
| **PRU-M2** | 偏好：「有新提案时自动打开审阅」；键盘 `j/k` 切换队列 | 可选 |

### 手工验收（S-PRU）

| ID | 步骤 | 预期 |
|----|------|------|
| S-PRU-01 | 触发 `plan_partner` 产出 ≥1 条 suggestion | 侧栏见短卡；主列默认仍聊天 |
| S-PRU-02 | 点侧栏「查看」或过程卡 | 主列全宽 diff；可滚动读完；采纳后落盘 |
| S-PRU-03 | 审阅中发送「把第三条拆开」 | 仍单输入；新提案入队；审阅面刷新计数 |
| S-PRU-04 | 点 ☰ 完整计划 | 主列 TASKS 开放队列；← 返回聊天 |

---

## 8. 非目标

- 第二套 Plan 聊天线 / `project.plan.bubble` 复活
- 主列与聊天左右对调（审计划时聊天变右栏）——留 PRU-M2+ 再评估
- 内联编辑 TASKS（M1 后可用「查看完整计划」+ 右键菜单已有能力）
- 替代 Markdown 编辑器级体验（对齐 Cursor 80% 即可）

---

## 9. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-08-04 | 初稿：状态机 + 组件图 + M0 范围 |
| **0.2.0** | 2026-08-04 | **§10 Affordance**：P7/P8；待采纳 vs 已写入；禁口述按钮；Phase 40 |
| **0.3.0** | 2026-08-06 | **§11 采纳队列**：BUG-026 base_hash · 乐观 UI；Phase 48 |

---

## 10. 控件对齐（Affordance · Phase 40 · **设计已签**）

> **触发（2026-08-04）**：huiyi 会话截图——主聊写「记得点「采纳」」；侧栏却是「计划存档 / 已写入 TASKS.md」+ diff，**看不见「采纳」按钮**。用户判定为设计失误。  
> **已有洞注记**：`project-panel.ts` banner 链注释已写「notice 说点采纳、无按钮 = dead end」——须收口，不能只靠注释。  
> **跟踪**：[BUG-022](./bugs/2026-08-04-adopt-affordance-mismatch.md) · [TASKS.md](./TASKS.md) Phase 40 · [PROJECT-SIDEBAR.md](./PROJECT-SIDEBAR.md) §15.13

### 10.1 根因（三层叠）

| # | 层 | 现象 |
|---|-----|------|
| 1 | **提示词** | [PLAN-SUBAGENT](./PLAN-SUBAGENT.md) §3.3 / `evolve/prompts/project.md` 要求「提醒侧栏采纳/忽略」→ 模型口述按钮名，不校验 UI 是否露出该控件 |
| 2 | **采纳后告知** | `plan_agent` accept 后 `partner_notices = "已写入 {path}\\n{diff}"` → 侧栏长得像提案，但 **无按钮** |
| 3 | **空间迁移滞后** | PRU-M0 已把主采纳动作放到主列；话术仍停在「侧栏点采纳」；过程卡 CTA 未成为唯一指路 |

### 10.2 已决（A1～A6）

| ID | 决议 |
|----|------|
| **A1** | **控件自解释**：有待拍板提案时，侧栏或主列必须露出可点控件（至少「查看」；主列「采纳/忽略」）。**禁止**把「点采纳」当作主聊必说口播 |
| **A2** | **待采纳 ≠ 已写入**：两态标题、色、是否含操作钮必须一眼可分；禁止「已写入」卡仍贴完整/半截 diff |
| **A3** | **采纳后告知**：一行短文案（如「已采纳写入 TASKS.md」）+「关闭」；`partner_notices` **不得**嵌入 diff hunk |
| **A4** | **侧栏短卡（待采纳）**：标题区固定「待采纳 · N」；每条 = 标题 + 一行摘要 + 可选 `+n −m`；按钮 **「查看」必有**；「采纳」「忽略」可作快捷，主路径仍是主列审阅 |
| **A5** | **过程卡 = CTA**：`proposals_ready` 文案用「打开审阅 / N 条待采纳」；点击进 `plan_review`。主 Agent 只简述提案内容，**不口述按钮名** |
| **A6** | **自动打开审阅**：默认仍 **关**（沿用 §2.2.3）；用户偏好「有新提案时自动打开」属 PRU-M2 / Phase 40 可选，不阻塞 A1～A5 |

### 10.3 实施优先级

| 优先级 | 内容 | 主要落点 |
|--------|------|----------|
| **P0** | 待采纳卡可见；已写入告知去 diff、换皮；banner 优先级保证 actionable > notice（已有意图须测死） | `project-panel.ts` · `plan_agent.py`（notice 形状） |
| **P1** | 改提示词纪律：禁「点采纳 / 侧栏采纳」口播；过程卡文案对齐 A5 | `evolve/prompts/project.md` · PLAN-SUBAGENT §3.3 · `chat-state` / 过程卡 |
| **P2** | 可选：自动打开 `plan_review`；顶栏 `plan_dirty`「确认开工」与「待采纳」文案去重 | `index.ts` · 偏好 |

### 10.4 验收（S-AFF / IT-AFF）

| ID | 步骤 | 预期 |
|----|------|------|
| **S-AFF-01** | `plan_partner` 产出 ≥1 条待采纳 | 侧栏见「待采纳 · N」+「查看」；主聊过程卡可点；主聊 **无**「记得点采纳」类口播（允许简述内容） |
| **S-AFF-02** | 点「采纳」落盘后 | 侧栏若有告知，仅为「已采纳写入 …」类一行 + 关闭；**无** `@@` diff；与待采纳卡视觉不同 |
| **S-AFF-03** | 仅有 `partner_notices`、队列为空 | 不得出现「点采纳」文案（前端 notice 或主聊模板） |
| **IT-AFF-01** | accept `apply_patch` 后 `partner_notices` | 单行、无 diff；suggestions 队列为空 |

### 10.5 非目标（本 Phase）

- 取消侧栏快捷「采纳」（A4 仍允许）
- 恢复双通道 / Plan 独立气泡
- 用 LLM 动态生成按钮标签（标签固定中文，代码写死）

---

## 11. 采纳队列与 base_hash（Phase 48 · **BUG-026 open**）

> **完整技术说明**：[bugs/2026-08-06-plan-patch-adopt-base-hash-queue.md](./bugs/2026-08-06-plan-patch-adopt-base-hash-queue.md)（数据模型 · hash 时序 · WS 流 · 修复伪代码）  
> **触发（2026-08-06）**：huiyi `plan_partner` 一次出 5 条提案（TASKS + MAP×2 + PROJECT×2）；第二条 MAP/PROJECT 起 `base_hash mismatch`。

### 11.1 问题摘要

计划域 patch 采用 **乐观并发控制**：每条提案在生成时快照 `content_hash` 前 16 位为 `payload.base_hash`；采纳时磁盘须未变（[PLAN-ARCH](./PLAN-ARCH.md) M6 · IT-182）。

**缺口**：`plan_agent._apply_plan_operations` 对 LLM 返回的 **每个** `patch` op 各建一张侧栏卡；同文件多 op 共享 **同一** 提案时刻 hash。用户顺序采纳第一张后磁盘变化 → 第二张必失败。

**叠加**：`index.ts` `acceptSuggestionById` 在 WS 返回前删卡并闪「已采纳写入 **TASKS.md**」（文件名写死），造成「成功→撤回」双重误导。

### 11.2 根因表

| # | 层 | 文件 | 说明 |
|---|-----|------|------|
| 1 | 提案 | `plan_agent.py` `_apply_plan_operations` | 每 op 独立 `build_patch_preview` + `park_gated_suggestion` |
| 2 | 校验 | `plan_patch.py` `build_patch_preview` | `base_hash != current_hash` → `ProjectModeError` |
| 3 | 采纳失败 | `plan_agent.py` `accept_suggestion` | 捕获异常 → `_mark_suggestion_resolved` + `已撤回无效提案` |
| 4 | 乐观 UI | `index.ts` L1209–1222 | 先删卡、先闪绿、文案写死 TASKS.md |
| 5 | WS | `project_api.py` L635–654 | `ok:false` 时仍推 state；前端已删卡 |

### 11.3 已决修复（T-4810～4813）

| ID | 方案 | 优先级 | 落点 |
|----|------|--------|------|
| **A1** | 同 `path` 多 op **合并**为一张卡 + 一条 `replacements[]` | P0 | `plan_agent._apply_plan_operations` |
| **A2** | 采纳后 **rebase** 同 path 其余 pending 的 hash | P1 defer | `plan_agent.accept_suggestion` |
| **B1** | 等服务端确认后再闪绿；文案用 `payload.path` | P0 | `index.ts` `acceptSuggestionById` |
| **B2** | `ok:false` toast，可选恢复卡 | P1 | `index.ts` + WS handler |

### 11.4 临时绕行

- 每个文件 **只采纳第一条** patch。  
- 采纳一批后请主 Agent **按当前磁盘重提案**。  
- 采纳过程中勿手改 TASKS/MAP/PROJECT。

### 11.5 验收

| ID | 预期 |
|----|------|
| IT-4810 | 同轮 2× MAP patch → 1 卡或双卡均可采纳 |
| IT-4811 | 失败时不先闪「已采纳」 |
| S-481 | huiyi 5 条含重复 path 全流程无 mismatch |
