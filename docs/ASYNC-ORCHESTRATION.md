# 异步编排续跑（ASYNC-ORCHESTRATION）

> 版本 **0.1.5** · 2026-08-07 · **状态：Pack 6 M0 done（S-560 pass）· M1 wake defer**  
> 父文档：[ROADMAP-PACK-1245.md](./ROADMAP-PACK-1245.md) §7（Pack 6）  
> 关联：[RUN-SERVICE.md](./RUN-SERVICE.md) · [ORCHESTRATION.md](./ORCHESTRATION.md) T-705 · [TASK-STOP.md](./TASK-STOP.md) · [EXEC-RELIABILITY.md](./EXEC-RELIABILITY.md) G14 · [RUNTIME-GUARDS.md](./RUNTIME-GUARDS.md) G13 · [EXEC-OBSERVABILITY.md](./EXEC-OBSERVABILITY.md)

---

## 0. 一句话

**起服 / 等 ready / 查日志 / 起下一服务** 应在 **同一用户回合内用工具做完**；禁止口头「等 15 秒我再查」就 `turn.end`。可选 M1：**服务仍在 starting 时由 harness 定时唤醒续回合**，减少用户反复发「继续」。

---

## 1. 问题（用户观测 · 2026-08-07）

| 现象 | Cursor | my-agent 今日 |
|------|--------|---------------|
| 起多个微服务后说「等 15～20 秒查日志再起前端」 | 回合 **不结束**；`wait` / `logs` / 再 `start` 在同轮完成 | 常 **说完就停**；用户须再发「继续」 |
| 服务表 `starting → running` | 聊天 + 工具循环持续 | 侧栏可有状态，但 **无自动续跑** |
| 宣称「已启动」 | 探活通过才收口 | **G14** 已拦假成功（`〔未满足后置条件·已拦截〕`） |

**根因分层**（非单一 bug）：

```text
L0  模型口头延期，未调 run_service wait/start.ready_timeout   ← M0 主攻
L1  project 关 T-705 segment 自动续跑                         ← 与 Task 一停正交
L2  Task 一停（report_progress 勾 [x] 后停）                  ← 保留，不拦起服链
L3  无「定时唤醒续回合」                                       ← M1 可选
```

**已有能力（勿重复造轮）**：

- `run_service` · `wait` · `start`+`ready_regex`/`ready_port`/`ready_timeout_sec` — **Phase 25 done**
- G13 空头动作门 — 仅覆盖「正在启动 / 我来写…」，**不覆盖**「等 N 秒后再…」
- G14 后置条件 — 拦 **假成功话术**，不解决 **回合提前结束**

---

## 2. 目标与非目标

### 2.1 目标（M0 · 必做）

1. **编排纪律**：起服流水线 = 单回合 **工具链**；口头延期视为违规。
2. **G13 扩展**：匹配「等\s*\d+…秒」「N 秒后.*(查|看|检查)」→ 注入 nudge，要求本回合调 `run_service` `wait`/`logs`/`status`。
3. **Prompt / INDEX**：`run.md` 或 project 段 1～2 句：多服务起服须 `wait` 或 blocking `start`；禁止「稍后我会…」收口。
4. **与 Task 一停边界**：仅 `report_progress` 成功 toggle **当前 task** `[x]` 后触发一停；**起服子步骤不算 task 完成**。

### 2.2 目标（M1 · 可选 · 签字后）

5. **Deferred wake**：`run_service start` 且 `ready=false` / `alive` 且 `starting` 时，sidecar 登记 `wake_at`；到点注入内核续跑（或等价自动 segment），上限 `MY_AGENT_ORCH_WAKE_MAX`（默认 3）/ 会话。
6. **用户可见**：侧栏或 notice「将在 N 秒后自动检查 gateway 日志」+ Cancel。

### 2.3 非目标

- 取消 **Task 一停**（Phase 20）或 **Progress Gate**
- 取消 `run_service` **start confirm**（M0 不改确认面；M1 也不默认免确认）
- 跨机 K8s 编排 / Cursor Cloud Agent 式后台 VM
- 用拉长 `mvn_exec` 超时冒充长驻（仍走 `run_service`）

---

## 3. 推荐工具链（M0 真源）

```text
用户：起 gateway + doctor + business，好了再起前端

助手（单回合内）:
  run_service start gateway   (+ ready_port/regex, ready_timeout_sec)
  run_service start doctor-service
  run_service start business-service
  run_service wait gateway    (或 start 已阻塞则 skip)
  run_service logs gateway / status list
  run_service start frontend  (ready 后再起)
  文字摘要（须 G14 后置条件通过才可写「已可访问」）
```

**禁止收口句式**（回合不得仅含此类而无后续 tool）：

- 「等 15～20 秒我检查日志」
- 「启动中，稍后再起前端」
- 「我先去查一下再告诉你」（无 tool_calls）

---

## 4. 任务与验收

| ID | 档位 | 交付物 | 验收 |
|----|------|--------|------|
| T-5600 | doc | 本文 + ROADMAP §7 | 评审 |
| **T-5601** | M0 | prompts · tool-catalog `run.md` 脚注 | grep 含 wait 纪律；无长段教程进 core |
| **T-5602** | M0 | `agent.py` G13 扩展 + IT-560 | 中：「等 20 秒后查日志」· 英：`wait 15 seconds then check logs` → nudge |
| **T-5603** | M0 | `TASK-STOP` / loader 边界一句 | IT-561：起服链未 report_progress 不 task_paused；**segment cap 中 wait 链** 行为 |
| **S-560** | M0 | 手工 smoke | huiyi 类：3 服务 + 前端 **一轮用户消息**内完成，**0 次「继续」** |
| T-5604 | M1 | `server.py` / session `orch_wake` 登记 | IT-562 wake 注入 |
| T-5605 | M1 | 桌面 Cancel + notice | S-563 |
| T-5606 | M1 | env `MY_AGENT_ORCH_WAKE_*` | defer 默认关 |

### 4.1 S-560 步骤（写入 stabilization-log）

1. 绑定含多模块后端的 project（建议 huiyi）。
2. 用户一句：「起 gateway、doctor、business，都好了再起前端。」
3. **通过**：
   - [ ] 过程区出现 `run_service` `wait`/`logs` 或 blocking `start`（非仅口头等待）
   - [ ] 未因 segment cap 中途停（若停须 auto 续或 M1 wake；M0 允许调大 segment 仅作 smoke 备注）
   - [ ] 用户 **未** 发送「继续」
   - [ ] 前端 ready 后话术通过 G14（无未拦截假成功）

**M1 升格触发**：若 S-560 **因单回合等待总时长超出可接受上限**（多服务 `wait` 累加撞 segment/wall）而 fail → **T-5604～5606 从 defer 升为 M0**（ROADMAP D6-3）。

---

## 5. 与 Pack 关系

| Pack | 关系 |
|------|------|
| **Pack 1** | 先过 S-472/480/481；S-560 可紧跟 Pack 2 或并行评审 |
| **Pack 2** | 同属「日用体感」；**T-5202 免确认** 与起服 confirm **独立** |
| **Pack 4/5** | 无依赖 |

**推荐编码顺序**（接 ROADMAP §2）：

```text
Pack 1  S-472 → S-480 → S-481
Pack 2  T-5202 → S-421
Pack 6  T-5601 → T-5602 → T-5603 → S-560
Pack 4  …
Pack 5  …
```

---

## 6. DOC-04

| 面 | 档位 | 回归 ID |
|----|------|---------|
| agent tool loop / G13 | P1 | IT-560 · IT-561 |
| run_service 编排 | P1 | S-560 · IT-75 回归 |
| Task 一停边界 | P1 | IT-561 |
| deferred wake（M1） | P2 | IT-562 · S-563 |
| 壳 / Plan 门 / host | — | 无 |

---

## 7. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-08-07 | 初版：L0/L3 分层 · M0/M1 · 并入 ROADMAP Pack 6 |
| 0.1.1 | 2026-08-07 | Fable5：父节 §7 · IT-560 双语 · IT-561 segment cap · M1 升格条件 |
| 0.1.2 | 2026-08-07 | T-5601：`run.md` + `project-boundaries` 起服 wait 纪律 · `test_async_orchestration_prompts.py` |
| 0.1.3 | 2026-08-07 | T-5602：G13 `announces_orchestration_defer` + `ORCH_DEFER_NUDGE_MESSAGE` · IT-560 |
| 0.1.4 | 2026-08-07 | T-5603：TASK-STOP §2.5 · overlay `orch_boundary` · IT-561 |
| 0.1.5 | 2026-08-07 | **S-560 pass** · Pack 6 M0 收口 |
