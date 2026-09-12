# 聊天区活动轨重设计（Chat Activity Timeline）

> 版本 **0.1.0** · 2026-09-03  
> 状态：`doc` — **UX-028 已实施**（P0～M2）  
> 关联：[DESKTOP.md](./DESKTOP.md) §3.2.2 · [UX-POLISH.md](./UX-POLISH.md) **UX-028** · [EXEC-OBSERVABILITY.md](./EXEC-OBSERVABILITY.md) · [TASKS.md](./TASKS.md) **UI-6040** · [INTERACTION-REDESIGN.md](./INTERACTION-REDESIGN.md)

---

## 0. 摘要

**问题**：聊天区同时出现两个「思考中…」、等待态与流式态两套 UI、底部状态栏与过程 pill 重复表达「正在思考」——用户无法判断 agent 是否真的在工作。

**方向**：把 `process` + `thinking` + `confirm` 收成 **一轮用户消息下的单一「活动轨」**（Activity Timeline）；**只有当前轮**可显示 live 脉冲；历史轮折叠为一行摘要。

**分期**：

| 阶段 | 范围 | 状态 |
|------|------|------|
| **P0 热修** | 新回合收尾在途 process；统一等待/流式 DOM；仅当前 `turnKey` live | **done** |
| **M0** | 活动轨时间线渲染（思考段 + 工具段交错）；单一 pill 入口 | **done** |
| **M1** | Turn Card 分组（用户气泡 + 活动轨 + 助手回复绑组）；状态栏降噪 | **done** |
| **M2** | 狂奔/侧栏「查看过程」与活动轨对齐；`session.history` 恢复策略 | **done** |

---

## 1. 动机

### 1.1 用户可见症状（2026-09-03）

| 症状 | 示例 |
|------|------|
| **双「思考中」** | 上行 `● 思考中…（133s）`，下行灰框 `● 思考中…（97s）` + reasoning 摘要 |
| **过程入口重复** | 独立「过程」文字 + pill `过程 · 1 步 · 进行中` |
| **状态栏与聊天区打架** | 底栏 `思考中…（Ns）` 与过程区内计时并存 |
| **狂奔加剧** | 自动续发用户消息时，上一轮 process 未收尾即开新轮 |

### 1.2 根因（实现层）

1. **状态泄漏**：`pushUserMessage()` → `beginTurn()` 换新 `currentTurnKey`，但**旧 process 块**的 `llmPending` / `reasoningPhase=streaming` 未 `finalize`，旧块继续 tick 计时。
2. **UI 双轨**：`llmPending && !reasoning` 走「脉冲行」组件；`reasoning.delta` 后走「灰框 accordion」——视觉上像两层独立思考区（即使同一块内切换也易误解）。
3. **概念叠层**：UX-021 的 B 层思考 + A 层工具 + 过程 pill + 底栏状态，四层都在表达「进行中」，缺单一真源。

### 1.3 与既有决议的关系

- **不推翻** [DESKTOP.md](./DESKTOP.md) §3.2.2 **D-T1～D-T7**：思考仍在过程区内、工具在下、>6 折叠、回合结束默认收起。
- **收束呈现**：把「过程块 + 思考 accordion + 工具行」在**信息架构**上合并为 **Activity Timeline**，减少平行入口。
- **对齐** [INTERACTION-REDESIGN.md](./INTERACTION-REDESIGN.md)：Harness 内部阶段不进主路径；活动轨只展示**用户可观察的执行事实**（思考片段、工具、需确认的写操作）。

---

## 2. 目标与非目标

### 2.1 目标

| # | 目标 |
|---|------|
| G1 | **任意时刻最多一个 live 思考指示器**（当前 `turnKey`） |
| G2 | **单一活动入口**：一轮助手回复上方只有一个可折叠「活动」区域 |
| G3 | **时间线语义**：思考段与工具段按发生顺序排列，而非「思考永远钉顶 + 工具永远在下」的静态分区（钉顶仅作为 *进行中* 的折叠标题行为保留） |
| G4 | **狂奔友好**：连续 auto-send 不堆叠僵尸 process |
| G5 | **保留 UX-021 能力**：reasoning 全文可展开、失败工具 alert、过程默认折叠 |

### 2.2 非目标（本 Phase）

| 非目标 | 理由 |
|--------|------|
| 改 WS 协议事件名 | 仍消费 `llm.pending` / `reasoning.delta` / `tool.*` |
| 聊天区吸顶 sticky | D-T6 已否决 |
| 重写 `session.history` 持久化格式 | M2 再议；M0 仅改前端 block 模型 |
| 合并 plan-subagent / review-subagent 卡 | 仍独立块，挂在活动轨之上或之下（见 §4.3） |

---

## 3. 信息架构：Turn Card

### 3.1 结构（目标态 · M1）

```text
┌─ TurnCard (data-turn-key) ─────────────────────────────────────┐
│ [用户]  继续完成项目                                            │
├─ Activity（可折叠 · 单一 pill）────────────────────────────────┤
│  ● 思考 12s                                                     │
│    Reviewing ADR deliverable…                                   │
│  · read_file · music/docs/ADR.md                    ✓ 1.2s     │
│  ● 思考 3s                                                      │
│  · deliverable_review · music                       运行中…      │
├─ [助手]  （流式或最终 markdown）                                │
└────────────────────────────────────────────────────────────────┘
```

### 3.2 与现状对照

| 现状 block | 目标 |
|------------|------|
| `process`（含 reasoning + tools） | `activity` 或保留 `process` 但内部改为 `entries[]` 时间线 |
| `confirm`（独立 surface） | 活动轨内 `entry.kind=confirm` 行；night overlay 保留 |
| 底栏 `思考中…` | live 时改为 `处理中…` 或工具名；**不再**与活动轨重复计时 |

### 3.3 活动 pill 文案（统一规则）

| 状态 | pill |
|------|------|
| 仅 live 思考 | `思考中…` |
| 有工具无思考 | `过程 · N 步` |
| 思考 + 工具 | `过程 · N 步 · 思考中` |
| 有运行中工具 | 追加 `· 进行中` |
| 已结束 | `过程 · N 步`（无脉冲点） |

规则实现在 `output-display.ts` → `processPillLabel()`；M0 后改名 `activityPillLabel()` 亦可，对外文案不变。

---

## 4. 数据模型

### 4.1 现行（`chat-state.ts`）

```typescript
// process block — 扁平 reasoning + tools[]
{
  kind: "process";
  turnKey: string;
  reasoning: string;
  reasoningPhase: "idle" | "streaming" | "pinned";
  llmPending?: boolean;
  tools?: ProcessTool[];
  collapsed: boolean;
}
```

**问题**：reasoning 与 tools 分字段 → 渲染强制「思考在上、工具在下」，无法表达多段思考-工具交错。

### 4.2 目标（M0 引入 · 向后兼容）

```typescript
type ActivityEntry =
  | { kind: "think"; id: string; text: string; phase: "streaming" | "pinned"; startedAt: number; pinnedAt?: number }
  | { kind: "tool"; callId: string; /* 同现有 ProcessTool */ }
  | { kind: "confirm"; requestId: string; preview: string; resolved?: string };

type ActivityBlock = {
  kind: "activity"; // 或保留 kind: "process" + schemaVersion: 2
  turnKey: string;
  collapsed: boolean;
  entries: ActivityEntry[];
  /** 迁移期：由 entries 聚合，或旧字段灌入 */
  llmPending?: boolean;
};
```

**事件映射**（`handleEvent`）：

| 事件 | 动作 |
|------|------|
| `llm.pending` | `llmPending=true`；若无 open think entry 则准备新段 |
| `reasoning.delta` | `llmPending=false`；append 到最后 open `think` 或新建 `think` |
| `tool.start` | pin 当前 open `think`；push `tool` running |
| `tool.end` | 更新对应 `tool` |
| `assistant.delta` | pin open `think` |
| `assistant.done` / `turn.end` | finalize all；`collapsed=true` |

### 4.3 其它块顺序（M1）

固定顺序（单轮内）：

1. `user`
2. `plan-subagent` / `review-subagent`（若有）
3. `activity`
4. `assistant` / `assistant-streaming`
5. `notice`（采纳 chip 等仍可按 UX 规则分组）

---

## 5. 生命周期与状态机

### 5.1 当前轮唯一 live（**P0 已决**）

在 `beginTurn()` **之前**调用 `finalizeInFlightProcessBlocks()`：

- 对所有 `llmPending || reasoningPhase===streaming` 的 process：`finalizeProcessAfterTurn` + `collapsed=true`
- 渲染侧：`thinkingLive` 仅当 `block.turnKey === model.currentTurnKey`

### 5.2 思考段收起时机（延续 D-T3）

1. `tool.start`
2. `assistant.delta`
3. `assistant.done` / `turn.end`
4. 新 `llm.pending` 且上一段已有 reasoning（`prepareProcessForLlmRound` 重置）

### 5.3 底栏状态（M1）

| 场景 | 底栏 |
|------|------|
| `llm.pending` / `reasoning.delta` | `处理中…`（无独立秒表） |
| `tool.start` | `· {tool}` |
| 空闲 | `就绪` |

秒表只在活动轨内展示（`thinkingTitleLabel` / tool elapsed）。

---

## 6. 渲染

### 6.1 组件树（M0）

```text
.unified-activity[data-turn]
  button.unified-activity-pill
  .unified-activity-body
    .unified-activity-timeline
      .unified-activity-entry.is-think[.is-streaming|.is-pinned]
      .unified-activity-entry.is-tool[.is-running|.is-ok|.is-fail]
      .unified-activity-entry.is-confirm
```

### 6.2 思考 entry 呈现

| phase | 呈现 |
|-------|------|
| streaming | 标题 `思考中…（Ns）` + 正文区跟 `reasoning.delta` |
| pinned | 标题 `思考 · Ns`；默认折叠，可展开全文 |
| pending（无正文） | 单行脉冲 + `思考中…（Ns）`（**无**第二套灰框外壳） |

### 6.3 增量渲染（`doRender`）

保留现有 A3-2～A3-6 指纹策略；`blockPrint` 改为序列化 `entries` 摘要。活动轨 mutation 必触发尾部块替换。

### 6.4 CSS 原则

- 活动轨左边线 + 浅底（延续 `.unified-process`）
- **禁止**同一轮内两个 `.unified-thinking` 根节点
- live 脉冲仅 `.unified-activity.is-live .unified-activity-pill-dot`

---

## 7. 实施分期

### 7.1 P0 热修（已编码 · 待验收）

| 文件 | 改动 |
|------|------|
| `chat-state.ts` | `finalizeInFlightProcessBlocks()` in `beginTurn()` |
| `unified/index.ts` | `isLiveProcessBlock()`；统一 `renderThinkingAccordion` |
| `unified.css` | 等待态样式收进单一 summary 行 |

**验收**：S-UX-028a

### 7.2 M0 · 时间线数据模型 + 渲染

| 任务 | 说明 |
|------|------|
| T-6040-01 | `ActivityEntry` 类型 + `handleEvent` 写入 |
| T-6040-02 | 从旧 `process` 块 on load 迁移（单 think + tools 列表） |
| T-6040-03 | `renderActivityTimeline()` 替换 `renderThinkingAccordion` + `renderCompactToolLines` |
| T-6040-04 | 更新 `blockPrint` / 增量渲染指纹 |
| T-6040-05 | 手工：狂奔连续两轮无双思考 |

**验收**：S-UX-028b～d

### 7.3 M1 · Turn Card + 状态栏

| 任务 | 说明 |
|------|------|
| T-6041-01 | `renderBlocksGrouped` 按 `turnIndex` 包 `.unified-turn-card` |
| T-6041-02 | 底栏去掉思考秒表；`setStatus` 规则 §5.3 |
| T-6041-03 | `jumpToCurrentTurnProcess` → `jumpToCurrentActivity` |

**验收**：S-UX-028e～f

### 7.4 M2 · 狂奔 / 历史 / 侧栏

| 任务 | 说明 |
|------|------|
| T-6042-01 | 侧栏「查看过程」定位到 live activity |
| T-6042-02 | `session.history` 不恢复 live 旗标（仅 assistant/user） |
| T-6042-03 | night perspective：confirm 仍走 overlay，活动轨隐藏 confirm entry |

**验收**：S-UX-028g

### 7.5 M3 · 同回合多段过程 + 狂奔 idle 侧栏（2026-09-04）

**背景（music 项目走查）**：狂奔 Harness 连发时 `beginServerProcessTurn()` 不递增 `turnCounter`，多段 `process` 块折叠在同一 Turn Card；助手正文可见，过程收成 `过程 · N步 ▸` pill。「查看过程」只跳当前 live 段。侧栏在 `runawayEnabled && !turnInProgress` 时显示「待命 + 继续狂奔」，与顶栏「狂奔：开」矛盾；重复点 resume 刷「狂奔已恢复」与「通道占用」。

| 任务 | 说明 |
|------|------|
| T-6053-01 | `liveTurnCardProcessBlocks()`：枚举当前 Turn Card 内全部 process 段 |
| T-6053-02 | `jumpToCurrentActivity()`：idle 时按 **新→旧** 轮询定位；进行中仍优先 live 段 |
| T-6053-03 | 多段 pill 标注 `(i/n)`；状态栏提示「过程段 i/n」 |
| T-6053-04 | 狂奔 v2 idle：侧栏 **续接中 + 查看过程**；禁默认「继续狂奔」（仅 blocked 时） |
| T-6053-05 | `resumeRunawayFromUi`：已开且未 blocked → 不 POST resume，改跳过程 |
| T-6053-06 | `project.runaway.set` 幂等 resume（已开且无 paused）→ **不**发 toggle notice |
| T-6054-01 | 狂奔 `plan_partner` 提案：回合末 `_flush_runaway_plan_proposals` + 失败 notice |
| T-6054-02 | 残留提案自动 chain 直写 VERIFY/MAP；v2 implement/prepare 禁 plan_partner |
| T-6054-03 | 模型禁止对用户说「请审阅采纳」（locale append） |
| T-6055-01 | 狂奔 idle：侧栏「手动续接」+ 5s 客户端 watchdog 自动 nudge |
| T-6055-02 | 未 auto-chain 时 `turn.notice` 说明原因；prepare 段内不因 PREPARE 失败数停链 |
| T-6056-01 | v2 非 human 计划 `chain_after_ok=True`；不受 qa/recall 意图掐断 |
| T-6056-02 | 「继续」等短句 → execute；服务端 chain 重试 4 次 + 客户端 2.5s watchdog |
| T-6107-01 | **续接收敛**：[RUNAWAY-V2-CONTINUATION.md](./RUNAWAY-V2-CONTINUATION.md) · `pending_runaway_work()` 单真源 |
| T-6107-02 | controller + server + agent 同调 `should_continue_runaway`；删 `chain_after_ok` / `plan.should_chain` 兼容面待收口 |
| T-6107-03 | IT-6107-a～g 已通过；进展指纹（借 v1 stuck）待收口 |

**验收**：S-UX-028h～j · S-UX-028k（提案不卡死）· **S-UX-028l**（idle 5s 内自动续接或可见手动出口）· **S-UX-028m**（发「继续」后应连跑 ≥3 内链条，不单轮停；**T-6107 编码后正式验收**）

**止血 vs 收敛**：T-6055～6056 为四层规则补丁；**T-6107 核心**已收敛为 `pending_work` 单真源，兼容面与进展指纹仍待完成（见续接专文 §10）。

**非目标（M3）**：`session.history` 跨会话回放 process（仍仅 user/assistant）；Harness 每段新开 Turn Card（defer M4）。

---

## 8. 验收标准

| ID | 步骤 | 期望 |
|----|------|------|
| **S-UX-028a** | 狂奔开启 · 连续两轮 auto user（或手动连发两条） | 仅**最后一个** process/activity 显示 live 思考；上一轮显示 `思考 · Ns` 或已折叠，**无**第二计时器 |
| **S-UX-028b** | 单轮：`llm.pending` → `reasoning.delta` → `tool.start` | 时间线顺序：思考段（收成 `思考 · Ns`）→ 工具行；**无**双思考 UI |
| **S-UX-028c** | 点活动 pill 收起/展开 | 思考 + 工具 + 失败 alert **一起**隐藏/显示（D-T5 / UX-021b） |
| **S-UX-028d** | `assistant.done` 后 | 活动轨默认折叠；助手正文在其下 |
| **S-UX-028e** | 流式思考中 | 底栏**不**显示独立 `思考中…（Ns）` 秒表（M1） |
| **S-UX-028f** | 侧栏「查看过程」 | 滚到当前 live 活动轨并展开（M1） |
| **S-UX-028g** | 切换会话后再回来 | 无僵尸 `思考中`；token/压缩状态不受影响（M2） |
| **S-UX-028h** | 狂奔同回合 ≥2 段 process · idle | 连点侧栏「查看过程」依次展开 **最新→更早** 段；pill 显示 `(2/3)` 等 |
| **S-UX-028i** | 狂奔已开 · 回合间隙 idle | 侧栏 **续接中** +「查看过程」；**无**默认「继续狂奔」 |
| **S-UX-028j** | 已开狂奔 idle 时点原「继续狂奔」入口 | 不刷「狂奔已恢复」；不触发 duplicate lease notice |
| **S-UX-028k** | plan_partner 后回合结束仍有残留提案 | 自动续链直写 VERIFY/MAP；**不**要求用户审阅采纳 |
| **S-UX-028l** | 狂奔已开 · 回合结束 idle | 5s 内自动续接 **或** 侧栏「手动续接」；notice 说明未续接原因 |

---

## 9. DOC-04 准入

| 项 | 内容 |
|----|------|
| **Surface** | desktop |
| **矩阵行** | DESKTOP §3.2.2 过程可见 · EXEC-OBSERVABILITY 聊天过程 · UX-POLISH UX-028 |
| **回归** | S-UX-021a～d（思考折叠不回归）· S-UX-023（工具 >6 折叠）· S-UX-025（载入落底）· S-UX-028a～g |
| **IT** | 无后端契约变更（P0/M0）；可选 `desktop` 组件单测 `activity-reducer`（todo） |

---

## 10. 风险与回滚

| 风险 | 缓解 |
|------|------|
| 增量渲染指纹漏 case → 幽灵 DOM | M0 完成前对 activity 变更走 full render 逃逸；加 IT 固定 HTML 快照 |
| 多 think 段 pin 时机错误 | 单测覆盖事件序列表 §4.2 |
| 与 night confirm overlay 冲突 | M2 显式矩阵；M0 不动 overlay |

回滚：保留 `kind: "process"` 与 `entries` 双读一周；`schemaVersion` 缺省视为 v1。

---

## 11. 讨论记录

| 日期 | 内容 |
|------|------|
| 2026-09-03 | 用户截图：双「思考中」+ 过程在外；结论：先 P0 热修再 M0 活动轨；**本文档落盘后再编码 M0** |
| 2026-09-03 | 与 UX-021 关系：收信息架构，不删 accordion 能力；D-T1～T7 仍有效 |
| 2026-09-04 | music 走查：同回合多段折叠 + 查看过程不跳历史 + 狂奔 idle 侧栏矛盾；**M3 落盘** |

---

## 12. 变更日志

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-09-03 | 初稿：问题、Turn Card、ActivityEntry、P0/M0/M1/M2、S-UX-028、DOC-04 |
| 0.2.0 | 2026-09-04 | M3：同回合过程段轮询跳转 · 狂奔 idle 侧栏 · 幂等 resume 降噪 |
