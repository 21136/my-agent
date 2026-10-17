# 狂奔编排架构地图 — 行业对照与 my-agent 落点

> 版本 **0.2.0** · 2026-10-16  
> 状态：**行业附录**；权威换血规范见 [RUNAWAY-ORCHESTRATION.md](./RUNAWAY-ORCHESTRATION.md)  
> 父文档：[RUNAWAY-V2.md](./RUNAWAY-V2.md) · [RUNAWAY-V2-CONTINUATION.md](./RUNAWAY-V2-CONTINUATION.md) · [PERSONAL-OMNICAP-ROADMAP.md](./PERSONAL-OMNICAP-ROADMAP.md)  
> 关联：[AGENT-PARENT-ORCHESTRATION.md](./AGENT-PARENT-ORCHESTRATION.md)（普模薄父） · [execution-lifecycle-design.md](./superpowers/specs/2026-09-11-execution-lifecycle-design.md)（T-6230）

---

## 0. 为什么要有这份地图

用户体感「狂奔还会出问题」，往往不是缺一个 **上层 Planner LLM**，而是 **编排权散落**：谁在续接、谁在判完成、谁在写 ENV、谁在抢 TURN_LOCK。  
本仓库 v2 已选定 **单司机 + 外部 checklist + 薄 harness**；本文把 **Claude / Codex / Cursor / DeepSeek Harness** 的公开架构与 **my-agent v1/v2 模块** 对齐，并列出 **下一刀编排工程**（不引入「太上皇模型」）。

---

## 1. 五家一句话 + 分层

| 来源 | 核心形态 | Harness 放哪 | 长程进度真源 | 多 Agent 怎么用 |
|------|----------|--------------|--------------|-----------------|
| **Anthropic 长程 harness** | Initializer + Coding 多 session | 提示词分叉 + 会话 ritual | `feature_list.json`、git、`progress` 文件 | 子代理/多 session；大规模用脚本编排 |
| **Claude Code** | 单 reactive loop | **Hooks**（硬）+ Skills/CLAUDE.md（软） | 对话 + compaction | **Subagents** 隔离上下文；**Workflow** 脱离对话跑大量 agent |
| **OpenAI Codex** | 单 Agent ReAct loop | CLI `agent-loop` + 系统前缀 + 工具契约 | 全量 history + **自动 compaction** | 基本单线程；工具并行可配 |
| **Cursor** | 本地 loop + **Cloud Temporal** | Harness **变薄** → 工具/环境；子类型仍有专用 routing | 云：DB + workflow + VM；本地：索引/会话 | 云多模型试同一 bug；child workflow ≈ subagent |
| **DeepSeek Harness (`dsh`)** | **一切皆插件**（Cordis） | 默认 `agent-loop` **可替换**；工具 **事件瀑布** | append-only **SessionEvent** | Agent teams / 插件 scope |

**行业共识（与 RUNAWAY-V2 §1 一致）**

1. Harness ≠ 第二个大脑，而是 **loop + 工具 + 护栏 + 磁盘进度**。  
2. 「完成」由 **外部清单/命令 exit**，不由模型口头宣布。  
3. 确定性阶段/续接 increasingly 在 **代码**，不在 prompt 小作文。  
4. 多 Agent 用于 **并行或清上下文**；主线交付常仍是一个 driver loop。

---

## 2. 架构示意（抽象）

### 2.1 Codex / Claude Code / 本地 Cursor（单环）

```text
用户
  → Harness（instructions · tools · sandbox · hooks）
  → LLM
  → tool calls → 环境（shell / FS / MCP）
  → 结果写回 context
  → 重复直到 stop / compact
```

### 2.2 Anthropic 长程 coding（多 session + 清单）

```text
Initializer session → 清单 + init.sh + progress + git
Coding session N   → 读清单 → 一项 → 自测 → 更新清单 + commit + progress
（新 context window，同一磁盘真源）
```

### 2.3 Cursor Cloud（durability 在外层）

```text
API → Temporal Workflow
        → VM 生命周期
        → 内层 agent loop（同本地哲学）
        → 状态/重试/跟评在 workflow，不在模型
```

### 2.4 DeepSeek Harness（可替换 loop）

```text
Cordis ctx
  session (事件日志) ←→ agent-loop (默认 driver)
  tools (pre/execute/post 瀑布) ←→ llm adapter
  UI / desktop / profiles = 插件
```

### 2.5 my-agent 狂奔 v2（当前目标形态）

```text
server._run_line（租约 · TURN_LOCK · ExecutionLifecycle）
  → Agent.run_turn
       → RunawayRuntime（kernel · v2 唯一编排 owner）
            derive_phase → build_turn_plan → _run_parent_tool_loop
            → run_acceptance → 更新 checklist
            → should_continue_runaway → 段内再 run_turn
  → project.state / Desktop（展示，不 business-chain v2）
```

**刻意不做**：v2 路径上的 bug-fix 轨、Harness 0-LLM 短接、8 态 checkpoint 控制面（见 RUNAWAY-V2 §3.1 禁止列表）。

---

## 3. 能力对照表 → my-agent 模块

| 行业概念 | Anthropic | Codex | Cursor | dsh | **my-agent v2** | **my-agent v1（冻结）** |
|----------|-----------|-------|--------|-----|-----------------|-------------------------|
| 外部清单 | feature_list | —（靠测试/对话） | 计划/PR 工具 | session 可扩展 | `.agent/runaway-checklist.json` | VERIFY 矩阵 + checkpoint |
| 一项一验收 | 每 feature E2E | prompt「跑测试」 | 云更自主 | tool pipeline | `run_acceptance` + `TurnPlan` | 硬验收 + Harness 谓词 |
| 单司机写码 | coding agent | 主 loop | 主 loop | agent-loop | `_run_parent_tool_loop` | 主 loop + **bug_fix 子代理** |
| 续接 | 新 session / 脚本 | compact 后继续 | Temporal signal | resume session | **Controller 段内** + `pending_work` | server chain + Desktop 2.5s |
| 硬护栏 | 测试工具 | sandbox / approval | hooks / VM | tools/* 瀑布 | confirm 管线 · write_scope | repair_count · 短接 |
| 进度文件 | progress.txt | — | — | SessionEvent | `RUNAWAY-PROGRESS.md` | PROGRESS-GATE 等 |
| 编排 LLM | 无（双提示） | 无 | 无 | 可插拔 | **无** | 无 |

普模（未开狂奔）编排见 [AGENT-PARENT-ORCHESTRATION.md](./AGENT-PARENT-ORCHESTRATION.md)：主 Agent 显式 `plan_partner` / `explore` / `deliverable_review`，与狂奔 **TurnPlan 预收窄** 是两条 profile。

---

## 4. 续接 owner 对照（最易踩坑）

| Owner | 职责 | v2 | v1 |
|-------|------|----|----|
| **RunawayRuntime** | 同 WS turn 内 `pending_work` → 再 `run_turn` | **唯一业务续接** | 不使用 |
| **server `_chain_runaway_after_turn`** | turn.end 后注入 `[Harness]` 行 | **直接 return** | 跨 turn 续接 |
| **Desktop `scheduleRunawayIdleResume`** | idle 2.5s 强拉 resume | **禁用**（`runaway_version >= 2`） | 兜底第三 owner |
| **ExecutionLifecycle** | queued/running/stopping；Stop 幂等 | 与续接 **可观测性** 绑定，非进度真源 | 同左 |

权威条文：[RUNAWAY-V2-CONTINUATION.md](./RUNAWAY-V2-CONTINUATION.md) §4。

---

## 5. 从行业借鉴什么、不搬什么

| 借鉴 | 不整包搬 | my-agent 落点 |
|------|----------|----------------|
| Anthropic 清单 + 一项一验 + progress ritual | Initializer/Coding **两个不同产品会话** | `checklist` + `append_progress` + `TurnPlan.acceptance` |
| Codex 单 turn 内多 tool round | Responses API / compaction 实现 | `_run_parent_tool_loop` + 会话 compact（普模） |
| Cursor 会话/项目/runtime 三层 + 薄 harness | Temporal + 全云 VM 栈 | `RUNAWAY-V2` §3.2；Desktop 只展示 v2 字段 |
| Cursor Cloud 工具化（gh cli 等） | Anyrun 细节 | 逐步把 Harness 行为改成 **builtin 工具**（Phase 61 方向） |
| dsh 工具事件瀑布 + session 日志 | Cordis 全插件内核 | 可参考 **confirm/工具前后钩子** 结构；**不**重写 sidecar 为 dsh |
| Claude Hooks | 全量 Hook 生态 | 已有 confirm 管线；狂奔 v2 用 **acceptance + write_scope** 作硬门 |
| Claude Subagents | 狂奔主线再叠子代理轨 | v2 **禁止** repairing 轨 `run_bug_fix`；子代理留在普模/手工 |

---

## 6. 推荐下一刀（编排工程，非新 LLM）

> **实施任务以 [TASKS.md](./TASKS.md) Phase 62 为准**（**T-6240～T-6248**；勿与 Phase 61 的 T-6210～6216 混用）。下表为历史草案 ID → 现行映射。

| 草案 ID | Phase 62 | 主题 |
|---------|----------|------|
| T-6110 | **T-6248** | 编排边界序列图 · import 守卫 |
| T-6111 | **T-6245** | ExecutionLifecycle × v2 段内续接 |
| T-6112 | **T-6246** | `pending_work` 可观测 |
| T-6113 | **T-6242** + **T-6244** | v1 迁包 · kernel 隔离 |
| T-6114 | **T-6247** | verify 工具 pre/post 瀑布 |

**不建议** 作为狂奔 v2 下一刀：

- 常驻 Planner/Evaluator 三模型（Anthropic 长程 App 实验轨，非默认产品）。  
- 把 DeepSeek Harness 嵌进 `agent-core`（与 Desktop/Electron 栈不匹配）。  
- 恢复 server/Desktop 为 v2 第二、第三续接 owner。

---

## 7. 参考链接（公开）

| 来源 | URL |
|------|-----|
| Anthropic · Effective harnesses for long-running agents | https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents |
| Anthropic · Harness design for long-running apps | https://www.anthropic.com/engineering/harness-design-long-running-apps |
| OpenAI · Unrolling the Codex agent loop | https://openai.com/index/unrolling-the-codex-agent-loop |
| OpenAI · Compaction | https://developers.openai.com/api/docs/guides/compaction |
| Cursor · Cloud agent lessons | https://cursor.com/blog/cloud-agent-lessons |
| Cursor · Cloud Agents | https://cursor.com/blog/cloud-agents |
| DeepSeek Harness · architecture | https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md |
| DeepSeek Harness · agent-loop | https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/README.md |
| Claude Code · Subagents | https://code.claude.com/docs/en/sub-agents |
| Claude Code · Hooks | https://code.claude.com/docs/en/hooks |

---

## 8. 文档关系

| 文档 | 角色 |
|------|------|
| 本文 | **行业对照 + my-agent 模块地图 + 下一刀任务草案** |
| [RUNAWAY-V2.md](./RUNAWAY-V2.md) | v2 运行时契约（阶段 · 清单 · TurnPlan） |
| [RUNAWAY-V2-CONTINUATION.md](./RUNAWAY-V2-CONTINUATION.md) | 续接单真源与 owner 表 |
| [RUNAWAY-FLOW-STATE-MACHINE.md](./RUNAWAY-FLOW-STATE-MACHINE.md) | v1 checkpoint（superseded 运行时） |
| [RUNAWAY-VERIFICATION-ORCHESTRATOR.md](./RUNAWAY-VERIFICATION-ORCHESTRATOR.md) | v1 谓词门（superseded 于 v2） |

---

## 9. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.2.0 | 2026-10-16 | 降为行业附录；编排权威迁至 RUNAWAY-ORCHESTRATION.md · Runtime 替换 Controller 叙述 |
| 0.1.0 | 2026-10-16 | 初稿：五家架构对照 · v1/v2 模块映射 · T-6110～6114 草案 |
