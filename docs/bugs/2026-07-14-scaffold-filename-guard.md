# BUG-018 · scaffold 守卫按 basename 误拦 workspace README.md

> 日期 2026-07-14 · 状态 **fixed**

## 现象

在 **project** 壳或一般对话中，`write_text` 写 `workspace/<project>/README.md`（或 `main.py`）返回：

```text
文件名 'README.md' 属于 evolved 工具脚手架，不能经 write_text 写入 workspace…
```

助手被迫改用 `run_python` 批量落盘。

## 根因

Phase 16 `_validate_scaffold_evolved_call` 对 `write_text` / `append_text` / `copy_move` 使用 `_tool_scaffold_filename`：**仅比较路径最后一段**是否在 `{main.py, tool.toml, README.md}`，不区分 `workspace/project1/` 与 `evolve/tools/<scope>/<name>/`。

设计意图是 grow **scaffold 回合**禁止用 `write_text` 写 evolved 三件套；泛化到所有回合后误伤普通项目文件。

## 修复

| 场景 | 规则 |
|------|------|
| `scaffold_tool_turn` | 仍禁止 workspace 内任意路径的脚手架**文件名**（逼走 `write_evolve`） |
| 其他回合 | 仅当路径匹配 `evolve/tools/<scope>/<tool>/(main.py\|tool.toml\|README.md)` 时拒绝 |
| `workspace/**/README.md` 等 | **允许** `write_text` |

实现：`executor.py` — `_is_evolve_tool_scaffold_path` + `_tool_scaffold_basename`。

## 回归

- `python agent-core/tools/executor.py`（demo 段）
- `python agent-core/tests/test_write_evolve_pipeline.py`

## 文档

- [TOOLS.md](../TOOLS.md) §7.6 执行器预检
- [RUNTIME-GUARDS.md](../RUNTIME-GUARDS.md) §3.2
- [loader.py](../../agent-core/loader.py) scaffold overlay
