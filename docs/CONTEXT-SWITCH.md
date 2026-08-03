# 上下文换线设计（CONTEXT-SWITCH）

> 版本 **0.3.2** · 2026-08-03  
> **状态**：**M0+M1+M2 已实现**（T-1902～T-1907）  
> **壳合并后**：前端不再 DOM 切壳；「跨壳」语义退化为 **会话线标签 / 项目绑定 / 新会话**。`ui.route` 硬切已退役；`active_shell` 仍可出现在 meta。  
> 关联：[PROJECT-MODE.md](./PROJECT-MODE.md) · [SHELL-CONSOLIDATION.md](./SHELL-CONSOLIDATION.md) · [DESKTOP.md](./DESKTOP.md) §0 · [CONFIRM-PIPELINE.md](./CONFIRM-PIPELINE.md) · BUG-020 · [PROJECT-THREADS.md](./PROJECT-THREADS.md)

---

## 0. 已决摘要

| ID | 决议 |
|----|------|
| **X1** | **换线意图**（换会话线 / 换项目 / 新会话 / 新建项目）与 **线内干活** 分离；前者改会话所有权，后者不改 |
| **X2** | **识别优先由 LLM**（自然语言）；系统提供结构化提案通道，不靠堆正则覆盖口语 |
| **X3** | **换线必须用户确认或拒绝**；无确认 = 不改 `shell_sessions` / `project_sessions` / `active_shell` 绑定 |
| **X4** | **执行只走内核既有 API**（`项目 新建/切换`、会话切换、`新会话` 等）；禁止 LLM 用 `write_text`「假装」立项或换线 |
| **X5** | **显式元命令**（`项目 新建 <id>`、`新会话`、顶栏新会话按钮）可 **免确认或轻确认**；口语路径一律走确认卡 |
| **X6** | 确认前 **禁止** 对「目标新上下文」写盘；提案回合默认 **只提案、不写线外路径** |
| **X7** | UI / 队列 **复用** confirm 管线心智（`request_id` · 超时 · 旧卡作废）；事件可独立命名以免与 tool confirm 混淆 |
| **X8** | `activity_router` **可**推荐主题；**真换会话所有权** 不经过 soft persist（延续 BUG-020）。前端 **不再** 因 `ui.route` 硬切 DOM |
| **X9** | 分期：M0 项目立项/切换 → M1 跨会话线换线 → M2 「新话题/新会话」建议 |

---

## 1. 动机

### 1.1 已观察失败

用户在 **project · stab-r1-demo** 会话说「新项目 java-doudizhu」：

1. 「新项目 …」**不是**元命令 → 进 LLM  
2. LLM 在**当前会话** `write_text` → 磁盘出现 `workspace/java-doudizhu/` 三件套  
3. 会话 meta 仍绑 `stab-r1-demo`  
4. 「项目 确认」确认的是 **旧项目**

同类风险（未全部爆发）：

| 壳 | 口语例 | 坏结果 |
|----|--------|--------|
| project | 「改做 doudizhu」「新开一个项目」 | 写错根 / 确认错项目 |
| project→grow | 「沉淀成 evolve 工具」 | 拒写或乱写；历史仍在 project |
| daily→grow | 「帮我造个工具」 | 软切壳但会话线未换干净 |
| grow | 「别碰刚才那个，新开一个工具话题」 | 同 grow 线串味（M2） |

### 1.2 结论

缺的是统一的 **Context Switch Gate**：

> LLM **提出**换线 → 用户 **确认/拒绝** → 内核 **执行**换线（`session_replaced`）。

不是再堆一条「新项目」别名就能收口（别名可作快捷通道，见 §5.3）。

---

## 2. 概念

### 2.1 两条线（已有）

| 线 | 索引 | 切换入口 |
|----|------|----------|
| 壳线 | `shell_sessions` · grow/daily/project | `shell.switch` |
| 项目线 | `project_sessions` · 每项目一会话 | `项目 切换` / `project.switch` |

### 2.2 三类用户意图

| 类 | 含义 | 本设计 |
|----|------|--------|
| **A · 线内干活** | 继续当前壳/项目 | 正常 turn；工具 confirm 照旧 |
| **B · 换线意图** | 要换所有权 | → `context.switch` 提案 + 用户门闩 |
| **C · 显式元命令** | 用户已写死命令 | 直接执行（X5）；可记 audit |

### 2.3 提案动作（`action`）

| action | 效果（确认后） |
|--------|----------------|
| `project.create` | `create_project` + **新会话**绑定 + 切 project 壳（对齐 P7/P14；**禁止**改绑当前已绑其他项目的会话） |
| `project.switch` | `switch_to_project` → `session_replaced` |
| `shell.switch` | `switch_shell` → 换 `shell_sessions` 指针 |
| `session.new` | 当前壳线 `create_new`（清空/新会话）；project 壳须明确是否解绑项目 |

---

## 3. 流程

```text
用户消息（当前会话）
    │
    ▼
LLM 本轮
    ├─ 判定仅线内干活 ──► 正常工具/回复
    │
    └─ 判定换线 ──► 调用 propose 通道（§4）
                         │
                         ▼
              emit context.switch.request
                         │
              桌面确认卡：[确认换线] [拒绝]
                         │
            ┌────────────┴────────────┐
            ▼                         ▼
     context.switch.response=y   =n / timeout
            │                         │
            ▼                         ▼
     内核 apply(action)          留在当前线
     session_replaced?           注入短系统注记「用户拒绝换线」
     灌 history / banner         本轮可再问一句「那继续在当前项目吗？」
```

**确认前硬约束（X6）**：

- 提案已发出且未决时：拒绝写 **目标** `workspace/<new_id>/`、拒绝跨壳写 evolve（若 action 含切 grow）等  
- 推荐实现：**同一 turn 内** `propose_context_switch` 与写工具互斥；先提案则本 turn 不再执行写工具  
- 若 LLM **未提案**却向「非当前 project_root」写三件套/源码 → executor **拒绝**并提示「请先提出换线并经用户确认」（防复现本次事故）

---

## 4. 协议与呈现

### 4.1 事件（建议）

| 方向 | type | 要点 |
|------|------|------|
| 出站 | `context.switch.request` | `request_id` · `action` · `target`（project_id / shell）· `reason` · `side_effects[]` · `current`（当前 shell/project/session_id） |
| 入站 | `context.switch.response` | `request_id` · `choice`: `y` \| `n` |
| 出站 | `context.switch.done` | `choice` · `applied` · 若换线则带 `session_id` · `session_replaced`（可与现有 `project.switch.done` / `shell.switch.done` 并存或由其承担 apply 结果） |

超时：默认对齐 confirm **90s** → 视为 `n` + notice（[TURN-CONTROL.md](./TURN-CONTROL.md)）。

旧卡：新 `context.switch.request` 到达时作废未决旧卡（同 CONFIRM C4）。

### 4.2 与 tool confirm / plan 确认的关系

| 机制 | 用途 |
|------|------|
| `confirm.request` | **工具执行**前（写文件、run_python…） |
| `plan.request` | **计划开工**（draft→confirmed） |
| `context.switch.request` | **会话/壳/项目所有权**变更 |

三者心智相同（点确认才往下），**语义不要混进同一 payload**，避免 UI 文案「同意执行工具」用在「换项目」上。

桌面可复用 confirm 块样式，文案模板例如：

> 要 **新建项目 `java-doudizhu`** 并切换到新会话吗？  
> 当前仍在 `stab-r1-demo`。确认后聊天区会换成新会话。  
> [确认换线] [拒绝]

### 4.3 CLI

- 口语路径：打印同等摘要，`(y/n)`；与桌面同一 apply 函数  
- 元命令：保持现有 `项目 新建` / `切换` / `新会话`（X5）

---

## 5. LLM 怎么提提案

### 5.1 推荐：专用工具（或 builtin）

`propose_context_switch`（名称可议）：

```json
{
  "action": "project.create",
  "target": "java-doudizhu",
  "reason": "用户要求新开 Java 斗地主项目，与当前 stab-r1-demo 分离"
}
```

- **always confirm**（对用户）：工具层弹出的是 **context.switch** 卡，不是普通 tool confirm  
- 成功（用户 y）：sidecar **先 apply 换线**，再让助手在**新会话**继续（或本 turn 结束并提示「已切换，请继续」）  
- 用户 n：返回 `rejected`，助手不得继续写目标目录

Prompt（project / 通用 core）须写明：

1. 用户要新项目 / 换项目 / 换壳干活 → **先** `propose_context_switch`  
2. **禁止**在未确认前 `write_text` 到其他 `workspace/<id>/`  
3. 拒绝后尊重用户，留在当前线

### 5.2 不采用（本期）

- 纯正则扫「新项目|写斗地主」作为唯一门闩（可作 **辅助启发**，不能替代 LLM+确认）  
- 让活动路由 soft `ui.route` 直接改 `project_sessions`  
- 无确认自动 `create_project`

### 5.3 元命令快捷通道（X5）

| 输入 | 行为 |
|------|------|
| `项目 新建 <id>` | 直接 create + **新会话绑定**（若当前已绑其他项目 → **必须** session_replaced，禁止改绑） |
| `项目 切换 <id>` | 现有确认策略可保留（跨项目已有卡则复用） |
| `新会话` | 直接新会话 |
| `新项目 <id>` | **可选别名** → 同 `项目 新建`（实现期可做；主路径仍是 LLM+卡） |

---

## 6. Apply 规则（确认后）

| action | 规则 |
|--------|------|
| `project.create` | `normalize_project_id`；已存在则改为建议 `project.switch` 或报错重提案；创建三件套骨架；**新 conversation_id**；`record_project_session`；`active_shell=project`；`plan_status=draft`；emit history 替换 |
| `project.switch` | 复用 `switch_to_project` |
| `shell.switch` | 复用 `switch_shell`；**sticky park**（BUG-020） |
| `session.new` | 当前壳 `create_new`；更新对应 `shell_sessions` / 或 project 映射按产品选择 |

拒绝 / 超时：不改任何索引；可选一行 assistant/system 可见说明。

---

## 7. 分期与 DOC-04

### 7.1 里程碑

| 里程碑 | 范围 | 验收要点 |
|--------|------|----------|
| **M0** | `project.create` / `project.switch` + 桌面卡 + 写盘门闩 + prompt | 「新项目 X」→ 卡 → 确认后顶栏/会话变为 X；拒绝则仍为旧项目且无继续写 X |
| **M1** | `shell.switch`（含 project↔grow） | 跨壳口语 → 卡 → 确认后 history 替换 |
| **M2** | `session.new` 建议（grow/daily/project 同壳） | 同壳空白会话；跨壳 target 拒绝；project 保留绑定与 plan_status |

### 7.2 覆盖矩阵影响（DOC-04 草案）

| 面 | 档位 | 建议验收 |
|----|------|----------|
| 口语新建项目 → 确认 → 新会话 | P0 | 新 S-xx；IT：propose→y→session_replaced |
| 拒绝换线后不写目标目录 | P0 | IT：propose→n → write 目标路径被拒 |
| 非当前 project_root 写三件套无提案 | P1 | IT：executor 拒绝 |
| 元命令 `项目 新建` 已绑项目时 session_replaced | P1 | 扩 `test_project_lifecycle` |
| 跨壳换线确认（M1） | P1 | S-/IT- 后续补 |
| soft `ui.route` 不改 ownership | P0 | 回归 BUG-020 / `test_shell_session_ownership` |

回归至少：现有 project switch / shell switch / confirm 90s / S-08～S-09 类项目确认路径。

---

## 8. 非目标（本期）

- 多 worktree / 并行会话编辑同一项目  
- 自动合并两个项目的聊天历史  
- 用 LLM 直接改 `data/state.json`  
- 取消工具 confirm / 计划确认（本设计不替代它们）

**同项目多归档线 /「新开线」**：不在本文件展开；见 [PROJECT-THREADS.md](./PROJECT-THREADS.md)（保留项目绑定的砍线；与 `session.new` 产品名对齐）。

---

## 9. 开放问题（实现前可再钉）

| # | 问题 | 倾向 |
|---|------|------|
| Q1 | 确认换线后，**同一轮用户原话**是否自动在新会话重放？ | M0：**不自动重放**；提示「已切换，请再说一次或继续」更简单可测 |
| Q2 | `propose_context_switch` 算 builtin 还是 host 工具？ | 倾向 **builtin / 内核特殊工具**，不进 evolve |
| Q3 | pet 窗是否显示换线卡？ | 与 daily 共用会话则 **共用**；仅工作台也可先做 |

---

## 10. 修订

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-07-18 | 初稿；X1～X9；LLM 识别 + 用户确认门；M0～M2 |
| 0.1.1 | 2026-07-19 | M0 落地：`propose_context_switch` · WS 卡 · 写盘门闩 · `项目 新建` session_replaced · `新项目` 别名 |
| 0.2.0 | 2026-07-19 | M1：`shell.switch` + 全局换线 overlay；确认后同步外壳 |
| 0.3.0 | 2026-07-19 | M2：`session.new` 同壳新会话（current/grow/daily/project） |
| 0.3.1 | 2026-07-30 | 壳合并后措辞：DOM 切壳退役；X8 / 动机表对齐 unified |
| 0.3.2 | 2026-08-03 | §8 指针：同项目「新开线」见 [PROJECT-THREADS.md](./PROJECT-THREADS.md) |
