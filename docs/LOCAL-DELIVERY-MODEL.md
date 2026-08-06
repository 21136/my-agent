# 本地交付模型（LOCAL-DELIVERY-MODEL）

> 版本 **0.3.3** · 2026-08-06 · **状态：设计已签 · 实现 done（T-4714～4719 · IT-476/477）**  
> **读者**：产品负责人、实现者、**外部 LLM 审查**  
> 修订链：… → v0.3.2 Fable5 → **v0.3.3 Opus5**（LDM 单一 ID · `_plan_progress_brief` · §5.6 补全）  
> 关联：[DELIVERABLE-REVIEW.md](./DELIVERABLE-REVIEW.md) · [PROJECT-MODE.md](./PROJECT-MODE.md) · [PLAN-ARCH.md](./PLAN-ARCH.md) · [AGENT-HARNESS.md](./AGENT-HARNESS.md) · [WRITE-SCOPE.md](./WRITE-SCOPE.md) · [CURSOR-ALIGN.md](./CURSOR-ALIGN.md) · [CURSOR-GAP-NEXT.md](./CURSOR-GAP-NEXT.md)

---

## 0. 一句话

**my-agent 是只活在单机上的项目开发工作台**：交付真源在 **栈-C（把关）**——**磁盘已写入 + 终端 build/test 绿**；**栈-D（纪律）** 上的 TASKS 视图与 `deliverable_review` **不**替代栈-C，**不**走云 PR；Phase 开放队列清空时 **侧栏 suggestion 提醒**验收，**不自动 spawn** 子代理。

---

## 1. 命名约定（全文强制 · 消歧）

### 1.1 两套刻度（禁止裸写 `L<n>`）

| 记号 | 名称 | 方向 | 定义出处 |
|------|------|------|----------|
| **栈-A / B / C / D** | 产品架构四层 | A 最底、D 最上 | **本文 §2** |
| **源-L0～源-L4** | 交付物真源 | 数字越大越「软」 | [DELIVERABLE-REVIEW.md](./DELIVERABLE-REVIEW.md) §6.2 |
| **LDM-1～LDM-9** | 产品决议（唯一 ID） | — | **本文 §10** |
| **S-xxx** | 手工验收项 | — | [TASKS.md](./TASKS.md) |
| **T-xxx / IT-xxx** | 实现 / 集成测试 | — | [TASKS.md](./TASKS.md) |

**为何栈层不用 L 或 S 数字**：真源 **源-L1**（测试/build）≈ 栈层 **栈-C** 的核心证据，但 **源-L3**（verdict 摘要）≠ 栈-C；若栈层也叫 L1–L4，读者必混。`S-` = 手工验收；`LDM-` = 产品决议；栈层用 **字母 A–D**。**禁止**再引入 `LD-` 前缀（v0.3.3 已废止，并入 **LDM-**）。

**跨文档**：[DELIVERABLE-REVIEW.md](./DELIVERABLE-REVIEW.md) §6.2 起已统一 **源-L***；若见历史裸写 `L0` 等，以 §6.2 为准，提 PR 消债。

### 1.2 易错对照表

| 文中写法 | 套系 | 含义 |
|----------|------|------|
| **栈-C** | 架构 | 人把关：**磁盘 + exit code** 为主；项目外/高风险写才必经 Accept |
| **源-L1** | 真源 | `run_project_tests` / `verify_build` 等可执行验收 |
| **源-L3** | 真源 | 主聊摘要 · `deliverable_review` verdict |
| **源-L4** | 真源 | PROJECT / MAP / TASKS / TASKS.archive |
| **栈-D** | 架构 | 进度板 · 里程碑提醒 · advisory review |

`deliverable_review` 审 **源-L0–源-L1** 与 **源-L4 漂移**；产品「什么叫交付完成」锚在 **栈-C**。

---

## 2. 四层栈模型（栈-A～栈-D）

```text
┌─────────────────────────────────────────────────────────────┐
│ 栈-D  纪律（项目视图 · 可选）                               │
│       TASKS 开放队列 · 侧栏 · 里程碑 suggestion               │
│       deliverable_review（advisory）· plan_partner + 采纳     │
├─────────────────────────────────────────────────────────────┤
│ 栈-C  把关（本地交付真源）                                   │
│       磁盘落盘 + 终端 build/test 绿（源-L1 证据）             │
│       项目外/敏感写：confirm 或 propose Accept                │
│       里程碑前建议 git_commit 快照                            │
├─────────────────────────────────────────────────────────────┤
│ 栈-B  Harness                                                │
│       扁工具 · glob · failure spill · write_policy · 止损     │
├─────────────────────────────────────────────────────────────┤
│ 栈-A  模型 API                                               │
└─────────────────────────────────────────────────────────────┘
```

### 2.1 栈-C 真源（修订 · Opus5）

| 组成部分 | 是否必经 | 说明 |
|----------|----------|------|
| **磁盘**（`workspace/` 下文件已写入） | **是** | 含 Phase 42 `write_policy` 免 confirm 的项目内写入 |
| **终端 exit code**（build/test） | **是** | `run_command` / `run_project_tests` 等 |
| **用户 Accept**（内联 diff） | **条件** | 项目外路径、敏感路径、非 write_policy 跳过 confirm 的写 |
| **`git_commit` 快照** | **强烈建议** | 里程碑 / 大批量改动**前**（本地无 PR 时的回退锚点） |

**LDM-3 在 solo 下**：任务归档成功 **不能**单独证明栈-C 满足；须本回合或近期有 **源-L1** 对口工具成功（Progress Gate 同旨）。

### 2.2 叠层纪律（见 §10 **LDM-2～LDM-5**）

| 主题 | 决议 |
|------|------|
| 交付真源与三问分工 | **LDM-2** · **LDM-4** |
| 栈层服从与冲突 | **LDM-3**（栈-D 服从栈-B+栈-C；冲突时栈-C 优先） |
| 里程碑与自动 review | **LDM-5** |
| 计划域写盘 | 三件套仅 `plan_partner` + 侧栏采纳（DELIVERABLE-REVIEW **R6**） |
| 子代理边界 | 不重跑栈-C 重命令；review `facts` 由父注入（**R9**） |

---

## 3. 产品定位摘要

### 3.1 非目标（已决）

| 非目标 | 替代 |
|--------|------|
| 云沙箱 + 自动 PR | 本地 `git_commit`；用户自行 push |
| 任务归档 = 交付完成 | **栈-C** test/build |
| 每 task 自动 review | 里程碑 suggestion + 口语触发 |
| Phase 勾满自动 spawn review | **LDM-5**；已废止 `SOLO_AUTO_REVIEW_ON_PHASE` 文档行 |
| 侧栏「验收」硬按钮 | `suggestions[]` |

### 3.2 本地栈-C 闭环（无 PR）

```text
（大改/里程碑前）建议 git_commit 快照
  → Agent 写盘（项目内可经 write_policy 少 confirm）
  → run_project_tests / build（源-L1）
  → 用户认定可交付；必要时口语「验收」→ deliverable_review
  → 复制 workspace/ 或依赖本机 git 回退
```

---

## 4. `deliverable_review`（可选 · advisory）

### 4.1 三档触发

| 档位 | 触发 | spawn？ |
|------|------|---------|
| A · 每 task | — | **禁止** |
| B · 里程碑 | §5 检测 | **只 suggestion，不 spawn** |
| C · 用户/父 | 口语 / `tool_calls` | **spawn** |

### 4.2 已废止：`SOLO_AUTO_REVIEW_ON_PHASE`

[DELIVERABLE-REVIEW.md](./DELIVERABLE-REVIEW.md) 旧稿 §5.4 曾列 `SOLO_AUTO_REVIEW_ON_PHASE=1`（Phase 勾满自动 review）。**代码从未实现**；与 **LDM-5 / M-R1** 冲突。

**已决（v0.3）**：自 DELIVERABLE-REVIEW **删除**该行；由本文 **里程碑 suggestion** 取代。禁止在新文档/ prompt 中复活「默认自动 spawn」。

### 4.3 与 `progress_gate` 一致

仅当 **同时**满足：`RITUAL_REVIEW_BLOCKS_PROGRESS` env **开启**（`ritual_review_blocks_progress_enabled()`）· `profile==ritual` · `verdict==fail` · `blockers>0` 才挡 `report_progress`；**solo 永不挡**（`test_solo_never_review_blocked`）。默认 env **关**——与 §4.2 废止 `SOLO_AUTO_REVIEW_ON_PHASE` 同旨：无静默自动 review 后门。

### 4.4 子代理分工（真源层）

| 子代理 | 范围 |
|--------|------|
| `deliverable_review` | **源-L0–源-L1** + **源-L4 漂移** |
| `checker` | 单点 scaffold / 单条测败 |
| `plan_partner` | **源-L4** 计划域；不判定交付 |

---

## 5. 里程碑自动提醒（栈-D · T-4714～4718）

### 5.1 PLAN-ARCH 归档语义（实现必读）

```text
report_progress → toggle_task(done=True) → archive_and_remove_task_line
  → 行从 TASKS.md 删除
  → 条目写入 TASKS.archive.md（含 phase: 字段）
```

**禁止**描述为「勾成 `[x]`」。**禁止**用 `phase_open_and_done_counts` 的 `done_n>0` 判断完成度——归档后 TASKS 内无 `[x]`，`done_n` 恒为 **0**。

**栈-D 全域**：任何基于 TASKS 开放视图的「已完成 / 夹心 / 跳段」判断均须读 **`TASKS.archive.md`**（`archive_done_count_for_phase`），**不得**用 `done_n`。里程碑（§5.3）与 Plan 进度简报（`plan_agent._plan_progress_brief` · **T-4719**）同律。

`phase_open_and_done_counts` **不宜复用**：不过滤已关闭区、子串匹配 Phase、不看 archive。须用 **§5.2** 专用快照 + archive。

### 5.2 专用辅助函数（T-4714 交付）

```python
def phase_open_count_visible(
    tasks_lines: list[str],
    phase_title: str,
    *,
    exact: bool = True,
) -> int:
    """仅 iter_tasks_lines_skipping_closed 可见行；phase 标题 **精确**匹配（非子串）。"""

def archive_done_count_for_phase(archive_path: Path, phase_title: str) -> int:
    """list_archive_entries 过滤 phase 字段 == phase_title（精确）且 reason==done。

    缺 ``reason`` 字段的旧 archive 条目视为 ``done``（与 project_mode.py 兼容）。
    """
```

### 5.3 M1 · Phase 完成（修订判据）

**在 `toggle_task` / `archive_and_remove_task_line` 成功之后**：

```python
result = archive_and_remove_task_line(...)  # 已有字段: phase, body, tasks_done, ...
phase = result["phase"]                      # _phase_for_line 求得

open_after = phase_open_count_visible(
    tasks_path.read_text().splitlines(),
    phase,
    exact=True,
)
archived_done = archive_done_count_for_phase(archive_path, phase)

# M1 当且仅当：
assert open_after == 0
assert archived_done > 0          # 排除从未有过任务的空 Phase 标题区
# phase_key 不在 reminded / dismissed（见 §5.6）
```

语义：该 **精确 Phase 标题** 下开放队列已空，且 archive 证明本 Phase **曾完成过至少一条任务**。`evaluate_milestone_after_archive` 另返回 **`remind_scope`**：`phase` · `project` · `phase_and_project`（写入 suggestion `payload`，影响 dismiss 是否连带 M2）。

### 5.4 M2 · 项目完成

`toggle` 成功后：

```python
stats = read_task_stats(tasks_path)  # 已跳过关闭区 + 含 archive done 累计
# M2：stats.total == stats.done and stats.done > 0
# 等价：开放队列空且曾有完成任务
# M2 去重键：project:complete（非 phase:N）
```

**可选加强**（M1 IT）：toggle 前 `open_before == 1` 且 `result["phase"]` 一致。

### 5.5 行为（M-R1～M-R7）

| ID | 行为 |
|----|------|
| **M-R1** | **不**自动 `deliverable_review`（无 env 后门，§4.2） |
| **M-R2** | **主载体**：`plan_agent._suggestion(kind="milestone_review")` → 持久化进 **`workspace/<id>/.plan-agent/state.json`**（`milestone_review_reminders.active_suggestions`）；**次**：`set_partner_notices` 一行摘要（≤160 字 · 易失 · 进项目清空） |
| **M-R3** | 文案：**先**建议 `git_commit` 快照 → build/test → 口语验收 |
| **M-R4** | 同 Phase 只提醒一次；`deliverable_review` pass/warn → 清空 `reminded` |
| **M-R5** | solo 默认文案；`delivery_profile==ritual` 时追加「fail 挡 report_progress」一句（`report_progress` / evolve `report_progress` 须透传 session profile） |
| **M-R6** | overlay：`milestone_review_suggested: <phase_key>` |
| **M-R7** | 无「验收」硬按钮 |

### 5.6 去重状态（plan state · 非 session）

**已决**：去重挂 **项目级** `plan_agent._save_state`，**不**挂 `session.meta`——同一项目第二条会话线不应重复提醒。

```json
// workspace/<project_id>/.plan-agent/state.json · milestone_review_reminders
{
  "milestone_review_reminders": {
    "reminded_phase_keys": ["phase:3", "project:complete"],
    "dismissed_phase_keys": ["phase:1"],
    "active_suggestions": {
      "sug-milestone_review-phase:3": {
        "kind": "milestone_review",
        "payload": {
          "phase_key": "phase:3",
          "remind_scope": "phase_and_project",
          "m1": true,
          "m2": true
        }
      }
    }
  }
}
```

**`phase_key` 规范化**：

```text
优先：phase 在 TASKS 中的序号（第 N 个 ## Phase 头，1-based）→ "phase:3"
回退：精确标题 casefold 哈希 → "title:<sha1[:12]>"
M2 哨兵：固定 "project:complete"（MILESTONE_PROJECT_COMPLETE_KEY）
```

**已知限制（v0.3.3 已决 · 接受）**：

| 场景 | 行为 |
|------|------|
| `plan_partner` **重命名** Phase 标题 | 序号不变 → **不**重复提醒（设计目标） |
| 在 TASKS **前插/删除** `##` Phase 头 | 后续 `phase:N` **平移**；历史 `reminded`/`dismissed` 可能错位 |
| 标题从 TASKS **消失**仅剩 archive | 同一 Phase 可能先有 `phase:N` 后有 `title:<hash>` → 去重可能失效 |

长期若要稳定主键，可改为 archive 侧标题哈希；当前以 **低实现成本 + 重命名友好** 为取舍。

| 事件 | 更新 |
|------|------|
| M1 触发 | `reminded_phase_keys += phase_key`；写 suggestion（`remind_scope` 见上） |
| M2 触发 | `reminded_phase_keys += project:complete`（可与 M1 同卡） |
| dismiss（侧栏 ignore） | `dismissed_phase_keys += phase_key`；若 `remind_scope` 含 project 则 **同时** `+= project:complete`（**永久**） |
| review pass/warn | 清空 `reminded_phase_keys`；移除 `active_suggestions` 中 `milestone_review` 卡 |

**实现（T-4716）**：`plan_agent._load_state` / `_save_state` · `ignore_suggestion` → `_dismiss_milestone_review_suggestion` · `clear_milestone_reminded_on_review`（`deliverable_review` pass/warn 时由 `executor` 调用）。

### 5.7 提醒文案（短版 · 适配 suggestion 卡）

```text
[里程碑] Phase 3 开放任务已归档。
建议：① git_commit 快照 ② build/test ③ 口语「验收」（只读 review，不挡写码）。
```

M2 追加：`全项目开放队列已空；收尾前建议 review + commit。`

### 5.8 挂钩点

```text
report_progress（plan_agent · evolve/tools/project/report_progress · project_api）
  → toggle_task → archive_and_remove_task_line 成功
  →（toggle 异常时静默跳过里程碑；不记 change log 的归档结果）
  → _emit_milestone_review_if_needed
       └─ 内部 evaluate_milestone_after_archive（§5.3–5.4）
       └─ if should_remind: _suggestion + _save_state
  → set_partner_notices(一行摘要)
loader.build_system_prompt（project 壳）
  → format_project_overlay(milestone_review_suggested=<phase_key>)  # M-R6 · T-4717
```

`delivery_profile`：桌面 / API 从 `session.meta.project_delivery_profile` 传入；`run_evolved` 路径由 executor 注入 `arguments.delivery_profile`。

### 5.9 实现锚点（代码 · T-4714～4717 done）

| 层 | 符号 / 文件 |
|----|-------------|
| M1/M2 检测 | `project_mode.evaluate_milestone_after_archive` · `phase_open_count_visible` · `archive_done_count_for_phase` · `phase_key_for_title` |
| 提醒发射 | `plan_agent._emit_milestone_review_if_needed` · `report_progress` · `_plan_progress_brief`（T-4719） |
| 去重状态 | `workspace/<id>/.plan-agent/state.json` → `milestone_review_reminders` |
| dismiss | `plan_agent.ignore_suggestion` → `_dismiss_milestone_review_suggestion` |
| review 清空 | `plan_agent.clear_milestone_reminded_on_review` · `executor._run_deliverable_review` |
| overlay | `project_mode.format_project_overlay` · `read_milestone_review_overlay_key` · `loader.build_system_prompt` |
| 测试 | `agent-core/tests/test_milestone_reminder.py`（IT-476 · IT-477） |

---

## 6. 与 Cursor / Claude / Codex（工作流对齐 · LDM-6）

> **LDM-6 展开**：… **每层只保留一个真源**（见 §2.2 · **LDM-2～LDM-3**）。  
> **非目标**：硬抄 **Codex Cloud** 的「云端沙箱 → 自动开 PR」；本地用 **栈-C 闭环**（§3.2）替代。

### 6.1 产品形态（先定载体）

| | **Cursor** | **Claude Code** | **Codex**（2025–2026 CLI + Cloud） | **my-agent** |
|---|------------|-----------------|-------------------------------------|--------------|
| **载体** | IDE 内嵌 Agent | 终端 / IDE / SDK | CLI 本地 + **可选**云端沙箱 | 自研桌面 · unified **project** 视角 |
| **核心循环** | Agent 工具循环 | 单一 agent loop（`query` 生成器） | Turn 循环（Responses API） | 父 Agent + 可选子代理 + Progress Gate |
| **编排厚度** | **薄**（复杂度藏在产品 harness） | **薄**（循环内 recovery） | **薄**（审批 + 沙箱产品化） | **中厚**（栈-D 状态机 · 子代理目录） |
| **扩展** | Rules · MCP · Bugbot | Skills · MCP · Hooks · 子代理 | MCP · **审批档位** | evolve 工具 · 三件套 · 配方 · `plan_partner` |
| **「做完」默认** | diff Accept + 终端绿 | 同上 + 权限闸门 | 本地：审批；Cloud：**PR + CI** | **栈-C**（§2.1）；栈-D 视图 **不**单独算交付 |

三家默认都 **不是**「TASKS 勾选 = 交付」产品；my-agent 在栈-D 提供进度板，但 **LDM-2 / LDM-3** 规定栈-C 优先。

### 6.2 栈层叠放：为何可兼得

```text
栈-D  项目纪律（my-agent 可选）  TASKS · MAP · 里程碑 suggestion · plan 采纳
栈-C  人把关真源                diff Accept · 终端 exit 0 ·（本地）git 快照
栈-B  Harness                   扁工具 · 截断 · 止损 · glob · write_policy · 权限/confirm
栈-A  模型 API
```

| 产品 | 强项（栈层） | my-agent 策略 |
|------|--------------|---------------|
| **Cursor** | 栈-B + **栈-C**（IDE Accept · 少确认 · 代码发现） | 对齐 harness + 写分层；见 §6.3.1 |
| **Claude Code** | **栈-B**（单循环 + recovery + 权限 + tool search） | 对齐 harness / 止损；子代理保持可选；见 §6.3.2 |
| **Codex** | 栈-B + 栈-C（沙箱 · **三档审批** · Cloud PR） | 学 **审批档位**；**不**抄 Cloud PR；见 §6.3.3 |
| **my-agent** | 栈-B～**栈-D** 都想覆盖 | 栈-D **服从**栈-C；solo 默认软化栈-D 硬闸 |

### 6.3 逐家：学什么 · 不学什么

#### 6.3.1 Cursor — 栈-B + 栈-C 手感

| Cursor 机制 | 映射到 my-agent | 规格 / 代码 |
|-------------|-----------------|-------------|
| 扁平原语工具（`read` / `search_replace` / `run_terminal_cmd`） | builtin proxy · 少嵌套 `run_evolved` | [AGENT-HARNESS.md](./AGENT-HARNESS.md) P1 · [TOOLS.md](./TOOLS.md) |
| 失败少刷屏（截断 · 内部消化） | failure spill · 段内失败预算 | AGENT-HARNESS P4/P5 |
| 项目内写码少弹窗 | `write_policy` · project_root 分层免 confirm | [CURSOR-GAP-NEXT.md](./CURSOR-GAP-NEXT.md) Track H · [WRITE-SCOPE.md](./WRITE-SCOPE.md) |
| build/test 在信任区少确认 | `run_command` A2（project_root 内） | [CURSOR-ALIGN.md](./CURSOR-ALIGN.md) Track A |
| 大仓找文件 | `glob_file_search`（M0）；语义索引 defer | CURSOR-GAP-NEXT Track I |
| 内联 diff / Accept | confirm 卡 · workorder `propose` · Cursor 原生编辑 | [CONFIRM-PIPELINE.md](./CONFIRM-PIPELINE.md) |
| 规划强 / 执行快 | `llm_routing`（Phase 42-J · 部分 defer） | [LLM-ROUTING.md](./LLM-ROUTING.md) |

**不学**：做第二个 IDE（LSP · 多文件 diff 编辑器 · 远程 SSH）——见 CURSOR-ALIGN §1「刻意不做」。

#### 6.3.2 Claude Code — 栈-B 工程密度

| Claude Code 机制 | 映射到 my-agent | 状态 |
|------------------|-----------------|------|
| **单 agent loop** 默认走到底 | 父 Agent 主循环；子代理 **按需** spawn，非默认 | [AGENT-PARENT-ORCHESTRATION.md](./AGENT-PARENT-ORCHESTRATION.md) |
| 循环内 **recovery**（压缩 · token 预算 · 回退） | context 压缩 · segment max（project=15） | [RUNTIME.md](./RUNTIME.md) · AGENT-HARNESS P2 |
| **权限** 在工具执行前拦截 | confirm 分层 · 危险命令永远确认 · session `a` | CONFIRM-PIPELINE · CURSOR-ALIGN A2 |
| **Tool Search** / 按需加载 schema | 工具目录 INDEX 短注入（Phase 23）· Mp/Mq/Mr | [TOOL-CATALOG.md](./TOOL-CATALOG.md) |
| Skills · MCP · Hooks | evolve skills · MCP 预留 · hooks 另 Phase | [EVOLVE.md](./EVOLVE.md) |
| `/effort` 推理档位 | `reasoning_effort`（若启用） | [REASONING-EFFORT.md](./REASONING-EFFORT.md) |

**不学**：把 Claude Code **整仓替换**进内核（[EXEC-RELIABILITY.md](./EXEC-RELIABILITY.md) 已决：借鉴思路，源码在 my-agent）。

#### 6.3.3 Codex — 栈-C 审批 · 非云 PR

**Codex Cloud**（非本地默认对标）：

```text
云端沙箱改代码 → 产出 PR → GitHub review + CI
```

前提：远程 Git · PR 界面 · 任务可上云。**与 my-agent 本地-only（LDM-1）两条产品假设。**

| Codex 机制 | 本地等价（栈-C） | 说明 |
|------------|------------------|------|
| **Suggest** 档（逐步确认） | 默认 confirm · 项目外写 | 最严 |
| **Auto-edit** 档（信任区内少问） | project_root + `write_policy` · run_command A2 | Phase 29/42 |
| **Full-auto** 档（高信任） | session `allow_approve_all` = **`a`** | 仍拦破坏性命令 |
| Cloud **PR 二审** | **`git_commit` 快照**（不 push）+ 口语 **`deliverable_review`** | advisory · 不挡写码 |
| 沙箱隔离 | `workspace/<id>/` agent root · WRITE-SCOPE deny-list | 非容器级沙箱 |

**不学**：默认自动开 PR · 绑 GitHub Actions 当交付真源。

### 6.4 本地栈-C 把关映射（三家共识落地）

完成定义（solo 默认）：

```text
workspace/ 磁盘已写入（含 write_policy 项目内写）
+ 终端 build/test exit 0（源-L1）
+（项目外/敏感写）用户 Accept 的 diff
+（强烈建议）里程碑前 git_commit 快照
+（可选）口语验收 → deliverable_review（advisory）
```

| 把关方式 | my-agent 已有 / 强化 | 对标 |
|----------|------------------------|------|
| 改文件肉眼审 | confirm · 内联 diff · `propose` | Cursor Accept |
| 终端真相 | `run_command` · `run_project_tests` · `verify_build` | 三家共通 |
| 审批档位 | suggest → 项目内免确认 → session `a` | Codex 三档（本地版） |
| 变更锚点 | `git_snapshot` / `git_commit` | 无 PR 时的回退 |
| 阶段二审 | `deliverable_review` · 里程碑 **suggestion only**（§5） | 无 PR 时的 review |
| 计划不动盘 | `plan_partner` + 侧栏采纳 | Cursor Plan → Build 纪律版 |

与 §3.2 **本地栈-C 闭环** 同义；本节强调 **对外部产品的映射关系**。

### 6.5 真正冲突：两套完成标准（消歧）

冲突 **不在**「有没有 TASKS」，而在 **同一问题被两层各判一遍且标准不一致**：

| 问题 | 栈-B/C 答案 | 栈-D 答案 | 错误表现 | 已决（LD） |
|------|-------------|-----------|----------|------------|
| 做完了吗？ | 磁盘 + exit 0 | `report_progress` 归档 | 勾满但未测 | **LDM-2** → 栈-C |
| 够格交付吗？ | 用户认定 + 可选 review | `deliverable_review` verdict | 把 verdict 当硬闸（solo） | **LDM-4** · solo 不挡 |
| 计划变了吗？ | — | 三件套须 plan 采纳 | 主聊直写 TASKS | DELIVERABLE-REVIEW **R6** |
| Phase 清空？ | — | M1 检测 + suggestion | 用 `done_n>0` 误判 | **LDM-7** · §5.3 |

**实施纪律**：新功能先声明落在哪一层；栈-D 功能 **不得**替代栈-C 跑重命令（**R9**）。

### 6.6 实现文档索引（按栈层）

| 栈层 | 主题 | 文档 |
|------|------|------|
| **栈-B** | Harness 总纲 | [AGENT-HARNESS.md](./AGENT-HARNESS.md) |
| **栈-B** | Cursor 七轨（确认 · 终端 · Git…） | [CURSOR-ALIGN.md](./CURSOR-ALIGN.md) |
| **栈-B** | 写分层 · Glob · 路由 | [CURSOR-GAP-NEXT.md](./CURSOR-GAP-NEXT.md) |
| **栈-B** | 工具目录注入 | [TOOL-CATALOG.md](./TOOL-CATALOG.md) |
| **栈-C** | 确认管线 | [CONFIRM-PIPELINE.md](./CONFIRM-PIPELINE.md) |
| **栈-C** | 写路径策略 | [WRITE-SCOPE.md](./WRITE-SCOPE.md) |
| **栈-C** | 结构化测试 | [PROJECT-VERIFY.md](./PROJECT-VERIFY.md) |
| **栈-D** | 项目模式 | [PROJECT-MODE.md](./PROJECT-MODE.md) |
| **栈-D** | 进度硬闸 | [PROGRESS-GATE.md](./PROGRESS-GATE.md) |
| **栈-D** | 父编排 · 子代理预算 | [AGENT-PARENT-ORCHESTRATION.md](./AGENT-PARENT-ORCHESTRATION.md) · [SUBAGENT-BUDGET.md](./SUBAGENT-BUDGET.md) |
| **栈-D** | 审查子代理 | [DELIVERABLE-REVIEW.md](./DELIVERABLE-REVIEW.md) |

外部评审读本节 + §2 即可判断 **LDM-6** 是否与实现路线一致；细节以各子文档为准。

---

## 7. 任务与测试

| ID | 任务 | 状态 |
|----|------|------|
| T-4714 | `phase_open_count_visible` + `archive_done_count_for_phase` + `evaluate_milestone_after_archive` | **done** |
| T-4715 | `report_progress` 挂钩 + suggestion | **done** |
| T-4716 | `state.json` `milestone_review_reminders` + `phase_key` | **done** |
| T-4717 | overlay `milestone_review_suggested` | **done** |
| T-4718 | 本文 v0.3 实现签核 | **done** |
| T-4719 | `_plan_progress_brief` 用 archive 计完成度（禁 `done_n`） | **done** |

**手工**：S-472（Phase 勾满 → notice → 口语验收 → review）仍 todo。

### IT-476

**自动化**：`agent-core/tests/test_milestone_reminder.py`（24 cases · 2026-08-06）

1. Phase A：2 条 open → 归档 1 条 → **无** suggestion  
2. 归档第 2 条 → **有** `milestone_review` suggestion；**无** `deliverable_review` 调用  
3. `archive_done_count_for_phase>0` 且 `open_after==0`  
4. 同 `phase_key` 已 `reminded` → 不重复  
5. review pass → `reminded` 清空  
6. 空 Phase（无 task 无 archive）→ **不**触发  

### IT-477

dismiss 后同 `phase_key` 不再提醒（即使用户新增 task 再归档——**v0.3 已决永久 dismiss**；若产品要「新任务后再提醒」另开 LD 决议）

---

## 8. 审查清单（v0.3.3）

| 条目 | 判定 |
|------|------|
| 决议 ID 仅 **LDM-**（无 `LD-` 混用） | **已改** v0.3.3 |
| M-R5 `delivery_profile` 已接线 | **已改** v0.3.2 |
| `_plan_progress_brief` 不用 `done_n` | **已改** v0.3.3 · T-4719 |
| `project:complete` / `remind_scope` 文档化 | **已改** v0.3.3 · §5.6 |
| 栈-A/B/C/D 与 源-L0–L4 分离 | **已改** |
| M1 不依赖 `done_n>0` | **已改** §5.3 |
| 不用 `phase_open_and_done_counts` 作里程碑 | **已改** §5.2 |
| suggestions 主 · notices 次 | **已改** M-R2 |
| 去重挂 plan state | **已改** §5.6 |
| SOLO_AUTO_REVIEW 删除 | **已改** §4.2 |
| 栈-C 含 write_policy / Accept 条件 | **已改** §2.1 |
| git_commit 在里程碑**前** | **已改** §2.1 / M-R3 |
| T-4714～4717 代码落地 | **done** §5.9 |
| IT-476 / IT-477 自动化 | **done** `test_milestone_reminder.py` |
| §6 Cursor/Claude/Codex 工作流对照 | **已改** v0.3.1 |
| M-R5 ritual 文案透传 `delivery_profile` | **已改** v0.3.2 · P1 |
| §5.2 `reason` 缺省视为 done | **已改** v0.3.2 · P2 |
| §1.1 `S-` / `LD-` 前缀归属 | **已改** v0.3.2 · P3 |

---

## 9. 变更记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-08-06 | 初版 |
| 0.2.0 | 2026-08-06 | Fable5：归档语义 · 栈-S · pre-toggle |
| 0.3.0 | 2026-08-06 | Opus5：栈-A/B/C/D · post-archive 判据 · plan state · 废止 SOLO_AUTO_REVIEW · 栈-C 定义 · **T-4714～4718 实现签核** |
| 0.3.1 | 2026-08-06 | 恢复并扩写 §6（三家工作流映射 · 本地栈-C 替代 PR · 冲突消歧 · 子文档索引） |
| 0.3.2 | 2026-08-06 | Fable5 审查：M-R5 ritual 接线 · §5.2/§1.1/§5.8 修订 |
| 0.3.3 | 2026-08-06 | Opus5：LDM 单一 ID · T-4719 `_plan_progress_brief` · §5.6 补全 · §4.3 env 点名 |

---

## 10. 决议摘要

| ID | 决议 |
|----|------|
| **LDM-1** | 本地-only；非云 PR |
| **LDM-2** | 交付真源在 **栈-C**（磁盘+exit code）；**≠ 源-L3**；「做完了吗」→ 栈-C；「开放队列空了吗」→ 栈-D 视图；「够格交付吗」→ **LDM-4** |
| **LDM-3** | **栈-D 服从栈-B+栈-C**；冲突时 **栈-C 优先**；`solo` 下 Progress Gate 认本回合对口工具成功 |
| **LDM-4** | review 可选/advisory |
| **LDM-5** | 里程碑 **suggestion only**；废止自动 spawn env |
| **LDM-6** | 与 Cursor/Claude/Codex 可兼得 |
| **LDM-7** | M1 = 归档后 `open_after==0` ∧ `archive_done(phase)>0` |
| **LDM-8** | 去重状态在 **plan `state.json`**；键 `phase_key` + M2 哨兵 **`project:complete`** |
| **LDM-9** | Accept 非栈-C 必经；write_policy 项目内写算落盘 |
