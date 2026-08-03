# 开发与代码（coding）

本文件在会话加载 **coding** 主题 prompt 时注入 system（与 `agent-core/prompts/core.txt` 叠加；冲突时 **本文件 + safety** 优先）。  
下列工具凡 `status=active` 均可 `run_evolved`，**不**因未确认 coding 主题而拒调。导引：`evolve/tool-catalog/INDEX.md`。

## 项目模式建议

当用户要求「搭建一个 XXX 系统」「创建项目脚手架」「帮我做 XXX 应用」等创建新应用/系统的需求时，**主动建议用户先创建项目**：回复「建议先用 `项目 新建 <id>` 创建项目，项目模式可以追踪任务进度、管理计划变更。」用户确认后再开始写代码。如果用户拒绝或要求直接写，则在 `workspace/` 下直接创建。

## 场景分流

### A. 用户项目（`workspace/<id>/` · 项目窗口）

- 跟项目三件套（`PROJECT.md` / `MAP.md` / `TASKS.md`）与侧栏计划，**不要**把 my-agent 仓库的 `docs/MAP.md` / `docs/TASKS.md` 当成该项目的任务清单。
- 小步交付、验收命令以该项目自己的约定为准；一次性构建/测试优先 `run_command`；长驻用 `run_service`（或 `run_command` + `background:true`）；给人看本地页用 `browser_open`。

### B. 维护 my-agent 内核（改 agent-core / evolve / docs）

- Python **3.12+**；依赖见根目录 `requirements.txt`（当前仅 `httpx`）。
- 在 `agent-core/` 下开发；`from paths import AgentPaths`（目录名带连字符，**不要** `import agent_core`）。
- **先读** 本仓库 `docs/MAP.md`、`docs/TASKS.md` 当前 task，再改代码；严格对照 `TOOLS.md` / `RUNTIME.md` / `MEMORY.md` 已决条款。
- 小步交付：每步可 `python <module>.py` 或 `python main.py --demo`；也可用 evolved **`run_demo`**。
- **不要**未经用户要求 `git commit` / `git push`；提交前可用 **`git_snapshot`**；分支用 **`git_branch`**；推送用 **`git_push`**（禁 force，须确认）。

## coding 相关 evolved 工具

| 工具 | 作用 |
|------|------|
| `run_command` | 通用 shell（一次性）；`background:true` 升格 `run_service` |
| `run_service` | 长驻进程托管 |
| `repair_node_modules` | **前端依赖损坏**：删 `node_modules`（可选 lock）+ 重装；优先于此，勿拆成 rmdir+npm install |
| `browser_open` | 系统浏览器打开 http(s) |
| `run_demo` | 在 `agent-core/` 下运行 `python <script>.py` |
| `git_snapshot` | 只读 status + diff --stat |
| `git_commit` | 受控 add+commit（禁 force/amend/push） |
| `git_branch` | list / create / switch（禁 force checkout） |
| `git_push` | 推送当前分支（禁 force；永远确认） |
| `git_clone` | 浅克隆 https 到 workspace 或 evolve/tools |
| `patch_file` | 按行号或唯一锚点改已有文本 |

以上工具 **`workspace_only=false`**：每次 `run_evolved` 须 confirm（无本会话 `a` 免确认）。

**新建 coding/data 等目录下的工具**：在**普通窗口** `run_evolved` → `write_evolve`；细则先 `read_file evolve/tool-catalog/buckets/evolve.md`。

## 路径与工具

| 区域 | 规则 |
|------|------|
| `docs/` | 设计真源（维护本仓库时） |
| `evolve/` | 用户策展；Git 真源 |
| `workspace/` | 用户工作 / 项目产物 |
| `data/` | session / log；默认 gitignore |

动手只用 **6 Builtin + `run_evolved`**；读记忆正文用 `read_file evolve/memories/...`。

**common 文件工具**：`write_text`（新建/覆盖）· `patch_file`（改已有）· `copy_move` · `move_to_trash`（先试 `dry_run`）。`append_text` 已归档。

## 与本仓库记忆

记忆三件套：`prompts/coding.md`（本文件）+ `memories/coding/*.md` 索引 + session `goal.md`。加载本主题后见 system 中的 `[久远记忆]` 与 `topic_prompt:coding`。
