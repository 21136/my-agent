# 开发与代码（coding）

本主题在会话确认 **coding** 后注入 system；与 `agent-core/prompts/core.txt` 叠加，冲突时 **本文件 + safety** 优先于通用习惯。

## 默认习惯

- Python **3.12+**；依赖见根目录 `requirements.txt`（当前仅 `httpx`）。
- 在 `agent-core/` 下开发；`from paths import AgentPaths`（目录名带连字符，**不要** `import agent_core`）。
- **先读** `docs/MAP.md`、`docs/TASKS.md` 当前 task，再改代码；严格对照 `TOOLS.md` / `RUNTIME.md` / `MEMORY.md` 已决条款。
- 小步交付：每步可 `python <module>.py` 或 `python main.py --demo` 手工验收；也可用 evolved **`run_demo`**。
- **不要**未经用户要求 `git commit`；提交前可用 **`git_snapshot`** 看 status/diff。

## coding 主题 evolved 工具

| 工具 | 作用 |
|------|------|
| `run_demo` | 在 `agent-core/` 下运行 `python <script>.py [extra_args]`，捕获 stdout/stderr/exit_code |
| `git_snapshot` | 只读 `git status --porcelain` + `diff --stat`（可选 staged） |
| `git_clone` | 浅克隆 **https** 公开仓到 `workspace/`（vendor）或 `evolve/tools/`（造工具参考）；**每次 confirm**；project 壳仅 workspace |
| `patch_file` | 按行号或唯一 `find` 锚点替换 agent 根下文本（`docs/` / `agent-core/` / `evolve/` / `workspace/`） |

以上工具 **`workspace_only=false`**：每次 `run_evolved` 须 confirm（无本会话 `a` 免确认）。

**新建 coding/data 等主题工具**：`run_evolved` → `write_evolve`；先 `main.py` 再 `tool.toml`。短片段可用 `content_base64`；**大段 `main.py` 优先** `write_text` → `workspace/_staging_*.py` + `content_workspace_path`（勿用易截断的大 base64）。

## 路径与工具

| 区域 | 规则 |
|------|------|
| `docs/` | 设计真源；先评审再写代码 |
| `evolve/` | 用户策展；Git 真源；`_index.toml` 手改 |
| `workspace/` | 用户工作文件 |
| `data/` | session / log；默认 gitignore |

动手只用 **6 Builtin + `run_evolved`**；读记忆正文用 `read_file evolve/memories/...`。

**common 文件工具**（每 session 可用）：`write_text` · `append_text` · `copy_move` · `move_to_trash`（均 `workspace_only`，先试 `dry_run`）。

## 与本仓库 Phase 3

记忆三件套：`prompts/coding.md`（本文件）+ `memories/coding/*.md` 索引 + session `goal.md`。主题确认后见 system 中的 `[久远记忆]` 与 `topic_prompt:coding`。
