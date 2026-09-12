# 狂奔 v2 续接收敛 — `pending_work` 单真源

> 版本 **0.2.0** · 2026-09-09  
> 状态：**续接核心已接入；无进展熔断、自动预算和暂停恢复已落地**  
> 父文档：[RUNAWAY-V2.md](./RUNAWAY-V2.md) · 取代其 **§6.5** 的分散 `should_chain` 条款  
> 触发：music 试点「跑 1 轮就停」；本版已把 v2 自动续接收敛到 controller，server / Desktop 仅保留 v1 兼容入口和状态展示。  
> 关联：[RUNAWAY-R6-RELIABILITY.md](./RUNAWAY-R6-RELIABILITY.md) · [PROGRESS-GATE.md](./PROGRESS-GATE.md) G9 · [EXEC-RELIABILITY.md](./EXEC-RELIABILITY.md) · [CHAT-ACTIVITY-TIMELINE.md](./CHAT-ACTIVITY-TIMELINE.md) §7.5

---

## 0. 问题陈述

狂奔 v2 的用户契约是：**开启后 Harness 自动跨 prepare → implement → verify 推进**，用户只在 human 升格、发布确认、高风险操作时介入。

实际体验却是「有时只跑 1 轮就静默停下」，根因不是模型偷懒，而是历史上 **「该不该续」被拆成四套规则，彼此不一致**：

| 层 | 代码锚点 | 典型条件 |
|----|----------|----------|
| L1 | `TurnPlan.chain_after_ok` | 每个 `build_turn_plan` 分支手写 bool |
| L2 | `should_chain()` | mode / phase / checklist /（曾）intent |
| L3 | `Agent.should_chain_runaway_after_turn()` | v1 兼容；v2 只保留只读判断 |
| L4 | Desktop `scheduleRunawayIdleResume()` | v1 兼容；v2 已禁用 |

典型故障模式：

1. **L2 已停、L3 以为能续** → server 注入 continue 行，controller 内链却不跑。  
2. **L2/L3 都停、L4 被 debounce 或 `turnInProgress` 挡住** → 用户看到「续接中」但无人发起下一 turn。  
3. **`intent` 误判**（「继续」→ `qa`）→ L1 `chain_after_ok=False` → 回合内立刻断链（T-6056 已补丁，但架构未收敛）。  
4. **`TURN_LOCK` / `_turn_busy` 竞态** → L3 静默 return（T-6055 已加重试 + notice，仍非主路径）。

**结论**：业务是否还有工作由磁盘派生的 `pending_runaway_work()` 决定；v2 的自动执行由 `RunawayController` 单独拥有。server 与 Desktop 不得为 v2 再启动第二、第三个回合。

---

## 1. 设计原则（借什么）

### 1.1 仓库内已有、应升格为续接真源

| 来源 | 做法 | 续接映射 |
|------|------|----------|
| **`derive_phase()`** | 阶段从 TASKS / 四件套 / checklist 重算 | `pending_work` 的 `phase` 字段 |
| **checklist.json** | 外部清单；只有 hook 改 `status` | Anthropic feature list 的 `passes` |
| **PROGRESS.md** | 跨 turn 进展摘要 | 续接文案 ritual 的必读上下文 |
| **§6.4 升格** | auto → directed → human；停 = 不 chain | `PendingWork.blocked_reason` |
| **R6 租约** | 跨进程互斥、TTL、重试 | 与「有没有活」**解耦**；只管能不能抢到执行权 |

### 1.2 仓库内已有、应借进 v2

| 来源 | 做法 | 续接映射 |
|------|------|----------|
| **v1 `_runaway_stuck_turns`** | 连续 N 轮 TASKS `done` 不涨 → warn | **进展指纹**（§6）；达到阈值即暂停 |
| **PROGRESS-GATE G9** | 用户「继续」仅在上一项真 `[x]` 后有意义 | 自动续接 = harness 代说「继续」，前提同样是 `pending_work ≠ None` |
| **T-6055 server 重试** | 4 次 + notice | 保留为 **跨 turn 通道层**，不承载业务「该不该续」 |

### 1.3 外部参考（RUNAWAY-V2 §14）

| 来源 | 借什么 | 不借什么 |
|------|--------|----------|
| **Anthropic 长程 harness** | 外部 JSON 清单；每 session 一项；session 开头读 progress + 清单 | _initializer / coding agent 双 prompt 分叉（v2 已有 prepare/implement） |
| **Anthropic sprint contract** | 回合结束前约定 done = `TurnPlan.acceptance` | 第二套 Planner 模型 |
| **Cursor cloud agent** | 薄 harness；会话 / 项目 / runtime 三层 | 云 VM 生命周期 |
| **Pi profile** | 狂奔是 profile，续接逻辑进 `runaway_v2/` 模块 | — |

---

## 2. 非目标

- **不**恢复 v1 checkpoint 枚举驱动续接。  
- **不**用 `turn_intent`（qa / recall / requirements）决定狂奔 profile 下是否续接；用户主动提问时由 **本轮** `TurnPlan` 处理，续接只看磁盘。  
- **不**让 Desktop watchdog 参与 v2 续接；它只保留给 v1 兼容路径。  
- **不**在本文重开 bug-fix 子代理或 Harness 短接栈讨论（v2 已废止）。

---

## 3. 目标架构

### 3.1 单一入口：`pending_runaway_work()`

新模块建议：`agent-core/runaway_v2/continuation.py`。

```text
pending_runaway_work(
    paths, project_id, session, *, checklist=None
) -> PendingWork | None
```

**`PendingWork`（frozen dataclass）** 建议字段：

| 字段 | 含义 |
|------|------|
| `phase` | `prepare` \| `implement` \| `verify` |
| `focus_item_id` | checklist 焦点项；prepare 可为 `PREPARE` |
| `active_task_id` | implement 时的 `T-*` |
| `mode` | `auto` \| `directed`（**不含** `human`——human 时返回 `None`） |
| `user_line` | 侧栏 / notice 用人话 |
| `chain_user_line` | 注入 history 的 harness 续接句（§7） |
| `blocked_reason` | 仅当 `None` 返回但本应工作时用于 debug（一般不暴露） |

**返回 `None` 当且仅当**（命中即停，优先级从上到下）：

1. 狂奔未开启，或 `session.cancel_event` 已 set。  
2. `derive_phase` → `human`（含依赖未满足）。  
3. `derive_phase` → `release_wait`（清单全绿、无开放 T-*、待发布）。  
4. `runaway_v2_blocked` / 升格到 human（`directed_used` 且 `attempts >= DIRECTED_AFTER`）。  
5. 无 checklist 开放项 **且** 无开放 formal `T-*` **且** prepare 已就绪（防御性；正常由 2/3 覆盖）。

**返回 `Some(PendingWork)` 当**：仍有 prepare 缺口、开放 `T-*`、或未绿 checklist 项，且未升格 human。

> **关键**：`pending_work` **不读** `finish_reason`、**不读** `intent`、**不读** `chain_after_ok`。  
> `finish_reason` 在 controller 的回合出口和 v1 server 兼容通道共同过滤；v2 的 controller 在递归前必须先处理致命退出（cancelled / timeout / error / duplicate lease）。

### 3.2 统一续接门：`should_continue_runaway()`

```text
should_continue_runaway(
    paths, project_id, session, *,
    finish_reason: str | None = None,   # 仅跨 turn 传入
    in_turn_depth: int = 0,
    max_in_turn_depth: int = 12,  # generic compatibility default; controller uses max_auto_turns()
) -> bool
```

为 true 当且仅当：

- `pending_runaway_work(...)` 非 `None`  
- `in_turn_depth < max_in_turn_depth`  
- `finish_reason` 不在致命集（见 §4.2）

### 3.3 调用方收敛

| 原调用方 | 目标 / 当前 |
|----------|------|
| `plan.should_chain()` | **目标删除**；当前仅保留兼容面，controller 已改调 `should_continue_runaway` |
| `TurnPlan.chain_after_ok` | **目标删除**；当前字段仍存在但续接单真源不读取它，plan 语义待收敛为 scope / acceptance |
| `RunawayController` | v2 自动续接唯一 owner；回合结束先处理 fatal `finish_reason`，再读 `pending_work` |
| `Agent._should_chain_runaway_v2_after_turn()` | 兼容查询：`should_continue_runaway(..., finish_reason=...)`；不由 server 调用 v2 自动回合 |
| `Agent.runaway_chain_skip_reason()` | `pending_work is None` 时派生人话；与 `build_v2_state_fields` 共用 blocked 文案 |
| `Agent.runaway_chain_user_line()` | `pending_work.chain_user_line` |
| Desktop `scheduleRunawayIdleResume` | v1 兼容；`runaway_version >= 2` 时直接禁用 |

---

## 4. v2 单 owner + v1 兼容兜底

```text
                    ┌─────────────────────────────────────┐
  同一 WS turn       │  v2 唯一 owner：RunawayController    │
  turn.end 之前      │  fatal → pause/return                │
                    │  normal → pending_work → 续接         │
                    └─────────────────┬───────────────────┘
                                      │ 用户停止 / 锁等待
                                      ▼
                    ┌─────────────────────────────────────┐
                    │ server 只负责锁、租约和状态广播       │
                    │ runaway_cancel_available=true/false  │
                    └─────────────────────────────────────┘

  v1 兼容客户端      │ server 跨 turn fallback + Desktop idle │
                    │ v2 不进入这两条路径                  │
                    └─────────────────────────────────────┘
```

### 4.1 L2 段内续接

- **职责**：同一用户消息 / 同一 `turn.start`～`turn.end` 窗口内，prepare 连跑、verify 连项、flush 残留提案。  
- **深度上限**：与 `MY_AGENT_RUNAWAY_V2_MAX_AUTO_TURNS` 共用预算（默认 **24**），避免 controller 比自动预算更早静默停止。  
- **不计入** session 的「用户轮次」展示；process 段仍可能折叠在同一 Turn Card（UX-028 M3）。  
- **每段结束**：重算 `pending_runaway_work`（acceptance hook 可能已改 checklist）。
- **致命出口**：`cancelled` / `timeout` / `error` 会持久化暂停原因并返回，不生成新的 harness user 消息。

### 4.2 v1 server 跨 turn 兼容

- **职责**：只服务 v1。`turn.end` 且 `finish_reason=completed`（或等价正常结束）后，若仍有待办，注入 harness user 行开新 turn；v2 直接跳过本路径。  
- **致命 `finish_reason` 集**（不续）：`cancelled` · `timeout` · `error` · `context_switched` · `runaway_paused` · `runaway_duplicate` · `runaway_verification_exit`。  
- **通道重试**：保留 T-6055（`RUNAWAY_CHAIN_RETRY_ATTEMPTS=4`，cooldown 0.8s / 1s）；失败发 `turn.notice` warn。  
- **等待状态**：抢锁前发送 `runaway_cancel_available=true`，开始回合或退出等待后发送 `false`，因此 UI 始终能显示/撤销挂起的续接。
- **与租约**：续接前 `_acquire_runaway_lease`；duplicate → 致命集，不 business-chain。

### 4.3 v1 Desktop 兜底

- **职责**：仅 v1；`turn.end` 后 UI 进入 idle，若 server fallback 因竞态未发起，2.5s 后 `resumeRunawayFromUi({ force: true })`。  
- **v2 行为**：`runaway_version >= 2` 时不设置 idle resume timer，避免第三 owner 偷偷重入。  
- **debounce**：手动续接 4s（T-6056），防连点 duplicate lease。

### 4.4 执行生命周期收口

续接等待和实际 Agent 回合由 `WsBridge` 持有同一份 `ExecutionLifecycle`：

```text
queued --可取消--> stopping --> settled / failed / paused
   \--启动-------> running  --完成--> settled / failed / paused
```

- `begin_continuation()` 先发 `execution.state=queued`，因此 cooldown、抢锁和恢复等待期间仍可点击 Stop。
- `begin_turn()` 激活排队记录时复用同一个 `run_id`；回合结束只允许第一次 `finish_execution()` 发终态快照和兼容 `turn.end`。
- Stop 是幂等的。第一次请求进入 `stopping` 并触发现有 Agent cancel / confirm 解锁；重复请求不重复调用 cancel callback，也不重新排队。
- 被取消的 queued continuation 不得调用模型。它必须收口为终态，不能只发一条“续接已停止”通知后留下工作态。
- 租约重复属于“执行请求被拒绝”，仍发 `running -> failed` 和带同一 `run_id` 的 `turn.end`，便于 Desktop/Terminal 清理工作态；它不启动 Agent，也不进入下一轮续接。

`execution.state` 是回合可观测性协议，不是项目进度真源。v2 的业务续接仍只由 `RunawayController` 和 `pending_runaway_work()` 决定；生命周期只负责记录排队、运行、停止和收口。Desktop 的 v1 idle fallback 不得重新打开已经由生命周期标记为 `stopping`、`paused` 或 `failed` 的执行。

---

## 5. 与升格状态机的关系

续接与 [RUNAWAY-V2.md](./RUNAWAY-V2.md) §6.4 **正交**：

| 事件 | checklist / mode | `pending_work` | `should_continue` |
|------|------------------|----------------|-------------------|
| hook ok | 项 → passed | 重算；可能仍有下一项 | true（若还有活） |
| hook fail, attempts < N | failed, auto | 同一项 | true |
| hook fail, 进入 directed | failed, directed | 同一项缩 scope | true |
| directed 后再 fail | human 升格 | **None** | false |
| 用户 resume | failed 项 attempts 清零 | 重算 | true |
| 清单全绿 + 无 T-* | — | **None**（release_wait） | false |

**禁止**：在 `build_turn_plan` 各分支用 `chain_after_ok=False` 表达 human 升格——升格应只体现在 `pending_work is None` + `runaway_blocked`。

---

## 6. 进展指纹（借 v1 stuck）

v1 `agent._runaway_stuck_turns` 在 v2 中缺失，导致模型空转时 harness 无反馈。

### 6.1 指纹定义

每轮 `run_turn` 结束记录：

```text
ProgressFingerprint = sha256(
    phase + active_task_id + plan_status
    + checklist(id, status, directed_used)
    + project artifacts
    + project files (excluding .agent/.plan-agent/node_modules/
      __pycache__/.pytest_cache/RUNAWAY-PROGRESS.md)
)
```

存入 `session.meta.project_runaway_v2_last_state_fingerprint`，并保留
`project_runaway_v2_no_progress_turns` 与 `project_runaway_v2_auto_turns` 供 UI/诊断使用。

### 6.2 规则

- 若连续 `MY_AGENT_RUNAWAY_V2_NO_PROGRESS_TURNS` 个自动回合 fingerprint 相同
  且 `pending_work` 仍非 `None`，立即进入 `paused`，原因是“连续 N 个自动回合没有检测到项目状态变化”。
- 若累计自动续接达到 `MY_AGENT_RUNAWAY_V2_MAX_AUTO_TURNS`，立即进入 `paused`，原因是“自动回合预算已用尽”。
- 初始用户回合只建立基线，不消耗自动预算；controller 负责记录一次，server 只读判定，避免同一回合双计数。
- 进入 `paused` 后，自动续接和服务端恢复入口都停止；用户显式恢复时清空两个计数和上一次指纹。

### 6.3 与 PROGRESS-GATE 的关系

implement 阶段「口头完成」不勾 TASKS 不算 fingerprint 进展；fingerprint 只看磁盘 `formal_task_stats.done` 与 checklist passed 数。

---

## 7. 续接文案 ritual（chain_user_line）

借 Anthropic「新 session 先读 progress + feature list」：

**固定结构**（`user_copy.harness_chain_user_line` 扩展）：

```text
[狂奔续接] 先读 PROGRESS.md 与验收清单，不要重复已完成项。
当前阶段：{phase}。焦点：{focus_label}。
{task_hint}
```

| phase | `focus_label` 示例 |
|-------|-------------------|
| prepare | `缺：PROJECT.md、VERIFY.md` |
| implement | `T-012 实现用户登录 API` |
| verify | `MX-5 ENV quality.commands` |

**用户显式短句**（「继续」「接着」「下一项」）：`exec_reliability.is_runaway_continue_utterance` 仍可将 intent 标为 `execute`，但 **不影响** `should_continue`（只影响本轮 plan 构建时的 user_text 解析）。

---

## 8. `runaway_chain_skip_reason` 统一派生

当 `should_continue_runaway` 为 false 时，按优先级返回用户可见一句：

| 条件 | 文案方向 |
|------|----------|
| `finish_reason` ∈ 致命集 | 「狂奔未自动续接：{原因}。可点侧栏手动续接或发送继续。」 |
| `release_wait` | 「验收已通过，等待你确认发布。」 |
| human 升格 / `runaway_blocked` | `runaway_block.reason` 或 `runaway_v2_user_line` |
| `pending_work is None` 且无 blocked | 「当前无待推进项。」（防御） |
| L3 通道占用 4 次失败 | 「狂奔通道暂时被占用…」（已有） |

**禁止**静默 return（T-6055-02 已决）。

---

## 9. 决策真值表（验收用）

假设：狂奔开启 · v2 on · 未取消 · 租约正常 · `finish_reason=completed` · controller depth < `MY_AGENT_RUNAWAY_V2_MAX_AUTO_TURNS`。

| derive_phase | checklist / TASKS | 升格 | pending_work | should_continue |
|--------------|-------------------|------|--------------|-----------------|
| prepare | 缺 VERIFY | — | Some(prepare) | **true** |
| prepare | 四件套齐 | — | — | → implement 行 |
| implement | 有开放 T-003 | — | Some(implement) | **true** |
| implement | 队列空 | — | — | → verify 行 |
| verify | MX-5 open | auto | Some(verify) | **true** |
| verify | MX-5 fail ×1 | auto | Some(verify) | **true** |
| verify | MX-5 fail ×2 | directed | Some(verify) | **true** |
| verify | MX-5 directed 后再 fail | human | **None** | **false** |
| verify | 全绿 | — | **None** | **false** → release_wait |
| human | 依赖未满足 | — | **None** | **false** |
| any | — | — | — | intent=qa **不影响** |

---

## 10. 迁移计划

### 10.1 编码顺序与当前状态（T-6107）

| 步 | 内容 |
|----|------|
| 1 | 新增 `continuation.py`：`PendingWork` · `pending_runaway_work` · `should_continue_runaway` · **已完成** |
| 2 | 单测 IT-6107-a～g（§11）· **已完成，纳入 v2 40/40 回归** |
| 3 | `controller.py`：续接改用 `should_continue_runaway` · **已完成** |
| 4 | `agent.py` / `server.py`：v2 续接判断统一走 `continuation`；server 不再拥有 v2 自动续接 · **已完成** |
| 5 | `plan.py`：移除 `chain_after_ok` 字段及赋值 · **待完成** |
| 6 | 移除 `plan.should_chain` 兼容面 · **待完成** |
| 7 | 进展指纹、自动预算、暂停恢复 §6 · **已完成** |

### 10.2 文档

| 文档 | 动作 |
|------|------|
| [RUNAWAY-V2.md](./RUNAWAY-V2.md) §6.5 | 改为摘要 + 链接本文 |
| 本文 | 运行时续接权威 |
| [CHAT-ACTIVITY-TIMELINE.md](./CHAT-ACTIVITY-TIMELINE.md) §7.5 | T-6056 标记为止血；收敛见 T-6107 |
| [TASKS.md](./TASKS.md) | 增 T-6107 · IT-6107 · S-UX-028m 绑定 |

### 10.3 兼容

- `MY_AGENT_RUNAWAY_V2=0`：v1 路径不动。  
- T-6055/T-6056 已落地行为 **不得回退**；收敛后由单测保证等价或更宽松（「更常续」可接受，「更常停」不可）。

---

## 11. 测试矩阵

| ID | 场景 | 期望 |
|----|------|------|
| **IT-6107-a** | prepare + 缺 TASKS | `pending_work.phase=prepare` · `should_continue=true` |
| **IT-6107-b** | implement + 开放 T-* | `pending_work.active_task_id` 正确 |
| **IT-6107-c** | verify + 首项 open | `should_continue=true`；intent=qa 仍 true |
| **IT-6107-d** | directed 后再 fail | `pending_work=None` · `should_continue=false` |
| **IT-6107-e** | 全绿 checklist + 无 T-* | `pending_work=None` · release_wait |
| **IT-6107-f** | controller + server 同输入 | 两者 `should_continue` 一致 |
| **IT-6107-g** | `finish_reason=cancelled` | 跨 turn false；段内若已取消亦 false |
| **S-UX-028m** | music · 发「继续」 | 正常进展时持续续接；重复无进展时按阈值暂停并显示原因 |

---

## 12. 文档关系

| 文档 | 本文之后 |
|------|----------|
| [RUNAWAY-V2.md](./RUNAWAY-V2.md) | §6.5 摘要指向本文；§6.4 升格不变 |
| [RUNAWAY-FLOW-STATE-MACHINE.md](./RUNAWAY-FLOW-STATE-MACHINE.md) | v1 checkpoint 续接；v2 不读 checkpoint |
| [RUNAWAY-R6-RELIABILITY.md](./RUNAWAY-R6-RELIABILITY.md) | 租约 / 预算；与 pending_work 解耦 |
| [CHAT-ACTIVITY-TIMELINE.md](./CHAT-ACTIVITY-TIMELINE.md) | UX 层 idle / 手动续接；业务真源仍是 pending_work |
| [PROGRESS-GATE.md](./PROGRESS-GATE.md) | G9「继续」语义；implement 勾选仍走 report_progress |

---

## 13. 开放问题（默认按括号实施）

1. **段内深度 12 是否可配置？** 默认：保留现有兼容上限，后续单独收口。  
2. **fingerprint 存 session.meta 还是仅内存？** 已决定：存 `session.meta`，跨进程恢复也能识别重复状态。  
3. **prepare hook 失败是否仍续？** 默认：**续**，直到无进展阈值、自动预算或 human 升格命中。  
4. **守卫阈值**：`MY_AGENT_RUNAWAY_V2_NO_PROGRESS_TURNS` 默认 3，`MY_AGENT_RUNAWAY_V2_MAX_AUTO_TURNS` 默认 24。
