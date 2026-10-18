# 狂奔编排架构 — 控制面换血规范

> 版本 **1.0.5** · 2026-10-16  
> 状态：**权威设计**；**R0 架构验收已收口**（2026-10-16 本机复核：v1 112/112、R1 kernel 14/14；见 §6）  
> 附录（行业对照）：[RUNAWAY-ORCHESTRATION-MAP.md](./RUNAWAY-ORCHESTRATION-MAP.md)  
> 产品语义：[RUNAWAY-V2.md](./RUNAWAY-V2.md) · 续接：[RUNAWAY-V2-CONTINUATION.md](./RUNAWAY-V2-CONTINUATION.md)

---

## 1. 换血目标（不是再写一张地图）

v2 已证明 **外部 checklist + 单司机 + 薄 harness** 可行；控制面已从 `RunawayController` 单类迁到 **`runaway_v2/kernel/`**，与 `agent.py` / `server.py` 的 v1 链式逻辑仍并存，**R2 迁包前不得宣称「换血完成」**。

本规范定义 **三平面拆分** 与 **可替换内核**，使：

1. **控制面**（阶段、计划、验收、续接、进度）可单测、可换 driver，不触碰 WS/租约。  
2. **执行面**（父工具环、模型路由、proposal flush）经 **端口** 调用。  
3. **传输面**（TURN_LOCK、idle resume、`_chain_runaway_after_turn`）对 v2 **只交付一次 `run_turn`**。

---

## 2. 三平面模型

（结构同 v1.0.0 — Transport → `RunawayRuntime` → `AgentDriver` → 父工具环。）

| 平面 | 拥有什么 | 不拥有什么 |
|------|----------|------------|
| **Transport** | WS 协议、锁、v1 外链 | phase/plan/checklist 语义 |
| **Control** | `RunawayRuntime` + domain 包 | LLM HTTP、工具实现 |
| **Execution** | 工具环、subagent、模型 id | `should_continue_runaway` |

---

## 3. 包布局

```text
agent-core/runaway_v2/
  kernel/
    ports.py              # AgentDriver 协议
    driver_adapter.py     # Agent → 端口（默认实现）
    project_sync.py
    runtime.py            # 唯一 v2 turn 编排入口
  continuation.py …
  controller.py           # 兼容 façade
```

**入口**：`Agent.run_turn`（v2 on）→ **`RunawayRuntime.run_turn`**。

---

## 4. 端口：`AgentDriver` 与注入

- 编排通过 **`AgentDriver`** 调用执行面（会话准备 · 工具构建 · 模型路由 · 父工具环 · executor 执行上下文 · proposal flush）。  
- **`RunawayRuntime(agent, driver=...)`** 可注入测试用 driver；未注入时默认 `AgentDriverAdapter(agent)`。  
- 会话准备 `prepare_session_for_s4` 与 executor 专属 write-scope/workflow 标记均由 adapter 包在端口后；`RunawayProjectSync` 不再直接访问 `executor.session`。控制面仍直接读写项目会话元数据，且 `TurnResult` 类型当前仍定义于 `agent`；这不等于完全脱离 Session/Agent 领域。  
- **R2 之前**不要大规模 `runaway_v1/` 迁包；R1 fake-driver 验收已过，仍须完成 v1 compat 复核和 R0 架构准入门槛。

---

## 5. 续接与完成（不变语义）

| 决策 | Owner |
|------|-------|
| `pending_runaway_work` / in-turn 深度 | `RunawayRuntime` + `continuation.py` |
| v1 外链续接 | `server._chain_runaway_after_turn`（v2 早退） |

---

## 6. 迁移阶段 R0–R5（诚实状态）

| 阶段 | 内容 | 状态 |
|------|------|------|
| **R0** | `kernel/` 抽出 Runtime + ProjectSync + Driver；`agent` 直挂 Runtime；`driver` 可注入 | **编码与架构验收完成**（本机 v1 compat 112/112、R1 kernel 14/14） |
| **R1** | Fake driver 单测：失败 / 取消 / 续接分支；会话准备与 executor 上下文走端口 | **done** T-6241（14 kernel tests） |
| **R2** | `agent.py` v1 runaway 迁 `runaway_v1/` | **done** T-6242（R0 gate 2026-10-16 已通过） |
| **R3** | transport 契约 + ExecutionLifecycle × 续接 | **T-6243** · **T-6245** |
| **R4** | kernel import 守卫 · v1 符号 lazy 隔离 | **T-6244**（承接原 T-6113 部分） |
| **R5** | 可替换 loop 插件点 | proposal |

### 6.1 自 T-6110～6114 的任务映射（防需求丢失）

| 原草案 | Phase 62 任务 | 说明 |
|--------|---------------|------|
| T-6110 | **T-6248** | 编排边界序列图 + import/IT 守卫 |
| T-6111 | **T-6245** | ExecutionLifecycle × v2 段内续接 / Stop 取消 queued |
| T-6112 | **T-6246** | `turn.start` / notice 外显 `pending_work` 摘要 |
| T-6113 | **T-6242** + **T-6244** | v1 物理迁包 + kernel 不拉 v1 控制面 |
| T-6114 | **T-6247** | verify 命令轻量 pre/post 工具瀑布 |

Phase 62 任务 ID 使用 **T-6240～T-6248**，勿与 Phase 61 **T-6210～6216**（提示词矩阵等）混用。

---

## 7. 验收（分档）

| 档位 | 条件 |
|------|------|
| **R0 编码** | `test_runaway_v2_*` 全绿；`RunawayRuntime` 为产品入口 |
| **R0 架构收口** | R1 fake-driver IT + **v1 compat 112/112 在本机环境复核** + 文档无 Controller/Runtime owner 冲突 |
| **R2 准入** | 上栏全部满足后执行 `runaway_v1/` 迁包；R0 gate 已通过，T-6242 done |

---

## 8. 变更记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 1.0.4 | 2026-10-16 | R1 driver boundary 收口：session prep 与 executor context 经 AgentDriver；R1 定向测试通过 |
| 1.0.5 | 2026-10-16 | R0 架构验收收口：v1 compat 112/112 · R1 kernel 14/14 · v2 + routing 79/79 · 统一 Runtime owner 文案 · T-6242 done |
| 1.0.3 | 2026-10-16 | Phase 62 任务迁至 **T-6240～6248**（避 Phase 61 冲突）· pending_work 续接 `cancel_event` 门 · `bind_cancel_event` 同步 session |
| 1.0.2 | 2026-10-16 | §8 章节编号修正 · R1 IT：fatal/取消/双续接（kernel 注入测） |
| 1.0.1 | 2026-10-16 | 审查收口：R0 诚实状态 · driver 注入 · T-611x→621x 映射 · 验收分档 |
| 1.0.0 | 2026-10-16 | 三平面规范 · R0 kernel 编码 |
