# 项目模式设计（PROJECT-MODE）

> 版本 **0.5.2** · 2026-08-15  
> **状态**：**设计已决 · 实现 done**（Phase 11）；**UI** = unified project perspective；**ENV E1–E11 done**；**§0e 进度闭环 done**（Phase 21 · F1–F6）；**Phase 58b T-5810～T-5819 制品链运行时基础 done，剩余 S-581**  
> **本地交付哲学**（四层栈 · 非云 PR · 里程碑提醒）：[LOCAL-DELIVERY-MODEL.md](./LOCAL-DELIVERY-MODEL.md)  
> **Desktop 教科书流程**（产品定调）：[DESKTOP-TEXTBOOK-FLOW.md](./DESKTOP-TEXTBOOK-FLOW.md)  
> 关联：[DESKTOP.md](./DESKTOP.md) §0 · [SHELL-CONSOLIDATION.md](./SHELL-CONSOLIDATION.md) · [TASK-STOP.md](./TASK-STOP.md) · [PROJECT-SIDEBAR.md](./PROJECT-SIDEBAR.md) · [PLAN-ARCH.md](./PLAN-ARCH.md) · [DESKTOP-REAL-RD-FLOW.md](./DESKTOP-REAL-RD-FLOW.md) · `TASKS.md` Phase 11/20/**21**/37/**58b** · [BUG-021](./bugs/2026-07-30-project-progress-deadlock.md)

---

## 0b. UI 迁移说明（2026-07-30）

| 旧 | 新 |
|----|-----|
| `desktop/src/shells/project/` | `desktop/src/shells/unified/` + `project-panel.ts` |
| 顶栏切到 project 壳 | 绑定项目 → `data-perspective="project"` |
| 静态 TASKS markdown | 任务流 + Plan Agent（见 PROJECT-SIDEBAR） |

正文 **P1「第四外壳 project」** 等决议仍描述产品语义；**前端路径以本表为准**。

---

## 0c. 本机工具链 ENV.md（已决 · 2026-07-30）

| ID | 决议 |
|----|------|
| **E1** | 每项目一份 `workspace/<id>/ENV.md`（YAML：`tools` + `prefer`） |
| **E2** | 新建/打开/切换项目时 **自动脚手架**：探测本机 node/npm/pnpm/yarn/mvn/java |
| **E3** | 再次进入时 **刷新 `tools`，保留 `prefer`**（手改偏好不被覆盖） |
| **E4** | **不**每轮注入 system；`npm_exec`/`mvn_exec` **自动读** ENV 解析路径与 package_manager |
| **E5** | LLM 只需关心 `prefer`（pnpm / JDK17）；可用 `read_file` 查看/改偏好 |
| **E6** | 实现：`agent-core/project_env.py`；挂钩 `create_project` / `execute_project_switch` / `context_switch` 项目创建 |

### E11 · 质量命令（已决 · done · 2026-08-04）

> 设计：[PROJECT-QUALITY.md](./PROJECT-QUALITY.md) §3 · Phase 45 · `run_quality` evolved 工具。

| ID | 决议 | 状态 |
|----|------|------|
| **E11** | `ENV.md` 可选 `quality.commands`（`id` + `cmd` 数组 + 可选 `cwd`）；`ensure_project_env` **刷新 tools 时保留** 已有 `quality:` 块 | **done** |

示例：

```yaml
quality:
  commands:
    - id: ruff
      cmd: ["python", "-m", "ruff", "check", "."]
      cwd: backend
    - id: eslint
      cmd: ["npm", "run", "lint"]
      cwd: frontend
```

| 非目标 | 理由 |
|--------|------|
| 自动探测 ruff/eslint | 项目差异大；须手写 `quality.commands` |
| 迁移写操作封装 | 仍走 `run_command` + confirm；只读见 `db_migrate_status` |

实施锚点：`agent-core/project_quality.py` · `evolve/tools/project/run_quality/` · `project_env.py` · `tests/test_project_quality.py`。

---

## 0d. 构建工具硬约束补强（已决 · done · 2026-07-30）

> 触发：huiyi 会话「测前端」时 LLM 用错参数名 `cwd`（应为 `working_dir`），随即改用 `repl` + 手写 `npm.cmd` **绕开 ENV**，长时间 `npm install`，用户体感「约束没用」。

### 已决条款

| ID | 决议 | 状态 |
|----|------|------|
| **E7** | `npm_exec` / `mvn_exec`：接受别名 **`cwd` → `working_dir`**（两者都有时以 `working_dir` 为准） | **done** |
| **E8** | **项目模式下禁止用 `repl` 跑包管理/构建**（检测 code 中的 `npm`/`pnpm`/`yarn`/`mvn`/`gradle` 等）：executor 硬拒，错误提示改走 **`run_command`** + `working_dir`（`repl` 已 archived） | **done** |
| **E9** | `project.md` + `npm_exec` 硬拒：已有 `node_modules` 时默认拒 `install`（需 `force_install: true`）；纪律写明先 `run build`/`test` | **done** |
| **E10** | `npm_exec`/`mvn_exec` 的 tool.toml + TOOLS：主参数 `working_dir`；文档说明 `cwd` 仅为别名 | **done** |

### 非目标（本轮不做）

| 非目标 | 理由 |
|--------|------|
| 禁止一切 `npm install` | 缺依赖时仍需要；只约束「有依赖还硬装」与「用 repl 装」 |
| 每轮注入整份 ENV.md 进 system | 仍按 E4；路径由工具吃 |
| 自动选国内镜像 | 可选后续；本次不强制 |

### 验收（实施后）

| 场景 | 预期 |
|------|------|
| `npm_exec` 传 `cwd` 不传 `working_dir` | 等价于 `working_dir`，或明确错误「请用 working_dir」 |
| 项目会话 `repl` 里 `subprocess…npm install` | `ok:false`，提示改用 **`run_command`**（`repl` 已 archived） |
| `workspace/<id>/frontend/node_modules` 已存在仍先 install | `npm_exec` `ok:false`，提示改 `run build` 或 `force_install` |
| 正确 `npm_exec` + `working_dir` | 继续读 ENV.md 选二进制（E4） |

### 实施锚点

| 层 | 文件 |
|----|------|
| evolved | `agent-core/project_npm_guard.py` · `run_command` · E8 `executor.py` |
| 硬门 | `agent-core/tools/executor.py` → `_validate_project_repl_build_bypass` |
| 提示 | `evolve/prompts/project.md` |
| 文档 | 本 § · [TOOLS.md](./TOOLS.md) §8.2 · [UX-POLISH.md](./UX-POLISH.md) 记录 |
| 测试 | `agent-core/tests/test_project_build_guards.py` |

---

## 0e. 项目进度闭环补强（已决 · done · 2026-07-31）

> **后续**：勾选证据硬闸（本回合对口成功才可 [x]）见 [PROGRESS-GATE.md](./PROGRESS-GATE.md) · Phase 24（**v0.3.0**：口语 write + `[evidence:…]` 标签）。

> 触发：huiyi（`20260730-27fd72d2`）助手完成工作后称「`report_progress` 不在清单」；用户质疑「工具缺失？」。  
> 证据：2026-07-30 隔离环境门禁模拟（见 [BUG-021](./bugs/2026-07-30-project-progress-deadlock.md)）。  
> 任务跟踪：`TASKS.md` **Phase 21**（T-2102～T-2107 **done**）。

### 死结（产品 · 已修）

```text
实现 → report_progress 在清单（F1）→ 注入 project_id（F4）→ 勾选 TASKS
     → 武装一停（F3）→ 同 turn 禁下一产物 → 用户「继续」
```

### 已决条款

| ID | 决议 | 状态 |
|----|------|------|
| **F1** | **不**新增索引主题 `project`（维持 **P8**）。当 `active_shell=="project"`（或已绑定 `project_root`）时，`session_evolved_allowlist` **额外并入** `scope=="project"` 且 `status=active` 的 evolved 工具（当前即 `report_progress`） | **done** |
| **F2** | 删除 `agent.run_turn` 在 `project_plan_gate_open` 时把 `active_shell` 强制改为 `"grow"` 的逻辑；draft 轮次保持 **`project`** | **done** |
| **F3** | `report_progress` **成功**后武装 task-stop | **done** |
| **F4** | executor 对 `report_progress`：缺 `project_id` 时从会话注入；tool.toml 旁注 | **done** |
| **F5** | `format_project_overlay`（confirmed）文案对齐硬门 | **done** |
| **F6** | WS `PlanAgent.report_progress`：有 `task_line` 时调用 `toggle_task` | **done** |

### 非目标（本轮不做）

| 非目标 | 理由 |
|--------|------|
| 注册 `_index` 主题 `id=project` | 与 P8 冲突；F1 用壳态并入即可 |
| 放开 confirmed 后直写 TASKS.md | 进度真源仍归 Plan Agent / report_progress |
| 自动「造」report_progress 工具 | 工具已存在；问题是清单，不是磁盘 |
| 改 P17（取消 coding 主题） | coding 工具仍需；F1 叠加 project scope |

### 影响矩阵（DOC-04）

| 面 | 影响 |
|----|------|
| 清单 / loader | `session_evolved_allowlist`（或 registry 调用方）按壳并入 project scope |
| 回合入口 | `agent.run_turn` draft 不再改 shell→grow |
| 执行门 | task-stop 武装路径；`report_progress` 参数注入 |
| 提示 | `format_project_overlay` ·（可选）`project.md` 一句对齐 |
| Plan API | `plan_agent.report_progress` 行为或文档 |
| 桌面 | 无强制 UI 改动；侧栏靠 after_turn 读盘刷新即可 |
| grow/daily | **不变**（仅 project 壳/绑定会话） |

### 回归 ID（实施时必跑）

| ID | 场景 |
|----|------|
| **S-60** | project 会话清单含 `report_progress`；grow+coding **不含**（除非另有 project 绑定） |
| **S-61** | draft 多轮聊天后 `active_shell` 仍为 `project`；`项目 确认` / `plan.response` 成功 |
| **S-62** | confirmed 后 `report_progress` 勾选 → TASKS `[x]` ↑ → 同 turn 再写产物被拒（一停）→「继续」可做下一项 |
| **IT-60** | `session_evolved_allowlist` / validate：project 壳可见 `report_progress` |
| **IT-61** | 缺 `project_id` 时注入后 `report_progress` 可跑通（fixture） |
| **IT-62** | `project_plan_gate_open` 轮次不把 shell 写成 grow（单测） |

### 验收

| 场景 | 预期 |
|------|------|
| 绑 project + topics=coding | 清单含 `report_progress` |
| draft 聊计划后点确认 | 成功；`project.md` 仍注入；after_turn 发 `project.state` |
| 完成一 task 调 `report_progress` | TASKS 勾选；侧栏刷新；`finish_reason=task_paused`（或等价文案） |
| 同 turn 再写下一 task 产物 | 硬拒 |
| 缺 `project_id` 只传 summary/task_line | 仍成功（注入） |
| grow 会话无项目绑定 | 清单仍无 `report_progress` |

### 实施锚点

| 层 | 文件 |
|----|------|
| 清单 | `loader.session_evolved_tools` / `session_evolved_allowlist` / catalog overlay |
| draft 壳 | `agent.py` `run_turn`（已去掉 grow 翻转） |
| 一停 | `executor._maybe_arm_task_stop`（含 `report_progress`） |
| 注入 | `executor._maybe_inject_report_progress_project_id` |
| 文案 | `project_mode.format_project_overlay` · `evolve/prompts/project.md` |
| Plan API | `plan_agent.report_progress` → `toggle_task` |
| schema | `evolve/tools/project/report_progress/tool.toml` |
| 测试 | `tests/test_project_progress_loop.py` · `tests/test_task_stop.py` |

### 与 P8 / P17 / P20 关系

| 已决 | 关系 |
|------|------|
| **P8** 不注册 project 主题 | **维持**；F1 用壳态并入 scope，不引入主题 id |
| **P17** 追加 coding | **维持** |
| **P20** / TASK-STOP | F3 修复「勾选路径」后一停门重新有效；直写 TASKS 仍禁 |

---

## 0f. 本地交付模型（已决 · 2026-08-06）

> 完整规格：[LOCAL-DELIVERY-MODEL.md](./LOCAL-DELIVERY-MODEL.md) · 审查子代理：[DELIVERABLE-REVIEW.md](./DELIVERABLE-REVIEW.md)

### 产品边界

| ID | 决议 |
|----|------|
| **F1** | **本地-only**：agent root + `workspace/<id>/` 为交付载体；**非目标** Codex Cloud 式云沙箱与自动开 PR |
| **F2** | **交付真源（栈-C）**：磁盘落盘 + 终端 build/test；项目外/敏感写才必经 Accept；**不是**任务归档 alone |
| **F3** | **默认 `solo` profile** |
| **F4** | 里程碑 **suggestion only**（LOCAL-DELIVERY-MODEL §5 · T-4714～4718） |
| **F5** | **栈-D 服从栈-C** |

### 与 Cursor / Claude / Codex

对齐 **栈-B / 栈-C**（harness · Accept · 审批档位 · 终端真源）；差异化 **栈-D**（TASKS · 配方 · 里程碑 suggestion）。**不**抄 Codex Cloud 自动 PR。逐家映射与冲突消歧见 [LOCAL-DELIVERY-MODEL.md](./LOCAL-DELIVERY-MODEL.md) **§6.1～6.6**。

### 实现锚点（里程碑提醒 · T-4714～4718 done）

| 层 | 文件 |
|----|------|
| 检测 | `evaluate_milestone_after_archive` · `phase_open_count_visible` · `archive_done_count_for_phase` |
| 提醒主载体 | `plan_agent._emit_milestone_review_if_needed` · `_suggestion(kind=milestone_review)` |
| 去重 | `workspace/<id>/.plan-agent/state.json` · `milestone_review_reminders` · `phase_key` |
| overlay | `milestone_review_suggested: <phase_key>` · `format_project_overlay` / `loader` |
| 规格 | [LOCAL-DELIVERY-MODEL.md](./LOCAL-DELIVERY-MODEL.md) §5.9 |

---

## 0. 已决摘要（2026-07-12）

| ID | 决议 |
|----|------|
| **P1** | 新增第四外壳 **`project`**，与 `grow` / `daily` / `govern` 平级 |
| **P2** | **grow** = 养 agent；**project** = 做产物（`workspace/<name>/`） |
| **P3** | 每标准项目强制七文件：`PROJECT.md` · `SCOPE.md` · `DESIGN.md` · `TECH-DESIGN.md` · `TASKS.md` · `VERIFY.md` · `RELEASE.md` |
| **P4** | 磁盘七文件 + `.plan-agent/manifest.json` = **抗压缩真源**；`MAP.md` / `ENV.md` 为旁路，未决以对应制品与 manifest revision 为准 |
| **P5** | `meta.json` 扩展：`active_shell` · `project_root` · `project_id` · `project_plan_status` |
| **P6** | project 壳 **硬拒绝** `write_evolve`；沉淀须显式切 grow |
| **P7** | **一活线一项目（A）**：同时仅一条活线绑该项目；换项目 → `项目 切换`；同项目污染 → **新开线**（归档旧线可回看）· [PROJECT-THREADS.md](./PROJECT-THREADS.md) |
| **P8** | **M0 用 7b**：仅 project 壳注入 `prompts/project.md`，**不**注册 `project` 主题 |
| **P9** | `project_plan_status`：`draft` \| `confirmed` \| `plan_dirty` |
| **P10** | **未 `confirmed` 禁止**写 `project_root` 下非三件套、禁止 `run_python` |
| **P11** | 开工前 **计划确认**：桌面 **计划确认卡**；CLI **`项目 确认`**（等价） |
| **P12** | 顶栏 `n/m` **仅**在 `confirmed` 后；否则显示「计划待确认」等（§8.2） |
| **P13** | **一次大确认**开工；改范围/验收/增删 **Phase** → `plan_dirty` + **mini-confirm** |
| **P14** | **新建项目** → 建议 **新会话/新开线**；**打开已有** → 续接该项目**活线**；同项目可另开线见 [PROJECT-THREADS.md](./PROJECT-THREADS.md) |
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

**项目模式** = 外壳分界 + **七文件制品链真源** + **计划确认门** + project prompt。

---

## 2. 四壳分工

```text
grow     → 养 agent（evolve/、proposal、write_evolve、内核）
project  → 做产物（workspace/<name>/、七文件制品链、验收）
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

**桌面（unified 工作台）**：项目绑定会话内仍禁 `write_evolve`（P6）。进入 grow 的路径见 [WORKBENCH-UI.md](./WORKBENCH-UI.md) **Q4**：无项目空态 **「先聊聊」**；已绑项目时顶栏 **「+ 对话」** 挂起项目后开 grow。

### 2.2 daily 与 project

- daily 不展示项目 UI；续写代码提示切 **项目 · xxx**。
- 路由见 §6。

---

## 3. workspace 七文件制品链

> T-5810 定义标准布局与 manifest 契约；T-5812 已将 `create_project` 切到七文件模板，并在首次建立 manifest 时一次性迁移旧四件套。新代码不得新增第二套计划真源。

### 3.1 目录

```text
workspace/
  _template/          # 七文件 + MAP/ENV 旁路模板
  <project-id>/       # 如 doudizhu
    PROJECT.md
    SCOPE.md           # REQ/AC/边界
    DESIGN.md          # UX 设计
    TECH-DESIGN.md     # 技术设计 / ADR
    TASKS.md           # 执行队列（仅开放项 · 见 PLAN-ARCH）
    VERIFY.md          # AC → V → L1 矩阵
    RELEASE.md         # 发布、迁移、回滚、人工验收
    .plan-agent/
      manifest.json    # 制品 revision / stale / 依赖
      changes.jsonl    # CHG ledger（后续任务落地）
    MAP.md             # 旁路代码地图
    ENV.md             # 旁路工具链与质量命令
    TASKS.archive.md    # 可选 · 已关闭归档（Phase 37 M3）
    bugs/               # 可选 · 缺陷/议题长文
    …                   # 源码（confirmed 后才可写）
```

角色与写权限见 [PLAN-ARCH.md](./PLAN-ARCH.md)（A1～A5）：叙事进 `bugs/` / `PROJECT` / `MAP`，不往 `TASKS.md` 倒全场信息。

### 3.1.1 默认 `normal` 文档基线（已决 · T-5831）

七文件是完整制品链的固定容器，不是七个空壳。默认 `normal` 项目至少应在 `DESIGN.md` 写清用例、主/异常流程、状态变化和关键时序，在 `TECH-DESIGN.md` 写清架构边界、数据/API、依赖和风险，并让图源以 Mermaid 形式留在对应制品内。

`normal` 图示是两个 **独立硬门槛**：`DESIGN.md` 内至少一个非时序 Mermaid 图（含 `UC-*`/`UX-*`）+ 至少一个 `sequenceDiagram`（含 `SEQ-*`，可在 `DESIGN` 或 `TECH-DESIGN`）；有生命周期才要求 `STATE-*`，否则写明理由。

`status: current` 不等于设计已完成。manifest 用 `completeness`（`skeleton`/`draft`/`complete`）与 `content_origin`（`migrated`/`scaffold`）区分结构与内容达标；`change_scope` 控制本次变更是否触发内容闸门（`small` 小修不因缺图阻塞）。具体以 [DESKTOP-REAL-RD-FLOW.md](./DESKTOP-REAL-RD-FLOW.md) §2.2.1–§2.2.2、§7.1 为准。

### 3.2 纪律

1. **无七文件不出标准计划** — 标准项目必须有七文件；`MAP.md` / `ENV.md` 旁路可后补，旧项目按 REAL-RD §10 一次性迁移。
2. **计划须用户确认（§4）** — 未 `confirmed` 不写代码、不 `run_python`。
3. **小步完成标 `[x]`** — 经 `report_progress` + Progress Gate（禁止主 Agent 直写勾选）。
4. **续做 / 压缩后** — 必须先 `read_file` `TASKS.md`（开放队列）；归档默认不充当「下一步」真源。
5. **交付** — 开放项清零 + 验收通过 → 才允许「交付完成」。
6. **每 task 一停（P20 · Phase 20）** — 标完当前 `[x]` 后必须停，等用户「继续」再开下一项；见 [TASK-STOP.md](./TASK-STOP.md) v0.2.0。
7. **计划域角色（P37）** — 长叙述 / 已关闭项不进默认注入；见 [PLAN-ARCH.md](./PLAN-ARCH.md)。

### 3.3 与 digest

压缩摘要仅参考；**未决以 `TASKS.md` 为准**（digest 模板见 §7.3）。

---

## 4. 计划确认门（核心）

### 4.1 流程

```text
① 立项
   项目 新建 <id>  /  「写斗地主」→ confirm 新建
   → workspace/<id>/ + 七文件骨架 + `.plan-agent/manifest.json`
   → project_plan_status = draft
   → 顶栏：项目 · <id> · 计划待确认

② 出计划（仅文档）
   对话中让助手填 PROJECT.md + SCOPE.md + TASKS.md（Phase + 条目）— **draft 阶段允许**，intent 命中文档制品链时走 `plan` 回合
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

**一活线一项目**（项目壳内）：每个 `workspace/<id>/` 在 `project_sessions` 中绑定**当前活线** `conversation_id`（同时至多一条）；切换项目 = 切换到目标项目的活线。同项目可 **新开线**：旧活线进入 `project_thread_archive`，仅回看；设计见 [PROJECT-THREADS.md](./PROJECT-THREADS.md)（Phase 36）。

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
  },
  "project_thread_archive": {
    "doudizhu": ["20260710-oldline01"]
  }
}
```

实现：`shell_switch.py`（壳切换）· `project_switch.py`（项目切换）· Phase 36 起归档索引。

| 操作 | 会话 |
|------|------|
| 桌面切到 **生长/日用** | 加载 `shell_sessions[grow\|daily]`；无则新建；**不**带 `project_id` |
| 桌面切到 **项目** | 加载 `project_sessions[last_project_id]`（或最近项目）**活线** |
| `项目 切换` | 同 M3；仅 project 壳内 |
| `项目 新开线` / UI「新开线」 | 同项目新 `conversation_id`；旧线入档；history 清空；可跳过交接 |
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

- 七文件制品链 + manifest + 计划确认门（§4）
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
| UI 目录 | `desktop/src/shells/unified/` + `project-panel.ts`（旧 `shells/project/` 已删） |

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
| 切换后 | `project.switch.done` → 若 `session_replaced` 则推送 `session.memory`（`context.session_memory_event`）+ `session.history`（`session.session_history_event`），桌面 `session.refresh`；聊天区 **替换**（非追加）；侧栏 `project.state` 同步 `TASKS.md`，`project.plan.state` 恢复项目级待采纳提案 |
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

内置 IDE、每项目自动 git、多项目并行 agent、同项目多活线并行执行、host 替代 workspace。同项目多归档线见 [PROJECT-THREADS.md](./PROJECT-THREADS.md)（不替代跨项目切换）。

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
| 0.2.5 | 2026-08-03 | P7/P14/§4.4：一活线一项目 + `project_thread_archive` 指针；详 [PROJECT-THREADS.md](./PROJECT-THREADS.md) |
| 0.4.2 | 2026-08-14 | Phase 58b T-5810～T-5819 + T-5818：七文件制品链、manifest/stale、任务关联、L1 证据、CHG ledger、阶段卡制品依据与持久化人工验收 |
| 0.4.3 | 2026-08-15 | IT-5821：项目恢复/切换补发 `project.plan.state`，待采纳提案不随会话重建消失 |
| 0.4.4 | 2026-08-15 | IT-5823：侧栏有效提案的「查看」统一切换计划审阅面；CHG 时间线默认紧凑折叠，仍可展开查看最近记录 |
| 0.4.5 | 2026-08-15 | IT-5824：侧栏提案卡的「查看」复用 `open-plan-review` 动作，避免与阶段卡分叉出第二条审阅入口 |
| 0.4.6 | 2026-08-15 | IT-5825：侧栏提案卡的「查看」改用专用 `open-suggestion-review` 动作，由提案区域捕获后直接切换主区审阅面 |
| 0.4.7 | 2026-08-15 | IT-5826：保留旧「查看」入口但隐藏，新增「审阅」按钮与独立动作链路，便于隔离验证 |
| 0.4.8 | 2026-08-15 | IT-5827：审阅按钮直接完成主区 plan-review DOM 切换，并显示打开成功/失败状态 |
| 0.4.9 | 2026-08-15 | IT-5828：审阅打开流程全量纳入错误边界，并提供读取、切换、渲染阶段状态 |
| 0.5.0 | 2026-08-15 | IT-5829：补齐计划审阅索引状态，修复 `planReviewIndex is not defined` |
| 0.5.1 | 2026-08-15 | IT-5830：采纳操作合并 PlanAgent 状态写入；项目分发异常回传并清理 Desktop pending 状态 |
| 0.5.2 | 2026-08-15 | T-5831 讨论稿：补齐 `normal/large` 文档内容下限、用例/时序/状态图和技术设计要求 |
| 0.5.3 | 2026-08-15 | T-5831 已决：双独立图示硬门槛、`completeness`/`content_origin`/`change_scope` 字段、迁移 skeleton 策略 |
