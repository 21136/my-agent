---
id: project-my-agent
topics: [coding]
status: active
summary: my-agent 仓库开发示例，Python 3.12
use_count: 3
last_used_at: "2026-08-09T00:41:37.678013Z"
---

## 背景

本仓库（产品内部名 my-agent）是本地可进化的个人 Agent；**对用户聊天自称小魔王**（见 `evolve/persona.md`）。**Git 为真源**，LLM 仅经当前暴露的已注册 Builtin、顶层扁平原语（`run_command` / `write_text` / `patch_file` / `git_diff` / `git_snapshot`）与 `run_evolved` 动手；进化产物在 `evolve/`（prompt / memory / tool）。

## 开发约定

- 内核：`agent-core/`；导入 `from paths import AgentPaths`，`from tools.registry import ...`。
- 主题索引：`evolve/_index.toml`；coding 主题含 `tool_dirs = ["tools/coding"]`。
- 启动时 system S0 注入本文件 **id + summary**；全文按需 `read_file evolve/memories/coding/example.md`。

## 参考

- 总览：`docs/MAP.md / docs/README.md`
- 实施细表：`docs/MAP.md`
- 记忆设计：`docs/MEMORY.md` §5
