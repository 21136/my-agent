# BUG-027：项目模式下自动 explore 误读 agent 根目录

> **状态**：**fixed**（T-4801～4803 · T-4802 · IT-4804 · **S-480 pass** 2026-08-07）  
> **严重度**：P1（**仅项目轨** · 交付审查/对账场景误导主 Agent）  
> **范围**：`project_id` 非空且 `active_shell=project`。**不**包含普通对话未绑项目时对 `docs/TOOLS.md` 的对账（见 [EXPLORE-SCOPE-RAILS.md](../EXPLORE-SCOPE-RAILS.md) S1/S2）。  
> **设计**：[AGENT-PARENT-ORCHESTRATION.md](../AGENT-PARENT-ORCHESTRATION.md) v0.4.0 · [EXPLORE-SCOPE-RAILS.md](../EXPLORE-SCOPE-RAILS.md)  
> **关联 Phase**：48 · T-4801～T-4804

---

## 1. 复现步骤（S-480 失败版）

**前置**

1. 桌面 `start-desktop.bat`，绑定项目 `huiyi`（`active_shell=project`）。  
2. `session.meta.project_id = huiyi`，建议 `delivery_profile=solo`。

**操作**

主聊发送：

```text
文档和代码可能脱节了，你看看
```

**实际（当前代码）**

| 观测点 | 预期（产品） | 实际（bug） |
|--------|--------------|-------------|
| `turn.start.intent_label` | 「先查阅再回答」或 review 路径 | 常为 **「先只读探索」** |
| 过程区 / WS | 无 explore 或应为 review | **explore** 子代理卡片 |
| 读盘路径 | `workspace/huiyi/TASKS.md` 等 | **`docs/TOOLS.md`**、`docs/MAP.md` 等 agent 根 |
| 主 Agent 后续 | 基于项目文件回答 | 可能 `list_dir workspace/huiyi/backend/...` **失败**（目录不存在） |

---

## 2. 根因链（五层）

### 2.1 分类层 — intent=research

`turn_intent.classify_turn("文档和代码可能脱节了，你看看")`：

- 非 recall（无「刚刚/刚才」时间锚）  
- 非 plan（无 tasks.md/map.md 等 artifact marker）  
- `research_hits`：「看看」→ 1  
- `execute_hits`：0  
- → **`research`**

### 2.2 触发层 — should_spawn_explore=true

`turn_intent.should_spawn_explore`（L198–211）：

1. `auto_explore_enabled()` — 默认 true（`MY_AGENT_AUTO_EXPLORE` 未设）  
2. intent ∈ `{execute, research}` ✓  
3. `「看看」` ∈ `_SPAWN_MARKERS` ✓  
→ **true**

**与 deliverable_review 无关**：review 仅父 Agent 调 builtin；本句 **未** 经过父循环选型。

### 2.3 编排层 — task=用户原话

`agent.run_turn` L1789–1794：

```python
explore_result = runner.run_explore(
    user_text,   # ← 整句用户输入，非结构化 task
    session=self.session,
    llm=self.llm,
    confirm_fn=self.executor.confirm_fn,
)
```

子代理 system/user 消息均为该字符串；**无** `scope` / `paths` / `facts` 参数。

### 2.4 执行层 — explore executor 无项目语义

`subagent.run_explore` L984–990：

```python
executor = ToolExecutor.create(
    paths=self.paths,
    session_dir=None,
    allowed_evolved=set(),
    ...
)
# 注意：无 executor.session.project_id / project_root
```

对比 `run_deliverable_review` L1370–1375 **有**：

```python
executor.session.active_shell = "project"
executor.session.project_id = project_id
executor.session.project_root = ... or project_root_rel(project_id)
```

### 2.5 路径层 — read_file 先 agent 后 workspace

`tools/builtin/read_file.py` `resolve_read_path`：

```python
agent_path = paths.resolve_under_agent(stripped, must_exist=False)
if agent_path.exists():
    return agent_path
# …
workspace_path = paths.resolve_under_workspace(stripped, must_exist=False)
```

子代理 LLM 发起 `read_file({"path": "docs/TOOLS.md"})` → 命中 `D:\my-agent\docs\TOOLS.md`，**不会**解析到 `workspace/huiyi`。

---

## 3. 日志与 WS 特征（排查用）

| 信号 | 位置 | 说明 |
|------|------|------|
| `turn.start` | WS | `intent: "research"`, `intent_label: "先只读探索"` |
| `subagent_run` | evolve_log | `kind: explore`（非 `review`） |
| overlay | session | `[子代理摘要 · explore]` |
| tool 流水 | 子代理内（不落主 messages） | `read_file` path 以 `docs/`、`agent-core/` 开头 |
| **不应出现** | — | `deliverable_review` tool_call（除非父循环另调） |

---

## 4. 不采用的修复

| 方案 | 原因 |
|------|------|
| explore 绑 `project_root` + prompt 禁读 `docs/` | 内核替主 Agent 决定 scope；用户要求「下命令」归主 Agent（见 AGENT-PARENT P4） |
| 改 `resolve_read_path` 在 project 模式优先 workspace | 全局行为变更；非 explore 场景可能破坏读内核文档 |
| 仅改 explore prompt「请读 workspace」 | 自动 spawn 时 task 仍是用户原话，约束弱 |

---

## 5. 已决修复方向（Phase 48）

见 [AGENT-PARENT-ORCHESTRATION.md](../AGENT-PARENT-ORCHESTRATION.md)：

| 任务 | 内容 |
|------|------|
| T-4801 | `project_id` + `active_shell=project` → `should_spawn_explore` 不执行 |
| T-4802 | 新增父调 `explore` builtin，`task` 必填 |
| T-4803 | project prompt：「脱节/验收」→ `deliverable_review` |
| T-4804 | S-480 手工验收 |

---

## 6. 临时绕行

| # | 做法 | 效果 |
|---|------|------|
| 1 | 环境变量 `MY_AGENT_AUTO_EXPLORE=0` | 全局禁自动 explore；父 Agent 仍可（将来）父调 explore |
| 2 | 用户明确说：`帮我 deliverable_review 验收 huiyi 文档与代码是否脱节` | 走 Phase 47 builtin |
| 3 | 避免「看看」「查一下」单独成句 | 减少误触 `_SPAWN_MARKERS` |
| 4 | 重启 sidecar 使 `project_cli` 口语放行生效 | 「项目…」口语不进 CLI（独立修复） |

---

## 7. 验收（修复后）

| ID | 步骤 | 预期 |
|----|------|------|
| **IT-4801** | 单测：`active_shell=project` + `project_id`  set + `should_spawn_explore("你看看")` | **false** |
| **IT-4801b** | 无 project 绑定 + `按 run_demo 造工具` | **true**（P3 保留） |
| **S-480** | huiyi 主聊「文档和代码脱节了，你看看」 | 无自动 explore；不读 `docs/TOOLS.md`；宜见 `deliverable_review` |

---

## 8. 相关代码索引

| 文件 | 符号 |
|------|------|
| `agent-core/turn_intent.py` | `_SPAWN_MARKERS`, `should_spawn_explore`, `auto_explore_enabled` |
| `agent-core/agent.py` | `run_turn` L1758–1796 |
| `agent-core/subagent.py` | `run_explore`, `run_deliverable_review` |
| `agent-core/tools/builtin/read_file.py` | `resolve_read_path` |
| `agent-core/tools/executor.py` | `_run_deliverable_review` |
| `agent-core/project_cli.py` | `parse_project_command`（口语放行，独立修复） |

---

## 9. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-08-06 | 初稿 |
| 2026-08-06 | v2：复现表 · 五层根因 · 日志特征 · 绕行表 · 代码索引 |
| 2026-08-07 | S-480 桌面复验 pass → **fixed** |
