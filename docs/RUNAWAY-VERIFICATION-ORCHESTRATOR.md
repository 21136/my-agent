# 狂奔 Verification 出口编排（Harness 谓词门）

> 版本 **0.1.0** · 2026-09-03  
> 状态：**done**（UI-6046～6052 · 2026-09-03）  
> 关联：[RUNAWAY-FLOW-STATE-MACHINE.md](./RUNAWAY-FLOW-STATE-MACHINE.md) · [RUNAWAY-STARTUP-GATES.md](./RUNAWAY-STARTUP-GATES.md) · [BUG-FIX-AGENT.md](./BUG-FIX-AGENT.md) · [AGENT-HARNESS.md](./AGENT-HARNESS.md) · [PROJECT-QUALITY.md](./PROJECT-QUALITY.md) · [TASKS.md](./TASKS.md) UI-6046～6052

---

## 0. 摘要

**问题**：狂奔在 **verification 出口**反复出现「主 Agent 知道要补 `ENV.md` / `PROJECT.md`，但计划域门禁拒绝写入」——不是模型笨，是 **权责错位**。

**结论**：verification 出口不由主 Agent 驱动，而由 **Harness 代码谓词**驱动；主 Agent 仅负责 **implementation** 写码。LLM 子代理 **bug-fix** 仅在 `repairing` 谓词成立时写入计划域配置。

**不是**：再叠一个「Harness 总代理 LLM」靠聊天切状态——状态真源仍是 **磁盘 + 脚本 + `transition_checkpoint()`**。

---

## 1. 背景与根因

### 1.1 用户可见现象

| 现象 | 常见误解 |
|------|----------|
| `verify-prototype.ps1` 已通过 | 「项目已可交付」 |
| `run_quality` 报缺 `quality.commands` | 「代码坏了」 |
| 主 Agent 尝试 `write_text` → `ENV.md` 被拒 | 「狂奔没开权限」 |
| `report_progress` 被 progress_gate 拒 | 「任务没勾完」 |
| 侧栏仍显示「实现当前任务」 | 「还在 implementation」 |

### 1.2 架构根因

```text
硬验收（PROJECT 脚本）通过  ≠  verification 出口全绿
                │
                ├─ MX 矩阵（含 ENV quality.commands）可能仍失败
                ├─ run_quality 依赖 ENV.md E11
                └─ 主 Agent 无计划域写权限（设计如此）

Harness 未及时 bootstrap / repairing  →  主 Agent 被 [Harness] 续接推着撞门禁  →  复读阻塞
```

与 [RUNAWAY-STARTUP-GATES.md](./RUNAWAY-STARTUP-GATES.md) **结尾**对称：开头是 prep 链；结尾是 **谓词门 + 配置 bootstrap + bug-fix 轨**。

---

## 2. 设计原则（借鉴 Cursor / Claude Code / CI）

| 原则 | 含义 | 产品对照 |
|------|------|----------|
| **谓词真源** | checkpoint 仅当可观测事实满足时切换 | CI gate：测试绿才 merge |
| **单写者** | 只有 `transition_checkpoint()` 改 `project_runaway_checkpoint` | Cursor：模式由 runtime 定，非模型自 declare |
| **分轨权限** | implementation → 主 Agent；计划域配置 → Harness 代码或 bug-fix | Claude：Plan vs Act、subagent 窄工具集 |
| **先验后聊** | `run_turn` 在调 LLM **之前**跑谓词与 bootstrap | 避免「说完再发现写不了」 |
| **Harness 行短接** | `[Harness]` 自动续接在 verification 出口 **默认 0 主 Agent 轮** | Cloud agent：父会话只看结果 |
| **review 去权** | `deliverable_review` 不单独进 `repairing`（已有 UI-5968） | 审查意见 ≠ 状态机 |

---

## 3. 执行体分工（Actor Model）

| Actor | 阶段 | 可写 | 不可写 |
|-------|------|------|--------|
| **Harness 代码** | 全阶段（谓词） | 调用 bootstrap、跑矩阵/硬验收、改 checkpoint | 不调用 LLM |
| **主 Agent** | requirements～implementation | 业务代码、VERIFY 证据（非计划域直写） | verification 出口：`PROJECT/ENV/TASKS` 直写 |
| **bug-fix 子代理** | `repairing` only | `BUG_FIX_PLAN_WRITE_ALLOWLIST`（含 ENV、PROJECT 验收段） | MAP、TASKS.archive |
| **用户** | `release_wait` | 发布确认 | — |

---

## 4. 谓词表（切换时机）

> **唯一真源**：下列谓词在代码中实现；prompt 只描述后果，**不得**反向驱动 checkpoint。

### 4.1 进入 `verification`

| 谓词 | 检测方式 |
|------|----------|
| `P-VER-ENTER-QUEUE` | `read_formal_task_stats(TASKS.md).all_done` |
| 动作 | `project_workflow_stage = verification`；`checkpoint → verifying`（UI-5971） |

### 4.2 出口全绿（`release_wait`）

须 **同时** 满足：

| ID | 谓词 | 检测 |
|----|------|------|
| `P-VER-MX` | 验收矩阵无 error | `lint_verify_matrix().ok`（含 **MX-5** ENV `quality.commands`） |
| `P-VER-HARD` | 硬验收通过 | `evidence_passed(run_harness_verification())` |
| `P-VER-QUEUE` | 正式队列空 | `_runaway_formal_queue_done()` |
| `P-VER-NOT-PAUSED` | 未暂停 | `project_runaway_paused_reason` 空 |

动作：`_sync_runaway_harness_truth()` → `checkpoint = release_wait`。

### 4.3 进入 `repairing`

| 谓词 | 检测 |
|------|------|
| `P-REP-MX-FAIL` | `P-VER-MX` 为 false（bootstrap 后仍失败） |
| `P-REP-HARD-FAIL` | `P-VER-HARD` 为 false |
| `P-REP-BUDGET` | `project_runaway_repair_count < RUNAWAY_REPAIR_BUDGET`（3） |
| `P-REP-FINGERPRINT` | 失败指纹 ≠ 上次（否则 `paused`） |

动作：`_enter_runaway_repair_checkpoint()` → spawn `run_bug_fix` → 复验。

**注意**：硬验收通过但 MX 失败（典型：缺 `quality.commands`）**也必须**进 repairing——不得因 acceptance 绿而提前 `return True`（UI-6046）。

### 4.4 人工门

| 谓词 | 动作 |
|------|------|
| 用户在 `release_wait` 确认发布 | `completed` |

---

## 5. 回合流水线（`run_turn` 钩子顺序）

目标顺序（**先 Harness，后主 Agent**）：

```text
1. _sync_turn_mode()
2. [verification] _bootstrap_runaway_verification_artifacts()
      ├─ ensure_project_acceptance_section()   # 已有 UI-6039
      └─ ensure_env_quality_commands()         # UI-6046：从 PROJECT 验收命令生成 ENV E11
3. _maybe_run_runaway_repair_lane_at_turn_start()
      ├─ _sync_runaway_harness_truth()         # 可能直接 release_wait
      ├─ verifying + MX fail → repairing + bug-fix
      └─ repairing 续跑 bug-fix
4. append user message · classify intent
5. _maybe_finish_runaway_verification_exit_turn()   # release_wait 0 LLM（UI-5970）
6. _maybe_short_circuit_runaway_verification_harness_turn()  # UI-6047：[Harness] + verification
7. （仅当未短接）主 Agent tool loop
```

### 5.1 Bootstrap 语义（UI-6046）

`ensure_env_quality_commands(paths, pid)`：

- 若 `ENV.md` 已有可解析 `quality.commands` → no-op  
- 否则从 `PROJECT.md` 第一条 ``命令：`…` `` 解析 argv（**含 powershell**，不限 python）  
- 写入 `ENV.md`：`id: acceptance`  
- **幂等**：每回合可安全重跑  

与 `ensure_project_acceptance_section` 对称：Harness 代码写盘，不经过主 Agent。

### 5.2 Harness 短接（UI-6047）

当同时满足：

- `project_runaway_enabled`  
- `project_workflow_stage == verification`  
- `is_runaway_harness_utterance(user_text)`（`[Harness]` 前缀）  
- checkpoint ∈ `{verifying, repairing}` 且 bootstrap+repair 后仍未 `release_wait`  

→ 返回固定 `TurnResult`（`finish_reason=runaway_verification_harness`），**不进入主 Agent tool loop**。

用户手动发自然语言（非 Harness 行）仍可进主 Agent，但 overlay 须声明禁止写计划域（§7）。

---

## 6. 与现有组件关系

| 组件 | 角色 |
|------|------|
| `runaway_verify_matrix.py` | MX-1～MX-5；MX-5 = ENV quality |
| `runaway_verification.py` | 硬验收证据 + bootstrap 函数 |
| `subagent.run_bug_fix` | repairing LLM 轨（UI-6045） |
| `progress_gate` | verification + 狂奔下 **跳过** G5 重复勾选（UI-6049） |
| `runaway_chain_user_line` | 队列空时用 verification 出口文案（UI-6050） |

---

## 7. Prompt / Overlay 策略

> **要做，但不靠大改 core.txt。** 优先级：**代码谓词 > 动态 overlay > 静态 project prompt**。

### 7.1 必须改（overlay · `format_project_overlay`）

| checkpoint | `stage_gate` / `plan_gate` 要求 |
|------------|-----------------------------------|
| `verifying` / `release_wait` | 主 Agent 禁止写 PROJECT/ENV/TASKS；出口由 Harness |
| `repairing` | 主 Agent 禁止 plan_partner；配置由 bug-fix |
| **else**（verification 但 cp 未识别） | **禁止**出现「狂奔已授权可连续写计划域」——与 executor 矛盾（UI-6048） |

### 7.2 建议改（静态 · 小句）

`evolve/.../project_prompt` 或 loader 注入片段增加一句：

```text
verification 出口：checkpoint 以 Harness 为准；缺 ENV quality 由 bootstrap/bug-fix 处理，主 Agent 勿 write_text 计划域。
```

### 7.3 不必改

- `prompts/core.txt`：已有狂奔一句；无需全文重写  
- 若 UI-6047 短接生效，Harness 续接回合 **不读** system prompt  

### 7.4 digest 去矛盾（UI-6051）

`loader.py` 在 `runaway_enabled && workflow_stage==verification` 时追加：

```text
digest_profile_note: verification — 忽略 digest 中「直写 ENV/PROJECT 验收」「report_progress 脱困」类旧叙述
```

---

## 8. 实施任务（DOC-04）

| ID | 优先级 | 内容 | 状态 |
|----|--------|------|------|
| **UI-6046** | P0 | MX-5 + `ensure_env_quality_commands` + 硬验收不因 acceptance 单独通过而跳过 MX | **done** |
| **UI-6047** | P0 | `[Harness]` verification 短接 · `_maybe_short_circuit_runaway_verification_harness_turn` | **done** |
| **UI-6048** | P1 | `format_project_overlay` verification 分支消歧（去掉 else 误导句） | **done** |
| **UI-6049** | P1 | `report_progress_repeat_block_reason` 在 runaway+verification 跳过 | **done** |
| **UI-6050** | P1 | `runaway_chain_user_line` 队列空 → verification 出口文案 | **done** |
| **UI-6051** | P2 | loader digest_note · project_prompt 一句对齐 | **done** |
| **UI-6052** | P2 | 谓词审计：`deliverable_review` / `report_progress` 不得触发 checkpoint | **done** |

### 8.1 代码锚点（计划）

| 区域 | 路径 |
|------|------|
| Bootstrap | `runaway_verification.py` · `agent._bootstrap_runaway_verification_artifacts` |
| 谓词 / 转移 | `agent._sync_runaway_harness_truth` · `_run_runaway_hard_verification` · `_enter_runaway_repair_checkpoint` |
| 回合短接 | `agent.run_turn` · `_maybe_short_circuit_runaway_verification_harness_turn` |
| 矩阵 | `runaway_verify_matrix.lint_verify_matrix`（MX-5） |
| Overlay | `project_mode.format_project_overlay` |
| 注入 | `loader.build_system_prompt` |
| 续接文案 | `agent.runaway_chain_user_line` |

---

## 9. 验收

### 9.1 自动化（IT）

| ID | 场景 | 测试 |
|----|------|------|
| IT-6046a | PROJECT 有 powershell 验收、ENV 无 quality → bootstrap 写入 | `test_runaway_verification_bootstrap` |
| IT-6046b | acceptance 绿、MX 红 → 不进 release_wait | `test_runaway_flow`（补） |
| IT-6047 | `[Harness]` + verifying → 0 tool rounds 主 Agent | `test_harness_short_circuit_verification_turn` |
| IT-6049 | verification 下 report_progress 不因 G5 拒 | `test_progress_gate`（补） |

### 9.2 手工（S）

| ID | 场景 |
|----|------|
| S-6046 | `workspace/music`（或 test）：任务全勾 → 狂奔续跑 → 无「主 Agent 写 ENV 被拒」复读；过程可见 bootstrap 或 bug-fix |
| S-6047 | release_wait 达成后 Harness 行不调用模型（与 UI-5970 一致） |

---

## 10. 非目标（本 Phase）

- 新增 Harness LLM「总代理」会话  
- 主 Agent 在 verification 获得计划域写权限  
- 废止 `bug-fix` 子代理（仍负责 MX/bootstrap 无法推断的修复）  
- Terminal 壳行为变更（Desktop project only）

---

## 11. 变更记录

| 日期 | 版本 | 说明 |
|------|------|------|
| 2026-09-03 | 0.1.0 | 成文：谓词门 · actor 分工 · run_turn 顺序 · prompt 策略 · UI-6046～6052 |
| 2026-09-03 | 0.1.1 | UI-6046～6052 编码收口 · 自动化 IT 通过 · S-6046 待手工 |
