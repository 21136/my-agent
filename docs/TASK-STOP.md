# 项目 Task 一停门（TASK-STOP）

> 版本 **0.2.0** · 2026-07-19  
> **状态**：**设计已决 · M0+M1 已实现**（T-2003～T-2007；M2=T-2008 defer）  
> 关联：[PROJECT-MODE.md](./PROJECT-MODE.md) · [ORCHESTRATION.md](./ORCHESTRATION.md) T-705 · [MODE-BUDGET.md](./MODE-BUDGET.md) · [RUNTIME-GUARDS.md](./RUNTIME-GUARDS.md) · [TURN-CONTROL.md](./TURN-CONTROL.md)  
> **延伸（Phase 24）**：勾选证据硬闸见 [PROGRESS-GATE.md](./PROGRESS-GATE.md)（一停扩禁同 turn 再 report_progress）。
> 触发：project 壳「开始编码」一轮内写 POM / 多文件 / 反复 `mvn_exec` / 等人确认，撞上 `TURN_WALL_SEC=900` 墙钟后「回合已超过墙钟限制，已自动停止」

---

## 0. 已决摘要

| ID | 决议 |
|----|------|
| **S1** | **project 壳**默认「**每个 task 一停**」：完成当前 task 后必须 `turn.end`，等用户说「继续」再开下一 task |
| **S2** | **task 单位** = `workspace/<id>/TASKS.md` 中一条可勾选条目（`- [ ]` / `- [x]`）；不以「整 Phase」或「整条用户消息」为单位 |
| **S3** | **完成定义（最小）**：本 task 约定文件已落盘 + `TASKS.md` 该条标 `[x]`；本 task 若写了验收命令则尽量跑通，失败可停并说明（不强制整仓绿） |
| **S4** | project 壳 **关闭** T-705 同 turn 自动续 segment（等价 `MY_AGENT_AUTO_CONTINUE=0`）；**grow / daily 默认不变** |
| **S5** | 标完当前 `[x]` 后，同 turn **禁止**再写下一 task 的产物（M1 硬门；M0 可先 prompt） |
| **S6** | 用户「继续 / 下一 task / 开始下一项」= **新 turn**，助手读 `TASKS.md` 取**第一条未勾**为当前 task |
| **S7** | `TURN_WALL_SEC` **保留**作假死兜底；**不**用拉长墙钟替代 task 门 |
| **S8** | **计划确认门**（`project_plan_status`）不变；本设计是 `confirmed` 之后的 **第二层门** |
| **S9** | 分期：**M0** prompt + project 关 auto-continue；**M1** 内核硬停 + 桌面文案；**M2**（可选）顶栏「当前 task」高亮 |
| **S10** | 标 `[x]` 后同 turn：**允许** `read_file` / `grep` / `list_dir`（含读下一 task）；**允许**写 `MAP.md`；**禁止**写下一 task 源码/产物 |
| **S11** | 用户一条消息要求「做完 T1 和 T2」：**仍一停** — 做完 T1 标 `[x]` 即停，提示「继续」再做 T2 |
| **S12** | `finish_reason=task_paused`：**M0** 仅用文案区分；**M1** 纳入协议与桌面（「本项已完成 · 待继续」，勿与 `timeout` 混淆） |
| **S13** | **checker** 默认只验**当前 task** 范围；整仓验收仍走 `项目 验收` / `project.verify` |

---

## 1. 动机

### 1.1 已观察失败（2026-07-19 · java-doudizhu）

用户确认计划后说「开始编码」：

1. 一轮内写 `pom.xml`、试写大段源码（触发 8192 内联写入 guard）、多次 `mvn_exec compile/test`
2. 多次工具确认 + 一次「确认超时，已跳过」
3. 整 turn 撞上 **900s 墙钟** →「回合已超过墙钟限制，已自动停止」
4. 用户感知：**大项目做不完 / 不适合做大项目**

### 1.2 根因（产品层）

| 现象 | 根因 |
|------|------|
| 墙钟掐断 | 一条用户消息被当成「整项目流水线」 |
| T-705 默认 auto-continue | segment 到顶仍自动续跑，鼓励「一轮干完」 |
| 仅有开工确认门 | `confirmed` 后无 **task 级**停顿 |
| 墙钟是兜底不是边界 | 停因超时，不是「这个 task 做完了」 |

### 1.3 结论

> **适合做大项目，但不适合一条消息偷偷做完多个 task。**

需要统一的 **Task Stop Gate**：

```text
计划确认（已有） → 每次只做一个 TASKS 条目 → 标 [x] → 停 → 用户「继续」→ 下一条
```

---

## 2. 概念

### 2.1 三层门（项目壳）

| 层 | 门 | 作用 |
|----|-----|------|
| L0 | **计划确认门** | 未 `confirmed` 禁止写码（已有） |
| L1 | **Task 一停门**（本文） | 每完成一条 `TASKS` 条目必须停 |
| L2 | **墙钟 / Stop** | 假死兜底；用户可随时停（已有） |

### 2.2 与 T-705 / 墙钟的关系

| 机制 | grow | project（本设计） |
|------|------|-------------------|
| `MY_AGENT_AUTO_CONTINUE` | 默认 `1` | **强制关**（或壳级覆盖） |
| segment 到顶 | 可自动下一段 | **停**，提示「继续」 |
| 标完一个 `[x]` | 可继续写下一件 | **必须 turn.end** |
| `TURN_WALL_SEC` | 900 兜底 | 同左；单 task 粒度应通常远低于 900s |

### 2.3 「当前 task」

```text
current_task = TASKS.md 中自上而下第一条 `- [ ] …`
（跳过标题 / 已 [x] / 非 checkbox 行）
```

- 用户「继续」→ 仍取**当时**第一条未勾（若刚标完，自然是下一条）。
- 用户点名「先做 T-3」→ 允许；该条成为本 turn 唯一目标（M1 可记 `meta.project_current_task`）。

### 2.4 粒度建议（写入模板，非硬编码）

| 过细 | 合适 | 过粗 |
|------|------|------|
| 「写一个 getter」 | 「搭 Maven 骨架 + 空 Main 可 compile」 | 「整个斗地主」 |
| 预计 &lt;2 分钟空转 | 预计 **5～15 分钟**可审 | 一轮必撞墙钟 |

`workspace/_template/TASKS.md` 与 project prompt 应提示：**一条 task ≈ 一次可独立验收的小交付**。

---

## 3. 流程

```text
① 计划 confirmed（已有）
② 用户：「开始编码」/「继续」
③ 助手 read_file TASKS.md → 锁定 current_task
④ 只为实现该条写盘 / 跑命令
⑤ 将该条标 [x]；回复摘要：
      - 完成了哪条
      - 改了哪些路径
      - 下一条是什么
      - 「回复『继续』开始下一项」
⑥ turn.end（finish_reason: completed | task_paused）
⑦ 用户「继续」→ 回到 ③
```

**禁止路径**：

```text
标 [x] 后同 turn 再 write_text 下一模块
同 turn 自动开下一 Phase
用「快做完了」跳过停顿
```

---

## 4. 行为细则

### 4.1 一停时必须输出

| 字段 | 说明 |
|------|------|
| 完成项 | 原文或稳定 id（如 `T-2` / checkbox 文案） |
| 变更摘要 | 路径列表（短） |
| 验证 | 已跑命令与结果，或「本 task 无验收」 |
| 下一项 | 下一条 `- [ ]` 原文；若无则提示可「项目 验收」 |
| 等待语 | 固定心智：**回复「继续」执行下一项** |

### 4.2 「继续」语义

| 用户说法 | 行为 |
|----------|------|
| `继续` / `下一 task` / `下一项` / `开始下一项` | 新 turn；取第一条未勾 |
| `开始编码`（已有未勾） | 同「继续」；若全 `[x]` 则提示验收 |
| `改做 T-x` | 以指定未勾项为当前 task（一轮只这一项） |
| `做完 T1 和 T2`（同条消息） | 做完 T1 即一停；**不**在同 turn 自动开 T2 |
| 无关闲聊 | 正常 qa；**不**自动开下一 task |

### 4.3 未完成就停（仍算一停）

允许因 blocker 停（依赖缺失、确认超时、编译失败）：

- **不要**假标 `[x]`
- 说明 blocker + 已改文件
- 等用户指示（修依赖 / 继续重试 / 改计划）

### 4.4 标 `[x]` 后同 turn 仍允许的动作（S10）

| 动作 | 允许 |
|------|------|
| `read_file` / `grep` / `list_dir`（含读下一 task 文案） | 是 |
| 写 `MAP.md` | 是 |
| 写 `TASKS.md`（仅纠错本条 `[x]`） | 是 |
| 写下一 task 源码、配置、测试等产物 | **否**（M1 硬拒） |

### 4.5 与工具确认 / 内联写入

- 工具 confirm、90s 确认超时：**仍计入**本 turn；故 task 不宜过大。
- 大文件继续走 `workspace/_staging` + `content_workspace_path`（既有 guard）；与一停正交。

### 4.6 `finish_reason`（S12）

| 值 | 何时 |
|----|------|
| `completed` | 正常收口（含「本 task 完成并已停」） |
| `task_paused` | **M1 起**显式标记「因 task 门停」（桌面：「本项已完成 · 待继续」） |
| `timeout` | 墙钟 / stall（既有） |
| `cancelled` | 用户 Stop（既有） |

M0 用 assistant 文案区分即可；**M1 起** `turn.end` 带 `finish_reason=task_paused`。

### 4.7 Checker（S13）

| 场景 | 范围 |
|------|------|
| 手动 `验收` / grow 自动 checker（当前 turn） | **仅当前 task** 约定文件与验收命令 |
| 整仓 / 全 `[x]` 交付 | `项目 验收` · `project.verify`（既有） |
| 跨 task 回归 | 用户显式要求或新开 turn，**不**在一停边界内自动扩 scope |

## 5. 实现落点（设计，未写码）

### 5.1 M0 — 约定 + 软开关

| 落点 | 动作 |
|------|------|
| `evolve/prompts/project.md` | 写入 S1～S6 纪律 |
| `workspace/_template/TASKS.md` | 粒度说明 + 示例条目拆分 |
| `agent.py` / project 模式 | project 壳 `auto_continue_enabled() → False` |
| 桌面 / CLI | 一停后状态栏可提示「待继续」（可选） |

**验收**：prompt 可见；project 下 segment 到顶提示「继续」而非自动下一段。→ **done**（IT-51 · `tests/test_task_stop.py`）

### 5.2 M1 — 硬门

| 落点 | 动作 |
|------|------|
| `meta.json` | 可选 `project_current_task` / `project_task_stop: true` |
| `agent` / `executor` | 本 turn 已将某 `- [ ]`→`[x]` 后：再写 `project_root` 源码/产物 → `validation_error`；`MAP.md` / `TASKS.md` 纠错仍允许（S10） |
| `loader` 用户可见文案 | 统一「本项已完成，回复继续」 |
| 桌面 | `finish_reason=task_paused` → 「本项已完成」而非「已超时」 |

**验收**：IT — 标 `[x]` 后同 turn 再写下一源码文件被拒；「继续」后允许。→ **done**（IT-52 · `tests/test_task_stop.py`）

### 5.3 M2 — UX（可选 / defer）

- 侧栏高亮当前未勾第一条
- 顶栏 `n/m` 旁显示当前 task 短标题
- 「继续下一项」快捷按钮（发用户消息 `继续`）

---

## 6. DOC-04 准入（提案自检）

### 6.1 影响 [STABILIZATION.md](./STABILIZATION.md) §3 矩阵行

| 矩阵面 | 档位 | 变更说明 |
|--------|------|----------|
| project 写码 / 计划 confirmed 后执行 | P0/P1 | 增加 task 级停顿；回归「确认后可写」仍成立 |
| T-705 / mode-budget segment 续跑 | P2（IT-48） | **project 壳覆盖**：关 auto-continue |
| turn.end / 超时文案 | P1 | 可选新 `task_paused`；勿与 `timeout` 混淆 |
| 顶栏 n/m · TASKS 侧栏 | P1 | M2 高亮；M0/M1 行为以文件为准 |
| CLI `项目 状态` / 继续语义 | P1 | 文档化「继续」= 下一未勾 task |

### 6.2 回归 / 新增验收 ID

| ID | 类型 | 说明 |
|----|------|------|
| **既有** | IT-02 / project.switch · plan gate · S-08/S-09 类 | 计划门与切换不回归 |
| **既有** | IT-48 segment / MODE-BUDGET | grow 仍可 auto-continue；仅 project 关闭 |
| **S-50**（新增） | smoke | project：完成一条 `[x]` 后助手停并提示继续；不自动开下一项 |
| **S-51**（新增） | smoke | project：用户「继续」后开始下一条未勾 |
| **IT-51**（新增） | 自动 | project 壳 `auto_continue` 关闭 |
| **IT-52**（新增 · M1） | 自动 | 标 `[x]` 后同 turn 写下一产物 → 拒绝 |

---

## 7. 非目标

| 不做 | 原因 |
|------|------|
| 取消墙钟 | 仍需假死兜底 |
| grow 也强制一停 | grow 养工具常需同 turn 多文件；保持 T-705 |
| 用 UI 勾选代替改 `TASKS.md` | 真源仍是磁盘（P4） |
| 多 agent 并行多 task | 超出本 Phase |
| 自动把粗 task 拆细 | 计划阶段人工/助手拆；一停只管执行边界 |

---

## 8. 与既有文档的指针

| 文档 | 关系 |
|------|------|
| [PROJECT-MODE.md](./PROJECT-MODE.md) | 新增 **P20**；§3.2 / §10 引用本文 |
| [ORCHESTRATION.md](./ORCHESTRATION.md) §5 | project 覆盖 T-705 auto-continue |
| [RUNTIME-GUARDS.md](./RUNTIME-GUARDS.md) | 墙钟仍为 L2；一停为 L1 |
| [MODE-BUDGET.md](./MODE-BUDGET.md) | agent 宽预算不变；project 用停顿而非缩轮次 |
| [TASKS.md](./TASKS.md) Phase 20 | 实现任务表 |

---

## 9. 已决事项（2026-07-19 评审）

| # | 问题 | 决议 |
|---|------|------|
| Q1 | 同 turn 是否允许「读下一 task / 更新 MAP」？ | **S10**：只读与读下一 task 允许；写 `MAP.md` 允许；写下一 task 产物禁止 |
| Q2 | 用户一条消息「做完 T1 和 T2」？ | **S11**：做完 T1 即停，须「继续」再做 T2 |
| Q3 | `task_paused` 是否进协议？ | **S12**：M0 文案；M1 协议 + 桌面 |
| Q4 | checker 是否跨 task？ | **S13**：默认仅当前 task；整仓走 `项目 验收` |

---

## 10. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0-draft | 2026-07-19 | 初稿：S1～S9；java-doudizhu 墙钟触发；M0/M1/M2；DOC-04 矩阵与 S-50/51、IT-51/52 |
| 0.2.0 | 2026-07-19 | **评审定稿**：S10～S13（Q1～Q4）；§4.4～4.7；T-2002 签字 |
| 0.2.0+M0 | 2026-07-19 | M0：`project.md` / `_template/TASKS.md`；`auto_continue_enabled(active_shell=)` project 强制关；IT-51 |
| 0.2.0+M1 | 2026-07-19 | M1：task_stop 硬门；`finish_reason=task_paused`；继续 overlay；路径兼容 `<id>/…`；IT-52 |
