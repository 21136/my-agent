# Agent Harness 对齐（AGENT-HARNESS）

> 版本 **0.3.1** · 2026-08-04 · **状态：P1～P5 路线完成（P3 → Phase 42 J · doc done）**  
> Phase **41** · 关联：[EXEC-RELIABILITY.md](./EXEC-RELIABILITY.md) · [TOOLS.md](./TOOLS.md) · [TOOL-RETRY.md](./TOOL-RETRY.md) · [CURSOR-ALIGN.md](./CURSOR-ALIGN.md) · [LLM-ROUTING.md](./LLM-ROUTING.md) · [output-format.md](./output-format.md)

---

## 0. 一句话

**同一 LLM API ≠ 同一 agent 体验。** Cursor / Claude Code 失败更少，主要靠 harness（工具面、错误饮食、止损），不是模型权重单独决定。本文定义 my-agent 对齐路线：**从低优先级改起，文档先行。**

---

## 1. 问题（用户观察）

| 现象 | 说明 |
|------|------|
| 失败次数多 | 一轮里 `run_command` / `grep` / `read_file` 连跪，满屏 guard 分型 |
| 同一 API 对比 Cursor | Sophnet / DeepSeek 端点相同，但 Cursor 侧「看起来」失败少、推进顺 |
| 根因不在 notice | `[guard] 失败分型` 多为 UI 观测；**真正异常是 agent 盲试太久** |

### 1.1 与 Cursor 的差异（摘要）

```text
共享：chat/completions 权重
不同：system prompt · 工具 schema · 结果截断 · 失败止损 · 默认模型 · 内部重试
```

| 层 | Cursor 类（经验） | my-agent（改前） |
|----|-------------------|------------------|
| 工具面 | 扁：`read_file` · `run_terminal_cmd` · `search_replace` | 写/跑多经 `run_evolved` 嵌套 JSON |
| 失败回灌 | 强截断 / 摘要 | 成功 spill；失败 `run_command` stderr 可达 64KiB 全进历史 |
| 止损 | 较早停、内部消化部分重试 | segment max **50**；熔断仅 **同指纹×3** |
| 观测 | 用户不见每次内部试错 | 每次 tool 失败进 UI + messages |

---

## 2. 优先级与实施顺序（已定：低 → 高）

**实施顺序与业务优先级相反**——先改风险小、契约清晰的，再动止损与上下文。

| 序 | 代号 | 优先级 | 内容 | 任务 | 状态 |
|----|------|--------|------|------|------|
| 1 | **P1** | 低 | **扁平原语 proxy**（`run_command` · `write_text` · `patch_file`） | T-4101 | **done** |
| 2 | **P2** | 中 | **项目模式 segment max** 50 → 15（`project` shell） | T-4102 | **done** |
| 3 | **P3** | 中 | **规划/执行模型分拆**（强模型规划 · Flash 执行） | T-4103 | 设计保留 · 代码 defer |
| 4 | **P4** | 高 | **失败 tool 结果 LLM 截断**（与成功 spill 对称） | T-4104 | **done** |
| 5 | **P5** | 高 | **段内失败预算**（countable 失败 ≥N → 强制停段） | T-4105 | **done** |

**非目标（本 Phase）**

- 换外部 Agent 产品；完整复刻 Cursor IDE。
- 无限增加 LLM function 数量（proxy 封顶见 §3.1）。
- 取消 confirm 管线（仍走 executor；Track A 另 Phase）。

### 2.1 Harness 分层（全 Phase 共用 · 实施前必读）

P1 教训：**多数项不必改 `core.txt`**。先对号入座再写代码。

| 层 | 典型路径 | 管什么 | 何时动 |
|----|----------|--------|--------|
| **A · LLM tools schema** | `tool_proxies.py` · `agent.build_llm_tools` | 函数名与参数形状 | 仅当改变「怎么调工具」（P1） |
| **B · 内核循环** | `agent.py` · `exec_reliability.py` | 轮次上限、失败预算、停段、注入 `[内核]` 消息 | P2 · P5 |
| **C · executor / 工具实现** | `executor.py` · `evolve/tools/**/main.py` | confirm、spill、stdout 上限 | P4（优先 agent 层对称 spill） |
| **D · messages.jsonl 饮食** | `agent` 写 `role:tool` 前 | 回灌 LLM 的体积与摘要 | P4 |
| **E · 工具目录** | `evolve/tool-catalog/` · `loader.format_capability_hints` | **选哪个工具**（说明书） | 仅当暴露面/叫法变了（P1） |
| **F · core.txt** | `prompts/core.txt` | 纪律：禁假装、熔断、计划域门 | **极少改**；不重复 INDEX |
| **G · 桌面 UI** | `desktop/` | 过程块、notice 展示 | 仅观测类（如 P5 后藏 guard notice） |
| **H · 模型路由** | `llm_models.py` · `plan_agent` | 哪步用哪模型 | P3 |

**默认纪律**：B/C/D 用代码强制；E 写选工具；F 不写操作教程；停段/熔断类说明用 **`[内核]` 注入**（同 `EXEC_CIRCUIT_NUDGE_MESSAGE`），不写进 `core.txt` 长文。

### 2.2 各档设计成熟度（诚实）

| 档 | 分层落盘 | 待决项 | 可开工？ |
|----|----------|--------|----------|
| **P1** | ✅ §3 | — | **done** |
| **P2** | ✅ §4 | — | **done** |
| **P3** | ⚠️ §5 草案 | 路由表、费用、子代理边界 | **defer** |
| **P4** | ✅ §6 | — | **done** |
| **P5** | ✅ §7 | — | **done** |

---

## 3. P1 — 扁平原语 proxy（T-4101）

### 3.1 设计

在 **不新增 evolved 实现**、不破坏 confirm / 熔断 / 日志的前提下，向 LLM 额外暴露最多 **3 个** flat function：

| Proxy 名 | 路由 | 说明 |
|----------|------|------|
| `run_command` | `run_evolved` → `run_command` | 与 manifest schema 对齐；`command` 顶层必填 |
| `write_text` | `run_evolved` → `write_text` | `path` + `content` 顶层 |
| `patch_file` | `run_evolved` → `patch_file` | `path` + `replacement` + 行号/锚点 |

**纪律**

- 执行仍唯一经 `run_evolved` 内核路径（`tool_proxies.rewrite_proxy_tool_call`）。
- `run_evolved` **保留**（catalog 内其它 evolved 仍用它）。
- ask 模式：与 `run_evolved` 一同从 LLM tools 列表剔除。
- **封顶 3 个 proxy**；新增须改本文 + TOOLS.md + DOC-04。

### 3.2 落点

| 层 | 改动 | 必须？ |
|----|------|--------|
| **LLM function schema** | `tool_proxies.py` + `build_llm_tools` | **是**（P1 本体） |
| **executor 路由** | `rewrite_proxy_tool_call` | **是** |
| **工具目录** | `INDEX.md` · `buckets/run.md` · `buckets/write.md` · `loader` 能力提示 | **是**（Phase 23 说明书） |
| **`core.txt`** | 不改执行边界表 | **否**（内核纪律；选工具看 INDEX） |

| 文件 | 改动 |
|------|------|
| `agent-core/tool_proxies.py` | schema · rewrite |
| `agent-core/agent.py` | `build_llm_tools` 注入 proxy definitions |
| `agent-core/tools/executor.py` | `run()` 入口 rewrite |
| `evolve/tool-catalog/INDEX.md` · `buckets/*.md` | 扁平原语说明 |
| `agent-core/loader.py` | `format_capability_hints` |
| `agent-core/tests/test_tool_proxies.py` | IT-410 |

### 3.3 验收

| ID | 场景 | 期望 |
|----|------|------|
| IT-410a | LLM tools 含 `run_command` 且参数无 `tool_name` 嵌套 | schema 顶层 `command` |
| IT-410b | `executor.run("run_command", {command:…})` | 等价 `run_evolved` + 同名 evolved |
| IT-410c | ask 模式 | proxy 与 `run_evolved` 均不可见 |

---

## 4. P2 — 项目模式 segment max（T-4102）

### 4.1 设计

| 项 | 默认提案 |
|----|----------|
| 条件 | `session.meta.active_shell == "project"`（**后端 session 标签**；非桌面 `perspective`） |
| `parent_execute_segment_max()` | project：**15**；其它 shell：**50**（`PARENT_EXECUTE_SEGMENT_MAX` env 仍可覆盖） |
| `parent_execute_total_max()` | **不改默认**（仍 50）；project 下单 segment 已 ≤15，通常触顶先于 total |
| 目的 | 少盲试；与 [TASK-STOP.md](./TASK-STOP.md) · [MODE-BUDGET.md](./MODE-BUDGET.md) 项目线一致 |

### 4.2 落点

| 层 | 改动 | 必须？ |
|----|------|--------|
| **B · 内核循环** | `agent.parent_execute_segment_max(session)` 或读 `active_shell` | **是** |
| **D · messages** | 无（触顶仍走现有 `format_tool_loop_user_message`） | — |
| **E · 工具目录** | **否** | — |
| **F · core.txt** | **否** | — |
| **G · UI** | 可选：project 触顶文案带「项目模式轮次上限」 | 否 |

### 4.3 待决 / 风险

| # | 问题 | 默认 |
|---|------|------|
| P2-Q1 | `qa` intent 在 project 是否也 15？ | **是**（统一 segment max，不按 intent 分叉） |
| P2-Q2 | 触顶后用户说「继续」？ | 新 user 消息 → 新 segment（现有 T-705 / TASK-STOP 行为） |

### 4.4 验收

**IT-411** — `active_shell=project` 时 `parent_execute_segment_max()==15`；`grow` 仍为 50。

---

## 5. P3 — 规划/执行模型分拆（T-4103 → Phase 42 J）

**真源**：[LLM-ROUTING.md](./LLM-ROUTING.md) · [CURSOR-GAP-NEXT.md](./CURSOR-GAP-NEXT.md) Track J · TASKS **T-4201～T-4203**。

| 步 | 模型档 | 说明 |
|----|--------|------|
| 计划域 / 复杂规划 | 强推理（pro） | `plan_partner` 子代理 |
| 主聊 tool 循环 | flash | `execution_model`；用户已选 `llm_model` 时尊重 |

实现落点：`llm_routing.py` · `resolve_model_for(role)` · **不改**工具 schema / core.txt。

**状态**：文档已签（v0.1.0）；实现待 T-4202。

---

## 6. P4 — 失败结果截断（T-4104）

### 6.1 设计

| 项 | 提案 |
|----|------|
| 时机 | `agent` 在 `append_message(role=tool)` **之前** |
| 规则 | `to_json(result)` 超 `TOOL_OUTPUT_SPILL_CHARS`（默认 8k）→ 与成功相同：`preview` + `output_path` 写入 `data/sessions/.../tool_outputs/` |
| 失败专用 | `error.message` + `details` 中 stderr/logs 优先进 preview；完整 body 落盘 |
| `run_command` | **不在** `main.py` 再砍 64KiB→4KiB（避免双处不一致）；统一在 **D 层** 截断回灌 |

### 6.2 落点

| 层 | 改动 | 必须？ |
|----|------|--------|
| **C · executor** | 可复用 `maybe_spill_result` 扩到 `ok=false` | **是**（或 agent 层包装） |
| **D · messages** | 失败 tool 消息体积受控 | **是** |
| **E · 工具目录** | **否**（行为约束，不是选工具） |
| **F · core.txt** | **否** |
| **G · UI** | 过程行仍显示 `tool.end` summary；大日志靠「日志」折叠 | 已有 |

### 6.3 待决

| # | 问题 | 默认 |
|---|------|------|
| P4-Q1 | preview 是否附带 `read_file <output_path>` 提示？ | **是**（一行，与成功 spill 一致） |

### 6.4 验收

**IT-412** — 伪造大 stderr 失败；`messages.jsonl` 中 tool 内容 ≤ 阈值 + 含 `output_path`。  
**S-410** — 人工：失败后下一轮模型不再因 stderr 墙胡试。

---

## 7. P5 — 段内失败预算（T-4105）

### 7.1 设计

| 项 | 提案 |
|----|------|
| 计数 | 每 execute segment 内，`is_circuit_countable_failure(result)` 为真 **+1**（与指纹无关） |
| 阈值 | 默认 **3**（`MY_AGENT_SEGMENT_FAILURE_BUDGET`） |
| 动作 | 达阈：**不再发起下一轮 tool**；注入 `[内核]` 文案（新常量，仿 `EXEC_CIRCUIT_NUDGE`）；`turn.notice`；结束当前 segment |
| 重置 | `begin_execute_segment` / 新 user 消息（与熔断同） |
| 与 G14 | **并存**：预算=总失败次数；熔断=同招重复 |

### 7.2 落点

| 层 | 改动 | 必须？ |
|----|------|--------|
| **B · 内核循环** | `ExecutorSession.segment_failure_count` · agent loop 检查 | **是** |
| **B · 内核消息** | `exec_reliability.EXEC_SEGMENT_FAILURE_NUDGE` → `role:user` 注入 | **是**（**非** core.txt） |
| **G · UI** | `exec_failure_class` → **不再** `guard.notice` 进主聊（仅 evolve_log + 侧栏） | 建议同 PR |
| **E · 工具目录** | **否** |
| **F · core.txt** | **否**（最多一句「段内连败内核会停」— **可选省略**） |

### 7.3 待决

| # | 问题 | 默认 |
|---|------|------|
| P5-Q1 | TOOL-RETRY 的 free retry 是否计入预算？ | **否**（`retry:true` 不计） |
| P5-Q2 | 达预算后 assistant 必须文字说明？ | **是**（segment 结束，走正常收口） |

### 7.4 验收

**IT-413** — 3 次不同命令 countable 失败 → 第 4 次 tool 调用前停段；messages 含内核 nudge。

---

## 8. 与 EXEC-RELIABILITY 关系

- G14 熔断 / 分型 **保留**；P5 补「段内总失败」盲区。
- P4 减少失败输出污染导致的**次生**失败。
- P1 减少 **参数嵌套类** 失败（A 类 / schema）。
- `exec_failure_class` UI notice：建议 **P5 同 PR** 改为仅侧栏 + evolve_log（§7.2 · 非 P5 前置）。

---

## 9. DOC-04 矩阵（Phase 41）

| 面 | P1 | P2 | P3 | P4 | P5 |
|----|----|----|----|----|-----|
| LLM tools schema (A) | ✓ | — | — | — | — |
| 内核循环 (B) | ✓ | ✓ | ✓ | — | ✓ |
| executor (C) | ✓ | — | — | ✓ | — |
| messages 饮食 (D) | — | — | — | ✓ | ✓ |
| 工具目录 (E) | ✓ | — | — | — | — |
| core.txt (F) | — | — | — | — | — |
| UI (G) | — | ○ | — | ○ | ✓ |
| 模型路由 (H) | — | — | ✓ | — | — |

○ = 可选 · — = 不动

回归：**IT-410** · **IT-411** · **IT-412** · **IT-413**；**S-410**

---

## 10. 变更记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.3.0 | 2026-08-04 | P5 done：段内失败预算 + 静默失败分型 notice · IT-413 |
| 0.2.2 | 2026-08-04 | P4 done：失败 envelope spill · IT-412 |
| 0.2.1 | 2026-08-04 | P2 done：`parent_execute_segment_max(active_shell=)` project=15 · IT-411 |
| 0.2.0 | 2026-08-04 | §2.1 分层表；P2～P5 落点/待决补全；P1 明确不改 core.txt |
| 0.1.0 | 2026-08-04 | 初版；P1～P5 路线；实施顺序低→高 |
