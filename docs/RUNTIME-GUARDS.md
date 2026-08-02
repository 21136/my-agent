# 运行时行为约束（RUNTIME-GUARDS）

> 版本 **0.2.1** · 2026-08-02  
> **状态**：**M0+M1 已实现** + **G13 空头动作声明门**（announce without tools → nudge）  
> 关联：[TURN-CONTROL.md](./TURN-CONTROL.md) · [CONFIRM-PIPELINE.md](./CONFIRM-PIPELINE.md) · [TOOLS.md](./TOOLS.md) · [ORCHESTRATION.md](./ORCHESTRATION.md)  
> 动机：2026-07-13 grow 沉淀 `mvn_exec` — 文件已落盘，但 **思考中卡死**、Stop 不灵、助手 **违反纪律** 反复 `run_python` / confirm；Phase 14/15 解决「管线诚实 + 用户可打断」，**不**解决「模型乱来仍能把回合拖死」。另：huiyi 联调「重新建库建表：」后无 tool_calls → **G13**。

---

## 0. 已决摘要

| ID | 决议 |
|----|------|
| **G1** | 约束在 **executor / agent loop / server** 落地；prompt 仅作说明，**不是安全边界** |
| **G2** | 与 Phase 14（confirm 诚实）、Phase 15（Stop）正交；本 Phase 管 **硬顶、拒调、自动收尾** |
| **G3** | Phase 15 defer 的 T-1410～T-1412 迁入本 Phase（见 §5） |
| **G4** | **M0 先不死**：LLM timeout 正常收口、turn 墙钟、可选 stall、子进程 cancel、`turn.end` 必达 |
| **G5** | `TURN_WALL_SEC=900`，覆盖一条用户消息的完整 turn（含 explore/checker/所有 segment），**segment 不重置墙钟** |
| **G6** | `STALL_WATCHDOG_SEC=0` 默认关闭；启用建议 `180`。`reasoning.delta` **不算进度**，否则无法识别“只思考不行动” |
| **G7** | 用户 Stop、stall、墙钟均复用同一 cancel 通道；**先触发者生效一次**。用户 Stop=`cancelled`，自动超时=`timeout` |
| **G8** | 子进程取消集中在 `run_evolved.execute_evolved_tool` 外层：`Popen` → `terminate` → 等 3s → `kill`；Windows 进程树终止 defer |
| **G9** | M0 提供可取消的内部 `run_scaffold_demo`（供手动 checker 调用）；M1 在 `tool.toml` 写入 + registry reload 后自动调用；不 confirm；失败不强制结束 turn |
| **G10** | 禁止粗暴封锁 `run_python`：仅在当前 execute segment、grow scaffold、目标为本 segment 新写 tool 的 demo 时拒调；staging 与 project 壳不受影响 |
| **G11** | 内联正文解码后 `>8192` 且无 `content_workspace_path` → confirm 前 `validation_error` |
| **G12** | Checker 见 [CHECKER-SUBAGENT.md](./CHECKER-SUBAGENT.md)：本 Phase **执法并产出硬事实**，checker **独立审计** |
| **G13** | **空头动作声明门**：agent 模式正文匹配「正在/接下来/我来/建表…」且 **无 tool_calls** → 注入内核 nudge **一次**并继续回合；ask/recall 不触发 |
| **G14** | **执行可靠性**（后置条件 + 熔断；**剧本自动 nudge 废止中** · 见 [EXEC-RELIABILITY.md](./EXEC-RELIABILITY.md) §3.4/M3）— 管「假成功 / 同招空转」，修好环境靠本地长任务能力 |

---

## 1. 动机

### 1.1 问题

| 现象 | 根因类型 | Phase 14/15 是否覆盖 |
|------|----------|----------------------|
| 「思考中…」10+ 分钟 | LLM 流 / 无事件静默 | 部分 — Stop；仍可能卡到 LLM 读阻塞 |
| Stop →「正在停止…」不停 | 同线程阻塞；UI 被 reasoning 覆盖 | M0 已修部分；无 stall 看门狗 |
| 沉淀完成仍调 `run_python` demo | 模型无视 prompt 纪律 | **否** — 软约束 |
| 大段 tool 参数进历史 | 无内联写入硬顶 | defer T-1411 |
| `npm_exec` / `run_python` 占满线程 | subprocess 不可 cancel | defer T-1412 |

### 1.2 与监工线的分工

```text
Phase 16 约束（本文件）  →  不让坏行为发生 / 不能无限持续
Phase 17 checker（另文）  →  做完之后查对不对（可选第二次 DeepSeek）
```

**约束优先**：便宜、可单测、不依赖模型自觉。

---

## 2. 架构（三层）

```text
用户 turn
    │
    ▼
┌─ 时间层 ─────────────────────────────────────────┐
│  LLM_TIMEOUT · CONFIRM_TIMEOUT · TURN_WALL_SEC   │
│  STALL_WATCHDOG（无 tool/assistant 事件 → 结束）  │
└──────────────────────────────────────────────────┘
    │
    ▼
┌─ 工具层（executor policy）────────────────────────┐
│  write_evolve guard · 内联上限 · 场景拒调表       │
│  自动 demo（无 confirm）· 沉淀后 tool 黑名单      │
└──────────────────────────────────────────────────┘
    │
    ▼
┌─ 协议 / UI 层 ───────────────────────────────────┐
│  turn.end 必达 · cancel 看门狗 · 状态不被 reasoning 覆盖 │
└──────────────────────────────────────────────────┘
```

---

## 3. 约束清单

### 3.1 时间约束

| 规则 | 默认 | 实现落点 | 备注 |
|------|----------|----------|------|
| 单次 LLM | 已有 `LLM_TIMEOUT_SEC` 120 | `llm_client` | 保持 |
| 单次 confirm | 已有 `CONFIRM_TIMEOUT_SEC` 90 | `server.confirm_fn` | 保持 |
| **回合墙钟** `TURN_WALL_SEC` | **900** | `_run_line` / agent | 整条 user turn；segment 不重置；到点 `finish_reason=timeout` |
| **stall 看门狗** `STALL_WATCHDOG_SEC` | **0（关闭）** | `WsBridge` / agent | opt-in 建议 180；距上次有效进度超时 → cancel |
| 桌面 cancel 兜底 | **45s** | `chat-state.ts` | 已实现；仅恢复 UI，不声称 sidecar 已停止 |
| **Task 一停（project）** | 草案 | Phase 20 · [TASK-STOP.md](./TASK-STOP.md) | 每 `TASKS` 条目完成即停；**不**用拉长墙钟替代；L2 墙钟仍保留 |

**有效进度事件**：`assistant.delta` · `tool.start` · `tool.end` · `confirm.request` · `confirm.done`。

`reasoning.delta` **明确不计入** stall 进度。它可以继续展示，但不能用无限 reasoning 延长看门狗。

**超时语义**：

- `LLMTimeoutError` 在 agent 层收口为 `timeout`，不作为未分类 `error` 泄漏。
- 用户 Stop 与自动 timeout 竞争时只允许一次终态；先触发者决定 `finish_reason`。
- `finish_reason=timeout` 必须由桌面恢复 composer，并显示「已超时」而非「错误」。

### 3.2 工具 / 写入约束

| 规则 | 触发条件 | 动作 | 状态 |
|------|----------|------|------|
| base64 预检 | `write_evolve` + `content_base64` | confirm 前拒 | **已实现** C7 |
| 内联写入上限 | plain/b64 解码后 > `WRITE_INLINE_MAX_CHARS` | `validation_error` | defer → **本 Phase** |
| **确定性 demo probe** | 手动 checker 验收，或 grow scaffold 已具备完整 `main.py` + `tool.toml` | 内核 `run_scaffold_demo`：`python <tool_dir>/main.py demo`；无 confirm、可 cancel | M0 |
| **自动 demo 触发** | grow scaffold 成功写入 `tool.toml` 且 registry reload 成功 | 自动调用同一 `run_scaffold_demo` | M1 |
| **窄域拒调** | 同一 execute segment 已写某 tool，模型又用 `run_python` 跑该 tool 的 demo | confirm 前 `validation_error`，引导读取自动 demo 结果 | M1 |
| staging | `write_text → workspace/_staging_*` | **允许**；这是大文件路径引用的规范流程 | 已决 |
| project 壳 | project 中的一般 `run_python` / 验收 | **豁免本条 scaffold guard**；仍受 project plan gate | 已决 |
| **脚手架路径守卫** | `write_text` 等目标为 `evolve/tools/<scope>/<tool>/(main.py\|tool.toml\|README.md)` | `validation_error`；须 `write_evolve` | **已实现** BUG-018 修正：仅按路径匹配，**不**拦 `workspace/**/README.md` 等项目文件 |
| scaffold 回合 | `scaffold_tool_turn` + 脚手架**文件名** | 仍禁止 `write_text` 写 `main.py` / `tool.toml` / `README.md`（任意 workspace 路径） | 已决 |

demo probe 失败产生 `notice`、result 摘要与审计事件，供父代理修复及 checker 使用；**不**强制结束 turn，也**不**自动宣称通过。

### 3.3 资源约束

| 规则 | 已决值 | 落点 |
|------|------|------|
| tool loop 上限 | 已有 `TOOL_LOOP_MAX` / segment 预算 | `agent.py` |
| 单轮并行 tool 结果字符 | `TOOL_OUTPUT_SPILL_CHARS` 8000 | 已有 |
| 内联写入进 history | `WRITE_INLINE_MAX_CHARS` 8192 | executor |

### 3.4 子进程约束（T-1512；原 T-1412）

| 规则 | 动作 |
|------|------|
| cancel 时正在 evolved 子进程 | 外层统一 `Popen`；轮询 cancel/timeout；`terminate()` → 等 3s → `kill()` |
| 适用范围 | `run_evolved.execute_evolved_tool`，因此覆盖 `run_python` · `npm_exec` · `mvn_exec` 等 |
| CLI | 与 desktop 共用同一 executor 路径，规则一致 |
| Windows 进程树 | M0 仅终止直接子进程；`taskkill /T` 等树杀在验证后另列 P1 |

---

## 4. 场景：grow 沉淀 evolved 工具（目标剧本）

```text
1. 工人 write_evolve main.py  → 成功
2. 工人 write_evolve tool.toml → 成功 + registry reload
3. executor 自动 demo（硬事实） → exit 0/非 0 写入 result/log
4. 工人若对同 tool 再 run_python demo → executor 窄域拒调
5. 工人可修复文件；不得自动 repair 无限循环
6. segment 结束或达墙钟 / stall → turn.end 必达
```

可选：**Phase 17** checker 读 demo 输出 + 文件，出 PASS/FAIL 报告（软+读）。

---

## 5. 自 Phase 15 迁入

| 原 ID | 内容 | Phase 16 ID |
|-------|------|------------------|
| T-1410 | stall / 无事件看门狗 | T-1510 |
| T-1411 | `WRITE_INLINE_MAX_CHARS` | T-1511 |
| T-1412 | subprocess terminate on cancel | T-1512 |

[TURN-CONTROL.md](./TURN-CONTROL.md) §9 保留交叉引用，实现以本文为准。

---

## 6. M0 / M1 划分

### M0 — 先不死

| 目标 | 任务 |
|------|----------|
| stall 看门狗 | T-1510 |
| 子进程 cancel | T-1512 |
| `turn.end` 墙钟兜底 | T-1513 |
| 可取消的 `run_scaffold_demo` | T-1514 |
| LLM timeout → `finish_reason=timeout` | T-1519 |
| 桌面 cancel 看门狗文档化 | T-1517（**done** · S-05 / S-28 · T-1808-bug-02） |
| 测试 + demo | T-1518 |

### M1 — 少乱来

| 目标 | 任务 |
|------|----------|
| 内联写入硬顶 | T-1511 |
| `tool.toml` 后自动触发 demo | T-1520 |
| 沉淀 segment 工具黑名单 | T-1515 |
| evolve_log / notice 可观测 | T-1516 |

---

## 7. 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `TURN_WALL_SEC` | `900` | 单用户消息触发的完整 turn 墙钟；`0` 可关闭 |
| `STALL_WATCHDOG_SEC` | `0` | 默认关闭；启用建议 `180`；无有效进度则 timeout |
| `WRITE_INLINE_MAX_CHARS` | `8192` | 见 TURN-CONTROL §9.1 |
| `AUTO_DEMO_ON_WRITE_EVOLVE` | `1` | M1 T-1520：grow scaffold 写完 `tool.toml` 后自动 demo |
| `DESKTOP_CANCEL_WATCHDOG_MS` | `45000` | 桌面 Stop 无 `turn.end` 兜底 |

---

## 8. 非目标（本 Phase）

- 不新增第 7 个 Builtin function（约束在 executor 内）
- 不替代 checker 的「对照 npm_exec 结构」类推理
- 不做进程级双 sidecar（M0 仍同进程）
- 不在 M0 做 Windows 进程树 kill
- 不全局禁止 `run_python`，也不禁止规范 staging

---

## 9. 后续问题（不阻塞 M0/M1）

1. Windows 是否引入进程组 / Job Object，可靠终止孙进程树？
2. 是否把 `PARENT_EXECUTE_TOTAL_MAX` 调高到大于 segment max，使 T-705 默认配置真正支持多 segment？
3. project 交付是否另设专用 guard/checker，不复用 grow scaffold 规则？

---

## 10. 验收

| # | 场景 | 通过标准 |
|---|------|----------|
| 1 | `STALL_WATCHDOG_SEC=2` 且无有效进度 | `turn.end timeout`；桌面可输入 |
| 2 | Stop during `run_python` sleep 300s | 直接子进程 terminate；3s 后仍活则 kill；最终 `turn.end cancelled` |
| 3 | 同 segment 写 tool 后再 `run_python` 跑其 demo | M1：`validation_error`，不弹 confirm；project 一般脚本不误伤 |
| 4 | 手动 checker 验收完整 tool | M0：先运行 demo probe，再把结果注入 checker |
| 5 | 写 `evolve/tools/x/tool.toml` 且 reload 成功 | M1：自动 demo 结果进 result/log/notice |
| 6 | 内联 9000 字 `write_text` | M1：拒 |
| 7 | LLM 抛 `LLMTimeoutError` | `finish_reason=timeout`，不落成 generic error |

---

## 11. 变更记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-07-13 | 草案：与 checker 分线；迁入 T-1410～T-1412 |
| 0.2.0 | 2026-07-13 | 设计定稿：900s 墙钟、stall opt-in、窄域拒调、tool.toml 后自动 demo |
| 0.2.1 | 2026-07-19 | §3.1 指针：project Task 一停（Phase 20 草案 · [TASK-STOP.md](./TASK-STOP.md)）；墙钟仍为 L2 兜底 |
| 0.2.2 | 2026-08-02 | G13 空头动作门；G14 指针 → [EXEC-RELIABILITY.md](./EXEC-RELIABILITY.md) |
| 0.2.3 | 2026-08-02 | G14：剧本废止倾向 + M3 本地执行硬化指针 |
