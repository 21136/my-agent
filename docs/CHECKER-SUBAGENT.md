# Checker 子代理 / 监工（CHECKER-SUBAGENT）

> 版本 **0.2.1** · 2026-07-30  
> **状态**：**M0+M1 已实现**（T-1601,T-1610～T-1614,T-1620～T-1623）  
> 关联：[ORCHESTRATION.md](./ORCHESTRATION.md) §4 · [RUNTIME-GUARDS.md](./RUNTIME-GUARDS.md) · `subagent.py` · [TOOLS.md](./TOOLS.md) · [DESKTOP.md](./DESKTOP.md) §0  
> 动机：沉淀工具时，**同一主会话 LLM** 既写又验不可靠；需要 **独立上下文 + 另一次 DeepSeek** 做验收报告。与 **约束线**（Phase 16）互补：约束 **执法**，checker **审计**。

---

## 0. 已决摘要

| ID | 决议 |
|----|------|
| **K1** | 新增子代理种类 **`checker`**，与 `explore` 并列；checker 是内核编排角色，不新增第 7 个 Builtin |
| **K2** | checker 使用同一 sidecar 内的 **独立 messages[] + 另一次 DeepSeek API 调用**；子消息不落用户 `messages.jsonl` |
| **K3** | M0 同进程同步执行；绑定父 turn `cancel_event`。独立 Python 进程不是 M0/M1 目标 |
| **K4** | checker 只读：仅 `read_file` · `list_dir` · `grep`；不提供 web、`run_evolved` 或任何写能力 |
| **K5** | 硬事实由 Phase 16 提供：手动验收先运行内部 `run_scaffold_demo`，M1 自动验收复用已有结果；checker 只读事实并做结构/语义审计 |
| **K6** | **M0 仅手动**：`验收 <tool>` / `check <tool>`；**M1 自动**：仅 grow scaffold、`tool.toml` 成功且用户未拒绝 confirm 后触发 |
| **K7** | 输出 `PASS / FAIL / WARN` + checklist + 证据；M0 为软门，不自动修复、不自动续 segment |
| **K8** | M1 可加“完成声明门”：非 PASS 时禁止标记「已验收/沉淀完成」，但仍必须正常 `turn.end` |
| **K9** | checker 独立预算 `5` 轮，不占父 agent tool-round 预算，但计入同一 `TURN_WALL_SEC=900` |
| **K10** | 默认模型跟随 session；`CHECKER_MODEL` 可覆盖，未配置时不擅自降级模型 |

---

## 1. 动机

### 1.1 explore 不够

| | explore（T-706） | checker（本设计） |
|--|------------------|-------------------|
| 目的 | 调研、读代码、摘要 | **验收**、对照标准 |
| 时机 | 动手前 / 用户 `探索` | **write_evolve 后** / 用户 `验收` |
| 工具 | 只读 builtin | 只读 + **读 demo 产物 / evolve_log** |
| 输出 | 摘要进 overlay | **PASS/FAIL 报告** |

### 1.2 与 Cursor 外挂监工

| | Cursor 监工 | 本 Phase checker |
|--|-------------|------------------|
| 进程 | IDE 外 | sidecar 内（同进程，**异上下文**） |
| 适用 | 开发期、零改码 | 产品内、可重复、可日志 |
| 卡死时 | sidecar 挂也能验 | 同 sidecar；由 Phase 16 先保证父 turn 可收口 |

**结论**：Cursor 监工可作为 **M0 前** 的人工流程；产品内以 checker 为准。

---

## 2. 架构

```text
父代理 segment（工人）
  write_evolve main.py
  write_evolve tool.toml ──► registry reload
  （可选）再 tool_calls → Phase 16 可拒
        │
        ▼
手动验收：Phase 16 run_scaffold_demo（确定性、无 LLM）
自动验收：复用 tool.toml 写后 demo 结果
        │
        ▼
SubagentRunner.run_checker(task, context)
  · 新 messages + checker system prompt
  · 工具：read_file, list_dir, grep,（读 notice/demo 结果）
  · ≤ SUBAGENT_CHECKER_MAX（默认 5）轮
        │
        ▼
SubagentResult { kind: "checker", verdict, checklist, ... }
        │
        ▼
父会话：notice + overlay「[子代理摘要 · checker]」
  · M0 手动命令直接结束；M1 自动触发后父代理仅可文字总结或显式进入用户发起的修复轮
```

```mermaid
flowchart LR
  W[父代理 工人] --> WE[write_evolve main.py + tool.toml]
  WE --> AD[Phase 16 demo probe]
  AD --> C[checker 子代理]
  C --> R[PASS/FAIL 报告]
  R --> U[用户 / 父代理总结]
```

---

## 3. checker 任务契约

### 3.1 输入 `CheckerTask`

| 字段 | 说明 |
|------|------|
| `kind` | 固定 `evolve_tool_scaffold`（M0 仅此一种） |
| `tool_name` | 如 `mvn_exec` |
| `tool_dir` | 如 `evolve/tools/common/mvn_exec/` |
| `reference_tool` | 可选，如 `npm_exec` |
| `demo_result` | 可选；Phase 16 注入：`attempted` · `exit_code` · `stdout/stderr` 摘要 · `skipped_reason` |
| `user_checklist` | 可选；用户消息里贴的条目 |

### 3.2 输出 `CheckerResult`

| 字段 | 说明 |
|------|------|
| `verdict` | `pass` \| `fail` \| `warn` |
| `checklist` | `[{ id, status: pass|fail|warn, note, evidence }]` |
| `paths_cited` | 读过路径 |
| `tool_rounds` | 消耗轮次 |
| `summary` | 人话报告（≤ `CHECKER_SUMMARY_MAX_CHARS`） |

### 3.3 M0 默认 checklist（evolve 工具）

| # | 检查项 |
|---|--------|
| 1 | `main.py` · `tool.toml` 存在 |
| 2 | `tool.toml`：`status=active`，`topics` 含 `common`（或预期 scope） |
| 3 | registry 可加载（可引用 demo probe 的 `[PASS] registry loads`） |
| 4 | demo probe `exit_code==0`；明确 `[SKIP]` 只可降为 WARN，不伪装 PASS |
| 5 | `tool.toml` 必填段齐全、TOML/schema 可加载；关键字段不可读或明显乱码 → FAIL |
| 6 | 若提供 reference，仅核对任务要求的关键字段/行为；纯风格差异最多 WARN |

### 3.4 verdict 归并

| verdict | 条件 |
|---------|------|
| `fail` | 任一硬项失败：文件缺失、manifest/registry 无法加载、demo 非 0 且非明确 SKIP、关键字段不可读 |
| `warn` | 硬项均过，但 demo 因缺外部环境 SKIP、描述/对标存在非阻塞偏差 |
| `pass` | 所有硬项通过且无 WARN；没有执行证据时不得 PASS |

---

## 4. 工具面

### M0 允许

- builtin：`read_file` · `list_dir` · `grep`
- **不**暴露 `run_evolved` · `write_evolve` · `web_search` · `fetch_url`

Phase 16 的 demo 结果通过 `CheckerTask.demo_result` 直接注入，不让 checker 自行读整份 `evolve_log`。

---

## 5. 触发方式

| 模式 | 说明 | 倾向 |
|------|------|------|
| **M0 手动** | `验收 mvn_exec` / `check mvn_exec`；先跑 Phase 16 demo probe，再运行 checker；不进入父 agent 写循环 |
| **M1 自动** | `CHECKER_AUTO_ON_SCAFFOLD=1`；仅 grow scaffold；`tool.toml` 写成功并完成自动 demo 后 |
| **重跑** | 用户可随时手动重跑；每次都是新 ephemeral messages |

用户拒绝/超时跳过关键 `write_evolve` confirm 时，**不自动触发** checker。

---

## 6. 与 Phase 16 的配合

| 层级 | 谁做 | 例子 |
|------|------|------|
| 硬 | Phase 16 | demo exit code；拒 `run_python` |
| 软+读 | checker | 「tool.toml 描述与 npm_exec 不一致」 |
| 人 | 用户 | 点确认、看报告 |

**原则**：checker LLM 自身不执行 subprocess。手动命令由内核先跑一次 probe；自动模式复用该 scaffold turn 已有结果，避免重复执行。

---

## 7. 会话与进程

| 选项 | 说明 | M0 |
|------|------|-----|
| 同进程、异 messages | 与 explore 相同；另一次 API 调用 | **M0/M1** |
| 父 turn cancel | checker 绑定同一 `cancel_event`，Stop 后尽快退出 | **必须** |
| 独立 Python 子进程 | 最强故障隔离，但复杂度高 | **远期另案** |

`conversation_id`：**不**新建用户可见会话；子上下文 ephemeral（与 explore 一致）。

---

## 8. 父代理纪律

checker 返回 `fail` 时：

- M0：显示失败项与证据；**不**自动修改文件、**不**自动开启修复 segment，防止 checker↔worker 循环。
- M1：可由内核记录 `scaffold_check_status=failed|warn|passed`；非 `passed` 时禁止 UI/父代理标记「已验收/沉淀完成」。
- 无论 verdict 如何，checker 都必须结束并允许 `turn.end`；硬门只约束**完成声明**，不锁死会话。

---

## 9. M0 / M1 划分

### M0

| 任务 | 内容 |
|----------|------|
| T-1601 | 本文评审定稿 |
| T-1610 | `SubagentKind` + `run_checker` 骨架（抄 explore；依赖 Phase 16 `run_scaffold_demo`） |
| T-1611 | `evolve_tool_scaffold` checklist + system prompt |
| T-1612 | 手动命令 `验收` / `check` |
| T-1613 | `format_subagent_overlay` · `evolve_log` `subagent_run` kind=checker |
| T-1614 | `subagent.py demo` + 单测 |

### M1

| 任务 | 内容 |
|----------|------|
| T-1620 | write_evolve 后 **自动** spawn checker |
| T-1621 | 桌面 notice / 顶栏 **验收：通过/失败** |
| T-1622 | 与 Phase 16 自动 demo 结果注入 `CheckerTask` |
| T-1623 | 完成声明门：非 PASS 不得标「已验收/沉淀完成」 |

---

## 10. 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `SUBAGENT_CHECKER_MAX` | `5` | checker tool 轮次上限 |
| `CHECKER_SUMMARY_MAX_CHARS` | `3000` | 摘要上限 |
| `CHECKER_AUTO_ON_SCAFFOLD` | `0` | M0 关；M1 实现后可设 `1` |
| `CHECKER_MODEL` | 空=跟 session | 显式覆盖 checker 模型 |

---

## 11. 边界与后续

- checker 不消耗父 agent tool-round 预算，但消耗 turn 墙钟和 API 成本。
- checker 与 explore 不并发：explore 在动手前；checker 在手动命令或 scaffold 后。
- project「交付完成」检查是另一种 checker kind，远期单独设计；M0 仅 `evolve_tool_scaffold`。
- governance review 是周期/全量/确定性治理；checker 是会话内、单产物、LLM 语义验收。
- fail 后自动修复明确不做；修复必须由用户新指令或后续显式 worker segment 发起。

---

## 12. 验收

| # | 场景 | 通过标准 |
|---|------|----------|
| 1 | 手动 `验收 mvn_exec`，文件齐全 | 内核 demo probe exit 0；checker `verdict=pass`，摘要含 checklist |
| 2 | 故意删 `tool.toml` 一行 | `verdict=fail`，指出缺失 |
| 3 | 关键字段乱码导致不可读 | `fail`；仅描述风格偏差为 `warn` |
| 4 | 子 messages 不入 `messages.jsonl` | 磁盘检查 |
| 5 | checker 运行中点 Stop | checker 退出；父 turn 正常 `turn.end cancelled` |
| 6 | checker fail | 不自动 repair，不锁住 composer |

---

## 13. 变更记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-07-13 | 草案：checker 子代理；与 RUNTIME-GUARDS 分线 |
| 0.2.0 | 2026-07-13 | 设计定稿：M0 手动只读、独立 API、软门；M1 自动触发与完成声明门 |
