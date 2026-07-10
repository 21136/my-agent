---
id: project-my-agent
topics: [coding]
status: active
summary: my-agent 个人进化 agent，Python 3.12，建设顺序先 tool 后 skill
---

## 背景

my-agent 是本地可进化的个人 Agent：**Git 为真源**，LLM 仅经 6 个 Builtin 与 `run_evolved` 动手；进化产物在 `evolve/`（prompt / memory / tool）。

## 建设顺序（已决）

1. **M1a** 工具层（Phase 1，`T-101`～`T-112`）
2. **M1b** 对话壳 + LLM（Phase 2）
3. **M1c** 记忆三件套（Phase 3，`T-301`～`T-308`）
4. **M2** proposal / 进化写入（仍不做 skill）
5. Skill 最后（M4 可选）

## 开发约定

- 内核：`agent-core/`；导入 `from paths import AgentPaths`，`from tools.registry import ...`。
- 主题索引：`evolve/_index.toml`；coding 主题含 `tool_dirs = ["tools/coding"]`。
- 启动时 system S0 注入本文件 **id + summary**；全文按需 `read_file evolve/memories/coding/example.md`。

## 参考

- 总览：`docs/PROJECT.md`
- 实施细表：`docs/TASKS.md`
- 记忆设计：`docs/MEMORY.md` §5
