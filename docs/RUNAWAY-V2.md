# 狂奔 v2 — 外部清单 · 单 Turn · 薄 Harness · Profile

> 版本 **0.3.0** · 2026-09-09  
> 状态：**M0/M1 已编码 · controller/checklist/acceptance/state/续接核心已接入；无进展熔断、自动预算和暂停恢复已落地**  
> 取代方向：[RUNAWAY-FLOW-STATE-MACHINE.md](./RUNAWAY-FLOW-STATE-MACHINE.md) 的 checkpoint 运行时 · [RUNAWAY-VERIFICATION-ORCHESTRATOR.md](./RUNAWAY-VERIFICATION-ORCHESTRATOR.md) 的谓词门 / 短接 / bug-fix 分轨  
> 关联：[PRODUCTION-PROJECT-MVP.md](./PRODUCTION-PROJECT-MVP.md) · [PROJECT-QUALITY.md](./PROJECT-QUALITY.md) · [PROJECT-MODE.md](./PROJECT-MODE.md) · [BUG-FIX-AGENT.md](./BUG-FIX-AGENT.md) · [TASKS.md](./TASKS.md) Phase 60 · [MAP.md](./MAP.md) §2

本文是 **运行时设计与当前实现契约**，不是口号清单。当前实现与后续编码以本节为准；与 v1 冲突时，v2 路径以本文为准。

---

## 0. 问题与目标

### 0.1 我们在修什么

v1 狂奔的用户契约本来干净（准备 → 实现 → 验证 → 发布），实现却长成多层系统叠在一起：

| 层 | 干什么 | 典型副作用 |
|----|--------|------------|
| 8 态 `project_runaway_checkpoint` | 控制推进 | 新会话 checkpoint=`idle`，制品已在 verification → `idle → verifying` 非法跳转 |
| `workflow_stage` + execution_stage | 阶段门 | 与 checkpoint 双轨，overlay 要写小作文消歧 |
| `[Harness]` 短接回合 | verification 出口 0 主 Agent | 主聊已经诊断清楚，执行权却不在主 Agent |
| bug-fix 子代理 | repairing 写 ENV/PROJECT | 独立上下文、120s 超时、失败只记「又失败一次」 |
| `repair_count` / fingerprint | 熔断 | 暂停是安全阀，不是出路；「查看原因」叙述强、可操作弱 |

真实项目（`workspace/music`）上的痛点不是「模型不知道缺 ENV」，而是：

1. **诊断闭环在聊天里完成**  
2. **写文件权在另一条轨**（bootstrap / bug-fix / 阶段门）  
3. **验收真源又是第三套**（MX lint + 硬命令 + review）  
4. 任一环失败 → 整轮算失败 → 预算 -1 → 暂停 → 用户换线也清不掉项目债

这不是再加一个「之上的代理」能修的。v2 的目标是把三套收成 **一份清单 + 一个 loop + 一个司机**。

### 0.2 成功标准（可验收）

对试点项目（先 `workspace/music`）：

1. 新开会话、重启 Desktop、关再开狂奔，**阶段与清单从磁盘重算**，不出现非法 checkpoint 跳转。  
2. 验收缺 `ENV.md quality.commands` 时，**同一司机**在本轮（或定向轮）写 `ENV.md` 并跑对应命令；不 spawn `run_bug_fix`。  
3. 命令 exit ≠ 0 时，侧栏给出 **项 id + 命令 + 末 20 行**，而不是内部状态名。  
4. 同一项失败两次后进入定向轮（缩写范围）；定向仍失败则停自动、等人。  
5. 关掉 `MY_AGENT_RUNAWAY_V2` 或关掉狂奔开关，行为回到「普通项目模式」（v1 冻结路径或非狂奔路径），agent 核不被狂奔剧本污染。

### 0.3 非目标

- 不做常驻 Planner / Evaluator 三模型架构（Anthropic 2026-03 那套只借 **sprint contract = TurnPlan.acceptance**）。  
- 不做「太上皇 LLM」覆盖门禁。  
- 不把 DeepSeek Harness 全插件内核搬进 Python sidecar。  
- 不在 v2 里重做 Desktop 教科书五段流程；制品仍是四件套 + ENV/VERIFY。  
- 不把发布确认自动化。  
- 第一期不保证 Terminal 狂野模式走 v2（Desktop project 为唯一试点面）。

---

## 1. 设计原则（已决，不可拆）

四条来自对照 Cursor / Codex / Claude 长程 harness / Pi 之后的产品选择，不是装饰。

| # | 原则 | 含义 | 刻意不做 |
|---|------|------|----------|
| 1 | **一个司机** | 每轮只有主 Agent 调工具。Harness 只做：选焦点、缩 scope、跑钩子、写进度。 | bug-fix 专用子代理轨；verification 短接跳过主 loop |
| 2 | **一份清单** | 进度权威在磁盘 checklist + TASKS/VERIFY/ENV，不在 checkpoint enum。 | 8 态状态机当控制面；会话 meta 当进度真源 |
| 3 | **一轮一闭环** | `derive → TurnPlan → tool loop → acceptance hook → 更新清单`。续接仍进同一个 `run_turn`。 | `_maybe_*` 森林；`[Harness]` 0-LLM 回合 |
| 4 | **僵局可升格** | auto → directed（同司机缩 scope）→ human。暂停是「不 chain」，不是第九态。 | 全局 `repair_count=3` 糊在所有失败上 |

**薄 harness（Cursor）**：业务剧本尽量是「跑哪条命令、写哪些路径」，不要「若 verifying 且 fingerprint 且 overlay…」。  
**Profile（Pi）**：狂奔是可开关的运行剖面，不是 `agent.py` 的永久分支地狱。

### 1.1 已决产品选择（讨论时钉过的）

| 议题 | 决定 | 理由 |
|------|------|------|
| 司机 A（Harness 调一切）vs B（主 Agent + 边界钩子） | **B** | 改动面小；诊断与执行同上下文；Harness 只在 turn 边界判生死 |
| 之上的代理 | **不做** | 再一层 LLM 仍要过同一套 gate，且争叙事 |
| checklist 落盘 | **落盘** `.agent/runaway-checklist.json` | 要保留 `attempts` / `last_failure`；纯每次 derive 会丢升格状态。制品大变时 regenerate（§4.5） |
| prepare 是否全自动 | **保留自动 organize/confirm** | 与 UI-6041 语义一致；但 prepare 用同一 `run_turn`，不用独立 prep 循环叠在 verification 上 |
| 新开会话 | **清聊天，不清项目债** | 产品必须在 UI 说清；阶段从磁盘 derive |

---

## 2. 用户契约

用户在已绑定项目中 **显式开启狂奔** 后：

```text
prepare（四件套未就绪或计划未确认）
  → implement（存在开放 formal T-*）
  → verify（队列空，checklist 未全绿）
  → release_wait（checklist 全绿，等人点发布）
```

### 2.1 自动做什么

- **prepare**：整理/补齐 PROJECT、DESIGN、TASKS、VERIFY 出口（现有 `project_cli`），不写业务代码。  
- **implement**：当前 `T-*` 范围内写码、跑测试、`plan_partner` 自动采纳（仍受 UI-6014/6018 净化：只认 formal `T-*`，禁止越序勾选）。切下一项前仍要 VERIFY 证据或对口命令成功（UI-6015 语义保留，证据写入清单/VERIFY，不写 checkpoint）。  
- **verify**：按 checklist **一次一项**：允许的写范围 = 该项 `write_scope`；turn 结束跑该项 acceptance。  
- 项目内可逆工具自动放行（UI-6013）：写码、测试、构建、普通依赖、本地服务。

### 2.2 必须停下来等人

| 类别 | 例子 | UI |
|------|------|-----|
| 高风险 | 删除、push、跨项目、宿主机敏感路径 | 现有确认管线，不进自动 chain |
| 无法推断 | 需求互相矛盾、范围突变 | `mode=human`，说明缺什么 |
| 验收定向失败 | 同一 checklist 项 directed 后再失败 | 项 id + 命令 + 末 20 行 +「继续狂奔」 |
| 发布 | `RELEASE.md` / 里程碑 | 现有 `project.release.accept` |

### 2.3 界面只说人话

展示：`user_line`、清单 `passed/total`、失败摘要、下一步按钮。  
**禁止**对用户展示：`implementing`、`repairing`、`[Harness]`、tool 白名单、MX 规则内部名可作为次要标签（如「验收项 MX-5」），但主句必须是「正在补 ENV 质量命令」。

### 2.4 新开线 vs 清债（必须写进产品）

| 动作 | 清什么 | 不清什么 |
|------|--------|----------|
| 项目内新开线 | 聊天、本会话 tool 轮次 | TASKS/VERIFY/ENV、checklist `attempts`、失败输出 |
| 关狂奔 | 停止 chain | 清单状态仍在磁盘 |
| 「继续狂奔 / resume」 | 当前 failed 项 `attempts`（见 §6.3） | 不重置已 `passed` 的项 |
| 显式「重置本次验收」 | checklist 缓存（regenerate） | 不自动改业务代码 |

v1 用户用换线缓解焦虑，对验收债无效。v2 UI 在新开线完成提示里应有一句：**聊天已清空，项目验收进度仍在。**

---

## 3. 运行时结构

### 3.1 模块边界

```text
agent.run_turn
  └─ if runaway_v2_enabled(session):
         return RunawayController(session).run_turn(user_text)
     else:
         return legacy_run_turn(...)          # v1 冻结

RunawayController
  derive_phase(paths, pid, session) -> Phase
  load_or_build_checklist(...) -> Checklist
  build_turn_plan(...) -> TurnPlan
  apply_turn_plan_to_executor(plan)          # write_scope / 禁 plan_partner
  run_parent_tool_loop(...)                  # 现有 loop，不短接
  run_acceptance(spec) -> HookResult
  commit_progress(checklist, progress.md)
  should_continue_runaway(...)   # 见 RUNAWAY-V2-CONTINUATION.md
```

**已落地模块**：`agent-core/runaway_v2/`（`config.py` · `checklist.py` · `phase.py` · `plan.py` · `acceptance.py` · `continuation.py` · `controller.py` · `progress.py` · `resume.py` · `state.py`）。续接核心、项目状态指纹、自动预算与暂停恢复均由 `continuation.py` 负责，避免再把 v2 逻辑散进 `agent.py`。

**复用、不改语义的现有模块**：

- `runaway_verify_matrix.lint_verify_matrix` — 只当 lint，不当状态机  
- `project_mode.next_open_task` / formal `T-*`  
- `runaway_flow.verify_task_documented`、TASKS sanitize（越序 revert）  
- `project_quality.parse_quality_commands_from_env_text`  
- `parse_acceptance_spec`（PROJECT 验收段）  
- 租约 `runaway_lease`：v2 仍要跨进程互斥，避免两 Desktop 同时写同一项目

**禁止** v2 路径调用：`_maybe_short_circuit_runaway_verification_harness_turn`、`_enter_runaway_repair_checkpoint`、`run_bug_fix`、`transition_checkpoint`（控制面）。

### 3.2 三层状态（Cursor 拆分）

| 层 | 内容 | 生命周期 |
|----|------|----------|
| Conversation | messages.jsonl、本轮 tool 轨迹 | 新开线清空 |
| Project artifacts | TASKS/VERIFY/ENV/PROJECT、checklist json、PROGRESS.md | 跨会话 |
| Runtime | 当前 TurnPlan、lease、取消标志 | 随 turn / 进程 |

会话 `meta.runaway_v2_phase` 只是 **debug 镜像**，derive 后写入，崩溃恢复时仍以磁盘为准。

### 3.3 特性开关

| 变量 | 默认 | 含义 |
|------|------|------|
| `MY_AGENT_RUNAWAY_V2` | `0` | `1` 才走 controller |
| `MY_AGENT_RUNAWAY_V2_DIRECTED_AFTER` | `2` | 同项 `failed` 次数达到该值进入 directed（含本轮刚失败） |
| `MY_AGENT_RUNAWAY_V2_HOOK_TIMEOUT_SEC` | `180` | acceptance 命令超时（与 bug-fix 120s 脱钩） |

狂奔用户开关仍是 `project_runaway_enabled`。v2 只改变 **开启之后怎么跑**，不改变「默认关狂奔」。

---

## 4. 阶段：derive_phase

权威算法（优先级从上到下，命中即停）：

```text
if 未绑定 project_id:
    非狂奔作业（controller 直接拒绝或 no-op）

if 用户尚未 project.release.accept 且 checklist 全绿
   且 无开放 T-* :
    → release_wait

if 存在开放 formal T-*（依赖已满足的 next_open_task 或仅有被依赖阻塞）:
    if next_open_task 存在:
        → implement
    else:
        → human（依赖未满足，列出 blockers）  // 等同 v1 paused 依赖句，但不写 checkpoint

if plan 未 confirmed 或四件套未达设计出口:
    → prepare

else:
    → verify    // 队列空，清单未全绿或尚未生成
```

**冲突处理**：

- 「有开放任务」优先于「checklist 还有红项」——先做完 T-*，验收项可并行记录但 **焦点** 在任务。
- checklist 里 `kind=task` 的项（已勾 T-* 缺 VERIFY）在 **implement 阶段** 可作为当前任务的 acceptance hook，不必等到 verify 阶段才补证据（避免「任务勾了但永远不写 VERIFY」）。
- MX 项默认只在 **verify 阶段** 作为焦点；implement 阶段若 MX-5 已红，**不抢**当前 T-* 焦点（防止每轮都去改 ENV 而任务不动）。例外：用户消息明确「先补 ENV / quality.commands」→ 本轮 `focus` 可切到该 MX 项（§6.2 用户定向）。

**陈旧会话恢复**：

- 会话恢复或新一轮狂奔开始时，若 `derive_phase` 得到 `prepare`，系统先执行一次 PREPARE 文档验收。
- 若 PROJECT / DESIGN / TASKS / VERIFY 已满足设计出口，即使 TASKS 已全部完成、会话元数据仍为 `draft / preparing`，也将把计划状态提升为 `confirmed`，然后重新派生阶段。
- 该提升只由 PREPARE 验收通过触发，不读取旧的运行时阶段镜像，也不把“全任务完成”当作验收证据。文档确实缺失时仍进入正常准备工具循环。

**不使用** `idle/preparing/implementing/verifying/repairing/paused/completed` 作为控制面。v1 字段在兼容期可只读映射：

| derive 结果 | v1 镜像（可选，供旧 UI） |
|-------------|-------------------------|
| prepare | `preparing` |
| implement | `implementing` |
| verify | `verifying` |
| release_wait | `release_wait` |
| human 停 | `paused` + `runaway_blocked=true` |

映射不得反向驱动 v2。

---

## 5. Checklist

### 5.1 路径与所有权

```text
workspace/<pid>/
  RUNAWAY-PROGRESS.md              # 追加日志，人读
  .agent/runaway-checklist.json    # 机读；attempts / last_failure 真源
```

`.agent/` 已用于 plan-agent 等；checklist 不进 git 也可（编码时默认 **写入工作区、gitignore 可选**）。PROGRESS.md **建议入库**，方便人回看。

Harness **bootstrap**（v1 `ensure_env_quality_commands`）在 v2 的位置：不是独立短接回合，而是 **MX-5 项第一次成为焦点时**，hook 失败前可先跑一次幂等 bootstrap（代码，非 LLM）。bootstrap 成功则该项可直接 `passed`，不必浪费一轮模型。

### 5.2 项 schema（完整）

```json
{
  "version": 1,
  "project_id": "music",
  "generated_at": "2026-09-04T02:00:00Z",
  "source_fingerprint": "sha256:…",
  "items": [
    {
      "id": "MX-5",
      "kind": "matrix",
      "title": "ENV.md 需定义 quality.commands",
      "status": "failed",
      "stable_key": "matrix:MX-5",
      "write_scope": ["ENV.md"],
      "attempts": 2,
      "directed_used": false,
      "acceptance": {
        "type": "matrix_rule",
        "rule_id": "MX-5"
      },
      "last_run_at": "…",
      "last_failure": {
        "exit_code": 1,
        "command": "lint_verify_matrix MX-5",
        "tail": "ENV.md 未定义 quality.commands…"
      }
    }
  ]
}
```

| 字段 | 规则 |
|------|------|
| `id` | 稳定、人可见：`MX-5`、`T-003`、`AC-PROJECT` |
| `stable_key` | regenerate 时对齐旧项，用来 **保留 attempts** |
| `status` | `pending` \| `running` \| `passed` \| `failed` \| `blocked` |
| `write_scope` | glob，相对项目根；空 = 本轮禁止写（只读验收） |
| `attempts` | 仅在 acceptance **失败** 时 +1；hook 超时也 +1 |
| `directed_used` | 进入过 directed 后仍失败 → 下一档 human |
| `acceptance.type` | `command` \| `verify_doc` \| `matrix_rule` |

**谁可以把 `status` 设为 `passed`：** 只有 `run_acceptance` 返回 ok。模型勾 TASKS、口头「已完成」、`deliverable_review` **都不能**单独把 MX/AC 项标绿（审查仍可作 `kind=review` 只读项，不进发布必要条件——与 UI-5968 一致：review 去权）。

### 5.3 生成规则

`build_checklist(paths, pid) -> Checklist`

| 来源 | `id` | `kind` | acceptance | `write_scope` 默认 |
|------|------|--------|------------|-------------------|
| `lint_verify_matrix` 每条 **error** | 规则 id（同号多条时 `MX-1:T-002`） | `matrix` | `matrix_rule` | 见表 5.4 |
| PROJECT `parse_acceptance_spec` | `AC-PROJECT` | `acceptance` | `command`（argv 来自 spec） | 业务树 `src/**` 等，若命令是只读则为 `[]` |
| 每个 **已勾** formal `T-*` 且 VERIFY 未覆盖 | `T-00x` | `task` | `verify_doc` | 任务相关路径未知则项目内非 plan 域 |
| ENV 缺 quality 且 MX-5 已覆盖 | 不重复生成 | — | — | — |

**开放中的 T-*** 不生成独立 checklist 焦点项；焦点是 `next_open_task`。勾选完成后若缺 VERIFY，才出现 `kind=task` 项（或 MX-1 已覆盖同一缺口时去重：同一 `stable_key` 只留一条）。

MX-4（已勾未覆盖 VERIFY）在 strict 模式下是 error，展开为 task/matrix 项，不要两项描述同一缺口。

### 5.4 矩阵项默认写范围

| 规则 | 默认 `write_scope` |
|------|-------------------|
| MX-1 开放任务缺 VERIFY | `VERIFY.md` |
| MX-2 孤儿 V-* | `VERIFY.md` |
| MX-3 无 PROJECT 验收命令 | `PROJECT.md` |
| MX-5 无 quality.commands | `ENV.md` |
| MX-4 / 与 MX-1 合并 | `VERIFY.md` |

这直接替换 v1「verification 主 Agent 不能写 ENV、只有 bug-fix 能写」的分轨。

### 5.5 regenerate vs merge

`source_fingerprint` = hash(TASKS + VERIFY + PROJECT 验收段 + ENV quality 段)。

| 情况 | 行为 |
|------|------|
| fingerprint 未变 | 只更新 lint 现势：新 error 补项；已 passed 且规则仍绿 → 保持 passed |
| fingerprint 变了 | merge：按 `stable_key` 保留 `attempts` / `directed_used` / `last_failure`；删除源中已消失的项；新 key 从 pending 起 |
| 用户「重置本次验收」 | 丢缓存，全量重建，attempts=0 |
| 项已 passed 但 lint 又红 | **打回 pending/failed**，attempts 不自动清零（避免抖） |

### 5.6 RUNAWAY-PROGRESS.md

每轮追加一节，格式固定，便于下轮模型只读尾部（Anthropic progress file）：

```markdown
## 2026-09-04T10:18Z · verify · MX-5 · failed
命令：matrix_rule MX-5
摘要：ENV.md 未定义 quality.commands
下一焦点：MX-5 directed
```

禁止把整段聊天贴进去。

---

## 6. TurnPlan 与单 turn

### 6.1 TurnPlan 字段

```python
@dataclass(frozen=True)
class TurnPlan:
    phase: Literal["prepare", "implement", "verify", "release_wait"]
    mode: Literal["auto", "directed", "human"]
    user_line: str
    focus_item_id: str | None
    active_task_id: str | None          # implement 阶段
    allowed_write_globs: tuple[str, ...]
    forbid_plan_partner: bool
    system_append: str                  # ≤ 800 字，禁止 v1 overlay 长文
    acceptance: AcceptanceSpec | None
    max_tool_rounds: int
    chain_after_ok: bool                # 兼容字段；v2 续接单真源不读取，待移除
```

`system_append` 模板（验收定向轮示例）：

```text
本轮只处理验收项 MX-5：给 ENV.md 补齐 quality.commands。
允许修改：ENV.md。不要改 MAP.md / TASKS.archive，不要调用 plan_partner。
回合结束后系统会重新检查该项；你不必宣布项目已交付。
```

### 6.2 选焦点

```text
if mode 将被标 human: 无执行焦点，acceptance=None

prepare:
    焦点 = 文档缺口列表（现有 documentation_ready_for_design）
    acceptance = 设计出口检查（非 LLM）

implement:
    焦点 = next_open_task
    acceptance = 本任务 verify_doc 或任务行内命令（若有）
    若用户文本命中明确验收项 id / 「补 ENV」: 本轮可改 focus 到该 MX 项

verify:
    焦点 = 第一项 status in (pending, failed)，ordered by 文件中的 items 序
    若该项 directed_used 且再次失败 → 下轮 human（本轮仍可 directed 最后一次，见 6.4）

release_wait:
    不跑 tool loop（除非用户在聊天里问只读问题：intent=qa/recall 可例外，与 UI-5970 精神一致）
```

### 6.3 `run_turn` 时序（唯一入口）

```text
acquire lease
derive_phase
if human-from-last-item: emit block, return (0 LLM)
load_or_build_checklist
build_turn_plan
if plan.mode == human: emit block, return
sync executor write_scope + flags from plan
run_parent_tool_loop(max_tool_rounds)
hook = run_acceptance(plan.acceptance)     # 无 spec 则 skip
update item status / attempts / last_failure
append PROGRESS.md
save checklist json
mirror debug phase to session.meta
if should_chain: 内部再调 run_turn("[continue]") 或现有续接，但必须重入 controller
release lease on cancel/error
```

**取消**：用户点停止 → 不改已 `passed` 的项；`running` 打回 `pending` 或保持 `failed`。

**禁止**：verification 下看到 `[Harness]` 就 return 固定 TurnResult 且不跑主 loop。续接文案可以是内部 continue，但仍走 6.3。

### 6.4 升格状态机（按项，不是全局）

对焦点项 `I`：

```text
hook ok     → I.status=passed, attempts 不变, directed_used 不变
hook fail   → attempts += 1, status=failed, 记录 tail
              if attempts >= DIRECTED_AFTER and not directed_used:
                  下一 chain 的 mode=directed, 写 scope=I.write_scope
              elif directed_used and fail:
                  mode=human, 停止 chain
              elif attempts >= DIRECTED_AFTER and directed_used:
                  human
用户 resume → 仅当前 failed 项 attempts=0 且 directed_used=false
              （或 attempts=DIRECTED_AFTER-1 强制下一轮 directed，二选一：默认清零后 auto）
```

**已决 resume**：`resume: true` 将 **当前 failed 项** `attempts=0`、`directed_used=false`，下一轮 `auto`（给一次干净重试）。用户也可在 UI 选「定向再试」→ `directed_used=false` 但 `mode=directed`。按钮文案分开，避免 v1「重置修复次数」语义含糊。

### 6.5 续接（superseded → 见续接专文）

> **运行时权威**：[RUNAWAY-V2-CONTINUATION.md](./RUNAWAY-V2-CONTINUATION.md)（v0.1.0）  
> 编码任务：**T-6107** · 测试 **IT-6107-a～g** · 体验 **S-UX-028m**

**已决（2026-09-04）**：续接不再由 `TurnPlan.chain_after_ok`、`turn_intent`、分散的 `should_chain` / `should_chain_runaway_after_turn` 四套规则决定，而由磁盘派生的 **`pending_runaway_work()`** 单真源驱动。

摘要：

- **`pending_work ≠ None`** 且未升格 human 且未取消 → **`should_continue_runaway = true`**（controller 段内使用 `MY_AGENT_RUNAWAY_V2_MAX_AUTO_TURNS` 预算；跨 turn 另滤致命 `finish_reason`）。  
- **`pending_work = None`**（`release_wait` · human 升格 · 无开放 T-* 且清单全绿）→ 不续接。  
- **intent（qa/recall/requirements）不参与续接判断**；用户短句「继续」仅影响本轮 plan 解析（`execute`）。  
- **单 owner 主路径**：v2 由 controller 在同一回合内派生 `pending_work` 并续接；server 只负责锁/租约/状态广播，Desktop 2.5s watchdog 对 v2 已禁用，仅保留 v1 兼容。  
- **致命出口**：`cancelled`、`timeout`、`error` 在 controller 内持久化暂停原因并立即返回，不能被转译为 `tool_loop_exceeded` 或新的续接回合。  
- **进展守卫**：连续 `MY_AGENT_RUNAWAY_V2_NO_PROGRESS_TURNS` 个自动回合无项目状态变化，或达到 `MY_AGENT_RUNAWAY_V2_MAX_AUTO_TURNS`，立即暂停并保留可恢复原因；恢复会清零守卫计数。

`chain_after_ok` / `should_chain()` 目前仍保留在 `runaway_v2/plan.py` 作为兼容面；controller、server、agent 的 v2 续接路径已不再读取它们，统一使用 `pending_runaway_work()`。兼容面移除仍可单独收口，进展守卫已由 `continuation.py` 落地。

### 6.6 acceptance 钩子

| type | 成功条件 | 失败 tail |
|------|----------|-----------|
| `command` | exit == expect；超时算失败 | stdout/stderr 末 20 行 |
| `matrix_rule` | 该项 rule 不再出现在 lint.errors | `format_matrix_summary` 中对应句 |
| `verify_doc` | `verify_task_documented` | 缺哪些 V-* |

命令在项目根执行，走现有 `run_command` 沙箱/确认策略；狂奔下项目内测试命令自动放行。

硬验收（PROJECT 脚本）是 **独立 checklist 项** `AC-PROJECT`，不要和 MX-5 绑成一项（v1 的坑：脚本绿 ≠ ENV 绿）。

---

## 7. 权限：TurnPlan 驱动，不靠 overlay 小说

### 7.1 executor

`begin_turn` 之后：

- `allowed_write_globs` 非空 → 写工具必须匹配；plan 域文件仅当 glob 显式包含（如 `ENV.md`）  
- `forbid_plan_partner` → verify / directed-MX 为 true  
- prepare → 现有 stage_gate：禁业务代码，允许四件套  

v1 `BUG_FIX_PLAN_WRITE_ALLOWLIST` **不作为角色**；写成 checklist 的 `write_scope`。

### 7.2 与 project_mode_block_reason

v2 实现时把「verification 不能写业务代码、但能写 ENV」收成：**当前焦点 glob**。没有焦点时保持最严（只读）。

### 7.3 明确删除的 v1 机制

| 机制 | 替代 |
|------|------|
| `run_bug_fix` | 定向轮主 Agent |
| Harness 短接 0 LLM | hook + 同司机再 plan |
| `transition_checkpoint` 控制面 | derive_phase |
| `_enter_runaway_repair_checkpoint` + fingerprint 假暂停 | 按项 attempts |
| loader verification 长 overlay | `system_append` ≤ 800 字 |
| 全局 `RUNAWAY_REPAIR_BUDGET=3` | 每项 DIRECTED_AFTER |

---

## 8. Desktop / API

### 8.1 `project.state`（v2）

```json
{
  "runaway_version": 2,
  "runaway_enabled": true,
  "runaway_phase": "verify",
  "runaway_user_line": "正在补 ENV 质量命令",
  "runaway_checklist": {
    "passed": 3,
    "total": 7,
    "current_id": "MX-5",
    "current_title": "ENV.md 需定义 quality.commands",
    "failed_id": "MX-5"
  },
  "runaway_mode": "directed",
  "runaway_blocked": false,
  "runaway_block": {
    "item_id": "MX-5",
    "reason": "同一验收项定向修复后仍失败",
    "command": "lint MX-5",
    "tail": "…"
  }
}
```

兼容期可继续填 `runaway_checkpoint` 镜像（§4 表），**侧栏主路径读 v2 字段**。旧 `runaway_paused_reason` 映射到 `runaway_block.reason`。

### 8.2 「查看原因」

打开 **当前失败项** 面板（输入框上方 dock 内，已修布局），内容 = `runaway_block`，按钮：

- 关闭  
- 继续狂奔（auto 重试）  
- 定向再试  
- （可选）重置本次验收  

不要 `scrollIntoView(block: center)` 把输入框顶飞。

### 8.3 空闲状态

不要为了占位显示「就绪」单独一行把 composer 顶高；token 条与失败摘要共用 meta 行。

---

## 9. 场景走查（编码对照）

### 9.1 music：队列已空，缺 ENV quality.commands

1. derive → `verify`  
2. checklist 含 MX-5 `failed`/`pending`  
3. TurnPlan focus=MX-5，write_scope=`ENV.md`，forbid_plan_partner  
4. 主 Agent 写 ENV  
5. hook = matrix_rule MX-5；绿则 passed，chain 下一项  
6. 若模型超时未写完：attempts+1，tail=超时，再 auto/directed，**不** spawn bug-fix  

### 9.2 新开会话 + 狂奔仍开

1. 聊天空，checklist 仍在 `.agent/`  
2. derive 仍为 verify  
3. 无 `idle → verifying` 跳转  

### 9.3 实现中途开狂奔

1. derive → implement，focus=T-00x  
2. MX-5 即使红也不抢焦点  
3. 用户说「先把 ENV 补了」→ 本轮 focus MX-5  

### 9.4 依赖阻塞

`next_open_task` 空但有开放行 → human，文案列出 `depends_on`，不空转 prep。

### 9.5 发布

checklist 全绿 → release_wait，0 自动写业务；等人 `project.release.accept`。review 红不单独拦发布（UI-5968），除非我们把 review 做成可选 checklist 项且默认关闭。

---

## 10. 测试与试点

### 10.1 自动化（IT-6101～6107）

当前 v2 自动测试共 **40/40 通过**；T-6106 另有 v1 兼容回归选集 108/112 通过，剩余 4 项旧断言待收口。

| ID | 断言 |
|----|------|
| IT-6101 | 缺 ENV quality → 恰有 MX-5 项；有命令后 rebuild 该项可 passed |
| IT-6102 | v2 `run_turn` mock loop **不**调用 `run_bug_fix` / short_circuit |
| IT-6103 | hook 非 0 不得 `passed`；模型消息含「已完成」也不行 |
| IT-6104 | 同项失败 2 次 → 下一 plan.mode=directed；directed 后再失败 → human 且不 chain |
| IT-6105 | fingerprint 变 merge 时 attempts 保留 |
| IT-6106 | derive：开放 T-* 时 phase=implement，即使 MX 红 |
| IT-6107-a～g | pending work 的 prepare/implement/verify、升格、release_wait、controller/server 一致性与 cancelled 语义 |

### 10.2 手工 S-6106 / S-UX-028m

`workspace/music`：v2 on，从暂停/缺 ENV 状态「继续狂奔」已完成真实试点。会话 `20260818-cef22232` 使用 `0x567-flash` 推进至 `release_wait`：36/36 正式任务完成，矩阵与 `AC-PROJECT` 全绿，过程无 bug-fix 子代理卡片。首轮发现并修复了 MX-2 多行验证绑定误报、`[P1] T-*` 正式任务解析遗漏和质量配置阻塞；当前续接守卫还会对重复无进展和自动回合预算显式暂停。

### 10.3 回归范围

v1 测试在 `MY_AGENT_RUNAWAY_V2=0` 下必须仍绿。禁止改 v1 测试迁就 v2。

---

## 11. 迁移

### 11.1 里程碑

截至 2026-09-07：M0/M1 已编码，M2 的 checklist 状态面与续接核心已接入；v2 目标回归 48/48 通过，Desktop 完整构建已通过；S-6106 music 真实试点已完成。T-6107 兼容层/指纹收口、S-UX-028m 和 M4 仍未完成。

| 步 | 内容 | 退出标准 | 当前状态 |
|----|------|----------|----------|
| M0 | `build_checklist` + derive_phase 纯函数 | IT-6101/6106 | **done** |
| M1 | Controller 接入 `run_turn` + 开关 | 开关 0=v1，1=v2 | **done** |
| M2 | executor glob + Desktop state + continuation core | 侧栏清单数字；IT-6107-a～g | **核心落地；兼容层/指纹待收口，完整构建已通过** |
| M3 | music S-6106 / S-UX-028m | 人工签字 | **部分完成：S-6106 已进入 release_wait；S-UX-028m 多段续接手工证据仍待收口** |
| M4 | 默认开 v2 或删 v1 死代码 | 另开任务，不在 M0 做 | todo |

### 11.2 v1 冻结纪律

`MY_AGENT_RUNAWAY_V2=0`：仅 P0/安全修复。新 UI-60xx **不得**再往 v1 短接栈加钩子。

---

## 12. 文档关系

| 文档 | v2 之后 |
|------|---------|
| 本文 | 运行时权威（阶段 · 清单 · turn plan · acceptance） |
| [RUNAWAY-V2-CONTINUATION.md](./RUNAWAY-V2-CONTINUATION.md) | **续接权威**（`pending_work` · 段内/跨 turn · 指纹） |
| [RUNAWAY-FLOW-STATE-MACHINE.md](./RUNAWAY-FLOW-STATE-MACHINE.md) | v1 契约；checkpoint 节 superseded |
| [RUNAWAY-VERIFICATION-ORCHESTRATOR.md](./RUNAWAY-VERIFICATION-ORCHESTRATOR.md) | v1 谓词/短接；superseded |
| [BUG-FIX-AGENT.md](./BUG-FIX-AGENT.md) | 非狂奔/手工修 bug 仍可保留；**狂奔 repairing 专轨 superseded** |
| [RUNAWAY-STARTUP-GATES.md](./RUNAWAY-STARTUP-GATES.md) | prepare 语义并入 derive；触发方式改为 controller，不再绑 intent==requirements 的坑（UI-6041 目标保留） |
| [RUNAWAY-EXPERIENCE.md](./RUNAWAY-EXPERIENCE.md) | 历史体验；v2 试点另记 |

---

## 13. 实施任务

见 [TASKS.md](./TASKS.md) Phase 60：T-6100～T-6107、S-6106。T-6100～T-6105 已编码；v2 目标回归 48/48 通过；T-6106 的 v1 兼容回归仍在收口；T-6107 续接核心已接入，兼容层/指纹与 S-UX-028m 待做，S-6106 手工试点已完成。

---

## 14. 参考

- [Anthropic · Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) — 外部 feature list、progress、一次一项、不许自判完成  
- [Anthropic · Harness design for long-running apps](https://www.anthropic.com/engineering/harness-design-long-running-apps) — 只借「先约定 done」= TurnPlan.acceptance  
- [Cursor · Cloud agent lessons](https://cursor.com/blog/cloud-agent-lessons) — 薄 harness、会话/环境/loop 拆分  
- [OpenAI · Unrolling the Codex agent loop](https://openai.com/index/unrolling-the-codex-agent-loop/) — 单 turn 编排  
- [Pi coding agent overview](https://badlogic-pi-mono.mintlify.app/coding-agent/overview) — 狂奔是 profile  

---

## 15. 仍待收口的实现事项

下面不是原则分歧，是实现细节，默认已按括号内实施；若要改请直接改本文。

1. **gitignore checklist json？** 默认：gitignore，PROGRESS.md 可提交。  
2. **resume 清 attempts 还是强制 directed？** 默认：清零后 auto；另按钮定向再试。  
3. **review 是否进 checklist？** 默认：不进，保持 UI-5968。  
4. **Terminal 狂奔？** 默认：Phase 60 不做。  
5. **M4 何时删 v1？** 默认：music 试点通过后再开任务。
