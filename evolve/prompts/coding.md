# 开发与代码（coding）

本文件在会话加载 **coding** 主题 prompt 时注入 system（与 `agent-core/prompts/core.txt` 叠加；冲突时 **本文件 + safety** 优先）。  
下列工具凡 `status=active` 均可 `run_evolved`，**不**因未确认 coding 主题而拒调。导引：`evolve/tool-catalog/INDEX.md`。

## 项目模式建议

当用户要求「搭建一个 XXX 系统」「创建项目脚手架」「帮我做 XXX 应用」等创建新应用/系统的需求时，普通模式主动建议先用 `项目 新建 <id>`；狂奔模式已明确授权时，直接在已绑定项目内完成需求、文档、实现和验证，不再要求用户确认计划或逐步继续。

## 场景分流

### A. 用户项目（`workspace/<id>/` · 项目窗口）

- 跟项目三件套（`PROJECT.md` / `MAP.md` / `TASKS.md`）与侧栏计划，**不要**把 my-agent 仓库的 `docs/MAP.md` / `docs/TASKS.md` 当成该项目的任务清单。
- 小步交付、验收命令以该项目自己的约定为准；一次性构建/测试优先 `run_command`；长驻用 `run_service`（或 `run_command` + `background:true`）；给人看本地页用 `browser_open`。

### B. 维护 my-agent 内核（改 agent-core / evolve / docs）

- Python **3.12+**；依赖见根目录 `requirements.txt`（httpx / websockets / rich / prompt_toolkit / python-docx 等；以文件为准）。
- 在 `agent-core/` 下开发；`from paths import AgentPaths`（目录名带连字符，**不要** `import agent_core`）。
- **先读** 本仓库 `docs/MAP.md`、`docs/TASKS.md` 当前 task，再改代码；严格对照 `TOOLS.md` / `RUNTIME.md` / `MEMORY.md` 已决条款。
- 小步交付：每步可 `python <module>.py` 或 `python main.py --demo`；也可用 evolved **`run_demo`**。
- **不要**未经用户要求 `git commit` / `git push`；提交前可用 **`git_snapshot`** / **`git_diff`**；分支用 **`git_branch`**；推送用 **`git_push`**（禁 force，须确认）；开 PR 用 **`gh_pr`**（需本机 `gh`）。

## coding 相关 evolved 工具

| 工具 | 作用 |
|------|------|
| `run_command` | 通用 shell（一次性）；`background:true` 升格 `run_service` |
| `run_service` | 长驻进程托管 |
| `interactive_terminal` | **active**；需要 stdin、实时输出、Ctrl-C、EOF 或 attach/resume；由长期 agent/server 宿主托管 worker |
| `repair_node_modules` | **前端依赖损坏**：删 `node_modules`（可选 lock）+ 重装；优先于此，勿拆成 rmdir+npm install |
| `browser_open` | 系统浏览器打开 http(s) |
| `run_demo` | 在 `agent-core/` 下运行 `python <script>.py` |
| `git_snapshot` | 只读 status + diff stat；需要检查具体改动时使用 `include_diff=true`，可选 staged/base_ref/path 过滤 |
| `git_diff` | **只读免确认**：默认返回 diff 补丁；支持 staged / base_ref / paths / name-status / stat_only |
| `git_restore` | **active**；默认 dry-run，只有显式 `dry_run=false`、expected_hash 匹配且通过确认后才恢复 |
| `structured_test` | **active**；统一测试 status/失败定位/超时/缺依赖，底层复用 `run_project_tests` |
| `git_commit` | 受控 add+commit（禁 force/amend/push） |
| `git_branch` | list / create / switch（禁 force checkout） |
| `git_push` | 推送当前分支（禁 force；永远确认） |
| `gh_pr` | 受控 PR：create（确认）/ view / checks / list；禁 merge/force/delete |
| `search_replace` | 多文件字面量替换；默认 dry_run；写入须确认 |
| `local_preview` | 可选启动 + 等端口 + 探测 + 浏览器打开 |
| `git_clone` | 浅克隆 https 到 workspace 或 evolve/tools |
| `patch_file` | 按行号或唯一锚点改已有文本 |

确认策略（对齐 `ToolPolicy.allow_approve_all` + executor 特例，**不要**再写已废弃的 `workspace_only`）：
- **只读 / 预览默认免确认**：`git_snapshot` / `git_diff`；`git_branch` 的 `list`；`gh_pr` 的 view/checks/list；`search_replace` 默认 dry_run；`git_restore` 默认 dry-run 预览；`run_service` 的 status/logs/list 等。
- **会改状态的须 confirm**：`git_branch` create/switch、`git_commit` / `git_push`（dry_run 除外）、写盘类、`run_command` 按策略等。
- `allow_approve_all=true` 的工具可在本会话用 `a` 免重复确认；`allow_approve_all=false` 的每次仍须确认（dry_run / 上列只读特例除外）。

**新建 evolved 工具**：仅在 **非项目绑定** 会话（grow / 先聊聊）；system 已含 `[tool_workshop]`；语法见 `buckets/evolve.md`。
**项目绑定会话**禁止 `write_evolve`（见 PROJECT-MODE P6）。

## 路径与工具

| 区域 | 规则 |
|------|------|
| `docs/` | 设计真源（维护本仓库时） |
| `evolve/` | 用户策展；Git 真源 |
| `workspace/` | 用户工作 / 项目产物 |
| `data/` | session / log；默认 gitignore |

动手只通过当前暴露的已注册 builtin、扁平原语和 `run_evolved`；读记忆正文用 `read_file evolve/memories/...`。当前 builtin 基线为 12 个（核心 8 + 编排 4），不把 evolved 工具误写成 builtin。

**common 文件工具**：`write_text`（新建/覆盖）· `patch_file`（改已有）· `copy_move` · `move_to_trash`（先试 `dry_run`）。`append_text` 已归档。

**写盘纪律（BUG-025）**：细节与大文件写入规范见 `evolve/tool-catalog/buckets/evolve.md`。

## 与本仓库记忆

记忆三件套：`prompts/coding.md`（本文件）+ `memories/coding/*.md` 索引 + session `goal.md`。加载本主题后见 system 中的 `[久远记忆]` 与 `topic_prompt:coding`。
