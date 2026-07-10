# 开发与代码（coding）

本主题在会话确认 **coding** 后注入 system；与 `agent-core/prompts/core.txt` 叠加，冲突时 **本文件 + safety** 优先于通用习惯。

## 默认习惯

- Python **3.12+**；依赖见根目录 `requirements.txt`（当前仅 `httpx`）。
- 在 `agent-core/` 下开发；`from paths import AgentPaths`（目录名带连字符，**不要** `import agent_core`）。
- **先读** `docs/MAP.md`、`docs/TASKS.md` 当前 task，再改代码；严格对照 `TOOLS.md` / `RUNTIME.md` / `MEMORY.md` 已决条款。
- 小步交付：每步可 `python <module>.py` 或 `python main.py --demo` 手工验收。
- **不要**未经用户要求 `git commit`；M1 不做 skill。

## 路径与工具

| 区域 | 规则 |
|------|------|
| `docs/` | 设计真源；先评审再写代码 |
| `evolve/` | 用户策展；Git 真源；`_index.toml` 手改 |
| `workspace/` | 用户工作文件；evolved 写盘边界 |
| `data/` | session / log；默认 gitignore |

动手只用 **6 Builtin + `run_evolved`**；读记忆正文用 `read_file evolve/memories/...`。

## 与本仓库 Phase 3

记忆三件套：`prompts/coding.md`（本文件）+ `memories/coding/*.md` 索引 + session `goal.md`。主题确认后见 system 中的 `[久远记忆]` 与 `topic_prompt:coding`。
