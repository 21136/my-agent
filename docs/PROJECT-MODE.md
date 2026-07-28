# 项目模式设计（PROJECT-MODE）

> 版本 **0.2.4** · 2026-07-19  
> **状态**：**设计已决**（T-1101 done；实现见 [TASKS.md](./TASKS.md) §Phase 11 · **M3 done**）  
> 关联：[DESKTOP.md](./DESKTOP.md) §3 · [RUNTIME.md](./RUNTIME.md) · [MEMORY.md](./MEMORY.md) · [ORCHESTRATION.md](./ORCHESTRATION.md) · [TASK-STOP.md](./TASK-STOP.md)（Phase 20 草案） · `TASKS.md` §Phase 11 / 20

---

## 0. 已决摘要（2026-07-12）

| ID | 决议 |
|----|------|
| **P1** | 新增第四外壳 **`project`**，与 `grow` / `daily` / `govern` 平级 |
| **P2** | **grow** = 养 agent；**project** = 做产物（`workspace/<name>/`） |
| **P3** | 每项目强制三件套：`PROJECT.md` · `MAP.md` · `TASKS.md` |
| **P4** | 磁盘三件套 = **抗压缩真源**；未决以 `TASKS.md` 为准 |
| **P5** | `meta.json` 扩展：`active_shell` · `project_root` · `project_id` · `project_plan_status` |
| **P6** | project 壳 **硬拒绝** `write_evolve`；沉淀须显式切 grow |
| **P7** | **一会话一项目（A）**：换项目 → `新会话` 或 `项目 切换` |
| **P8** | **M0 用 7b**：仅 project 壳注入 `prompts/project.md`，**不**注册 `project` 主题 |
| **P9** | `project_plan_status`：`draft` \| `confirmed` \| `plan_dirty` |
| **P10** | **未 `confirmed` 禁止**写 `project_root` 下非三件套、禁止 `run_python` |
| **P11** | 开工前 **计划确认**：桌面 **计划确认卡**；CLI **`项目 确认`**（等价） |
| **P12** | 顶栏 `n/m` **仅**在 `confirmed` 后；否则显示「计划待确认」等（§8.2） |
| **P13** | **一次大确认**开工；改范围/验收/增删 **Phase** → `plan_dirty` + **mini-confirm** |
| **P14** | **新建项目** → 建议 **新会话**；**打开已有** → 可续接原 `conversation_id` |
| **P15** | `patch_file` **允许**，仅限 `project_root` 内 |
| **P16** | 说「写斗地主」等 → **换线提案** + confirm「新建/切换项目？」→ 再 `项目 新建`/切换；**不可自动开工**；总设计见 [CONTEXT-SWITCH.md](./CONTEXT-SWITCH.md) |
| **P17** | project 壳仍追加 **`coding` 主题**（`run_tests` / `patch_file` 等） |
| **P18** | **M0 无 TASKS 侧栏**；复用 grow 聊天 + 顶栏；M1 再上只读侧栏 |
| **P19** | 活动路由：**用户锁定外壳优先**，不覆盖手动选的壳 |
| **P20** | **Task 一停门**（草案）：`confirmed` 后每次只做一个 `TASKS.md` 条目，标 `[x]` 即停，等「继续」；详见 [TASK-STOP.md](./TASK-STOP.md) |

---

## 1. 动机

### 1.1 问题

| 现象 | 根因 |
|------|------|
| workspace 大活与养 agent 混在同一条 chat | 无 **project** 阶段 |
| 压缩后忘记进度 | 无磁盘任务真源 |
| 顶栏进度不可信 | 计划未经用户确认即动手 |
| 斗地主被路由到 grow | `execute` + coding → grow |

### 1.2 结论

**项目模式** = 外壳分界 + **三件套真源** + **计划确认门** + project prompt。

---

## 2. 四壳分工

```text
grow     → 养 agent（evolve/、proposal、write_evolve、内核）
project  → 做产物（workspace/<name>/、三件套、验收）
daily    → 日用（聊、workflow、轻 qa）
govern   → 治理（review / audit）
```

### 2.1 grow vs project

| 维度 | grow | project |
|------|------|---------|
| 主要写哪里 | `evolve/`、`agent-core/`、`docs/` | **`workspace/<project>/`** |
| 典型工具 | `write_evolve`、`patch_file`（仓内） | `write_text`、`run_python`、`patch_file`（**仅项目内**） |
| 成功标准 | registry / proposal 合并 | **`TASKS` 全 `[x]` + `PROJECT` 验收** |
| 默认禁止 | — | **`write_evolve`** |

### 2.2 daily 与 project

- daily 不展示项目 UI；续写代码提示切 **项目 · xxx**。
- 路由见 §6。

---

## 3. workspace 三件套

### 3.1 目录

```text
workspace/
  _template/          # PROJECT.md · MAP.md · TASKS.md
  <project-id>/       # 如 doudizhu
    PROJECT.md
    MAP.md
    TASKS.md
    …                   # 源码（confirmed 后才可写）
```

### 3.2 纪律

1. **无三件套不出计划** — 先 `PROJECT.md` + `TASKS.md`（`MAP.md` 可后补）。
2. **计划须用户确认（§4）** — 未 `confirmed` 不写代码、不 `run_python`。
3. **小步完成标 `[x]`** — 同轮更新 `TASKS.md`。
4. **续做 / 压缩后** — 必须先 `read_file` `TASKS.md`。
5. **交付** — 全 `[x]` + 验收通过 → 才允许「交付完成」。
6. **每 task 一停（P20 · Phase 20）** — 标完当前 `[x]` 后必须停，等用户「继续」再开下一项；见 [TASK-STOP.md](./TASK-STOP.md) v0.2.0。

### 3.3 与 digest

压缩摘要仅参考；**未决以 `TASKS.md` 为准**（digest 模板见 §7.3）。

---

## 4. 计划确认门（核心）

### 4.1 流程

```text
① 立项
   项目 新建 <id>  /  「写斗地主」→ confirm 新建
   → workspace/<id>/ + 三件套骨架
   → project_plan_status = draft
   → 顶栏：项目 · <id> · 计划待确认

② 出计划（仅文档）
   对话中让助手填 PROJECT.md + TASKS.md（Phase + 条目）— **draft 阶段允许**，intent 命中三件套时走 `plan` 回合
   → 仍 draft；禁止写 src/、禁止 run_python
   → **不要**先点「确认开工」；填完计划、你认可后再确认

③ 计划确认（用户必点）
   桌面：计划确认卡（类 tool confirm）
         摘要：目标、Phase 列表、验收标准
         [修改计划] [确认开工]
   CLI：项目 确认
   → project_plan_status = confirmed
   → 顶栏：项目 · <id> · n/m 未完成（§8.2）

④ 动手
   写代码、run_python、标 [x]

⑤ 计划变更
   增删 Phase / 改范围或验收 → 助手更新文档
   → plan_dirty → mini-confirm（同上卡，文案「计划已变更」）
   → 仅增删 task、不改 Phase：可直接改 TASKS.md，保持 confirmed
```

### 4.2 `project_plan_status`

| 值 | 含义 | 顶栏 | 写代码 |
|----|------|------|--------|
| `draft` | 计划未确认 | 计划待确认 | **禁止** |
| `confirmed` | 已确认开工 | n/m | 允许 |
| `plan_dirty` | 结构性变更待再确认 | 计划已变更 · 待确认 | **禁止**（至再确认） |

### 4.3 `meta.json` 字段

```json
{
  "active_shell": "project",
  "project_root": "workspace/doudizhu",
  "project_id": "doudizhu",
  "project_plan_status": "confirmed",
  "project_plan_confirmed_at": "2026-07-12T14:00:00Z"
}
```

### 4.4 会话策略（P7 / P14 / 壳隔离 T-1116）

**一会话一项目**（项目壳内）：每个 `workspace/<id>/` 在 `project_sessions` 中绑定至多一个 `conversation_id`；切换项目 = 切换专用会话。

**一线一壳**（桌面）：`grow` / `daily` / `project` 各维护独立会话指针；**切壳 = 换 backend 会话**，聊天区 `session.history` 替换，不混上下文。

```json
{
  "last_conversation_id": "20260712-abc",
  "last_project_id": "cli-demo-proj",
  "shell_sessions": {
    "grow": "20260710-grow01",
    "daily": "20260709-daily01"
  },
  "project_sessions": {
    "doudizhu": "20260711-8a22b88f",
    "todo-app": "20260712-def012"
  }
}
```

实现：`shell_switch.py`（壳切换）· `project_switch.py`（项目切换）。

| 操作 | 会话 |
|------|------|
| 桌面切到 **生长/日用** | 加载 `shell_sessions[grow\|daily]`；无则新建；**不**带 `project_id` |
| 桌面切到 **项目** | 加载 `project_sessions[last_project_id]`（或最近项目） |
| `项目 切换` | 同 M3；仅 project 壳内 |
| 跨壳查别项目对话 | **不**自动注入；`project_catalog` → `read_file data/sessions/<id>/messages.jsonl`（非当前会话 **confirm**） |
| 跨壳查项目代码/进度 | `read_file workspace/<id>/…`（无 confirm） |

| 桌面切换完成 | `shell.switch` → `session.banner` + `session.history`（仅活跃壳处理） |

### 4.5 `goal.md` 模板

```markdown
项目根：workspace/doudizhu
进度真源：workspace/doudizhu/TASKS.md
计划状态：见 meta.project_plan_status
```

---

## 5. 工具与权限

### 5.1 允许（`confirmed` 后）

| 工具 | 范围 |
|------|------|
| `write_text` / `append_text` | `project_root/**`；三件套在 `draft` 也可写 |
| `patch_file` | **仅 `project_root` 内** |
| `run_python` / `run_tests` | 脚本在 `project_root` |
| `read_file` / `grep` / `list_dir` | agent 根读 |

### 5.2 禁止 / 拒绝

| 动作 | 策略 |
|------|------|
| `write_evolve` | **硬拒绝** + 提示切 grow |
| 写 `evolve/`、`agent-core/` | **拒绝** |
| 非三件套写入（`draft` / `plan_dirty`） | **executor 拒绝** |
| `run_python`（`draft` / `plan_dirty`） | **拒绝** |

### 5.3 confirm

- `run_python`、一般写文件：仍逐次 confirm。
- `TASKS.md` / `MAP.md`：`workspace_only` 可 session **`a`**（减摩擦）。

### 5.4 主题

- project 壳注入 `prompts/project.md`（**7b**）。
- 同时追加 **`coding` 主题**（`run_tests`、`patch_file` 等）。

---

## 6. 活动路由

`ShellId` += `"project"`。用户 **锁定外壳** 时忽略自动 `ui.route`。

| 优先级 | 条件 | 壳 | topics |
|--------|------|-----|--------|
| 1 | govern markers | govern | — |
| 2 | pending proposals | grow | — |
| 3 | 锁定壳 | 用户所选 | — |
| 4 | `project_root` 已设 | project | coding（若无） |
| 5 | `项目` / `做项目` / `workspace/<id>/` | project | coding |
| 6 | 「写斗地主」类 → context.switch 确认后再新建/切换（[CONTEXT-SWITCH.md](./CONTEXT-SWITCH.md)） | project | coding |
| 7 | grow markers | grow | coding |
| 8 | workflow | daily | workflow |
| 9 | qa / recall / plan | daily | — |
| 10 | 默认 | 续接 `active_shell` 或 daily | — |

---

## 7. Prompt

### 7.1 `evolve/prompts/project.md`（T-1102）

- 三件套 + 计划确认门（§4）
- grow / project 边界
- 未 `confirmed` 禁止写码
- 续做必读 `TASKS.md`

### 7.2 digest 增补

```markdown
## 活跃项目
- 根：workspace/…
- 计划：confirmed | draft | plan_dirty
- 未决：read_file TASKS.md（勿猜）
```

---

## 8. 桌面壳

### 8.1 M0（已决 · P18）

| 项 | 约定 |
|----|------|
| 布局 | **复用 grow 聊天区**；无 TASKS 侧栏 |
| 顶栏 | `生长 \| 项目 \| 日用 \| 治理` + `项目 · <id>` |
| 顶栏进度 | `draft` → **计划待确认**；`plan_dirty` → **计划已变更 · 待确认**；`confirmed` → **`5/12` 未完成**（点击 popover 列未勾 task，可选实现） |
| 计划确认 | **计划确认卡**（`plan.confirm` WS，对齐 §3.2.1 tool confirm） |
| 壳目录 | `desktop/src/shells/project/`（M0 可薄包装 grow） |

### 8.2 M1

- **只读**侧栏渲染 `TASKS.md`（**不在 UI 勾选**，改文件为准）
- 侧栏顶部 **确认计划** 按钮（与 M0 卡同一协议）
- 布局：左侧栏 vs 底部抽屉 **实现时二选一**

### 8.3 M2

- **独立视觉**（蓝图色系；全窗 busy 蓝绿渐变，区别于 grow / daily）
- 侧栏 **任务 / 地图** 切换；只读渲染 `MAP.md`
- **验收**：侧栏「运行验收」或 CLI `项目 验收` / WS `project.verify` — 解析 `PROJECT.md` 中 `命令：\`python …\`` 并 `run_python`（无 tool confirm）

### 8.4 M3（T-1113 · 已实现）

| 项 | 约定 |
|----|------|
| 侧栏 **我的项目** | 挂载时 `project.list`；显示 id、`n/m 未完成`、**当前** / **可续接** / **新建会话** |
| 点击切换 | 发 `project.switch`；当前项 disabled |
| 确认卡 | 已绑其他项目且目标为 `load_session` / `new_session` 时 → `project.switch.request`；用户 **确认切换** 后带 `confirm: true` 重发 |
| 忙时 | 助手执行中（`isWorking`）禁止切换 |
| 切换后 | `project.switch.done` → 若 `session_replaced` 则推送 `session.memory`（`context.session_memory_event`）+ `session.history`（`session.session_history_event`），桌面 `session.refresh`；聊天区 **替换**（非追加）；侧栏 `project.state` 同步 `TASKS.md` |
| 新建项目 | 列表为空时提示对话 `项目 新建 <id>`（M3 不做侧栏新建按钮） |

### 8.5 WS（M1 · T-1109；M2 +T-1112；M3 +T-1113）

| type | 说明 |
|------|------|
| `plan.request` | 服务端 → 桌面：计划摘要 + `request_id` |
| `plan.response` | `confirm` \| `edit` |
| `project.list` / `project.open` / `project.state` | 项目列表与状态（含 `map_markdown` · `acceptance_command` · `session_id`） |
| `project.switch` | 桌面 → 服务端：`{ project_id, confirm?, request_id? }` |
| `project.switch.request` | 服务端 → 桌面：跨项目切换须确认（`needs_confirm` · `message` · `action`） |
| `project.switch.done` | 切换结果（`session_id` · `session_replaced` · `action`） |
| `project.verify` | 桌面 → 服务端：一键验收 |
| `project.verify.done` | 退出码 / stdout / stderr |

**M0**：无新 WS；`plan.request` 可先用 inline `confirm.request` 扩展字段。

---

## 9. CLI

| 命令 | 说明 |
|------|------|
| `项目 列表` | 列含 `TASKS.md` 的 workspace 子目录 |
| `项目 新建 <id>` | `_template` → `workspace/<id>/`；`draft`；建议接 `新会话` |
| `项目 打开 <id>` | 设 `project_root`；`active_shell=project`（当前会话须未绑其他项目） |
| `项目 切换 <id>` | 按 `project_sessions` 续接或新建专用会话；跨项目须确认（CLI 等价于桌面确认卡） |
| `项目 确认` | `draft`/`plan_dirty` → `confirmed`（等同桌面确认开工） |
| `项目 验收` | 解析 `PROJECT.md` 验收命令并 `run_python`（须 `confirmed`） |
| `项目 状态` | 计划状态 + 未勾 task 数 |

---

## 10. 内核编排

- **T-705**：`confirmed` 后 `[x]` 仍算 segment 进展；**Phase 20**：project 壳 **关闭**同 turn auto-continue，改为 task 一停（[TASK-STOP.md](./TASK-STOP.md)）。
- **交付**：软校验 `TASKS.md` 无 `- [ ]`。
- **T-1110**：executor / `run_turn` 检查 `project_plan_status`（§5.2）。

---

## 11. 实现分期

| ID | 交付 | 状态 |
|----|------|------|
| T-1101 | 本文档定稿 | **done** |
| T-1102 | `prompts/project.md` + `workspace/_template/` | **done** |
| T-1103 | `meta` + CLI `项目 …` | **done** |
| T-1104 | `activity_router` · `ShellId project` | **done** |
| T-1105 | 桌面 M0：壳 + 顶栏三态 | **done** |
| T-1106 | digest / 续做 overlay | **done** |
| T-1107 | executor：禁 write_evolve + 计划门 | **done** |
| **T-1110** | **计划确认卡 / `项目 确认` / plan_dirty** | **done** |
| T-1108 | M1 只读 TASKS 侧栏 | **done** |
| T-1109 | WS `project.*` / `plan.*` | **done** |
| T-1111 | M2 独立视觉 + MAP 预览 | **done** |
| T-1112 | M2 验收 `project.verify` | **done** |
| T-1113 | M3 项目列表 + 切换续接 | **done** |

**M0 完成标志**：新建 → 出计划 → **须确认** → 写码 + `TASKS [x]` + 顶栏 `n/m`；未确认时 `run_python` 拒绝；压缩后续做读 `TASKS.md`。

**M1 完成标志**：桌面左侧只读 `TASKS.md` + `plan.request`/`plan.response` 确认卡；`project.state` 同步侧栏。

**M2 完成标志**：蓝图 project 壳；侧栏任务/地图；`project.verify` 一键验收。

**M3 完成标志**：侧栏 **我的项目** 列表；`project.switch` 一会话一项目续接；跨项目确认卡；切换后 `session.history` 灌聊天区。

---

## 12. 非目标

内置 IDE、每项目自动 git、多项目并行 agent、替代 `新会话`、host 替代 workspace。

---

## 13. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0-draft | 2026-07-12 | 初稿 + §13 待决 |
| 0.2.0 | 2026-07-12 | **评审定稿**：P1–P19；计划确认门 §4；M0 无侧栏 |
| 0.2.1 | 2026-07-12 | **M3**：§4.4 `project_sessions` 索引；§8.4 侧栏项目列表 + `project.switch` 续接；T-1113 |
| 0.2.2 | 2026-07-14 | §8.4 切换后事件源：`session.memory` ← `context` · `session.history` ← `session`；BUG-019 |
| 0.2.3 | 2026-07-14 | §4.1 draft 出计划：三件套可在确认前由助手填写；提及 PROJECT/TASKS/MAP 走 `plan` intent |
| 0.2.4 | 2026-07-19 | **P20** 指针：[TASK-STOP.md](./TASK-STOP.md) v0.2.0；§3.2 / §10 每 task 一停 |
